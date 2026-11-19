"""Allowlisted DeepSeek streaming provider. No redirect, proxy URL or auto retry."""
import json,time,urllib.request,urllib.error
from typing import Protocol
class AgentProvider(Protocol):
    paid: bool
    name: str
    model: str
    def verify(self) -> bool: ...
    def complete(self,messages,tools,cancel,emit,deadline) -> dict: ...
MODELS=('deepseek-flash','deepseek-v4-pro')
class ProviderError(Exception):
    def __init__(self,code='LLM_UNAVAILABLE',dispatched=True):super().__init__(code);self.code=code;self.dispatched=dispatched
class Cancelled(ProviderError):pass
class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self,*args):return None

def usage_value(raw):
    if raw is None:return None
    if type(raw) is not dict:raise ProviderError('INVALID_USAGE')
    result={}
    for source,target in [('prompt_tokens','input_tokens'),('completion_tokens','output_tokens'),('prompt_cache_hit_tokens','cached_input_tokens')]:
        value=raw.get(source)
        if value is not None and (type(value) is not int or not 0<=value<=10000000):raise ProviderError('INVALID_USAGE')
        result[target]=value
    return result
class DeepSeekAgentProvider:
    paid=True;name='deepseek'
    def __init__(self,key,model='deepseek-flash',max_tokens=2048):
        if model not in MODELS:raise ValueError('Unsupported model')
        self.key=key;self.model=model;self.max_tokens=max_tokens
    def _open(self,path,payload=None):
        if path not in ('/models','/chat/completions'):raise ProviderError('ENDPOINT_DENIED',False)
        try:req=urllib.request.Request('https://api.deepseek.com'+path,headers={'Authorization':'Bearer '+self.key,'Content-Type':'application/json'},data=json.dumps(payload,ensure_ascii=False).encode() if payload is not None else None)
        except (TypeError,ValueError,UnicodeError):raise ProviderError('INVALID_REQUEST',False) from None
        try:return urllib.request.build_opener(NoRedirect,urllib.request.ProxyHandler({})).open(req,timeout=15)
        except urllib.error.HTTPError as e:raise ProviderError('KEY_REJECTED' if e.code in (401,403) else 'PROVIDER_RATE_LIMITED' if e.code==429 else 'LLM_UNAVAILABLE') from None
        except Exception:raise ProviderError() from None
    def verify(self):
        with self._open('/models') as r:
            raw=r.read(262145)
        if len(raw)>262144:raise ProviderError('INVALID_RESPONSE')
        try:return self.model in [m['id'] for m in json.loads(raw)['data']]
        except Exception:raise ProviderError('INVALID_RESPONSE') from None
    def complete(self,messages,tools,cancel,emit,deadline):
        if cancel():raise Cancelled('CANCELLED',False)
        payload={'model':self.model,'messages':messages,'tools':tools,'thinking':{'type':'disabled'},'response_format':{'type':'json_object'},'max_tokens':self.max_tokens,'stream':True,'stream_options':{'include_usage':True}}
        if len(tools)==1 and tools[0]['function']['name']=='plan_movie_turn':payload['tool_choice']={'type':'function','function':{'name':'plan_movie_turn'}}
        content='';calls={};usage=None;finish=None;total=0;done=False
        with self._open('/chat/completions',payload) as response:
            while True:
                if cancel():raise Cancelled('CANCELLED')
                if time.monotonic()>deadline:raise ProviderError('TIMEOUT')
                line=response.readline(131073);total+=len(line)
                if len(line)>131072 or total>2097152:raise ProviderError('RESPONSE_LIMIT')
                if not line:break
                if not line.startswith(b'data:'):continue
                data=line[5:].strip()
                if data==b'[DONE]':done=True;break
                try:chunk=json.loads(data)
                except Exception:raise ProviderError('INVALID_JSON') from None
                if chunk.get('usage') is not None:usage=usage_value(chunk['usage'])
                choices=chunk.get('choices',[])
                if not choices:continue
                choice=choices[0];delta=choice.get('delta') or {};finish=choice.get('finish_reason') or finish
                text=delta.get('content') or ''
                if type(text) is not str:raise ProviderError('INVALID_RESPONSE')
                content+=text
                if len(content)>40000:raise ProviderError('OUTPUT_LIMIT')
                if text:emit(text)
                for tool in delta.get('tool_calls') or []:
                    index=tool.get('index')
                    if type(index) is not int or not 0<=index<8:raise ProviderError('TOOL_LIMIT')
                    entry=calls.setdefault(index,{'id':'','type':'function','function':{'name':'','arguments':''}})
                    if tool.get('id'):entry['id']+=tool['id']
                    for k in ('name','arguments'):entry['function'][k]+=tool.get('function',{}).get(k) or ''
                    if len(entry['function']['arguments'])>12000 or len(entry['function']['name'])>80 or len(entry['id'])>128:raise ProviderError('TOOL_LIMIT')
        if not done or finish not in ('stop','tool_calls'):raise ProviderError('TRUNCATED_RESPONSE')
        return {'content':content,'tool_calls':[calls[k] for k in sorted(calls)],'usage':usage,'finish_reason':finish}
class ScriptedAgentProvider:
    """Injectable tests only; never selected by a user's provider field."""
    paid=False;name='mock';model='mock-agent'
    def __init__(self,steps):self.steps=list(steps);self.calls=0
    def verify(self):return True
    def complete(self,messages,tools,cancel,emit,deadline):
        self.calls+=1;self.last_messages=messages
        if cancel():raise Cancelled('CANCELLED',False)
        step=self.steps.pop(0)
        if isinstance(step,Exception):raise step
        content=step.get('content','')
        for i in range(0,len(content),8):
            if cancel():raise Cancelled('CANCELLED')
            emit(content[i:i+8])
        return dict(step,usage=step.get('usage',{'input_tokens':100,'output_tokens':40,'cached_input_tokens':0}))
