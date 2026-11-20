<script setup lang="ts">
import {ref,shallowRef,computed,onMounted,onBeforeUnmount,watch} from 'vue'
import {useRoute,useRouter} from 'vue-router'
import AppSidebar from '../components/AppSidebar.vue'
import {accountSettingsTarget} from '../services/accountSettings'
import {tmdbRequest,tmdbChanged,tmdbErrors} from '../services/tmdb'
import {createFeedbackBoard,vFeedback} from '../services/recommendationFeedback'
import {movieArtwork} from '../services/movieArtwork'
const route=useRoute(),router=useRouter(),expanded=ref(localStorage.getItem('mi2_nav_expanded')==='true')
const channels=ref<any[]>([]),data=ref<any>(null),genres=ref<any[]>([]),loading=ref(true),error=ref(''),failed=ref<Record<string,boolean>>({})
const languageNames:Record<string,string>={zh:'中文',cn:'中文',en:'英语',ja:'日语',ko:'韩语',fr:'法语',de:'德语',es:'西班牙语',it:'意大利语',ru:'俄语',hi:'印地语',th:'泰语',pt:'葡萄牙语',da:'丹麦语',sv:'瑞典语',nl:'荷兰语',pl:'波兰语',tr:'土耳其语',ar:'阿拉伯语',no:'挪威语',fi:'芬兰语',cs:'捷克语',hu:'匈牙利语',el:'希腊语',he:'希伯来语',fa:'波斯语',id:'印尼语',vi:'越南语',uk:'乌克兰语'}
const languages=ref<{code:string,count:number}[]>([]),languageOptions=computed(()=>languages.value.length?languages.value.map(l=>({code:l.code,label:languageNames[l.code]||l.code.toUpperCase()})):[{code:'zh',label:'中文'},{code:'en',label:'英语'},{code:'ja',label:'日语'},{code:'ko',label:'韩语'},{code:'fr',label:'法语'},{code:'de',label:'德语'}])
// 库内影片的海报资产多为 local_static 相对路径，后端 asset_url 不把它转成可访问地址，
// 推荐接口的 poster 因此可能为空；按电影库/首页的做法回退到已核验的静态海报。
function posterCandidates(m:any){
 const id=m?.movie_id,tmdb=m?.tmdb_id
 return [...new Set([m?.poster,m?.poster_fallback,id?movieArtwork[String(id)]?.poster:undefined,tmdb?movieArtwork[String(tmdb)]?.poster:undefined])]
  .filter((src:unknown):src is string=>typeof src==='string'&&src!==''&&!src.startsWith('//'))
}
function posterOf(m:any){return posterCandidates(m).find(src=>!failed.value[m.key+':'+src])}
function posterError(m:any,event:Event){const src=(event.target as HTMLImageElement).getAttribute('src');if(src)failed.value[m.key+':'+src]=true}
const tmdbBusy=ref(false),tmdbNotice=ref('')
const keyword=ref(''),genre=ref(''),language=ref(''),yearFrom=ref(''),yearTo=ref(''),runtime=ref(''),minVotes=ref(''),localOnly=ref(false),unwatched=ref(false)
const channel=computed(()=>String(route.query.channel||'daily'))
const selected=computed(()=>channels.value.find(c=>c.id===channel.value))
let controller:AbortController|undefined,initController:AbortController|undefined,sequence=0
const feedback=shallowRef<ReturnType<typeof createFeedbackBoard>>()
async function get(path:string,signal?:AbortSignal){const r=await fetch('/api/catalog/'+path,{credentials:'same-origin',signal});let body:any=null;try{body=await r.json()}catch{body=null};if(!r.ok)throw new Error(r.status===401?'登录已失效，请重新登录。':r.status===400?'筛选条件有误，请检查年份和范围。':'推荐频道暂时无法读取，请重试。');if(!body||body.code!=='OK'||body.data==null)throw new Error('推荐频道响应异常，请重试。');return body.data}
function sync(){const q=route.query;keyword.value=String(q.keyword||'');genre.value=String(q.genre||'');language.value=String(q.language||'');yearFrom.value=String(q.year_from||'');yearTo.value=String(q.year_to||'');runtime.value=String(q.runtime_max||'');minVotes.value=String(q.min_votes||'');localOnly.value=q.scope==='local';unwatched.value=q.unwatched==='true'}
async function load(){sync();controller?.abort();controller=new AbortController();const id=++sequence;loading.value=true;error.value='';data.value=null;feedback.value?.dispose()
 try{const params=new URLSearchParams({channel:channel.value,limit:'36'});for(const key of ['keyword','genre','language','year_from','year_to','runtime_max','min_votes','scope','unwatched'])if(route.query[key])params.set(key,String(route.query[key]));const result=await get('recommendations?'+params,controller.signal);if(id===sequence){data.value=result;feedback.value=createFeedbackBoard({channel:channel.value,period:result.period,context:'recommendations',items:result.items})}}
 catch(e){if(id===sequence&&!controller.signal.aborted)error.value=(e as Error).message}finally{if(id===sequence)loading.value=false}}
