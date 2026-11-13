"""Low-privilege, transactional TMDB catalog import. No DDL, shell, paths or keys."""
import hashlib,json,re
from datetime import datetime,timezone,date
from decimal import Decimal
from sqlalchemy import select,insert,update,text
from .models import CatalogBase
from .read_queries import _PERSON_ASSETS
from .tmdb_local import adapt,LICENSE
from app.m6_database import verify_m6_connection

MAX_MOVIES=5
PHOTO=re.compile(r'/[A-Za-z0-9_-]+\.(?:jpg|jpeg|png|webp)',re.I)
FIELDS=('id','adult','title','original_title','imdb_id','release_date','runtime','original_language','overview','genres','poster_path','backdrop_path','popularity','vote_average','vote_count','revenue','budget','production_countries')
class ImportRejected(ValueError):pass

def canonical(value):return json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False)
def digest(value):return hashlib.sha256(canonical(value).encode()).hexdigest()
def capture(raw):
    if type(raw) is not dict:raise ImportRejected('INVALID_UPSTREAM')
    payload={k:raw.get(k) for k in FIELDS}
    entries=[];credits=raw.get('credits') or {}
    if type(credits) is not dict or type(credits.get('crew',[])) is not list or any(type(p) is not dict for p in credits.get('crew',[])):raise ImportRejected('INVALID_CREDITS')
    for role,rows in [('cast',credits.get('cast',[])),('crew',[p for p in credits.get('crew',[]) if p.get('job')=='Director'])]:
        if type(rows) is not list or len(rows)>2000:raise ImportRejected('INVALID_CREDITS')
        for p in rows:
            if type(p) is not dict:raise ImportRejected('INVALID_PERSON')
            entry={k:p.get(k) for k in ('id','name','original_name','credit_id','character','order','profile_path')}
            entry['role']=role;entries.append(entry)
    payload['entries']=entries
    validate(payload)
    return payload

def validate(payload):
    raw={k:v for k,v in payload.items() if k!='entries'}
    encoded=canonical(raw).encode();tid=raw.get('id')
    evidence=dict(license_ref=LICENSE,http_status='200',validated=True,sha256=hashlib.sha256(encoded).hexdigest(),acquired_utc=datetime.now(timezone.utc).isoformat(),url=f'https://api.themoviedb.org/3/movie/{tid}?language=zh-CN')
    row=adapt(encoded,evidence)
    if raw.get('adult') is not False:raise ImportRejected('ADULT_OR_NON_MOVIE')
    for name in ('poster_path','backdrop_path'):
        if raw.get(name) and not PHOTO.fullmatch(raw[name]):raise ImportRejected('INVALID_ASSET')
    seen={}
    for p in payload['entries']:
        if type(p.get('id')) is not int or not 1<=p['id']<=9999999999:raise ImportRejected('INVALID_PERSON')
        for name in ('name','original_name','character'):
            value=p.get(name)
            if value is not None and (type(value) is not str or len(value)>255):raise ImportRejected('INVALID_PERSON_TEXT')
        if not p.get('name') or p['role'] not in ('cast','crew') or not re.fullmatch(r'[A-Za-z0-9_-]{1,100}',str(p.get('credit_id') or '')):raise ImportRejected('INVALID_CREDIT')
        if p.get('order') is not None and (type(p['order']) is not int or not 0<=p['order']<=100000):raise ImportRejected('INVALID_ORDER')
        if p.get('profile_path') and not PHOTO.fullmatch(p['profile_path']):raise ImportRejected('INVALID_PHOTO')
        key=(p['role'],p['credit_id'])
        if key in seen and seen[key]!=p:raise ImportRejected('DUPLICATE_CREDIT_CONFLICT')
        seen[key]=p
    return row

