import assert from 'node:assert/strict'
import {fileURLToPath} from 'node:url'
import {createServer} from 'vite'
import vue from '@vitejs/plugin-vue'
import {createSSRApp} from 'vue'
import {renderToString} from '@vue/server-renderer'
import {createPinia,setActivePinia} from 'pinia'
import {createRouter,createMemoryHistory} from 'vue-router'
const server=await createServer({configFile:false,plugins:[vue()],ssr:{noExternal:['vuetify']},root:fileURLToPath(new URL('..',import.meta.url)),cacheDir:fileURLToPath(new URL('../../.local-cache/settings-test-vite',import.meta.url)),optimizeDeps:{noDiscovery:true,include:[]},server:{middlewareMode:true},appType:'custom'})
const originalFetch=globalThis.fetch,originalWindow=globalThis.window,originalStorage=globalThis.localStorage,calls=[],checks=[]
function check(name,ok){checks.push({name,passed:!!ok});assert.ok(ok,name)}
try{
 globalThis.fetch=async path=>{
  calls.push(path);assert.equal(path,'/api/v1/assistant/status','opening settings must never start model generation')
  return new Response(JSON.stringify({code:'OK',data:{configured:false,enabled:false,persistent:true,model:'deepseek-flash'}}),{headers:{'Content-Type':'application/json'}})
 }
 const {createVuetify}=await server.ssrLoadModule('vuetify')
 const {default:Settings}=await server.ssrLoadModule('/src/components/GlobalSettings.vue')
 const {default:Explore}=await server.ssrLoadModule('/src/views/AIExploreView.vue')
 const {useAuthStore}=await server.ssrLoadModule('/src/stores/auth.ts')
 async function render(component,path){
  const pinia=createPinia();setActivePinia(pinia);const auth=useAuthStore(pinia);auth.user={id:1,username:'synthetic-settings'};auth.ready=true
  const router=createRouter({history:createMemoryHistory(),routes:[{path:'/:pathMatch(.*)*',component:{template:'<div/>'}}]})
  await router.push(path);await router.isReady()
  return renderToString(createSSRApp(component).use(pinia).use(router).use(createVuetify()))
 }
 for(const tab of ['account','display','ai','tmdb','privacy','about']){
  const html=await render(Settings,'/settings?tab='+tab+'&return_to=%2Fmovies%3Fkeyword%3Dtest')
  check('settings_'+tab+'_no_chat_mount',html.includes('全局设置')&&!html.includes('data-source-id="chatWindow"')&&!html.includes('data-source-id="composerForm"'))
  check('settings_'+tab+'_safe_source_return',html.includes('/movies?keyword=test'))
 }
 check('only_metadata_read_on_initial_render',calls.every(path=>path.endsWith('/status')))
 const {assistantJson,diagnosticRequestAfterError}=await server.ssrLoadModule('/src/services/assistantApi.ts')
 let mockPosts=0
 globalThis.fetch=async(path,options={})=>{
  if(path==='/api/v1/auth/csrf')return new Response(JSON.stringify({data:{csrf_token:'synthetic-csrf'}}))
  assert.equal(path,'/api/v1/assistant/test');assert.equal(options.headers['X-CSRF-Token'],'synthetic-csrf');mockPosts++
  return new Response(JSON.stringify({code:'UPSTREAM_RESULT_UNKNOWN'}),{status:503})
 }
 await assert.rejects(()=>assistantJson('test','POST',{confirm_paid:true,request_id:'synthetic-settings-request',stream:false}))
 check('unknown_test_result_never_auto_retried',mockPosts===1)
 for(const scenario of [{state:'FAILED',status:409},{state:'REJECTED',status:409},{state:'CANCELLED',status:409},{state:'EXPIRED',status:409},{state:undefined,status:403},{state:'PENDING',status:202},{state:undefined,status:503}]){
  let posts=0
  globalThis.fetch=async path=>{
   if(path==='/api/v1/auth/csrf')return new Response(JSON.stringify({data:{csrf_token:'synthetic-csrf'}}))
   posts++;return new Response(JSON.stringify({code:'SYNTHETIC_DIAGNOSTIC_RESPONSE',data:{state:scenario.state}}),{status:scenario.status})
  }
  let nextId='synthetic-request-id'
  try{await assistantJson('test','POST',{confirm_paid:true,request_id:nextId,stream:false});assert.fail('Expected diagnostic response')}
  catch(error){nextId=diagnosticRequestAfterError(nextId,error)}
  const canStartNew=scenario.status===409||scenario.status===403
  check('diagnostic_recovery_'+(scenario.state||scenario.status),posts===1&&(canStartNew?nextId===null:nextId==='synthetic-request-id'))
 }
 check('diagnostic_network_unknown_keeps_id',diagnosticRequestAfterError('synthetic-request-id',new TypeError('Synthetic network failure'))==='synthetic-request-id')
 globalThis.localStorage={getItem:()=>null,setItem:()=>{}}
 const chat=await render(Explore,'/ai');check('explore_has_chat_not_settings',chat.includes('data-source-id="chatWindow"')&&!chat.includes('data-source-id="globalSettingsPage"'))
 const {aiWorkspace,resetAIWorkspace}=await server.ssrLoadModule('/src/services/aiWorkspace.ts')
 const {clearAssistantDisplay,CLEAR_ASSISTANT_DISPLAY}=await server.ssrLoadModule('/src/services/assistantDisplay.ts')
 globalThis.window=new EventTarget();resetAIWorkspace(1);aiWorkspace.archives.explore=[{message:'synthetic-a'}];aiWorkspace.archives.floating=[{message:'synthetic-b'}]
 check('clear_one_surface_only',clearAssistantDisplay('explore')&&aiWorkspace.archives.explore.length===0&&aiWorkspace.archives.floating.length===1)
 const busy=e=>{e.detail.busy=true};window.addEventListener(CLEAR_ASSISTANT_DISPLAY,busy)
 check('busy_surface_cannot_clear',!clearAssistantDisplay('floating')&&aiWorkspace.archives.floating.length===1);window.removeEventListener(CLEAR_ASSISTANT_DISPLAY,busy)
 resetAIWorkspace(0);check('logout_clears_owned_view_preferences',!aiWorkspace.archives.floating.length&&aiWorkspace.dialogMode==='gemini')
 console.log(JSON.stringify({all_pass:true,checks,model_dispatches:0}))
}finally{if(originalStorage===undefined)delete globalThis.localStorage;else globalThis.localStorage=originalStorage;globalThis.fetch=originalFetch;if(originalWindow===undefined)delete globalThis.window;else globalThis.window=originalWindow;await server.close()}
