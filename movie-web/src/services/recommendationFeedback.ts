import type {ObjectDirective} from 'vue'
let queue:Promise<unknown>=Promise.resolve()
function post(path:string,body:unknown):Promise<boolean>{
 const task=queue.then(()=>send(path,body));queue=task.catch(()=>false);return task
}
async function send(path:string, body:unknown):Promise<boolean> {
 for(let attempt=0;attempt<2;attempt++) {
  try {
   const r=await fetch('/api/v1/auth/csrf',{credentials:'same-origin',signal:AbortSignal.timeout(3000)})
   if(!r.ok)return false
   const token=(await r.json())?.data?.csrf_token
   if(!token)return false
   const response=await fetch('/api/catalog/'+path,{method:'POST',credentials:'same-origin',signal:AbortSignal.timeout(3000),headers:{'Content-Type':'application/json','X-CSRF-Token':token},body:JSON.stringify(body)})
   const result=await response.json()
   // Retry only an explicit rejection, never an unknown commit result.
   if(response.status===403&&result.code==='REQUEST_VERIFICATION_FAILED'&&attempt===0)continue
   return response.ok&&result.code==='OK'
  }catch{return false}
 }
 return false
}
export function createFeedbackBoard(config:{channel:string;period?:string;context:string;items:any[];boardVersion?:string}) {
 const requestId=crypto.randomUUID(),pending=new Map<number,Promise<boolean>>()
 let disposed=false
 function record(position:number) {
  if(disposed||position<1||position>config.items.length)return Promise.resolve(false)
  if(!pending.has(position)) {
   const m=config.items[position-1],mid=Number(m.movie_id),tid=Number(m.tmdb_id)
   if(!(Number.isSafeInteger(mid)&&mid>0)&&!(Number.isSafeInteger(tid)&&tid>0))return Promise.resolve(false)
   pending.set(position,post('recommendation-impressions',{request_id:requestId,channel:config.channel,period:config.period||null,board_version:config.boardVersion||'visible-v2',context:config.context,items:[{position,movie_id:mid>0?mid:null,tmdb_id:mid>0?null:tid}]}))
  }
  return pending.get(position)!
 }
 return {record,async click(position:number){if(await record(position))await post('recommendation-clicks',{request_id:requestId,position})},dispose(){disposed=true}}
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
