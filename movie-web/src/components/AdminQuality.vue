<script setup lang="ts">
import {onMounted,onBeforeUnmount,ref,computed} from 'vue'
const report=ref<any>(),run=ref<any>(),busy=ref(false),error=ref(''),pollError=ref(''),notice=ref(''),confirm=ref(false)
const totals=computed(()=>{const rows=run.value?.items||[];return {saved:rows.filter((i:any)=>i.status==='SUCCEEDED').length,held:rows.filter((i:any)=>['REQUIRES_REVIEW','FAILED','NEEDS_REVIEW'].includes(i.status)).length}})
const abort=new AbortController();let timer:ReturnType<typeof setTimeout>|undefined
async function api(path:string,data?:unknown){
 const signal=AbortSignal.any([abort.signal,AbortSignal.timeout(30000)])
 let token=''
 if(data!==undefined){const r=await fetch('/api/v1/auth/csrf',{signal,credentials:'same-origin'});if(!r.ok)throw new Error('登录已失效，请重新登录。');token=(await r.json()).data.csrf_token}
 const r=await fetch('/api/admin/imports/quality'+path,{signal,credentials:'same-origin',...(data!==undefined?{method:'POST',headers:{'Content-Type':'application/json','X-CSRF-Token':token},body:JSON.stringify(data)}:{})})
 const body=await r.json();if(!r.ok)throw new Error(({QUALITY_SCOPE_CHANGED:'目录发生变化，请刷新盘点后重新确认。',QUALITY_RUN_ACTIVE:'已有核验任务运行，请等待完成。',AUTHENTICATION_REQUIRED:'登录已失效，请重新登录。',FORBIDDEN:'需要管理员权限。'} as Record<string,string>)[body.code]||'核验未完成：'+body.code);return body.data
}
async function load(){busy.value=true;error.value='';try{report.value=await api('');run.value=await api('/runs')}catch(e){error.value=(e as Error).message}finally{busy.value=false}}
async function start(){busy.value=true;error.value='';notice.value='';try{run.value=await api('/runs',{scope_digest:report.value.scope_digest,confirm:true});confirm.value=false}catch(e){error.value=(e as Error).message}finally{busy.value=false}}
async function cancel(){busy.value=true;error.value='';try{await api('/runs/cancel',{});notice.value='已请求停止，将在当前影片完成后停止后续工作；已提交数据保留。'}catch(e){error.value=(e as Error).message}finally{busy.value=false}}
async function resume(){busy.value=true;error.value='';notice.value='';try{run.value=await api('/runs/resume',{job_id:run.value.job_id,confirm:true})}catch(e){error.value=(e as Error).message}finally{busy.value=false}}
async function poll(){try{const last=run.value?.status;run.value=await api('/runs');if(last==='RUNNING'&&run.value?.status!=='RUNNING')report.value=await api('');pollError.value=''}catch(e){if(!abort.signal.aborted)pollError.value=(e as Error).message}finally{if(!abort.signal.aborted)timer=setTimeout(poll,5000)}}
const labels:Record<string,string>={RUNNING:'核验进行中',COMPLETED:'本轮核验结束',STOPPED:'核验已停止'}
const itemLabels:Record<string,string>={SUCCEEDED:'资料补充完成',REQUIRES_REVIEW:'身份或资料需要复核，未提交',FAILED:'导入失败',NEEDS_REVIEW:'提交结果需要核对',IMPORT_PREPARED:'已封存，正在确认导入结果',VERIFIED_EXTERNAL_MOVIE_NOT_IMPORTED:'库外电影详情已核验，未入库',POSSIBLE_EXISTING_MOVIE_REQUIRES_REVIEW:'可能与现有影片重复，需复核',CANDIDATE_REQUIRES_REVIEW:'发现电影候选，未绑定',SEARCH_NO_MOVIE_CANDIDATE:'本轮电影搜索未命中'}
onMounted(async()=>{await load();if(!abort.signal.aborted)timer=setTimeout(poll,5000)});onBeforeUnmount(()=>{abort.abort();if(timer)clearTimeout(timer)})
</script>
<template><section class="quality-panel" aria-labelledby="quality-title">
 <header><h2 id="quality-title">数据质量核验</h2><button type="button" :disabled="busy" @click="load">刷新盘点</button></header>
 <p v-if="error||pollError" role="alert" class="error">{{error||pollError}}</p>
 <p v-if="notice" role="status">{{notice}}</p><p v-if="run?.status==='RUNNING'&&run.network_wait" role="status">TMDB 网络暂不稳定，等待后再次读取公开资料（第 {{run.network_wait.attempt}} 次）；已提交的数据库写入不会重发。</p>
 <p v-if="!report&&!error" role="status">正在盘点电影身份、评分及票房来源…</p>
 <template v-if="report"><p>当前 {{report.counts.active.toLocaleString()}} 部影片 · TMDB 已映射 {{report.counts.mapped.toLocaleString()}} 部 · 身份待核验 {{report.counts.identity_gaps}} 部 · 榜单库外候选 {{report.counts.pending}} 部。</p>
 <p>有投票支持的 TMDB 评分 {{report.counts.reliable_tmdb_rating.toLocaleString()}} 部；已知累计票房 {{report.counts.known_revenue.toLocaleString()}} 部。0 或缺失票房保留为未知，不代表没有收入。</p>
 <template v-if="run?.status!=='RUNNING'"><label class="quality-confirm"><input v-model="confirm" type="checkbox" :disabled="busy"/>核验 {{report.counts.identity_gaps}} 个身份缺口和 {{report.counts.pending}} 个库外候选；仅对 {{report.counts.refresh_candidates}} 部已有 TMDB 映射且资料不全的影片补充来源快照、评分、票数与票房。身份冲突跳过，不新增电影、不自动绑定候选。</label><button type="button" :disabled="busy||!confirm" @click="start">{{busy?'正在提交…':'开始本轮核验与可靠资料补充'}}</button></template>
 <div v-if="run" role="status" class="quality-status"><strong>{{labels[run.status]||run.status}}</strong><span>{{run.completed}} / {{run.total}} 项</span><p>已补充 {{totals.saved}} 部影片；{{totals.held}} 项暂停或需要复核。核验结束不代表所有身份缺口已解决。</p><p v-if="run.error_code">停止原因：{{run.error_code}}。已完成数据保留；不会自动重试本轮。</p><p>逐片预检查、备份、事务与回执复用统一导入接口；可以切换页面，服务端继续执行。</p><button v-if="run.status==='RUNNING'" type="button" :disabled="busy" @click="cancel">当前影片完成后停止</button><button v-else-if="run.status==='STOPPED'&&['NETWORK_UNAVAILABLE','UPSTREAM_UNAVAILABLE','RATE_LIMITED','COOLDOWN'].includes(run.error_code)" type="button" :disabled="busy" @click="resume">继续未完成条目</button></div>
 <details v-if="run"><summary>查看逐项核验结果（{{run.items.length}}）</summary><div class="quality-results"><p v-for="(item,i) in run.items" :key="i">{{item.movie_id||item.target?.movie_id||'库外'}} · {{item.title||item.target?.title||item.tmdb_id||item.target?.tmdb_id||(typeof item.target==='number'?item.target:'未知影片')}} · {{itemLabels[item.status]||item.status}}<span v-if="item.error_code"> · {{item.error_code}}</span><span v-if="item.review"> · {{item.review.reason}}</span><span v-else-if="item.candidates"> · {{item.candidates.length}} 个电影候选，未自动绑定</span></p></div></details>
 <a href="/api/admin/imports/quality/runs" target="_blank" rel="noopener">查看完整核验 JSON</a></template>
