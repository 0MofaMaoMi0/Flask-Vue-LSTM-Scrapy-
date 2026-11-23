<script setup lang="ts">
import {onMounted,onBeforeUnmount,ref,computed} from 'vue'
import {fetchJsonWithTimeout,mapQualityError} from '../services/catalogResilience'
const report=ref<any>(),run=ref<any>(),busy=ref(false),error=ref(''),pollError=ref(''),notice=ref(''),confirm=ref(false)
const ITEM_STEP=50,itemLimit=ref(ITEM_STEP),detailsOpen=ref(false)
const runItems=computed(()=>Array.isArray(run.value?.items)?run.value.items:[])
const visibleRunItems=computed(()=>detailsOpen.value?runItems.value.slice(0,itemLimit.value):[])
const totals=computed(()=>{const rows=runItems.value;return {saved:rows.filter((i:any)=>i.status==='SUCCEEDED').length,held:rows.filter((i:any)=>['REQUIRES_REVIEW','FAILED','NEEDS_REVIEW'].includes(i.status)).length}})
const abort=new AbortController();let loadController:AbortController|undefined,loadSeq=0,timer:ReturnType<typeof setTimeout>|undefined
const isActiveRun=(status?:string)=>['RUNNING','QUEUED','PENDING'].includes(String(status||''))
function clearPollTimer(){if(timer){clearTimeout(timer);timer=undefined}}
function schedulePoll(){clearPollTimer();if(abort.signal.aborted||document.hidden||!isActiveRun(run.value?.status))return;timer=setTimeout(poll,5000)}
async function api(path:string,data?:unknown,outerSignal?:AbortSignal){
 const linked=new AbortController()
 const abortLinked=()=>linked.abort()
 if(abort.signal.aborted||outerSignal?.aborted){
  linked.abort()
 }else{
  abort.signal.addEventListener('abort',abortLinked,{once:true})
  outerSignal?.addEventListener('abort',abortLinked,{once:true})
 }
 try{
  let token=''
  if(data!==undefined){
   const {response:r,body:cb}=await fetchJsonWithTimeout('/api/v1/auth/csrf',{
    credentials:'same-origin',
    signal:linked.signal,
    timeoutMs:30_000,
    timeoutMessage:'质量盘点请求超时，请稍后刷新重试。',
   })
   token=cb?.data?.csrf_token||''
   if(!r.ok||!token||(cb?.code!==0&&cb?.code!=='OK'))throw new Error('登录已失效，请重新登录。')
  }
  const {response:r,body}=await fetchJsonWithTimeout('/api/admin/imports/quality'+path,{
   credentials:'same-origin',
   signal:linked.signal,
   timeoutMs:30_000,
   timeoutMessage:'质量盘点读取超时，请稍后刷新重试。',
   ...(data!==undefined?{method:'POST',headers:{'Content-Type':'application/json','X-CSRF-Token':token},body:JSON.stringify(data)}:{}),
  })
  if(!r.ok)throw new Error(mapQualityError(r.status,body?.code))
  if(!body||body.code!=='OK'||body.data==null)throw new Error('核验响应格式异常，请稍后刷新。')
  return body.data
 }finally{
  abort.signal.removeEventListener('abort',abortLinked)
  outerSignal?.removeEventListener('abort',abortLinked)
 }
}
async function load(){
 const ticket=++loadSeq;loadController?.abort();const current=new AbortController();loadController=current
 busy.value=true;error.value=''
 try{
  const [repRes,runsRes]=await Promise.allSettled([
   api('',undefined,current.signal),
   api('/runs',undefined,current.signal),
  ])
  if(ticket!==loadSeq||abort.signal.aborted||current.signal.aborted)return
  const errors:string[]=[]
  if(repRes.status==='fulfilled'){
   report.value=repRes.value
  }else{
   errors.push((repRes.reason as Error)?.message||'质量盘点读取失败，请稍后重试。')
  }
  if(runsRes.status==='fulfilled'){
   run.value=runsRes.value
   schedulePoll()
  }else{
   errors.push((runsRes.reason as Error)?.message||'核验任务记录读取失败，请稍后重试。')
  }
  if(errors.length)error.value=errors.join('；')
 }finally{
  if(ticket===loadSeq)busy.value=false
 }
}
async function start(){busy.value=true;error.value='';notice.value='';try{run.value=await api('/runs',{scope_digest:report.value.scope_digest,confirm:true});confirm.value=false;itemLimit.value=ITEM_STEP;schedulePoll()}catch(e){error.value=(e as Error).message}finally{busy.value=false}}
async function cancel(){busy.value=true;error.value='';try{await api('/runs/cancel',{});notice.value='已请求停止，将在当前影片完成后停止后续工作；已提交数据保留。';schedulePoll()}catch(e){error.value=(e as Error).message}finally{busy.value=false}}
async function resume(){busy.value=true;error.value='';notice.value='';try{run.value=await api('/runs/resume',{job_id:run.value.job_id,confirm:true});schedulePoll()}catch(e){error.value=(e as Error).message}finally{busy.value=false}}
async function poll(){try{const last=run.value?.status;run.value=await api('/runs');if(isActiveRun(last)&&!isActiveRun(run.value?.status))report.value=await api('');pollError.value=''}catch(e){if(!abort.signal.aborted)pollError.value=(e as Error).message}finally{schedulePoll()}}
function onVisibility(){if(document.hidden){clearPollTimer()}else if(!abort.signal.aborted&&isActiveRun(run.value?.status)){void poll()}}
function onToggleDetails(e:Event){detailsOpen.value=(e.target as HTMLDetailsElement).open;if(!detailsOpen.value)itemLimit.value=ITEM_STEP}
const labels:Record<string,string>={RUNNING:'核验进行中',COMPLETED:'本轮核验结束',STOPPED:'核验已停止'}
const itemLabels:Record<string,string>={SUCCEEDED:'资料补充完成',REQUIRES_REVIEW:'身份或资料需要复核，未提交',FAILED:'导入失败',NEEDS_REVIEW:'提交结果需要核对',IMPORT_PREPARED:'已封存，正在确认导入结果',VERIFIED_EXTERNAL_MOVIE_NOT_IMPORTED:'库外电影详情已核验，未入库',POSSIBLE_EXISTING_MOVIE_REQUIRES_REVIEW:'可能与现有影片重复，需复核',CANDIDATE_REQUIRES_REVIEW:'发现电影候选，未绑定',SEARCH_NO_MOVIE_CANDIDATE:'本轮电影搜索未命中'}
onMounted(async()=>{document.addEventListener('visibilitychange',onVisibility);await load()});onBeforeUnmount(()=>{++loadSeq;loadController?.abort();abort.abort();clearPollTimer();document.removeEventListener('visibilitychange',onVisibility)})
defineExpose({refresh:load})
</script>
<template><section class="quality-panel" aria-labelledby="quality-title">
 <header><h2 id="quality-title">数据质量核验</h2><button type="button" :disabled="busy" @click="load">刷新盘点</button></header>
 <p v-if="error||pollError" role="alert" class="error">{{error||pollError}}</p>
 <p v-if="notice" role="status">{{notice}}</p><p v-if="run?.status==='RUNNING'&&run.network_wait" role="status">TMDB 网络暂不稳定，等待后再次读取公开资料（第 {{run.network_wait.attempt}} 次）；已提交的数据库写入不会重发。</p>
 <p v-if="!report&&!error" role="status">正在盘点电影身份、评分及票房来源…</p>
 <template v-if="report"><p>当前 {{report.counts.active.toLocaleString()}} 部影片 · TMDB 已映射 {{report.counts.mapped.toLocaleString()}} 部 · 身份待核验 {{report.counts.identity_gaps}} 部 · 榜单库外候选 {{report.counts.pending}} 部。</p>
 <p>有投票支持的 TMDB 评分 {{report.counts.reliable_tmdb_rating.toLocaleString()}} 部；已知累计票房 {{report.counts.known_revenue.toLocaleString()}} 部。0 或缺失票房保留为未知，不代表没有收入。</p>
 <template v-if="run?.status!=='RUNNING'"><label class="quality-confirm"><input v-model="confirm" type="checkbox" :disabled="busy"/>核验 {{report.counts.identity_gaps}} 个身份缺口和 {{report.counts.pending}} 个库外候选；仅对 {{report.counts.refresh_candidates}} 部已有 TMDB 映射且资料不全的影片补充来源快照、评分、票数与票房。身份冲突跳过，不新增电影、不自动绑定候选。</label><button type="button" :disabled="busy||!confirm" @click="start">{{busy?'正在提交…':'开始本轮核验与可靠资料补充'}}</button></template></template>
 <div v-if="run" role="status" class="quality-status"><strong>{{labels[run.status]||run.status}}</strong><span>{{run.completed}} / {{run.total}} 项</span><p>已补充 {{totals.saved}} 部影片；{{totals.held}} 项暂停或需要复核。核验结束不代表所有身份缺口已解决。</p><p v-if="run.error_code">停止原因：{{run.error_code}}。已完成数据保留；不会自动重试本轮。</p><p>逐片预检查、备份、事务与回执复用统一导入接口；可以切换页面，服务端继续执行。</p><button v-if="run.status==='RUNNING'" type="button" :disabled="busy" @click="cancel">当前影片完成后停止</button><button v-else-if="run.status==='STOPPED'&&['NETWORK_UNAVAILABLE','UPSTREAM_UNAVAILABLE','RATE_LIMITED','COOLDOWN'].includes(run.error_code)" type="button" :disabled="busy" @click="resume">继续未完成条目</button></div>
 <details v-if="run" @toggle="onToggleDetails"><summary>查看逐项核验结果（{{runItems.length}}）</summary><div v-if="detailsOpen" class="quality-results"><p v-for="(item,i) in visibleRunItems" :key="i">{{item.movie_id||item.target?.movie_id||'库外'}} · {{item.title||item.target?.title||item.tmdb_id||item.target?.tmdb_id||(typeof item.target==='number'?item.target:'未知影片')}} · {{itemLabels[item.status]||item.status}}<span v-if="item.error_code"> · {{item.error_code}}</span><span v-if="item.review"> · {{item.review.reason}}</span><span v-else-if="item.candidates"> · {{item.candidates.length}} 个电影候选，未自动绑定</span></p><button v-if="runItems.length>itemLimit" type="button" @click="itemLimit+=ITEM_STEP">显示更多（已显示 {{visibleRunItems.length}} / {{runItems.length}} 项）</button></div></details>
 <a v-if="report||run" href="/api/admin/imports/quality/runs" target="_blank" rel="noopener">查看完整核验 JSON</a>
