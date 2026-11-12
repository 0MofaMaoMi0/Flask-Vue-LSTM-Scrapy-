<script setup lang="ts">
import {computed,onBeforeUnmount,onMounted,ref} from 'vue'
import {useRouter} from 'vue-router'
import DashboardChart from './DashboardChart.vue'
import DashboardWorldMap from './DashboardWorldMap.vue'
import {countryName} from '../../lib/globe/countryDisplay.js'
import {ISO2_TO_ISO3} from '../../lib/globe/iso2to3.js'

type Count={name:string;count:number}
type Dashboard={
 as_of:string;window:{days:number;from:string;to:string;timezone:string}
 catalog:{total:number;tmdb_mapped:number;countries:{iso2:string;count:number}[];countries_covered_movies:number;countries_unknown_movies:number;genres:Count[];decades:Count[];ratings:Count[];rating_source:string;quality:Record<string,number|null>}
 behavior:{events:number;active_users:number;daily:{date:string;count:number}[];kinds:Count[];top_movies:{movie_id:number;title:string;count:number}[];current:Record<string,number>}
 users:{accounts_active:number;new_in_window:number;sessions_valid:number}
 ai:{status:string;calls:number|null;real_calls?:number;mock_calls?:number;unknown_provenance_calls?:number;real_input_tokens?:number|null;real_output_tokens?:number|null;real_unknown_calls?:number;known_input_tokens:number|null;known_output_tokens:number|null;unknown_calls:number|null;models:Count[];amount:null;amount_status:string}
 notes:string[]
}
const router=useRouter(),data=ref<Dashboard>(),busy=ref(false),error=ref(''),days=ref(30),autoRefresh=ref(false),selectedCountry=ref('')
let controller:AbortController|undefined,sequence=0,timer:ReturnType<typeof setInterval>|undefined
const number=(value:number|null|undefined)=>value==null?'—':new Intl.NumberFormat('zh-CN').format(value)
const compact=(value:number|null|undefined)=>value==null?'未知':new Intl.NumberFormat('zh-CN',{notation:'compact',maximumFractionDigits:1}).format(value)
const percent=(n:number|null|undefined,total:number)=>n==null||!total?'—':(n/total*100).toFixed(1)+'%'
const nation=(iso2:string)=>countryName((ISO2_TO_ISO3 as Record<string,string>)[iso2]||iso2)
const currentLabels:Record<string,string>={watched:'已看',favorite:'收藏',want:'想看',rated:'评分',not_interested:'不感兴趣'}
const qualityLabels=[{key:'tmdb_mapped',name:'TMDB 身份映射'},{key:'posters',name:'电影海报资料'},{key:'cast',name:'演员阵容资料'},{key:'cast_photos',name:'演员照片资料'},{key:'overview',name:'剧情简介'}]
const mapCountries=computed(()=>data.value?.catalog.countries||[])
const selected=computed(()=>mapCountries.value.find(x=>x.iso2===selectedCountry.value))
const topCountries=computed(()=>[...mapCountries.value].sort((a,b)=>b.count-a.count).slice(0,5))
const updated=computed(()=>data.value?new Date(data.value.as_of).toLocaleTimeString('zh-CN',{hour12:false}):'—')
const states=computed(()=>Object.entries(data.value?.behavior.current||{}).filter(([key])=>key in currentLabels).map(([key,value])=>({name:currentLabels[key]!,value})))
const genreRows=computed(()=>(data.value?.catalog.genres||[]).filter(x=>x.name!=='未分类').sort((a,b)=>b.count-a.count).slice(0,7).map(x=>({name:x.name,value:x.count})))
const ratingRows=computed(()=>(data.value?.catalog.ratings||[]).map(x=>({name:x.name,value:x.count})))
const decadeRows=computed(()=>(data.value?.catalog.decades||[]).map(x=>({name:x.name,value:x.count})))
const eventKinds=computed(()=>(data.value?.behavior.kinds||[]).filter(x=>x.count>0))
const dailyRows=computed(()=>(data.value?.behavior.daily||[]).map(x=>({name:x.date,value:x.count})))
const chartInk={color:'#a9a1bc',fontSize:11},axisLine={lineStyle:{color:'#ffffff15'}},splitLine={lineStyle:{color:'#ffffff08'}}
function horizontal(rows:{name:string;value:number|null}[],color:string){return {grid:{top:5,bottom:8,left:8,right:35,containLabel:true},xAxis:{type:'value',minInterval:1,axisLabel:{...chartInk},splitLine},yAxis:{type:'category',inverse:true,data:rows.map(x=>x.name),axisLine:{show:false},axisTick:{show:false},axisLabel:{...chartInk,color:'#d4cbe2',width:70,overflow:'truncate'}},series:[{type:'bar',data:rows.map(x=>x.value),barMaxWidth:12,itemStyle:{color,borderRadius:[0,3,3,0]},label:{show:true,position:'right',color:'#e0d5ef',fontSize:11}}]}}
const genresOption=computed(()=>horizontal(genreRows.value,'#af91e7'))
const ratingsOption=computed(()=>({grid:{left:8,right:8,top:18,bottom:8,containLabel:true},xAxis:{type:'category',data:ratingRows.value.map(x=>x.name),axisLabel:{...chartInk,fontSize:10},axisLine,axisTick:{show:false}},yAxis:{type:'value',minInterval:1,axisLabel:chartInk,splitLine},series:[{type:'bar',data:ratingRows.value.map(x=>x.value),barMaxWidth:26,itemStyle:{color:'#d5b48a',borderRadius:[3,3,0,0]}}]}))
const decadesOption=computed(()=>({grid:{left:8,right:10,top:15,bottom:5,containLabel:true},xAxis:{type:'category',data:decadeRows.value.map(x=>x.name),axisLabel:{...chartInk,fontSize:10,rotate:30},axisLine,axisTick:{show:false}},yAxis:{type:'value',minInterval:1,axisLabel:chartInk,splitLine},series:[{type:'bar',data:decadeRows.value.map(x=>x.value),barMaxWidth:24,itemStyle:{color:'#8a9dc9',borderRadius:[3,3,0,0]}}]}))
const dailyOption=computed(()=>({grid:{left:8,right:16,top:15,bottom:8,containLabel:true},tooltip:{trigger:'axis'},xAxis:{type:'category',boundaryGap:false,data:dailyRows.value.map(x=>x.name),axisLabel:{...chartInk,formatter:(v:string)=>v.slice(5)},axisLine,axisTick:{show:false}},yAxis:{type:'value',minInterval:1,axisLabel:chartInk,splitLine},series:[{name:'行为事件',type:'line',data:dailyRows.value.map(x=>x.value),smooth:false,showSymbol:dailyRows.value.length<10,symbolSize:5,lineStyle:{width:2,color:'#b99aee'},itemStyle:{color:'#c1a7ef'},areaStyle:{color:'rgba(168,132,229,.10)'}}]}))

