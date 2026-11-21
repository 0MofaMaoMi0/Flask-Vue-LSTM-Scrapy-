<script setup lang="ts">
import {ref,computed,onMounted,onBeforeUnmount} from 'vue'
import {useRoute} from 'vue-router'
import {accountSettingsTarget} from '../services/accountSettings'
const route=useRoute()
const ids=ref(''),jobs=ref<any[]>([]),preview=ref<any>(),confirmed=ref(false),busy=ref(false),error=ref(''),pollError=ref(''),maxMovies=ref(0)
const JOB_STEP=10,jobLimit=ref(JOB_STEP)
const visibleJobs=computed(()=>jobs.value.slice(0,jobLimit.value))
function jobTitle(job:any){const plans=Array.isArray(job?.plans)?job.plans:[];const names=plans.slice(0,8).map((p:any)=>p.title);return plans.length>8?`${names.join('、')} 等 ${plans.length} 部`:names.join('、')}
const abort=new AbortController();let reloadController:AbortController|undefined,reloadSeq=0,timer:ReturnType<typeof setTimeout>|undefined
const messages:Record<string,string>={FORBIDDEN:'需要管理员权限。',AUTHENTICATION_REQUIRED:'登录已失效，请重新登录。',REQUEST_VERIFICATION_FAILED:'请求验证已更新，请重新提交。',IDENTITY_CONFLICT:'外部身份与现有电影冲突，已停止。',YEAR_REQUIRES_REVIEW:'年份与现有电影不一致，需要人工核对。',PREVIEW_TARGET_CHANGED:'预检查后数据发生变化，请重新检查。',PREVIEW_EXPIRED:'预检查已过期，请重新检查。',TMDB_NOT_CONNECTED:'请先在全局设置中启用个人 TMDB 连接。',TMDB_DISABLED:'个人 TMDB 连接已停用，请先启用。',CREDIT_REQUIRES_REVIEW:'既有演职员记录与来源不一致，需要人工核对。',DATA_UNAVAILABLE:'导入服务暂时不可用（DATA_UNAVAILABLE），请稍后刷新。'}
const isActiveJob=(status?:string)=>['QUEUED','RUNNING','PENDING'].includes(String(status||''))
const hasActiveJobs=()=>jobs.value.some(j=>isActiveJob(j?.status))
function clearPollTimer(){if(timer){clearTimeout(timer);timer=undefined}}
function schedulePoll(){clearPollTimer();if(abort.signal.aborted||document.hidden||!hasActiveJobs())return;timer=setTimeout(poll,3000)}
async function api(path='',data?:unknown,outerSignal?:AbortSignal){
 let token=''
 const signals=[abort.signal,AbortSignal.timeout(data?120000:10000)];if(outerSignal)signals.push(outerSignal)
 const signal=AbortSignal.any(signals)
 if(data!==undefined){const r=await fetch('/api/v1/auth/csrf',{credentials:'same-origin',signal});let cb:any=null;try{cb=await r.json()}catch{cb=null};token=cb?.data?.csrf_token||'';if(!r.ok||!token||(cb?.code!==0&&cb?.code!=='OK'))throw new Error('无法验证登录，请重试。')}
 const r=await fetch('/api/admin/imports'+path,{credentials:'same-origin',signal,...(data!==undefined?{method:'POST',headers:{'Content-Type':'application/json','X-CSRF-Token':token},body:JSON.stringify(data)}:{})})
 let body:any=null;try{body=await r.json()}catch{throw new Error('服务连接中断，请稍后刷新。')}
 if(!r.ok)throw new Error(messages[body?.code||'']||('操作未完成：'+(body?.code||r.status)))
 if(!body||body.code!=='OK'||body.data==null)throw new Error('导入服务响应格式异常，请稍后刷新。')
 return body.data
}
async function reload(){
 const ticket=++reloadSeq;reloadController?.abort();const current=new AbortController();reloadController=current
 const data=await api('',undefined,current.signal)
 if(ticket!==reloadSeq||current.signal.aborted)return
 jobs.value=Array.isArray(data.items)?data.items:[];maxMovies.value=data.max_movies;if(preview.value)preview.value=jobs.value.find(j=>j.job_id===preview.value.job_id)||preview.value;schedulePoll()
}
async function check(){busy.value=true;error.value='';confirmed.value=false;preview.value=undefined
 try{const raw=ids.value.trim().split(/[\s,，]+/).filter(Boolean);if(!raw.length||raw.some(i=>!/^\d+$/.test(i)))throw new Error('请输入数字 TMDB ID，以逗号或空格分隔。');const values=raw.map(Number);if(values.length>maxMovies.value||new Set(values).size!==values.length)throw new Error('数量超限或包含重复 ID。');preview.value=await api('/preview',{kind:'tmdb_catalog',tmdb_ids:values});await reload()}
 catch(e){error.value=(e as Error).message}finally{busy.value=false}}
