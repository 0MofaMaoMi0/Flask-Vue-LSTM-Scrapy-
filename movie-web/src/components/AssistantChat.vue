<script setup lang="ts">


import {ref,onMounted,onBeforeUnmount,onDeactivated,nextTick,computed,watch} from 'vue'
import {useAuthStore} from '../stores/auth'
import {useRoute} from 'vue-router'
import TMDBSettings from './TMDBSettings.vue'
import AccountClosure from './AccountClosure.vue'
import {aiWorkspace} from '../services/aiWorkspace'
import {movieArtwork} from '../services/movieArtwork'
const props=defineProps<{surface:'explore'|'floating'}>()
const settings=ref(false),thread=ref<HTMLElement>()
const message=ref(''),busy=ref(false),configBusy=ref(false),error=ref(''),notice=ref(''),result=ref<any>(null),conversation=ref<string|null>(null),status=ref<any>({configured:false,enabled:false}),draft=ref('')
const composerInput=ref<HTMLTextAreaElement>(),expanded=ref(false)
function resizeComposer(){const el=composerInput.value;if(!el)return;el.style.setProperty('height','auto','important');const natural=el.scrollHeight,cap=162;const h=expanded.value?Math.max(48,Math.min(natural,cap)):30;el.style.setProperty('height',h+'px','important');el.style.setProperty('overflow-y',expanded.value&&natural>cap?'auto':'hidden','important')}
function autoGrow(){const el=composerInput.value;if(!el)return;el.style.setProperty('height','auto','important');el.style.setProperty('min-height','0px','important');const natural=el.scrollHeight;el.style.removeProperty('min-height');const must=el.value.length>0&&(el.value.includes('\n')||natural>34);if(must!==expanded.value){expanded.value=must;nextTick(resizeComposer)}else resizeComposer()}
watch(message,()=>nextTick(autoGrow))
const apiKey=ref(''),model=ref('deepseek-flash'),output=ref(2048),consent=ref(false),paidConsent=ref(false),cacheEnabled=ref(false)
const history=ref<{question:string,answer:string,result?:any,feedback?:string|null}[]>([])
let controller:AbortController|null=null,pending:any=null
async function request(path:string,method='GET',body?:any,signal?:AbortSignal){
 const headers:Record<string,string>={}
 if(method!=='GET'){
  const r=await fetch('/api/v1/auth/csrf',{credentials:'same-origin'});if(!r.ok)throw new Error('登录状态已失效，请重新登录。')
  headers['X-CSRF-Token']=(await r.json()).data.csrf_token;headers['Content-Type']='application/json'
 }
 return fetch('/api/v1/assistant/'+path,{method,headers,credentials:'same-origin',body:body===undefined?undefined:JSON.stringify(body),signal})
}
async function jsonRequest(path:string,method='GET',body?:any){
 const r=await request(path,method,body);const b=await r.json();if(!r.ok||b.code!=='OK')throw new Error(r.status===401?'请重新登录。':b.code==='REQUEST_ID_CONFLICT'?'此请求编号已使用，未再次调用模型。':b.code==='KEY_CHECK_FAILED'?'密钥检查失败，请检查模型、Key或服务商网络。':b.code==='INVALID_INPUT'?'配置或输入格式无效，请检查后再试。':'操作未完成：'+b.code)
 return b.data
}
async function refreshStatus(){try{status.value=await jsonRequest('status');if(status.value.model){model.value=status.value.model;cacheEnabled.value=!!status.value.cache_enabled}}catch(e){error.value=(e as Error).message}}
async function saveConfig(){
 if(configBusy.value)return
 configBusy.value=true;error.value='';notice.value=''
 try{await jsonRequest('config','POST',{provider:'deepseek',model:model.value,api_key:apiKey.value,daily_calls:null,max_output_tokens:Number(output.value),enabled:true,consent:consent.value,cache_enabled:cacheEnabled.value});await refreshStatus();broadcast();notice.value='密钥已按账户加密保存到数据库。退出登录或服务重启后保留；保存没有调用模型。'}catch(e){error.value=(e as Error).message}finally{apiKey.value='';configBusy.value=false}
}
async function verifyKey(){configBusy.value=true;error.value='';try{await jsonRequest('verify','POST',{});await refreshStatus();broadcast();notice.value='已完成模型列表检查，没有发送生成请求。'}catch(e){error.value=(e as Error).message;await refreshStatus()}finally{configBusy.value=false}}
async function disconnect(){configBusy.value=true;try{await jsonRequest('disconnect','POST',{});apiKey.value='';await refreshStatus();broadcast();notice.value='连接已停用，数据库中的密钥保留；在途请求将尝试取消，已发送的调用仍可能计费。'}catch(e){error.value=(e as Error).message}finally{configBusy.value=false}}
async function enableSaved(){configBusy.value=true;error.value='';try{await jsonRequest('enable','POST',{});await refreshStatus();broadcast();notice.value='已启用数据库保存的密钥；本操作没有调用模型。'}catch(e){error.value=(e as Error).message}finally{configBusy.value=false}}
function consume(event:string,data:any){
 nextTick(()=>{if(thread.value)thread.value.scrollTop=thread.value.scrollHeight})
 if(event==='draft')draft.value=data.text||''
 if(event==='result'){
  if(data.state==='DONE'){result.value=data;conversation.value=data.conversation_id||conversation.value;history.value.push({question:pending?.message||'AI 深度解释',answer:data.answer,result:data});history.value=history.value.slice(-12);message.value='';pending=null}
  else{result.value={...data,source:'INTERRUPTED',answer:data.state==='CANCELLED'?'已取消显示。已发送的模型请求仍可能计费。':'请求未完成，未自动重试可能计费的调用。'};error.value=data.error_code||data.state}
  draft.value=''
 }
}
async function send(path:string,body:any){
 if(busy.value)return
 busy.value=true;error.value='';notice.value='';draft.value='';controller=new AbortController();pending={...body,path}
 try{
  const r=await request(path,'POST',{...body,stream:true},controller.signal)
  if(!r.ok){const b=await r.json();throw new Error(b.code==='CONVERSATION_UNAVAILABLE'?'对话已失效，请开始新对话。':b.code==='REQUEST_ID_CONFLICT'?'请求编号冲突，未重新计费。':'请求未完成：'+b.code)}
  if(!r.body)throw new Error('浏览器无法读取流式响应。')
  const reader=r.body.getReader(),decoder=new TextDecoder();let buffer=''
  while(true){const {value,done}=await reader.read();buffer+=decoder.decode(value||new Uint8Array(),{stream:!done});let boundary:number
   while((boundary=buffer.indexOf('\n\n'))>=0){const frame=buffer.slice(0,boundary);buffer=buffer.slice(boundary+2);const event=frame.match(/^event: (.+)$/m)?.[1],data=frame.match(/^data: (.+)$/m)?.[1];if(event&&data)consume(event,JSON.parse(data))}
   if(done)break
  }
 }catch(e){if((e as Error).name!=='AbortError')error.value='连接中断，未自动重发。可重试原请求以读取结果，不会静默再次调用。'}finally{controller=null;busy.value=false;await refreshStatus()}
}
async function submit(){
 if(!message.value.trim()||busy.value)return
 const text=message.value.trim();const same=pending?.path==='query'&&pending.message===text&&pending.conversation_id===conversation.value
 await send('query',{message:text,conversation_id:conversation.value,request_id:same?pending.request_id:crypto.randomUUID()})
}
async function explain(id:number){await send('explain',{movie_id:id,request_id:crypto.randomUUID()})}
async function testConnection(){if(!paidConsent.value||busy.value)return;await send('test',{confirm_paid:true,request_id:crypto.randomUUID()})}
async function cancel(){
 if(!pending)return
 try{const id=pending.request_id;const data=await jsonRequest('cancel','POST',{request_id:id});if(!data.cancel_requested&&data.state==='DONE'){const completed=await jsonRequest('requests/'+id);consume('result',{...completed,state:'DONE'});notice.value='请求已经完成，未执行取消。'}else{result.value={source:'INTERRUPTED',answer:'已请求取消。已发送的调用可能计费，最终用量可能暂时未知。',cost:data.cost};notice.value='不会自动重发此请求。'}}catch(e){error.value=(e as Error).message}finally{controller?.abort();draft.value=''}
}
function reset(){newSession()}
function clearSensitive(){apiKey.value=''}
defineExpose({clearSensitive})
onDeactivated(clearSensitive)
function sharedStatus(){refreshStatus()}
function broadcast(){window.dispatchEvent(new Event('mi2-model-status'))}
function closeTransient(e:Event){const el=e.target as HTMLElement;if(!el?.closest?.('.side-list'))sessionMenu.value='';if((e as KeyboardEvent).key==='Escape'){sessionMenu.value='';editingId.value=''}}
onMounted(()=>{refreshStatus();window.addEventListener('mi2-model-status',sharedStatus);window.addEventListener('focus',sharedStatus);document.addEventListener('pointerdown',closeTransient);document.addEventListener('keydown',closeTransient)})
onBeforeUnmount(()=>{apiKey.value='';controller?.abort();window.removeEventListener('mi2-model-status',sharedStatus);window.removeEventListener('focus',sharedStatus);document.removeEventListener('pointerdown',closeTransient);document.removeEventListener('keydown',closeTransient)})

function movieUrl(id:number){return '/movies?from=library#movie/'+id}
async function copyAnswer(text:string){try{await navigator.clipboard.writeText(text);notice.value='已复制回答'}catch{error.value='无法访问剪贴板，请手动复制。'}}