async function refresh(){
 const request=++sequence;controller?.abort();controller=new AbortController();busy.value=true;error.value=''
 try{
  const r=await fetch('/api/admin/dashboard?days='+days.value,{credentials:'same-origin',signal:controller.signal})
  if(!r.ok){if(r.status===401||r.status===403)data.value=undefined;throw new Error(r.status===403?'仅管理员可以查看这份数据。':r.status===401?'登录已失效，请重新登录。':'驾驶舱数据暂时不可用，请稍后重试。')}
  const body=await r.json();if(request!==sequence)return
  data.value=body.data
 }catch(e){if(request===sequence&&(e as Error).name!=='AbortError')error.value=(e as Error).message}
 finally{if(request===sequence)busy.value=false}
}
function chooseWindow(value:number){if(days.value===value)return;days.value=value;refresh()}
function setAuto(){if(timer)clearInterval(timer);timer=undefined;if(autoRefresh.value)timer=setInterval(()=>{if(!document.hidden&&!busy.value)refresh()},60000)}
function openMovie(movieId:number){router.push({path:'/movies',hash:'#movie/'+movieId,query:{from:'admin'}})}
onMounted(refresh)
onBeforeUnmount(()=>{++sequence;controller?.abort();if(timer)clearInterval(timer)})
defineExpose({refresh})
</script>

