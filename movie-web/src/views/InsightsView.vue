<script setup lang="ts">
import {ref,computed,onMounted,onBeforeUnmount} from 'vue'
import AppSidebar from '../components/AppSidebar.vue'
import MetricChart from '../components/insights/MetricChart.vue'
import {loadInsights,type Metric} from '../services/insights'
const expanded=ref(localStorage.getItem('mi2_nav_expanded')==='true')
const tabs=[{id:'catalog/summary',name:'电影库分析'},{id:'me/behavior',name:'个人行为洞察'},{id:'me/ai-usage',name:'AI用量'},{id:'recommendation/evaluation',name:'推荐评估'}]
const connection=ref('未读取'),tab=ref(tabs[0]!.id),from=ref(''),to=ref(''),busy=ref(false),error=ref(''),metrics=ref<Metric[]>([]);let controller:AbortController|undefined,serial=0
async function load(){const id=++serial;controller?.abort();controller=new AbortController();const signal=controller.signal;busy.value=true;error.value='';metrics.value=[];try{const data=await loadInsights(tab.value,from.value,to.value,signal);if(id!==serial)return;metrics.value=data;if(tab.value==='me/ai-usage'){try{const r=await fetch('/api/v1/assistant/status',{credentials:'same-origin',signal});let s:any=null;try{s=await r.json()}catch{s=null};if(id!==serial)return;connection.value=r.ok&&s?.code==='OK'&&s?.data?(s.data.configured?s.data.model+' · '+(s.data.enabled?'已启用':'未启用'):'未连接个人模型'):'模型连接状态不可得'}catch{if(id===serial&&!signal.aborted)connection.value='模型连接状态不可得'}}}catch(e){if(id===serial&&(e as Error).name!=='AbortError')error.value=(e as Error).message}finally{if(id===serial)busy.value=false}}
function choose(id:string){tab.value=id;load()}
const names:Record<string,string>={'active_movie_count':'活跃电影数','status_distribution':'目录状态','source_coverage':'外部来源覆盖','rating_coverage':'评分来源覆盖','genres_distribution':'影片类型','decades_distribution':'上映年代','overview_completeness':'有效简介覆盖率','poster_coverage':'可访问海报覆盖率','ingest_batches':'导入批次状态','ingest_items':'导入条目状态','data_quality_issues':'质量问题状态','rating':'已评分影片','favorite':'收藏影片','want':'想看影片','watched':'已看影片','not_interested':'不感兴趣影片','daily_events':'每日行为事件','genre_preferences':'正反馈类型偏好','usage':'个人模型用量','bill_amount':'真实账单','latency':'请求耗时','precision_at_10':'Precision@10','recall_at_10':'Recall@10','ndcg_at_10':'NDCG@10','coverage_at_10':'Item Coverage@10'}
const label=(m:Metric)=>names[m.metric_id.split('.').pop()!]||m.metric_id
const states:Record<string,string>={OK:'可用',NO_DATA:'暂无数据',INSUFFICIENT_DATA:'样本不足',UNKNOWN:'未知',UNAVAILABLE:'不可得'}
function rows(m:Metric){if(Array.isArray(m.value))return m.value.map((x:any)=>({label:x.date,value:x.count}));if(m.value&&typeof m.value==='object')return Object.entries(m.value).filter(([,v])=>typeof v==='number').map(([k,v])=>({label:k,value:v as number}));return []}
function display(m:Metric){return m.value===null?'N/A':m.unit==='ratio'?(m.value*100).toFixed(1)+'%':String(m.value)}
const usage=computed(()=>metrics.value.find(m=>m.metric_id==='ai.usage')?.value)
onMounted(load);onBeforeUnmount(()=>{++serial;controller?.abort()})
</script>
<template><div class="insights-shell" :style="{'--nav-width':expanded?'280px':'52.5px'}"><AppSidebar @expanded-change="expanded=$event"/><main class="insights"><header><h1>数据洞察</h1><nav><router-link to="/home">账户</router-link><router-link to="/movies">电影与助手</router-link></nav></header><p>电影库与本人数据 · 数据不足或费用未知时如实展示。时间趋势按UTC统计，区间为开始含、结束不含；当前状态与电影库为全量快照。</p><div class="tabs" role="tablist" aria-label="分析模块"><button v-for="t in tabs" :key="t.id" role="tab" :aria-selected="tab===t.id" @click="choose(t.id)">{{t.name}}</button></div><form @submit.prevent="load"><label>开始日期（UTC）<input type="date" v-model="from"></label><label>结束日期（UTC，不含）<input type="date" v-model="to"></label><button :disabled="busy">刷新数据</button></form><p v-if="busy" role="status">正在读取真实数据…</p><div v-else-if="error" role="alert">{{error}} <button @click="load">重试</button></div><template v-else><p v-if="tab==='catalog/summary'" class="notice">当前已验真活跃影片：{{metrics[0]?.value??'未知'}}部。小样本分布参考意义有限，不代表外部平台全量电影。</p><p v-if="tab==='recommendation/evaluation'" class="notice">暂无足够且完成独立时间留出的真实交互。下列N/A不代表算法得分为零；合成测试仅验证计算器，不能证明推荐改进。</p><div class="cards"><article v-for="m in metrics" :key="m.metric_id"><h2>{{label(m)}}</h2><p class="state">{{states[m.status]||m.status}} · 样本 {{m.sample_size}}</p><template v-if="m.metric_id==='ai.usage'&&usage"><p>当前连接：{{connection}}</p><p>请求状态（保留创建时间的账本）：{{usage.request_states}}</p><p>实际派发 {{usage.calls}} 次 · 用量未知 {{usage.unknown_calls}} 次</p><p>已知输入 {{usage.known_tokens.input_tokens??'未知'}} / 输出 {{usage.known_tokens.output_tokens??'未知'}} / 缓存输入 {{usage.known_tokens.cached_input_tokens??'未知'}}</p><p>缓存输入包含在输入中，不重复累加。真实账单未知，Token不等于收费账单。</p><MetricChart title="每日模型调用" :rows="usage.daily_models.map((x:any)=>({label:x.date+' '+x.model+' '+x.provenance,value:x.calls}))"/><MetricChart title="Token组成（已知部分；缓存为输入子集）" :rows="usage.daily_models.map((x:any)=>({label:x.date+' '+x.model,value:x.input_tokens}))" :stacks="[{name:'非缓存输入',values:usage.daily_models.map((x:any)=>x.uncached_input_tokens)},{name:'缓存输入',values:usage.daily_models.map((x:any)=>x.cached_input_tokens)},{name:'输出',values:usage.daily_models.map((x:any)=>x.output_tokens)}]"/><table><caption>模型与来源分组；历史Mock不作为真实服务商用量</caption><thead><tr><th>日期/模型/来源</th><th>输入</th><th>输出</th><th>缓存输入（子集）</th><th>未知次数</th></tr></thead><tbody><tr v-for="(x,i) in usage.daily_models" :key="i"><td>{{x.date}} {{x.model}} {{x.provenance}} {{x.paid_request?'计费派发标记（非实扣）':'非计费标记'}}</td><td>{{x.input_tokens??'未知'}}</td><td>{{x.output_tokens??'未知'}}</td><td>{{x.cached_input_tokens??'未知'}}</td><td>{{x.unknown_calls}}</td></tr></tbody></table></template><MetricChart v-else-if="rows(m).length" :title="label(m)" :rows="rows(m)" :line="m.metric_id==='behavior.daily_events'"/><p v-else-if="m.value!==null&&typeof m.value==='object'">当前范围暂无数据。</p><strong v-else>{{display(m)}}</strong><details><summary>指标口径与来源</summary><p>{{m.notes}}</p><p>来源：{{m.source}} · 范围：{{m.scope}}</p><p>分母：{{m.denominator??'不适用'}} · 定义 {{m.definition_version}}</p><p>统计窗口：{{m.window.from??'全量快照'}} — {{m.window.to??'当前'}}</p><time :datetime="m.as_of">更新：{{new Date(m.as_of).toLocaleString()}}</time></details></article></div></template></main></div></template>
<style scoped>
:global(body:has(.insights-shell)){margin:0;background:var(--mi-bg);color:var(--mi-text)}
.insights-shell{min-height:100vh;background:var(--mi-bg);color:var(--mi-text);font-family:var(--sans)}
.insights{margin-left:var(--nav-width,52.5px);width:calc(100% - var(--nav-width,52.5px));box-sizing:border-box;background:var(--mi-bg);color:var(--mi-text);min-height:100vh;padding:40px clamp(20px,4vw,56px) 56px;position:relative;z-index:1;overflow-wrap:anywhere;font:15px/1.65 var(--sans);font-variant-numeric:tabular-nums;transition:margin-left .25s}
.insights>header{display:flex;justify-content:space-between;align-items:flex-end;gap:20px;flex-wrap:wrap;border-bottom:1px solid var(--mi-border);padding-bottom:20px;margin-bottom:22px}
.insights h1{font-family:var(--serif);font-size:clamp(30px,3vw,38px);font-weight:500;letter-spacing:.01em;margin:0;color:var(--mi-text)}
.insights header nav{display:flex;gap:20px}
.insights a{color:var(--mi-text-secondary);text-decoration:none;font-size:12.5px;line-height:1.6;border-bottom:1px solid transparent;padding-bottom:2px;transition:color .18s ease,border-color .18s ease}
.insights a:hover,.insights a:focus-visible{color:var(--mi-text);border-bottom-color:var(--mi-accent-border)}
.insights>p{color:var(--mi-text-secondary);font-size:12.5px;line-height:1.9;max-width:840px;margin:0 0 22px}
.tabs{display:flex;flex-wrap:wrap;gap:2px;padding:3px;background:var(--mi-surface-raised);border:1px solid var(--mi-border);border-radius:8px;width:fit-content;max-width:100%}
.tabs button{font:inherit;font-size:12.5px;color:var(--mi-text-muted);border:1px solid transparent;background:transparent;border-radius:6px;padding:7px 12px;cursor:pointer;transition:background-color .18s ease,color .18s ease,border-color .18s ease}
.tabs button:hover{color:var(--mi-text);background:var(--mi-chip-hover)}
.tabs button[aria-selected=true]{background:var(--mi-surface);border-color:var(--mi-accent-border);color:var(--mi-text)}
.insights form{display:flex;gap:12px;flex-wrap:wrap;align-items:flex-end;margin:16px 0 24px}
.insights label{display:flex;flex-direction:column;gap:6px;font-size:11.5px;color:var(--mi-text-muted);letter-spacing:.02em}
.insights input{font:inherit;font-size:12.5px;height:38px;padding:0 11px;border:1px solid var(--mi-border-strong);background:var(--mi-input-bg);color:var(--mi-text);border-radius:8px;transition:border-color .18s ease}
.insights input:focus{border-color:var(--mi-accent);outline:2px solid var(--mi-accent-soft);outline-offset:1px}
.insights form button,.insights [role=alert] button{font:inherit;font-size:12px;height:38px;padding:0 14px;border:1px solid var(--mi-border-strong);background:transparent;color:var(--mi-text-soft);border-radius:8px;cursor:pointer;transition:background-color .18s ease,border-color .18s ease}
.insights form button:hover,.insights [role=alert] button:hover{background:var(--mi-surface-hover);border-color:var(--mi-border-strong)}
.insights form button:disabled{opacity:.5;cursor:not-allowed}
.insights [role=status]{color:var(--mi-text-secondary);font-size:12.5px;padding:8px 0 18px}
.insights [role=alert]{color:var(--mi-negative);font-size:12.5px;padding:10px 0 18px}
.notice{background:var(--mi-surface);border:1px solid var(--mi-border);border-left:2px solid var(--mi-accent);padding:12px 16px;color:var(--mi-text-soft);font-size:12.5px;line-height:1.8;border-radius:6px;margin:0 0 20px}
.cards{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:16px}
.cards article{background:var(--mi-surface);color:var(--mi-text-ivory);border:1px solid var(--mi-border);border-radius:12px;padding:22px 24px;min-width:0;box-shadow:var(--mi-shadow-sm)}
.cards h2{font-family:var(--serif);font-size:20px;font-weight:500;letter-spacing:.01em;line-height:1.35;margin:0 0 6px;color:var(--mi-text-ivory)}
.state{font-size:11px;letter-spacing:.01em;color:var(--mi-text-muted);margin:0 0 14px;font-variant-numeric:tabular-nums}
.cards strong{display:block;font-size:30px;font-weight:500;letter-spacing:-.01em;line-height:1.3;color:var(--mi-accent);margin:6px 0 2px}
.cards article p{font-size:12.5px;line-height:1.85;color:var(--mi-text-soft);margin:6px 0}
.cards article details{margin-top:16px;border-top:1px solid var(--mi-border);padding-top:12px}
.cards summary{cursor:pointer;font-size:12px;color:var(--mi-accent)}
.cards details p{font-size:11.5px;line-height:1.8;color:var(--mi-text-muted);margin:6px 0}
.cards details time{font-size:11px;color:var(--mi-text-muted)}
.cards table{width:100%;border-collapse:collapse;font-size:12px;margin-top:10px;color:var(--mi-text-soft);font-variant-numeric:tabular-nums}
.cards td,.cards th{border-bottom:1px solid var(--mi-border-subtle);text-align:left;padding:6px 8px;vertical-align:top}
.cards th{font-size:11px;font-weight:500;color:var(--mi-text-muted);background:var(--mi-surface-raised)}
.cards caption{text-align:left;color:var(--mi-text-muted);font-size:11px;padding:4px 0;caption-side:top}
.insights :deep(.chart h3){font-family:var(--sans);font-size:13px;font-weight:500;letter-spacing:.01em;color:var(--mi-text);margin:18px 0 6px}
.insights :deep(.plot){width:100%;height:240px}
.insights :focus-visible{outline:2px solid var(--mi-accent);outline-offset:3px}
.cards article :focus-visible{outline-color:var(--mi-accent)}
@media(max-width:900px){.cards{grid-template-columns:1fr}}
@media(max-width:700px){.insights{margin-left:52.5px;width:calc(100% - 52.5px);padding:24px 16px 40px}.insights h1{font-size:28px}.insights>header{align-items:flex-start;gap:10px}.cards article{padding:16px}.insights form label{width:100%}.tabs{width:100%}.tabs button{flex:1;padding:7px 8px}}
@media(prefers-reduced-motion:reduce){.insights{transition:none}}
</style>