<script setup lang="ts">
import {ref,onMounted,onBeforeUnmount,onDeactivated,nextTick,computed,watch} from 'vue'
import {useAuthStore} from '../stores/auth'
import {useRoute,useRouter} from 'vue-router'
import {aiWorkspace} from '../services/aiWorkspace';import {isMotionReduced,registerOrbMotion} from '../services/orbMotion'
import {isAccountSettingsTab,accountSettingsTarget} from '../services/accountSettings'
import {CLEAR_ASSISTANT_DISPLAY} from '../services/assistantDisplay'
import {assistantRequest as request,assistantJson as jsonRequest,assistantConflictMessage} from '../services/assistantApi'
import {behaviorLockReason,behaviorReady,behaviorState,consumeInternalBehaviorEvent,readBehavior,setBehaviorOwner,toggleBehavior,type BehaviorKind} from '../services/movieBehavior'
import {personalRequest} from '../services/personalFeatures'
import {movieArtwork} from '../services/movieArtwork'
import {assistantMovieTarget} from '../services/movieNavigation'

type TurnState='streaming'|'done'|'interrupted'|'error'
type HistoryTurn={
 id?:string
 question:string
 answer:string
 result?:any
 feedback?:string|null
 state?:TurnState
 errorText?:string
 request_id?:string
}

const props=defineProps<{surface:'explore'|'floating'}>()
const workspaceOwner=aiWorkspace.owner
let disposed=false
function live(){return !disposed&&aiWorkspace.owner===workspaceOwner}
const thread=ref<HTMLElement>(),router=useRouter()
const message=ref(''),busy=ref(false),error=ref(''),notice=ref(''),result=ref<any>(null),conversation=ref<string|null>(null),status=ref<any>({configured:false,enabled:false}),draft=ref('')
const composerInput=ref<HTMLTextAreaElement>(),expanded=ref(false)
const isNearBottom=ref(true)
let lastScrollTop=0,programmaticScrollUntil=0

function resizeComposer(){const el=composerInput.value;if(!el)return;el.style.setProperty('height','auto','important');const natural=el.scrollHeight,cap=162;const h=expanded.value?Math.max(48,Math.min(natural,cap)):30;el.style.setProperty('height',h+'px','important');el.style.setProperty('overflow-y',expanded.value&&natural>cap?'auto':'hidden','important')}
function autoGrow(){const el=composerInput.value;if(!el)return;el.style.setProperty('height','auto','important');el.style.setProperty('min-height','0px','important');const natural=el.scrollHeight;el.style.removeProperty('min-height');const must=el.value.length>0&&(el.value.includes('\n')||natural>34);if(must!==expanded.value){expanded.value=must;nextTick(resizeComposer)}else resizeComposer()}
function focusComposer(){nextTick(()=>{autoGrow();composerInput.value?.focus()})}
function fillComposer(text:string,nextNotice?:string){
 message.value=text
 shortcutsOpen.value=false
 if(nextNotice!==undefined)notice.value=nextNotice
 focusComposer()
}
watch(message,()=>nextTick(autoGrow))

const history=ref<HistoryTurn[]>([])
let controller:AbortController|null=null,feedbackController:AbortController|null=null,pending:any=null,activeTurn:HistoryTurn|null=null
type StreamDonePayload={turn:HistoryTurn,doneResult:any,fullAnswer:string,conversationId:string|null}
let streamTargetText='',streamCodepointOffsets:number[]=[0],streamDisplayedCount=0,streamCarry=0,streamLastTs=0,streamRafId=0,streamBurstRate=90,streamDoneStartTs=0,streamLastScrollHeight=0,statusRefreshedForSend=false
let streamTargetTurn:HistoryTurn|null=null,streamDonePayload:StreamDonePayload|null=null
const isActivelyTyping=ref(false)

function buildCodepointOffsets(text:string){
 const offsets=[0]
 let pos=0
 for(const ch of text){pos+=ch.length;offsets.push(pos)}
 return offsets
}
function stopStreamRaf(){
 if(streamRafId&&typeof cancelAnimationFrame==='function')cancelAnimationFrame(streamRafId)
 streamRafId=0
 streamLastTs=0
}
function resetStreamBuffer(){
 stopStreamRaf()
 streamTargetText=''
 streamCodepointOffsets=[0]
 streamDisplayedCount=0
 streamCarry=0
 streamBurstRate=90
 streamDoneStartTs=0
 streamTargetTurn=null
 streamDonePayload=null
 isActivelyTyping.value=false
}
function syncCatchupScroll(){
 nextTick(()=>{
  if(!isNearBottom.value)return
  const el=thread.value
  if(!el)return
  if(el.scrollHeight!==streamLastScrollHeight){
   streamLastScrollHeight=el.scrollHeight
   el.scrollTop=el.scrollHeight
   lastScrollTop=el.scrollTop
  }
 })
}
function updateStreamTarget(text:string,turn:HistoryTurn){
 streamTargetTurn=turn
 if(text===streamTargetText)return
 if(streamTargetText&&text.startsWith(streamTargetText)){
  const suffix=text.slice(streamTargetText.length)
  let pos=streamCodepointOffsets[streamCodepointOffsets.length-1]||0
  for(const ch of suffix){pos+=ch.length;streamCodepointOffsets.push(pos)}
  streamTargetText=text
 }else{
  streamTargetText=text
  streamCodepointOffsets=buildCodepointOffsets(text)
  const targetLen=streamCodepointOffsets.length-1
  streamDisplayedCount=Math.min(streamDisplayedCount,targetLen)
  const clamped=text.slice(0,streamCodepointOffsets[streamDisplayedCount]||0)
  turn.answer=clamped
  draft.value=clamped
 }
}
function finalizeDoneTurn(){
 const payload=streamDonePayload
 if(!payload){resetStreamBuffer();return}
 stopStreamRaf()
 isActivelyTyping.value=false
 streamDonePayload=null
 const{turn,doneResult,fullAnswer,conversationId}=payload
 turn.answer=fullAnswer
 turn.result=doneResult
 turn.state='done'
 turn.errorText=''
 if(doneResult?.request_id)turn.request_id=doneResult.request_id
 result.value=doneResult
 if(conversationId)conversation.value=conversationId
 history.value=history.value.slice(-12)
 activeTurn=null
 draft.value=''
 resetStreamBuffer()
 busy.value=false
 if(!statusRefreshedForSend){statusRefreshedForSend=true;void refreshStatus()}
 if(isNearBottom.value)scrollThreadToBottom(true)
}
function flushOrStopStreamCatchup(mode:'flush'|'stop'){
 if(mode==='flush'&&streamDonePayload){
  finalizeDoneTurn()
  return
 }
 resetStreamBuffer()
}
function stepStreamCatchup(ts:number){
 streamRafId=0
 if(!live()||!streamTargetTurn){resetStreamBuffer();return}
 const dtMs=streamLastTs>0?Math.max(0,Math.min(ts-streamLastTs,50)):16.67
 streamLastTs=ts
 const targetLen=streamCodepointOffsets.length-1
 const rem=targetLen-streamDisplayedCount
 if(rem<=0){
  isActivelyTyping.value=false
  if(streamDonePayload)finalizeDoneTurn()
  return
 }
 let step=0
 if(streamDonePayload){
  if(streamDoneStartTs<=0)streamDoneStartTs=ts
  if(ts-streamDoneStartTs>=245){
   step=rem
  }else{
   const rate=Math.max(streamBurstRate,90)
   streamCarry+=(rate*dtMs)/1000
   step=Math.floor(streamCarry)
   if(step<1&&dtMs>=12){step=1;streamCarry=Math.max(0,streamCarry-1)}
   else streamCarry-=step
   step=Math.min(rem,step)
  }
 }else{
  const rate=48+rem*4.5
  streamCarry+=(rate*dtMs)/1000
  step=Math.floor(streamCarry)
  if(step<1&&dtMs>=12){step=1;streamCarry=Math.max(0,streamCarry-1)}
  else streamCarry-=step
  step=Math.min(rem,step)
 }
 if(step>0){
  streamDisplayedCount+=step
  const rendered=streamTargetText.slice(0,streamCodepointOffsets[streamDisplayedCount]||0)
  streamTargetTurn.answer=rendered
  draft.value=rendered
  isActivelyTyping.value=true
  if(isNearBottom.value)syncCatchupScroll()
 }
 if(streamDisplayedCount>=targetLen){
  isActivelyTyping.value=false
  if(streamDonePayload)finalizeDoneTurn()
  return
 }
 streamRafId=requestAnimationFrame(stepStreamCatchup)
}
function startStreamRaf(){
 if(streamRafId||typeof requestAnimationFrame!=='function')return
 streamLastTs=0
 streamRafId=requestAnimationFrame(stepStreamCatchup)
}

