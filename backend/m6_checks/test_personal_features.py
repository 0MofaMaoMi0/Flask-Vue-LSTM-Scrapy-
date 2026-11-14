import base64,binascii,struct,zlib
import pytest
from assistant.personal_features import clean_avatar

def png():
 def chunk(k,d):return struct.pack('>I',len(d))+k+d+struct.pack('>I',binascii.crc32(k+d)&0xffffffff)
 return b'\x89PNG\r\n\x1a\n'+chunk(b'IHDR',struct.pack('>IIBBBBB',128,128,8,6,0,0,0))+chunk(b'IDAT',zlib.compress(b'\0'*(128*513)))+chunk(b'IEND',b'')
def data_url(raw):return 'data:image/png;base64,'+base64.b64encode(raw).decode()
def test_avatar_cropped_png_roundtrip_and_transparency():
 image=png();assert clean_avatar(data_url(image))==image;assert clean_avatar(None) is None
@pytest.mark.parametrize('value',['data:image/svg+xml;base64,PHN2Zz4=','https://127.0.0.1/a.png','data:image/png;base64,not_valid',data_url(b'not_png')])
def test_bad_avatar_rejected(value):
 with pytest.raises(ValueError):clean_avatar(value)
def test_png_crc_size_trailer_and_decompression_bounds():
 good=png()
 for invalid in [good+b'extra',good[:-2],good[:20]+b'broken'+good[26:],b'x'*70001]:
  with pytest.raises(ValueError):clean_avatar(data_url(invalid))

def test_account_owner_lock_serializes_personal_mutations():
 from assistant.personal_features import PersonalFeatures
 from contextlib import contextmanager
 class C:
  def scalar(self,query):
   assert query._for_update_arg is not None;return 'closed'
 class Engine:
  @contextmanager
  def begin(self):yield C()
 store=PersonalFeatures(Engine(),Engine())
 with pytest.raises(PermissionError):
  with store.owner(1):raise AssertionError('Closed account must not mutate')

def api(monkeypatch,authenticated=True,csrf=True):
 from flask import Flask
 from types import SimpleNamespace
 from contextlib import contextmanager
 from assistant import personal_api
 class DB:
  @contextmanager
  def begin(self):yield None
 class Store:
  t={}
  def __init__(self,*args):self.calls=[]
  def ready(self):pass
  def profile(self,uid):return {'user_id':uid,'avatar':None,'version':0}
  def avatar(self,uid,value,version):self.calls.append((uid,value,version));return {'user_id':uid,'version':version+1}
  def feedback(self,*args):self.calls.append(args);return {}
 monkeypatch.setattr(personal_api,'PersonalFeatures',Store)
 monkeypatch.setattr(personal_api,'session_row',lambda _:SimpleNamespace(user_id=7) if authenticated else None)
 monkeypatch.setattr(personal_api,'csrf_ok',lambda _:csrf)
 app=Flask(__name__);app.config.update(TESTING=True,MAX_CONTENT_LENGTH=8192)
 app.extensions.update(m7_engine=object(),auth_engine=object(),auth_db=DB(),credential_store=SimpleNamespace())
 personal_api.install(app);return app

@pytest.mark.parametrize('authenticated,csrf,status',[(False,True,401),(True,False,403)])
def test_api_auth_csrf_precede_mutation(monkeypatch,authenticated,csrf,status):
 app=api(monkeypatch,authenticated,csrf)
 assert app.test_client().post('/api/v1/account/profile',json={'avatar':None,'version':0}).status_code==status
 assert not app.extensions['personal_features'].calls

@pytest.mark.parametrize('body',[{'avatar':None,'version':0,'user_id':8},{'avatar':None},{'avatar':None,'version':0,'url':'https://other.invalid'}])
def test_profile_no_other_identity_or_upload_url(monkeypatch,body):
 app=api(monkeypatch);assert app.test_client().post('/api/v1/account/profile',json=body).status_code==400
 assert not app.extensions['personal_features'].calls

def test_route_size_override_does_not_expand_other_routes(monkeypatch):
 from flask import request
 app=api(monkeypatch)
 @app.post('/other')
 def other():return request.get_json()
 c=app.test_client();body={'avatar':'x'*9000,'version':0}
 assert c.post('/api/v1/account/profile',json=body).status_code==200
 assert c.post('/other',json=body).status_code==413

def test_feedback_read_sentinel_and_duplicate_query_are_private(monkeypatch):
 app=api(monkeypatch);c=app.test_client()
 assert c.post('/api/v1/account/assistant-feedback',json={'request_id':'synthetic_123','reaction':'READ'}).status_code==400
 assert c.get('/api/v1/account/assistant-feedback?request_id=a&request_id=b').status_code==400
 assert not app.extensions['personal_features'].calls

def test_unavailable_personal_store_cannot_silently_skip_account_erasure(monkeypatch):
 from assistant import personal_api
 from sqlalchemy.exc import SQLAlchemyError
 from types import SimpleNamespace
 from flask import Flask
 class Unavailable:
  t={'private_table':object()}
  def __init__(self,*args):pass
  def ready(self):raise SQLAlchemyError()
 monkeypatch.setattr(personal_api,'PersonalFeatures',Unavailable)
 app=Flask(__name__);app.extensions.update(m7_engine=object(),auth_engine=object(),credential_store=SimpleNamespace())
 personal_api.install(app)
 assert app.extensions['personal_features'] is None
 assert app.extensions['credential_store'].personal_tables is Unavailable.t