const emit=defineEmits<{(e:'close'):void,(e:'toggle-full'):void}>()
const auth=useAuthStore(),rootElement=ref<HTMLElement>(),sidebarOpen=ref(true),dialogMode=ref('gemini'),settingsTab=ref('ai'),profileOpen=ref(false),shortcutsOpen=ref(false),sessionSearch=ref(''),sessionMenu=ref(''),sharedOpen=ref(false),editingId=ref(''),editTitle=ref('')
const uid=(id:string)=>props.surface+'-'+id
type SavedSession={id:string,title:string,history:any[],conversation:string|null,message:string,result:any,pending:any}
const sessions=ref<SavedSession[]>([]),activeSession=ref('')
const filteredSessions=computed(()=>sessions.value.filter(s=>s.title.toLowerCase().includes(sessionSearch.value.toLowerCase())))
const otherSessions=computed(()=>aiWorkspace.archives[props.surface==='explore'?'floating':'explore'].filter(s=>s.history.length))
const clone=<T,>(value:T):T=>JSON.parse(JSON.stringify(value))
function saveSession(){const s=sessions.value.find(x=>x.id===activeSession.value);if(!s)return;s.history=clone(history.value);s.conversation=conversation.value;s.message=message.value;s.result=clone(result.value);s.pending=pending?clone(pending):null;if(s.history.length&&s.title==='新对话')s.title=s.history[0].question.slice(0,24);aiWorkspace.archives[props.surface]=clone(sessions.value)}
function newSession(){if(busy.value)return;saveSession();const s:SavedSession={id:crypto.randomUUID(),title:'新对话',history:[],conversation:null,message:'',result:null,pending:null};sessions.value.unshift(s);activeSession.value=s.id;history.value=[];conversation.value=null;message.value='';result.value=null;pending=null;error.value='';notice.value='';sessionMenu.value='';saveSession()}
function chooseSession(id:string){if(busy.value)return;saveSession();const s=sessions.value.find(x=>x.id===id);if(!s)return;activeSession.value=id;history.value=clone(s.history);conversation.value=s.conversation;message.value=s.message;result.value=clone(s.result);pending=s.pending?clone(s.pending):null;error.value='';notice.value='';sessionMenu.value=''}
const menuPos=ref({top:0,left:0})
function toggleMenu(id:string,e:MouseEvent){if(sessionMenu.value===id){sessionMenu.value='';return}const r=(e.currentTarget as HTMLElement).getBoundingClientRect(),w=132,h=76,gap=4;let top=r.bottom+gap;if(top+h>window.innerHeight-8)top=Math.max(8,r.top-h-gap);let left=Math.min(r.right-w,window.innerWidth-w-8);menuPos.value={top,left};sessionMenu.value=id}
async function startRename(s:SavedSession){sessionMenu.value='';editingId.value=s.id;editTitle.value=s.title;await nextTick();const el=rootElement.value?.querySelector<HTMLInputElement>('[data-edit="'+s.id+'"]');el?.focus();el?.select()}
function commitRename(s:SavedSession){const title=editTitle.value.trim();if(title&&title!==s.title){s.title=title.slice(0,80);saveSession()}editingId.value=''}
function cancelRename(){editingId.value=''}
function deleteSession(id:string){if(busy.value)return;sessions.value=sessions.value.filter(s=>s.id!==id);if(activeSession.value===id){activeSession.value='';newSession()}saveSession();notice.value='已移除此窗口的显示记录，未删除服务端数据。'}
function clearSessions(){if(busy.value)return;sessions.value=[];activeSession.value='';newSession();notice.value='已清空此窗口显示记录，未删除服务端数据。'}
function importSession(other:SavedSession){if(busy.value)return;newSession();const s=sessions.value[0]!;s.title='导入 · '+other.title;s.history=clone(other.history);history.value=clone(s.history);conversation.value=null;pending=null;saveSession();sharedOpen.value=false;notice.value='已复制记录；后续提问使用独立的新模型上下文，不修改另一窗口。'}
watch([history,conversation,message,result],saveSession,{deep:true})
const settingsRoute=useRoute()
watch(()=>[settingsRoute.path,settingsRoute.query.settings],()=>{if(props.surface==='explore'&&settingsRoute.path==='/ai'&&settingsRoute.query.settings==='tmdb')openSettings('tmdb')},{immediate:true})
function openSettings(tab='ai'){settingsTab.value=tab;settings.value=true;apiKey.value='';profileOpen.value=false}
function feedback(turn:any,value:string){turn.feedback=turn.feedback===value?null:value;notice.value='反馈仅在此页记录，未写入评价或推荐数据。'}
function followup(title:string){message.value='我想进一步了解《'+title+'》，适合什么心情观看？'}
async function movieWant(mid:number){if(busy.value)return;try{const r=await fetch('/api/v1/auth/csrf',{credentials:'same-origin'});if(!r.ok)throw new Error('登录已失效');const token=(await r.json()).data.csrf_token;const b=await fetch('/api/catalog/behavior',{credentials:'same-origin'});if(!b.ok)throw new Error('行为状态不可用');const state=(await b.json()).data.state;const undo=!!state[String(mid)]?.want;const saved=await fetch('/api/catalog/behavior',{method:'POST',credentials:'same-origin',headers:{'Content-Type':'application/json','X-CSRF-Token':token},body:JSON.stringify({event_id:crypto.randomUUID(),movie_id:mid,kind:'want',action:undo?'undo':'set',value:null,time:new Date().toISOString(),source:'USER_ACTION'})});if(!saved.ok)throw new Error(saved.status===409?'与已看或不感兴趣状态冲突，请在详情页调整。':'保存未确认，请到详情页核对；不会自动重试。');notice.value=undo?'已撤销想看':'已保存想看';window.dispatchEvent(new Event('mi2-behavior-changed'))}catch(e){error.value=(e as Error).message}}
function sourceLabel(r:any){return r?.source==='LLM'?'AI 生成回答':r?.source==='CACHE'?'AI 缓存回答':r?.source==='MOCK'?'模拟测试':'本地检索 / 降级'}
function modelLink(url:string){return /^https:\/\/www\.themoviedb\.org\/movie\/\d+(?:[/?#-]|$)/.test(url)}
const cropOpen=ref(false),cropZoom=ref(3),cropPreview=ref('')
let cropImage:HTMLImageElement|null=null,cropX=0,cropY=0,cropDrag:{x:number,y:number}|null=null
function sourceNode<T extends HTMLElement>(id:string){return rootElement.value?.querySelector<T>('[data-source-id="'+id+'"]')}
async function openCrop(){cropOpen.value=true;cropImage=null;cropZoom.value=3;cropX=0;cropY=0;cropPreview.value='';await nextTick();drawCrop()}
function chooseAvatarFile(){sourceNode<HTMLInputElement>('cropFileInput')?.click()}
function resetCrop(){cropZoom.value=3;cropX=0;cropY=0;drawCrop()}
function cropParams(){if(!cropImage)return null;const scale=Math.max(40/cropImage.naturalWidth,40/cropImage.naturalHeight)*cropZoom.value;const w=cropImage.naturalWidth*scale,h=cropImage.naturalHeight*scale;cropX=Math.max(-(w-40)/2,Math.min((w-40)/2,cropX));cropY=Math.max(-(h-40)/2,Math.min((h-40)/2,cropY));return {w,h,x:(240-w)/2+cropX,y:(240-h)/2+cropY}}
function drawCrop(){const canvas=sourceNode<HTMLCanvasElement>('cropCanvas'),ctx=canvas?.getContext('2d');if(!ctx)return;ctx.clearRect(0,0,480,480);ctx.save();ctx.scale(2,2);ctx.fillStyle='#191923';ctx.fillRect(0,0,240,240);const p=cropParams();if(cropImage&&p)ctx.drawImage(cropImage,p.x,p.y,p.w,p.h);ctx.fillStyle='#05050ac4';ctx.fillRect(0,0,240,100);ctx.fillRect(0,100,100,40);ctx.fillRect(140,100,100,40);ctx.fillRect(0,140,240,100);ctx.strokeStyle='#c5b5e8';ctx.lineWidth=1.4;ctx.strokeRect(100.7,100.7,38.6,38.6);ctx.restore();if(cropImage&&p){const out=document.createElement('canvas');out.width=128;out.height=128;out.getContext('2d')?.drawImage(cropImage,(p.x-100)*3.2,(p.y-100)*3.2,p.w*3.2,p.h*3.2);cropPreview.value=out.toDataURL('image/png')}}
function loadAvatar(e:Event){const file=(e.target as HTMLInputElement).files?.[0];if(!file)return;if(!['image/png','image/jpeg','image/webp'].includes(file.type)||file.size>8*1024*1024){error.value='请选择 8MB 以内的 PNG、JPEG 或 WebP';return}const url=URL.createObjectURL(file),img=new Image();img.onload=()=>{URL.revokeObjectURL(url);if(img.naturalWidth*img.naturalHeight>40000000){error.value='图片尺寸过大';return}cropImage=img;resetCrop()};img.onerror=()=>{URL.revokeObjectURL(url);error.value='图片无法读取'};img.src=url}
function startCrop(e:PointerEvent){cropDrag={x:e.clientX,y:e.clientY};(e.currentTarget as HTMLElement).setPointerCapture(e.pointerId)}
function moveCrop(e:PointerEvent){if(!cropDrag)return;cropX+=e.clientX-cropDrag.x;cropY+=e.clientY-cropDrag.y;cropDrag={x:e.clientX,y:e.clientY};drawCrop()}
function stopCrop(){cropDrag=null}
function applyAvatar(){if(cropPreview.value){aiWorkspace.avatar=cropPreview.value;cropOpen.value=false}}
onMounted(()=>newSession())

</script>
<template><div ref="rootElement" class="prototype-ai mi2-docked-ai" :class="['surface-'+surface,{'pref-large-text':aiWorkspace.large,'pref-reduced-motion':aiWorkspace.reduced}]"><svg style="display:none" xmlns="http://www.w3.org/2000/svg"><symbol :id="uid('i-menu')" data-source-id="i-menu" viewBox="0 0 24 24"><path d="M4 7h16M4 12h16M4 17h16"></path></symbol><symbol :id="uid('i-plus')" data-source-id="i-plus" viewBox="0 0 24 24"><path d="M12 5v14M5 12h14"></path></symbol><symbol :id="uid('i-max')" data-source-id="i-max" viewBox="0 0 24 24"><path d="M8 3H4a1 1 0 0 0-1 1v4m13-5h4a1 1 0 0 1 1 1v4M3 16v4a1 1 0 0 0 1 1h4m13-5v4a1 1 0 0 1-1 1h-4"></path></symbol><symbol :id="uid('i-chevron-down')" data-source-id="i-chevron-down" viewBox="0 0 24 24"><path d="m6 9 6 6 6-6"></path></symbol><symbol :id="uid('i-min')" data-source-id="i-min" viewBox="0 0 24 24"><path d="M4 9h5V4m11 11h-5v5M9 9 4 4m11 11 5 5"></path></symbol><symbol :id="uid('i-close')" data-source-id="i-close" viewBox="0 0 24 24"><path d="M5 5l14 14M19 5 5 19"></path></symbol><symbol :id="uid('i-send')" data-source-id="i-send" viewBox="0 0 24 24"><path d="m5 12 14-7-5 14-2-7-7 0z"></path></symbol><symbol :id="uid('i-settings')" data-source-id="i-settings" viewBox="0 0 24 24"><circle cx="12" cy="12" r="3"></circle><path d="M12 2v3m0 14v3M2 12h3m14 0h3M5 5l2 2m10 10 2 2M19 5l-2 2M7 17l-2 2"></path></symbol><symbol :id="uid('i-copy')" data-source-id="i-copy" viewBox="0 0 24 24"><rect height="12" rx="2" width="12" x="8" y="8"></rect><path d="M16 8V6a2 2 0 0 0-2-2H6a2 2 0 0 0-2 2v8a2 2 0 0 0 2 2h2"></path></symbol><symbol :id="uid('i-refresh')" data-source-id="i-refresh" viewBox="0 0 24 24"><path d="M20 7v5h-5M4 17v-5h5"></path><path d="M5.9 9a7 7 0 0 1 11.9-2l2.2 2M4 15l2.2 2a7 7 0 0 0 11.9-2"></path></symbol><symbol :id="uid('i-thumb-up')" data-source-id="i-thumb-up" viewBox="0 0 24 24"><path d="M7 10v11H4a2 2 0 0 1-2-2v-7a2 2 0 0 1 2-2h3Zm0 0 4.5-8a3 3 0 0 1 3 3v4h5a2.5 2.5 0 0 1 2.4 3.2l-2 7a2.5 2.5 0 0 1-2.4 1.8H7"></path></symbol><symbol :id="uid('i-thumb-down')" data-source-id="i-thumb-down" viewBox="0 0 24 24"><path d="M7 14V3H4a2 2 0 0 0-2 2v7a2 2 0 0 0 2 2h3Zm0 0 4.5 8a3 3 0 0 0 3-3v-4h5a2.5 2.5 0 0 0 2.4-3.2l-2-7A2.5 2.5 0 0 0 17.5 3H7"></path></symbol><symbol :id="uid('i-arrow-up')" data-source-id="i-arrow-up" viewBox="0 0 24 24"><path d="M12 19V5"></path><path d="m6 11 6-6 6 6"></path></symbol><symbol :id="uid('i-moon')" data-source-id="i-moon" viewBox="0 0 24 24"><path d="M21 13A9 9 0 0 1 11 3a9 9 0 1 0 10 10"></path></symbol><symbol :id="uid('i-search')" data-source-id="i-search" viewBox="0 0 24 24"><circle cx="11" cy="11" r="6"></circle><path d="m16 16 4 4"></path></symbol><symbol :id="uid('i-side')" data-source-id="i-side" viewBox="0 0 24 24"><path d="m14 6-6 6 6 6"></path></symbol><symbol :id="uid('i-import')" data-source-id="i-import" viewBox="0 0 24 24"><path d="M12 4v10m0 0 4-4m-4 4-4-4"></path><path d="M5 19h14"></path></symbol></svg>
<section :data-dialog-mode="dialogMode" aria-label="MovieInsight AI 电影探索" class="window show full" :class="{'sidebar-open':sidebarOpen}" :id="uid('chatWindow')" data-source-id="chatWindow" role="dialog" v-show="!settings">
<header class="window-head"><button v-if="surface==='floating'||!sidebarOpen" @click="sidebarOpen=!sidebarOpen" aria-label="切换会话侧栏" class="icon-btn" :id="uid('sidebarToggle')" data-source-id="sidebarToggle" title="展开会话侧栏"><svg class="ico"><use :href="'#'+uid('i-menu')"></use></svg></button><div class="head-id"><span class="orb-skin mini mark" data-orb=""><img alt="" draggable="false" src="/m9/assistant-orb.png"/><i class="eye left"></i><i class="eye right"></i></span><div><div class="head-title">MovieInsight AI</div></div></div><div class="head-fill"></div><button @click="openSettings('ai')" aria-label="查看 BYOK 连接状态" class="head-status" :id="uid('connectionStatus')" data-source-id="connectionStatus" title="个人模型连接状态"><span class="dot"></span>{{status.cloud_available?status.model+" 已连接":"BYOK 未连接 · 本地兜底"}}</button><div class="head-ops"><button v-if="!sidebarOpen" @click="reset" aria-label="新建对话" class="icon-btn" :id="uid('newTop')" data-source-id="newTop" title="新建对话"><svg class="ico"><use :href="'#'+uid('i-plus')"></use></svg></button><span class="divider-v"></span><button @click="emit('toggle-full')" aria-label="切换全屏" class="icon-btn" :id="uid('fullToggle')" data-source-id="fullToggle" title="切换全屏" v-if="surface==='floating'"><svg class="ico" :id="uid('fullSvg')" data-source-id="fullSvg"><use :href="'#'+uid('i-max')"></use></svg></button><button @click="emit('close')" aria-label="收起窗口" class="icon-btn" :id="uid('closeChat')" data-source-id="closeChat" title="收起窗口" v-if="surface==='floating'"><svg class="ico"><use :href="'#'+uid('i-close')"></use></svg></button></div></header>
<div class="window-body"><aside :class="{'closed':!sidebarOpen}" aria-label="会话历史" class="sidebar" :id="uid('sidebar')" data-source-id="sidebar"><div class="side-panel"><div class="side-top"><span class="side-label">对话</span><button @click="sidebarOpen=false" aria-label="收起会话侧栏" class="side-collapse" type="button"><svg class="ico"><use :href="'#'+uid('i-side')"></use></svg></button></div><button @click="reset" class="side-action" :id="uid('newSide')" data-source-id="newSide" type="button"><svg class="ico"><use :href="'#'+uid('i-plus')"></use></svg><span>新对话</span></button><label class="side-search"><svg class="ico"><use :href="'#'+uid('i-search')"></use></svg><input v-model="sessionSearch" aria-label="搜索对话" placeholder="搜索对话" type="search"/></label><div class="side-list" :id="uid('sessionList')" data-source-id="sessionList" @scroll="sessionMenu=''"><div :class="{active:s.id===activeSession}" :key="s.id" class="side-row" v-for="(s,index) in filteredSessions"><input v-if="editingId===s.id" :data-edit="s.id" v-model="editTitle" aria-label="重命名对话" class="side-rename" @blur="commitRename(s)" @keydown.enter.prevent="commitRename(s)" @keydown.esc.prevent="cancelRename"/><button v-else :disabled="busy" :title="s.title" @click="chooseSession(s.id)" class="side-title" type="button">{{s.title}}</button><button :aria-expanded="sessionMenu===s.id" :aria-label="'管理对话：'+s.title" @click="toggleMenu(s.id,$event)" class="side-more" type="button">⋯</button><div class="side-pop" v-if="sessionMenu===s.id" :style="{top:menuPos.top+'px',left:menuPos.left+'px'}"><button @click="startRename(s)" type="button">重命名</button><button class="danger" @click="deleteSession(s.id)" type="button">删除对话</button></div></div><p v-if="!filteredSessions.length" class="side-empty">没有匹配的对话</p></div><div class="side-import" v-if="otherSessions.length"><button @click="sharedOpen=!sharedOpen" class="side-import-btn" type="button"><svg class="ico"><use :href="'#'+uid('i-import')"></use></svg><span>导入另一窗口的对话</span></button><div class="side-import-tray" v-if="sharedOpen"><p class="side-import-hint">导入为独立记录，不改动原窗口</p><button :disabled="busy" :key="s.id" @click="importSession(s)" class="side-import-row" v-for="s in otherSessions" type="button"><strong>{{s.title}}</strong><span>{{s.history.length}} 条回答</span></button></div></div><section :hidden="!profileOpen" aria-label="个人信息预览" class="profile-panel" :id="uid('profilePanel')" data-source-id="profilePanel">
<div class="profile-top">
<span aria-hidden="true" class="profile-avatar-lg avatar-slot is-empty"><svg aria-hidden="true" class="avatar-placeholder" fill="none" stroke="currentColor" stroke-linecap="round" stroke-width="1.55" v-if="!aiWorkspace.avatar" viewBox="0 0 24 24"><circle cx="12" cy="8.2" r="3.45"></circle><path d="M5.5 19.1c.6-3.45 2.88-5.1 6.5-5.1s5.9 1.65 6.5 5.1"></path></svg><img :src="aiWorkspace.avatar" alt="" class="user-avatar-img" v-if="aiWorkspace.avatar"/></span>
<div class="profile-meta"><strong>{{auth.user?.username}}</strong><small>个人信息 · 可在全局设置中修改</small></div>
<button @click="profileOpen=false" aria-label="关闭个人信息" class="profile-close" :id="uid('profileClose')" data-source-id="profileClose" type="button">×</button>
</div>
<div class="profile-detail">评分、收藏与想看由当前登录账户独立保存。</div>
<button @click="openSettings('account')" class="profile-edit-link" :id="uid('openAccountSettings')" data-source-id="openAccountSettings" type="button">编辑头像与个人资料</button><div class="profile-note">头像仅在本次页面会话预览；账户名称由身份系统管理。</div>
</section><div class="side-account"><button @click="profileOpen=!profileOpen" aria-label="打开个人信息" class="account-identity" :id="uid('profileEntry')" data-source-id="profileEntry" title="个人信息" type="button"><span aria-hidden="true" class="account-avatar avatar-slot is-empty"><svg aria-hidden="true" class="avatar-placeholder" fill="none" stroke="currentColor" stroke-linecap="round" stroke-width="1.55" v-if="!aiWorkspace.avatar" viewBox="0 0 24 24"><circle cx="12" cy="8.2" r="3.45"></circle><path d="M5.5 19.1c.6-3.45 2.88-5.1 6.5-5.1s5.9 1.65 6.5 5.1"></path></svg><img :src="aiWorkspace.avatar" alt="" class="user-avatar-img" v-if="aiWorkspace.avatar"/></span><span class="account-name">{{auth.user?.username}}</span></button><button @click="openSettings('account')" aria-label="打开全局设置" class="account-settings" :id="uid('accountSettingsEntry')" data-source-id="accountSettingsEntry" title="全局设置" type="button"><svg aria-hidden="true" fill="none" height="20" stroke="currentColor" stroke-linecap="round" stroke-linejoin="round" stroke-width="1.7" viewBox="0 0 24 24" width="20"><circle cx="12" cy="12" r="3.2"></circle><path d="M19.4 13.3a7.5 7.5 0 0 0 0-2.6l2-1.5-2-3.4-2.4.9a7.6 7.6 0 0 0-2.2-1.3L14.4 3H9.6l-.4 2.4A7.6 7.6 0 0 0 7 6.7l-2.4-.9-2 3.4 2 1.5a7.5 7.5 0 0 0 0 2.6l-2 1.5 2 3.4 2.4-.9a7.6 7.6 0 0 0 2.2 1.3l.4 2.4h4.8l.4-2.4a7.6 7.6 0 0 0 2.2-1.3l2.4.9 2-3.4-2-1.5Z"></path></svg></button></div></div></aside><main class="chat-main"><div aria-live="polite" aria-relevant="additions" class="thread" :id="uid('thread')" data-source-id="thread" ref="thread"><div class="thread-inner" :id="uid('threadInner')" data-source-id="threadInner"><div v-if="!history.length&&!busy" class="empty-state"><div class="empty-orb"><span class="orb-skin"><img src="/m9/assistant-orb.png" alt="" draggable="false"/><i class="eye left"></i><i class="eye right"></i></span></div><h2>今晚，我们聊什么电影？</h2><p>说出你的心情、偏好或最近喜欢的电影。也可以直接向我提问。</p><div class="empty-suggestions"><button class="followup" @click="message='今晚看什么电影？'">帮我选今晚的电影</button><button class="followup" @click="message='想看烧脑一点的科幻电影'">想看烧脑科幻</button><button class="followup" @click="message='推荐一部温暖、有希望的电影'">想看温暖的故事</button></div></div>
<div v-if="history.length" class="date-line">当前会话</div>
<template v-for="(turn,i) in history" :key="i"><div class="msg user"><div class="bubble">{{turn.question}}</div><div class="mi17-user-actions"><button class="response-action" @click="copyAnswer(turn.question)">复制</button><button class="response-action" :disabled="busy" @click="message=turn.question">编辑</button></div></div><div class="msg assistant"><div class="avatar"><span class="orb-skin mini"><img src="/m9/assistant-orb.png" alt=""/><i class="eye left"></i><i class="eye right"></i></span></div><div class="assistant-body"><div class="assistant-name"><span>MovieInsight AI</span><small>{{sourceLabel(turn.result)}}</small></div><div class="reply"><p class="reply-body">{{turn.answer}}</p></div>
<template v-if="turn.result?.items?.length"><div class="trust-note">库内电影 · 真实目录 ID</div><div class="cards-head"><strong>电影选择 · {{turn.result.items.length}} 部</strong><span>横向浏览 · 可追问</span></div><div class="cards-rail"><article v-for="m in turn.result.items" :key="m.movie_id" class="movie-card"><router-link :to="movieUrl(m.movie_id)" class="poster" :aria-label="'查看'+m.title+'详情'"><img v-if="movieArtwork[String(m.movie_id)]" class="poster-uploaded" :src="movieArtwork[String(m.movie_id)]!.poster" :alt="m.title+'海报'" loading="lazy"/><div v-else class="poster-title">{{m.title}}</div></router-link><div class="movie-info"><div class="movie-title">{{m.title}}</div><div class="movie-meta">{{m.release_year||'年份未知'}} · {{m.genres?.join(' / ')}}</div><span class="verified">库内影片</span><p class="why">{{m.reasons?.join('；')}}</p><div class="movie-actions"><router-link class="mini-btn" :to="movieUrl(m.movie_id)">详情</router-link><button class="mini-btn" :disabled="busy" @click="movieWant(m.movie_id)">想看</button><button class="mini-btn" @click="followup(m.title)">追问</button></div></div></article></div></template>
<div v-if="turn.result?.discoveries?.length" class="no-result"><strong>库外电影发现 · 尚未入库</strong><article v-for="m in turn.result.discoveries" :key="m.title"><p>{{m.title}} · {{m.year||'年份未确认'}}</p><p>{{m.reason}}</p><small>{{m.verification==='TMDB_TITLE_YEAR_MATCH'?'TMDB 标题与年份匹配，仍未入库':m.verification==='TMDB_OFFICIAL_LIST_SNAPSHOT'?'TMDB 官方榜单快照核验，仍未入库':'未经 TMDB 核实的模型建议'}}</small><a v-if="m.tmdb_url&&modelLink(m.tmdb_url)" :href="m.tmdb_url" target="_blank" rel="noopener noreferrer">查看 TMDB</a></article></div>
<div class="followups"><button class="followup" @click="message='那更偏烧脑一点呢？'">更偏烧脑一点</button><button class="followup" @click="message='有没有更温暖、更有希望的？'">想要温暖一点</button><button class="followup" @click="message='当前库里还有哪些电影？'">库里还有什么</button></div>
<div class="response-actions"><div class="response-actions-buttons"><button class="response-action" aria-label="复制这条回复" @click="copyAnswer(turn.answer)"><svg class="ico"><use :href="'#'+uid('i-copy')"/></svg><span>复制</span></button><button class="response-action" :disabled="busy" @click="message=turn.question;notice='已填入原问题；确认发送可能产生新的模型费用。'"><svg class="ico"><use :href="'#'+uid('i-refresh')"/></svg><span>重新提问</span></button><button class="response-action" :aria-pressed="turn.feedback==='up'" @click="feedback(turn,'up')"><svg class="ico"><use :href="'#'+uid('i-thumb-up')"/></svg><span>有帮助</span></button><button class="response-action" :aria-pressed="turn.feedback==='down'" @click="feedback(turn,'down')"><svg class="ico"><use :href="'#'+uid('i-thumb-down')"/></svg><span>需改进</span></button></div></div>
<p v-if="turn.result?.cost" class="reply-status">模型调用 {{turn.result.cost.provider_calls??'未知'}} 次 · 输入 {{turn.result.cost.input_tokens??'未知'}} / 输出 {{turn.result.cost.output_tokens??'未知'}} Token · 金额 {{turn.result.cost.amount===0?'0':'未知'}}</p><p v-for="limit in turn.result?.limitations" :key="limit" class="reply-status">{{limit}}</p></div></div></template>
<div v-if="busy" class="msg assistant"><div class="avatar"><span class="orb-skin mini"><img src="/m9/assistant-orb.png" alt=""/><i class="eye left"></i><i class="eye right"></i></span></div><div class="assistant-body"><div class="assistant-name">MovieInsight AI <small>正在理解与查询</small></div><p class="reply-body">{{draft}}</p><div class="typing-dots"><i></i><i></i><i></i></div><small class="reply-status">生成草稿尚未完成事实核验。</small></div></div>
<p v-if="result?.source==='INTERRUPTED'" class="reply-status" role="status">{{result.answer}}</p><p v-if="error" class="reply-status" role="alert">{{error}}</p><p v-if="notice" class="reply-status" role="status">{{notice}}</p>
</div></div><div class="composer-zone"><div class="composer-wrap"><div :hidden="!shortcutsOpen" class="composer-shortcuts" :id="uid('composerShortcuts')" data-source-id="composerShortcuts"><div class="shortcut-label">快捷提问</div><button @click="message='今晚看什么电影？'" data-shortcut="今晚看什么电影？" type="button">今晚看什么</button><button @click="message='想看烧脑一点的科幻电影'" data-shortcut="想看烧脑一点的科幻电影" type="button">烧脑科幻</button><button @click="message='推荐一部温暖、有希望的电影'" data-shortcut="推荐一部温暖、有希望的电影" type="button">温暖剧情</button></div><form @submit.prevent="submit" :class="{'mi14-expanded':expanded}" class="compose compose-r5 mi14-compose" :id="uid('composerForm')" data-source-id="composerForm">
<label class="sr-only" :for="uid('messageInput')">输入关于电影的问题</label>
<textarea :disabled="busy" @keydown.enter.exact.prevent="submit" @input="autoGrow" ref="composerInput" :id="uid('messageInput')" data-source-id="messageInput" maxlength="2000" placeholder="问问 MovieInsight…" rows="1" spellcheck="false" v-model="message"></textarea>
<div class="mi14-controls">
<button @click="shortcutsOpen=!shortcutsOpen" aria-label="快捷提问" class="compose-icon lead" :id="uid('attachBtn')" data-source-id="attachBtn" title="快捷提问" type="button"><svg class="ico"><use :href="'#'+uid('i-plus')"></use></svg></button>
<div class="compose-actions"><button @click="openSettings('ai')" aria-label="查看全局 AI 模型设置" class="mode-chip" :id="uid('modeChip')" data-source-id="modeChip" title="查看全局 AI 模型设置" type="button"><span class="mode-name">{{status.cloud_available?status.model:"MovieInsight 自动"}}</span><span class="mode-name-mobile">自动模式</span><svg class="ico tiny"><use :href="'#'+uid('i-chevron-down')"></use></svg></button><button :aria-label="busy?'取消生成':'发送消息'" :disabled="!busy&&!message.trim()" :type="busy?'button':'submit'" @click="busy&&cancel()" class="send send-r5" :id="uid('sendBtn')" data-source-id="sendBtn" title="发送消息" ><svg class="ico"><use :href="'#'+uid(busy?'i-close':'i-arrow-up')"></use></svg></button></div>
</div></form><div class="compose-footer"><span :id="uid('composerStatus')" data-source-id="composerStatus">{{busy?"正在处理…":status.cloud_available?"使用你的服务商账户 · 可能计费":"本地检索 · 不调用模型"}}</span><span class="key">Enter 发送 · Shift + Enter 换行</span></div></div></div>



</main></div></section>
<section aria-label="MovieInsight 全局设置页面" class="global-settings-page" :id="uid('globalSettingsPage')" data-source-id="globalSettingsPage" v-show="settings">
<header class="gs-top">
<button @click="settings=false;apiKey=''" class="gs-return" :id="uid('globalSettingsBack')" data-source-id="globalSettingsBack" type="button">← 返回电影探索</button>
<div class="gs-brand">MovieInsight <small>偏好与账户 · 全局设置</small></div>
<span class="gs-tag">个人偏好与模型连接</span>
</header>
<div class="gs-shell">
<nav aria-label="设置分类" class="gs-nav">
<button :class="{active:settingsTab==='account'}" @click="settingsTab='account'" class="gs-tab" data-gs-tab="account">◉　个人资料</button>
<button :class="{active:settingsTab==='display'}" @click="settingsTab='display'" class="gs-tab" data-gs-tab="display">◈　外观与布局</button>
<button :class="{active:settingsTab==='ai'}" @click="settingsTab='ai'" class="gs-tab" data-gs-tab="ai">✧　AI 与 BYOK</button>
<button :class="{active:settingsTab==='tmdb'}" @click="settingsTab='tmdb'" class="gs-tab" data-gs-tab="tmdb"><svg aria-hidden="true" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><ellipse cx="12" cy="5" rx="8" ry="3"/><path d="M4 5v14c0 4 16 4 16 0V5M4 12c0 4 16 4 16 0"/></svg>　TMDB 数据连接</button>
<button :class="{active:settingsTab==='privacy'}" @click="settingsTab='privacy'" class="gs-tab" data-gs-tab="privacy">◇　隐私与数据</button>
<button :class="{active:settingsTab==='about'}" @click="settingsTab='about'" class="gs-tab" data-gs-tab="about">ⓘ　关于 MovieInsight</button>
</nav>
<div class="gs-main">
<div class="gs-intro"><span class="gs-eyebrow">MOVIEINSIGHT</span><h1>全局设置</h1><p>这里集中管理整个 MovieInsight 的个人资料、外观、AI 服务及数据偏好。与聊天窗口设置分开，避免修改入口散落各处。</p></div>
<section aria-label="个人资料设置" class="gs-panel" :id="uid('gs-account')" data-source-id="gs-account" v-show="settingsTab==='account'">
<div class="gs-card"><h2>头像与公开资料</h2><p>默认显示通用占位符，不预设头像。可选本地 PNG、WebP 或 JPG；选区限定在 40×40 的头像框，透明背景原样保留。</p>
<div class="gs-avatar-row"><span aria-label="未设置头像" class="gs-avatar-swatch avatar-slot is-empty"><svg aria-hidden="true" class="avatar-placeholder" fill="none" stroke="currentColor" stroke-linecap="round" stroke-width="1.55" v-if="!aiWorkspace.avatar" viewBox="0 0 24 24"><circle cx="12" cy="8.2" r="3.45"></circle><path d="M5.5 19.1c.6-3.45 2.88-5.1 6.5-5.1s5.9 1.65 6.5 5.1"></path></svg><img :src="aiWorkspace.avatar" alt="" class="user-avatar-img" :id="uid('gsAvatarLarge')" data-source-id="gsAvatarLarge" v-if="aiWorkspace.avatar"/></span><div class="gs-avatar-caption"><strong>当前头像</strong><small>头像尺寸：40×40 px · 透明背景可保留</small><button @click="openCrop" class="gs-cta" :id="uid('editAvatarBtn')" data-source-id="editAvatarBtn" type="button">选择与裁剪头像</button><button @click="aiWorkspace.avatar=''" class="gs-cta gs-cta-muted" :id="uid('resetAvatarBtn')" data-source-id="resetAvatarBtn" type="button">恢复占位符</button></div></div>
<div class="gs-formline"><label :for="uid('gsDisplayName')">显示名称</label><input :value="auth.user?.username" autocomplete="off" class="gs-input" :disabled="true" :id="uid('gsDisplayName')" data-source-id="gsDisplayName" maxlength="24" /></div>
<div class="gs-actions"><button class="gs-cta" :disabled="true" :id="uid('saveDisplayName')" data-source-id="saveDisplayName" type="button">账户名称由身份系统管理</button></div><div aria-live="polite" class="gs-save-status" :id="uid('accountStatus')" data-source-id="accountStatus">头像只在当前登录页面预览，不上传或改写账户资料。</div>
</div>
<div class="gs-card"><h2>个人资料入口</h2><p>当前账户来自服务端登录身份；本页不修改认证机制。</p></div>
</section>
<section aria-label="全站外观与布局" class="gs-panel" :id="uid('gs-display')" data-source-id="gs-display" v-show="settingsTab==='display'">
<div class="gs-card"><h2>外观与布局</h2><p>沿用已确认的深色紫黑视觉，阅读与动效偏好同步到两个 AI 窗口。</p>
<div class="gs-setting-row"><div><h3>站点色彩</h3><p>V12-A / V13 深色紫黑品牌基线。</p></div><span class="gs-pill">保持原配色</span></div>
<div class="gs-setting-row"><div><h3>阅读字体</h3><p>影响 AI 消息文字与用户消息的阅读大小。</p></div><select :value="aiWorkspace.large?'large':'normal'" @change="aiWorkspace.large=($event.target as HTMLSelectElement).value==='large'" class="gs-select" :id="uid('gsTextSize')" data-source-id="gsTextSize"><option value="normal">标准</option><option value="large">较大</option></select></div>
<div class="gs-setting-row"><div><h3>动效偏好</h3><p>降低动画时精灵球与窗口过渡会减少动态效果。</p></div><select :value="aiWorkspace.reduced?'reduce':'normal'" @change="aiWorkspace.reduced=($event.target as HTMLSelectElement).value==='reduce'" class="gs-select" :id="uid('gsMotion')" data-source-id="gsMotion"><option value="normal">标准动画</option><option value="reduce">减少动态效果</option></select></div>
<div class="gs-setting-row"><div><h3>AI 默认打开方式</h3><p>默认悬浮大窗口；可在设置中改为全屏起始。</p></div><select :value="aiWorkspace.startFull?'fullscreen':'floating'" @change="aiWorkspace.startFull=($event.target as HTMLSelectElement).value==='fullscreen'" class="gs-select" :id="uid('gsStartMode')" data-source-id="gsStartMode"><option value="floating">悬浮窗口（当前默认）</option><option value="fullscreen">全屏工作区</option></select></div>
<div class="gs-setting-row"><div><h3>电影推荐卡片</h3><p>采用你已选定的 A 横向紧凑卡。</p></div><span class="gs-pill">A · 横向紧凑</span></div><div class="gs-setting-row"><div><h3>对话版式</h3><p>Gemini 阅读式偏长文阅读，MovieInsight 对话式偏即时问答。</p></div><select :value="dialogMode" @change="dialogMode=($event.target as HTMLSelectElement).value" class="gs-select" :id="uid('gsDialogMode')" data-source-id="gsDialogMode"><option value="gemini">Gemini 阅读式</option><option value="native">MovieInsight 对话式</option></select></div>
</div>
</section>
<section aria-label="全站 AI 与 BYOK 设置" class="gs-panel" :id="uid('gs-ai')" data-source-id="gs-ai" v-show="settingsTab==='ai'"><div class="gs-card"><h2>AI 服务与 BYOK</h2><p>密钥默认按账户加密保存到数据库，退出登录和服务重启后保留，注销账户时删除。模型配置共享，对话和草稿互相独立。</p><p v-if="!status.persistent" role="status">当前环境尚未启用数据库凭据服务，暂不能保存新密钥。</p><div class="settings-api"><form @submit.prevent="saveConfig" autocomplete="off">
<label :for="props.surface+'-model'">DeepSeek 模型</label><select :id="props.surface+'-model'" v-model="model" :disabled="configBusy||busy"><option value="deepseek-flash">DeepSeek Flash</option><option value="deepseek-v4-pro">DeepSeek V4 Pro（通常费用更高）</option></select>
<label :for="props.surface+'-key'">API Key（不会回显或保存到浏览器）</label><input :id="props.surface+'-key'" v-model="apiKey" type="password" autocomplete="off" spellcheck="false" autocapitalize="off" maxlength="512" required :disabled="configBusy||busy"/>
<div class="settings-grid"><p class="assistant-note">每日及每轮模型调用次数：不限（按你的设置）</p><label>每次最大输出 Token<input v-model.number="output" type="number" min="256" max="8192" required/></label></div>
<label class="check"><input v-model="consent" type="checkbox" required/>我同意向所选服务商发送对话和必要的电影/偏好信息；启用后提交智能请求可能产生费用。</label>
<label class="check"><input v-model="cacheEnabled" type="checkbox"/>允许5分钟内复用相同用户、上下文和电影/偏好版本的回答（默认关闭）</label><button type="submit" :disabled="configBusy||busy||!consent||!apiKey||!status.persistent">保存并启用个人模型</button>
</form>
<div class="assistant-controls"><button type="button" :disabled="configBusy||busy||!status.configured" @click="verifyKey">检查 Key 与模型列表</button><button v-if="status.configured&&!status.enabled&&status.persistent" type="button" :disabled="configBusy||busy" @click="enableSaved">启用已保存的密钥</button><button type="button" :disabled="configBusy||!status.enabled" @click="disconnect">停用连接（保留密钥）</button></div>
<p class="assistant-note">状态：{{status.verification||'DISCONNECTED'}}；今日已派发 {{status.daily_calls_used??0}} / {{status.daily_calls??'不限'}} 次（UTC日）。列表检查不发送生成请求。不设每日或每轮调用次数上限；仍受90秒超时、并发和上下文长度保护。金额未取得可靠账单时显示未知。</p>
<label class="check"><input v-model="paidConsent" type="checkbox"/>我明确同意发送一次可能计费的模型连接测试。</label><button type="button" :disabled="busy||configBusy||!paidConsent||!status.enabled" @click="testConnection">发送模型测试（可能计费）</button>
</div></div></section>
<section aria-label="TMDB 数据连接设置" class="gs-panel" v-if="settings&&settingsTab==='tmdb'"><div class="gs-card"><TMDBSettings :id-prefix="props.surface"/></div></section>
<section aria-label="隐私与数据设置" class="gs-panel" :id="uid('gs-privacy')" data-source-id="gs-privacy" v-show="settingsTab==='privacy'">
<div class="gs-card"><h2>隐私与数据</h2><p>可查看当前页面的数据范围与控制方式。</p>
<div class="gs-setting-row"><div><h3>聊天记录</h3><p>当前登录页面内保留；刷新后不自动恢复记录。</p></div><span class="gs-pill">本地临时</span></div>
<div class="gs-setting-row"><div><h3>头像图片</h3><p>仅在当前页面裁剪与预览，不上传、不长期持久化。</p></div><span class="gs-pill">不上传</span></div>
<div class="gs-setting-row"><div><h3>清空此窗口记录</h3><p>只移除此窗口内的显示记录，不删除服务端数据。</p></div><button @click="clearSessions" class="gs-cta gs-cta-muted" :id="uid('clearDemoSessions')" data-source-id="clearDemoSessions" type="button">清空此窗口显示记录</button></div>
<div aria-live="polite" class="gs-save-status" :id="uid('privacyStatus')" data-source-id="privacyStatus"></div>
</div>
</section>
<AccountClosure v-if="settings&&settingsTab==='privacy'" :id-prefix="props.surface"/>
<section aria-label="关于 MovieInsight" class="gs-panel" :id="uid('gs-about')" data-source-id="gs-about" v-show="settingsTab==='about'">
<div class="gs-card"><h2>关于 MovieInsight</h2><p>AI 驱动的电影发现工具。目录、个人行为与助手请求都由服务端校验。</p><div class="gs-divider"></div><p>用户自带模型 Key；电影资料与个人行为由服务端验证。</p><p>This product uses the TMDB API but is not endorsed or certified by TMDB.</p><p><a href="https://www.themoviedb.org" target="_blank" rel="noopener noreferrer">电影数据来源：The Movie Database（TMDB）</a></p><div class="gs-notice" style="margin-top:16px">当前版本：M9 · 界面已接入真实目录与个人行为数据。</div></div>
</section>
</div>
</div>
</section>
<div aria-label="选择头像显示范围" aria-modal="true" class="crop-overlay" :id="uid('avatarCropOverlay')" data-source-id="avatarCropOverlay" role="dialog" v-if="cropOpen">
<div class="crop-dialog"><div class="crop-head"><div><h2>调整头像显示范围</h2><p>只有中心 40×40 像素的方框是头像选区。可拖动图片调整构图、缩放图片，框外部分不会被选中；PNG、WebP 透明区域不会填底。</p></div><button @click="cropOpen=false" aria-label="取消裁剪" class="crop-close" :id="uid('cropClose')" data-source-id="cropClose" type="button">×</button></div>
<div class="crop-content"><div class="crop-work"><div @pointercancel="stopCrop" @pointerdown="startCrop" @pointermove="moveCrop" @pointerup="stopCrop" class="crop-viewport" :id="uid('cropViewport')" data-source-id="cropViewport" style="touch-action:none"><canvas height="480" :id="uid('cropCanvas')" data-source-id="cropCanvas" width="480"></canvas></div><span style="font-size:11px;color:#a29aac">中央 40×40 取景框 · 可拖动底图</span></div><div class="crop-preview-block"><span>实际头像尺寸预览</span><span aria-label="头像预览" class="crop-preview avatar-slot is-empty"><svg aria-hidden="true" class="avatar-placeholder" fill="none" stroke="currentColor" stroke-linecap="round" stroke-width="1.55" viewBox="0 0 24 24"><circle cx="12" cy="8.2" r="3.45"></circle><path d="M5.5 19.1c.6-3.45 2.88-5.1 6.5-5.1s5.9 1.65 6.5 5.1"></path></svg><img :src="cropPreview" alt="" class="user-avatar-img" :id="uid('cropPreview')" data-source-id="cropPreview" v-if="cropPreview"/></span><strong style="font-size:12px;color:#e3dcf3">40 × 40 px · 与侧栏相同</strong><button @click="resetCrop" class="gs-cta gs-cta-muted" :id="uid('cropReset')" data-source-id="cropReset" type="button">恢复居中</button></div></div>
<div class="crop-controls"><label :for="uid('cropZoom')">缩放图片</label><input @input="drawCrop" :id="uid('cropZoom')" data-source-id="cropZoom" max="8" min="1" step="0.05" type="range" v-model.number="cropZoom" value="3"/></div>
<div class="crop-actions"><button @click="chooseAvatarFile" class="gs-cta gs-cta-muted" :id="uid('cropChooseFile')" data-source-id="cropChooseFile" type="button">选择本地图片</button><button @click="cropOpen=false" class="gs-cta gs-cta-muted" :id="uid('cropCancel')" data-source-id="cropCancel" type="button">取消</button><button @click="applyAvatar" :disabled="!cropPreview" class="gs-cta" :id="uid('cropApply')" data-source-id="cropApply" type="button">应用头像</button></div>
<input @change="loadAvatar" accept="image/png,image/jpeg,image/webp" hidden="" :id="uid('cropFileInput')" data-source-id="cropFileInput" type="file"/>
</div>
</div></div></template>
<style scoped>

.prototype-ai{--paper:#0a0b10;--surface:#14141d;--surface-hi:#1c1b28;--line:rgba(211,198,255,.115);--soft:#a6a2b7;--text:#f3f1f9;--purple:#9b7bf2;--glow:#a270ff;--muted:#727084;--button:#6654ca;--focus:#bbaaff;--amber:#e5ad75}*{box-sizing:border-box}.prototype-ai,.prototype-ai{height:100%;margin:0}.prototype-ai{background:var(--paper);color:var(--text);font-family:Inter,'Microsoft Yahei','PingFang SC',system-ui,sans-serif;overflow-x:hidden;font-size:14px}button,textarea,input{font:inherit}button{cursor:pointer}button:focus-visible,textarea:focus-visible{outline:2px solid #bbaaff;outline-offset:2px}button:disabled{opacity:.45;cursor:default}button svg{pointer-events:none}svg.ico{width:19px;height:19px;stroke:currentColor;fill:none;stroke-width:1.8;stroke-linecap:round;stroke-linejoin:round;flex:none} .sr-only{position:absolute;width:1px;height:1px;clip:rect(0 0 0 0);overflow:hidden;white-space:nowrap}
.site{min-height:100vh;position:relative;overflow:hidden}.site:before{content:'';position:absolute;inset:-20% -20% 0;z-index:-2;background:radial-gradient(ellipse at 76% 23%,#4e3d63 0%,#29253a 24%,transparent 46%),radial-gradient(ellipse at 54% 60%,#372e4b 0%,transparent 42%),radial-gradient(ellipse at 90% 82%,#473040 0%,transparent 45%),linear-gradient(130deg,#090a10 10%,#191823 65%,#1b1823)}.site:after{content:'';z-index:-1;position:absolute;inset:170px -10% 8%;background:linear-gradient(0deg,#08080d 0%,#0a0b12aa 24%,transparent 73%),radial-gradient(ellipse at 75% 70%,transparent 5%,#090a12bd 78%);pointer-events:none}.site-nav{height:75px;display:flex;align-items:center;gap:30px;padding:0 clamp(22px,5vw,90px);border-bottom:1px solid #ffffff11;position:relative;z-index:1;background:#08091066;backdrop-filter:blur(16px)}.logo{font-family:Georgia,'Noto Serif SC',serif;font-size:23px;font-weight:bold;white-space:nowrap;letter-spacing:-.6px}.logo small{font-size:11px;letter-spacing:3px;display:block;font-family:system-ui;margin-top:3px;color:#b5adcd;font-weight:400}.site-links{display:flex;gap:29px;margin-left:auto}.site-links button{border:0;background:none;color:#b5afbf;padding:8px 0}.site-links button:hover,.site-links button.active{color:white}.site-actions{margin-left:7px;display:flex;gap:10px}.nav-pill{background:#ffffff10;border:1px solid #ffffff20;color:#d5cbe9;padding:9px 13px;border-radius:99px}.nav-pill:hover{background:#ffffff19}.hero{min-height:calc(100vh - 75px);display:flex;align-items:center;padding:74px clamp(25px,8.1vw,145px) 144px;position:relative}.hero-copy{max-width:690px;position:relative;z-index:1}.eyebrow{font-size:11px;letter-spacing:3px;text-transform:uppercase;color:#ceaeef;display:flex;align-items:center;gap:10px}.eyebrow:before{content:'';width:30px;height:1px;background:#e0b4ff}.hero h1{font-weight:500;font-family:Georgia,'Noto Serif SC',serif;font-size:clamp(45px,5.7vw,86px);line-height:1.09;letter-spacing:-3px;margin:24px 0}.hero h1 em{font-style:normal;color:#c9b4ed}.hero p{color:#c5bdcb;max-width:490px;line-height:1.95;font-size:15px}.hero-btns{display:flex;gap:14px;margin-top:32px;flex-wrap:wrap}.primary,.secondary{display:inline-flex;align-items:center;gap:9px;justify-content:center;border-radius:14px;padding:15px 21px;font-weight:600;border:1px solid}.primary{background:linear-gradient(135deg,#7659d9,#5541af);color:#fff;border-color:#a495ee6b;box-shadow:0 10px 30px #261a5180}.primary:hover{background:linear-gradient(135deg,#8770e8,#6250c7)}.secondary{background:#ffffff10;color:#ece5fa;border-color:#ffffff20}.secondary:hover{background:#ffffff1a}.hero-index{position:absolute;bottom:36px;left:clamp(25px,8vw,145px);font-size:11px;letter-spacing:1px;color:#9e92a3}.film-graphic{position:absolute;right:4%;top:15%;width:min(50vw,620px);height:70%;opacity:.63;pointer-events:none;filter:drop-shadow(0 25px 44px #0008)}.film-graphic .arch{position:absolute;inset:8% 12%;border:45px solid #c9a5ca10;border-radius:48% 48% 4% 4%;transform:rotate(12deg)}.film-graphic .disc{position:absolute;top:15%;left:20%;width:60%;aspect-ratio:1;border-radius:50%;border:1px solid #c7aad63b;box-shadow:0 0 0 16px #a99bd112,0 0 0 48px #9f87bd09,0 0 120px #bd7de344;background:radial-gradient(circle at 40% 45%,#beabf055,#66569e11 31%,#10111d 68%)}.film-graphic .disc:after{content:'';position:absolute;inset:26%;border:1px solid #d4c1f047;border-radius:50%;box-shadow:0 0 0 26px #a48be010}.film-graphic .bars{position:absolute;width:72%;height:5px;bottom:14%;left:14%;background:linear-gradient(90deg,transparent,#a796d450,transparent);transform:rotate(-16deg);box-shadow:0 18px 0 #b38bd716,0 -18px 0 #b38bd71c}
.orb-launcher{position:fixed;right:27px;bottom:26px;width:64px;height:64px;z-index:25;background:none;border:0;border-radius:50%;padding:0;display:grid;place-items:center;transition:transform .21s cubic-bezier(.2,.75,.2,1);outline-offset:6px}.orb-launcher:hover{transform:scale(1.055)}.orb-launcher[hidden]{display:none}.orb-skin{display:block;position:relative;isolation:isolate;width:100%;height:100%;aspect-ratio:1;pointer-events:none;--ex:0px;--ey:0px}.orb-skin img{display:block;width:100%;height:100%;object-fit:contain;position:relative;z-index:2;user-select:none}.orb-skin:before{content:'';position:absolute;z-index:0;inset:-24%;border-radius:50%;background:radial-gradient(circle,rgba(160,103,255,.96) 0%,rgba(145,84,255,.80) 18%,rgba(126,66,248,.52) 34%,rgba(106,48,230,.20) 49%,rgba(88,36,201,.04) 60%,transparent 68%);filter:blur(7px);animation:orbHalo 5.3s ease-in-out infinite;opacity:.86}.orb-skin.mini:before{display:none!important}.orb-skin .eye{position:absolute;z-index:3;top:43.2%;width:7.6%;height:22%;background:#fff;border-radius:9px;box-shadow:0 0 8px #fff8,0 0 14px #b8aaff7d;transform:translate(var(--ex),var(--ey)) scaleY(var(--blink,1));transform-origin:center;transition:transform .15s ease-out}.orb-skin .eye.left{left:32.2%}.orb-skin .eye.right{left:60.8%}.orb-launcher.focusing .orb-skin{animation:focusPulse .38s ease forwards}@keyframes orbHalo{0%,100%{opacity:.53;transform:scale(.97)}50%{opacity:1;transform:scale(1.05)}}@keyframes focusPulse{0%{transform:scale(1)}45%{transform:scale(.91);filter:brightness(1.55)}100%{transform:scale(.88);opacity:.1}}
.backdrop{position:fixed;inset:0;z-index:29;background:#04050aca;backdrop-filter:blur(8px) saturate(.8);opacity:0;visibility:hidden;transition:opacity .32s ease,visibility .32s}.backdrop.show{opacity:1;visibility:visible}.window{position:fixed;z-index:30;left:50%;top:50%;width:min(1280px,calc(100vw - 60px));height:min(812px,calc(100dvh - 56px));transform:translate(-50%,-48%) scale(.97);opacity:0;visibility:hidden;display:flex;flex-direction:column;border:1px solid #c8b0ee3b;border-radius:25px;overflow:hidden;box-shadow:0 30px 90px #000b,0 0 0 1px #ffffff08;background:linear-gradient(145deg,#13111c 0%,#0b0b11 45%,#090a0e 100%);transition:opacity .27s ease,transform .32s ease,visibility .32s,border-radius .3s,width .3s,height .3s}.window.show{visibility:visible;opacity:1;transform:translate(-50%,-50%) scale(1)}.window.full{width:100vw;height:100dvh;border-radius:0;left:0;top:0;transform:translateY(0) scale(1);border:0}.window-head{flex:none;min-height:74px;display:flex;align-items:center;gap:13px;padding:13px 23px;background:linear-gradient(100deg,#221c37dc,#181426db 46%,#12101bde);border-bottom:1px solid #d4c4fb1c}.icon-btn{display:grid;place-items:center;width:38px;height:38px;border-radius:11px;background:transparent;border:1px solid transparent;color:#bdb7ce}.icon-btn:hover{background:#ffffff12;border-color:#ffffff13;color:#fff}.head-id{display:flex;align-items:center;gap:11px;min-width:0}.mark{height:35px;width:35px;flex:none}.head-title{font-size:14px;line-height:1.2;font-weight:650;letter-spacing:.08px}.head-sub{font-size:11px;color:#a59eb9;margin-top:5px}.head-fill{flex:1}.head-status{display:flex;align-items:center;gap:7px;background:#ffffff09;border:1px solid #ffffff16;color:#b9b2c9;font-size:11px;padding:8px 10px;border-radius:10px;white-space:nowrap}.head-status .dot{width:6px;height:6px;border-radius:50%;background:#deb477;box-shadow:0 0 0 3px #dbb66b18}.head-ops{display:flex;gap:4px}.divider-v{height:24px;width:1px;background:#ffffff20;margin:0 3px}.window-body{flex:1;min-height:0;display:flex;position:relative}.sidebar{width:0;flex:none;overflow:hidden;border-right:0 solid var(--line);background:#111019;transition:width .28s ease;z-index:4}.window.sidebar-open .sidebar{width:246px;border-right-width:1px}.sidebar-inner{width:245px;height:100%;padding:19px 14px;display:flex;flex-direction:column;gap:13px}.side-head{display:flex;align-items:center;justify-content:space-between;margin:0 6px 3px;color:#a8a1b7;font-size:11px;letter-spacing:1px}.side-new{border:1px dashed #8576ba69;border-radius:12px;background:#8b76bf12;color:#d8c9fb;padding:11px 12px;text-align:left;display:flex;align-items:center;gap:9px}.side-new:hover{background:#8b76bf24}.session-list{display:flex;flex-direction:column;gap:5px;overflow:auto}.session{border:1px solid transparent;border-radius:10px;background:none;text-align:left;color:#b7b0c5;padding:10px 11px;white-space:nowrap;text-overflow:ellipsis;overflow:hidden}.session:hover{background:#ffffff09}.session.active{background:#6b559329;color:#eee6ff;border-color:#a58bd02f}.side-footer{margin-top:auto;color:#80798b;font-size:11px;line-height:1.6;padding:12px 6px;border-top:1px solid #ffffff14}.side-footer b{color:#c3b9d6;font-weight:500}
.chat-main{display:flex;flex:1;flex-direction:column;min-width:0;min-height:0;position:relative}.thread{flex:1;overflow-y:auto;overflow-x:hidden;scrollbar-color:#564b63 transparent;scrollbar-width:thin;padding:31px clamp(18px,4.1vw,60px) 35px;overscroll-behavior:contain}.thread-inner{max-width:1000px;margin:0 auto;display:flex;flex-direction:column;gap:29px;min-height:100%}.thread-tag{text-align:center;color:#6f6b7c;font-size:11px;letter-spacing:.5px}.msg{display:flex;gap:13px;max-width:100%;min-width:0}.msg.user{align-self:flex-end;max-width:min(75%,690px)}.bubble{color:#f9f5ff;background:linear-gradient(140deg,#5f54dc,#6253c8);padding:16px 20px;border-radius:19px 19px 5px 19px;font-size:14px;line-height:1.8;white-space:pre-wrap;overflow-wrap:anywhere;box-shadow:0 8px 32px #4932a02b}.avatar{width:34px;height:34px;flex:none;margin-top:2px}.assistant-body{min-width:0;flex:1;max-width:100%}.assistant-name{display:flex;align-items:center;gap:9px;margin:8px 0 13px;font-weight:650;font-size:13px}.assistant-name small{font-weight:400;font-size:11px;color:#817c8d}.reply{line-height:1.8;color:#ddd9e7;font-size:14px;max-width:860px;overflow-wrap:anywhere}.reply b{font-weight:600;color:#f4efff}.reply p{margin:0 0 12px}.trust-note{display:flex;align-items:center;gap:7px;color:#aea6be;font-size:11px;margin:13px 0 12px;flex-wrap:wrap}.trust-note:before{content:'';height:5px;width:5px;border-radius:50%;background:#a28cc9}.cards-head{display:flex;align-items:center;gap:9px;justify-content:space-between;margin:17px 0 11px;color:#c4bdd4;font-size:12px}.cards-head span{color:#797387;font-size:11px}.cards-rail{display:flex;align-items:stretch;gap:11px;overflow-x:auto;padding:1px 3px 9px 1px;scrollbar-width:thin;scrollbar-color:#50445b transparent;scroll-snap-type:x proximity;max-width:100%}.movie-card{position:relative;scroll-snap-align:start;flex:0 0 365px;min-height:200px;display:flex;gap:13px;background:linear-gradient(135deg,#24232b,#17171f 85%);border:1px solid #ffffff15;border-radius:16px;padding:10px;overflow:hidden;transition:border-color .2s,background .2s}.movie-card:hover{border-color:#a696dd55;background:linear-gradient(135deg,#302c3b,#191820 85%)}.poster{position:relative;display:flex;align-items:flex-end;justify-content:center;flex:none;width:111px;aspect-ratio:2/3;max-height:173px;overflow:hidden;border-radius:9px;background:#1b1e31;color:#fff;isolation:isolate}.poster:before{content:'';position:absolute;inset:0;z-index:-2}.poster.inception:before{background:radial-gradient(circle at 62% 35%,#6b8ba1b0 0%,transparent 39%),linear-gradient(150deg,#101a30,#233354 42%,#08131e 100%)}.poster.shawshank:before{background:radial-gradient(ellipse at 48% 28%,#f7d39cdb 0%,#d9ad8045 23%,transparent 63%),linear-gradient(170deg,#5a4e5e,#554253 45%,#10141c)}.poster-art{position:absolute;inset:0;z-index:-1;pointer-events:none}.poster-art:before{content:'';position:absolute;left:-18%;width:136%;height:120%;top:42%;background:repeating-linear-gradient(100deg,#9eafcb30 0 2px,transparent 2px 18px);transform:perspective(200px) rotateX(39deg)}.poster.shawshank .poster-art:before{inset:20% 25% -10%;width:50%;height:90%;border:2px solid #f2d5ac55;border-radius:45% 45% 0 0;transform:none;background:linear-gradient(90deg,transparent 42%,#ffdaaa33 45% 49%,transparent 52%)}.poster:after{content:'';position:absolute;inset:0;background:linear-gradient(180deg,transparent 44%,#02060bbf 100%);pointer-events:none}.poster-title{z-index:2;text-align:center;font-family:Georgia,'Noto Serif SC',serif;font-weight:bold;font-size:16px;line-height:1.3;letter-spacing:2px;margin:0 3px 16px;text-shadow:0 2px 10px #000;word-break:break-all}.poster-title small{display:block;letter-spacing:2px;font-size:7px;color:#f7f5fbaf;margin-top:4px}.poster-label{position:absolute;top:5px;left:5px;z-index:2;padding:2px 4px;border-radius:4px;background:#05050992;color:#e1dbed;font-size:8px}.poster img.poster-uploaded{width:100%;height:100%;object-fit:cover;position:absolute;inset:0;z-index:1}.movie-info{display:flex;flex:1;min-width:0;flex-direction:column;align-items:flex-start;padding:4px 1px 1px 0}.movie-title{font-size:14px;font-weight:650;margin:2px 0 5px;color:#f4f1f9}.movie-meta{font-size:11px;color:#a19aab}.verified{color:#d6b7ee;background:#a88ccd19;border:1px solid #b09ed12a;display:inline-flex;align-items:center;border-radius:5px;font-size:10px;padding:3px 6px;margin-top:9px}.why{font-size:11px;line-height:1.7;color:#b6afc0;margin:10px 0 10px;display:-webkit-box;-webkit-line-clamp:3;-webkit-box-orient:vertical;overflow:hidden}.movie-actions{display:flex;gap:6px;align-items:center;flex-wrap:wrap;margin-top:auto}.mini-btn{font-size:11px;color:#d8d3e8;border:1px solid #ffffff24;border-radius:7px;background:#ffffff0c;padding:6px 9px;white-space:nowrap}.mini-btn:hover{background:#8f77dd35;border-color:#9d88eb66}.mini-btn.is-active{background:#8468da35;color:#e5d9ff;border-color:#a28de363}.movie-menu{font-size:10px;border:0;background:none;color:#aaa0bc;padding:4px 0}.movie-menu:hover{color:#fff}.followups{display:flex;flex-wrap:wrap;gap:8px;margin-top:16px}.followup{border:1px solid #9e80da54;border-radius:99px;background:#8b6dcc0e;color:#cfbfff;padding:8px 12px;font-size:11px}.followup:hover{background:#7a59c234}.inline-action{display:inline-flex;align-items:center;gap:6px;color:#d6bdff;border:0;background:none;padding:4px 0;font-size:12px}.inline-action:hover{text-decoration:underline}.no-result{margin-top:11px;background:#3b2f2722;border:1px dashed #b59a7052;padding:15px;border-radius:12px;color:#cdbca8;font-size:12px}.empty-state{margin:auto;max-width:600px;text-align:center;padding:40px 0}.empty-orb{width:82px;height:82px;display:block;margin:0 auto 17px}.empty-state h2{font-family:Georgia,'Noto Serif SC',serif;font-weight:500;font-size:28px;letter-spacing:.4px;margin:9px 0}.empty-state p{color:#928c9f;font-size:13px;line-height:1.9}.empty-suggestions{display:flex;gap:9px;justify-content:center;flex-wrap:wrap;margin-top:19px}.date-line{color:#767082;font-size:11px;text-align:center;margin:0 0 13px}
.composer-zone{flex:none;border-top:1px solid #ffffff14;padding:16px clamp(18px,4.1vw,60px) 12px;background:linear-gradient(180deg,#111016f0,#100f17 85%)}.composer-wrap{max-width:1000px;margin:auto}.compose{display:flex;align-items:flex-end;gap:11px;background:#1d1b25;border:1px solid #68588d80;border-radius:17px;box-shadow:0 0 0 2px #6f58bb0a;transition:border-color .2s}.compose:focus-within{border-color:#b39cf3a3;box-shadow:0 0 0 3px #785bbb12}.compose textarea{flex:1;min-width:0;resize:none;min-height:53px;max-height:148px;padding:16px 8px 12px 17px;border:0;outline:0!important;color:#f5f1ff;background:transparent;line-height:1.65;font-size:14px;scrollbar-width:thin}.compose textarea::placeholder{color:#8f899e}.compose .send{flex:none;border:0;border-radius:12px;width:38px;height:38px;display:grid;place-items:center;background:#7357ce;color:white;margin:0 10px 9px 0}.compose .send:hover{background:#8867e9}.compose .send svg{width:18px;height:18px}.compose .send:disabled{background:#555067}.compose-footer{display:flex;justify-content:space-between;align-items:center;gap:8px;color:#787184;font-size:10px;margin-top:8px}.compose-footer .key{color:#afa4c5}.typing-dots{display:flex;gap:4px;align-items:center;height:18px}.typing-dots i{width:5px;height:5px;border-radius:100%;background:#b8a1fb;animation:pulse 1.1s infinite ease-in-out}.typing-dots i:nth-child(2){animation-delay:.15s}.typing-dots i:nth-child(3){animation-delay:.3s}@keyframes pulse{0%,65%,100%{opacity:.35;transform:translateY(0)}30%{opacity:1;transform:translateY(-3px)}}
.detail-panel,.settings-panel,.compare-pop{position:absolute;z-index:9;top:16px;right:20px;width:min(380px,calc(100% - 40px));background:#22202c;border:1px solid #b3a6cf4d;border-radius:16px;box-shadow:0 16px 55px #0009;padding:20px}.detail-panel[hidden],.settings-panel[hidden],.compare-pop[hidden]{display:none}.detail-panel h3,.settings-panel h3,.compare-pop h3{margin:0 0 10px;font-size:16px}.panel-x{float:right;color:#cec7df;border:0;background:transparent;font-size:20px;line-height:1}.detail-panel p,.settings-panel p,.compare-pop p{color:#b4adc3;font-size:12px;line-height:1.8}.panel-tag{font-size:11px;color:#e0c3a2;background:#deb58e11;padding:6px 9px;border:1px solid #a9947251;border-radius:9px;display:inline-block}.toggle-row{display:grid;grid-template-columns:1fr 1fr;gap:8px;margin:15px 0}.toggle-row button{background:#181722;border:1px solid #ffffff23;border-radius:10px;color:#bcb4cd;padding:12px 7px}.toggle-row button.selected{background:#6551a730;border-color:#ae98e06b;color:#eee7ff}.info-line{font-size:11px;color:#8c8598;padding:8px 0;border-top:1px solid #ffffff15}.scope-tag{position:fixed;left:17px;bottom:12px;z-index:1;color:#9790a4;letter-spacing:.3px;font-size:10px}.dash-screen{display:none;margin:16px clamp(25px,8vw,145px);border:1px solid #6f62814f;background:#101018b8;border-radius:18px;padding:25px;position:relative}.dash-screen.show{display:block}.dash-grid{display:grid;grid-template-columns:repeat(4,1fr);gap:11px}.dash-item{border:1px solid #ffffff18;border-radius:12px;padding:18px;background:#201e2b88;min-height:100px}.dash-item span{display:block;font-size:12px;color:#ddd4ef;font-weight:650;margin-bottom:12px}.dash-item p{font-size:11px;line-height:1.6;color:#9f96ad}.dash-screen h2{font-weight:500;margin:0 0 6px}.dash-screen>p{font-size:12px;color:#978ea8;margin:0 0 20px}.site.admin .hero{min-height:70vh}.site.admin #navDash{display:inline-flex}.site:not(.admin) #navDash{display:none}.site.admin .dash-screen{display:block}
.prototype-ai.card-portrait .movie-card{flex-direction:column;flex-basis:218px;min-height:0;padding:9px}.card-portrait .poster{width:100%;max-height:none;height:214px;aspect-ratio:auto}.card-portrait .movie-info{padding:5px 3px;min-height:155px}.card-portrait .why{-webkit-line-clamp:2}.card-portrait .poster-title{font-size:23px}.card-portrait .poster-label{font-size:9px}.card-portrait .movie-actions{margin-top:auto}
@media(max-width:920px){.site-links{gap:15px}.site-nav{padding:0 26px}.hero{padding:70px 38px 120px}.film-graphic{opacity:.35}.window{width:calc(100vw - 28px);height:calc(100dvh - 28px)}.window-head{padding:11px 13px}.thread{padding:24px 22px}.composer-zone{padding:13px 22px 10px}.dash-grid{grid-template-columns:repeat(2,1fr)}}
@media(max-width:620px){.site-nav{height:63px;padding:0 18px}.logo{font-size:21px}.site-links{display:none}.site-actions{margin-left:auto}.nav-pill{font-size:11px;padding:9px}.hero{min-height:calc(100vh - 63px);align-items:center;padding:40px 23px 100px}.hero h1{font-size:47px;letter-spacing:-1.5px}.hero p{font-size:13px}.film-graphic{width:90vw;right:-27%;height:55%;top:4%;opacity:.3}.scope-tag{font-size:9px}.orb-launcher{right:18px;bottom:21px;width:56px;height:56px}.window{top:0;left:0;width:100vw;height:100dvh;border-radius:0;border:0;transform:translateY(14px) scale(.98)}.window.show{transform:translateY(0) scale(1)}.window.full{transform:none}.window-head{min-height:66px;gap:5px;padding:10px 8px}.head-id{gap:7px}.mark{width:30px;height:30px}.head-title{font-size:12px}.head-sub{font-size:10px}.head-status{display:flex;width:27px;height:29px;justify-content:center;padding:4px;font-size:0;border-radius:9px}.head-status .dot{width:7px;height:7px}.icon-btn{width:34px;height:34px}.head-ops{gap:0}.divider-v{display:none}.window.sidebar-open .sidebar{position:absolute;left:0;top:0;bottom:0;width:80%;max-width:280px;box-shadow:20px 0 40px #0009;border-right:1px solid #b7a4f02b}.thread{padding:20px 14px 24px}.thread-inner{gap:24px}.msg{gap:9px}.msg.user{max-width:90%}.bubble{font-size:13px;padding:12px 14px}.avatar{width:29px;height:29px}.assistant-name{margin:4px 0 10px}.reply{font-size:13px}.movie-card{flex-basis:282px;min-height:175px;gap:10px}.poster{width:90px;max-height:149px}.movie-title{font-size:13px}.movie-meta{font-size:10px}.why{-webkit-line-clamp:2;font-size:10px;margin:7px 0}.mini-btn{font-size:10px;padding:5px 7px}.composer-zone{padding:10px 12px calc(8px + env(safe-area-inset-bottom))}.compose textarea{min-height:49px;padding:13px 5px 10px 13px;font-size:13px}.compose-footer{font-size:9px}.window.sidebar-open .sidebar{transition:width .2s}.detail-panel,.settings-panel,.compare-pop{right:10px;top:10px;width:calc(100% - 20px)}.dash-screen{margin:15px;padding:17px}.dash-grid{grid-template-columns:1fr}.site.admin .hero{min-height:50vh}}
@media(prefers-reduced-motion:reduce){*,*:before,*:after{scroll-behavior:auto!important;animation-duration:.001ms!important;transition-duration:.001ms!important}}

/* V13.9 reading layout retained, standalone floating-sidepanel removed */

.window{
  width:min(1400px,calc(100vw - 44px));
  height:min(860px,calc(100dvh - 30px));
  border-radius:28px;
  border:1px solid #c8b0ee20;
  background:radial-gradient(1200px 520px at 50% 115%,#25174626 0%,transparent 60%),linear-gradient(180deg,#08090d 0%,#090a10 56%,#0a0b11 100%);
  box-shadow:0 22px 70px #000a,0 0 0 1px #ffffff05;
}
.window-head{
  min-height:56px;
  padding:12px 20px 6px;
  background:transparent;
  border-bottom:none;
  gap:12px;
}
.head-id .mark{width:30px;height:30px}
.head-title{font-size:13px;font-weight:620;color:#f3efff}
.head-sub{font-size:10px;color:#938ca3;margin-top:3px}
.head-status{background:#ffffff08;border-color:#ffffff10;color:#aca4bd;padding:7px 10px;border-radius:999px;font-size:11px}
.head-ops{gap:6px}
.icon-btn{width:34px;height:34px;border-radius:999px;background:#ffffff08;border-color:#ffffff10;color:#cdc7dc}
.icon-btn:hover{background:#ffffff12;border-color:#ffffff18}
.divider-v{display:none}


.thread{
  padding:34px 24px 26px;
}
.thread-inner{
  max-width:760px;
  margin:0 auto;
  gap:36px;
}
.thread-tag,.date-line{text-align:left;color:#6d687a;font-size:11px}
.msg{gap:12px;max-width:100%}
.msg.user{align-self:center;max-width:760px}
.bubble{
  background:linear-gradient(180deg,#1a1721,#17151d);
  border:1px solid #ffffff10;
  color:#f5f1ff;
  border-radius:20px;
  box-shadow:none;
  padding:14px 18px;
  font-size:14px;
  line-height:1.85;
}
.avatar{width:32px;height:32px;margin-top:4px}
.assistant-body{max-width:100%}
.assistant-name{
  margin:0 0 14px;
  font-size:12px;
  color:#d9d4e6;
}
.assistant-name small{font-size:10px;color:#868092}
.reply{
  max-width:100%;
  font-size:15px;
  line-height:1.95;
  color:#e5e0ef;
}
.reply p{margin:0 0 14px}
.reply ul,.reply ol{margin:0 0 14px 1.35em;padding:0}
.trust-note{margin:14px 0 12px;color:#9f96b1}
.cards-head{margin:18px 0 12px}
.cards-rail{padding-bottom:8px}
.movie-card{
  flex:0 0 min(430px,92vw);
  border-radius:18px;
  background:linear-gradient(145deg,#1b1b24,#12131a 86%);
}
.followups{margin-top:18px}
.followup{background:#11101a;border-color:#7662ad5d;color:#d8cbff;padding:8px 13px}
.empty-state{
  margin:0 auto auto;
  max-width:760px;
  text-align:left;
  padding:56px 0 24px;
}
.empty-state .empty-orb{margin:0 0 18px}
.empty-state h2{font-size:30px;margin:0 0 10px}
.empty-suggestions{justify-content:flex-start}
.composer-zone{
  border-top:none;
  background:transparent;
  padding:8px 24px 18px;
}
.composer-wrap{max-width:760px;margin:auto}
.compose{
  background:linear-gradient(180deg,#17171f,#15151d);
  border:1px solid #ffffff10;
  border-radius:28px;
  box-shadow:0 8px 35px #0006;
  gap:10px;
  padding-right:8px;
}
.compose:focus-within{border-color:#9b84db80;box-shadow:0 12px 40px #0008,0 0 0 1px #7458b320}
.compose textarea{min-height:58px;max-height:164px;padding:16px 10px 14px 18px;font-size:14px;line-height:1.7}
.send{align-self:center;border-radius:999px;width:42px;height:42px;background:linear-gradient(180deg,#7053cf,#5d42bc);box-shadow:none}
.compose-footer{padding:10px 6px 0;color:#878095}
.scope-tag{max-width:760px;margin:0 auto 12px;padding:0 24px;color:#635f6d}
.compare-pop,.settings-panel,.detail-panel{z-index:8}
@media (min-width: 1100px){
  .window-head{padding-inline:28px}
  .thread{padding-top:42px}
}
@media (max-width: 820px){
  .window{width:100vw;height:100dvh;border-radius:0;border:0}
  .window-head{padding:10px 14px 6px;gap:8px}
  .head-id .head-sub{display:none}
  .head-status{display:none}
  .thread{padding:22px 16px 18px}
  .thread-inner,.composer-wrap,.empty-state{max-width:100%}
  .reply{font-size:14px;line-height:1.9}
  .composer-zone{padding:6px 14px 14px}
  .movie-card{flex-basis:min(360px,88vw)}
  .scope-tag{padding:0 16px}
}

/* V13.10 R1 · integrated sidebar: same background plane as the reading area */
.window-body{display:flex;flex:1;min-height:0;position:relative;background:transparent}
.sidebar{
  position:relative;left:auto;top:auto;bottom:auto;
  width:0;flex:0 0 auto;opacity:1;transform:none;
  overflow:hidden;background:transparent;
  border:0;border-right:0 solid rgba(211,198,255,.11);
  border-radius:0;box-shadow:none;backdrop-filter:none;
  transition:width .28s ease;z-index:4;
}
.window.sidebar-open .sidebar{width:246px;border-right-width:1px}
.sidebar-inner{width:245px;height:100%;padding:19px 14px;display:flex;flex-direction:column;gap:13px}
.sidebar .side-head{color:#a8a1b7}
.sidebar .side-new{background:#8b76bf12}
.sidebar .session.active{background:#6b559329}
/* compact A cards fit two across inside 760px reading column */
.movie-card{flex:0 0 345px}
.chat-main{display:flex;flex:1;flex-direction:column;min-width:0;min-height:0;position:relative}
@media(max-width:620px){
  .window.sidebar-open .sidebar{
    position:absolute;left:0;top:0;bottom:0;width:80%;max-width:280px;
    background:#090a10;border-radius:0;box-shadow:12px 0 28px #0005;
    border-right:1px solid rgba(211,198,255,.11);z-index:7;
  }
  .sidebar-inner{width:min(279px,calc(80vw - 1px))}
  .movie-card{flex-basis:282px}
}

/* V13.10 R2 · unified account footer, following the small avatar/name + gear reference */
.sidebar-inner{position:relative;min-height:0}
.sidebar-inner .account-footer{
  margin-top:auto;
  display:flex;
  flex:none;
  align-items:center;
  justify-content:space-between;
  gap:10px;
  padding:12px 6px 3px;
  border-top:1px solid rgba(211,198,255,.075);
  background:transparent;
  min-height:54px;
}
.account-identity{display:flex;align-items:center;gap:10px;min-width:0;flex:1;padding:4px 3px;border:0;border-radius:10px;background:transparent;color:#f1edfa;text-align:left;cursor:pointer}
.account-identity:hover,.account-identity:focus-visible{background:#ffffff0a}
.account-avatar,.profile-avatar-lg{display:grid;place-items:center;flex:none;border-radius:50%;background:radial-gradient(circle at 35% 24%,#5a4a8b,#30254d 65%,#181626);border:1px solid #b8a0f15e;overflow:hidden}
.account-avatar{width:31px;height:31px}.account-avatar svg{width:27px;height:27px;transform:translateY(3px)}
.account-name{font-size:13px;font-weight:600;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;color:#eeebf6}
.account-settings{flex:none;width:36px;height:36px;display:grid;place-items:center;border:0;border-radius:10px;color:#bbb0d7;background:transparent;cursor:pointer}
.account-settings:hover,.account-settings:focus-visible{background:#ffffff10;color:#e9ddff}
.profile-panel[hidden]{display:none!important}
.profile-panel{position:absolute;z-index:12;bottom:70px;left:12px;right:12px;padding:16px 14px;background:#17141f;border:1px solid #7965955c;border-radius:14px;box-shadow:0 16px 46px #000a;color:#eeeaf8}
.profile-top{display:flex;align-items:center;gap:10px}
.profile-avatar-lg{width:44px;height:44px}.profile-avatar-lg svg{width:38px;height:38px;transform:translateY(4px)}
.profile-meta{display:flex;flex-direction:column;gap:4px;min-width:0;flex:1}.profile-meta strong{font-size:14px;color:#f6f1ff}.profile-meta small{font-size:10px;color:#9b91ae}
.profile-close{width:24px;height:24px;align-self:flex-start;display:grid;place-items:center;border:0;background:transparent;color:#b7aecb;font-size:20px;cursor:pointer;border-radius:6px}
.profile-close:hover{background:#ffffff10;color:#fff}
.profile-detail,.profile-note{line-height:1.65;font-size:11px;color:#bbb3c9;margin-top:14px}
.profile-note{border-top:1px solid #ffffff16;padding-top:12px;color:#8e879a}
@media(max-width:620px){.sidebar-inner .account-footer{padding-bottom:calc(7px + env(safe-area-inset-bottom))}}

#layoutToggle{display:none!important}
/* V13.10 R4 — account thumbnail matches screenshot #2 */
.account-avatar,.profile-avatar-lg{
  background:#2e2e32!important;border:0!important;border-radius:11px!important;
  box-shadow:none!important;overflow:hidden!important;
}
.account-avatar{width:38px!important;height:38px!important}
.profile-avatar-lg{width:52px!important;height:52px!important;border-radius:14px!important}
.user-avatar-img{display:block;width:100%;height:100%;object-fit:cover}
.account-identity{gap:10px;padding:5px 7px;border-radius:12px}
.account-identity:hover{background:#ffffff08}
.account-settings{color:#cec1e7}
.profile-edit-link{display:block;width:100%;margin-top:12px;border:1px solid #ffffff24;background:#ffffff0b;color:#e3dafa;border-radius:9px;padding:10px}
/* Full-page route; settings is a dedicated screen, not a drawer, not a chat modal. */
.global-settings-page[hidden],.crop-overlay[hidden]{display:none!important}
.global-settings-page{
 position:fixed;inset:0;z-index:80;overflow:auto;overscroll-behavior:contain;
 background:radial-gradient(ellipse at 80% 5%,#2b203f37,transparent 45%),#090a0f;
 color:#f0edf6;font-family:inherit;
}
.gs-top{
 position:sticky;top:0;z-index:4;min-height:70px;display:flex;align-items:center;justify-content:space-between;
 gap:14px;padding:14px clamp(18px,4vw,55px);background:#0b0c12ee;backdrop-filter:blur(15px);
 border-bottom:1px solid #ffffff13;
}
.gs-return{display:flex;align-items:center;gap:10px;background:transparent;border:1px solid #ffffff22;border-radius:12px;color:#eee7fa;padding:9px 13px}
.gs-return:hover{background:#ffffff12}
.gs-brand{font-size:15px;font-weight:650;letter-spacing:.2px}.gs-brand small{display:block;font-size:11px;color:#938ca5;font-weight:400;margin-top:4px}
.gs-tag{font-size:11px;color:#a59ab8;background:#ffffff0a;border:1px solid #ffffff14;border-radius:999px;padding:8px 10px}
.gs-shell{width:min(1140px,100%);display:grid;grid-template-columns:225px minmax(0,1fr);gap:28px;margin:0 auto;padding:38px 24px 84px;align-items:start}
.gs-nav{position:sticky;top:99px;display:flex;flex-direction:column;gap:6px;padding:8px;border:1px solid #ffffff0c;border-radius:17px;background:#12121b88}
.gs-tab{display:flex;align-items:center;gap:11px;border:1px solid transparent;border-radius:11px;background:transparent;color:#a9a3b6;padding:13px;text-align:left;font-size:13px}
.gs-tab:hover{background:#ffffff0b;color:#fff}.gs-tab.active{color:#f7f2ff;background:#6655a62b;border-color:#a894d126}
.gs-main{min-width:0;display:flex;flex-direction:column;gap:18px}
.gs-intro{padding:7px 0 12px}.gs-eyebrow{font-size:11px;color:#b9a4e6;letter-spacing:2px}
.gs-intro h1{font-size:32px;font-weight:600;letter-spacing:-.6px;margin:12px 0 8px}.gs-intro p{line-height:1.8;font-size:13px;color:#a5a0b3;max-width:670px}
.gs-panel[hidden]{display:none!important}.gs-panel{display:flex;flex-direction:column;gap:17px}
.gs-card{background:#14141d;border:1px solid #ffffff12;border-radius:18px;padding:23px 24px;box-shadow:0 10px 27px #00000016}
.gs-card h2{font-size:16px;font-weight:620;margin:0 0 7px}.gs-card p{font-size:12px;color:#a9a3b7;line-height:1.8;margin:0}
.gs-divider{height:1px;background:#ffffff12;margin:20px 0}
.gs-avatar-row{display:flex;align-items:center;gap:18px;margin-top:18px}
.gs-avatar-swatch{display:block;width:64px;height:64px;object-fit:cover;border-radius:15px;background:#303036}
.gs-avatar-caption{display:flex;flex-direction:column;gap:9px;min-width:0}
.gs-avatar-caption strong{font-size:13px}.gs-avatar-caption small{color:#a09bad;font-size:11px}
.gs-cta{display:inline-flex;align-items:center;justify-content:center;border:1px solid #9d88d04f;background:#8d6bc42c;border-radius:10px;padding:10px 14px;color:#efe8fc;font-size:12px;cursor:pointer}
.gs-cta:hover{background:#8d6bc446}.gs-cta-muted{background:#ffffff09;border-color:#ffffff23;color:#cfc8dd}.gs-actions{display:flex;gap:9px;flex-wrap:wrap;margin-top:17px}
.gs-formline{display:flex;flex-direction:column;gap:8px;margin-top:18px}.gs-formline label{font-size:12px;color:#bfb9cc}
.gs-input,.gs-select{width:100%;background:#0e0e15;color:#f4efff;border:1px solid #ffffff25;border-radius:11px;padding:12px 13px;max-width:450px;outline-offset:3px}
.gs-setting-row{display:flex;align-items:center;justify-content:space-between;gap:22px;padding:16px 0;border-bottom:1px solid #ffffff10}
.gs-setting-row:last-child{border:0;padding-bottom:0}.gs-setting-row h3{font-size:13px;font-weight:550;margin:0 0 5px}.gs-setting-row p{font-size:11px;color:#9790a6;margin:0;max-width:470px}
.gs-setting-row select{flex:none;max-width:190px;min-width:140px}
.gs-pill{color:#c9b6ef;background:#6d4f9830;border:1px solid #967bc24a;border-radius:999px;font-size:11px;padding:7px 11px;white-space:nowrap}
.gs-notice{background:#231d2d;border:1px solid #8365a03e;border-radius:13px;padding:13px 15px;font-size:12px;line-height:1.8;color:#bbb0d3}
.gs-inline-link{color:#cab7f7;text-decoration:underline;background:none;border:0;cursor:pointer}
.gs-save-status{min-height:20px;margin-top:9px;color:#bbd9c9;font-size:12px}
/* avatar crop: fixed-square field of view, square output, move/zoom the image */
.crop-overlay{position:fixed;inset:0;z-index:90;background:#000b;display:grid;place-items:center;padding:14px;overflow:auto}
.crop-dialog{width:min(620px,100%);background:#14131d;border:1px solid #9d83d54d;border-radius:22px;box-shadow:0 28px 85px #000b;padding:24px}
.crop-head{display:flex;align-items:start;justify-content:space-between;gap:20px;margin-bottom:16px}
.crop-head h2{font-size:18px;margin:0 0 7px}.crop-head p{font-size:12px;color:#a8a0b6;line-height:1.7;margin:0}
.crop-close{border:0;background:#ffffff10;color:#eee;flex:none;border-radius:9px;width:34px;height:34px;font-size:20px}
.crop-content{display:flex;gap:28px;align-items:center;justify-content:center;flex-wrap:wrap}
.crop-work{display:flex;flex-direction:column;align-items:center;gap:11px}
.crop-viewport{width:240px;height:240px;border-radius:20px;overflow:hidden;background:#272730;cursor:grab;touch-action:none;border:1px solid #ffffff38;box-shadow:0 0 0 5px #6d52b91a;position:relative}
.crop-viewport.dragging{cursor:grabbing}
.crop-viewport canvas{width:100%;height:100%;display:block;pointer-events:none}
.crop-viewport:after{content:'';position:absolute;inset:0;border:2px solid #b6a4e6a6;border-radius:19px;pointer-events:none}
.crop-preview-block{min-width:150px;display:flex;flex-direction:column;align-items:center;gap:10px;color:#c6bed1;font-size:12px}
.crop-preview{width:44px;height:44px;object-fit:cover;background:#272730;border-radius:11px}
.crop-controls{display:flex;flex-direction:column;gap:11px;margin-top:18px}.crop-controls label{font-size:12px;color:#c5bbd2}.crop-controls input[type=range]{width:100%;accent-color:#ac8de7}
.crop-actions{display:flex;gap:10px;justify-content:flex-end;margin-top:24px;flex-wrap:wrap}
.prototype-ai.pref-large-text .reply{font-size:17px}.prototype-ai.pref-large-text .bubble{font-size:15px}
.prototype-ai.pref-reduced-motion .orb-skin:before,.prototype-ai.pref-reduced-motion .orb-launcher.focusing .orb-skin{animation:none!important}
.prototype-ai.pref-reduced-motion .window,.prototype-ai.pref-reduced-motion .sidebar{transition:none!important}
@media(max-width:720px){
 .gs-shell{grid-template-columns:1fr;gap:16px;padding:20px 14px 60px}
 .gs-nav{position:static;display:flex;flex-direction:row;overflow-x:auto;border-radius:13px;gap:5px;white-space:nowrap}
 .gs-tab{padding:10px 12px;flex:none}
 .gs-main{gap:12px}.gs-card{padding:19px 17px}.gs-intro h1{font-size:26px}
 .gs-setting-row{align-items:flex-start;flex-direction:column;gap:10px}
 .gs-setting-row select{width:100%;max-width:100%}
 .gs-top{padding:12px 14px}.gs-tag{display:none}
 .crop-dialog{padding:16px}.crop-content{gap:18px}
}

/* V13.10 R4 · empty avatar placeholder / exactly 40 px slot / preserve transparency */
.account-avatar.avatar-slot,.profile-avatar-lg.avatar-slot,.gs-avatar-swatch.avatar-slot,.crop-preview.avatar-slot{
  width:40px!important;height:40px!important;border-radius:11px!important;flex:none!important;
  position:relative!important;display:grid!important;place-items:center!important;
  overflow:hidden!important;border:0!important;box-shadow:none!important;
  background:transparent!important;
}
.avatar-slot.is-empty{background:#28272d!important;color:#9c96ae}
.avatar-slot .avatar-placeholder{width:23px;height:23px;position:relative;z-index:0;display:none}
.avatar-slot.is-empty .avatar-placeholder{display:block}
.avatar-slot .user-avatar-img{width:100%!important;height:100%!important;position:absolute;inset:0;display:block;object-fit:contain!important;background:transparent!important;border-radius:inherit!important}
.avatar-slot img[hidden]{display:none!important}
.gs-avatar-row{align-items:center}
.gs-avatar-caption{gap:7px}
.gs-avatar-caption .gs-cta{align-self:flex-start}
.crop-preview.avatar-slot{margin:auto}
.crop-viewport{border-radius:15px;background:#191923;box-shadow:none}
.crop-viewport:after{display:none}
.crop-actions button:disabled{opacity:.4;cursor:not-allowed}

/* V13.10 R5 · remove subtitle and update composer controls */
.head-id > div{display:flex;align-items:center;min-width:0}
.head-id .head-sub{display:none!important}
.head-title{font-size:14px;display:flex;align-items:center;min-height:32px}
.compose-r5{align-items:center!important;gap:10px;padding:0 10px 0 8px;min-height:62px}
.compose-r5 textarea{min-height:56px!important;max-height:144px;padding:17px 8px 15px 2px!important;display:flex;align-items:center}
.compose-icon{flex:none;border:0;background:transparent;color:#ddd6ef;width:38px;height:38px;border-radius:999px;display:grid;place-items:center}
.compose-icon:hover{background:#ffffff10}
.compose-icon .ico{width:20px;height:20px}
.compose-icon.lead{color:#f3efff}
.compose-actions{margin-left:auto;display:flex;align-items:center;gap:4px;align-self:center}
.mode-chip{border:0;background:transparent;color:#f1edf8;font-size:13px;display:flex;align-items:center;gap:4px;padding:8px 10px;border-radius:999px;white-space:nowrap}
.mode-chip:hover{background:#ffffff0c}
.mode-chip .ico.tiny{width:14px;height:14px;stroke-width:2.1}
.send-r5{margin:0!important;width:40px!important;height:40px!important;border-radius:999px!important;background:#4f79ff!important;display:grid;place-items:center;box-shadow:none}
.send-r5:hover{background:#6287ff!important}
.send-r5 .ico{width:18px;height:18px;stroke-width:2.2}
.compose-footer{padding-inline:4px}
@media(max-width:620px){
  .compose-r5{min-height:58px;padding:0 8px 0 6px;gap:6px}
  .compose-r5 textarea{min-height:52px!important;padding:15px 4px 13px 0!important;font-size:13px}
  .compose-icon{width:34px;height:34px}
  .mode-chip{font-size:12px;padding:6px 6px}
  .send-r5{width:36px!important;height:36px!important}
}

/* V13.11 - mobile typing first, controls second; cinematic reading layout unchanged */
.mode-name-mobile{display:none}
.composer-wrap{position:relative}
.composer-shortcuts[hidden]{display:none!important}
.composer-shortcuts{position:absolute;left:0;right:0;bottom:calc(100% + 10px);z-index:9;border:1px solid #8675ae55;border-radius:18px;padding:13px;background:#1b1826ee;box-shadow:0 15px 42px #0009;display:flex;align-items:center;gap:8px;flex-wrap:wrap;backdrop-filter:blur(12px)}
.shortcut-label{color:#aa9cc8;font-size:11px;margin-right:8px}
.composer-shortcuts button{border:1px solid #78669666;background:#251f32;color:#e5dcfb;border-radius:999px;padding:8px 11px;font-size:12px}
.composer-shortcuts button:hover{background:#352a4c}
.compose-r5{align-items:center!important}
.compose-r5 textarea{align-self:center!important;box-sizing:border-box}
@media(max-width:620px){
  .mode-name{display:none!important}
  .mode-name-mobile{display:inline}
  .compose-r5{display:flex!important;flex-wrap:wrap!important;align-items:center!important;justify-content:space-between!important;row-gap:2px!important;column-gap:7px!important;min-height:96px!important;padding:8px 9px!important;border-radius:25px!important}
  .compose-r5 textarea{order:0!important;flex:0 0 100%!important;width:100%!important;min-width:0!important;max-height:130px!important;min-height:42px!important;height:auto;padding:11px 11px 7px!important;line-height:1.55!important;font-size:14px!important}
  .compose-r5 .compose-icon.lead{order:1;align-self:center!important}
  .compose-r5 .compose-actions{order:2;align-self:center!important;margin-left:auto!important;gap:5px!important}
  .compose-r5 .mode-chip{min-height:32px;font-size:12px;padding:6px 8px}
  .compose-r5 .send-r5{width:37px!important;height:37px!important}
  .composer-zone{padding-bottom:calc(10px + env(safe-area-inset-bottom))!important}
  .composer-shortcuts{padding:12px;gap:6px}
  .shortcut-label{flex:0 0 100%}
}
@media(max-width:350px){
  .compose-r5{padding-inline:7px!important}
  .compose-r5 .compose-icon{width:31px;height:31px}
  .compose-r5 .mode-chip{padding:5px}
}

/* V13.12: Readable AI replies, unobtrusive actions, A horizontal compact cards */
.reply{color:#ded9e9}
.reply .reply-lead{font-size:15px;font-weight:590;line-height:1.85;letter-spacing:.005em;color:#f0ecfa;margin:0 0 12px}
.reply .reply-body{font-size:14px;font-weight:400;line-height:1.95;color:#c6c0d2;margin:0 0 13px}
.response-actions{display:flex;align-items:center;justify-content:space-between;gap:12px;flex-wrap:wrap;padding-top:10px;margin:13px 0 4px;border-top:1px solid #ffffff0c}
.response-actions-buttons{display:flex;flex-wrap:wrap;gap:6px;align-items:center}
.response-action{display:inline-flex;align-items:center;justify-content:center;gap:6px;min-height:32px;padding:6px 9px;border:1px solid transparent;background:none;border-radius:9px;color:#9992a8;font-size:11px;transition:background .15s,color .15s}
.response-action .ico{width:14px;height:14px;stroke-width:1.7}
.response-action:hover{color:#e7dcff;background:#ffffff0b}
.response-action.active{background:#7962b627;border-color:#a58ad344;color:#d9c8ff}
.response-feedback-note{font-size:10px;color:#9990ae;white-space:nowrap}
.cards-head{margin-top:16px;margin-bottom:10px}
.cards-rail{gap:11px;scroll-padding:3px;padding-bottom:10px}
.movie-card{flex:0 0 min(349px,87vw);min-height:191px;padding:10px;border-radius:15px}
.poster{width:108px;max-height:169px}
.movie-title{font-size:13px}
.why{line-height:1.65;font-size:11px;margin:7px 0}
.trust-note{font-size:10px;margin:11px 0}
.compose-r5 .compose-actions{gap:6px}
@media(max-width:620px){
  .reply .reply-lead{font-size:14px;line-height:1.8}
  .reply .reply-body{font-size:13px;line-height:1.9}
  .response-action{padding:5px 7px;font-size:10px;min-height:31px}
  .response-action .ico{width:13px;height:13px}
  .movie-card{flex:0 0 min(302px,86vw);min-height:177px;gap:10px}
  .poster{width:91px;max-height:145px}
  .compose-r5 .compose-actions{gap:3px}
}

/* V13.12 R1 · centered FLIP scale transition for fullscreen; never fly from a corner */
.window{
  left:50%;top:50%;
  transform-origin:center center;
  transition:opacity .27s ease, transform .32s ease, visibility .32s, box-shadow .3s ease;
}
.window.show,
.window.full,
.window.full.show{
  left:50%;top:50%;
  transform:translate(-50%,-50%) scale(1);
  transform-origin:center center;
}
.window:not(.show){transform:translate(-50%,-48%) scale(.97)}
/* Remove old width/height tween: the entire panel is transformed from its center by WAAPI. */
.window.full-motion,
.window.full-motion.show{
  transition:opacity .27s ease, visibility .32s, box-shadow .3s ease;
}
@media (max-width:820px){
  .window,.window.show,.window.full,.window.full.show{
    left:50%;top:50%;
    transform:translate(-50%,-50%) scale(1);
  }
  .window:not(.show){transform:translate(-50%,-49%) scale(.99)}
}
@media (prefers-reduced-motion:reduce){
  .window.full-motion,.window.full-motion.show{transition:none!important}
}

/* V13.13 · reply state visuals; existing dark MovieInsight design retained */
.chat-main{position:relative}
.stream-label{display:inline-flex;align-items:center;gap:9px;font-size:12px;color:#b8a9d8;letter-spacing:.15px;margin:8px 0 13px}
.stream-label::before{content:"";width:7px;height:7px;border-radius:50%;background:#a98bff;box-shadow:0 0 0 4px #a98bff1a;animation:mi13Pulse 1.15s ease-in-out infinite}
.stream-label.is-error{color:#e0b4bd}.stream-label.is-error::before{background:#d58b9a;animation:none}
.stream-label.is-stopped{color:#aaa3b9}.stream-label.is-stopped::before{background:#8a829e;animation:none}
.stream-text{white-space:pre-wrap;font-size:15px;line-height:1.9;color:#e3ddec;overflow-wrap:anywhere;min-height:20px}
.stream-text.is-writing:after{content:"";display:inline-block;width:2px;height:1em;background:#bba1ff;vertical-align:-.13em;margin-left:3px;animation:mi13Caret .8s steps(1) infinite}
.stream-ops{display:flex;align-items:center;gap:9px;margin-top:15px;flex-wrap:wrap}
.stream-retry{color:#dfd1ff;background:#2b2439;border:1px solid #9178c658;border-radius:10px;padding:9px 12px;cursor:pointer;font-size:12px}
.stream-retry:hover{background:#3c2d54}
.stream-hint{color:#898295;font-size:11px}
.send-r5.is-stopping{background:#675486!important;box-shadow:0 0 0 3px #76619d22!important}
.send-r5.is-stopping:hover{background:#7c64a1!important}
.jump-latest{position:absolute;bottom:108px;left:50%;transform:translateX(-50%);z-index:3;display:flex;align-items:center;gap:7px;white-space:nowrap;border:1px solid #8974b659;background:#201a2aec;color:#e7dafa;border-radius:999px;padding:9px 13px;font-size:12px;box-shadow:0 5px 26px #0008;cursor:pointer}
.jump-latest:hover{background:#332441}
.jump-latest[hidden]{display:none!important}
@keyframes mi13Pulse{50%{opacity:.46;transform:scale(.85)}}
@keyframes mi13Caret{50%{opacity:0}}
@media(max-width:620px){.stream-text{font-size:14px}.jump-latest{bottom:126px}}
@media(prefers-reduced-motion:reduce){.stream-label:before,.stream-text:after{animation:none!important}}

/* MovieInsight V13.14 · Gemini-inspired, MovieInsight-colored two-level composer.
   A single reading surface for text, with anchored control row and NO microphone. */
.composer-zone{padding:6px clamp(15px,4vw,48px) 11px!important;background:transparent!important;border-top:0!important}
.composer-wrap{max-width:760px!important}
form[data-source-id="composerForm"].mi14-compose{
  display:flex!important;flex-direction:column!important;flex-wrap:nowrap!important;
  align-items:stretch!important;justify-content:initial!important;
  padding:10px 14px 10px 14px!important;
  width:100%!important;min-height:100px!important;
  row-gap:2px!important;column-gap:0!important;
  border-radius:28px!important;
  background:#202024!important;border:1px solid #ffffff10!important;
  box-shadow:0 10px 32px #0000003b!important;
  transition:background-color .18s ease,border-color .18s ease!important;
}
form[data-source-id="composerForm"].mi14-compose:focus-within{border-color:#a58cdc66!important;box-shadow:0 10px 32px #0000004a,0 0 0 1px #6545bd18!important}
form[data-source-id="composerForm"].mi14-compose textarea[data-source-id="messageInput"]{
  box-sizing:border-box!important;order:0!important;
  flex:0 0 auto!important;align-self:stretch!important;
  display:block!important;position:relative!important;
  width:100%!important;min-width:0!important;
  height:43px;min-height:43px!important;max-height:176px!important;
  margin:0!important;padding:10px 11px 6px!important;
  border:0!important;background:transparent!important;
  color:#f5f2fa!important;font-size:15px!important;
  line-height:1.55!important;resize:none!important;
  outline:0!important;overflow-y:hidden;
  scrollbar-width:thin;scrollbar-color:#6b6374 transparent;
}
form[data-source-id="composerForm"].mi14-compose textarea[data-source-id="messageInput"]::placeholder{color:#a6a1ab!important;opacity:1}
.mi14-controls{
  order:1!important;display:flex!important;align-items:center!important;
  justify-content:space-between!important;min-height:40px!important;
  gap:10px!important;padding:0 2px!important;
}
.mi14-controls .compose-icon.lead{
  flex:0 0 39px!important;width:39px!important;height:39px!important;
  padding:0!important;align-self:center!important;color:#f1eff6;
  display:grid!important;place-items:center!important;
}
.mi14-controls .compose-icon.lead .ico{width:22px;height:22px;stroke-width:2.3}
.mi14-controls .compose-actions{
  display:flex!important;align-items:center!important;justify-content:flex-end!important;
  margin:0 0 0 auto!important;align-self:center!important;
  gap:9px!important;min-height:40px;
}
.mi14-controls .mode-chip{
  display:flex!important;align-items:center!important;justify-content:center!important;
  min-height:36px!important;border-radius:18px!important;
  padding:6px 10px!important;color:#e3e0e9!important;font-size:13px!important;
}
.mi14-controls .mode-chip .ico.tiny{transform:none;width:15px;height:15px;opacity:.84}
.mi14-controls button[data-source-id="sendBtn"]{
  width:38px!important;height:38px!important;min-width:38px!important;
  margin:0!important;align-self:center!important;
  border-radius:999px!important;background:#3659bc!important;
  display:grid!important;place-items:center!important;
}
.mi14-controls button[data-source-id="sendBtn"]:hover{background:#4669d1!important}
.mi14-controls button[data-source-id="sendBtn"].is-stopping{background:#66517d!important}
.mi14-controls button[data-source-id="sendBtn"] .ico{width:18px!important;height:18px!important}
.composer-zone .compose-footer{margin:7px 5px 0!important;padding:0!important}
@media(max-width:620px){
  .composer-zone{padding:6px 12px calc(9px + env(safe-area-inset-bottom))!important}
  form[data-source-id="composerForm"].mi14-compose{
    min-height:97px!important;padding:8px 12px 8px!important;
    border-radius:26px!important;row-gap:0!important;
  }
  form[data-source-id="composerForm"].mi14-compose textarea[data-source-id="messageInput"]{
    width:100%!important;flex:0 0 auto!important;height:42px;
    min-height:42px!important;max-height:128px!important;
    font-size:14px!important;line-height:1.55!important;
    padding:9px 7px 4px!important;
  }
  .mi14-controls{min-height:38px!important;padding:0!important}
  .mi14-controls .compose-icon.lead{flex-basis:36px!important;width:36px!important;height:36px!important}
  .mi14-controls .compose-actions{min-height:38px;gap:7px!important}
  .mi14-controls .mode-chip{min-height:34px!important;padding:5px 8px!important;font-size:12px!important}
  .mi14-controls button[data-source-id="sendBtn"]{width:36px!important;height:36px!important;min-width:36px!important}
  .jump-latest{bottom:148px!important}
}
@media(max-width:350px){
  form[data-source-id="composerForm"].mi14-compose{padding-inline:9px!important}
  .mi14-controls .compose-actions{gap:4px!important}
  .mi14-controls .mode-chip{padding:4px 5px!important}
}
@media(prefers-reduced-motion:reduce){form[data-source-id="composerForm"].mi14-compose{transition:none!important}}

/* V13.15: one-line capsule, expand ONLY for actual multiline input. */
form[data-source-id="composerForm"].mi14-compose {
  display:grid!important;flex-direction:unset!important;
  grid-template-columns:38px minmax(0,1fr) auto!important;
  grid-template-rows:40px!important;
  grid-template-areas:"plus entry actions"!important;
  align-items:center!important;justify-items:stretch!important;
  column-gap:8px!important;row-gap:0!important;
  min-height:58px!important;
  padding:8px 14px!important;
  border-radius:999px!important;
  transition:border-radius .18s ease,background-color .18s ease,border-color .18s ease!important;
}
form[data-source-id="composerForm"].mi14-compose.mi14-expanded {
  grid-template-rows:auto 40px!important;
  grid-template-areas:"entry entry entry" "plus . actions"!important;
  row-gap:7px!important;
  padding:11px 14px 9px!important;
  border-radius:27px!important;
}
form[data-source-id="composerForm"].mi14-compose .mi14-controls {display:contents!important}
form[data-source-id="composerForm"].mi14-compose textarea[data-source-id="messageInput"] {
  grid-area:entry!important;
  box-sizing:border-box!important;
  align-self:center!important;
  min-width:0!important;width:100%!important;
  min-height:26px!important;
  max-height:162px!important;
  height:26px;
  padding:1px 3px!important;
  line-height:24px!important;
  font-size:14px!important;
  scrollbar-width:thin;
}
form[data-source-id="composerForm"].mi14-compose.mi14-expanded textarea[data-source-id="messageInput"]{
  align-self:stretch!important;
  min-height:48px!important;
  max-height:162px!important;
  padding:2px 6px 0!important;
}
form[data-source-id="composerForm"].mi14-compose .mi14-controls .compose-icon.lead{
  grid-area:plus!important;
  justify-self:center!important;
  align-self:center!important;
  width:38px!important;height:38px!important;
  flex-basis:auto!important;
}
form[data-source-id="composerForm"].mi14-compose .mi14-controls .compose-actions{
  grid-area:actions!important;
  justify-self:end!important;
  align-self:center!important;
  margin:0!important;
  display:flex!important;align-items:center!important;
  gap:7px!important;
}
form[data-source-id="composerForm"].mi14-compose .mi14-controls button[data-source-id="sendBtn"]{
  width:38px!important;height:38px!important;min-width:38px!important;
  margin:0!important;
}
@media(max-width:620px){
  form[data-source-id="composerForm"].mi14-compose{
    grid-template-columns:34px minmax(0,1fr) auto!important;
    grid-template-rows:38px!important;
    min-height:56px!important;
    padding:7px 11px!important;
    column-gap:6px!important;
    border-radius:999px!important;
  }
  form[data-source-id="composerForm"].mi14-compose.mi14-expanded{
    grid-template-rows:auto 38px!important;
    min-height:unset!important;
    padding:10px 11px 8px!important;
    row-gap:6px!important;
    border-radius:25px!important;
  }
  form[data-source-id="composerForm"].mi14-compose textarea[data-source-id="messageInput"]{
    min-height:26px!important;max-height:140px!important;
    padding:1px 2px!important;
    font-size:13px!important;line-height:24px!important;
  }
  form[data-source-id="composerForm"].mi14-compose.mi14-expanded textarea[data-source-id="messageInput"]{
    min-height:48px!important;max-height:140px!important;
    padding:2px 4px!important;
  }
  form[data-source-id="composerForm"].mi14-compose .mi14-controls .compose-icon.lead{
    width:34px!important;height:34px!important;
  }
  form[data-source-id="composerForm"].mi14-compose .mi14-controls button[data-source-id="sendBtn"]{
    width:36px!important;height:36px!important;min-width:36px!important;
  }
  form[data-source-id="composerForm"].mi14-compose .mi14-controls .compose-actions{gap:4px!important}
  .jump-latest{bottom:110px!important}
  .composer-zone:has(form.mi14-expanded) .jump-latest{bottom:180px!important}
}

/* V13.15 · conversation reading design: neutral, centered, unobstructed. */
.mi15-stylebar{flex:none;display:flex;align-items:center;justify-content:flex-end;gap:10px;max-width:100%;min-height:40px;padding:4px 22px 2px;background:transparent}
.mi15-stylebar-label{font-size:10px;color:#696473;white-space:nowrap}
.mi15-mode-tabs{display:flex;gap:3px;padding:3px;border:1px solid #ffffff0b;border-radius:999px;background:#ffffff06}
.mi15-mode{background:transparent;color:#8f899d;border:0;padding:5px 12px;font-size:11px;border-radius:999px;white-space:nowrap}
.mi15-mode:hover{color:#d7d0e4}
.mi15-mode.active{background:#353044;color:#f3eaff;box-shadow:inset 0 0 0 1px #b7a1ef26}
.thread{padding-top:20px!important}
.thread-inner{max-width:760px!important;gap:44px!important}
.thread .date-line{color:#716a78;font-size:11px;text-align:center;margin:2px 0 8px}
.msg.user{align-self:stretch!important;justify-content:flex-end;width:100%!important;max-width:none!important;min-width:0}
.msg.user .bubble{max-width:min(82%,560px);width:max-content;background:#202025!important;border:1px solid #ffffff06!important;color:#f4f2f7;border-radius:25px!important;padding:12px 18px!important;line-height:1.75;box-shadow:none!important;font-size:14px;white-space:pre-wrap;overflow-wrap:anywhere}
.msg.assistant{align-self:stretch;width:100%;max-width:100%;gap:0}
.msg.assistant .assistant-body{min-width:0;max-width:100%;flex:1}
.msg.assistant .reply{max-width:100%;font-size:15px;line-height:1.91;color:#e4e1e9;overflow-wrap:anywhere}
.msg.assistant .reply p{margin:0 0 17px}
.msg.assistant .reply-lead{font-weight:500;color:#f1edf5}
.msg.assistant .mi15-structured-list{margin:6px 0 20px;padding-left:2.15em;line-height:1.85}
.mi15-structured-list li{padding-left:5px;margin:10px 0}
.mi15-structured-list strong{font-weight:650;color:#f5f1fa}
.msg.assistant .reply-tail{margin-top:4px}
.msg.assistant .avatar{width:28px;height:28px;flex:none}
.msg.assistant .assistant-name{margin:0 0 14px}
.msg.assistant .assistant-name small{font-size:10px}
.msg.assistant .response-actions{margin-top:11px}
.msg.assistant .response-actions-buttons{display:flex;align-items:center;gap:4px;flex-wrap:wrap}
.msg.assistant .response-action{width:32px;height:32px;min-width:32px;display:inline-flex;align-items:center;justify-content:center;padding:0;border:0;border-radius:8px;background:transparent;color:#9c96a6;gap:0}
.msg.assistant .response-action:hover{background:#ffffff0b;color:#efebfa}
.msg.assistant .response-action.active{background:#6b569d2e;color:#ccbbf9}
.msg.assistant .response-action .ico{width:16px;height:16px;flex:none}
.msg.assistant .response-action span{display:none}
.msg.assistant .response-feedback-note{color:#857d93;font-size:10px;margin-left:5px}
.cards-head{margin-top:24px}
.cards-rail{margin-top:5px}
/* Default: Gemini-like clean plain-text turn with no repeated assistant logo/name. */
.window[data-dialog-mode="gemini"] .msg.assistant>.avatar,
.window[data-dialog-mode="gemini"] .msg.assistant .assistant-name{display:none!important}
.window[data-dialog-mode="gemini"] .msg.assistant .reply-lead{font-weight:450}
.window[data-dialog-mode="gemini"] .msg.assistant .reply{line-height:1.95}
.window[data-dialog-mode="gemini"] .msg.assistant .response-actions{margin-left:-4px}
.window[data-dialog-mode="gemini"] .msg.assistant .stream-label{margin-top:0}
/* Alternate: MovieInsight native mode restores only a small identity cue. */
.window[data-dialog-mode="native"] .msg.assistant{gap:12px}
.window[data-dialog-mode="native"] .msg.assistant .avatar{display:block;align-self:flex-start;margin-top:1px}
.window[data-dialog-mode="native"] .msg.assistant .assistant-name{display:flex;margin:4px 0 11px;color:#ded5f4}
.window[data-dialog-mode="native"] .msg.assistant .assistant-name small{color:#81788f}
.window[data-dialog-mode="native"] .msg.assistant .reply-lead{font-weight:600;color:#f6f0ff}
.window[data-dialog-mode="native"] .msg.assistant .response-actions{margin-top:14px}
.window[data-dialog-mode="native"] .msg.assistant .response-action{width:auto;min-width:32px;padding:0 7px;gap:5px}
.window[data-dialog-mode="native"] .msg.assistant .response-action span{display:inline;font-size:11px}
@media(max-width:620px){
 .mi15-stylebar{justify-content:space-between;padding:4px 12px 2px;min-height:38px;gap:5px}
 .mi15-stylebar-label{font-size:9px}
 .mi15-mode{font-size:10px;padding:5px 7px}
 .thread{padding-top:14px!important}
 .thread-inner{gap:29px!important}
 .msg.user .bubble{max-width:87%;font-size:13px;padding:10px 14px!important}
 .msg.assistant .reply{font-size:14px;line-height:1.85}
 .msg.assistant .mi15-structured-list{padding-left:1.85em}
 .window[data-dialog-mode="native"] .msg.assistant{gap:8px}
 .window[data-dialog-mode="native"] .msg.assistant .avatar{width:24px;height:24px}
 .window[data-dialog-mode="native"] .msg.assistant .response-action span{display:none}
}

.site-nav{display:none!important}.scope-tag{display:none!important}.prototype-ai{overflow-x:hidden!important}

/* The iframe is cropped to the card itself, so it cannot paint over the old page. */
.prototype-ai:not(.mi2-docked-ai) .window,.prototype-ai:not(.mi2-docked-ai) .window.show,
.prototype-ai:not(.mi2-docked-ai) .window.full{
 position:fixed!important;inset:0!important;left:0!important;top:0!important;
 width:100vw!important;height:100dvh!important;max-width:none!important;max-height:none!important;
 transform:none!important;opacity:1!important;visibility:visible!important;
 border-radius:24px!important;box-shadow:none!important;transition:none!important;
}
@media(max-width:700px){.prototype-ai:not(.mi2-docked-ai) .window,.prototype-ai:not(.mi2-docked-ai) .window.show{border-radius:16px!important}}

/* MovieInsight M9-P8.7 · OSS-inspired interaction refinements, no new visual theme */
.prototype-ai{--mi17-hairline:rgba(182,158,223,.13);--mi17-soft:rgba(179,140,239,.10);--mi17-focus:#c4a5fc}
.mi17-history-search{display:flex;align-items:center;gap:7px;border:1px solid rgba(178,157,218,.17);border-radius:11px;background:#1b1926;padding:0 10px;min-height:38px;flex:none}
.mi17-history-search svg{width:16px;height:16px;fill:none;stroke:#a59cb9;stroke-width:1.8}
.mi17-history-search input{min-width:0;flex:1;width:100%;border:0;background:none;outline:0;color:#f1eaff;font:inherit;font-size:12px;line-height:1.3;padding:10px 0}
.mi17-history-search input::placeholder{color:#81788e}
.mi17-history-search:focus-within{border-color:rgba(191,155,255,.7);box-shadow:0 0 0 2px rgba(185,143,245,.10)}
.mi17-history-note{font-size:10px;color:#938ba6;padding:0 4px;min-height:14px;line-height:1.4}
.session-list .mi17-session-row{display:flex;align-items:center;gap:3px;border:1px solid transparent;border-radius:11px;min-width:0;position:relative;background:transparent}
.session-list .mi17-session-row:hover{background:rgba(255,255,255,.035)}
.session-list .mi17-session-row.active{background:rgba(149,115,207,.12);border-color:rgba(177,146,225,.18)}
.session-list .mi17-session-row .session{padding:10px 7px 10px 9px;border:0!important;background:transparent!important;flex:1;min-width:0;display:flex;align-items:center;gap:6px;text-align:left;border-radius:8px}
.mi17-title{display:block;min-width:0;flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;font-size:12px}
.mi17-pin{font-size:11px;color:#c1a0fb;flex:none}
.mi17-session-menu-btn{width:28px;min-width:28px;height:30px;border:0;background:transparent;color:#938aa8;border-radius:8px;display:grid;place-items:center;font-size:17px}
.mi17-session-menu-btn:hover,.mi17-session-menu-btn[aria-expanded="true"]{background:rgba(194,162,250,.12);color:#f5eeff}
.mi17-session-pop{position:absolute;z-index:12;top:calc(100% + 3px);right:0;width:142px;border:1px solid rgba(179,152,216,.19);padding:5px;border-radius:11px;background:#282337;box-shadow:0 14px 32px #0008;display:flex;flex-direction:column;gap:2px}
.mi17-session-pop[hidden]{display:none!important}
.mi17-session-pop button{border:0;border-radius:8px;padding:9px 10px;background:transparent;text-align:left;color:#e4ddf0;font-size:11px;white-space:nowrap}
.mi17-session-pop button:hover{background:rgba(191,155,255,.12)}
.mi17-session-pop button.mi17-danger{color:#ffb8ca}
.mi17-rename{border:1px solid #a389ce;background:#171520;color:#f6eefe;border-radius:6px;min-width:0;width:100%;font-size:12px;padding:6px}
.mi17-history-empty{font-size:11px;text-align:center;color:#988dab;padding:16px 8px;line-height:1.7}
.mi17-user-wrap{display:flex;flex-direction:column;align-items:flex-end;gap:6px;min-width:0}
.mi17-user-actions{display:flex;justify-content:flex-end;gap:9px;align-items:center;opacity:0;transition:opacity .13s}
.msg.user:hover .mi17-user-actions,.msg.user:focus-within .mi17-user-actions{opacity:1}
.mi17-user-actions button{border:0;background:none;color:#9891a8;font-size:10px;padding:3px 5px;border-radius:6px}
.mi17-user-actions button:hover{color:#e2ceff;background:#252031}
.mi17-edit{background:#201d2c;border:1px solid #62517c;border-radius:12px;padding:10px;width:min(520px,100%);display:flex;flex-direction:column;gap:8px}
.mi17-edit textarea{color:#f4edf9;background:#12131b;border:1px solid #5b4d73;border-radius:8px;resize:vertical;padding:10px;font:13px/1.55 inherit;min-height:90px;max-height:230px}
.mi17-edit-controls{display:flex;align-items:center;justify-content:flex-end;gap:8px}
.mi17-edit-controls button{background:#322a48;border:1px solid #5c497d;border-radius:8px;padding:8px 11px;font-size:11px;color:#ede2fc}
.mi17-edit-controls button.mi17-save{background:#7453ad;color:white;border-color:#8b68c9}
.mi17-edit-tip{font-size:10px;color:#b7a9ce;line-height:1.5}
.movie-card .poster.mi17-poster-link{cursor:pointer;outline-offset:3px}
.movie-card .poster.mi17-poster-link:hover{filter:brightness(1.09)}
.movie-card .poster.mi17-poster-link:focus-visible{outline:2px solid #b895f0}
.mi17-toast{position:absolute;bottom:108px;left:50%;transform:translateX(-50%);z-index:30;background:#292238;border:1px solid #665083;box-shadow:0 14px 40px #0007;color:#ebe1fc;border-radius:999px;font-size:11px;padding:9px 14px;white-space:nowrap;pointer-events:none}
.mi17-toast[hidden]{display:none!important}
.mi2-docked-ai [data-source-id="fullToggle"]{display:none!important}
.mi2-docked-ai .window{animation:none!important}
@media(max-width:620px){.mi17-user-actions{opacity:1}.mi17-history-search{min-height:40px}.mi17-toast{bottom:98px;max-width:90vw;white-space:normal}.mi17-session-pop{right:3px}}
@media(prefers-reduced-motion:reduce){.mi17-user-actions,.movie-card .poster{transition:none!important}}


.mi28-shared-trigger{margin:6px 12px 8px;width:calc(100% - 24px);border:1px solid rgba(176,142,222,.2);background:#272338;color:#d6c7f1;text-align:left;padding:10px 12px;border-radius:10px;font-size:11px;cursor:pointer}
.mi28-shared-trigger:hover{background:#342b49}
.mi28-shared-tray[hidden]{display:none!important}
.mi28-shared-tray{margin:0 12px 12px;padding:11px;background:#141420;border-radius:11px;border:1px solid #40334e;max-height:260px;overflow:auto}
.mi28-shared-heading{color:#9d91b5;font-size:10px;letter-spacing:.02em;margin-bottom:9px}
.mi28-shared-tray p{font-size:11px;color:#a69bb5;margin:9px 0}
.mi28-shared-row{display:flex;flex-direction:column;gap:5px;width:100%;padding:10px;text-align:left;color:#e3daed;background:#1f1d2d;border:1px solid #302a40;border-radius:9px;margin-top:6px;cursor:pointer}
.mi28-shared-row:hover{background:#30283e}.mi28-shared-row strong{font-size:12px}.mi28-shared-row span{font-size:10px;color:#a69db8}
/* Never repurpose floating window's full-screen control to navigate to the AI Explore route. */
[data-source-id="fullToggle"]{display:none!important}


/* M9-P8.2 shared design tokens — cinema graphite / restrained violet */
.prototype-ai{--mi2-bg:#080a13;--mi2-stage:#0d0f1b;--mi2-panel:#141624;--mi2-panel-2:#1b1c2c;
 --mi2-ink:#f2eff9;--mi2-sub:#aaa4bc;--mi2-quiet:#817b96;--mi2-accent:#ad8bea;
 --mi2-accent-hover:#c3a6fa;--mi2-line:rgba(191,174,225,.13);--mi2-focus:#c2a6f4;
 --mi2-shadow:0 16px 50px rgba(2,3,12,.25)}
:where(button,input,select,textarea,[role="button"]):focus-visible{outline:2px solid var(--mi2-focus)!important;outline-offset:3px}

/* P8.2 cross-page color normalization, keeping each page's accepted layout */
.prototype-ai{--ink:#f2eff9;--text:#f2eff9;--muted:#aaa4bc;--line:rgba(191,174,225,.13);
 --border:rgba(191,174,225,.13);--purple:#ad8bea;--accent:#ad8bea;--lav:#c0a5f5}
.prototype-ai,.prototype-ai{scrollbar-color:#504467 #111321}
:where(button,input,select,textarea,[role="button"]):focus-visible{outline:2px solid #c2a6f4!important;outline-offset:3px}


/* The host owns the only floating launcher. Keep the V13.15 chat intact. */
#orbLauncher,.site,.site-nav,#siteNav{display:none!important}
.prototype-ai{background:transparent!important}


.prototype-ai.mi2-docked-ai{background:#080a13!important;overflow:hidden!important}
.prototype-ai.mi2-docked-ai .window,.prototype-ai.mi2-docked-ai .window.show,.prototype-ai.mi2-docked-ai .window.full{
 position:fixed!important;inset:0!important;left:0!important;top:0!important;width:100vw!important;height:100dvh!important;
 transform:none!important;opacity:1!important;visibility:visible!important;transition:none!important;animation:none!important;
 border:0!important;border-radius:0!important;box-shadow:none!important
}
.prototype-ai.mi2-docked-ai .backdrop,.prototype-ai.mi2-docked-ai #orbLauncher{display:none!important}
.prototype-ai.mi2-docked-ai *,.prototype-ai.mi2-docked-ai *:before,.prototype-ai.mi2-docked-ai *:after{
 animation-duration:0s!important;transition-duration:0s!important;scroll-behavior:auto!important
}


/* Floating-card mode: the host page must remain visible behind the V13 chat card. */
.prototype-ai:not(.mi2-docked-ai),.prototype-ai{background:transparent!important}
.prototype-ai:not(.mi2-docked-ai) .backdrop,
.prototype-ai:not(.mi2-docked-ai) #backdrop{display:none!important;background:transparent!important;backdrop-filter:none!important;opacity:0!important}
.prototype-ai:not(.mi2-docked-ai) .scope-tag{display:none!important}

.prototype-ai{position:relative;width:100%;height:100%;overflow:hidden;isolation:isolate}
.prototype-ai .window,.prototype-ai .window.show,.prototype-ai .window.full{position:absolute!important;inset:0!important;left:0!important;top:0!important;width:100%!important;height:100%!important;max-width:none!important;max-height:none!important;min-width:0!important;min-height:0!important;transform:none!important;opacity:1!important;visibility:visible!important;border-radius:0!important;box-shadow:none!important;transition:none!important}
.prototype-ai.surface-floating .window{background:transparent!important;border:0!important}
.prototype-ai .global-settings-page{position:absolute!important;inset:0!important;width:100%!important;height:100%!important;min-height:0!important;overflow:auto!important;z-index:40}
.prototype-ai .crop-overlay{position:absolute!important;inset:0!important}
.prototype-ai .mi15-stylebar{flex:none}.prototype-ai .mi17-history-search{width:calc(100% - 20px);margin:4px 10px 12px;background:#171420;color:#eee5fa;border:1px solid #ffffff20;border-radius:9px;padding:10px;font:inherit;font-size:12px}
.prototype-ai .reply-body{white-space:pre-wrap;overflow-wrap:anywhere}.prototype-ai .poster{cursor:pointer}.prototype-ai .poster-uploaded{opacity:1!important;position:absolute;inset:0;width:100%;height:100%;object-fit:cover}.prototype-ai .reply-status{color:#c9b5dd;font-size:11px;line-height:1.8}
.prototype-ai .settings-api label{display:block;margin:16px 0 8px;font-size:12px;color:#c9bfd5}.prototype-ai .settings-api :is(input:not([type=checkbox]),select){width:100%;max-width:450px;min-width:0;background:#0e0e15;border:1px solid #ffffff25;border-radius:11px;padding:12px;color:#f4efff;font:inherit}.prototype-ai .settings-api .check{display:flex;gap:10px;align-items:start;line-height:1.8}.prototype-ai .settings-api button{border:1px solid #9d88d04f;background:#8d6bc42c;border-radius:10px;padding:10px 14px;color:#efe8fc;font-size:12px}.prototype-ai .settings-api .assistant-controls{display:flex;gap:10px;flex-wrap:wrap;margin:15px 0}.prototype-ai .settings-api .assistant-note{font-size:11px;color:#b0a5c0;line-height:1.9}
.prototype-ai [hidden]{display:none!important}.prototype-ai button:disabled{opacity:.45;cursor:default}


.prototype-ai.mi2-docked-ai [data-source-id="chatWindow"].window.full.show{position:absolute!important;inset:0!important;width:100%!important;height:100%!important;left:0!important;top:0!important;transform:none!important}

.prototype-ai.surface-floating{background:transparent!important}.prototype-ai.surface-floating [data-source-id="chatWindow"].window.full.show{background:transparent!important}.prototype-ai.surface-floating .window-head{background:rgba(26,19,39,.65)!important}.prototype-ai.surface-floating .chat-main{background:transparent!important}.prototype-ai.surface-floating .sidebar{background:rgba(15,13,24,.55)!important}

.prototype-ai.surface-floating [data-source-id="fullToggle"]{display:grid!important}

/* M9 sidebar: ChatGPT-style conversation rail */
.window.sidebar-open .sidebar{width:264px;border-right:1px solid rgba(211,198,255,.09)}
.side-panel{position:relative;display:flex;flex-direction:column;width:100%;height:100%;padding:10px 8px 8px;gap:2px}
.side-top{display:flex;align-items:center;justify-content:space-between;padding:4px 8px 10px}
.side-label{font-size:11px;font-weight:650;letter-spacing:1.4px;color:#8f87a0}
.side-collapse{width:30px;height:30px;display:grid;place-items:center;border:0;border-radius:8px;background:transparent;color:#a99ec0;cursor:pointer}
.side-collapse:hover,.side-collapse:focus-visible{background:#ffffff0d;color:#efe7ff}
.side-action{display:flex;align-items:center;gap:9px;height:38px;padding:0 11px;border:1px solid rgba(190,160,240,.2);border-radius:9px;background:rgba(139,118,191,.16);color:#e9ddff;font:inherit;font-size:13px;font-weight:600;cursor:pointer;transition:background .16s,border-color .16s}
.side-action:hover,.side-action:focus-visible{background:rgba(139,118,191,.28);border-color:rgba(190,160,240,.36)}
.side-action .ico{width:16px;height:16px;color:#cbb4f7}
.side-search{display:flex;align-items:center;gap:8px;height:34px;margin:8px 2px 4px;padding:0 9px;border:1px solid rgba(178,157,218,.14);border-radius:8px;background:#16141f}
.side-search .ico{width:15px;height:15px;color:#a79cbb;flex:none}
.side-search input{flex:1;min-width:0;border:0;background:transparent;outline:0;color:#efe9fb;font:inherit;font-size:12.5px}
.side-search input::placeholder{color:#8f87a0}
.side-search:focus-within{border-color:rgba(191,155,255,.55);box-shadow:0 0 0 2px rgba(185,143,245,.10)}
.side-list{flex:1;min-height:0;overflow-y:auto;overflow-x:hidden;display:flex;flex-direction:column;gap:1px;padding:2px 0;scrollbar-width:thin;scrollbar-color:#463a58 transparent}
.side-row{position:relative;display:flex;align-items:center;border-radius:8px}
.side-row:hover{background:rgba(255,255,255,.045)}
.side-row.active{background:rgba(149,115,207,.16)}
.side-title{flex:1;min-width:0;height:36px;padding:0 10px;border:0;border-radius:8px;background:none;color:#c8c1d6;text-align:left;font:inherit;font-size:13px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;cursor:pointer}
.side-row.active .side-title{color:#f3ecff;font-weight:550}
.side-rename{flex:1;min-width:0;height:32px;margin:2px;padding:0 8px;border:1px solid rgba(194,162,250,.55);border-radius:7px;background:#1b1826;color:#f3ecff;font:inherit;font-size:13px;outline:0}
.side-more{width:28px;min-width:28px;height:28px;margin-right:4px;border:0;border-radius:7px;background:transparent;color:#9b92ad;opacity:0;cursor:pointer;transition:opacity .12s,background .12s}
.side-row:hover .side-more,.side-row.active .side-more,.side-more:focus-visible{opacity:1}
.side-more:hover,.side-more:focus-visible{background:rgba(194,162,250,.14);color:#f4ecff}
.side-pop{position:fixed;z-index:60;width:132px;display:flex;flex-direction:column;gap:1px;padding:4px;border:1px solid rgba(179,152,216,.18);border-radius:10px;background:#241f31;box-shadow:0 14px 32px #000a}
.side-pop button{border:0;border-radius:7px;padding:8px 9px;background:transparent;color:#e4ddf0;font:inherit;font-size:12px;text-align:left;cursor:pointer}
.side-pop button:hover,.side-pop button:focus-visible{background:rgba(191,155,255,.12)}
.side-pop .danger{color:#ffb3c7}
.side-empty{padding:18px 10px;margin:0;color:#8a8299;font-size:12px;text-align:center}
.side-import{padding:6px 2px 0}
.side-import-btn{display:flex;align-items:center;gap:8px;width:100%;height:32px;padding:0 9px;border:0;border-radius:8px;background:transparent;color:#a79cbb;font:inherit;font-size:12px;text-align:left;cursor:pointer}
.side-import-btn:hover,.side-import-btn:focus-visible{background:#ffffff0a;color:#e7dcff}
.side-import-btn .ico{width:15px;height:15px}
.side-import-tray{margin:4px 2px 0;padding:6px;border:1px solid rgba(179,152,216,.16);border-radius:10px;background:#16131f}
.side-import-hint{margin:0 4px 6px;color:#8f87a0;font-size:11px;line-height:1.5}
.side-import-row{display:flex;flex-direction:column;gap:2px;width:100%;padding:7px 8px;border:0;border-radius:7px;background:transparent;text-align:left;cursor:pointer}
.side-import-row:hover,.side-import-row:focus-visible{background:#ffffff0a}
.side-import-row strong{color:#e6dcf7;font-size:12px;font-weight:600;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.side-import-row span{color:#9189a2;font-size:11px}
.side-account{display:flex;align-items:center;gap:6px;margin-top:auto;padding:10px 4px 4px;border-top:1px solid rgba(211,198,255,.08)}
.side-account .account-identity{flex:1}
.side-account .account-avatar{width:30px!important;height:30px!important}
.side-account .account-avatar svg{width:24px;height:24px;transform:translateY(3px)}
.side-account .account-name{font-size:12.5px}
.side-account .account-settings{width:32px;height:32px}
.sidebar .side-title:focus-visible,.sidebar .side-more:focus-visible,.sidebar .side-action:focus-visible{outline:2px solid #c2a6f4;outline-offset:-2px}
@media (prefers-reduced-motion:reduce){.side-action,.side-more{transition:none}}@media (hover:none){.side-more{opacity:1}}

/* M9 layout pass: readable type, aligned rhythm, larger targets */
.window-body{font-size:15px}
.window-head{min-height:76px!important;padding:14px 24px!important;gap:14px!important;border-bottom:1px solid rgba(212,196,251,.11)!important}
.head-title{font-size:16px!important;font-weight:650!important}
.head-status{font-size:12.5px!important;padding:9px 13px!important;min-height:36px!important;border-radius:999px!important}
.head-ops .icon-btn{width:40px!important;height:40px!important}
.window.sidebar-open .sidebar{width:280px!important}
.side-panel{padding:12px 10px 10px!important}
.side-top{padding:4px 10px 12px!important}
.side-label{font-size:12px!important;letter-spacing:1.6px!important;color:#9a92a9!important}
.side-collapse{width:32px!important;height:32px!important}
.side-action{height:44px!important;font-size:14px!important;border-radius:10px!important}
.side-search{height:38px!important;margin:10px 2px 6px!important;padding:0 11px!important;border-radius:9px!important}
.side-search input{font-size:13.5px!important}
.side-title{height:40px!important;font-size:14px!important;padding:0 11px!important}
.side-more{width:30px!important;min-width:30px!important;height:30px!important;font-size:16px!important}
.side-pop{width:144px!important}
.side-pop button{font-size:13px!important;padding:9px 10px!important}
.side-account .account-avatar{width:34px!important;height:34px!important}
.side-account .account-name{font-size:13.5px!important}
.side-account .account-settings{width:36px!important;height:36px!important}
.thread{padding:28px clamp(24px,5vw,72px) 20px!important}
.thread-inner{max-width:860px!important;gap:30px!important;min-height:100%!important}
.empty-state{margin:auto!important;max-width:720px!important;text-align:center!important;padding:24px 0!important}
.empty-state .empty-orb{width:96px!important;height:96px!important;margin:0 auto 22px!important}
.empty-state h2{font-size:clamp(28px,3vw,36px)!important;margin:0 0 12px!important;letter-spacing:.2px!important}
.empty-state p{font-size:15.5px!important;line-height:1.85!important;color:#ada6ba!important;margin:0 auto!important;max-width:520px!important}
.empty-suggestions{display:flex!important;flex-wrap:wrap!important;justify-content:center!important;gap:10px!important;margin-top:24px!important}
.followup{min-height:40px!important;padding:0 16px!important;font-size:13.5px!important;border-radius:999px!important}
.followup:hover{background:#7a59c240!important}
.reply{font-size:15.5px!important;line-height:1.85!important}
.reply-body{font-size:inherit!important}
.bubble{font-size:15px!important;line-height:1.75!important}
.assistant-name{font-size:14px!important}
.assistant-name small{font-size:12px!important}
.cards-head{font-size:13.5px!important}
.cards-head span{font-size:12px!important}
.movie-title{font-size:14.5px!important}
.movie-meta{font-size:12.5px!important}
.why{font-size:12.5px!important;line-height:1.7!important}
.mini-btn{font-size:12.5px!important;min-height:32px!important;padding:6px 10px!important}
.trust-note{font-size:12px!important}
.response-action{font-size:12.5px!important;min-height:34px!important;padding:7px 10px!important}
.response-action .ico{width:15px!important;height:15px!important}
.response-feedback-note{font-size:11.5px!important}
.composer-zone{padding:10px clamp(20px,4vw,56px) 16px!important}
.composer-wrap{max-width:860px!important}

form[data-source-id="composerForm"].mi14-compose textarea[data-source-id="messageInput"]{font-size:16px!important;line-height:28px!important;padding:1px 5px!important;max-height:162px!important}
.mi14-controls .compose-icon.lead{flex:0 0 42px!important;width:42px!important;height:42px!important}
.mi14-controls .compose-icon.lead .ico{width:23px!important;height:23px!important}
.mi14-controls .compose-actions{gap:10px!important;min-height:44px!important}
.mi14-controls .mode-chip{min-height:40px!important;font-size:14px!important;padding:8px 12px!important}
.mi14-controls button[data-source-id="sendBtn"]{width:42px!important;height:42px!important;min-width:42px!important;background:#4f79ff!important}.mi14-controls button[data-source-id="sendBtn"]:hover{background:#6287ff!important}.mi14-controls button[data-source-id="sendBtn"]:disabled{background:#4a4359!important}
.mi14-controls button[data-source-id="sendBtn"] .ico{width:20px!important;height:20px!important}
.compose-footer{font-size:12px!important;color:#8f879e!important;padding:10px 8px 0!important}
.compose-footer .key{font-size:12px!important;color:#b8aec9!important}
.composer-shortcuts button{font-size:13px!important;min-height:36px!important;padding:8px 12px!important}
.mi17-session-pop,.mi17-history-search{font-size:13px!important}
.gs-setting-row h3{font-size:14px!important}
.gs-setting-row p{font-size:12.5px!important}
@media (max-width:820px){
  .window.sidebar-open .sidebar{width:min(300px,86vw)!important}
  .thread{padding:20px 18px 16px!important}
  .empty-state h2{font-size:26px!important}
  .empty-state p{font-size:14.5px!important}
  .followup{font-size:13px!important}
}
/* mode chip: optical alignment of the chevron with the label */
.mi14-controls .mode-chip{gap:7px!important}
.mi14-controls .mode-chip .mode-name{line-height:1!important;display:inline-flex;align-items:center;letter-spacing:.1px}
.mi14-controls .mode-chip .ico.tiny{width:15px!important;height:15px!important;position:relative;top:-1.5px;opacity:.88!important;stroke-width:1.9!important}
</style>