function makeTurnId(seed?:string){
 if(seed)return seed
 if(typeof crypto!=='undefined'&&typeof crypto.randomUUID==='function')return crypto.randomUUID()
 return 'turn-'+Date.now()+'-'+Math.random().toString(36).slice(2,8)
}
function normalizeTurn(raw:any,index=0):HistoryTurn{
 const reqId=typeof raw?.request_id==='string'&&raw.request_id
  ?raw.request_id
  :typeof raw?.result?.request_id==='string'&&raw.result.request_id
   ?raw.result.request_id
   :undefined
 const id=typeof raw?.id==='string'&&raw.id?raw.id:makeTurnId(reqId||`legacy-${index}-${Date.now()}`)
 let state:TurnState='done'
 if(raw?.state==='streaming'||raw?.state==='interrupted')state='interrupted'
 else if(raw?.state==='error')state='error'
 else if(raw?.state==='done')state='done'
 const errorText=typeof raw?.errorText==='string'
  ?raw.errorText
  :state==='interrupted'
   ?'生成已中断，未自动重试。可点击重发或填回编辑。'
   :''
 return {
  id,
  question:typeof raw?.question==='string'?raw.question:'',
  answer:typeof raw?.answer==='string'?raw.answer:'',
  result:raw?.result??null,
  feedback:raw?.feedback??null,
  state,
  errorText,
  ...(reqId?{request_id:reqId}:{})
 }
}
function normalizeHistory(list:any):HistoryTurn[]{
 return Array.isArray(list)?list.map((item,idx)=>normalizeTurn(item,idx)):[]
}
function resolveLiveTurn(target?:HistoryTurn|null):HistoryTurn|null{
 if(target){
  const found=history.value.find(t=>t===target||(target.id&&t.id===target.id))
  if(found)return found
 }
 if(activeTurn){
  const found=history.value.find(t=>t===activeTurn||(activeTurn?.id&&t.id===activeTurn.id))
  if(found)return found
 }
 const reqId=pending?.request_id
 if(reqId){
  const byReq=history.value.find(t=>t.request_id===reqId||t.id===reqId)
  if(byReq)return byReq
 }
 return null
}
function updateNearBottom(){
 const el=thread.value
 if(!el){isNearBottom.value=true;return}
 const gap=el.scrollHeight-el.scrollTop-el.clientHeight
 isNearBottom.value=gap<=96
 lastScrollTop=el.scrollTop
}
function onThreadScroll(){
 const el=thread.value
 if(!el)return
 const gap=el.scrollHeight-el.scrollTop-el.clientHeight
 if(gap<=96){
  isNearBottom.value=true
  programmaticScrollUntil=0
  lastScrollTop=el.scrollTop
  return
 }
 if(el.scrollTop<lastScrollTop-4){
  programmaticScrollUntil=0
  isNearBottom.value=false
  lastScrollTop=el.scrollTop
  return
 }
 if(Date.now()>programmaticScrollUntil)isNearBottom.value=false
 lastScrollTop=el.scrollTop
}
async function refreshStatus(){try{const data=await jsonRequest('status');if(live())status.value=data}catch(e){if(live())error.value=(e as Error).message}}
function scrollThreadToBottom(smooth=true){
 isNearBottom.value=true
 nextTick(()=>{
  const el=thread.value
  if(!el)return
  const useSmooth=smooth&&!isMotionReduced()
  programmaticScrollUntil=useSmooth?Date.now()+480:0
  if(typeof el.scrollTo==='function')el.scrollTo({top:el.scrollHeight,behavior:useSmooth?'smooth':'auto'})
  else el.scrollTop=el.scrollHeight
  lastScrollTop=el.scrollTop
 })
}
function turnFaultText(turn:HistoryTurn){
 if(turn.errorText)return turn.errorText
 if(turn.result?.answer)return turn.result.answer
 return turn.state==='interrupted'
  ?'已中断生成，未自动重试可能计费的调用。'
  :'请求未完成，未自动重试。可点击重发或填回编辑。'
}
function consume(event:string,data:any){
 const turn=resolveLiveTurn()
 if(event==='draft'){
  const text=data.text||''
  if(!turn){draft.value=text;return}
  if(isMotionReduced()||typeof requestAnimationFrame!=='function'){
   stopStreamRaf()
   isActivelyTyping.value=false
   updateStreamTarget(text,turn)
   streamDisplayedCount=Math.max(0,streamCodepointOffsets.length-1)
   turn.answer=text
   draft.value=text
   if(isNearBottom.value)scrollThreadToBottom(false)
   return
  }
  updateStreamTarget(text,turn)
  const targetLen=streamCodepointOffsets.length-1
  if(streamDisplayedCount<targetLen)startStreamRaf()
  return
 }
 if(event==='result'){
  const resolvedReqId=data.request_id||pending?.request_id||turn?.request_id
  if(data.state==='DONE'){
   const fullAnswer=typeof data.answer==='string'?data.answer:''
   const pendingQuestion=pending?.message
   pending=null
   const doneResult={...data,request_id:resolvedReqId}
   result.value=doneResult
   conversation.value=data.conversation_id||conversation.value
   if(!turn||isMotionReduced()||typeof requestAnimationFrame!=='function'){
    stopStreamRaf()
    isActivelyTyping.value=false
    streamDonePayload=null
    if(turn){
     turn.answer=fullAnswer
     turn.result=doneResult
     turn.state='done'
     turn.errorText=''
     if(resolvedReqId)turn.request_id=resolvedReqId
    }else{
     history.value.push({
      id:makeTurnId(resolvedReqId),
      question:pendingQuestion||'AI 深度解释',
      answer:fullAnswer,
      result:doneResult,
      state:'done',
      errorText:'',
      ...(resolvedReqId?{request_id:resolvedReqId}:{})
     })
    }
    history.value=history.value.slice(-12)
    activeTurn=null
    draft.value=''
    resetStreamBuffer()
    busy.value=false
    if(!statusRefreshedForSend){statusRefreshedForSend=true;void refreshStatus()}
    if(isNearBottom.value)scrollThreadToBottom(true)
    return
   }
   updateStreamTarget(fullAnswer,turn)
   const targetLen=streamCodepointOffsets.length-1
   const rem=targetLen-streamDisplayedCount
   if(rem<=0){
    stopStreamRaf()
    isActivelyTyping.value=false
    streamDonePayload=null
    turn.answer=fullAnswer
    turn.result=doneResult
    turn.state='done'
    turn.errorText=''
    if(resolvedReqId)turn.request_id=resolvedReqId
    history.value=history.value.slice(-12)
    activeTurn=null
    draft.value=''
    resetStreamBuffer()
    busy.value=false
    if(!statusRefreshedForSend){statusRefreshedForSend=true;void refreshStatus()}
    if(isNearBottom.value)scrollThreadToBottom(true)
    return
   }
   streamBurstRate=Math.max(90,Math.ceil(rem/0.22))
   streamDoneStartTs=0
   streamDonePayload={
    turn,
    doneResult,
    fullAnswer,
    conversationId:data.conversation_id||null
   }
   startStreamRaf()
   return
  }else{
   flushOrStopStreamCatchup('stop')
   const interruptedAnswer=data.state==='CANCELLED'?'已取消显示。已发送的模型请求仍可能计费。':'请求未完成，未自动重试可能计费的调用。'
   const errMsg=assistantConflictMessage(data.error_code||'REQUEST_UNAVAILABLE',data.state)
   const interruptedResult={...data,request_id:resolvedReqId,source:'INTERRUPTED',answer:interruptedAnswer}
   result.value=interruptedResult
   if(turn){
    turn.state=data.state==='CANCELLED'?'interrupted':'error'
    turn.result=interruptedResult
    turn.errorText=errMsg
    if(resolvedReqId)turn.request_id=resolvedReqId
   }else{
    error.value=errMsg
   }
   activeTurn=null
  }
  draft.value=''
  if(isNearBottom.value)scrollThreadToBottom(true)
 }
}
class ReplyError extends Error{constructor(message:string){super(message);this.name='ReplyError'}}
async function send(path:string,body:any,targetTurn?:HistoryTurn){
 if(busy.value)return
 resetStreamBuffer()
 statusRefreshedForSend=false
 streamLastScrollHeight=thread.value?.scrollHeight??0
 busy.value=true;error.value='';notice.value='';draft.value=''
 const sendingController=new AbortController();controller=sendingController;pending={...body,path}
 activeTurn=targetTurn??resolveLiveTurn()
 const current=()=>live()&&controller===sendingController
 try{
  const r=await request(path,'POST',{...body,stream:true},sendingController.signal)
  if(!current())return
  if(!r.ok){let b:any=null;try{b=await r.json()}catch{b=null};throw new ReplyError(assistantConflictMessage(b?.code,typeof b?.data?.state==='string'?b.data.state:undefined))}
  if(!r.body)throw new Error('浏览器无法读取流式响应。')
  const reader=r.body.getReader(),decoder=new TextDecoder();let buffer='';const dispatchFrame=(frame:string)=>{if(!frame.trim())return;let event='';const dataLines:string[]=[];for(const rawLine of frame.split('\n')){const line=rawLine.replace(/\r$/,'');if(line.startsWith('event:'))event=line.slice(6).trim();else if(line.startsWith('data:'))dataLines.push(line.slice(5).replace(/^ /,''))}if(!event||!dataLines.length)return;let parsed:any;try{parsed=JSON.parse(dataLines.join('\n'))}catch{return};consume(event,parsed)}
  while(true){const {value,done}=await reader.read();if(!current()){await reader.cancel();return}buffer=(buffer+decoder.decode(value||new Uint8Array(),{stream:!done})).replace(/\r\n/g,'\n');let boundary:number
   while((boundary=buffer.indexOf('\n\n'))>=0){const frame=buffer.slice(0,boundary);buffer=buffer.slice(boundary+2);dispatchFrame(frame)}
   if(done){if(buffer.trim())dispatchFrame(buffer);break}
  }
  if(current()){
   if(!streamDonePayload){
    const unfinished=resolveLiveTurn(targetTurn)
    if(unfinished&&unfinished.state==='streaming'){
     flushOrStopStreamCatchup('stop')
     const endMsg='连接已结束但未收到完整结果，未自动重试。可手动重发。'
     unfinished.state='interrupted'
     unfinished.errorText=endMsg
     unfinished.result={source:'INTERRUPTED',request_id:body.request_id,answer:endMsg}
     result.value=unfinished.result
     if(isNearBottom.value)scrollThreadToBottom(true)
    }
    activeTurn=null
   }
  }
 }catch(e){
  if(!current())return
  if(streamDonePayload)return
  flushOrStopStreamCatchup('stop')
  const liveTurn=resolveLiveTurn(targetTurn)
  if((e as Error).name==='AbortError'){
   if(liveTurn&&liveTurn.state==='streaming'){
    const abortMsg=error.value||'已取消生成，未自动重发此请求。'
    if(error.value)error.value=''
    liveTurn.state='interrupted'
    liveTurn.errorText=abortMsg
    liveTurn.result=liveTurn.result||{source:'INTERRUPTED',request_id:body.request_id,answer:abortMsg}
    result.value=liveTurn.result
    if(isNearBottom.value)scrollThreadToBottom(true)
   }
  }else{
   const errMsg=e instanceof ReplyError?e.message:'连接中断，未自动重发。可重试原请求以读取结果，不会静默再次调用。'
   const errResult={source:'INTERRUPTED',request_id:body.request_id,answer:errMsg}
   result.value=errResult
   if(liveTurn){
    liveTurn.state='error'
    liveTurn.errorText=errMsg
    liveTurn.result=errResult
   }else{
    error.value=errMsg
   }
   if(isNearBottom.value)scrollThreadToBottom(true)
  }
  activeTurn=null
 }finally{
  if(current()){
   controller=null
   if(!streamDonePayload){
    busy.value=false
    draft.value=''
    if(!statusRefreshedForSend){statusRefreshedForSend=true;await refreshStatus()}
   }
  }
 }
}
async function submit(){
 const text=message.value.trim()
 if(!text||busy.value)return
 const same=pending?.path==='query'&&pending.message===text&&pending.conversation_id===conversation.value
 const requestId=same?pending.request_id:makeTurnId()
 const body={message:text,conversation_id:conversation.value,request_id:requestId}
 message.value=''
 shortcutsOpen.value=false
 nextTick(autoGrow)

 const tail=history.value[history.value.length-1]
 const retryingTail=Boolean(tail&&(tail.state==='interrupted'||tail.state==='error')&&tail.question.trim()===text)
 let targetTurn:HistoryTurn
 if(retryingTail&&tail){
  tail.id=tail.id||requestId
  tail.question=text
  tail.answer=''
  tail.result=null
  tail.state='streaming'
  tail.errorText=''
  tail.request_id=requestId
  targetTurn=tail
 }else{
  history.value.push({id:requestId,question:text,answer:'',result:null,state:'streaming',errorText:'',request_id:requestId})
  targetTurn=history.value[history.value.length-1]!
 }
 scrollThreadToBottom(true)
 await send('query',body,targetTurn)
}
async function retryTurn(turn:HistoryTurn){
 if(busy.value)return
 const text=(turn.question||'').trim()
 if(!text)return
 const same=pending?.path==='query'&&pending.message===text&&pending.conversation_id===conversation.value
 const requestId=same?pending.request_id:makeTurnId()
 const body={message:text,conversation_id:conversation.value,request_id:requestId}
 shortcutsOpen.value=false
 turn.id=turn.id||requestId
 turn.question=text
 turn.answer=''
 turn.result=null
 turn.state='streaming'
 turn.errorText=''
 turn.request_id=requestId
 const liveTurn=resolveLiveTurn(turn)||turn
 if(liveTurn===history.value[history.value.length-1])scrollThreadToBottom(true)
 await send('query',body,liveTurn)
}
function onComposerEnter(e:KeyboardEvent){
 if(e.isComposing)return
 e.preventDefault()
 if(!busy.value)void submit()
}
async function cancel(){
 if(streamDonePayload){
  flushOrStopStreamCatchup('flush')
  return
 }
 if(!pending)return
 const currentTurn=resolveLiveTurn()
 try{
  const id=pending.request_id
  const data=await jsonRequest('cancel','POST',{request_id:id})
  if(!data.cancel_requested&&data.state==='DONE'){
   const completed=await jsonRequest('requests/'+id)
   consume('result',{...completed,state:'DONE'})
   notice.value='请求已经完成，未执行取消。'
  }else{
   flushOrStopStreamCatchup('stop')
   const cancelMsg='已请求取消。已发送的调用可能计费，最终用量可能暂时未知。'
   const cancelResult={source:'INTERRUPTED',request_id:id,answer:cancelMsg,cost:data.cost}
   result.value=cancelResult
   if(currentTurn){
    currentTurn.state='interrupted'
    currentTurn.result=cancelResult
    currentTurn.errorText=cancelMsg
    currentTurn.request_id=id
   }
   activeTurn=null
   notice.value='不会自动重发此请求。'
   if(isNearBottom.value)scrollThreadToBottom(true)
  }
 }catch(e){error.value=(e as Error).message}
 finally{
  if(!streamDonePayload){
   controller?.abort()
   draft.value=''
  }
 }
}
function reset(){newSession()}
function clearSensitive(){profileOpen.value=false;shortcutsOpen.value=false}
defineExpose({clearSensitive,busy});let stopHeadOrb:(()=>void)|undefined,stopEmptyOrb:(()=>void)|undefined
onDeactivated(clearSensitive)
function sharedStatus(){refreshStatus()}
function closeTransient(e:Event){
 if(e.type==='pointerdown'){
  const el=e.target as HTMLElement|null
  if(!el?.closest?.('.side-list'))sessionMenu.value=''
  if(!el?.closest?.('.composer-wrap'))shortcutsOpen.value=false
  if(!el?.closest?.('.profile-panel')&&!el?.closest?.('[data-source-id="profileEntry"]'))profileOpen.value=false
  return
 }
 if((e as KeyboardEvent).key==='Escape'){sessionMenu.value='';editingId.value='';shortcutsOpen.value=false;profileOpen.value=false}
}
onMounted(()=>{refreshStatus();syncBehavior();window.addEventListener('mi2-behavior-changed',sharedBehavior);window.addEventListener('mi2-model-status',sharedStatus);window.addEventListener(CLEAR_ASSISTANT_DISPLAY,clearRequestedDisplay);window.addEventListener('focus',sharedStatus);document.addEventListener('pointerdown',closeTransient);document.addEventListener('keydown',closeTransient);stopHeadOrb=registerOrbMotion(()=>headOrbEl.value,{range:1.4,blink:true,busy});stopEmptyOrb=registerOrbMotion(()=>emptyOrbEl.value,{range:2.8,blink:true,busy})})
onBeforeUnmount(()=>{
 stopHeadOrb?.();stopEmptyOrb?.();
 if(streamDonePayload)flushOrStopStreamCatchup('flush')
 else flushOrStopStreamCatchup('stop')
 for(const t of history.value){
  if(t.state==='streaming'){
   t.state='interrupted'
   if(!t.errorText)t.errorText='窗口已关闭或切换，生成中断，未自动重试。'
  }
 }
 saveSession();disposed=true;controller?.abort();feedbackController?.abort();feedbackController=null;
 window.removeEventListener('mi2-behavior-changed',sharedBehavior);window.removeEventListener('mi2-model-status',sharedStatus);window.removeEventListener(CLEAR_ASSISTANT_DISPLAY,clearRequestedDisplay);window.removeEventListener('focus',sharedStatus);document.removeEventListener('pointerdown',closeTransient);document.removeEventListener('keydown',closeTransient)
})
watch(()=>aiWorkspace.reduced,(reduced)=>{
 if(!reduced)return
 if(streamDonePayload){
  flushOrStopStreamCatchup('flush')
  return
 }
 if(streamTargetTurn&&streamTargetTurn.state==='streaming'){
  stopStreamRaf()
  isActivelyTyping.value=false
  if(streamTargetText){
   streamTargetTurn.answer=streamTargetText
   draft.value=streamTargetText
   streamDisplayedCount=Math.max(0,streamCodepointOffsets.length-1)
   if(isNearBottom.value)scrollThreadToBottom(false)
  }
 }
})

function movieUrl(id:number){return assistantMovieTarget(id,props.surface,settingsRoute.fullPath)}
async function copyAnswer(text:string){try{await navigator.clipboard.writeText(text);notice.value='已复制回答'}catch{error.value='无法访问剪贴板，请手动复制。'}}