async function updateTMDB(){
 if(tmdbBusy.value||loading.value)return
 tmdbBusy.value=true;tmdbNotice.value=''
 try{
  const publicBoard=!!selected.value?.public,ids=publicBoard?[]:(data.value?.items||[]).filter((m:any)=>m.in_catalog&&m.tmdb_id).slice(0,data.value?.tmdb_connection?.max_detail_refresh||6).map((m:any)=>m.movie_id)
  if(!publicBoard&&!ids.length){tmdbNotice.value='当前片单没有可匹配 TMDB 的影片。';return}
  const r=await tmdbRequest('refresh','POST',publicBoard?{channel:channel.value}:{movie_ids:ids});const ok=r.results.filter((x:any)=>x.status==='OK').length
  tmdbNotice.value=r.all_updated?(publicBoard?'已取得可用官方榜单；短时间内复用最新缓存。':'已更新前 '+ok+' 部影片的公开资料；个人片单顺序保持稳定。'):'部分更新失败，保留已有缓存。'+r.results.filter((x:any)=>x.status!=='OK').map((x:any)=>tmdbErrors['TMDB_'+x.status]||'上游暂不可用').join(' ')
  await load();tmdbChanged()
 }catch(e){tmdbNotice.value=(e as Error).message}finally{tmdbBusy.value=false}
}
function change(id:string){router.push({path:'/recommendations',query:{...route.query,channel:id}})}
function filter(){const query:Record<string,string>={channel:channel.value};for(const [key,value] of Object.entries({keyword:keyword.value.trim(),genre:genre.value,language:language.value,year_from:yearFrom.value,year_to:yearTo.value,runtime_max:runtime.value,min_votes:minVotes.value,scope:localOnly.value?'local':'',unwatched:unwatched.value?'true':''}))if(value)query[key]=value;const target={path:'/recommendations',query};if(router.resolve(target).fullPath===route.fullPath)load();else router.push(target)}
function detail(movie:any){feedback.value?.click((data.value?.items?.indexOf(movie)??-1)+1);router.push({path:'/movies',query:{...route.query,from:'recommendations'},hash:'#movie/'+movie.movie_id})}
function stamp(value?:string){if(!value)return '未提供';const d=new Date(value);return Number.isNaN(d.getTime())?'未提供':d.toLocaleString('zh-CN',{hour12:false})}
function money(value:number){return new Intl.NumberFormat('zh-CN',{style:'currency',currency:'USD',maximumFractionDigits:0}).format(value)}
const onBehavior=()=>load()
onMounted(async()=>{window.addEventListener('mi2-behavior-changed',onBehavior);window.addEventListener('mi2-tmdb-status',onBehavior);initController?.abort();initController=new AbortController();const signal=initController.signal;try{const [c,g,l]=await Promise.all([get('recommendation-channels',signal),get('genres',signal),get('languages',signal).catch(()=>({items:[]}))]);if(signal.aborted)return;channels.value=c.items;genres.value=g.items;languages.value=l.items||[];await load()}catch(e){if(!signal.aborted){error.value=(e as Error).message;loading.value=false}}})
watch(()=>route.fullPath,()=>{if(channels.value.length)load()})
onBeforeUnmount(()=>{feedback.value?.dispose();initController?.abort();controller?.abort();window.removeEventListener('mi2-behavior-changed',onBehavior);window.removeEventListener('mi2-tmdb-status',onBehavior)})
</script>
<template>
 <div class="recommend-shell" :style="{'--nav-width':expanded?'280px':'52.5px'}"><AppSidebar @expanded-change="expanded=$event"/>
 <main class="recommend-main"><h1 class="visually-hidden">推荐频道</h1>
