"""Small, private account features in the auxiliary schema, no authentication DDL."""
import base64,binascii,hashlib,re,struct,zlib
from datetime import datetime
from contextlib import contextmanager
from sqlalchemy import MetaData,Table,Column,BigInteger,Integer,String,LargeBinary,DateTime,select,insert,update
from app.models import User
from sqlalchemy.dialects.mysql import MEDIUMBLOB

MAX_AVATAR=70000
class FeatureConflict(ValueError):pass
class FeatureNotFound(ValueError):pass

def tables():
 md=MetaData()
 Table('user_profiles',md,Column('user_id',BigInteger,primary_key=True),Column('avatar_png',LargeBinary().with_variant(MEDIUMBLOB(),'mysql')),Column('version',Integer,nullable=False),Column('updated_at',DateTime,nullable=False))
 Table('movie_notes',md,Column('user_id',BigInteger,primary_key=True),Column('movie_id',BigInteger,primary_key=True),Column('note',String(200),nullable=False),Column('version',Integer,nullable=False),Column('updated_at',DateTime,nullable=False))
 Table('assistant_feedback',md,Column('user_id',BigInteger,primary_key=True),Column('request_key',String(64),primary_key=True),Column('request_id',String(100),nullable=False),Column('reaction',String(8)),Column('updated_at',DateTime,nullable=False))
 return md.tables

def clean_avatar(value):
 if value is None:return None
 prefix='data:image/png;base64,'
 if type(value) is not str or not value.startswith(prefix) or len(value)>MAX_AVATAR*4//3+40:raise ValueError('Avatar format')
 try:raw=base64.b64decode(value[len(prefix):],validate=True)
 except (ValueError,binascii.Error):raise ValueError('Avatar encoding') from None
 if len(raw)>MAX_AVATAR or not raw.startswith(b'\x89PNG\r\n\x1a\n'):raise ValueError('Avatar size/type')
 pos=8;ihdr=None;compressed=b'';ended=False
 while pos<len(raw):
  if pos+12>len(raw):raise ValueError('PNG chunk')
  size=struct.unpack('>I',raw[pos:pos+4])[0];kind=raw[pos+4:pos+8];data=raw[pos+8:pos+8+size]
  if size>MAX_AVATAR or pos+size+12>len(raw):raise ValueError('PNG bounds')
  crc=struct.unpack('>I',raw[pos+8+size:pos+12+size])[0]
  if binascii.crc32(kind+data)&0xffffffff!=crc:raise ValueError('PNG checksum')
  if ihdr is None:
   if kind!=b'IHDR' or size!=13:raise ValueError('PNG header')
   w,h,depth,color,comp,filt,interlace=struct.unpack('>IIBBBBB',data)
   if (w,h,depth,color,comp,filt,interlace)!=(128,128,8,6,0,0,0):raise ValueError('Cropped 128px RGBA PNG required')
   ihdr=data
  elif kind==b'IDAT':compressed+=data
  elif kind==b'IEND':
   if size:raise ValueError('PNG end')
   ended=True;pos+=12;break
  elif kind not in (b'sRGB',b'gAMA',b'pHYs',b'cHRM'):raise ValueError('Unsupported PNG metadata')
  pos+=size+12
 if not ended or pos!=len(raw):raise ValueError('PNG trailer')
 try:
  codec=zlib.decompressobj();pixels=codec.decompress(compressed,128*(128*4+1)+1)
 except zlib.error:raise ValueError('PNG compression') from None
 if len(pixels)!=128*(128*4+1) or not codec.eof or codec.unused_data or codec.unconsumed_tail or any(pixels[i]>4 for i in range(0,len(pixels),513)):raise ValueError('PNG raster')
 def chunk(kind,data):return struct.pack('>I',len(data))+kind+data+struct.pack('>I',binascii.crc32(kind+data)&0xffffffff)
 return b'\x89PNG\r\n\x1a\n'+chunk(b'IHDR',ihdr)+chunk(b'IDAT',zlib.compress(pixels))+chunk(b'IEND',b'')