class CatalogImporter:
    def __init__(self,engine,config):self.engine=engine;self.config=config;self.t=CatalogBase.metadata.tables
    def plan(self,c,payload):
        row=validate(payload);ext=self.t['movie_external_ids'];m=self.t['movies']
        mid=c.scalar(select(ext.c.movie_id).where(ext.c.source=='tmdb',ext.c.external_id==str(payload['id'])))
        if mid:
            old=c.execute(select(m).where(m.c.movie_id==mid)).mappings().one()
            if old['catalog_status']!='active':raise ImportRejected('MOVIE_NOT_ACTIVE')
            if old['release_year'] and row['release_year'] and old['release_year']!=row['release_year']:raise ImportRejected('YEAR_REQUIRES_REVIEW')
        for source,eid in row['external_ids'].items():
            owner=c.scalar(select(ext.c.movie_id).where(ext.c.source==source,ext.c.external_id==eid))
            if owner and owner!=mid:raise ImportRejected('IDENTITY_CONFLICT')
            if mid:
                prior=c.scalar(select(ext.c.external_id).where(ext.c.movie_id==mid,ext.c.source==source))
                if prior and prior!=eid:raise ImportRejected('IDENTITY_CONFLICT')
        return dict(tmdb_id=payload['id'],movie_id=mid,title=row['title'],year=row['release_year'],action='supplement' if mid else 'append',cast=sum(p['role']=='cast' for p in payload['entries']),photos=sum(bool(p.get('profile_path')) for p in payload['entries']))
    def preview(self,payloads):
        plans=[]
        with self.engine.connect() as c:
            verify_m6_connection(c,self.config)
            for payload in payloads:plans.append(self.plan(c,payload))
        return plans
    def receipt(self,jobid):
        t=self.t['ingest_batches']
        with self.engine.connect() as c:
            rows=c.execute(select(t.c.counts_json).where(t.c.source_snapshot_ref=='admin-import:'+jobid,t.c.status=='succeeded')).scalars().all()
        if len(rows)>1:raise ImportRejected('AMBIGUOUS_RECEIPT')
        return rows[0] if rows else None
    def apply(self,jobid,payloads,backup,expected_plans=None):
        # Same advisory lock as the existing M9 append importer.
        lock='m6import:'+self.config['M6_SCHEMA'];c=self.engine.connect();locked=False;committing=False
        try:
            verify_m6_connection(c,self.config);c.commit()
            if c.scalar(text('SELECT GET_LOCK(:name,10)'),dict(name=lock))!=1:raise ImportRejected('IMPORT_BUSY')
            locked=True;c.commit()
            prior=self.receipt(jobid)
            if prior:return dict(prior,replayed=True)
            with c.begin():
                plans=[self.plan(c,p) for p in payloads]
                if expected_plans is not None and plans!=expected_plans:raise ImportRejected('PREVIEW_TARGET_CHANGED')
                # Rollback journal contains only affected catalog rows, not auth data.
                before={};mids=[p['movie_id'] for p in plans if p['movie_id']]
                for name in ('movies','movie_external_ids','movie_source_records','movie_ratings_summary','movie_genres','movie_credits','movie_assets'):
                    t=self.t[name];before[name]=[dict(r) for r in c.execute(select(t).where(t.c.movie_id.in_(mids))).mappings()] if mids else []
                pids={r['person_id'] for r in before['movie_credits']}
                for name in ('people','person_external_ids'):
                    t=self.t[name];before[name]=[dict(r) for r in c.execute(select(t).where(t.c.person_id.in_(pids))).mappings()] if pids else []
                before['person_assets']=[dict(r) for r in c.execute(select(_PERSON_ASSETS).where(_PERSON_ASSETS.c.person_id.in_(pids))).mappings()] if pids else []
                backup(before)
                now=datetime.now(timezone.utc).replace(tzinfo=None);b=self.t['ingest_batches']
                batch=c.execute(insert(b).values(source='tmdb',mode='write',status='running',started_at=now,source_snapshot_ref='admin-import:'+jobid,counts_json={})).inserted_primary_key[0]
                results=[self._apply_one(c,p,plan,batch,now) for p,plan in zip(payloads,plans)]
                result=dict(batch_id=batch,items=results,replayed=False,source_hashes=[digest(p) for p in payloads],backup_created=True)
                c.execute(update(b).where(b.c.batch_id==batch).values(status='succeeded',finished_at=now,counts_json=result))
                committing=True
            verified=self.receipt(jobid)
            if verified!=result:raise ImportRejected('COMMIT_REQUIRES_RECONCILIATION')
            from .recommendation_data import invalidate
            invalidate(self.engine)
            return result
        except Exception as e:
            if committing:raise ImportRejected('COMMIT_REQUIRES_RECONCILIATION') from None
            raise
        finally:
            if c.in_transaction():c.rollback()
            if locked:
                try:c.execute(text('SELECT RELEASE_LOCK(:name)'),dict(name=lock));c.commit()
                except Exception:pass
            c.close()
    def _apply_one(self,c,p,plan,batch,now):
        t=self.t;row=validate(p);mid=plan['movie_id'];counts={}
        def put(table_name,**values):
            table=_PERSON_ASSETS if table_name=='person_assets' else t[table_name]
            result=c.execute(insert(table).values(**values));counts[table_name]=counts.get(table_name,0)+1
            return result.inserted_primary_key[0] if len(table.primary_key.columns)==1 else None
        if not mid:
            mid=put('movies',title=row['title'],original_title=row['original_title'],release_date=date.fromisoformat(row['release_date']) if row['release_date'] else None,release_year=row['release_year'],runtime_minutes=row['runtime_minutes'],original_language=row['original_language'],overview=row['overview'],is_adult=False,catalog_status='active',created_at=now,updated_at=now)
        src=t['movie_source_records'];source_id=str(p['id'])+':admin:'+digest(p)
        record=c.scalar(select(src.c.id).where(src.c.source=='tmdb',src.c.source_record_id==source_id))
        if record:
            owner=c.scalar(select(src.c.movie_id).where(src.c.id==record))
            if owner!=mid:raise ImportRejected('SOURCE_OWNER_CONFLICT')
            return dict(tmdb_id=p['id'],movie_id=mid,inserted={},already_present=True)
        record=put('movie_source_records',movie_id=mid,source='tmdb',source_record_id=source_id,fetched_at=now,payload_hash=digest(p),metadata_json={k:v for k,v in p.items() if k!='entries'},ingest_batch_id=batch,license_status='allowed')
        for source,eid in row['external_ids'].items():
            table=t['movie_external_ids']
            if not c.scalar(select(table.c.id).where(table.c.movie_id==mid,table.c.source==source)):
                put('movie_external_ids',movie_id=mid,source=source,external_id=eid,verified_method='tmdb_api',verified_at=now,created_at=now)
        for genre in p.get('genres') or []:
            table=t['genres'];gid=c.scalar(select(table.c.genre_id).where(table.c.tmdb_genre_id==str(genre['id'])))
            if not gid:gid=put('genres',name_zh=genre['name'],name_en=None,tmdb_genre_id=str(genre['id']))
            table=t['movie_genres']
            if not c.scalar(select(table.c.genre_id).where(table.c.movie_id==mid,table.c.genre_id==gid)):put('movie_genres',movie_id=mid,genre_id=gid)
        for fact in row['rating_facts']:
            table=t['movie_ratings_summary'];where=(table.c.movie_id==mid)&(table.c.source=='tmdb')
            values=dict(score_value=Decimal(fact['score_value']) if fact['score_value'] is not None else None,score_scale=10,vote_count=fact['vote_count'],observed_at=now,selected_source_record_pk=record)
            if c.scalar(select(table.c.movie_id).where(where)):
                c.execute(update(table).where(where).values(**values));counts['ratings_updated']=counts.get('ratings_updated',0)+1
            else:put('movie_ratings_summary',movie_id=mid,source='tmdb',**values)
        for kind in ('poster','backdrop'):
            path=p.get(kind+'_path');table=t['movie_assets']
            if path and not c.scalar(select(table.c.asset_id).where(table.c.movie_id==mid,table.c.asset_type==kind,table.c.source=='tmdb',table.c.source_path==path)):
                primary=c.scalar(select(table.c.asset_id).where(table.c.movie_id==mid,table.c.asset_type==kind,table.c.is_primary==True))
                put('movie_assets',movie_id=mid,asset_type=kind,source='tmdb',source_path=path,is_primary=primary is None,license_status='allowed',updated_at=now)
        for person in p['entries']:
            table=t['person_external_ids'];pid=c.scalar(select(table.c.person_id).where(table.c.source=='tmdb',table.c.external_id==str(person['id'])))
            if not pid:
                pid=put('people',name=person['name'],original_name=person.get('original_name'),created_at=now,updated_at=now)
                put('person_external_ids',person_id=pid,source='tmdb',external_id=str(person['id']),verified_method='tmdb_credits',verified_at=now)
            path=person.get('profile_path');table=_PERSON_ASSETS
            if path and not c.scalar(select(table.c.asset_id).where(table.c.person_id==pid,table.c.source=='tmdb',table.c.source_path==path)):
                primary=c.scalar(select(table.c.asset_id).where(table.c.person_id==pid,table.c.is_primary==True))
                put('person_assets',person_id=pid,source='tmdb',source_path=path,is_primary=primary is None,license_status='unknown',updated_at=now)
            role=person['role'];key=role+':'+person['credit_id'];table=t['movie_credits']
            existing=c.execute(select(table.c.person_id,table.c.character_name,table.c.sort_order).where(table.c.movie_id==mid,table.c.source=='tmdb',table.c.source_credit_key==key)).first()
            expected=(pid,person.get('character') or None if role=='cast' else None,int(person.get('order') or 0) if role=='cast' else 0)
            if existing:
                if tuple(existing)!=expected:raise ImportRejected('CREDIT_REQUIRES_REVIEW')
            else:put('movie_credits',movie_id=mid,person_id=pid,source='tmdb',source_credit_key=key,role_type=role,job='Director' if role=='crew' else None,character_name=expected[1],sort_order=expected[2])
        put('ingest_items',batch_id=batch,source_record_id=source_id,status='inserted' if plan['action']=='append' else 'updated',movie_id=mid,payload_hash=digest(p),attempt_count=1,updated_at=now)
        return dict(tmdb_id=p['id'],movie_id=mid,inserted=counts,already_present=False)