</section></template>
<style scoped>
.quality-panel{padding-block:8px 24px;margin-block:0 24px;border-bottom:1px solid #ffffff1a;color:#ece8f4}.quality-panel header{display:flex;align-items:center;justify-content:space-between;gap:16px}.quality-panel h2{font-size:19px;margin:0}.quality-panel p,.quality-confirm,.quality-panel summary{font-size:14px;line-height:1.8;color:#c8bed5}.quality-panel button{padding:10px 14px;border:1px solid #a78bc44d;border-radius:8px;background:#352747;color:#eee5ff;cursor:pointer}.quality-panel button:hover:not(:disabled){background:#463459}.quality-panel button:disabled{opacity:.5;cursor:default}.quality-panel :is(button,input,a,summary):focus-visible{outline:2px solid #c7acf1;outline-offset:4px}.quality-confirm{display:flex;gap:10px;align-items:flex-start;margin-block:18px}.quality-confirm input{margin-top:6px;accent-color:#a78bca;flex:none}.quality-status{margin-block:18px}.quality-status span{margin-inline-start:18px;font-variant-numeric:tabular-nums}.quality-status p{margin-block:4px}.quality-results{max-height:360px;overflow:auto;scrollbar-color:#685483 #17121f}.quality-results p{overflow-wrap:anywhere}.quality-panel a{display:inline-block;margin-top:16px;color:#d3b8f5;font-size:14px;text-underline-offset:3px}.quality-panel .error{color:#f0a5b6}
</style>