<div class="top-actions"><router-link to="/home">返回影院首页</router-link></div>
 <nav class="channel-nav" aria-label="推荐频道"><button v-for="c in channels" :key="c.id" :aria-pressed="channel===c.id" :class="{active:channel===c.id}" @click="change(c.id)">{{c.title}}</button></nav>
 <section class="board-heading"><h2>{{selected?.title||'推荐频道'}}</h2><p>{{selected?.description}}</p></section>
 <div class="tmdb-toolbar"><span>{{data?.tmdb_connection?.last_error?'TMDB 在线读取未成功 · 使用缓存':data?.tmdb_connection?.configured&&!data?.tmdb_connection?.enabled?'TMDB 连接已停用 · 使用缓存':data?.tmdb_connection?.enabled?'TMDB 在线连接已启用 · 按需获取':'TMDB 未连接 · 使用现有缓存'}}</span><router-link :to="accountSettingsTarget('tmdb',route.fullPath)">TMDB 数据连接设置</router-link><button v-if="data?.tmdb_connection?.enabled" :disabled="tmdbBusy||loading" @click="updateTMDB">{{tmdbBusy?'正在更新…':selected?.public?'立即更新此热榜':'更新前 '+(data?.tmdb_connection?.max_detail_refresh||6)+' 部影片资料'}}</button></div><p v-if="tmdbNotice" role="status" class="notice">{{tmdbNotice}}</p>
 <form class="filters" @submit.prevent="filter">
  <label>电影名称<input v-model="keyword" maxlength="100" placeholder="搜索当前频道"/></label>
  <label>类型<select v-model="genre"><option value="">全部类型</option><option v-for="g in genres" :key="g.genre_id" :value="g.name_zh">{{g.name_zh}}</option></select></label><label>语言<select v-model="language"><option value="">全部语言</option><option v-for="l in languageOptions" :key="l.code" :value="l.code">{{l.label}}</option></select></label>
  <label>起始年份<input v-model="yearFrom" type="number" min="1880" max="2100" placeholder="不限"/></label>
  <label>截止年份<input v-model="yearTo" type="number" min="1880" max="2100" placeholder="不限"/></label>
  <label>最长时长<select v-model="runtime"><option value="">不限</option><option value="90">90 分钟</option><option value="120">120 分钟</option><option value="150">150 分钟</option><option value="180">180 分钟</option></select></label>
  <label>最低票数<input v-model="minVotes" type="number" min="1" max="10000000" :placeholder="channel==='top_rated'?'默认100票':'不限'"/></label>
  <label class="check" v-if="selected?.public"><input type="checkbox" v-model="localOnly"/>只看已入库</label>
  <label class="check"><input type="checkbox" v-model="unwatched"/>排除已看</label><button class="apply" type="submit">应用筛选</button><router-link :to="{path:'/recommendations',query:{channel}}">重置</router-link>
 </form>
 <div v-if="loading" class="state" role="status">正在读取推荐与个人状态…</div>
 <div v-else-if="error" class="state" role="alert"><p>{{error}}</p><button @click="load">重试</button><router-link to="/login">重新登录</router-link></div>
 <template v-else-if="data"><div class="provenance"><span>{{selected?.public?'官方榜单采集':'库内资料更新'}}：{{stamp(data.source_as_of)}}</span><span v-if="data.period">片单周期：{{data.period}} · 北京时间</span><span>{{data.candidate_count||0}} 部符合条件 · 零模型调用</span><button @click="load">重新读取</button></div>
 <p v-if="data.status==='STALE'" class="notice" role="status">当前为缓存榜单，请以采集时间为准。</p><p v-for="note in data.limitations" :key="note" class="notice">{{note}}</p>
 <div v-if="!data.items.length" class="state"><h3>{{data.status==='UNAVAILABLE'?'官方榜单尚未就绪':'暂无符合条件的电影'}}</h3><p>可以减少筛选条件；个人频道需要相应的收藏、评分或想看记录。</p><router-link to="/movies">浏览电影并完善偏好</router-link></div>
 <div v-else class="poster-grid"><article v-for="(movie,index) in data.items" :key="sequence+':'+movie.key" v-feedback="{board:feedback,position:Number(index)+1}" class="movie-card" :data-movie-id="movie.movie_id||''">
  <button v-if="movie.in_catalog" class="board-poster" :aria-label="'查看《'+movie.title+'》详情'" @click="detail(movie)"><img v-if="posterOf(movie)" :src="posterOf(movie)" :alt="movie.title+'海报'" loading="lazy" @error="posterError(movie,$event)"/><span v-else class="fallback">{{movie.title}}<small>暂无可用海报</small></span></button>
  <a v-else class="board-poster" @click="feedback?.click(Number(index)+1)" :href="movie.tmdb_url" target="_blank" rel="noopener noreferrer" :aria-label="'在 TMDB 查看《'+movie.title+'》（新标签页）'"><img v-if="posterOf(movie)" :src="posterOf(movie)" :alt="movie.title+'海报'" loading="lazy" @error="posterError(movie,$event)"/><span v-else class="fallback">{{movie.title}}<small>暂无可用海报</small></span><span class="external">TMDB</span></a>
  <h3>{{movie.title}}</h3><p class="meta">{{movie.release_year||'年份未提供'}}<template v-if="movie.runtime_minutes"> · {{movie.runtime_minutes}} 分钟</template></p><p class="genre">{{movie.genres?.join(' / ')||'类型未提供'}}</p>
  <p v-if="channel==='revenue'" class="metric">累计 {{money(movie.revenue)}}<small v-if="movie.revenue_as_of" class="meta"> · 资料 {{new Date(movie.revenue_as_of).toLocaleDateString('zh-CN')}}</small></p><p v-else-if="movie.tmdb_rating!=null" class="metric">TMDB {{movie.tmdb_rating.toFixed(1)}}<span> · {{movie.vote_count.toLocaleString()}} 票</span></p>
  <p v-if="selected?.public&&movie.tmdb_popularity!=null" class="meta">热度 {{movie.tmdb_popularity.toFixed(1)}}</p><p class="reason" :title="movie.reasons?.join('；')">{{movie.reasons?.join('；')}}</p>
 </article></div></template>
 </main></div>
