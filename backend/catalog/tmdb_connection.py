"""Session-authorized TMDB reads with account-encrypted credentials and public-data caching."""
import contextvars,hashlib,json,os,re,secrets,threading,time,urllib.request,urllib.error,urllib.parse
from contextlib import contextmanager
from datetime import datetime,timezone
from pathlib import Path
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from assistant.agent_provider import NoRedirect

BOARD_PATHS={'popular':'movie/popular','trending_day':'trending/movie/day','trending_week':'trending/movie/week'}
BOARD_TTL=900
DETAIL_TTL=86400
MAX_DETAIL_REFRESH=6
_current=contextvars.ContextVar('tmdb_connection',default=None)

def stamp():return datetime.now(timezone.utc).isoformat()
def age(doc):
    try:return (datetime.now(timezone.utc)-datetime.fromisoformat(doc['captured_at'])).total_seconds()
    except (ValueError,TypeError,KeyError):return float('inf')

class TMDBError(Exception):
    def __init__(self,code,retry_after=0):self.code=code;self.retry_after=retry_after;super().__init__(code)

@contextmanager
def using(service,user,binding):
    token=_current.set((service,user,binding) if service else None)
    try:yield
    finally:_current.reset(token)

def current():return _current.get()
def request_binding(app):
    from flask import request
    return hashlib.sha256(request.cookies.get(app.config.get('AUTH_COOKIE_NAME','mi2_m6_sid'),'').encode()).hexdigest()