<template>
 <section class="cinema-dashboard" aria-label="影院数据指挥屏" :aria-busy="busy">
  <div class="screen-tools">
   <div class="data-status"><i :class="{loading:busy}"/><span>{{busy?'正在更新':data?'MySQL 聚合快照':'等待数据'}}</span><span v-if="data" class="updated">{{updated}} 更新</span></div>
   <div class="screen-controls">
    <div class="periods" role="group" aria-label="行为与模型统计时间窗口"><button v-for="value in [7,30,90]" :key="value" :aria-pressed="days===value" @click="chooseWindow(value)">近 {{value}} 天</button></div>
    <label class="auto-refresh"><input v-model="autoRefresh" type="checkbox" @change="setAuto">每分钟刷新</label>
   </div>
  </div>
  <div v-if="error" class="screen-message failure" role="alert">{{error}}<span v-if="data"> 保留上次成功快照；当前数据未更新。</span><button @click="refresh">重新读取</button></div>
  <div v-if="!data" class="dashboard-wait" role="status"><span v-if="busy" class="loading-line"/>{{busy?'正在汇总电影、用户行为与模型用量…':'尚未取得驾驶舱数据。'}}</div>
  <template v-else>
   <div class="summary-strip">
    <article><span>在架影片</span><strong>{{number(data.catalog.total)}}<small>部</small></strong><p>全电影库 · ID 去重</p></article>
    <article><span>TMDB 身份映射</span><strong>{{percent(data.catalog.tmdb_mapped,data.catalog.total)}}</strong><p>{{number(data.catalog.tmdb_mapped)}} 部已建立映射</p></article>
    <article><span>窗口活跃用户</span><strong>{{number(data.behavior.active_users)}}<small>人</small></strong><p>有效账号 {{number(data.users.accounts_active)}} 个</p></article>
    <article><span>用户行为事件</span><strong>{{number(data.behavior.events)}}<small>次</small></strong><p>近 {{data.window.days}} 天 · UTC</p></article>
    <article><span>BYOK 模型派发</span><strong>{{data.ai.status==='AVAILABLE'?number(data.ai.real_calls):'—'}}<small v-if="data.ai.status==='AVAILABLE'">次</small></strong><p>{{data.ai.status==='AVAILABLE'?'Mock '+number(data.ai.mock_calls)+' 次，独立统计':'用量数据暂不可得'}}</p></article>
   </div>
   <div class="dashboard-columns">
    <div class="dashboard-column profile-column">
     <section class="screen-panel genre-panel"><header><h2>电影类型</h2><span>TOP {{genreRows.length}}</span></header><DashboardChart label="电影类型分布，一部影片可以属于多个类型" :rows="genreRows" :option="genresOption" unit="部"/></section>
     <section class="screen-panel rating-panel"><header><h2>来源评分分布</h2><span>{{data.catalog.rating_source==='douban'?'豆瓣 · 10分制':data.catalog.rating_source}}</span></header><DashboardChart label="来源评分分布，按每部电影同一来源评分统计" :rows="ratingRows" :option="ratingsOption" unit="部"/></section>
     <section class="screen-panel decades-panel"><header><h2>电影年代</h2><span>上映年份分组</span></header><DashboardChart label="按上映年代统计的全库电影数量" :rows="decadeRows" :option="decadesOption" unit="部"/></section>
    </div>
    <div class="dashboard-column discovery-column">
     <section class="screen-panel world-panel"><header><div><h2>世界电影版图</h2><p>制作国家分布 · 合拍影片可计入多个国家</p></div><strong>{{mapCountries.length}}<small>个国家 / 地区</small></strong></header>
     <DashboardWorldMap :countries="mapCountries" @select="selectedCountry=$event"/>
     <div class="country-leaders"><button v-for="(item,index) in topCountries" :key="item.iso2" :class="{selected:selectedCountry===item.iso2}" @click="selectedCountry=item.iso2"><span class="country-rank">{{String(index+1).padStart(2,'0')}}</span><span>{{nation(item.iso2)}}</span><b>{{number(item.count)}}</b></button></div>
     <p class="map-footnote" aria-live="polite"><span v-if="selected">{{nation(selected.iso2)}}：{{number(selected.count)}} 部；</span>有国家资料 {{number(data.catalog.countries_covered_movies)}} 部，未提供 {{number(data.catalog.countries_unknown_movies)}} 部。</p>
    </section>
     <section class="screen-panel popular-panel"><header><h2>热门互动影片</h2><span>近 {{data.window.days}} 天</span></header><ol v-if="data.behavior.top_movies.length" class="popular-list"><li v-for="(item,index) in data.behavior.top_movies.slice(0,5)" :key="item.movie_id"><button @click="openMovie(item.movie_id)"><span class="rank" :class="{leading:index<3}">{{String(index+1).padStart(2,'0')}}</span><span class="popular-title">{{item.title}}</span><b>{{number(item.count)}}<small>次</small></b></button></li></ol><p v-else class="empty-panel">这个时间窗口还没有影片交互。</p><div class="behavior-states" aria-label="当前用户行为状态，可重叠"><span v-for="item in states" :key="item.name">{{item.name}} <b>{{number(item.value)}}</b></span></div><p class="panel-caption">榜单按事件次数；下方为独立的当前状态，可重叠。</p></section>
    </div>
    <div class="dashboard-column operation-column">
     <section class="screen-panel activity-panel"><header><h2>用户交互趋势</h2><span>近 {{data.window.days}} 天</span></header><DashboardChart label="每日用户行为事件，按 UTC 日期统计" :rows="dailyRows" :option="dailyOption" unit="次"/><div class="event-mix" aria-label="窗口事件构成，含设置和撤销"><div class="mix-heading"><span>窗口事件构成</span><small>含设置与撤销</small></div><div v-for="kind in eventKinds" :key="kind.name" class="event-kind"><span>{{kind.name}}</span><i aria-hidden="true"><b :style="{width:(data.behavior.events?kind.count/data.behavior.events*100:0)+'%'}"/></i><b>{{number(kind.count)}}</b></div><p v-if="!eventKinds.length">当前窗口暂无行为事件。</p></div></section>
     <section class="screen-panel quality-panel"><header><h2>电影资料覆盖</h2><span>当前全库</span></header><div class="quality-list"><div v-for="item in qualityLabels" :key="item.key"><div class="quality-copy"><span>{{item.name}}</span><b>{{percent(data.catalog.quality[item.key],data.catalog.total)}}</b></div><progress v-if="data.catalog.quality[item.key]!=null" :value="data.catalog.quality[item.key]!" :max="Math.max(1,data.catalog.total)" :aria-label="item.name"/><span v-else class="quality-unknown">数据不可得</span></div></div><p class="panel-caption">资料关联覆盖，不代表已逐张验证远端图片可达。</p></section>
     <section class="screen-panel ai-panel"><header><h2>AI 服务观察</h2><span>近 {{data.window.days}} 天</span></header><template v-if="data.ai.status==='AVAILABLE'"><div class="token-figures"><div><span>真实派发 · 已知输入 Token</span><strong>{{compact(data.ai.real_input_tokens)}}</strong></div><div><span>真实派发 · 已知输出 Token</span><strong>{{compact(data.ai.real_output_tokens)}}</strong></div></div><div class="ai-rows"><div><span>真实派发中用量未确认</span><b>{{number(data.ai.real_unknown_calls)}} 次</b></div><div><span>实际账单</span><b class="unknown-bill">金额未知</b></div></div><div class="model-list"><span>Mock {{number(data.ai.mock_calls)}} 次</span><span>来源未确认 {{number(data.ai.unknown_provenance_calls)}} 次</span></div><p class="panel-caption">仅汇总已有账本，Token 不等于实际扣费。本页不会触发模型调用。</p></template><p v-else class="empty-panel">模型用量暂不可得，不将未知记为零。</p></section>
    </div>
   </div>
   <footer class="dashboard-footer"><span>统计窗口仅影响行为与模型；电影库和当前状态为全量快照。</span><details><summary>指标口径</summary><ul><li v-for="note in data.notes" :key="note">{{note}}</li></ul></details></footer>
  </template>
 </section>
