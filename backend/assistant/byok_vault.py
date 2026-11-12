"""Session-bound, expiring encrypted RAM custody. Never persists a user's key."""
import os,secrets,time,threading,re
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
MODELS=('deepseek-flash','deepseek-v4-pro')
class Vault:
    def __init__(self,ttl=1800,capacity=64):
        self._cipher=AESGCM(os.urandom(32));self._entries={};self._lock=threading.RLock();self.ttl=ttl;self.capacity=capacity
    def put(self,user,binding,body):
        if type(body) is not dict or set(body)-{'provider','model','api_key','daily_calls','max_output_tokens','enabled','consent','cache_enabled'}:raise ValueError('Invalid configuration')
        key=body.get('api_key');model=body.get('model','deepseek-flash');daily=body.get('daily_calls');output=body.get('max_output_tokens',2048)
        if body.get('provider','deepseek')!='deepseek' or model not in MODELS:raise ValueError('Unsupported provider/model')
        if type(key) is not str or not re.fullmatch(r'[a-zA-Z0-9_.-]{16,512}',key):raise ValueError('Invalid key format')
        if (daily is not None and (type(daily) is not int or not 1<=daily<=1000)) or type(output) is not int or not 256<=output<=8192:raise ValueError('Invalid limits')
        if body.get('consent') is not True or type(body.get('enabled',True)) is not bool or type(body.get('cache_enabled',False)) is not bool:raise ValueError('Explicit consent required')
        nonce=os.urandom(12);aad=f'{user}:{binding}'.encode()
        with self._lock:
            self._prune()
            if user not in self._entries and len(self._entries)>=self.capacity:raise ValueError('Custody capacity reached')
            self._entries[user]={'nonce':nonce,'secret':self._cipher.encrypt(nonce,key.encode(),aad),'binding':binding,'expires':time.monotonic()+self.ttl,'model':model,'daily_calls':daily,'max_output_tokens':output,'enabled':body.get('enabled',True),'generation':secrets.token_hex(16),'verification':'UNVERIFIED','cache_enabled':body.get('cache_enabled',False)}
        return self.status(user,binding)
    def _prune(self):
        for user,e in list(self._entries.items()):
            if e['expires']<=time.monotonic():del self._entries[user]
    def config(self,user,binding):
        with self._lock:
            self._prune();e=self._entries.get(user)
            return dict(e) if e and e['binding']==binding else None
    def key(self,user,binding,generation):
        e=self.config(user,binding)
        if not e or e['generation']!=generation or not e['enabled']:raise ValueError('Key disconnected or expired')
        return self._cipher.decrypt(e['nonce'],e['secret'],f'{user}:{binding}'.encode()).decode()
    def status(self,user,binding):
        e=self.config(user,binding)
        return {'configured':bool(e),'enabled':bool(e and e['enabled']),'provider':'deepseek','model':e['model'] if e else None,'verification':e['verification'] if e else 'DISCONNECTED','expires_in_seconds':max(0,int(e['expires']-time.monotonic())) if e else 0,'daily_calls':e['daily_calls'] if e else None,'max_output_tokens':e['max_output_tokens'] if e else 2048,'cache_enabled':bool(e and e['cache_enabled']),'custody':'ENCRYPTED_PROCESS_MEMORY','persistent':False}
    def verified(self,user,binding,generation,result):
        with self._lock:
            e=self._entries.get(user)
            if e and e['binding']==binding and e['generation']==generation:e['verification']=result
    def disconnect(self,user,binding):
        with self._lock:
            e=self._entries.get(user)
            if e and e['binding']==binding:del self._entries[user]

    def disconnect_binding(self,binding):
        with self._lock:
            for user,e in list(self._entries.items()):
                if e['binding']==binding:del self._entries[user]

def redact(text):
    text=re.sub(r'sk-[A-Za-z0-9_-]{8,}', '[REDACTED]',str(text))
    return re.sub(r'(?i)(?:password|api_key|token)\s*[=:]\s*\S+','[REDACTED]',text)
