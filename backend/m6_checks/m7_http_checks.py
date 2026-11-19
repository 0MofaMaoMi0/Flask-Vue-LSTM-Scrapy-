"""Actual HTTP/M6/MySQL integration. Only new synthetic accounts and behavior."""
import urllib.request,urllib.error,http.cookiejar,json,secrets,time,sys
from pathlib import Path
BASE='http://127.0.0.1:5174'
checks=[]
def check(name,value):
    checks.append({'name':name,'passed':bool(value)})
    if not value:raise AssertionError(name)
class Client:
    def __init__(self):self.opener=urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()));self.token=''
    def call(self,path,body=None,csrf=True):
        headers={'Origin':BASE,'Content-Type':'application/json'}
        if csrf:headers['X-CSRF-Token']=self.token
        req=urllib.request.Request(BASE+path,data=json.dumps(body).encode() if body is not None else None,headers=headers)
        try:r=self.opener.open(req,timeout=15)
        except urllib.error.HTTPError as e:r=e
        return r.status,json.load(r)
    def login_new(self):
        self.token=self.call('/api/v1/auth/csrf')[1]['data']['csrf_token'];name='m7_'+secrets.token_hex(8);password=secrets.token_urlsafe(24)
        status,_=self.call('/api/v1/auth/register',{'username':name,'password':password});check('register_new_synthetic_user',status==201)
        status,_=self.call('/api/v1/auth/login',{'username':name,'password':password});check('login_real_mysql',status==200)
        self.token=self.call('/api/v1/auth/csrf')[1]['data']['csrf_token']
    def query(self,message,cid=None,rid=None):return self.call('/api/v1/assistant/query',{'message':message,'conversation_id':cid,'request_id':rid or secrets.token_hex(12)})
def main():
    anonymous=Client();check('anonymous_denied',anonymous.call('/api/v1/assistant/status')[0]==401)
    a=Client();a.login_new();s,b=a.call('/api/v1/assistant/status');check('local_ready_cloud_disabled',s==200 and b['data']['local_available'] and not b['data']['cloud_available'])
    s,b=a.query('推荐电影');check('recommend_local',s==200 and b['data']['source']=='LOCAL' and b['data']['cost']['paid_model_request'] is False)
    data=b['data'];cid=data['conversation_id'];check('real_existing_candidates',bool(data['items']))
    rid=secrets.token_hex(12);first=a.query('2010年之后的科幻片',cid,rid);second=a.query('2010年之后的科幻片',cid,rid);check('retry_idempotent',first==second)
    s,b=a.query('不要恐怖，只看我没看过的',cid);check('multiturn_mysql_session',s==200 and b['data']['conversation_id']==cid)
    s,b=a.query('2099年之后的电影');check('empty_is_honest',s==200 and b['data']['items']==[])
    s,b=a.call('/api/v1/assistant/query',{'message':'推荐电影','request_id':secrets.token_hex(12),'user_id':999});check('identity_injection_denied',s==400)
    s,b=a.call('/api/v1/assistant/query',{'message':'推荐电影','request_id':secrets.token_hex(12)},csrf=False);check('csrf_denied',s==403)
    other=Client();other.login_new();s,_=other.query('推荐电影',cid);check('cross_user_conversation_denied',s==404)
    s,b=other.call('/api/v1/assistant/explain',{'movie_id':data['items'][0]['movie_id'],'request_id':secrets.token_hex(12)});check('explain_disabled_falls_back',s==200 and b['data']['source']=='FALLBACK' and not b['data']['cost']['paid_model_request'])
if __name__=='__main__':
    output=Path(sys.argv[1]);output.parent.mkdir(parents=True,exist_ok=True)
    try:main()
    finally:output.write_text(json.dumps({'all_pass':bool(checks) and all(x['passed'] for x in checks),'checks':checks},ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'all_pass':all(x['passed'] for x in checks),'checks':len(checks)}))
