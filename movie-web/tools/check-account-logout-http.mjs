// Actual frontend auth store -> local proxy -> isolated MySQL, synthetic user only.
import fs from 'node:fs/promises'
import assert from 'node:assert/strict'
import {fileURLToPath} from 'node:url'
import {createServer} from 'vite'
import {createPinia,setActivePinia} from 'pinia'
const fixturePath=process.env.MI2_ACCOUNT_BROWSER_FIXTURE
if(!fixturePath)throw new Error('Explicit synthetic fixture required')
const fixture=JSON.parse(await fs.readFile(fixturePath,'utf8'))
assert.ok(fixture.username.startsWith('accountmenu_ui_'))
const server=await createServer({configFile:false,root:fileURLToPath(new URL('..',import.meta.url)),cacheDir:fileURLToPath(new URL('../../.local-cache/account-menu-test-vite',import.meta.url)),optimizeDeps:{noDiscovery:true,include:[]},server:{middlewareMode:true},appType:'custom'})
const originalFetch=globalThis.fetch,jar=new Map(),checks=[]
function check(name,ok){checks.push({name,passed:!!ok});assert.ok(ok,name)}
try{
 globalThis.fetch=async(path,options={})=>{
  assert.ok(path.startsWith('/api/v1/auth/')||path==='/api/v1/account/profile'||path==='/api/v1/account/close')
  const headers=new Headers(options.headers);headers.set('Origin','http://127.0.0.1:5174')
  if(jar.size)headers.set('Cookie',[...jar].map(([k,v])=>k+'='+v).join('; '))
  const response=await originalFetch('http://127.0.0.1:5174'+path,{...options,headers,signal:AbortSignal.timeout(15000)})
  for(const cookie of response.headers.getSetCookie()){
   const first=cookie.split(';')[0],equal=first.indexOf('=');const name=first.slice(0,equal),value=first.slice(equal+1)
   if(value)jar.set(name,value);else jar.delete(name)
  }
  return response
 }
 const {useAuthStore}=await server.ssrLoadModule('/src/stores/auth.ts')
 const {request,call}=await server.ssrLoadModule('/src/api/auth.ts')
 setActivePinia(createPinia());const auth=useAuthStore()
 await request('login',{username:fixture.username,password:fixture.password});await auth.signedIn()
 check('frontend_store_real_login',auth.user?.id===fixture.user_id)
 await auth.logout();check('frontend_store_real_logout',auth.user===null&&!auth.ready)
 await auth.initialize();check('revoked_session_cannot_restore_identity',auth.user===null)
 await request('login',{username:fixture.username,password:fixture.password});await auth.signedIn()
 check('synthetic_identity_can_relogin',auth.user?.id===fixture.user_id)
 const token=(await call('csrf')).csrf_token
 const response=await fetch('/api/v1/account/close',{method:'POST',headers:{'Content-Type':'application/json','X-CSRF-Token':token},body:JSON.stringify({password:fixture.password,confirmation:fixture.username,understand:true})})
 check('close_only_owned_synthetic_fixture',response.status===200&&(await response.json()).data.closed)
 await fs.unlink(fixturePath)
 console.log(JSON.stringify({all_pass:true,checks,model_dispatches:0,secrets_printed:false}))
}catch(error){console.log(JSON.stringify({all_pass:false,checks,error_type:error.constructor.name,secrets_printed:false}));process.exitCode=1}
finally{globalThis.fetch=originalFetch;await server.close()}