class PersonalFeatures:
 def __init__(self,engine,auth_engine):self.engine=engine;self.auth_engine=auth_engine;self.t=tables()
 def ready(self):
  with self.engine.connect() as c:
   for table in self.t.values():c.execute(select(table).limit(0))
 @contextmanager
 def owner(self,uid):
  # Same account-row lock order as closure. Closed accounts cannot resurrect data.
  with self.auth_engine.begin() as c:
   if c.scalar(select(User.status).where(User.id==uid).with_for_update())!='active':raise PermissionError('Account inactive')
   with self.engine.begin() as data:yield data
 def profile(self,uid):
  with self.engine.connect() as c:r=c.execute(select(self.t['user_profiles']).where(self.t['user_profiles'].c.user_id==uid)).mappings().first()
  return dict(user_id=uid,avatar='data:image/png;base64,'+base64.b64encode(r['avatar_png']).decode() if r and r['avatar_png'] else None,version=r['version'] if r else 0)
 def avatar(self,uid,value,version):
  png=clean_avatar(value)
  if type(version) is not int or version<0:raise ValueError('Version')
  t=self.t['user_profiles']
  with self.owner(uid) as c:
   prior=c.execute(select(t).where(t.c.user_id==uid).with_for_update()).mappings().first()
   if (prior['version'] if prior else 0)!=version:raise FeatureConflict('PROFILE_VERSION_CONFLICT')
   values=dict(avatar_png=png,version=version+1,updated_at=datetime.utcnow())
   c.execute(update(t).where(t.c.user_id==uid).values(**values) if prior else insert(t).values(user_id=uid,**values))
  return self.profile(uid)
 def notes(self,uid):
  with self.engine.connect() as c:rows=c.execute(select(self.t['movie_notes']).where(self.t['movie_notes'].c.user_id==uid)).mappings().all()
  return {str(r['movie_id']):dict(note=r['note'],version=r['version']) for r in rows}
 def note(self,uid,mid,note,version):
  from catalog.models import CatalogBase
  m=CatalogBase.metadata.tables['movies']
  if type(mid) is not int or mid<1 or type(note) is not str or len(note)>200 or type(version) is not int or version<0:raise ValueError('Note')
  t=self.t['movie_notes']
  with self.auth_engine.connect() as c:
   if c.scalar(select(m.c.movie_id).where(m.c.movie_id==mid,m.c.catalog_status=='active')) is None:raise FeatureNotFound('MOVIE_NOT_FOUND')
  with self.owner(uid) as c:
   prior=c.execute(select(t).where(t.c.user_id==uid,t.c.movie_id==mid).with_for_update()).mappings().first()
   if (prior['version'] if prior else 0)!=version:raise FeatureConflict('NOTE_VERSION_CONFLICT')
   values=dict(note=note,version=version+1,updated_at=datetime.utcnow())
   c.execute(update(t).where(t.c.user_id==uid,t.c.movie_id==mid).values(**values) if prior else insert(t).values(user_id=uid,movie_id=mid,**values))
  return dict(note=note,version=version+1,movie_id=mid)
 def feedback(self,uid,rid,reaction='READ'):
  from .store import tables as ledger_tables
  if type(rid) is not str or not re.fullmatch(r'[a-zA-Z0-9_-]{8,100}',rid) or reaction not in ('READ','up','down',None):raise ValueError('Feedback')
  key=hashlib.sha256(f'byok-v2:{uid}:{rid}'.encode()).hexdigest();t=self.t['assistant_feedback'];req=ledger_tables().tables['requests']
  with self.owner(uid) as c:
   prior=c.execute(select(t).where(t.c.user_id==uid,t.c.request_key==key)).mappings().first()
   original=c.execute(select(req.c.user_id,req.c.response).where(req.c.id==key)).first()
   if not original or original[0]!=uid or (not prior and (original[1] or {}).get('state')!='DONE'):raise FeatureNotFound('ANSWER_NOT_FOUND')
   if reaction=='READ':return dict(request_id=rid,reaction=prior['reaction'] if prior else None)
   values=dict(reaction=reaction,updated_at=datetime.utcnow())
   c.execute(update(t).where(t.c.user_id==uid,t.c.request_key==key).values(**values) if prior else insert(t).values(user_id=uid,request_key=key,request_id=rid,**values))
  return dict(request_id=rid,reaction=reaction)
