import {reactive} from 'vue'

// Shared, per-account movie behavior snapshot for the chat surfaces.
// Business state only: drafts, sessions, busy flags and request controllers stay per instance.
export type BehaviorKind='favorite'|'want'
export type MovieBehavior={favorite?:boolean;want?:boolean;watched?:boolean;not_interested?:boolean;rating?:number;watched_at?:string|null}
export type BehaviorSnapshot=Record<string,MovieBehavior>
export type BehaviorResult={ok:boolean;message:string}

export const movieBehavior=reactive({owner:0,loaded:false,loading:false,error:'',version:0,movies:{} as BehaviorSnapshot})

// Reactive on purpose: chat buttons must disable/re-enable the moment a write starts,
// finishes, or an unknown result locks the movie in another window.
const pending=reactive(new Set<string>())
const unsynced=reactive(new Set<string>())

type ReadTicket={owner:number,epoch:number,seq:number,batch:number,writeSeq:number,force:boolean}
let ownerEpoch=0
let writeSeq=0
let readSeq=0
let activeRead:{promise:Promise<boolean>,ticket:ReadTicket}|null=null
let refreshBatch=0
let batchQueued=false
let internalEvent=false

const lockedKey=(owner:number,movieId:number)=>owner+':'+movieId

const savedText:Record<BehaviorKind,string>={favorite:'已加入收藏',want:'已加入想看'}
const undoneText:Record<BehaviorKind,string>={favorite:'已取消收藏',want:'已撤销想看'}
const unavailable='电影行为状态暂时不可用，收藏与想看已暂停，请稍后重试。'

// A monotonic epoch, not the account number: A→B→A must invalidate A's older requests,
// otherwise a late response from the first A session could paint the second one.
export function setBehaviorOwner(owner:number){
 if(movieBehavior.owner===owner)return
 ownerEpoch+=1
 writeSeq+=1
 movieBehavior.owner=owner
 movieBehavior.movies={}
 movieBehavior.loaded=false
 movieBehavior.loading=false
 movieBehavior.error=''
 movieBehavior.version=0
 pending.clear()
 unsynced.clear()
 activeRead=null
}

export function behaviorState(movieId:number){
 const record=movieBehavior.movies[String(movieId)]||{}
 return {want:record.want===true,favorite:record.favorite===true,watched:record.watched===true,notInterested:record.not_interested===true,rating:typeof record.rating==='number'?record.rating:null}
}

export function behaviorReady(){return movieBehavior.owner>0&&movieBehavior.loaded&&!movieBehavior.error}
export function behaviorPending(movieId:number){return pending.has(lockedKey(movieBehavior.owner,movieId))}
export function behaviorUnsynced(movieId:number){return unsynced.has(lockedKey(movieBehavior.owner,movieId))}
export function behaviorErrorMessage(){return movieBehavior.error}
export function consumeInternalBehaviorEvent(){return internalEvent}
export function behaviorEpoch(){return ownerEpoch}

export function behaviorLockReason(movieId:number):string{
 if(!movieBehavior.owner)return '登录后可用'
 if(!behaviorReady())return movieBehavior.error||'行为状态正在读取'
 if(behaviorPending(movieId))return '正在保存上一次操作'
 if(behaviorUnsynced(movieId))return '上次提交结果未确认，请先到电影详情页核对'
 return ''
}

// Reads started in the same synchronous task (one event, two chat listeners) share one
// request. A forced read never joins an older in-flight read, so a refresh is not a fake sync.
function currentBatch(){
 if(!batchQueued){batchQueued=true;queueMicrotask(()=>{batchQueued=false;refreshBatch+=1})}
 return refreshBatch
}

function ticketValid(ticket:ReadTicket){
 return ticket.epoch===ownerEpoch&&ticket.owner===movieBehavior.owner&&ticket.seq===readSeq&&ticket.writeSeq===writeSeq
}

export function readBehavior(owner:number,options:{force?:boolean;silent?:boolean}={}):Promise<boolean>{
 if(!owner)return Promise.resolve(false)
 const force=!!options.force
 if(activeRead&&activeRead.ticket.owner===owner&&activeRead.ticket.epoch===ownerEpoch){
  if(!force)return activeRead.promise
  if(activeRead.ticket.force&&activeRead.ticket.batch===currentBatch())return activeRead.promise
 }
 const ticket:ReadTicket={owner,epoch:ownerEpoch,seq:++readSeq,batch:currentBatch(),writeSeq,force}
 if(movieBehavior.owner===owner)movieBehavior.loading=true
 const task=(async()=>{
  try{
   const r=await fetch('/api/catalog/behavior',{credentials:'same-origin'})
   if(!ticketValid(ticket))return false
   if(!r.ok){
    if(options.silent)return false
    movieBehavior.loaded=false
    movieBehavior.error=r.status===401?'登录已失效，请重新登录。':unavailable
    return false
   }
   const body=await r.json()
   if(!ticketValid(ticket))return false
   const snapshot=body?.data?.state
   if(!snapshot||typeof snapshot!=='object'){
    if(options.silent)return false
    movieBehavior.loaded=false
    movieBehavior.error=unavailable
    return false
   }
   movieBehavior.movies=snapshot as BehaviorSnapshot
   movieBehavior.loaded=true
   movieBehavior.error=''
   movieBehavior.version+=1
   return true
  }catch{
   if(ticketValid(ticket)&&!options.silent){
    movieBehavior.loaded=false
    movieBehavior.error='网络异常，电影行为状态未能读取，收藏与想看已暂停，请稍后重试。'
   }
   return false
  }finally{
   if(ticketValid(ticket))movieBehavior.loading=false
   if(activeRead&&activeRead.ticket===ticket)activeRead=null
  }
 })()
 activeRead={promise:task,ticket}
 return task
}