</template>

<style scoped>
.cinema-dashboard{--panel:#11111d;--ink:#ede8f5;--soft:#aaa1bb;--purple:#bb9deb;color:var(--ink);min-width:0;font-size:13px;font-variant-numeric:tabular-nums;display:flex;flex-direction:column;flex:1;min-height:0}
.screen-tools,.data-status,.screen-controls,.periods{display:flex;align-items:center}.screen-tools{justify-content:space-between;gap:16px;margin:2px 0 14px;flex-wrap:wrap}.data-status{gap:8px;font-size:11px;color:#beb4ce}.data-status i{width:6px;height:6px;background:#b49cdb;border-radius:50%}.data-status i.loading{background:#d9ba8e}.updated{margin-left:8px;color:#9c92ae}.screen-controls{gap:15px}.periods{gap:3px;padding:3px;background:#151420;border:1px solid #ffffff10;border-radius:8px}.periods button{font:inherit;font-size:11px;color:#aea4bf;border:0;background:transparent;cursor:pointer;border-radius:5px;padding:6px 10px}.periods button[aria-pressed=true]{background:#332842;color:#eee5fb}.auto-refresh{display:flex;gap:6px;align-items:center;font-size:11px;color:#aaa0bc;cursor:pointer}.auto-refresh input{accent-color:#b095da;margin:0}.screen-controls button:hover{color:white;background:#3b2e4d}.cinema-dashboard :focus-visible{outline:2px solid #c5a8f0;outline-offset:3px}
.summary-strip{display:grid;grid-template-columns:repeat(5,minmax(0,1fr));margin-bottom:16px;padding:14px 0 16px;border-top:1px solid #ffffff14;border-bottom:1px solid #ffffff14}.summary-strip article{padding:0 22px;border-right:1px solid #ffffff10;min-width:0}.summary-strip article:first-child{padding-left:0}.summary-strip article:last-child{border:0;padding-right:0}.summary-strip article>span{display:block;font-size:11px;color:#b6abc6}.summary-strip strong{display:block;font-size:clamp(25px,2.25vw,38px);font-weight:650;letter-spacing:-.03em;line-height:1.25;margin:6px 0 3px}.summary-strip strong small{font-size:11px;font-weight:400;letter-spacing:0;color:#aa9dbe;margin-left:7px}.summary-strip p{font-size:10px;color:#988ba9;margin:0;line-height:1.6}
.dashboard-columns{display:grid;grid-template-columns:minmax(220px,1fr) minmax(320px,1.6fr) minmax(240px,1fr);gap:16px;min-height:690px;flex:1}.dashboard-column{display:flex;flex-direction:column;gap:16px;min-width:0;min-height:0}.profile-column .screen-panel{flex:1;min-height:210px}.discovery-column .world-panel{flex:1;min-height:410px}.operation-column .activity-panel{flex:1;min-height:220px}.quality-panel,.ai-panel,.popular-panel{flex:none}.screen-panel{position:relative;display:flex;flex-direction:column;min-width:0;min-height:0;background:var(--panel);border:1px solid #ffffff10;border-radius:12px;padding:17px 17px 13px;overflow:hidden}.screen-panel>header{display:flex;justify-content:space-between;align-items:center;gap:10px;flex-shrink:0;margin-bottom:12px}.screen-panel h2{font-size:13px;line-height:1.5;font-weight:600;margin:0;color:#e6dff0}.screen-panel header>span{font-size:10px;color:#a799b9;white-space:nowrap}.screen-panel :deep(.dashboard-chart){flex:1;min-height:0}
.world-panel{background:#10101b;padding:19px 22px 12px}.world-panel>header{margin-bottom:0;align-items:flex-start}.world-panel h2{font-size:16px}.world-panel header p{font-size:10px;line-height:1.6;color:#a99bbd;margin:6px 0 0}.world-panel header strong{font-size:25px;font-weight:550;color:#d5bee9;text-align:right;line-height:1}.world-panel header strong small{display:block;font-size:9px;color:#a99aba;font-weight:400;margin-top:7px}.world-panel :deep(.dashboard-world){flex:1;min-height:0;height:auto}.country-leaders{display:flex;gap:7px;flex-shrink:0;border-top:1px solid #ffffff10;padding-top:12px}.country-leaders button{flex:1;min-width:0;position:relative;border:0;border-bottom:2px solid transparent;background:transparent;color:#b8a8ca;text-align:left;padding:3px 0 8px;font-size:10px;cursor:pointer}.country-leaders button span:not(.country-rank){display:block;white-space:nowrap;text-overflow:ellipsis;overflow:hidden;margin:3px 0}.country-leaders b{font-size:16px;font-weight:550;color:#e4d8f0}.country-rank{font-size:9px;color:#a28db5}.country-leaders button:hover,.country-leaders button.selected{border-bottom-color:#a98bd0}.map-footnote{font-size:10px;color:#aa9ab9;line-height:1.7;margin:7px 0 0;flex-shrink:0}
.quality-list{display:flex;flex-direction:column;gap:5px;margin:0 0 6px}.quality-copy{display:flex;justify-content:space-between;gap:6px;font-size:10px;line-height:1.2;color:#bfb3cf;margin-bottom:3px}.quality-copy b{font-size:10px;font-weight:500;color:#c4aadf}.quality-list progress{display:block;width:100%;height:3px;border:0;border-radius:2px;overflow:hidden;background:#242030;accent-color:#9e81c6}.quality-list progress::-webkit-progress-bar{background:#242030}.quality-list progress::-webkit-progress-value{background:#9e81c6}.quality-list progress::-moz-progress-bar{background:#9e81c6}.quality-unknown{font-size:10px;color:#ad9dba}.panel-caption{color:#9e8fae;font-size:9px;line-height:1.7;margin:8px 0 0}
.popular-list{list-style:none;margin:0;padding:0;flex:none;max-height:160px;overflow:auto}.popular-list button{width:100%;display:flex;align-items:center;gap:10px;text-align:left;border:0;background:transparent;color:#c6b9d4;font:inherit;font-size:11px;line-height:1.2;padding:3px 0;cursor:pointer;border-bottom:1px solid #ffffff07}.popular-list button:hover{color:white;background:#ffffff04}.rank{font-size:11px;color:#a28db5;min-width:18px}.rank.leading{color:#c3a9e1}.popular-title{font-size:11px;flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.popular-list b{font-size:12px;font-weight:550;color:#d8c2e8}.popular-list b small{font-size:9px;color:#a190af;margin-left:4px;font-weight:400}.behavior-states{display:flex;gap:10px;flex-wrap:wrap;border-top:1px solid #ffffff0e;padding-top:8px;margin-top:8px}.behavior-states span{font-size:9px;color:#a493b3}.behavior-states b{font-weight:550;color:#d7c9e6;margin-left:4px}
.token-figures{display:flex;gap:10px;margin:4px 0 14px}.token-figures>div{flex:1}.token-figures span{font-size:10px;color:#b2a2c2;display:block}.token-figures strong{display:block;color:#d8b8df;font-size:25px;font-weight:550;margin-top:7px}.ai-rows>div{display:flex;justify-content:space-between;gap:10px;font-size:10px;color:#ad9abd;padding:6px 0;border-top:1px solid #ffffff0c}.ai-rows b{font-weight:500;color:#d3c2e4}.ai-rows b.unknown-bill{color:#cbb38f}.model-list{display:flex;gap:8px;flex-wrap:wrap;margin-top:10px}.model-list span{font-size:9px;color:#ac9cbd;max-width:100%;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.model-list b{margin-left:4px;color:#cfbce0;font-weight:500}
.dashboard-footer{display:flex;justify-content:space-between;align-items:flex-start;gap:20px;margin-top:15px;color:#a191b1;font-size:10px;line-height:1.8}.dashboard-footer summary{cursor:pointer;white-space:nowrap;color:#bbabcc}.dashboard-footer details{max-width:55%;text-align:right}.dashboard-footer ul{padding-left:16px;text-align:left;line-height:1.9}.dashboard-wait{min-height:60vh;display:flex;align-items:center;justify-content:center;gap:12px;color:#b2a2c5;font-size:13px}.loading-line{width:30px;height:2px;background:#b091da;animation:loading 1s ease-in-out infinite alternate}.screen-message{font-size:12px;color:#c5b695;margin:10px 0 16px;padding:12px 15px;border:1px solid #8b665e44;border-radius:8px;background:#261c2466;line-height:1.8}.screen-message button{font:inherit;border:0;background:transparent;color:#e2c9f0;text-decoration:underline;margin-left:10px;cursor:pointer}.screen-message.failure{color:#ddafb9}.empty-panel{margin:12px 0;color:#ae9abf;font-size:11px;line-height:1.9}.cinema-dashboard button:disabled{opacity:.5;cursor:default}::selection{background:#765692;color:white}
@keyframes loading{to{opacity:.25;transform:scaleX(.4)}}
.event-mix{display:none;flex:none;border-top:1px solid #ffffff10;padding-top:10px;margin-top:8px}.mix-heading{display:flex;justify-content:space-between;gap:10px;color:#c8bad9;font-size:11px;margin-bottom:8px}.mix-heading small{font-size:10px;color:#a797b9}.event-kind{display:flex;align-items:center;gap:10px;font-size:10px;line-height:1.4;padding:3px 0;color:#b9a9ca}.event-kind>span{width:50px;flex:none}.event-kind>i{flex:1;height:4px;background:#292235;border-radius:3px;overflow:hidden}.event-kind>i b{display:block;height:100%;background:#8c70b5;border-radius:3px}.event-kind>b{min-width:28px;text-align:right;color:#d6c2e8;font-weight:500}.event-mix p{font-size:11px;color:#aa99b9;margin:8px 0 0}
@media(min-height:1100px){.event-mix{display:block}}
@media(min-width:1650px){.screen-panel{padding:19px 21px 15px}.screen-panel h2{font-size:14px}.screen-panel header>span{font-size:11px}.panel-caption{font-size:10px}.summary-strip{padding-top:15px;padding-bottom:17px}}
@container admin-content (max-width:900px){.event-mix{display:none}.dashboard-columns{grid-template-columns:minmax(0,1fr) minmax(0,1.45fr);grid-template-areas:'profile discovery' 'operations operations';min-height:0;align-items:stretch}.profile-column{grid-area:profile}.discovery-column{grid-area:discovery}.operation-column{grid-area:operations;display:grid;grid-template-columns:repeat(3,minmax(0,1fr));align-items:start}.operation-column .activity-panel{height:220px;min-height:0}.profile-column .screen-panel{min-height:210px}.summary-strip article{padding:0 10px}.summary-strip article>span{font-size:10px}.screen-controls{gap:9px}.world-panel{padding-left:14px;padding-right:14px}.country-leaders{gap:4px}.world-panel header strong{font-size:20px}}
@container admin-content (max-width:620px){.summary-strip{grid-template-columns:repeat(2,minmax(0,1fr));gap:20px}.summary-strip article{padding-left:0;border:0}.summary-strip article:last-child{grid-column:1/-1}.dashboard-columns,.operation-column{display:flex;flex-direction:column}.profile-column .screen-panel,.operation-column .activity-panel{flex:none;height:220px}.discovery-column .world-panel{min-height:420px}.screen-controls{flex-wrap:wrap}.dashboard-footer{display:block}.dashboard-footer details{max-width:100%;text-align:left}.screen-tools{align-items:flex-start;gap:12px}}
@media(prefers-reduced-motion:reduce){*,*:before,*:after{animation:none!important;scroll-behavior:auto!important}}
</style>