const emit=defineEmits<{(e:'close'):void,(e:'toggle-full'):void}>()
const auth=useAuthStore(),rootElement=ref<HTMLElement>(),headOrbEl=ref<HTMLElement>(),emptyOrbEl=ref<HTMLElement>(),sidebarOpen=ref(true),dialogMode=computed(()=>aiWorkspace.dialogMode),profileOpen=ref(false),shortcutsOpen=ref(false),sessionSearch=ref(''),sessionMenu=ref(''),sharedOpen=ref(false),editingId=ref(''),editTitle=ref('')
const showScrollLatest=computed(()=>history.value.length>0&&!isNearBottom.value&&!shortcutsOpen.value)
const uid=(id:string)=>props.surface+'-'+id
type SavedSession={id:string,title:string,history:HistoryTurn[],conversation:string|null,message:string,result:any,pending:any}
const sessions=ref<SavedSession[]>([]),activeSession=ref('')
const filteredSessions=computed(()=>sessions.value.filter(s=>s.title.toLowerCase().includes(sessionSearch.value.toLowerCase())))
const otherSessions=computed(()=>aiWorkspace.archives[props.surface==='explore'?'floating':'explore'].filter(s=>s.history?.length))
const clone=<T,>(value:T):T=>JSON.parse(JSON.stringify(value))
function saveSession(){if(!live())return;const s=sessions.value.find(x=>x.id===activeSession.value);if(!s)return;s.history=clone(history.value);s.conversation=conversation.value;s.message=message.value;s.result=clone(result.value);s.pending=pending?clone(pending):null;if(s.history.length&&s.title==='新对话')s.title=s.history[0]?.question?.trim()?.slice(0,24)||'新对话';aiWorkspace.archives[props.surface]=clone(sessions.value);aiWorkspace.activeSessions[props.surface]=s.id}
function newSession(){if(busy.value)return;flushOrStopStreamCatchup('flush');feedbackController?.abort();feedbackController=null;saveSession();const s:SavedSession={id:makeTurnId(),title:'新对话',history:[],conversation:null,message:'',result:null,pending:null};sessions.value.unshift(s);activeSession.value=s.id;history.value=[];conversation.value=null;message.value='';result.value=null;pending=null;draft.value='';error.value='';notice.value='';sessionMenu.value='';shortcutsOpen.value=false;saveSession();nextTick(()=>{autoGrow();isNearBottom.value=true})}
function chooseSession(id:string){if(busy.value)return;flushOrStopStreamCatchup('flush');saveSession();const s=sessions.value.find(x=>x.id===id);if(!s)return;activeSession.value=id;history.value=normalizeHistory(clone(s.history));hydrateFeedback();conversation.value=s.conversation;message.value=s.message;result.value=clone(s.result);pending=s.pending?clone(s.pending):null;draft.value='';error.value='';notice.value='';sessionMenu.value='';shortcutsOpen.value=false;nextTick(()=>{autoGrow();updateNearBottom()})}
const menuPos=ref({top:0,left:0})
function toggleMenu(id:string,e:MouseEvent){if(sessionMenu.value===id){sessionMenu.value='';return}const r=(e.currentTarget as HTMLElement).getBoundingClientRect(),w=132,h=76,gap=4;let top=r.bottom+gap;if(top+h>window.innerHeight-8)top=Math.max(8,r.top-h-gap);let left=Math.min(r.right-w,window.innerWidth-w-8);menuPos.value={top,left};sessionMenu.value=id}
async function startRename(s:SavedSession){sessionMenu.value='';editingId.value=s.id;editTitle.value=s.title;await nextTick();const el=rootElement.value?.querySelector<HTMLInputElement>('[data-edit="'+s.id+'"]');el?.focus();el?.select()}
function commitRename(s:SavedSession){const title=editTitle.value.trim();if(title&&title!==s.title){s.title=title.slice(0,80);saveSession()}editingId.value=''}
function cancelRename(){editingId.value=''}
function deleteSession(id:string){if(busy.value)return;flushOrStopStreamCatchup('flush');sessions.value=sessions.value.filter(s=>s.id!==id);if(activeSession.value===id){activeSession.value='';newSession()}saveSession();notice.value='已移除此窗口的显示记录，未删除服务端数据。'}
function clearSessions(){if(busy.value)return;flushOrStopStreamCatchup('flush');sessions.value=[];activeSession.value='';newSession();notice.value='已清空此窗口显示记录，未删除服务端数据。'}
function importSession(other:SavedSession){if(busy.value)return;newSession();const s=sessions.value[0]!;s.title='导入 · '+other.title;const imported=normalizeHistory(clone(other.history));s.history=imported;history.value=clone(imported);hydrateFeedback();conversation.value=null;pending=null;saveSession();sharedOpen.value=false;notice.value='已复制记录；后续提问使用独立的新模型上下文，不修改另一窗口。';nextTick(updateNearBottom)}
watch([history,conversation,message,result],saveSession,{deep:true})
const settingsRoute=useRoute()
async function openSettings(tab='ai'){
 if(!isAccountSettingsTab(tab))return
 profileOpen.value=false
 await router.push(accountSettingsTarget(tab,settingsRoute.fullPath))
 if(props.surface==='floating')emit('close')
}
function clearRequestedDisplay(e:Event){
 const detail=(e as CustomEvent).detail
 if(detail?.surface!==props.surface||detail.owner!==aiWorkspace.owner)return
 if(busy.value){detail.busy=true;return}
 clearSessions()
}
const feedbackBusy=ref<Record<string,boolean>>({})
function hydrateFeedback(){feedbackController?.abort();const fc=new AbortController();feedbackController=fc;for(const turn of history.value){if(turn.state&&turn.state!=='done')continue;const rid=turn.result?.request_id;if(rid)personalRequest('assistant-feedback?request_id='+encodeURIComponent(rid),undefined,fc.signal).then(r=>{if(live()&&feedbackController===fc&&!fc.signal.aborted)turn.feedback=r.reaction}).catch(()=>{})}}
async function feedback(turn:any,value:string){const rid=turn.result?.request_id;if(!rid||feedbackBusy.value[rid])return;feedbackBusy.value[rid]=true;try{const r=await personalRequest('assistant-feedback',{request_id:turn.result.request_id,reaction:turn.feedback===value?null:value});turn.feedback=r.reaction;notice.value='反馈已保存到当前账户，不会改动电影评分或清单。'}catch(e){notice.value=(e as Error).message}finally{delete feedbackBusy.value[rid]}}
function followup(title:string){fillComposer('我想进一步了解《'+title+'》，适合什么心情观看？')}
async function toggleMovieBehavior(mid:number,kind:BehaviorKind){if(busy.value)return;const owner=aiWorkspace.owner;notice.value='';error.value='';const r=await toggleBehavior(Number(mid),kind);if(!live()||owner!==aiWorkspace.owner)return;if(r.ok)notice.value=r.message;else error.value=r.message}
function movieState(m:any){return behaviorState(Number(m.movie_id))}
function behaviorLocked(m:any){return busy.value||!!behaviorLockReason(Number(m.movie_id))}
function behaviorHint(m:any,kind:BehaviorKind){const blocked=behaviorLockReason(Number(m.movie_id));if(blocked)return blocked;const on=kind==='want'?movieState(m).want:movieState(m).favorite;return (on?'撤销':'加入')+(kind==='want'?'想看':'收藏')}
function syncBehavior(){const owner=aiWorkspace.owner;if(!owner)return;setBehaviorOwner(owner);void readBehavior(owner)}
function sharedBehavior(){if(consumeInternalBehaviorEvent())return;const owner=aiWorkspace.owner;if(!owner)return;setBehaviorOwner(owner);void readBehavior(owner,{force:true})}
function sourceLabel(r:any){return r?.source==='LLM'?'AI 生成回答':r?.source==='CACHE'?'AI 缓存回答':r?.source==='MOCK'?'模拟测试':'本地检索 / 降级'}
function modelLink(url:string){return /^https:\/\/www\.themoviedb\.org\/movie\/\d+(?:[/?#-]|$)/.test(url)}
onMounted(()=>{
 sessions.value=clone(aiWorkspace.archives[props.surface]).map((s:SavedSession)=>({...s,history:normalizeHistory(s.history)}))
 const selected=aiWorkspace.activeSessions[props.surface]
 const s=sessions.value.find(x=>x.id===selected)||sessions.value[0]
 if(s)chooseSession(s.id)
 else newSession()
})

</script>
<template><div ref="rootElement" class="prototype-ai mi2-docked-ai" :class="['surface-'+surface,{'pref-large-text':aiWorkspace.large,'pref-reduced-motion':aiWorkspace.reduced}]"><svg style="display:none" xmlns="http://www.w3.org/2000/svg"><symbol :id="uid('i-menu')" data-source-id="i-menu" viewBox="0 0 24 24"><path d="M4 7h16M4 12h16M4 17h16"></path></symbol><symbol :id="uid('i-plus')" data-source-id="i-plus" viewBox="0 0 24 24"><path d="M12 5v14M5 12h14"></path></symbol><symbol :id="uid('i-max')" data-source-id="i-max" viewBox="0 0 24 24"><path d="M8 3H4a1 1 0 0 0-1 1v4m13-5h4a1 1 0 0 1 1 1v4M3 16v4a1 1 0 0 0 1 1h4m13-5v4a1 1 0 0 1-1 1h-4"></path></symbol><symbol :id="uid('i-chevron-down')" data-source-id="i-chevron-down" viewBox="0 0 24 24"><path d="m6 9 6 6 6-6"></path></symbol><symbol :id="uid('i-min')" data-source-id="i-min" viewBox="0 0 24 24"><path d="M4 9h5V4m11 11h-5v5M9 9 4 4m11 11 5 5"></path></symbol><symbol :id="uid('i-close')" data-source-id="i-close" viewBox="0 0 24 24"><path d="M5 5l14 14M19 5 5 19"></path></symbol><symbol :id="uid('i-send')" data-source-id="i-send" viewBox="0 0 24 24"><path d="m5 12 14-7-5 14-2-7-7 0z"></path></symbol><symbol :id="uid('i-settings')" data-source-id="i-settings" viewBox="0 0 24 24"><circle cx="12" cy="12" r="3"></circle><path d="M12 2v3m0 14v3M2 12h3m14 0h3M5 5l2 2m10 10 2 2M19 5l-2 2M7 17l-2 2"></path></symbol><symbol :id="uid('i-copy')" data-source-id="i-copy" viewBox="0 0 24 24"><rect height="12" rx="2" width="12" x="8" y="8"></rect><path d="M16 8V6a2 2 0 0 0-2-2H6a2 2 0 0 0-2 2v8a2 2 0 0 0 2 2h2"></path></symbol><symbol :id="uid('i-refresh')" data-source-id="i-refresh" viewBox="0 0 24 24"><path d="M20 7v5h-5M4 17v-5h5"></path><path d="M5.9 9a7 7 0 0 1 11.9-2l2.2 2M4 15l2.2 2a7 7 0 0 0 11.9-2"></path></symbol><symbol :id="uid('i-thumb-up')" data-source-id="i-thumb-up" viewBox="0 0 24 24"><path d="M7 10v11H4a2 2 0 0 1-2-2v-7a2 2 0 0 1 2-2h3Zm0 0 4.5-8a3 3 0 0 1 3 3v4h5a2.5 2.5 0 0 1 2.4 3.2l-2 7a2.5 2.5 0 0 1-2.4 1.8H7"></path></symbol><symbol :id="uid('i-thumb-down')" data-source-id="i-thumb-down" viewBox="0 0 24 24"><path d="M7 14V3H4a2 2 0 0 0-2 2v7a2 2 0 0 0 2 2h3Zm0 0 4.5 8a3 3 0 0 0 3-3v-4h5a2.5 2.5 0 0 0 2.4-3.2l-2-7A2.5 2.5 0 0 0 17.5 3H7"></path></symbol><symbol :id="uid('i-arrow-up')" data-source-id="i-arrow-up" viewBox="0 0 24 24"><path d="M12 19V5"></path><path d="m6 11 6-6 6 6"></path></symbol><symbol :id="uid('i-moon')" data-source-id="i-moon" viewBox="0 0 24 24"><path d="M21 13A9 9 0 0 1 11 3a9 9 0 1 0 10 10"></path></symbol><symbol :id="uid('i-search')" data-source-id="i-search" viewBox="0 0 24 24"><circle cx="11" cy="11" r="6"></circle><path d="m16 16 4 4"></path></symbol><symbol :id="uid('i-side')" data-source-id="i-side" viewBox="0 0 24 24"><path d="m14 6-6 6 6 6"></path></symbol><symbol :id="uid('i-import')" data-source-id="i-import" viewBox="0 0 24 24"><path d="M12 4v10m0 0 4-4m-4 4-4-4"></path><path d="M5 19h14"></path></symbol></svg>
<section :data-dialog-mode="dialogMode" aria-label="MovieInsight AI 电影探索" class="window show full" :class="{'sidebar-open':sidebarOpen}" :id="uid('chatWindow')" data-source-id="chatWindow" role="dialog">
<header class="window-head"><button v-if="surface==='floating'||surface==='explore'||!sidebarOpen" @click="sidebarOpen=!sidebarOpen" aria-label="切换会话侧栏" class="icon-btn" :id="uid('sidebarToggle')" data-source-id="sidebarToggle" title="展开会话侧栏"><svg class="ico"><use :href="'#'+uid('i-menu')"></use></svg></button><div class="head-id"><span ref="headOrbEl" class="orb-skin mini mark" :class="{'is-thinking':busy}" data-orb=""><img alt="" draggable="false" src="/m9/assistant-orb.png"/><i class="eye left"></i><i class="eye right"></i></span><div><div class="head-title">MovieInsight AI</div></div></div><div class="head-fill"></div><button @click="openSettings('ai')" aria-label="查看 BYOK 连接状态" class="head-status" :id="uid('connectionStatus')" data-source-id="connectionStatus" title="个人模型连接状态"><span class="dot"></span>{{status.cloud_available?status.model+" 已连接":"BYOK 未连接 · 本地兜底"}}</button><div class="head-ops"><button v-if="!sidebarOpen" @click="reset" aria-label="新建对话" class="icon-btn" :id="uid('newTop')" data-source-id="newTop" title="新建对话"><svg class="ico"><use :href="'#'+uid('i-plus')"></use></svg></button><span class="divider-v"></span><button @click="emit('toggle-full')" aria-label="切换全屏" class="icon-btn" :id="uid('fullToggle')" data-source-id="fullToggle" title="切换全屏" v-if="surface==='floating'"><svg class="ico" :id="uid('fullSvg')" data-source-id="fullSvg"><use :href="'#'+uid('i-max')"></use></svg></button><button @click="emit('close')" aria-label="收起窗口" class="icon-btn" :id="uid('closeChat')" data-source-id="closeChat" title="收起窗口" v-if="surface==='floating'"><svg class="ico"><use :href="'#'+uid('i-close')"></use></svg></button></div></header>
<div class="window-body"><aside :class="{'closed':!sidebarOpen}" aria-label="会话历史" class="sidebar" :id="uid('sidebar')" data-source-id="sidebar"><div class="side-panel"><div class="side-top"><span class="side-label">对话</span><button @click="sidebarOpen=false" aria-label="收起会话侧栏" class="side-collapse" type="button"><svg class="ico"><use :href="'#'+uid('i-side')"></use></svg></button></div><button @click="reset" class="side-action" :id="uid('newSide')" data-source-id="newSide" type="button"><svg class="ico"><use :href="'#'+uid('i-plus')"></use></svg><span>新对话</span></button><label class="side-search"><svg class="ico"><use :href="'#'+uid('i-search')"></use></svg><input v-model="sessionSearch" aria-label="搜索对话" placeholder="搜索对话" type="search"/></label><div class="side-list" :id="uid('sessionList')" data-source-id="sessionList" @scroll="sessionMenu=''"><div :class="{active:s.id===activeSession}" :key="s.id" class="side-row" v-for="(s,index) in filteredSessions"><input v-if="editingId===s.id" :data-edit="s.id" v-model="editTitle" aria-label="重命名对话" class="side-rename" @blur="commitRename(s)" @keydown.enter.prevent="commitRename(s)" @keydown.esc.prevent="cancelRename"/><button v-else :disabled="busy" :title="s.title" @click="chooseSession(s.id)" class="side-title" type="button">{{s.title}}</button><button :aria-expanded="sessionMenu===s.id" :aria-label="'管理对话：'+s.title" @click="toggleMenu(s.id,$event)" class="side-more" type="button">⋯</button><div class="side-pop" v-if="sessionMenu===s.id" :style="{top:menuPos.top+'px',left:menuPos.left+'px'}"><button @click="startRename(s)" type="button">重命名</button><button class="danger" @click="deleteSession(s.id)" type="button">删除对话</button></div></div><p v-if="!filteredSessions.length" class="side-empty">没有匹配的对话</p></div><div class="side-import" v-if="otherSessions.length"><button @click="sharedOpen=!sharedOpen" class="side-import-btn" type="button"><svg class="ico"><use :href="'#'+uid('i-import')"></use></svg><span>导入另一窗口的对话</span></button><div class="side-import-tray" v-if="sharedOpen"><p class="side-import-hint">导入为独立记录，不改动原窗口</p><button :disabled="busy" :key="s.id" @click="importSession(s)" class="side-import-row" v-for="s in otherSessions" type="button"><strong>{{s.title}}</strong><span>{{s.history.length}} 条回答</span></button></div></div><section :hidden="!profileOpen" aria-label="个人信息预览" class="profile-panel" :id="uid('profilePanel')" data-source-id="profilePanel">
<div class="profile-top">
<span aria-hidden="true" class="profile-avatar-lg avatar-slot is-empty"><svg aria-hidden="true" class="avatar-placeholder" fill="none" stroke="currentColor" stroke-linecap="round" stroke-width="1.55" v-if="!aiWorkspace.avatar" viewBox="0 0 24 24"><circle cx="12" cy="8.2" r="3.45"></circle><path d="M5.5 19.1c.6-3.45 2.88-5.1 6.5-5.1s5.9 1.65 6.5 5.1"></path></svg><img :src="aiWorkspace.avatar" alt="" class="user-avatar-img" v-if="aiWorkspace.avatar"/></span>
<div class="profile-meta"><strong>{{auth.user?.username}}</strong><small>个人信息 · 可在全局设置中修改</small></div>
<button @click="profileOpen=false" aria-label="关闭个人信息" class="profile-close" :id="uid('profileClose')" data-source-id="profileClose" type="button">×</button>
</div>
<div class="profile-detail">评分、收藏与想看由当前登录账户独立保存。</div>
<button @click="openSettings('account')" class="profile-edit-link" :id="uid('openAccountSettings')" data-source-id="openAccountSettings" type="button">编辑头像与个人资料</button><div class="profile-note">头像按账户保存；账户名称由身份系统管理。</div>
</section><div class="side-account"><button @click="profileOpen=!profileOpen" aria-label="打开个人信息" class="account-identity" :id="uid('profileEntry')" data-source-id="profileEntry" title="个人信息" type="button"><span aria-hidden="true" class="account-avatar avatar-slot is-empty"><svg aria-hidden="true" class="avatar-placeholder" fill="none" stroke="currentColor" stroke-linecap="round" stroke-width="1.55" v-if="!aiWorkspace.avatar" viewBox="0 0 24 24"><circle cx="12" cy="8.2" r="3.45"></circle><path d="M5.5 19.1c.6-3.45 2.88-5.1 6.5-5.1s5.9 1.65 6.5 5.1"></path></svg><img :src="aiWorkspace.avatar" alt="" class="user-avatar-img" v-if="aiWorkspace.avatar"/></span><span class="account-name">{{auth.user?.username}}</span></button><button @click="openSettings('account')" aria-label="打开全局设置" class="account-settings" :id="uid('accountSettingsEntry')" data-source-id="accountSettingsEntry" title="全局设置" type="button"><svg aria-hidden="true" fill="none" height="20" stroke="currentColor" stroke-linecap="round" stroke-linejoin="round" stroke-width="1.7" viewBox="0 0 24 24" width="20"><circle cx="12" cy="12" r="3.2"></circle><path d="M19.4 13.3a7.5 7.5 0 0 0 0-2.6l2-1.5-2-3.4-2.4.9a7.6 7.6 0 0 0-2.2-1.3L14.4 3H9.6l-.4 2.4A7.6 7.6 0 0 0 7 6.7l-2.4-.9-2 3.4 2 1.5a7.5 7.5 0 0 0 0 2.6l-2 1.5 2 3.4 2.4-.9a7.6 7.6 0 0 0 2.2 1.3l.4 2.4h4.8l.4-2.4a7.6 7.6 0 0 0 2.2-1.3l2.4.9 2-3.4-2-1.5Z"></path></svg></button></div></div></aside><main class="chat-main"><div aria-live="polite" aria-relevant="additions" class="thread" :id="uid('thread')" data-source-id="thread" ref="thread" @scroll="onThreadScroll"><div class="thread-inner" :id="uid('threadInner')" data-source-id="threadInner"><div v-if="!history.length" class="empty-state"><div class="empty-orb"><span ref="emptyOrbEl" class="orb-skin" data-orb=""><img src="/m9/assistant-orb.png" alt="" draggable="false"/><i class="eye left"></i><i class="eye right"></i></span></div><h2>今晚，我们聊什么电影？</h2><p>说出你的心情、偏好或最近喜欢的电影。也可以直接向我提问。</p><div class="empty-suggestions"><button class="followup" type="button" @click="fillComposer('今晚看什么电影？')">帮我选今晚的电影</button><button class="followup" type="button" @click="fillComposer('想看烧脑一点的科幻电影')">想看烧脑科幻</button><button class="followup" type="button" @click="fillComposer('推荐一部温暖、有希望的电影')">想看温暖的故事</button></div></div>
<div v-if="history.length" class="date-line">当前会话</div>
<template v-for="(turn,i) in history" :key="turn.id||turn.result?.request_id||i"><div class="msg user"><div class="bubble">{{turn.question}}</div><div class="mi17-user-actions"><button class="response-action" type="button" @click="copyAnswer(turn.question)">复制</button><button class="response-action" type="button" aria-label="重新提问" :disabled="busy||turn.state==='streaming'" @click="fillComposer(turn.question)">编辑</button></div></div><div class="msg assistant" :class="'turn-'+(turn.state||'done')"><div class="avatar"><span class="orb-skin mini" :class="{'is-thinking':turn.state==='streaming'}" data-orb=""><img src="/m9/assistant-orb.png" alt="" draggable="false"/><i class="eye left"></i><i class="eye right"></i></span></div><div class="assistant-body"><div class="assistant-name" :class="{'is-thinking':turn.state==='streaming'}"><span>MovieInsight AI</span><small v-if="turn.state==='streaming'">{{turn.answer?'正在生成回答':'正在理解与查询'}}</small><small v-else-if="turn.state==='interrupted'">已中断</small><small v-else-if="turn.state==='error'">请求未完成</small><small v-else>{{sourceLabel(turn.result)}}</small></div>
<template v-if="turn.state==='streaming'">
<div class="thinking-cue" aria-live="polite"><i class="thinking-dot" aria-hidden="true"></i><span>{{turn.answer?'正在生成回答':'正在理解与查询'}}</span></div>
<div v-if="turn.answer" class="reply"><p class="reply-body is-writing" :class="{'is-actively-typing':isActivelyTyping}">{{turn.answer}}</p></div>
<div v-else class="typing-dots" aria-hidden="true"><i></i><i></i><i></i></div>
<small class="reply-status">生成草稿尚未完成事实核验。</small>
</template>
<template v-else-if="turn.state==='interrupted'||turn.state==='error'">
<div v-if="turn.answer" class="reply"><p class="reply-body">{{turn.answer}}</p></div>
<div class="turn-fault" :class="'is-'+turn.state" :role="turn.state==='error'?'alert':'status'">
<p class="turn-fault-text">{{turnFaultText(turn)}}</p>
<p v-if="turn.result?.answer&&turn.result.answer!==turnFaultText(turn)" class="turn-fault-note">{{turn.result.answer}}</p>
<div class="turn-fault-actions"><button class="turn-fault-btn is-primary" type="button" :disabled="busy" @click="retryTurn(turn)"><svg class="ico"><use :href="'#'+uid('i-refresh')"/></svg><span>重发</span></button><button class="turn-fault-btn" type="button" :disabled="busy" @click="fillComposer(turn.question)"><span>填回编辑</span></button></div>
</div>
</template>
<template v-else>
<div class="reply"><p class="reply-body">{{turn.answer}}</p></div>
<template v-if="turn.result?.items?.length"><div class="trust-note">库内电影 · 真实目录 ID</div><div class="cards-head"><strong>电影选择 · {{turn.result.items.length}} 部</strong><span v-if="behaviorReady()">横向浏览 · 可追问</span><span v-else class="behavior-warn" role="status">行为状态不可用，收藏与想看已暂停</span></div><div class="cards-rail"><article v-for="(m,cardIndex) in turn.result.items" :key="m.movie_id" class="movie-card" :style="{'--mi-card-i':Math.min(Number(cardIndex),3)}"><router-link :to="movieUrl(m.movie_id)" class="poster" :aria-label="'查看'+m.title+'详情'"><img v-if="m.poster||movieArtwork[String(m.movie_id)]?.poster" class="poster-uploaded" :src="m.poster||movieArtwork[String(m.movie_id)]?.poster" :alt="m.title+'海报'" loading="lazy"/><div v-else class="poster-title">{{m.title}}</div></router-link><div class="movie-info"><div class="movie-title">{{m.title}}</div><div class="movie-meta">{{m.release_year||'年份未知'}} · {{m.genres?.join(' / ')}}</div><span class="verified">库内影片</span><p class="why">{{m.reasons?.join('；')}}</p><div class="movie-actions"><router-link class="mini-btn" :to="movieUrl(m.movie_id)">详情</router-link><button class="mini-btn" :class="{'is-active':movieState(m).want}" :aria-pressed="movieState(m).want" :disabled="behaviorLocked(m)" :title="behaviorHint(m,'want')" @click="toggleMovieBehavior(m.movie_id,'want')">{{movieState(m).want?'撤销想看':'想看'}}</button><button class="mini-btn" :class="{'is-active':movieState(m).favorite}" :aria-pressed="movieState(m).favorite" :disabled="behaviorLocked(m)" :title="behaviorHint(m,'favorite')" @click="toggleMovieBehavior(m.movie_id,'favorite')">{{movieState(m).favorite?'取消收藏':'收藏'}}</button><button class="mini-btn" @click="followup(m.title)">追问</button></div></div></article></div></template>
<div v-if="turn.result?.discoveries?.length" class="no-result"><strong>库外电影发现 · 尚未入库</strong><article v-for="m in turn.result.discoveries" :key="m.title"><p>{{m.title}} · {{m.year||'年份未确认'}}</p><p>{{m.reason}}</p><small>{{m.verification==='TMDB_TITLE_YEAR_MATCH'?'TMDB 标题与年份匹配，仍未入库':m.verification==='TMDB_OFFICIAL_LIST_SNAPSHOT'?'TMDB 官方榜单快照核验，仍未入库':'未经 TMDB 核实的模型建议'}}</small><a v-if="m.tmdb_url&&modelLink(m.tmdb_url)" :href="m.tmdb_url" target="_blank" rel="noopener noreferrer">查看 TMDB</a></article></div>
<div class="followups"><button class="followup" type="button" @click="fillComposer('那更偏烧脑一点呢？')">更偏烧脑一点</button><button class="followup" type="button" @click="fillComposer('有没有更温暖、更有希望的？')">想要温暖一点</button><button class="followup" type="button" @click="fillComposer('当前库里还有哪些电影？')">库里还有什么</button></div>
<div class="response-actions"><div class="response-actions-buttons"><button class="response-action" type="button" aria-label="复制这条回复" @click="copyAnswer(turn.answer)"><svg class="ico"><use :href="'#'+uid('i-copy')"/></svg><span>复制</span></button><button class="response-action" type="button" aria-label="重新提问" :disabled="busy" @click="fillComposer(turn.question,'已填入原问题；确认发送可能产生新的模型费用。')"><svg class="ico"><use :href="'#'+uid('i-refresh')"/></svg><span>重新提问</span></button><button class="response-action" type="button" aria-label="有帮助" :aria-pressed="turn.feedback==='up'" :disabled="feedbackBusy[turn.result?.request_id]||!turn.result?.request_id" @click="feedback(turn,'up')"><svg class="ico"><use :href="'#'+uid('i-thumb-up')"/></svg><span>有帮助</span></button><button class="response-action" type="button" aria-label="需改进" :aria-pressed="turn.feedback==='down'" :disabled="feedbackBusy[turn.result?.request_id]||!turn.result?.request_id" @click="feedback(turn,'down')"><svg class="ico"><use :href="'#'+uid('i-thumb-down')"/></svg><span>需改进</span></button></div></div>
<p v-if="turn.result?.cost" class="reply-status">模型调用 {{turn.result.cost.provider_calls??'未知'}} 次 · 输入 {{turn.result.cost.input_tokens??'未知'}} / 输出 {{turn.result.cost.output_tokens??'未知'}} Token · 金额 {{turn.result.cost.amount===0?'0':'未知'}}</p><p v-for="limit in turn.result?.limitations" :key="limit" class="reply-status">{{limit}}</p>
</template>
</div></div></template>
<p v-if="result?.source==='INTERRUPTED'&&!history.some(t=>t.state==='interrupted'||t.state==='error')" class="reply-status" role="status">{{result.answer}}</p><p v-if="error" class="reply-status" role="alert">{{error}}</p><p v-if="notice" class="reply-status" role="status">{{notice}}</p>
</div></div><div class="composer-zone"><div class="composer-wrap"><div class="scroll-latest-anchor"><Transition name="mi-scroll-pill"><button v-if="showScrollLatest" :id="uid('scrollLatestBtn')" data-source-id="scrollLatestBtn" class="scroll-latest-pill" type="button" aria-label="回到底部查看最新消息" @click="scrollThreadToBottom(true)"><span aria-hidden="true" class="scroll-latest-arrow">↓</span><span>回到最新</span></button></Transition></div><div :hidden="!shortcutsOpen" class="composer-shortcuts" :id="uid('composerShortcuts')" data-source-id="composerShortcuts"><div class="shortcut-label">快捷提问</div><button @click="fillComposer('今晚看什么电影？')" data-shortcut="今晚看什么电影？" type="button">今晚看什么</button><button @click="fillComposer('想看烧脑一点的科幻电影')" data-shortcut="想看烧脑一点的科幻电影" type="button">烧脑科幻</button><button @click="fillComposer('推荐一部温暖、有希望的电影')" data-shortcut="推荐一部温暖、有希望的电影" type="button">温暖剧情</button></div><form @submit.prevent="submit" :class="{'mi14-expanded':expanded}" class="compose compose-r5 mi14-compose" :id="uid('composerForm')" data-source-id="composerForm">
<label class="sr-only" :for="uid('messageInput')">输入关于电影的问题</label>
<textarea @keydown.enter.exact="onComposerEnter" @input="autoGrow" ref="composerInput" :id="uid('messageInput')" data-source-id="messageInput" maxlength="2000" placeholder="问问 MovieInsight…" rows="1" spellcheck="false" v-model="message"></textarea>
<div class="mi14-controls">
<button @click="shortcutsOpen=!shortcutsOpen" aria-label="快捷提问" :aria-expanded="shortcutsOpen" class="compose-icon lead" :id="uid('attachBtn')" data-source-id="attachBtn" title="快捷提问" type="button"><svg class="ico"><use :href="'#'+uid('i-plus')"></use></svg></button>
<div class="compose-actions"><button @click="openSettings('ai')" aria-label="查看全局 AI 模型设置" class="mode-chip" :id="uid('modeChip')" data-source-id="modeChip" title="查看全局 AI 模型设置" type="button"><span class="mode-name">{{status.cloud_available?status.model:"MovieInsight 自动"}}</span><span class="mode-name-mobile">自动模式</span><svg class="ico tiny"><use :href="'#'+uid('i-chevron-down')"></use></svg></button><button :aria-label="busy?'取消生成':'发送消息'" :title="busy?'取消生成':'发送消息'" :disabled="!busy&&!message.trim()" :type="busy?'button':'submit'" @click="busy&&cancel()" class="send send-r5" :class="{'is-busy':busy,'is-ready':!busy&&!!message.trim()}" :id="uid('sendBtn')" data-source-id="sendBtn"><svg class="ico"><use :href="'#'+uid(busy?'i-close':'i-arrow-up')"></use></svg></button></div>
</div></form><div class="compose-footer"><span :id="uid('composerStatus')" data-source-id="composerStatus">{{busy?"正在处理…":status.cloud_available?"使用你的服务商账户 · 可能计费":"本地检索 · 不调用模型"}}</span><span class="key">Enter 发送 · Shift + Enter 换行</span></div></div></div>



</main></div></section>
</div></template>
<style scoped src="../styles/assistant-surface.css"></style>
<style scoped>



.orb-skin{display:block;position:relative;isolation:isolate;width:100%;height:100%;aspect-ratio:1;pointer-events:none;--ex:0px;--ey:0px}.orb-skin img{display:block;width:100%;height:100%;object-fit:contain;position:relative;z-index:2;user-select:none}.orb-skin:before{content:'';position:absolute;z-index:0;inset:-24%;border-radius:50%;background:radial-gradient(circle,rgba(160,103,255,.96) 0%,rgba(145,84,255,.80) 18%,rgba(126,66,248,.52) 34%,rgba(106,48,230,.20) 49%,rgba(88,36,201,.04) 60%,transparent 68%);filter:blur(7px);animation:orbHalo 5.3s ease-in-out infinite;opacity:.86}.orb-skin.mini:before{display:none!important}.orb-skin .eye{position:absolute;z-index:3;top:43.2%;width:7.6%;height:22%;background:#fff;border-radius:9px;box-shadow:0 0 8px #fff8,0 0 14px #b8aaff7d;transform:translate(var(--ex),var(--ey)) scaleY(var(--blink,1));transform-origin:center;transition:transform .15s ease-out}.orb-skin .eye.left{left:32.2%}.orb-skin .eye.right{left:60.8%}@keyframes orbHalo{0%,100%{opacity:.53;transform:scale(.97)}50%{opacity:1;transform:scale(1.05)}}
.window{position:fixed;z-index:30;left:50%;top:50%;width:min(1280px,calc(100vw - 60px));height:min(812px,calc(100dvh - 56px));transform:translate(-50%,-48%) scale(.97);opacity:0;visibility:hidden;display:flex;flex-direction:column;border:1px solid #c8b0ee3b;border-radius:25px;overflow:hidden;box-shadow:0 30px 90px #000b,0 0 0 1px #ffffff08;background:linear-gradient(145deg,#13111c 0%,#0b0b11 45%,#090a0e 100%);transition:opacity .27s ease,transform .32s ease,visibility .32s,border-radius .3s,width .3s,height .3s}.window.show{visibility:visible;opacity:1;transform:translate(-50%,-50%) scale(1)}.window.full{width:100vw;height:100dvh;border-radius:0;left:0;top:0;transform:translateY(0) scale(1);border:0}.window-head{flex:none;min-height:74px;display:flex;align-items:center;gap:13px;padding:13px 23px;background:linear-gradient(100deg,#221c37dc,#181426db 46%,#12101bde);border-bottom:1px solid #d4c4fb1c}.icon-btn{display:grid;place-items:center;width:38px;height:38px;border-radius:11px;background:transparent;border:1px solid transparent;color:#bdb7ce}.icon-btn:hover{background:#ffffff12;border-color:#ffffff13;color:#fff}.head-id{display:flex;align-items:center;gap:11px;min-width:0}.mark{height:35px;width:35px;flex:none}.head-title{font-size:14px;line-height:1.2;font-weight:650;letter-spacing:.08px}.head-fill{flex:1}.head-status{display:flex;align-items:center;gap:7px;background:#ffffff09;border:1px solid #ffffff16;color:#b9b2c9;font-size:11px;padding:8px 10px;border-radius:10px;white-space:nowrap}.head-status .dot{width:6px;height:6px;border-radius:50%;background:#deb477;box-shadow:0 0 0 3px #dbb66b18}.head-ops{display:flex;gap:4px}.divider-v{height:24px;width:1px;background:#ffffff20;margin:0 3px}.window-body{flex:1;min-height:0;display:flex;position:relative}.sidebar{width:0;flex:none;overflow:hidden;border-right:0 solid var(--line);background:#111019;transition:width .28s ease;z-index:4}.window.sidebar-open .sidebar{width:246px;border-right-width:1px}
.chat-main{display:flex;flex:1;flex-direction:column;min-width:0;min-height:0;position:relative}.thread{flex:1;overflow-y:auto;overflow-x:hidden;scrollbar-color:#564b63 transparent;scrollbar-width:thin;padding:31px clamp(18px,4.1vw,60px) 35px;overscroll-behavior:contain}.thread-inner{max-width:1000px;margin:0 auto;display:flex;flex-direction:column;gap:29px;min-height:100%}.msg{display:flex;gap:13px;max-width:100%;min-width:0}.msg.user{align-self:flex-end;max-width:min(75%,690px)}.bubble{color:#f9f5ff;background:linear-gradient(140deg,#5f54dc,#6253c8);padding:16px 20px;border-radius:19px 19px 5px 19px;font-size:14px;line-height:1.8;white-space:pre-wrap;overflow-wrap:anywhere;box-shadow:0 8px 32px #4932a02b}.avatar{width:34px;height:34px;flex:none;margin-top:2px}.assistant-body{min-width:0;flex:1;max-width:100%}.assistant-name{display:flex;align-items:center;gap:9px;margin:8px 0 13px;font-weight:650;font-size:13px}.assistant-name small{font-weight:400;font-size:11px;color:#817c8d}.reply{line-height:1.8;color:#ddd9e7;font-size:14px;max-width:860px;overflow-wrap:anywhere}.reply b{font-weight:600;color:#f4efff}.reply p{margin:0 0 12px}.trust-note{display:flex;align-items:center;gap:7px;color:#aea6be;font-size:11px;margin:13px 0 12px;flex-wrap:wrap}.trust-note:before{content:'';height:5px;width:5px;border-radius:50%;background:#a28cc9}.cards-head{display:flex;align-items:center;gap:9px;justify-content:space-between;margin:17px 0 11px;color:#c4bdd4;font-size:12px}.cards-head span{color:#797387;font-size:11px}.cards-head .behavior-warn{color:#e5ad75}.cards-rail{display:flex;align-items:stretch;gap:11px;overflow-x:auto;padding:1px 3px 9px 1px;scrollbar-width:thin;scrollbar-color:#50445b transparent;scroll-snap-type:x proximity;max-width:100%}.movie-card{position:relative;scroll-snap-align:start;flex:0 0 365px;min-height:200px;display:flex;gap:13px;background:linear-gradient(135deg,#24232b,#17171f 85%);border:1px solid #ffffff15;border-radius:16px;padding:10px;overflow:hidden;transition:border-color .2s,background .2s}.movie-card:hover{border-color:#a696dd55;background:linear-gradient(135deg,#302c3b,#191820 85%)}.poster{position:relative;display:flex;align-items:flex-end;justify-content:center;flex:none;width:111px;aspect-ratio:2/3;max-height:173px;overflow:hidden;border-radius:9px;background:#1b1e31;color:#fff;isolation:isolate}.poster:after{content:'';position:absolute;inset:0;background:linear-gradient(180deg,transparent 44%,#02060bbf 100%);pointer-events:none}.poster-title{z-index:2;text-align:center;font-family:Georgia,'Noto Serif SC',serif;font-weight:bold;font-size:16px;line-height:1.3;letter-spacing:2px;margin:0 3px 16px;text-shadow:0 2px 10px #000;word-break:break-all}.poster-title small{display:block;letter-spacing:2px;font-size:7px;color:#f7f5fbaf;margin-top:4px}.poster img.poster-uploaded{width:100%;height:100%;object-fit:cover;position:absolute;inset:0;z-index:1}.movie-info{display:flex;flex:1;min-width:0;flex-direction:column;align-items:flex-start;padding:4px 1px 1px 0}.movie-title{font-size:14px;font-weight:650;margin:2px 0 5px;color:#f4f1f9}.movie-meta{font-size:11px;color:#a19aab}.verified{color:#d6b7ee;background:#a88ccd19;border:1px solid #b09ed12a;display:inline-flex;align-items:center;border-radius:5px;font-size:10px;padding:3px 6px;margin-top:9px}.why{font-size:11px;line-height:1.7;color:#b6afc0;margin:10px 0 10px;display:-webkit-box;-webkit-line-clamp:3;-webkit-box-orient:vertical;overflow:hidden}.movie-actions{display:flex;gap:6px;align-items:center;flex-wrap:wrap;margin-top:auto}.mini-btn{font-size:11px;color:#d8d3e8;border:1px solid #ffffff24;border-radius:7px;background:#ffffff0c;padding:6px 9px;white-space:nowrap}.mini-btn:hover{background:#8f77dd35;border-color:#9d88eb66}.mini-btn.is-active{background:#8468da35;color:#e5d9ff;border-color:#a28de363}.followups{display:flex;flex-wrap:wrap;gap:8px;margin-top:16px}.followup{border:1px solid #9e80da54;border-radius:99px;background:#8b6dcc0e;color:#cfbfff;padding:8px 12px;font-size:11px}.followup:hover{background:#7a59c234}.no-result{margin-top:11px;background:#3b2f2722;border:1px dashed #b59a7052;padding:15px;border-radius:12px;color:#cdbca8;font-size:12px}.empty-state{margin:auto;max-width:600px;text-align:center;padding:40px 0}.empty-orb{width:82px;height:82px;display:block;margin:0 auto 17px}.empty-state h2{font-family:Georgia,'Noto Serif SC',serif;font-weight:500;font-size:28px;letter-spacing:.4px;margin:9px 0}.empty-state p{color:#928c9f;font-size:13px;line-height:1.9}.empty-suggestions{display:flex;gap:9px;justify-content:center;flex-wrap:wrap;margin-top:19px}.date-line{color:#767082;font-size:11px;text-align:center;margin:0 0 13px}
.composer-zone{flex:none;border-top:1px solid #ffffff14;padding:16px clamp(18px,4.1vw,60px) 12px;background:linear-gradient(180deg,#111016f0,#100f17 85%)}.composer-wrap{max-width:1000px;margin:auto}.compose{display:flex;align-items:flex-end;gap:11px;background:#1d1b25;border:1px solid #68588d80;border-radius:17px;box-shadow:0 0 0 2px #6f58bb0a;transition:border-color .2s}.compose:focus-within{border-color:#b39cf3a3;box-shadow:0 0 0 3px #785bbb12}.compose textarea{flex:1;min-width:0;resize:none;min-height:53px;max-height:148px;padding:16px 8px 12px 17px;border:0;outline:0!important;color:#f5f1ff;background:transparent;line-height:1.65;font-size:14px;scrollbar-width:thin}.compose textarea::placeholder{color:#8f899e}.compose .send{flex:none;border:0;border-radius:12px;width:38px;height:38px;display:grid;place-items:center;background:#7357ce;color:white;margin:0 10px 9px 0}.compose .send:hover{background:#8867e9}.compose .send svg{width:18px;height:18px}.compose .send:disabled{background:#555067}.compose-footer{display:flex;justify-content:space-between;align-items:center;gap:8px;color:#787184;font-size:10px;margin-top:8px}.compose-footer .key{color:#afa4c5}.typing-dots{display:flex;gap:4px;align-items:center;height:18px}.typing-dots i{width:5px;height:5px;border-radius:100%;background:#b8a1fb;animation:pulse 1.1s infinite ease-in-out}.typing-dots i:nth-child(2){animation-delay:.15s}.typing-dots i:nth-child(3){animation-delay:.3s}@keyframes pulse{0%,65%,100%{opacity:.35;transform:translateY(0)}30%{opacity:1;transform:translateY(-3px)}}
@media(max-width:920px){.window{width:calc(100vw - 28px);height:calc(100dvh - 28px)}.window-head{padding:11px 13px}.thread{padding:24px 22px}.composer-zone{padding:13px 22px 10px}}
@media(max-width:620px){.window{top:0;left:0;width:100vw;height:100dvh;border-radius:0;border:0;transform:translateY(14px) scale(.98)}.window.show{transform:translateY(0) scale(1)}.window.full{transform:none}.window-head{min-height:66px;gap:5px;padding:10px 8px}.head-id{gap:7px}.mark{width:30px;height:30px}.head-title{font-size:12px}.head-status{display:flex;width:27px;height:29px;justify-content:center;padding:4px;font-size:0;border-radius:9px}.head-status .dot{width:7px;height:7px}.icon-btn{width:34px;height:34px}.head-ops{gap:0}.divider-v{display:none}.window.sidebar-open .sidebar{position:absolute;left:0;top:0;bottom:0;width:80%;max-width:280px;box-shadow:20px 0 40px #0009;border-right:1px solid #b7a4f02b}.thread{padding:20px 14px 24px}.thread-inner{gap:24px}.msg{gap:9px}.msg.user{max-width:90%}.bubble{font-size:13px;padding:12px 14px}.avatar{width:29px;height:29px}.assistant-name{margin:4px 0 10px}.reply{font-size:13px}.movie-card{flex-basis:282px;min-height:175px;gap:10px}.poster{width:90px;max-height:149px}.movie-title{font-size:13px}.movie-meta{font-size:10px}.why{-webkit-line-clamp:2;font-size:10px;margin:7px 0}.mini-btn{font-size:10px;padding:5px 7px}.composer-zone{padding:10px 12px calc(8px + env(safe-area-inset-bottom))}.compose textarea{min-height:49px;padding:13px 5px 10px 13px;font-size:13px}.compose-footer{font-size:9px}.window.sidebar-open .sidebar{transition:width .2s}}
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
.date-line{text-align:left;color:#6d687a;font-size:11px}
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
@media (min-width: 1100px){
  .window-head{padding-inline:28px}
  .thread{padding-top:42px}
}
@media (max-width: 820px){
  .window{width:100vw;height:100dvh;border-radius:0;border:0}
  .window-head{padding:10px 14px 6px;gap:8px}
  .head-status{display:none}
  .thread{padding:22px 16px 18px}
  .thread-inner,.composer-wrap,.empty-state{max-width:100%}
  .reply{font-size:14px;line-height:1.9}
  .composer-zone{padding:6px 14px 14px}
  .movie-card{flex-basis:min(360px,88vw)}
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
/* compact A cards fit two across inside 760px reading column */
.movie-card{flex:0 0 345px}
.chat-main{display:flex;flex:1;flex-direction:column;min-width:0;min-height:0;position:relative}
@media(max-width:620px){
  .window.sidebar-open .sidebar{
    position:absolute;left:0;top:0;bottom:0;width:80%;max-width:280px;
    background:#090a10;border-radius:0;box-shadow:12px 0 28px #0005;
    border-right:1px solid rgba(211,198,255,.11);z-index:7;
  }
  .movie-card{flex-basis:282px}
}

/* V13.10 R2 · unified account footer, following the small avatar/name + gear reference */
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
/* V13.10 R4 — account thumbnail matches screenshot #2 */
.account-avatar,.profile-avatar-lg{
  background:#2e2e32!important;border:0!important;border-radius:11px!important;
  box-shadow:none!important;overflow:hidden!important;
}
.account-avatar{width:38px!important;height:38px!important}
.profile-avatar-lg{width:52px!important;height:52px!important;border-radius:14px!important}
.account-identity{gap:10px;padding:5px 7px;border-radius:12px}
.account-identity:hover{background:#ffffff08}
.account-settings{color:#cec1e7}
.profile-edit-link{display:block;width:100%;margin-top:12px;border:1px solid #ffffff24;background:#ffffff0b;color:#e3dafa;border-radius:9px;padding:10px}
/* Full-page route; settings is a dedicated screen, not a drawer, not a chat modal. */
/* avatar crop: fixed-square field of view, square output, move/zoom the image */
.prototype-ai.pref-large-text .reply{font-size:17px}.prototype-ai.pref-large-text .bubble{font-size:15px}
.prototype-ai.pref-reduced-motion .orb-skin:before{animation:none!important}
.prototype-ai.pref-reduced-motion .window,.prototype-ai.pref-reduced-motion .sidebar{transition:none!important}

/* V13.10 R4 · empty avatar placeholder / exactly 40 px slot / preserve transparency */

/* V13.10 R5 · remove subtitle and update composer controls */
.head-id > div{display:flex;align-items:center;min-width:0}
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
.reply .reply-body{font-size:14px;font-weight:400;line-height:1.95;color:#c6c0d2;margin:0 0 13px}
.response-actions{display:flex;align-items:center;justify-content:space-between;gap:12px;flex-wrap:wrap;padding-top:10px;margin:13px 0 4px;border-top:1px solid #ffffff0c}
.response-actions-buttons{display:flex;flex-wrap:wrap;gap:6px;align-items:center}
.response-action{display:inline-flex;align-items:center;justify-content:center;gap:6px;min-height:32px;padding:6px 9px;border:1px solid transparent;background:none;border-radius:9px;color:#9992a8;font-size:11px;transition:background .15s,color .15s}
.response-action .ico{width:14px;height:14px;stroke-width:1.7}
.response-action:hover{color:#e7dcff;background:#ffffff0b}
.response-action.active{background:#7962b627;border-color:#a58ad344;color:#d9c8ff}
.cards-head{margin-top:16px;margin-bottom:10px}
.cards-rail{gap:11px;scroll-padding:3px;padding-bottom:10px}
.movie-card{flex:0 0 min(349px,87vw);min-height:191px;padding:10px;border-radius:15px}
.poster{width:108px;max-height:169px}
.movie-title{font-size:13px}
.why{line-height:1.65;font-size:11px;margin:7px 0}
.trust-note{font-size:10px;margin:11px 0}
.compose-r5 .compose-actions{gap:6px}
@media(max-width:620px){
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
.window.show,.window.full,.window.full.show{
  left:50%;top:50%;
  transform:translate(-50%,-50%) scale(1);
  transform-origin:center center;
}
.window:not(.show){transform:translate(-50%,-48%) scale(.97)}
@media (max-width:820px){
  .window,.window.show,.window.full,.window.full.show{
    left:50%;top:50%;
    transform:translate(-50%,-50%) scale(1);
  }
  .window:not(.show){transform:translate(-50%,-49%) scale(.99)}
}

/* V13.13 · reply state visuals; existing dark MovieInsight design retained */
.chat-main{position:relative}

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
  scrollbar-width:thin;transition:height .14s cubic-bezier(.22,1,.36,1);scrollbar-color:#6b6374 transparent;
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
  transition:border-radius .2s cubic-bezier(.22,1,.36,1),min-height .18s cubic-bezier(.22,1,.36,1),background-color .18s ease,border-color .18s ease,box-shadow .18s ease!important;
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
  scrollbar-width:thin;transition:height .14s cubic-bezier(.22,1,.36,1);
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
}

/* V13.15 · conversation reading design: neutral, centered, unobstructed. */
.thread{padding-top:20px!important}
.thread-inner{max-width:760px!important;gap:44px!important}
.thread .date-line{color:#716a78;font-size:11px;text-align:center;margin:2px 0 8px}
.msg.user{align-self:stretch!important;justify-content:flex-end;width:100%!important;max-width:none!important;min-width:0}
.msg.user .bubble{max-width:min(82%,560px);width:max-content;background:#202025!important;border:1px solid #ffffff06!important;color:#f4f2f7;border-radius:25px!important;padding:12px 18px!important;line-height:1.75;box-shadow:none!important;font-size:14px;white-space:pre-wrap;overflow-wrap:anywhere}
.msg.assistant{align-self:stretch;width:100%;max-width:100%;gap:0}
.msg.assistant .assistant-body{min-width:0;max-width:100%;flex:1}
.msg.assistant .reply{max-width:100%;font-size:15px;line-height:1.91;color:#e4e1e9;overflow-wrap:anywhere}
.msg.assistant .reply p{margin:0 0 17px}
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
.cards-head{margin-top:24px}
.cards-rail{margin-top:5px}
/* Default: Gemini-like clean plain-text turn with no repeated assistant logo/name. */
.window[data-dialog-mode="gemini"] .msg.assistant>.avatar,.window[data-dialog-mode="gemini"] .msg.assistant .assistant-name{display:none!important}
.window[data-dialog-mode="gemini"] .msg.assistant .reply{line-height:1.95}
.window[data-dialog-mode="gemini"] .msg.assistant .response-actions{margin-left:-4px}
/* Alternate: MovieInsight native mode restores only a small identity cue. */
.window[data-dialog-mode="native"] .msg.assistant{gap:12px}
.window[data-dialog-mode="native"] .msg.assistant .avatar{display:block;align-self:flex-start;margin-top:1px}
.window[data-dialog-mode="native"] .msg.assistant .assistant-name{display:flex;margin:4px 0 11px;color:#ded5f4}
.window[data-dialog-mode="native"] .msg.assistant .assistant-name small{color:#81788f}
.window[data-dialog-mode="native"] .msg.assistant .response-actions{margin-top:14px}
.window[data-dialog-mode="native"] .msg.assistant .response-action{width:auto;min-width:32px;padding:0 7px;gap:5px}
.window[data-dialog-mode="native"] .msg.assistant .response-action span{display:inline;font-size:11px}
@media(max-width:620px){
 .thread{padding-top:14px!important}
 .thread-inner{gap:29px!important}
 .msg.user .bubble{max-width:87%;font-size:13px;padding:10px 14px!important}
 .msg.assistant .reply{font-size:14px;line-height:1.85}
 .window[data-dialog-mode="native"] .msg.assistant{gap:8px}
 .window[data-dialog-mode="native"] .msg.assistant .avatar{width:24px;height:24px}
 .window[data-dialog-mode="native"] .msg.assistant .response-action span{display:none}
}

.prototype-ai{overflow-x:hidden!important}

/* The iframe is cropped to the card itself, so it cannot paint over the old page. */
.prototype-ai:not(.mi2-docked-ai) .window,.prototype-ai:not(.mi2-docked-ai) .window.show,.prototype-ai:not(.mi2-docked-ai) .window.full{
 position:fixed!important;inset:0!important;left:0!important;top:0!important;
 width:100vw!important;height:100dvh!important;max-width:none!important;max-height:none!important;
 transform:none!important;opacity:1!important;visibility:visible!important;
 border-radius:24px!important;box-shadow:none!important;transition:none!important;
}
@media(max-width:700px){.prototype-ai:not(.mi2-docked-ai) .window,.prototype-ai:not(.mi2-docked-ai) .window.show{border-radius:16px!important}}

/* MovieInsight M9-P8.7 · OSS-inspired interaction refinements, no new visual theme */
.prototype-ai{--mi17-hairline:rgba(182,158,223,.13);--mi17-soft:rgba(179,140,239,.10);--mi17-focus:#c4a5fc}
.mi17-user-actions{display:flex;justify-content:flex-end;gap:9px;align-items:center;opacity:0;transition:opacity .13s}
.msg.user:hover .mi17-user-actions,.msg.user:focus-within .mi17-user-actions{opacity:1}
.mi17-user-actions button{border:0;background:none;color:#9891a8;font-size:10px;padding:3px 5px;border-radius:6px}
.mi17-user-actions button:hover{color:#e2ceff;background:#252031}
.mi2-docked-ai [data-source-id="fullToggle"]{display:none!important}
.mi2-docked-ai .window{animation:none!important}
@media(max-width:620px){.mi17-user-actions{opacity:1}}
@media(prefers-reduced-motion:reduce){.mi17-user-actions,.movie-card .poster{transition:none!important}}


/* Never repurpose floating window's full-screen control to navigate to the AI Explore route. */
[data-source-id="fullToggle"]{display:none!important}


/* M9-P8.2 shared design tokens — cinema graphite / restrained violet */
.prototype-ai{--mi2-bg:#0c0d0f;--mi2-stage:#0e0f13;--mi2-panel:#141519;--mi2-panel-2:#1b1c21;
 --mi2-ink:#fff;--mi2-sub:#c2c4cb;--mi2-quiet:#95979f;--mi2-accent:#ad8bea;
 --mi2-accent-hover:#c3a6fa;--mi2-line:rgba(255,255,255,.12);--mi2-focus:#f1ece2;
 --mi2-shadow:0 16px 50px rgba(0,0,0,.35)}
:where(button,input,select,textarea,[role="button"]):focus-visible{outline:2px solid var(--mi2-focus)!important;outline-offset:3px}

/* P8.2 cross-page color normalization, keeping each page's accepted layout */
.prototype-ai{--ink:#f2eff9;--text:#f2eff9;--muted:#aaa4bc;--line:rgba(191,174,225,.13);
 --border:rgba(191,174,225,.13);--purple:#ad8bea;--accent:#ad8bea;--lav:#c0a5f5}
.prototype-ai,.prototype-ai{scrollbar-color:#504467 #111321}
:where(button,input,select,textarea,[role="button"]):focus-visible{outline:2px solid #c2a6f4!important;outline-offset:3px}


.prototype-ai{background:transparent!important}


.prototype-ai.mi2-docked-ai{background:#080a13!important;overflow:hidden!important}
.prototype-ai.mi2-docked-ai .window,.prototype-ai.mi2-docked-ai .window.show,.prototype-ai.mi2-docked-ai .window.full{
 position:fixed!important;inset:0!important;left:0!important;top:0!important;width:100vw!important;height:100dvh!important;
 transform:none!important;opacity:1!important;visibility:visible!important;transition:none!important;animation:none!important;
 border:0!important;border-radius:0!important;box-shadow:none!important
}
.prototype-ai.mi2-docked-ai *,.prototype-ai.mi2-docked-ai *:before,.prototype-ai.mi2-docked-ai *:after{
 /* animations enabled for orb, thinking & sidebar */
}


/* Floating-card mode: the host page must remain visible behind the V13 chat card. */
.prototype-ai:not(.mi2-docked-ai),.prototype-ai{background:transparent!important}

.prototype-ai{position:relative;width:100%;height:100%;overflow:hidden;isolation:isolate}
.prototype-ai .window,.prototype-ai .window.show,.prototype-ai .window.full{position:absolute!important;inset:0!important;left:0!important;top:0!important;width:100%!important;height:100%!important;max-width:none!important;max-height:none!important;min-width:0!important;min-height:0!important;transform:none!important;opacity:1!important;visibility:visible!important;border-radius:0!important;box-shadow:none!important;transition:none!important}
.prototype-ai.surface-floating .window{background:transparent!important;border:0!important}
.prototype-ai .reply-body{white-space:pre-wrap;overflow-wrap:anywhere}.prototype-ai .poster{cursor:pointer}.prototype-ai .poster-uploaded{opacity:1!important;position:absolute;inset:0;width:100%;height:100%;object-fit:cover}.prototype-ai .reply-status{color:#c9b5dd;font-size:11px;line-height:1.8}
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
/* 组2 统一：聊天容器与控件向首页基准靠拢（#0c0d0f / #141519 / 白色正文 / 细发丝线），紫眼 3D 球与业务逻辑不动 */
.window{background:linear-gradient(180deg,#0c0d0f 0%,#0e0f13 58%,#101116 100%)!important}
.sidebar{background:#0b0c10!important;border-right:1px solid #ffffff14;box-shadow:none!important}
.window-head{background:transparent}
.composer-zone{background:linear-gradient(180deg,#0c0d0fe6 0%,#0c0d0f 45%)!important}
.send,.send-r5,.mi14-controls button[data-source-id="sendBtn"]{background:#f1ece2!important;color:#0c0d0f!important}
.send:hover:not(:disabled),.send-r5:hover:not(:disabled),.mi14-controls button[data-source-id="sendBtn"]:hover:not(:disabled){background:#fff!important}
.send:disabled,.send-r5:disabled,.mi14-controls button[data-source-id="sendBtn"]:disabled{background:#26272d!important;color:#8b8d95!important}
:where(button,input,select,textarea,[role="button"]):focus-visible{outline-color:#ffffff66!important}
.side-action:hover,.side-action:focus-visible{background:#ffffff0f!important;border-color:#ffffff24!important}
.side-more:hover,.side-more:focus-visible{background:#ffffff12!important;color:#fff!important}
/* 组2 收口：侧栏残余紫底中性化（新对话 / 搜索 / 选中 / 顶部），不重复堆 !important */
.side-label{color:#b9bbc2!important}
.side-collapse{color:#b9bbc2}
.side-collapse:hover,.side-collapse:focus-visible{background:rgba(255,255,255,.06);color:#fff}
.side-action{background:transparent;border-color:rgba(255,255,255,.16);color:#fff}
.side-action:hover,.side-action:focus-visible{background:rgba(255,255,255,.07);border-color:rgba(255,255,255,.24)}
.side-search{background:#141519;border-color:rgba(255,255,255,.12)}
.side-search .ico{color:#b9bbc2}
.side-search input{color:#fff}
.side-search input::placeholder{color:#9a9ca4}
.side-search:focus-within{border-color:rgba(255,255,255,.4);box-shadow:0 0 0 2px rgba(255,255,255,.08)}
.side-row.active{background:rgba(255,255,255,.08)}
.side-row.active .side-title{color:#fff}
.side-empty{color:#9a9ca4}
/* 组2 收口：浮窗专用的紫底覆盖（原规则为 surface-floating 三段选择器 !important），此处同权重后置改为中性 */
.prototype-ai.surface-floating .window-head{background:#0c0d0f!important}
.prototype-ai.surface-floating .sidebar{background:#0c0d0f!important}
.window .sidebar{color:#fff}
.window.sidebar-open .sidebar{border-right-color:rgba(255,255,255,.12)!important}
/* 组2 收口：窄屏侧栏改为不透底抽屉，顶部栏抬到抽屉之上，关闭/切换按钮始终可点 */
.window-head{position:relative;z-index:9}
@media (max-width:620px){
  .window.sidebar-open .sidebar{background:#0c0d0f;border-right:1px solid rgba(255,255,255,.12);box-shadow:18px 0 42px rgba(0,0,0,.6);width:min(300px,86vw);max-width:none}
  .window.sidebar-open .sidebar .side-panel{background:#0c0d0f}
}</style>
<style>
/* 浅色主题（unslop Warm Paper）：保持布局零改动，统一日间纸感与暖铜金强调色 */
:root[data-theme="light"] .prototype-ai{background:var(--mi-bg);color:var(--mi-text)}
:root[data-theme="light"] .prototype-ai .window{background:var(--mi-bg)!important;color:var(--mi-text)!important;border-color:var(--mi-border)!important;box-shadow:var(--mi-shadow-md)!important}
:root[data-theme="light"] .prototype-ai .window-head{background:var(--mi-bg)!important;border-bottom-color:var(--mi-border)!important;color:var(--mi-text)!important}
:root[data-theme="light"] .prototype-ai.surface-floating .window-head{background:var(--mi-surface)!important}
:root[data-theme="light"] .prototype-ai .head-title,:root[data-theme="light"] .prototype-ai .head-id{color:var(--mi-text)!important}
:root[data-theme="light"] .prototype-ai .head-status,:root[data-theme="light"] .prototype-ai .date-line,:root[data-theme="light"] .prototype-ai .trust-note,:root[data-theme="light"] .prototype-ai .cards-head,:root[data-theme="light"] .prototype-ai .movie-meta,:root[data-theme="light"] .prototype-ai .why,:root[data-theme="light"] .prototype-ai .shortcut-label,:root[data-theme="light"] .prototype-ai .reply-status{color:var(--mi-text-secondary)!important}
:root[data-theme="light"] .prototype-ai .icon-btn,:root[data-theme="light"] .prototype-ai .mini-btn,:root[data-theme="light"] .prototype-ai .response-action{color:var(--mi-text-secondary)!important;border-color:var(--mi-border)!important;background:transparent!important}
:root[data-theme="light"] .prototype-ai .icon-btn:hover,:root[data-theme="light"] .prototype-ai .mini-btn:hover,:root[data-theme="light"] .prototype-ai .response-action:hover{color:var(--mi-text)!important;background:var(--mi-surface-hover)!important;border-color:var(--mi-border-strong)!important}
:root[data-theme="light"] .prototype-ai .sidebar,:root[data-theme="light"] .prototype-ai.surface-floating .sidebar{background:var(--mi-sidebar-bg)!important;border-right:1px solid var(--mi-sidebar-border)!important;color:var(--mi-text)!important}
:root[data-theme="light"] .prototype-ai .side-panel,:root[data-theme="light"] .prototype-ai .window.sidebar-open .sidebar .side-panel{background:var(--mi-sidebar-bg)!important;color:var(--mi-text)!important}
:root[data-theme="light"] .prototype-ai .side-label,:root[data-theme="light"] .prototype-ai .side-action,:root[data-theme="light"] .prototype-ai .side-row.active,:root[data-theme="light"] .prototype-ai .side-row.active .side-title,:root[data-theme="light"] .prototype-ai .account-name{color:var(--mi-text)!important}
:root[data-theme="light"] .prototype-ai .side-collapse,:root[data-theme="light"] .prototype-ai .side-more,:root[data-theme="light"] .prototype-ai .side-import-btn,:root[data-theme="light"] .prototype-ai .side-empty,:root[data-theme="light"] .prototype-ai .side-import-hint{color:var(--mi-text-muted)!important}
:root[data-theme="light"] .prototype-ai .side-action{border-color:var(--mi-border-strong)!important;background:var(--mi-surface)!important}
:root[data-theme="light"] .prototype-ai .side-action:hover,:root[data-theme="light"] .prototype-ai .side-row:hover,:root[data-theme="light"] .prototype-ai .side-more:hover,:root[data-theme="light"] .prototype-ai .side-collapse:hover{background:var(--mi-surface-hover)!important;color:var(--mi-text)!important}
:root[data-theme="light"] .prototype-ai .side-search{background:var(--mi-input-bg)!important;border-color:var(--mi-border)!important}
:root[data-theme="light"] .prototype-ai .side-search input,:root[data-theme="light"] .prototype-ai .side-rename{color:var(--mi-text)!important}
:root[data-theme="light"] .prototype-ai .side-search input::placeholder{color:var(--mi-text-subtle)!important}
:root[data-theme="light"] .prototype-ai .side-row{color:var(--mi-text-secondary)!important}
:root[data-theme="light"] .prototype-ai .side-row.active{background:var(--mi-sidebar-active)!important}
:root[data-theme="light"] .prototype-ai .side-pop,:root[data-theme="light"] .prototype-ai .profile-panel,:root[data-theme="light"] .prototype-ai .side-import-tray{background:var(--mi-surface)!important;border:1px solid var(--mi-border-strong)!important;color:var(--mi-text)!important;box-shadow:var(--mi-shadow-md)!important}
:root[data-theme="light"] .prototype-ai .side-pop button{color:var(--mi-text)!important}
:root[data-theme="light"] .prototype-ai .side-pop button:hover{background:var(--mi-surface-hover)!important}
:root[data-theme="light"] .prototype-ai .side-pop button.danger{color:var(--mi-negative)!important}
:root[data-theme="light"] .prototype-ai .thread,:root[data-theme="light"] .prototype-ai .chat-main{background:transparent!important;color:var(--mi-text)!important;scrollbar-color:var(--mi-scroll-thumb) transparent!important}
:root[data-theme="light"] .prototype-ai .empty-state h2,:root[data-theme="light"] .prototype-ai .assistant-name,:root[data-theme="light"] .prototype-ai .reply-body,:root[data-theme="light"] .prototype-ai .reply-body strong,:root[data-theme="light"] .prototype-ai .reply-body b,:root[data-theme="light"] .prototype-ai .movie-title,:root[data-theme="light"] .prototype-ai .poster-title{color:var(--mi-text)!important}
:root[data-theme="light"] .prototype-ai .empty-state p{color:var(--mi-text-secondary)!important}
:root[data-theme="light"] .prototype-ai .msg.user .bubble{background:var(--mi-surface-raised)!important;border:1px solid var(--mi-border)!important;color:var(--mi-text)!important;box-shadow:none!important}
:root[data-theme="light"] .prototype-ai .msg.assistant .bubble,:root[data-theme="light"] .prototype-ai .reply{background:transparent!important;color:var(--mi-text)!important}
:root[data-theme="light"] .prototype-ai .movie-card{background:var(--mi-surface)!important;border:1px solid var(--mi-border)!important;box-shadow:var(--mi-shadow-sm)!important;color:var(--mi-text)!important}
:root[data-theme="light"] .prototype-ai .movie-card:hover{border-color:var(--mi-accent)!important;box-shadow:var(--mi-shadow-md)!important}
:root[data-theme="light"] .prototype-ai .movie-card .poster{background:var(--mi-surface-raised)!important;border-color:var(--mi-border)!important}
:root[data-theme="light"] .prototype-ai .movie-actions button,:root[data-theme="light"] .prototype-ai .followup,:root[data-theme="light"] .prototype-ai .mode-chip{background:var(--mi-surface)!important;border:1px solid var(--mi-border-strong)!important;color:var(--mi-text-soft)!important}
:root[data-theme="light"] .prototype-ai .movie-actions button:hover,:root[data-theme="light"] .prototype-ai .followup:hover,:root[data-theme="light"] .prototype-ai .mode-chip:hover{background:var(--mi-surface-hover)!important;border-color:var(--mi-accent)!important;color:var(--mi-text)!important}
:root[data-theme="light"] .prototype-ai .movie-actions button.is-active{background:var(--mi-accent-soft)!important;border-color:var(--mi-accent-border)!important;color:var(--mi-accent-text)!important}
:root[data-theme="light"] .prototype-ai .verified{background:var(--mi-positive-soft)!important;color:var(--mi-positive)!important;border-color:var(--mi-positive-border)!important}
:root[data-theme="light"] .prototype-ai .behavior-warn{background:var(--mi-warning-soft)!important;color:var(--mi-warning)!important;border-color:var(--mi-accent-border)!important}
:root[data-theme="light"] .prototype-ai .composer-zone{background:var(--mi-composer-fade)!important;border-top-color:var(--mi-border)!important}
:root[data-theme="light"] .prototype-ai .compose,:root[data-theme="light"] .prototype-ai .compose-r5,:root[data-theme="light"] .prototype-ai .mi14-compose{background:var(--mi-surface)!important;border:1px solid var(--mi-border-strong)!important;box-shadow:var(--mi-shadow-sm)!important;color:var(--mi-text)!important}
:root[data-theme="light"] .prototype-ai .compose:focus-within,:root[data-theme="light"] .prototype-ai .compose-r5:focus-within,:root[data-theme="light"] .prototype-ai .mi14-compose:focus-within{border-color:var(--mi-accent)!important}
:root[data-theme="light"] .prototype-ai .compose textarea,:root[data-theme="light"] .prototype-ai .compose-r5 textarea,:root[data-theme="light"] .prototype-ai .mi14-compose textarea{color:var(--mi-text)!important;background:transparent!important}
:root[data-theme="light"] .prototype-ai .compose textarea::placeholder,:root[data-theme="light"] .prototype-ai .compose-r5 textarea::placeholder,:root[data-theme="light"] .prototype-ai .mi14-compose textarea::placeholder{color:var(--mi-text-subtle)!important}
:root[data-theme="light"] .prototype-ai .send,:root[data-theme="light"] .prototype-ai .send-r5,:root[data-theme="light"] .prototype-ai .mi14-controls button[data-source-id="sendBtn"]{background:var(--mi-primary-btn-bg)!important;color:var(--mi-primary-btn-text)!important}
:root[data-theme="light"] .prototype-ai .send:hover:not(:disabled),:root[data-theme="light"] .prototype-ai .send-r5:hover:not(:disabled),:root[data-theme="light"] .prototype-ai .mi14-controls button[data-source-id="sendBtn"]:hover:not(:disabled){background:var(--mi-primary-btn-hover)!important}
:root[data-theme="light"] .prototype-ai .send:disabled,:root[data-theme="light"] .prototype-ai .send-r5:disabled,:root[data-theme="light"] .prototype-ai .mi14-controls button[data-source-id="sendBtn"]:disabled{background:var(--mi-surface-raised)!important;color:var(--mi-text-muted)!important}
:root[data-theme="light"] .prototype-ai .compose-icon,:root[data-theme="light"] .prototype-ai .mi14-controls .compose-icon.lead{color:var(--mi-text)!important}
:root[data-theme="light"] .prototype-ai .compose-icon:hover,:root[data-theme="light"] .prototype-ai .mi14-controls .compose-icon.lead:hover{background:var(--mi-surface-hover)!important;color:var(--mi-text)!important}
:root[data-theme="light"] .prototype-ai .account-settings,:root[data-theme="light"] .prototype-ai .profile-edit-link{color:var(--mi-accent-text)!important}
:root[data-theme="light"] .prototype-ai :where(button,input,select,textarea,[role="button"]):focus-visible{outline-color:var(--mi-accent)!important}

/* 深色主题正文统一默认纯白（保留深底与语义状态色） */
:root[data-theme="dark"] .prototype-ai,
:root[data-theme="dark"] .prototype-ai .window,
:root[data-theme="dark"] .prototype-ai .window-head,
:root[data-theme="dark"] .prototype-ai .head-title,
:root[data-theme="dark"] .prototype-ai .head-id,
:root[data-theme="dark"] .prototype-ai .head-status,
:root[data-theme="dark"] .prototype-ai .date-line,
:root[data-theme="dark"] .prototype-ai .trust-note,
:root[data-theme="dark"] .prototype-ai .cards-head,
:root[data-theme="dark"] .prototype-ai .cards-head span:not(.behavior-warn),
:root[data-theme="dark"] .prototype-ai .movie-meta,
:root[data-theme="dark"] .prototype-ai .why,
:root[data-theme="dark"] .prototype-ai .shortcut-label,
:root[data-theme="dark"] .prototype-ai .reply-status,
:root[data-theme="dark"] .prototype-ai .icon-btn,
:root[data-theme="dark"] .prototype-ai .mini-btn,
:root[data-theme="dark"] .prototype-ai .response-action,
:root[data-theme="dark"] .prototype-ai .sidebar,
:root[data-theme="dark"] .prototype-ai .side-panel,
:root[data-theme="dark"] .prototype-ai .side-label,
:root[data-theme="dark"] .prototype-ai .side-action,
:root[data-theme="dark"] .prototype-ai .side-collapse,
:root[data-theme="dark"] .prototype-ai .side-more,
:root[data-theme="dark"] .prototype-ai .side-import-btn,
:root[data-theme="dark"] .prototype-ai .side-empty,
:root[data-theme="dark"] .prototype-ai .side-import-hint,
:root[data-theme="dark"] .prototype-ai .side-row,
:root[data-theme="dark"] .prototype-ai .side-title,
:root[data-theme="dark"] .prototype-ai .side-search input,
:root[data-theme="dark"] .prototype-ai .side-rename,
:root[data-theme="dark"] .prototype-ai .side-pop button:not(.danger),
:root[data-theme="dark"] .prototype-ai .profile-panel,
:root[data-theme="dark"] .prototype-ai .profile-meta strong,
:root[data-theme="dark"] .prototype-ai .profile-meta small,
:root[data-theme="dark"] .prototype-ai .profile-detail,
:root[data-theme="dark"] .prototype-ai .profile-note,
:root[data-theme="dark"] .prototype-ai .account-name,
:root[data-theme="dark"] .prototype-ai .account-settings,
:root[data-theme="dark"] .prototype-ai .profile-edit-link,
:root[data-theme="dark"] .prototype-ai .thread,
:root[data-theme="dark"] .prototype-ai .chat-main,
:root[data-theme="dark"] .prototype-ai .empty-state h2,
:root[data-theme="dark"] .prototype-ai .empty-state p,
:root[data-theme="dark"] .prototype-ai .assistant-name,
:root[data-theme="dark"] .prototype-ai .assistant-name small,
:root[data-theme="dark"] .prototype-ai .msg.user .bubble,
:root[data-theme="dark"] .prototype-ai .reply,
:root[data-theme="dark"] .prototype-ai .reply-body,
:root[data-theme="dark"] .prototype-ai .reply-body strong,
:root[data-theme="dark"] .prototype-ai .reply-body b,
:root[data-theme="dark"] .prototype-ai .movie-title,
:root[data-theme="dark"] .prototype-ai .poster-title,
:root[data-theme="dark"] .prototype-ai .followup,
:root[data-theme="dark"] .prototype-ai .mode-chip,
:root[data-theme="dark"] .prototype-ai .compose textarea,
:root[data-theme="dark"] .prototype-ai .compose-footer,
:root[data-theme="dark"] .prototype-ai .compose-footer .key{color:var(--mi-text)!important}
:root[data-theme="dark"] .prototype-ai input::placeholder,
:root[data-theme="dark"] .prototype-ai textarea::placeholder{color:var(--mi-text-subtle)!important}

/* AI 助手灵动动效：精灵球呼吸与视线微追踪、侧栏滑入、思考三点律动与流式光标 */
.prototype-ai .orb-skin{--ex:0px;--ey:0px;--blink:1}
.prototype-ai .orb-skin .eye{transform:translate3d(var(--ex,0px),var(--ey,0px),0) scaleY(var(--blink,1));transition:transform .11s cubic-bezier(.22,1,.36,1)}
.prototype-ai .empty-orb .orb-skin{animation:miOrbIdleFloat 5.2s ease-in-out infinite}
.prototype-ai .orb-skin.mini.is-thinking{position:relative}
.prototype-ai .orb-skin.mini.is-thinking::after{content:"";position:absolute;inset:-4px;border-radius:50%;border:1.5px solid var(--mi-accent);opacity:.65;pointer-events:none;animation:miOrbThinkRing 1.35s ease-in-out infinite}
.prototype-ai .sidebar{transition:width .26s cubic-bezier(.22,1,.36,1),border-color .22s ease!important}
.prototype-ai .side-panel{opacity:0;transform:translate3d(-8px,0,0);transition:opacity .2s ease,transform .26s cubic-bezier(.22,1,.36,1)}
.prototype-ai .window.sidebar-open .side-panel{opacity:1;transform:translate3d(0,0,0)}
.prototype-ai .assistant-name.is-thinking small{display:inline-flex;align-items:center;gap:6px}
.prototype-ai .assistant-name.is-thinking small::before{content:"";width:6px;height:6px;border-radius:50%;background:var(--mi-accent);animation:miThinkPulse 1.3s ease-in-out infinite}
.prototype-ai .typing-dots{display:flex;gap:5px;align-items:center;height:20px;margin:2px 0 6px}
.prototype-ai .typing-dots i{display:block;width:6px;height:6px;border-radius:50%;background:var(--mi-accent);opacity:.38;animation:miTypingWave 1.15s ease-in-out infinite}
:root[data-theme="dark"] .prototype-ai .typing-dots i{background:#b8a1fb}
.prototype-ai .typing-dots i:nth-child(2){animation-delay:.16s}
.prototype-ai .typing-dots i:nth-child(3){animation-delay:.32s}
.prototype-ai .reply-body.is-writing::after{content:"";display:inline-block;width:2px;height:1em;margin-left:3px;vertical-align:-.14em;border-radius:1px;background:var(--mi-accent);animation:miStreamCaret 1.15s ease-in-out infinite}
.prototype-ai .reply-body.is-writing.is-actively-typing::after{animation:none;opacity:.88}
@keyframes miOrbIdleFloat{0%,100%{transform:translate3d(0,0,0)}50%{transform:translate3d(0,-3px,0)}}
@keyframes miOrbThinkRing{0%,100%{opacity:.35;transform:scale(.96)}50%{opacity:.9;transform:scale(1.12)}}
@keyframes miThinkPulse{0%,100%{opacity:.4;transform:scale(.82)}50%{opacity:1;transform:scale(1.1)}}
@keyframes miTypingWave{0%,60%,100%{opacity:.35;transform:translate3d(0,0,0) scale(.88)}30%{opacity:1;transform:translate3d(0,-4px,0) scale(1.12)}}
@keyframes miStreamCaret{0%,100%{opacity:.92}50%{opacity:.28}}
@keyframes miMsgEnter{from{opacity:0;transform:translate3d(0,6px,0)}to{opacity:1;transform:translate3d(0,0,0)}}
@keyframes miCardReveal{from{opacity:0;transform:translate3d(0,8px,0) scale(.985)}to{opacity:1;transform:translate3d(0,0,0) scale(1)}}
@keyframes miPopoverEnter{from{opacity:0;transform:translate3d(0,6px,0) scale(.985)}to{opacity:1;transform:translate3d(0,0,0) scale(1)}}
.prototype-ai .thinking-cue{display:none;align-items:center;gap:8px;min-height:20px;line-height:20px;margin-bottom:8px;font-size:12px;font-weight:500;letter-spacing:.01em;color:var(--mi-text,#ffffff)}
.prototype-ai .window[data-dialog-mode="gemini"] .thinking-cue{display:inline-flex}
.prototype-ai .thinking-cue .thinking-dot{width:8px;height:8px;border-radius:50%;flex:none;background:radial-gradient(circle at 35% 35%,#e7dcff,#9c7aff 70%);box-shadow:0 0 10px rgba(167,139,250,.55);animation:miThinkPulse 1.5s ease-in-out infinite}
:root[data-theme="light"] .prototype-ai .thinking-cue .thinking-dot{background:radial-gradient(circle at 35% 35%,#e2bc87,#8c6226 72%);box-shadow:0 0 8px rgba(140,98,38,.36)}
:root[data-theme="light"] .prototype-ai .orb-skin.mini.is-thinking::after{border-color:var(--mi-accent);box-shadow:0 0 0 2px var(--mi-accent-soft)}
:root[data-theme="light"] .prototype-ai .assistant-name.is-thinking small::before{background:#8c6226}
.prototype-ai .thread .msg{animation:miMsgEnter .18s cubic-bezier(.22,1,.36,1) both}
.prototype-ai .empty-state{animation:miMsgEnter .24s cubic-bezier(.22,1,.36,1) both}
.prototype-ai .cards-rail .movie-card{animation:miCardReveal .24s cubic-bezier(.22,1,.36,1) both;animation-delay:calc(var(--mi-card-i,0) * 45ms)}
.prototype-ai .composer-shortcuts:not([hidden]),.prototype-ai .profile-panel:not([hidden]){transform-origin:bottom center;animation:miPopoverEnter .16s cubic-bezier(.22,1,.36,1) both}
.prototype-ai .mi14-controls button[data-source-id="sendBtn"]{transition:transform .16s cubic-bezier(.22,1,.36,1),opacity .16s ease,background-color .16s ease}
.prototype-ai .mi14-controls button[data-source-id="sendBtn"]:hover:not(:disabled){transform:scale(1.04)}
.prototype-ai .mi14-controls button[data-source-id="sendBtn"]:active:not(:disabled){transform:scale(.93)}
.prototype-ai .mode-chip,.prototype-ai .response-action,.prototype-ai .side-row,.prototype-ai .followup,.prototype-ai .icon-btn{transition:background-color .16s ease,border-color .16s ease,color .16s ease,transform .14s ease}
.prototype-ai .followup:hover{transform:translateY(-1px)}
.prototype-ai .followup:active{transform:translateY(0) scale(.985)}
.prototype-ai .turn-fault{margin-top:8px;padding:12px 14px;border-radius:14px;border:1px solid var(--mi-border,rgba(241,236,226,.14));background:rgba(255,255,255,.035);display:flex;flex-direction:column;gap:8px;max-width:640px}
.prototype-ai .turn-fault.is-error{border-color:var(--mi-negative-border,rgba(232,122,122,.48));background:var(--mi-negative-soft,rgba(204,76,76,.12))}
.prototype-ai .turn-fault.is-interrupted{border-color:var(--mi-accent-border,rgba(200,164,106,.45));background:var(--mi-warning-soft,rgba(200,164,106,.10))}
.prototype-ai .turn-fault-text{margin:0;font-size:13.5px;line-height:1.65;color:var(--mi-text,#ffffff)}
.prototype-ai .turn-fault-note{margin:0;font-size:12px;line-height:1.6;color:var(--mi-text-subtle,rgba(255,255,255,.68))}
.prototype-ai .turn-fault-actions{display:flex;flex-wrap:wrap;gap:8px;align-items:center;margin-top:2px}
.prototype-ai .turn-fault-btn{display:inline-flex;align-items:center;gap:6px;min-height:32px;padding:5px 12px;border-radius:999px;border:1px solid var(--mi-border-strong,rgba(241,236,226,.24));background:rgba(255,255,255,.06);color:var(--mi-text,#ffffff);font-size:12.5px;font-weight:500;transition:background-color .16s ease,border-color .16s ease,transform .14s ease,opacity .16s ease}
.prototype-ai .turn-fault-btn:hover:not(:disabled){background:rgba(255,255,255,.12);border-color:var(--mi-accent-border,rgba(200,164,106,.45));transform:translateY(-1px)}
.prototype-ai .turn-fault-btn:active:not(:disabled){transform:translateY(0) scale(.98)}
.prototype-ai .turn-fault-btn.is-primary{border-color:var(--mi-accent-border,rgba(200,164,106,.45));background:var(--mi-accent-soft,rgba(200,164,106,.12));color:var(--mi-accent-text,#DCC49B)}
.prototype-ai .turn-fault-btn.is-primary:hover:not(:disabled){background:rgba(200,164,106,.22)}
.prototype-ai .turn-fault-btn .ico{width:14px;height:14px}
:root[data-theme="light"] .prototype-ai .turn-fault{background:var(--mi-surface);border-color:var(--mi-border-strong)}
:root[data-theme="light"] .prototype-ai .turn-fault.is-error{background:var(--mi-negative-soft);border-color:var(--mi-negative-border)}
:root[data-theme="light"] .prototype-ai .turn-fault.is-interrupted{background:var(--mi-warning-soft);border-color:var(--mi-accent-border)}
:root[data-theme="light"] .prototype-ai .turn-fault-text{color:var(--mi-text)}
:root[data-theme="light"] .prototype-ai .turn-fault-note{color:var(--mi-text-subtle)}
:root[data-theme="light"] .prototype-ai .turn-fault-btn{background:var(--mi-surface);border-color:var(--mi-border-strong);color:var(--mi-text)}
:root[data-theme="light"] .prototype-ai .turn-fault-btn:hover:not(:disabled){background:var(--mi-surface-hover);border-color:var(--mi-accent)}
:root[data-theme="light"] .prototype-ai .turn-fault-btn.is-primary{background:var(--mi-accent-soft);border-color:var(--mi-accent-border);color:var(--mi-accent-text)}
:root[data-theme="light"] .prototype-ai .turn-fault-btn.is-primary:hover:not(:disabled){background:rgba(140,98,38,.18)}
.prototype-ai .scroll-latest-anchor{position:absolute;left:0;right:0;bottom:calc(100% + 12px);z-index:8;display:flex;justify-content:center;pointer-events:none}
.prototype-ai .scroll-latest-pill{pointer-events:auto;display:inline-flex;align-items:center;gap:6px;min-height:32px;padding:6px 13px;border-radius:999px;border:1px solid var(--mi-border-strong,rgba(241,236,226,.22));background:rgba(20,21,25,.88);backdrop-filter:blur(10px);color:var(--mi-text,#ffffff);font-size:12px;font-weight:550;box-shadow:0 8px 24px rgba(0,0,0,.42);transition:opacity .18s ease,transform .18s cubic-bezier(.22,1,.36,1),background-color .16s ease,border-color .16s ease}
.prototype-ai .scroll-latest-pill:hover{background:var(--mi-surface-hover,#202127);border-color:var(--mi-accent-border,rgba(200,164,106,.45));transform:translate3d(0,-1px,0)}
.prototype-ai .scroll-latest-pill:active{transform:translate3d(0,0,0) scale(.97)}
.prototype-ai .scroll-latest-arrow{font-size:13px;line-height:1;color:var(--mi-accent-text,#DCC49B)}
:root[data-theme="light"] .prototype-ai .scroll-latest-pill{background:rgba(255,255,255,.92);border-color:var(--mi-border-strong);color:var(--mi-text);box-shadow:var(--mi-shadow-md)}
:root[data-theme="light"] .prototype-ai .scroll-latest-pill:hover{background:var(--mi-surface-hover);border-color:var(--mi-accent)}
:root[data-theme="light"] .prototype-ai .scroll-latest-arrow{color:var(--mi-accent-text)}
.prototype-ai .mi-scroll-pill-enter-active,.prototype-ai .mi-scroll-pill-leave-active{transition:opacity .18s ease,transform .18s cubic-bezier(.22,1,.36,1)}
.prototype-ai .mi-scroll-pill-enter-from,.prototype-ai .mi-scroll-pill-leave-to{opacity:0;transform:translate3d(0,8px,0) scale(.96)}
:is(.pref-reduced-motion .prototype-ai,.prototype-ai.pref-reduced-motion) *,:is(.pref-reduced-motion .prototype-ai,.prototype-ai.pref-reduced-motion) *::before,:is(.pref-reduced-motion .prototype-ai,.prototype-ai.pref-reduced-motion) *::after{animation:none!important;transition:none!important;scroll-behavior:auto!important}
@media (prefers-reduced-motion:reduce){.prototype-ai *,.prototype-ai *::before,.prototype-ai *::after{animation:none!important;transition:none!important;scroll-behavior:auto!important}}
:is(.pref-reduced-motion .prototype-ai,.prototype-ai.pref-reduced-motion) .followup:hover,:is(.pref-reduced-motion .prototype-ai,.prototype-ai.pref-reduced-motion) .followup:active,:is(.pref-reduced-motion .prototype-ai,.prototype-ai.pref-reduced-motion) .turn-fault-btn:hover,:is(.pref-reduced-motion .prototype-ai,.prototype-ai.pref-reduced-motion) .turn-fault-btn:active,:is(.pref-reduced-motion .prototype-ai,.prototype-ai.pref-reduced-motion) .scroll-latest-pill:hover,:is(.pref-reduced-motion .prototype-ai,.prototype-ai.pref-reduced-motion) .scroll-latest-pill:active,:is(.pref-reduced-motion .prototype-ai,.prototype-ai.pref-reduced-motion) button[data-source-id="sendBtn"]:hover,:is(.pref-reduced-motion .prototype-ai,.prototype-ai.pref-reduced-motion) button[data-source-id="sendBtn"]:active{transform:none!important}
@media(prefers-reduced-motion:reduce){.prototype-ai .followup:hover,.prototype-ai .followup:active,.prototype-ai .turn-fault-btn:hover,.prototype-ai .turn-fault-btn:active,.prototype-ai .scroll-latest-pill:hover,.prototype-ai .scroll-latest-pill:active,.prototype-ai button[data-source-id="sendBtn"]:hover,.prototype-ai button[data-source-id="sendBtn"]:active{transform:none!important}}
</style>
<style scoped src="../styles/assistant-avatar.css"></style>