</template>
<style scoped>
:global(body:has(.recommend-shell)){margin:0;background:#0c0d0f;color:#fff}
.recommend-shell{--ink:#0c0d0f;--ink-soft:#141519;--paper:#fff;--paper-dim:rgba(255,255,255,.82);--muted:rgba(255,255,255,.58);--line:rgba(241,236,226,.14);--line-strong:rgba(241,236,226,.26);--accent:#c8a46a;--serif:'Noto Serif SC','Source Han Serif SC','Songti SC',Georgia,'Times New Roman',serif;--sans:Inter,'PingFang SC','Microsoft YaHei',sans-serif;min-height:100vh;background:var(--ink);color:var(--paper);font-family:var(--sans)}
.recommend-main{margin-left:var(--nav-width,52.5px);width:calc(100% - var(--nav-width,52.5px));max-width:none;box-sizing:border-box;container:recommendations / inline-size;padding:40px clamp(22px,3.4vw,56px) 64px;transition:margin-left .25s;min-width:0}
.recommend-main *{box-sizing:border-box}
.recommend-main a{color:var(--paper-dim);text-underline-offset:4px;text-decoration-color:var(--line-strong)}
.recommend-main button,.recommend-main input,.recommend-main select{font:inherit;color:inherit}
.recommend-main button{cursor:pointer}
.recommend-main :focus-visible{outline:2px solid var(--accent);outline-offset:3px}
.recommend-main ::selection{background:var(--accent);color:var(--ink)}
.board-heading p{color:var(--muted);font-size:13px;line-height:1.7;margin:0}
.channel-nav{display:flex;flex-wrap:wrap;gap:4px;padding:0;border-bottom:1px solid var(--line)}
.channel-nav button{background:transparent;border:0;border-bottom:2px solid transparent;border-radius:0;padding:11px 14px;margin-bottom:-1px;font-size:13px;color:var(--muted);transition:color .16s,border-color .16s}
.channel-nav button:hover{color:var(--paper)}
.channel-nav button.active{background:transparent;border-bottom-color:var(--accent);color:var(--paper);font-weight:500}
.board-heading{margin:28px 0 16px}
.board-heading h2{font-family:var(--serif);font-size:28px;font-weight:500;letter-spacing:-.01em;margin:0 0 7px;color:var(--paper)}
.tmdb-toolbar{display:flex;align-items:center;flex-wrap:wrap;gap:10px 18px;margin:0 0 20px;padding:11px 0;border-top:1px solid var(--line);border-bottom:1px solid var(--line);color:var(--muted);font-size:11px;line-height:1.7}
.tmdb-toolbar a{color:var(--paper-dim)}
.tmdb-toolbar button{background:transparent;border:1px solid var(--line-strong);padding:7px 11px;border-radius:7px;color:var(--paper);font-size:11px}
.tmdb-toolbar button:hover{border-color:var(--paper)}
.tmdb-toolbar button:disabled{opacity:.45;cursor:not-allowed}
.notice{font-size:11px;line-height:1.7;color:var(--accent);margin:6px 0}
.filters{display:grid;grid-template-columns:repeat(auto-fit,minmax(145px,1fr));gap:12px 14px;align-items:end;padding:0 0 22px;border-bottom:1px solid var(--line);margin-bottom:18px}
.filters label{display:flex;flex-direction:column;gap:6px;color:var(--muted);font-size:11px;min-width:0}
.filters input,.filters select{height:40px;width:100%;max-width:none;background:var(--ink-soft);border:1px solid var(--line);border-radius:7px;padding:6px 10px;color:var(--paper);font-size:12px}
.filters input::placeholder{color:var(--muted)}
.filters input:focus,.filters select:focus{border-color:var(--line-strong);outline:none}
.filters input[type=number]{width:100%}
.filters .check{flex-direction:row;align-items:center;min-height:40px;gap:8px;color:var(--paper-dim);white-space:nowrap}
.filters input[type=checkbox]{accent-color:var(--accent);width:14px;height:14px;margin:0;flex:none}
.filters .apply,.state button{padding:10px 14px;background:transparent;color:var(--paper);border:1px solid var(--paper);border-radius:7px;font-size:12px;font-weight:500;height:40px;white-space:nowrap}
.filters .apply:hover,.state button:hover{background:rgba(255,255,255,.08)}
.filters>a{font-size:12px;line-height:40px;color:var(--muted);text-decoration:none;white-space:nowrap}
.filters>a:hover{color:var(--paper)}
.provenance{display:flex;flex-wrap:wrap;gap:8px 20px;color:var(--muted);font-size:11px;line-height:1.7;align-items:center;margin:0 0 16px}
.provenance button{background:none;border:0;color:var(--paper-dim);text-decoration:underline;text-underline-offset:3px;text-decoration-color:var(--line-strong);padding:0;font-size:11px}
.poster-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(172px,1fr));gap:30px 18px;margin-top:22px}
.movie-card{min-width:0}
.board-poster{display:block;width:100%;aspect-ratio:2/3;position:relative;overflow:hidden;background:var(--ink-soft);border:1px solid rgba(255,255,255,.16);border-radius:14px;padding:0;color:var(--paper);text-decoration:none;transition:transform .22s ease,border-color .22s ease,box-shadow .22s ease;box-shadow:0 5px 17px #0003}
.board-poster:hover,.board-poster:focus-within{transform:translateY(-4px);border-color:rgba(241,236,226,.38);box-shadow:0 16px 35px #0009}
.board-poster img{width:100%;height:100%;object-fit:cover;display:block}
.external{position:absolute;left:10px;right:auto;bottom:10px;background:rgba(12,13,15,.78);border:1px solid rgba(255,255,255,.28);color:var(--paper);padding:5px 8px;border-radius:999px;font-size:9px;line-height:1.2;letter-spacing:.03em;backdrop-filter:blur(3px)}
.fallback{height:100%;display:flex;flex-direction:column;align-items:center;justify-content:center;padding:20px;text-align:center;gap:10px;line-height:1.6;font-family:var(--serif);font-size:18px;color:var(--paper)}
.fallback small{color:var(--muted);font-family:var(--sans);font-size:10px}
.movie-card h3{font-size:14px;line-height:1.45;font-weight:500;margin:11px 0 4px;color:var(--paper);overflow-wrap:anywhere}
.meta,.genre{font-size:11px;color:var(--muted);line-height:1.65;margin:2px 0}
.metric{color:var(--accent);font-size:13px;margin:8px 0 4px;font-variant-numeric:tabular-nums}
.metric span{color:var(--muted);font-size:10px}
.reason{font-size:11px;line-height:1.65;color:var(--muted);margin:5px 0}
.state{padding:64px 0;color:var(--muted);line-height:1.8;min-height:240px}
.state h3{font-family:var(--serif);font-size:24px;font-weight:500;color:var(--paper);margin:0 0 8px}
.state p{font-size:13px}
.state button{margin-right:16px}
@media(min-width:2000px){.poster-grid{grid-template-columns:repeat(auto-fill,minmax(190px,1fr))}}
@media(max-width:1100px){.poster-grid{grid-template-columns:repeat(auto-fill,minmax(150px,1fr));gap:24px 14px}}
@media(max-width:700px){.recommend-main{padding:26px 16px 56px}.channel-nav{gap:0;overflow-x:auto;flex-wrap:nowrap;scrollbar-width:none}.channel-nav::-webkit-scrollbar{display:none}.channel-nav button{padding:11px 12px;white-space:nowrap}.board-heading h2{font-size:24px}.filters{display:flex;flex-wrap:nowrap;overflow-x:auto;align-items:flex-end;gap:9px;padding-bottom:14px;scrollbar-width:thin;scrollbar-color:rgba(255,255,255,.22) transparent}.filters label{flex:0 0 148px}.filters .apply{flex:0 0 92px}.filters>a{grid-column:auto;flex:0 0 auto;line-height:40px;padding:0 4px}.poster-grid{grid-template-columns:repeat(2,minmax(0,1fr));gap:24px 12px}.movie-card h3{font-size:13px}.provenance{gap:6px 14px}}
@media(prefers-reduced-motion:reduce){.recommend-main,.board-poster,.channel-nav button{transition:none}.board-poster:hover,.board-poster:focus-within{transform:none;box-shadow:0 5px 17px #0003}}

@container recommendations (max-width:900px){.filters{display:flex;flex-wrap:wrap;gap:12px}.filters label{flex:1 1 140px;min-width:0}.filters .apply{flex:0 0 auto}}
@container recommendations (max-width:540px){.filters{flex-wrap:nowrap;overflow-x:auto;align-items:flex-end}.filters label{flex:0 0 148px}.filters .apply{flex:0 0 92px}}
@media(max-width:700px){.recommend-main{margin-left:52.5px;width:calc(100% - 52.5px)}}

/* 排版重构：标题区收薄、频道改为单行横滚、资料行并成一条、筛选压缩成两行内，海报提前进入首屏 */
.recommend-main{padding:32px clamp(20px,3vw,48px) 56px}
.channel-nav{flex-wrap:nowrap;overflow-x:auto;gap:2px;margin:0;scrollbar-width:none}
.channel-nav::-webkit-scrollbar{display:none}
.channel-nav button{padding:10px 12px;font-size:12.5px;white-space:nowrap}
.board-heading{margin:18px 0 12px}
.board-heading h2{font-size:24px;margin:0 0 5px}
.board-heading p{font-size:12.5px;line-height:1.6}
.tmdb-toolbar{border-top:0;border-bottom:1px solid var(--line);padding:0 0 10px;margin:0 0 12px;font-size:10.5px;gap:6px 14px}
.tmdb-toolbar button{padding:5px 9px;border-radius:6px;font-size:10.5px}
.notice{margin:4px 0 8px;font-size:10.5px}
.filters{grid-template-columns:repeat(auto-fit,minmax(118px,1fr));gap:8px 10px;padding:0 0 14px;margin-bottom:14px;align-items:end}
.filters label{gap:4px;font-size:10.5px}
.filters input,.filters select{height:34px;border-radius:6px;padding:4px 8px;font-size:12px}
.filters .check{min-height:34px;gap:7px;font-size:11.5px}
.filters .apply,.state button{height:34px;padding:0 12px;border-radius:6px;font-size:11.5px}
.filters>a{line-height:34px;font-size:11.5px}
.provenance{margin:0 0 14px;font-size:10.5px;gap:4px 14px}
.provenance button{font-size:10.5px}
.poster-grid{margin-top:6px;gap:28px 18px;grid-template-columns:repeat(auto-fill,minmax(min(100%,180px),1fr))}
.movie-card h3{font-size:14px;margin:10px 0 2px}
.meta,.genre{font-size:10.5px;line-height:1.55;margin:1px 0}
.metric{font-size:12.5px;margin:6px 0 2px}
.metric span{font-size:10px}
.reason{font-size:10.5px;line-height:1.55;margin:4px 0 0;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden}
.external{border-radius:4px;padding:3px 6px;font-size:9px}
@media(max-width:1100px){.poster-grid{grid-template-columns:repeat(auto-fill,minmax(min(100%,160px),1fr));gap:24px 16px}}
/* 与电影库同处理：去掉可见页头，内容从顶部起排，标题只留给读屏与文档结构；返回链接保留 */
.recommend-main{padding-top:24px}
.visually-hidden{position:absolute;width:1px;height:1px;padding:0;margin:-1px;overflow:hidden;clip:rect(0 0 0 0);white-space:nowrap;border:0}
.top-actions{display:flex;justify-content:flex-end;margin:0 0 4px}
.top-actions a{font-size:11.5px;color:var(--paper-dim);text-decoration:none;border-bottom:1px solid var(--line);padding-bottom:3px}
.top-actions a:hover{border-color:var(--paper)}
@media(max-width:700px){
 .recommend-main{padding:16px 16px 48px}
 .board-heading{margin:14px 0 10px}
 .board-heading h2{font-size:21px}
 .tmdb-toolbar{font-size:10px;gap:4px 12px;padding-bottom:8px;margin-bottom:10px}
 .poster-grid{gap:24px 12px;margin-top:4px}
}
/* 筛选条吸顶：与电影库索引栏一致，下滚时固定在视口顶部 */
.filters{position:sticky;top:0;z-index:30;background:var(--ink);padding:10px 0 14px}
@media(max-width:700px){.filters{padding:8px 0 12px}}

/* 工具栏与海报共用居中的列轨道，缩放窗口时左右边缘同步对齐。 */
.recommend-main{--poster-width:263px;--poster-gap:22px;display:grid;grid-template-columns:repeat(auto-fill,minmax(min(100%,var(--poster-width)),var(--poster-width)));column-gap:var(--poster-gap);justify-content:center;align-content:start}
.recommend-main>:not(.visually-hidden){grid-column:1/-1;min-width:0}
.poster-grid{grid-template-columns:repeat(auto-fill,minmax(min(100%,var(--poster-width)),var(--poster-width)));justify-content:start;gap:32px var(--poster-gap)}
@media(max-width:1100px){.recommend-main{--poster-gap:20px}.poster-grid{row-gap:28px}}
/* 海报上的标注与海报同宽 */
.movie-card .external{left:0!important;right:0!important;bottom:0!important;width:100%!important;display:block!important;text-align:center!important;padding:5px 0!important;border:0!important;border-top:1px solid rgba(255,255,255,.18)!important;border-radius:0 0 14px 14px!important;background:rgba(12,13,15,.82)!important;letter-spacing:.08em!important}
.movie-card .board-poster{border-radius:14px!important;overflow:hidden!important}</style>