function applyTransition(movieId:number,kind:BehaviorKind,undo:boolean){
 const k=String(movieId)
 const current:MovieBehavior={...(movieBehavior.movies[k]||{})}
 if(undo)delete (current as Record<string,unknown>)[kind]
 else (current as Record<string,unknown>)[kind]=true
 if(Object.keys(current).length)movieBehavior.movies[k]=current
 else delete movieBehavior.movies[k]
}

function dispatchBehaviorChanged(){
 if(typeof window==='undefined'||typeof window.dispatchEvent!=='function')return
 internalEvent=true
 try{window.dispatchEvent(new Event('mi2-behavior-changed'))}finally{internalEvent=false}
}

// One explicit click produces at most one write. A second click for the same account+movie
// while a write or its reconcile is in flight is ignored rather than queued.
export async function toggleBehavior(movieId:number,kind:BehaviorKind):Promise<BehaviorResult>{
 const epoch=ownerEpoch
 const owner=movieBehavior.owner
 if(!owner)return {ok:false,message:'登录已失效，请重新登录。'}
 const locked=behaviorLockReason(movieId)
 if(locked)return {ok:false,message:locked.endsWith('。')?locked:locked+'。'}
 const id=lockedKey(owner,movieId)
 const undo=behaviorState(movieId)[kind]
 pending.add(id)
 try{
  let csrf=await fetch('/api/v1/auth/csrf',{credentials:'same-origin'})
  if(epoch!==ownerEpoch)return {ok:false,message:'账户已切换，本次操作未提交。'}
  if(!csrf.ok)return {ok:false,message:'登录状态已失效，请重新登录。'}
  let token=(await csrf.json())?.data?.csrf_token
  if(epoch!==ownerEpoch)return {ok:false,message:'账户已切换，本次操作未提交。'}
  if(!token)return {ok:false,message:'请求验证未能获取，本次未提交。'}
  const payload={event_id:crypto.randomUUID(),movie_id:movieId,kind,action:undo?'undo':'set',value:null,time:new Date().toISOString(),source:'USER_ACTION'}
  for(let attempt=0;attempt<2;attempt++){
   if(epoch!==ownerEpoch)return {ok:false,message:'账户已切换，本次操作未提交。'}
   const r=await fetch('/api/catalog/behavior',{method:'POST',credentials:'same-origin',headers:{'Content-Type':'application/json','X-CSRF-Token':token},body:JSON.stringify(payload)})
   let body:any=null
   try{body=await r.json()}catch{}
   if(epoch!==ownerEpoch)return {ok:false,message:'账户已切换，本次结果未采用。'}
   if(r.ok&&body?.code==='OK'){
    applyTransition(movieId,kind,undo)
    writeSeq+=1
    unsynced.delete(id)
    dispatchBehaviorChanged()
    const text=undo?undoneText[kind]:savedText[kind]
    // Other pages write the same account concurrently, so the local transition is only a
    // hint: re-read the authoritative snapshot, and never let an older read overwrite it.
    const synced=await readBehavior(owner,{force:true,silent:true})
    if(epoch!==ownerEpoch)return {ok:true,message:text}
    return {ok:true,message:synced?text:text+'（已保存，状态刷新未确认，请稍后手动刷新核对）'}
   }
   if(r.status===403&&body?.code==='REQUEST_VERIFICATION_FAILED'&&attempt===0){
    // Only an explicit verification rejection is retried, with the same event id.
    csrf=await fetch('/api/v1/auth/csrf',{credentials:'same-origin'})
    if(epoch!==ownerEpoch)return {ok:false,message:'账户已切换，本次操作未提交。'}
    token=csrf.ok?(await csrf.json())?.data?.csrf_token:''
    if(!token)return {ok:false,message:'请求验证已失效，本次未保存，请刷新后重试。'}
    continue
   }
   if(r.status===409||body?.code==='STATE_CONFLICT')return {ok:false,message:'与已看或不感兴趣状态冲突，请在电影详情页调整；这里不会代你撤销其他状态。'}
   if(r.status===401||body?.code==='AUTHENTICATION_REQUIRED')return {ok:false,message:'登录已失效，请重新登录。'}
   unsynced.add(id)
   return {ok:false,message:'保存结果未确认，请到电影详情页核对；不会自动重复提交。'}
  }
  unsynced.add(id)
  return {ok:false,message:'保存结果未确认，请到电影详情页核对；不会自动重复提交。'}
 }catch{
  if(epoch===ownerEpoch)unsynced.add(id)
  return {ok:false,message:'网络中断，保存结果未确认，请到电影详情页核对；不会自动重试。'}
 }finally{
  // Only the generation that took the lock may release it: A's stale attempt returning
  // after A→B→A must not clear the newer A session's pending lock for the same movie.
  if(epoch===ownerEpoch)pending.delete(id)
 }
}
