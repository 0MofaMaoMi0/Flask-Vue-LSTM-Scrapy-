"""Account-level encrypted provider credentials, with bounded RAM caches and an isolated test fallback."""
import os,secrets,time,threading,re
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
MODELS=('deepseek-flash','deepseek-v4-pro')
class Vault:
    def __init__(self,ttl=1800,capacity=64,store=None):
        self._cipher=AESGCM(os.urandom(32));self._entries={};self._lock=threading.RLock();self.ttl=ttl;self.capacity=capacity;self.store=store
    def put(self,user,binding,body):
        if type(body) is not dict or set(body)-{'provider','model','api_key','daily_calls','max_output_tokens','enabled','consent','cache_enabled'}:raise ValueError('Invalid configuration')
        key=body.get('api_key');model=body.get('model','deepseek-flash');daily=body.get('daily_calls');output=body.get('max_output_tokens',2048)
        if body.get('provider','deepseek')!='deepseek' or model not in MODELS:raise ValueError('Unsupported provider/model')
        if type(key) is not str or not re.fullmatch(r'[a-zA-Z0-9_.-]{16,512}',key):raise ValueError('Invalid key format')
        if (daily is not None and (type(daily) is not int or not 1<=daily<=1000)) or type(output) is not int or not 256<=output<=8192:raise ValueError('Invalid limits')
        if body.get('consent') is not True or type(body.get('enabled',True)) is not bool or type(body.get('cache_enabled',False)) is not bool:raise ValueError('Explicit consent required')
        generation=self.store.save(user,binding,'deepseek',dict(api_key=key,model=model,daily_calls=daily,max_output_tokens=output,enabled=body.get('enabled',True),cache_enabled=body.get('cache_enabled',False))) if self.store else secrets.token_hex(16)
        nonce=os.urandom(12);aad=f'{user}:{binding}'.encode();index=(user,binding) if self.store else user
        with self._lock:
            self._prune()
            if index not in self._entries and len(self._entries)>=self.capacity:
                if not self.store:raise ValueError('Custody capacity reached')
                self._entries.pop(next(iter(self._entries)),None)
            self._entries[index]={'nonce':nonce,'secret':self._cipher.encrypt(nonce,key.encode(),aad),'binding':binding,'expires':time.monotonic()+self.ttl,'model':model,'daily_calls':daily,'max_output_tokens':output,'enabled':body.get('enabled',True),'generation':generation,'verification':'UNVERIFIED','cache_enabled':body.get('cache_enabled',False)}
        return self.status(user,binding)
    def _prune(self):
        for user,e in list(self._entries.items()):
            if e['expires']<=time.monotonic():del self._entries[user]
    def config(self,user,binding):
        with self._lock:
            self._prune();index=(user,binding) if self.store else user;e=self._entries.get(index)
            if self.store:
                record=self.store.load(user,binding,'deepseek')
                if not record:self._entries.pop(index,None);return None
                if not e or e['generation']!=record['generation']:
                    p=record['payload'];nonce=os.urandom(12)
                    if len(self._entries)>=self.capacity:self._entries.pop(next(iter(self._entries)),None)
                    e=dict(p,nonce=nonce,secret=self._cipher.encrypt(nonce,p['api_key'].encode(),f'{user}:{binding}'.encode()),binding=binding,expires=time.monotonic()+self.ttl,generation=record['generation'],verification=record['verification']);e.pop('api_key',None);self._entries[index]=e
                else:e['verification']=record['verification']
            return dict(e) if e and e['binding']==binding else None
    def key(self,user,binding,generation):
        e=self.config(user,binding)
        if not e or e['generation']!=generation or not e['enabled']:raise ValueError('Key disconnected or expired')
        return self._cipher.decrypt(e['nonce'],e['secret'],f'{user}:{binding}'.encode()).decode()
    def status(self,user,binding):
        e=self.config(user,binding)
        return {'configured':bool(e),'enabled':bool(e and e['enabled']),'provider':'deepseek','model':e['model'] if e else None,'verification':e['verification'] if e else 'DISCONNECTED','expires_in_seconds':None if self.store else max(0,int(e['expires']-time.monotonic())) if e else 0,'daily_calls':e['daily_calls'] if e else None,'max_output_tokens':e['max_output_tokens'] if e else 2048,'cache_enabled':bool(e and e['cache_enabled']),'custody':'ENCRYPTED_DATABASE' if self.store else 'ENCRYPTED_PROCESS_MEMORY','persistent':bool(self.store)}
    def verified(self,user,binding,generation,result):
        if self.store:self.store.verified(user,binding,'deepseek',generation,result)
        with self._lock:
            e=self._entries.get((user,binding) if self.store else user)
            if e and e['binding']==binding and e['generation']==generation:e['verification']=result
    def disconnect(self,user,binding):
        if self.store:self.store.enabled(user,binding,'deepseek',False)
        with self._lock:
            index=(user,binding) if self.store else user;e=self._entries.get(index)
            if e and e['binding']==binding:del self._entries[index]

    def enable(self,user,binding):
        if not self.store:raise ValueError('No persisted configuration')
        self.store.enabled(user,binding,'deepseek',True)
        return self.status(user,binding)

    def clear_user(self,user):
        with self._lock:
            for index in list(self._entries):
                if index==user or (type(index) is tuple and index[0]==user):del self._entries[index]

    def disconnect_binding(self,binding):
        with self._lock:
            for user,e in list(self._entries.items()):
                if e['binding']==binding:del self._entries[user]

def redact(text):
    text=re.sub(r'sk-[A-Za-z0-9_-]{8,}', '[REDACTED]',str(text))
    return re.sub(r'(?i)(?:password|api_key|token)\s*[=:]\s*\S+','[REDACTED]',text)