class TMDBConnection:
    _preferred_host=None
    _preferred_until=0
    def __init__(self,folder,*,ttl=7200,capacity=64,transport=None,store=None):
        self.folder=Path(folder);self.ttl=ttl;self.capacity=capacity;self._cipher=AESGCM(os.urandom(32));self.store=store
        self._entries={};self._lock=threading.RLock();self._resource_locks={};self._boards={};self._details={};self._transport=transport
        self._network_lock=threading.Lock();self._next_request=0;self._slots=threading.BoundedSemaphore(2)
        self._genres=None
        for channel in BOARD_PATHS:
            doc=self._read(self.folder/'boards'/(channel+'.json'))
            if doc and doc.get('schema')==1 and doc.get('channel')==channel and type(doc.get('items')) is list and age(doc)!=float('inf'):self._boards[channel]=doc
        # Read only files created by this service. Credentials are never written.
        for file in (self.folder/'details').glob('*.json'):
            if file.stem.isdecimal():
                doc=self._read(file)
                if doc and doc.get('schema')==1 and type(doc.get('movie')) is dict and str(doc['movie'].get('id'))==file.stem and age(doc)!=float('inf'):self._details[int(file.stem)]=doc
    def _prune(self):
        for key,e in list(self._entries.items()):
            if e['expires']<=time.monotonic():del self._entries[key]
    def put(self,user,binding,body):
        if type(body) is not dict or set(body)-{'access_token','consent'} or body.get('consent') is not True:raise ValueError('Consent required')
        token=body.get('access_token')
        if type(token) is not str:raise ValueError('Token')
        if token.startswith('Bearer '):token=token[7:]
        if not re.fullmatch(r'[A-Za-z0-9_.-]{20,2048}',token):raise ValueError('Token')
        generation=self.store.save(user,binding,'tmdb',dict(access_token=token,enabled=True)) if self.store else secrets.token_hex(16)
        with self._lock:
            self._prune();key=(user,binding)
            if key not in self._entries and len(self._entries)>=self.capacity:
                if not self.store:raise TMDBError('CUSTODY_CAPACITY')
                self._entries.pop(next(iter(self._entries)),None)
            nonce=os.urandom(12)
            self._entries[key]=dict(nonce=nonce,secret=self._cipher.encrypt(nonce,token.encode(),f'{user}:{binding}'.encode()),generation=generation,expires=time.monotonic()+self.ttl,verification='UNVERIFIED',last_error=None,last_success_at=None,last_checked_at=None,cooldown=0,last_verify=0,enabled=True)
        return self.status(user,binding)
    def _entry(self,user,binding):
        with self._lock:
            self._prune();index=(user,binding);e=self._entries.get(index)
            if self.store:
                record=self.store.load(user,binding,'tmdb')
                if not record:self._entries.pop(index,None);return None
                if not e or e['generation']!=record['generation']:
                    p=record['payload'];nonce=os.urandom(12)
                    if len(self._entries)>=self.capacity:self._entries.pop(next(iter(self._entries)),None)
                    e=dict(nonce=nonce,secret=self._cipher.encrypt(nonce,p['access_token'].encode(),f'{user}:{binding}'.encode()),generation=record['generation'],expires=time.monotonic()+self.ttl,verification=record['verification'],last_error=None,last_success_at=None,last_checked_at=None,cooldown=0,last_verify=0,enabled=p['enabled']);self._entries[index]=e
                else:e['verification']=record['verification']
            return e
    def status(self,user,binding):
        with self._lock:
            e=self._entry(user,binding)
            return dict(configured=bool(e),enabled=bool(e and e['enabled']),verification=('DISABLED' if not e['enabled'] else e['verification']) if e else 'DISCONNECTED',last_error=e['last_error'] if e else None,last_success_at=e['last_success_at'] if e else None,last_checked_at=e['last_checked_at'] if e else None,expires_in_seconds=None if self.store else max(0,int(e['expires']-time.monotonic())) if e else 0,retry_after=max(0,int(e['cooldown']-time.monotonic())+1) if e else 0,persistent=bool(self.store),custody='ENCRYPTED_DATABASE' if self.store else 'ENCRYPTED_PROCESS_MEMORY',board_cache_seconds=BOARD_TTL,detail_cache_seconds=DETAIL_TTL,max_detail_refresh=MAX_DETAIL_REFRESH)
    def disconnect(self,user,binding):
        if self.store:self.store.enabled(user,binding,'tmdb',False)
        with self._lock:self._entries.pop((user,binding),None)
    def enable(self,user,binding):
        if not self.store:raise ValueError('No persisted configuration')
        self.store.enabled(user,binding,'tmdb',True)
        return self.status(user,binding)
    def clear_user(self,user):
        with self._lock:
            for key in list(self._entries):
                if key[0]==user:del self._entries[key]
    def disconnect_binding(self,binding):
        with self._lock:
            for key in list(self._entries):
                if key[1]==binding:del self._entries[key]
    def _check_generation(self,user,binding,generation):
        e=self._entry(user,binding)
        if not e or not e['enabled'] or e['generation']!=generation:raise TMDBError('DISCONNECTED')
        return e
    def _failed(self,user,binding,generation,error):
        if error.code in ('COOLDOWN','DISCONNECTED','NOT_CONNECTED'):return
        with self._lock:
            e=self._entries.get((user,binding))
            if e and e['generation']==generation:
                e.update(last_error=error.code,last_checked_at=stamp(),cooldown=time.monotonic()+max(30,error.retry_after))
                if error.code=='KEY_REJECTED':e['verification']='KEY_REJECTED'
        if self.store and error.code=='KEY_REJECTED':self.store.verified(user,binding,'tmdb',generation,'KEY_REJECTED')
    def _get(self,user,binding,path,params=None):
        e=self._entry(user,binding)
        if not e:raise TMDBError('NOT_CONNECTED')
        if not e['enabled']:raise TMDBError('DISABLED')
        if e['verification']=='KEY_REJECTED':raise TMDBError('KEY_REJECTED')
        if e['cooldown']>time.monotonic():raise TMDBError('COOLDOWN',max(1,int(e['cooldown']-time.monotonic())))
        generation=e['generation'];token=self._cipher.decrypt(e['nonce'],e['secret'],f'{user}:{binding}'.encode()).decode()
        try:
            with self._slots:
                with self._network_lock:
                    wait=self._next_request-time.monotonic()
                    if wait>0:time.sleep(wait)
                    self._next_request=time.monotonic()+.25
                self._check_generation(user,binding,generation)
                data=self._transport(path,params or {},token) if self._transport else self._http(path,params or {},token,lambda:self._check_generation(user,binding,generation))
            if type(data) is not dict:raise TMDBError('INVALID_UPSTREAM')
            with self._lock:
                e=self._check_generation(user,binding,generation);e.update(verification='VERIFIED',last_error=None,last_checked_at=stamp(),last_success_at=stamp())
            if self.store:self.store.verified(user,binding,'tmdb',generation,'VERIFIED')
            return data
        except TMDBError as error:
            self._failed(user,binding,generation,error)
            raise
    @staticmethod
    def _http(path,params,token,alive=None):
        # No caller-provided host, path, headers, redirects, proxy, or TLS bypass.
        if path not in ('configuration','genre/movie/list','search/movie',*BOARD_PATHS.values()) and re.fullmatch(r'movie/[1-9][0-9]{0,9}',path) is None:raise TMDBError('INVALID_RESOURCE')
        hosts=['api.themoviedb.org','api.tmdb.org']
        if TMDBConnection._preferred_host in hosts and TMDBConnection._preferred_until>time.monotonic():
            hosts.remove(TMDBConnection._preferred_host);hosts.insert(0,TMDBConnection._preferred_host)
        for index,host in enumerate(hosts):
            if alive:alive()
            headers={'Accept':'application/json'}
            if token:headers['Authorization']='Bearer '+token
            req=urllib.request.Request('https://'+host+'/3/'+path+'?'+urllib.parse.urlencode(params),headers=headers)
            try:
                with urllib.request.build_opener(NoRedirect,urllib.request.ProxyHandler({})).open(req,timeout=5) as response:raw=response.read(1500001)
                if len(raw)>1500000:raise TMDBError('INVALID_UPSTREAM')
                try:
                    value=json.loads(raw)
                    TMDBConnection._preferred_host=host;TMDBConnection._preferred_until=time.monotonic()+600
                    return value
                except (ValueError,UnicodeError):raise TMDBError('INVALID_UPSTREAM') from None
            except urllib.error.HTTPError as error:
                if error.code in (401,403):raise TMDBError('KEY_REJECTED') from None
                if error.code==429:
                    raw_retry=error.headers.get('Retry-After','60')
                    try:
                        from email.utils import parsedate_to_datetime
                        retry=int(raw_retry) if raw_retry.isdecimal() else int((parsedate_to_datetime(raw_retry)-datetime.now(timezone.utc)).total_seconds())+1
                    except (ValueError,TypeError,OverflowError):retry=60
                    raise TMDBError('RATE_LIMITED',max(1,retry)) from None
                if error.code==404:raise TMDBError('NOT_FOUND') from None
                if index==len(hosts)-1:raise TMDBError('UPSTREAM_UNAVAILABLE') from None
            except (urllib.error.URLError,TimeoutError,OSError):
                if index==len(hosts)-1:raise TMDBError('NETWORK_UNAVAILABLE') from None
    def verify(self,user,binding):
        with self._lock:
            e=self._entry(user,binding)
            if not e:raise TMDBError('NOT_CONNECTED')
            if time.monotonic()-e['last_verify']<10:raise TMDBError('CHECK_THROTTLED',10)
            e['last_verify']=time.monotonic()
        data=self._get(user,binding,'configuration')
        if type(data.get('images')) is not dict:
            error=TMDBError('INVALID_UPSTREAM');self._failed(user,binding,e['generation'],error);raise error
        return self.status(user,binding)
    @staticmethod
    def _read(path):
        try:
            if path.is_symlink() or path.stat().st_size>1500000:return None
            doc=json.loads(path.read_text(encoding='utf-8'))
            return doc if type(doc) is dict else None
        except (OSError,ValueError):return None
    def _save(self,kind,name,doc):
        parent=self.folder/kind
        try:
            self.folder.mkdir(parents=True,exist_ok=True,mode=0o700)
            parent.mkdir(parents=True,exist_ok=True,mode=0o700)
        except OSError:raise TMDBError('CACHE_UNAVAILABLE') from None
        if self.folder.is_symlink() or parent.is_symlink():raise TMDBError('CACHE_UNAVAILABLE')
        temp=parent/(name+'.'+secrets.token_hex(8)+'.tmp');target=parent/(name+'.json')
        try:
            with temp.open('x',encoding='utf-8') as stream:
                os.chmod(temp,0o600);json.dump(doc,stream,ensure_ascii=False,separators=(',',':'))
            os.replace(temp,target)
        except OSError:raise TMDBError('CACHE_UNAVAILABLE') from None
    def _resource_lock(self,key):
        with self._lock:return self._resource_locks.setdefault(key,threading.Lock())
    def snapshot(self,channel,user=None,binding=None,*,force=False):
        if channel not in BOARD_PATHS:raise ValueError('Channel')
        with self._resource_lock(channel):
            doc=self._boards.get(channel) or self._read(self.folder/'boards'/(channel+'.json'))
            if doc and (doc.get('schema')!=1 or doc.get('channel')!=channel or type(doc.get('items')) is not list or age(doc)==float('inf')):doc=None
            if doc:self._boards[channel]=doc
            if user is None:return doc
            entry=self._entry(user,binding)
            if not entry or not entry['enabled']:return doc
            if doc and age(doc)<(60 if force else BOARD_TTL):return doc
            e=self._entry(user,binding);generation=e['generation']
            try:
                if not self._genres or age(self._genres)>=DETAIL_TTL:
                    genres=self._get(user,binding,'genre/movie/list',{'language':'zh-CN'}).get('genres')
                    if type(genres) is not list or any(type(g) is not dict or type(g.get('id')) is not int or type(g.get('name')) is not str for g in genres):raise TMDBError('INVALID_UPSTREAM')
                    self._genres={'genres':genres,'captured_at':stamp()}
                items=[];seen=set()
                for page in range(1,4):
                    result=self._get(user,binding,BOARD_PATHS[channel],{'language':'zh-CN','page':page})
                    if type(result.get('results')) is not list:raise TMDBError('INVALID_UPSTREAM')
                    for offset,row in enumerate(result['results'][:20]):
                        if type(row) is not dict or type(row.get('id')) is not int or row['id']<1 or row['id'] in seen or row.get('adult') is True:continue
                        if type(row.get('title')) is not str or type(row.get('genre_ids')) is not list or any(type(g) is not int for g in row['genre_ids']):raise TMDBError('INVALID_UPSTREAM')
                        seen.add(row['id']);item={k:row.get(k) for k in ('id','title','original_title','release_date','original_language','poster_path','genre_ids','vote_average','vote_count','popularity','adult','overview')};item['source_rank']=(page-1)*20+offset+1;items.append(item)
                new={'schema':1,'channel':channel,'endpoint':BOARD_PATHS[channel],'captured_at':stamp(),'items':items,'genres':self._genres['genres'],'pages':3}
                with self._lock:
                    self._check_generation(user,binding,generation);self._save('boards',channel,new);self._boards[channel]=new
                return new
            except TMDBError as error:
                self._failed(user,binding,generation,error)
                if force:raise
                return doc
    def detail(self,user,binding,tmdb_id,*,force=False):
        if type(tmdb_id) is not int or not 1<=tmdb_id<=9999999999:raise ValueError('TMDB ID')
        with self._resource_lock('movie:'+str(tmdb_id)):
            doc=self._details.get(tmdb_id)
            if doc and age(doc)<(60 if force else DETAIL_TTL):return doc
            e=self._entry(user,binding)
            if not e:raise TMDBError('NOT_CONNECTED')
            generation=e['generation'];raw=self._get(user,binding,'movie/'+str(tmdb_id),{'language':'zh-CN','append_to_response':'credits'})
            if raw.get('id')!=tmdb_id or type(raw.get('id')) is not int or raw.get('adult') is True:
                error=TMDBError('INVALID_UPSTREAM');self._failed(user,binding,generation,error);raise error
            movie={k:raw.get(k) for k in ('id','title','original_title','release_date','runtime','original_language','overview','genres','poster_path','backdrop_path','popularity','vote_average','vote_count','revenue','budget')}
            credits=raw.get('credits') or {};movie['cast']=[{k:p.get(k) for k in ('id','name','character','profile_path','order')} for p in credits.get('cast',[])[:30] if type(p) is dict]
            movie['directors']=[{k:p.get(k) for k in ('id','name','profile_path')} for p in credits.get('crew',[]) if type(p) is dict and p.get('job')=='Director'][:10]
            new={'schema':1,'captured_at':stamp(),'movie':movie}
            with self._lock:
                self._check_generation(user,binding,generation);self._save('details',str(tmdb_id),new);self._details[tmdb_id]=new
            return new
    def search(self,user,binding,title,year=None):
        if type(title) is not str or not 1<=len(title)<=200 or (year is not None and (type(year) is not int or not 1880<=year<=2100)):raise ValueError('Search')
        params={'query':title,'language':'zh-CN','include_adult':'false'}
        if year:params['year']=year
        rows=self._get(user,binding,'search/movie',params).get('results',[])
        if type(rows) is not list:raise TMDBError('INVALID_UPSTREAM')
        exact=[m for m in rows[:20] if type(m) is dict and type(m.get('id')) is int and m['id']>0 and m.get('adult') is not True and title.casefold().strip() in [str(m.get(k) or '').casefold().strip() for k in ('title','original_title')] and (not year or str(m.get('release_date') or '').startswith(str(year)))]
        return exact[0]['id'] if len({m['id'] for m in exact})==1 else None
    def enrich(self,movies):
        from .recommendation_data import features,valid_number
        facts={}
        with self._lock:
            for doc in self._boards.values():
                for item in doc['items']:
                    tid=str(item['id'])
                    facts.setdefault(tid,[]).append((item,doc['captured_at']))
            for tid,doc in self._details.items():
                facts.setdefault(str(tid),[]).append((doc['movie'],doc['captured_at']))
        for m in movies:
            for payload,observed in sorted(facts.get(str(m.get('tmdb_id')),[]),key=lambda record:datetime.fromisoformat(record[1])):
                # A newer heat-only list must not re-date or discard known revenue.
                old=m.get('feature_as_of');revenue=m.get('revenue');revenue_as_of=m.get('revenue_as_of') or old
                try:older=not old or datetime.fromisoformat(old)<=datetime.fromisoformat(observed)
                except ValueError:older=True
                if older:
                    features(m,payload,observed,'TMDB_ONLINE_CACHE')
                    if 'revenue' not in payload:m.update(revenue=revenue,revenue_as_of=revenue_as_of)
                elif 'revenue' in payload and (not revenue_as_of or datetime.fromisoformat(revenue_as_of)<=datetime.fromisoformat(observed)):
                    value=payload.get('revenue');m.update(revenue=int(value) if valid_number(value) and value>0 else None,revenue_as_of=observed)
        import math
        maximum=max((math.log1p(m['tmdb_popularity']) for m in movies if m.get('tmdb_popularity') is not None),default=0)
        for m in movies:m['catalog_popularity']=math.log1p(m['tmdb_popularity'])/maximum if maximum and m.get('tmdb_popularity') is not None else 0
        return movies

def enrich(movies):
    context=current()
    return context[0].enrich(movies) if context else movies
