// Exercise the actual Pinia store and auth client, with transport failures only.
import assert from 'node:assert/strict'
import {createServer} from 'vite'
import {createPinia,setActivePinia} from 'pinia'
import {fileURLToPath} from 'node:url'
const server=await createServer({configFile:false,root:fileURLToPath(new URL('..',import.meta.url)),cacheDir:fileURLToPath(new URL('../../.local-cache/account-menu-test-vite',import.meta.url)),optimizeDeps:{noDiscovery:true,include:[]},server:{middlewareMode:true},appType:'custom'})
const originalFetch=globalThis.fetch
const checks=[]
try{
 const {useAuthStore}=await server.ssrLoadModule('/src/stores/auth.ts')
 let failure='',posts=0
 globalThis.fetch=async(url,options={})=>{
  assert.ok(String(url).startsWith('/api/v1/auth/'),'logout must not call a model/provider')
  if(failure==='offline')throw new TypeError('Synthetic offline transport')
  const logout=String(url).endsWith('/logout');if(logout)posts++
  const status=logout&&failure==='server'?503:logout&&failure==='csrf'?403:logout&&failure==='expired'?401:200
  return new Response(JSON.stringify({code:status===200?0:status,data:logout?{}:{csrf_token:'synthetic-csrf'}}),{status,headers:{'Content-Type':'application/json'}})
 }
 for(const scenario of ['offline','server','csrf','success','expired']){
  setActivePinia(createPinia());const auth=useAuthStore();auth.user={id:1,username:'synthetic'};auth.ready=true;failure=scenario;posts=0
  if(['offline','server','csrf'].includes(scenario)){
   await assert.rejects(()=>auth.logout());assert.equal(auth.user.username,'synthetic');assert.equal(auth.ready,true)
  }else{await auth.logout();assert.equal(auth.user,null);assert.equal(auth.ready,false)}
  assert.ok(posts<=1,'no automatic retry of a logout mutation')
  checks.push({name:scenario,passed:true})
 }
 console.log(JSON.stringify({all_pass:true,checks,model_dispatches:0}))
}finally{globalThis.fetch=originalFetch;await server.close()}
