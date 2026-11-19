import type {ObjectDirective} from 'vue'

const BATCH_WINDOW_MS=50
const MAX_BATCH=50
const CONSERVATIVE_COOLDOWN_MS=60_000
const MAX_RETRY_AFTER_MS=24*60*60*1000
type FeedbackConfig={channel:string;period?:string;context:string;items:any[];boardVersion?:string}
type PendingEntry={position:number;movie_id:number|null;tmdb_id:number|null;epoch:number;resolve:(ok:boolean)=>void}
type BoardState={requestId:string;config:FeedbackConfig;pending:Map<number,Promise<boolean>>;entries:PendingEntry[];timer?:ReturnType<typeof setTimeout>;disposed:boolean}
let queue:Promise<unknown>=Promise.resolve()
let cooldownUntil=0
let requestEpoch=0

function enqueue<T>(operation:()=>Promise<T>):Promise<T>{
 const task=queue.then(operation)
 queue=task.then(()=>undefined,()=>undefined)
 return task
}
function cooldownActive(){
 if(cooldownUntil&&cooldownUntil<=Date.now())cooldownUntil=0
 return cooldownUntil>Date.now()
}
function canSend(board:BoardState,epoch:number){return !board.disposed&&epoch===requestEpoch&&!cooldownActive()}
function retryAfterDelay(value:string|null){
 const raw=value?.trim()
 if(raw&&/^\d+$/.test(raw)){
  const seconds=Number(raw)
  if(Number.isFinite(seconds)&&seconds>=0){
   // Defensive cap keeps the deadline finite even for a syntactically valid
   // but absurdly large seconds value; it never becomes an infinite cooldown.
   return Math.max(1000,Math.min(MAX_RETRY_AFTER_MS,seconds*1000))
  }
 }
 if(raw){
  const at=Date.parse(raw)
  if(Number.isFinite(at)&&at>Date.now())return Math.max(1000,at-Date.now())
 }
 // Missing, invalid, or already-expired server dates: conservative local backoff,
 // never presented as a server-specified deadline.
 return CONSERVATIVE_COOLDOWN_MS
}
function retryAfterHeader(response:any){
 return typeof response?.headers?.get==='function'?response.headers.get('Retry-After'):null
}
function enterCooldown(response:any){
 const deadline=Date.now()+retryAfterDelay(retryAfterHeader(response))
 if(Number.isFinite(deadline))cooldownUntil=Math.max(cooldownUntil,deadline)
 // Invalidate batches queued before the throttle signal; they must not be written
 // later when the cooldown expires.
 requestEpoch++
}
async function sendWithCsrf(path:string,body:unknown,board:BoardState,epoch:number):Promise<boolean>{
 for(let attempt=0;attempt<2;attempt++){
  if(!canSend(board,epoch))return false
  let csrf:Response
  try{csrf=await fetch('/api/v1/auth/csrf',{credentials:'same-origin',signal:AbortSignal.timeout(3000)})}catch{return false}
  if(csrf.status===429){enterCooldown(csrf);return false}
  if(!csrf.ok)return false
  let token:string|undefined
  try{token=(await csrf.json())?.data?.csrf_token}catch{return false}
  if(!token)return false
  // dispose/cooldown can happen while the CSRF request is in flight.
  if(!canSend(board,epoch))return false
  let response:Response
  try{response=await fetch('/api/catalog/'+path,{method:'POST',credentials:'same-origin',signal:AbortSignal.timeout(3000),headers:{'Content-Type':'application/json','X-CSRF-Token':token},body:JSON.stringify(body)})}catch{return false}
  if(response.status===429){enterCooldown(response);return false}
  let result:any
  try{result=await response.json()}catch{return false}
  // Retry only an explicit rejection, never an unknown commit result or a 429.
  if(response.status===403&&result?.code==='REQUEST_VERIFICATION_FAILED'&&attempt===0)continue
  return response.ok&&result?.code==='OK'
 }
 return false
}
function settle(entries:PendingEntry[],ok:boolean){for(const entry of entries)entry.resolve(ok)}
function schedule(board:BoardState){
 if(board.disposed||board.timer!==undefined)return
 board.timer=setTimeout(()=>{board.timer=undefined;void flush(board)},BATCH_WINDOW_MS)
}
async function flush(board:BoardState){
 if(board.disposed||cooldownActive()){settle(board.entries.splice(0),false);return}
 const currentEpoch=requestEpoch
 const all=board.entries.splice(0)
 const stale=all.filter(entry=>entry.epoch!==currentEpoch)
 settle(stale,false)
 const fresh=all.filter(entry=>entry.epoch===currentEpoch)
 const entries=fresh.slice(0,MAX_BATCH)
 if(fresh.length>MAX_BATCH)board.entries.unshift(...fresh.slice(MAX_BATCH))
 if(!entries.length)return
 const body={request_id:board.requestId,channel:board.config.channel,period:board.config.period||null,board_version:board.config.boardVersion||'visible-v2',context:board.config.context,items:entries.map(entry=>({position:entry.position,movie_id:entry.movie_id,tmdb_id:entry.tmdb_id}))}
 const ok=await enqueue(()=>sendWithCsrf('recommendation-impressions',body,board,currentEpoch))
 settle(entries,ok)
 if(board.entries.length)schedule(board)
}
function record(board:BoardState,position:number){
 // The server contract is an integer 1..50, independent of how many items the
 // local board holds. Reject 51st/fractional/NaN positions before enqueueing.
 if(board.disposed||!Number.isInteger(position)||position<1||position>Math.min(MAX_BATCH,board.config.items.length))return Promise.resolve(false)
 const existing=board.pending.get(position)
 if(existing)return existing
 if(cooldownActive())return Promise.resolve(false)
 const movie=board.config.items[position-1]
 const mid=Number(movie?.movie_id),tid=Number(movie?.tmdb_id)
 const hasMid=Number.isSafeInteger(mid)&&mid>0,hasTid=Number.isSafeInteger(tid)&&tid>0
 if(!hasMid&&!hasTid)return Promise.resolve(false)
 let resolve!:(ok:boolean)=>void
 const promise=new Promise<boolean>(done=>{resolve=done})
 board.pending.set(position,promise)
 board.entries.push({position,movie_id:hasMid?mid:null,tmdb_id:hasMid?null:tid,epoch:requestEpoch,resolve})
 schedule(board)
 return promise
}
async function click(board:BoardState,position:number){
 const ok=await record(board,position)
 if(!ok||board.disposed)return
 const epoch=requestEpoch
 await enqueue(()=>sendWithCsrf('recommendation-clicks',{request_id:board.requestId,position},board,epoch))
}
export function createFeedbackBoard(config:FeedbackConfig){
 const board:BoardState={requestId:crypto.randomUUID(),config,pending:new Map(),entries:[],disposed:false}
 return {
  record:(position:number)=>record(board,position),
  click:(position:number)=>click(board,position),
  dispose(){if(board.disposed)return;board.disposed=true;if(board.timer!==undefined){clearTimeout(board.timer);board.timer=undefined}settle(board.entries.splice(0),false)},
 }
}
type Binding={board:ReturnType<typeof createFeedbackBoard>|undefined;position:number}
const cleanups=new WeakMap<Element,()=>void>()
export const vFeedback:ObjectDirective<HTMLElement,Binding>={
 mounted(el,binding){
  let timer:ReturnType<typeof setTimeout>|undefined
  const observer=new IntersectionObserver(entries=>{
   const visible=entries.some(e=>e.isIntersecting&&e.intersectionRatio>=0.5)
   if(timer)clearTimeout(timer)
   if(visible)timer=setTimeout(()=>{void binding.value.board?.record(binding.value.position);observer.disconnect()},1000)
  },{threshold:[0,0.5]})
  observer.observe(el)
  cleanups.set(el,()=>{if(timer)clearTimeout(timer);observer.disconnect()})
 },
 beforeUnmount(el){cleanups.get(el)?.();cleanups.delete(el)},
}