async function submit(){if(!confirmed.value||!preview.value)return;busy.value=true;error.value=''
 try{preview.value=await api('',{preview_id:preview.value.job_id,digest:preview.value.digest,confirm:true});confirmed.value=false;await reload()}
 catch(e){error.value=(e as Error).message}finally{busy.value=false}}
async function cancel(job:any){busy.value=true;error.value='';try{await api('/'+job.job_id+'/cancel',{});await reload()}catch(e){error.value=(e as Error).message}finally{busy.value=false}}
async function reconcile(job:any){busy.value=true;error.value='';try{const r=await api('/'+job.job_id+'/reconcile',{});if(!r.receipt_found)error.value='尚未查到已提交回执，保留待核验状态，不会重复写入。';await reload()}catch(e){error.value=(e as Error).message}finally{busy.value=false}}
const labels:Record<string,string>={PREVIEW:'待确认',QUEUED:'排队中',RUNNING:'导入中',SUCCEEDED:'完成',FAILED:'失败',NEEDS_REVIEW:'需要核验提交结果',CANCELLED:'已取消',EXPIRED:'已过期'}
async function poll(){try{await reload();pollError.value=''}catch(e){if(!abort.signal.aborted&&(e as Error).name!=='AbortError')pollError.value=(e as Error).message}finally{schedulePoll()}}
function onVisibility(){if(document.hidden){clearPollTimer()}else if(!abort.signal.aborted&&hasActiveJobs()){void poll()}}
async function refresh(){try{await reload();pollError.value=''}catch(e){if(!abort.signal.aborted&&(e as Error).name!=='AbortError')pollError.value=(e as Error).message}}
onMounted(async()=>{document.addEventListener('visibilitychange',onVisibility);await refresh()});onBeforeUnmount(()=>{++reloadSeq;reloadController?.abort();abort.abort();clearPollTimer();document.removeEventListener('visibilitychange',onVisibility)})
defineExpose({refresh})
</script>
<template><section class="import-panel" aria-label="统一数据导入">
 <h2>统一数据导入</h2><p>使用当前管理员已启用的 TMDB 连接。补充电影资料、评分、海报、演员、导演及人物照片，保留旧来源与既有身份。</p>
 <form @submit.prevent="check"><label for="import-ids">TMDB 电影 ID（每批最多 {{maxMovies||'…'}} 部）</label><input id="import-ids" v-model="ids" maxlength="120" placeholder="例如：81, 550" :disabled="busy"/><button type="submit" :disabled="busy||!maxMovies">{{busy?'正在处理…':'预检查'}}</button><router-link :to="accountSettingsTarget('tmdb',route.fullPath)">TMDB 连接设置</router-link></form>
 <p v-if="error||pollError" role="alert" class="error">{{error||pollError}}</p>
 <div v-if="preview" class="preview"><h3>本次预检查</h3><ul><li v-for="p in preview.plans" :key="p.tmdb_id">{{p.title}}（{{p.year||'年份未知'}}） · TMDB {{p.tmdb_id}} · {{p.action==='append'?'新增电影':'补充已有电影 #'+p.movie_id}} · 演员 {{p.cast}} · 照片 {{p.photos}}</li></ul>
 <template v-if="preview.status==='PREVIEW'"><label class="confirmation"><input v-model="confirmed" type="checkbox"/>确认将以上具体电影导入指定 Ubuntu 隔离电影库；冲突停止，执行前保存受影响数据的备份记录。</label><button :disabled="busy||!confirmed" @click="submit">确认并提交后台导入</button></template><p v-else>任务状态：{{labels[preview.status]||preview.status}}</p></div>
 <h3>我的导入任务</h3><p v-if="!jobs.length">尚无任务。预检查不会写入电影表。</p><div class="tasks"><article v-for="job in visibleJobs" :key="job.job_id"><strong>{{jobTitle(job)}}</strong><span>{{labels[job.status]||job.status}}</span><small>{{new Date(job.created_at).toLocaleString('zh-CN')}} · {{job.job_id}}</small><p v-if="job.error_code" role="status">{{messages[job.error_code]||job.error_code}}；不会自动重复写入。</p><p v-if="job.result">数据库批次 {{job.result.batch_id}} · {{job.result.items.length}} 部完成 · 备份已保存</p><button v-if="job.status==='NEEDS_REVIEW'" :disabled="busy" @click="reconcile(job)">核对数据库回执</button><button v-if="['PREVIEW','QUEUED'].includes(job.status)" :disabled="busy" @click="cancel(job)">取消此任务</button></article><button v-if="jobs.length>jobLimit" type="button" @click="jobLimit+=JOB_STEP">显示更多任务（已显示 {{visibleJobs.length}} / {{jobs.length}} 个）</button></div>
 <p class="note">任务和备份仅保存在服务器受保护目录。服务重启后保留任务；提交结果不明确时暂停并核验，不盲目重试。库外电影必须经这里确认才入库。</p>