</section></template>
<style scoped>
.quality-panel{padding-block:8px 24px;margin-block:0 24px;border-bottom:1px solid var(--mi-border);color:var(--mi-text-soft)}.quality-panel header{display:flex;align-items:center;justify-content:space-between;gap:16px}.quality-panel h2{font-family:var(--serif);font-size:18px;font-weight:500;margin:0;color:var(--mi-text-ivory)}.quality-panel p,.quality-confirm,.quality-panel summary{font-size:13.5px;line-height:1.8;color:var(--mi-text-secondary)}.quality-panel summary{color:var(--mi-accent-text);cursor:pointer}.quality-panel button{padding:8px 14px;border:1px solid var(--mi-border-strong);border-radius:8px;background:transparent;color:var(--mi-text);cursor:pointer;transition:background-color .18s ease,border-color .18s ease}.quality-panel button:hover:not(:disabled){background:var(--mi-surface-hover);border-color:var(--mi-accent-border);color:var(--mi-text)}.quality-panel button:disabled{opacity:.5;cursor:default}.quality-panel :is(button,input,a,summary):focus-visible{outline:2px solid var(--mi-accent);outline-offset:3px}.quality-confirm{display:flex;gap:10px;align-items:flex-start;margin-block:18px}.quality-confirm input{margin-top:6px;accent-color:var(--mi-accent);flex:none}.quality-status{margin-block:18px}.quality-status strong{color:var(--mi-text-ivory)}.quality-status span{margin-inline-start:18px;font-variant-numeric:tabular-nums}.quality-status p{margin-block:4px}.quality-results{max-height:360px;overflow:auto;scrollbar-color:var(--mi-scroll-thumb) var(--mi-scroll-track)}.quality-results p{overflow-wrap:anywhere}.quality-panel a{display:inline-block;margin-top:16px;color:var(--mi-accent-text);font-size:13px;text-underline-offset:3px}.quality-panel a:hover{color:var(--mi-accent-hover)}.quality-panel .error{color:var(--mi-negative)}
</style>