</section></template>
<style scoped>
.import-panel{margin:0 0 24px;padding:20px 22px;border:1px solid var(--mi-border);border-radius:14px;background:var(--mi-surface);color:var(--mi-text-soft)}.import-panel h2{margin:0 0 10px;font-family:var(--serif);font-size:18px;font-weight:500;color:var(--mi-text-ivory)}.import-panel h3{font-size:13px;font-weight:500;color:var(--mi-text);margin:18px 0 10px}.import-panel p,.import-panel li{font-size:13px;line-height:1.8;color:var(--mi-text-secondary)}.import-panel form{display:flex;align-items:center;gap:12px;flex-wrap:wrap;margin:18px 0}.import-panel label{font-size:13px;color:var(--mi-text-soft)}.import-panel input:not([type=checkbox]){background:var(--mi-input-bg);border:1px solid var(--mi-border-strong);border-radius:8px;color:var(--mi-text);padding:9px 11px;min-width:220px}.import-panel input[type=checkbox]{accent-color:var(--mi-accent)}.import-panel button{color:var(--mi-text);background:transparent;border:1px solid var(--mi-border-strong);border-radius:8px;padding:8px 14px;cursor:pointer;transition:background-color .18s ease,border-color .18s ease}.import-panel button:hover:not(:disabled){background:var(--mi-surface-hover);border-color:var(--mi-accent-border);color:var(--mi-text)}.import-panel button:disabled{opacity:.45;cursor:default}.import-panel a{color:var(--mi-accent-text);font-size:13px;text-underline-offset:3px}.import-panel a:hover{color:var(--mi-accent-hover)}.import-panel :is(button,input,a):focus-visible{outline:2px solid var(--mi-accent);outline-offset:2px}.preview,.tasks article{padding:16px;background:var(--mi-surface-warm);border:1px solid var(--mi-border);border-radius:10px;margin:12px 0}.confirmation{display:flex;gap:9px;line-height:1.7;margin:14px 0}.tasks strong{font-size:14px;color:var(--mi-text-ivory)}.tasks span{float:right;font-size:12.5px;color:var(--mi-accent-text)}.tasks small{display:block;margin-top:9px;color:var(--mi-text-muted);overflow-wrap:anywhere}.import-panel .error{color:var(--mi-negative)}.note{margin:18px 0 0}
</style>
