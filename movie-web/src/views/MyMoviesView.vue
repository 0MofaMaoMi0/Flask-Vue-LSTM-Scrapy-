<script setup lang="ts">
import {ref,computed,watch,onMounted,onBeforeUnmount} from 'vue'
import {useRouter,useRoute} from 'vue-router'
import AppSidebar from '../components/AppSidebar.vue'
import MovieGlobe from '../components/MovieGlobe.vue'
import {movieArtwork} from '../services/movieArtwork'
import {personalRequest} from '../services/personalFeatures'
import {MOVIE_COUNTRIES} from '../data/movieCountries'
import {ISO2_TO_ISO3} from '../lib/globe/iso2to3.js'
import {countryName,flagUrl,flagAlt} from '../lib/globe/countryDisplay.js'
import '../styles/globe.css'

type Tab='globe'|'seen'|'favorite'|'want'|'insights'
const router=useRouter(),route=useRoute()
const expanded=ref(localStorage.getItem('mi2_nav_expanded')==='true')
const movies=ref<any[]>([]),behavior=ref<Record<string,any>>({}),busy=ref(true),error=ref(''),failed=ref<Record<string,boolean>>({})
const countryMap=ref<Record<string,string[]>>({...MOVIE_COUNTRIES})
function countriesOf(id:any){return countryMap.value[String(id)]||[]}
const metric=ref('catalog_count'),selected=ref(''),globeSearch=ref(''),labels=ref<string[]>([]),autoRotate=ref(true),globeRef=ref<any>(null)
const controller=new AbortController()
let loadController:AbortController|undefined,loadSeq=0,selfBehaviorEvent=false
const MAX_CATALOG_PAGES=10,LIST_STEP=48,listLimit=ref(LIST_STEP)
const viewLabel=computed(()=>({globe:'世界电影分布',seen:'观看记录',favorite:'收藏清单',want:'想看清单',insights:'观影数据'} as Record<Tab,string>)[tab.value])
const tab=computed<Tab>(()=>{const v=String(route.query.view||'globe');return (['globe','seen','favorite','want','insights'] as string[]).includes(v)?v as Tab:'globe'})
watch(tab,()=>{listLimit.value=LIST_STEP})
const list=computed(()=>{
  const all=movies.value
  if(tab.value==='seen')return all.filter(m=>behavior.value[String(m.movie_id)]?.watched)
  if(tab.value==='favorite')return all.filter(m=>behavior.value[String(m.movie_id)]?.favorite)
  return all.filter(m=>behavior.value[String(m.movie_id)]?.want)
})
const visibleList=computed(()=>list.value.slice(0,listLimit.value))
const watchedMovies=computed(()=>movies.value.filter(m=>behavior.value[String(m.movie_id)]?.watched))
const insights=computed(()=>{
  const list=watchedMovies.value
  const ratings=list.map(m=>behavior.value[String(m.movie_id)]?.rating).filter((r:any)=>typeof r==='number')
  const favorite=list.filter(m=>behavior.value[String(m.movie_id)]?.favorite).length
  const genreMap=new Map<string,number>()
  for(const m of list)for(const g of (m.genres||[]))genreMap.set(g.name_zh,(genreMap.get(g.name_zh)||0)+1)
  const genres=[...genreMap.entries()].sort((a,b)=>b[1]-a[1]).slice(0,6)
  const countryMap=new Map<string,number>()
  for(const m of list)for(const c of countriesOf(m.movie_id))countryMap.set(c,(countryMap.get(c)||0)+1)
  const countries=[...countryMap.entries()].sort((a,b)=>b[1]-a[1])
  return {count:list.length,rated:ratings.length,avg:ratings.length?(ratings.reduce((a:number,b:number)=>a+b,0)/ratings.length).toFixed(2):'—',coverage:list.length?Math.round(ratings.length/list.length*100):0,favorite,genres,countries,maxGenre:genres.length?genres[0][1]:1}
})
const trend=computed(()=>{
  const now=new Date(),rows:{key:string,label:string,count:number}[]=[]
  for(let i=11;i>=0;i--){const d=new Date(now.getFullYear(),now.getMonth()-i,1);rows.push({key:d.getFullYear()+'-'+String(d.getMonth()+1).padStart(2,'0'),label:(d.getMonth()+1)+'月',count:0})}
  const index=new Map(rows.map((r,i)=>[r.key,i]))
  for(const m of watchedMovies.value){
    const v=behavior.value[String(m.movie_id)]?.watched_at
    if(!v)continue
    const d=new Date(v)
    if(isNaN(d.getTime()))continue
    const i=index.get(d.getFullYear()+'-'+String(d.getMonth()+1).padStart(2,'0'))
    if(i!==undefined)rows[i].count++
  }
  return {rows,max:Math.max(1,...rows.map(r=>r.count))}
})
const filterOpen=ref(false),filterGenre=ref(''),onlyFavorite=ref(false),onlyChecked=ref(false)
const catalogStats=computed(()=>{
  const map=new Map<string,any>()
  for(const m of movies.value){
    const s=behavior.value[String(m.movie_id)]||{}
    for(const iso of countriesOf(m.movie_id)){
      let c=map.get(iso)
      if(!c){c={iso3:iso,catalog_count:0,watched_count:0,favorite_count:0,ratings:[],movies:[],genreMap:new Map()};map.set(iso,c)}
      c.catalog_count++
      for(const g of m.genres||[])c.genreMap.set(g.name_zh,(c.genreMap.get(g.name_zh)||0)+1)
      if(s.watched){c.watched_count++;c.movies.push({movie_id:m.movie_id,title:m.title,rating:typeof s.rating==='number'?s.rating:null});if(typeof s.rating==='number')c.ratings.push(s.rating)}
      if(s.favorite)c.favorite_count++
    }
  }
  const countries=[...map.values()].map((c:any)=>({...c,average_rating:c.ratings.length?Math.round(c.ratings.reduce((a:number,b:number)=>a+b,0)/c.ratings.length*100)/100:null,rating_sample_count:c.ratings.length,genres:[...c.genreMap.entries()].sort((a:any,b:any)=>b[1]-a[1])}))
  return {total_unique_movies:movies.value.length,covered_country_count:countries.length,countries}
})
const genreOptions=computed(()=>{const map=new Map<string,number>();for(const m of movies.value)for(const g of m.genres||[])map.set(g.name_zh,(map.get(g.name_zh)||0)+1);return [...map.entries()].sort((a,b)=>b[1]-a[1]).slice(0,10).map(([n])=>n)})
const ranks=computed(()=>catalogStats.value.countries.slice().sort((a:any,b:any)=>(b[metric.value]||0)-(a[metric.value]||0)))
const visibleRanks=computed(()=>ranks.value.filter((c:any)=>{
  const q=globeSearch.value.toLowerCase()
  if(q&&!countryName(c.iso3).includes(globeSearch.value)&&!c.iso3.toLowerCase().includes(q))return false
  if(onlyChecked.value&&!labels.value.includes(c.iso3))return false
  if(onlyFavorite.value&&!c.favorite_count)return false
  if(filterGenre.value&&!c.genres.some(([n]:any)=>n===filterGenre.value))return false
  return true
}))
const activeFilters=computed(()=>[filterGenre.value,onlyFavorite.value,onlyChecked.value].filter(Boolean).length)
const selectedStat=computed(()=>catalogStats.value.countries.find((c:any)=>c.iso3===selected.value))
const overviewGenres=computed(()=>{const map=new Map<string,number>();for(const m of movies.value)for(const g of m.genres||[])map.set(g.name_zh,(map.get(g.name_zh)||0)+1);return [...map.entries()].sort((a,b)=>b[1]-a[1]).slice(0,6) as [string,number][]})
const overviewMax=computed(()=>Math.max(1,...overviewGenres.value.map(([,n])=>n)))
const detailGenres=computed<[string,number][]>(()=>(selectedStat.value?.genres||[]).slice(0,6))
const detailMax=computed(()=>Math.max(1,...detailGenres.value.map(([,n])=>n)))
const personalTop=computed(()=>movies.value.filter(m=>behavior.value[String(m.movie_id)]?.watched).map(m=>({...m,rating:behavior.value[String(m.movie_id)]?.rating??null})).sort((a,b)=>((b.rating||0)-(a.rating||0))).slice(0,3))
function toggleAll(){const all=visibleRanks.value.map((c:any)=>c.iso3);const on=all.every((x:string)=>labels.value.includes(x));labels.value=on?labels.value.filter(x=>!all.includes(x)):[...new Set([...labels.value,...all])]}
function resetFilters(){globeSearch.value='';filterGenre.value='';onlyFavorite.value=false;onlyChecked.value=false}
function choose(code:string){if(selected.value===code){globeRef.value?.focus(code);return}selected.value=code;autoRotate.value=false;globeRef.value?.setAutoRotate(false)}
function resetGlobe(){if(selected.value){selected.value=''}else{globeRef.value?.reset()}}
function toggleRotate(){autoRotate.value=!autoRotate.value;globeRef.value?.setAutoRotate(autoRotate.value)}
function toggleLabel(code:string,on:boolean){labels.value=on?[...new Set([...labels.value,code])]:labels.value.filter(x=>x!==code)}
function clearLabels(){labels.value=[]}
function genres(m:any){return (m?.genres||[]).map((g:any)=>g.name_zh).join(' / ')}
function stateOf(m:any){return behavior.value[String(m.movie_id)]||{}}
function watchedAt(m:any){const v=stateOf(m).watched_at;if(!v)return '—';const d=new Date(v);return isNaN(d.getTime())?'—':d.toLocaleDateString('zh-CN')}
function label(m:any){const s=stateOf(m);if(s.watched)return '已看';if(s.want)return '想看';if(s.favorite)return '已收藏';return ''}
const movieNotes=ref<Record<string,{note:string,version:number}>>({}),editNote=ref(''),notesReady=ref(false)
const editOpen=ref(false),editMovie=ref<any>(null),editSeen=ref(false),editFav=ref(false),editWant=ref(false),editRating=ref(''),editSaving=ref(false),editError=ref('')
const editLoadingNote=ref(false)
let editLoadVersion=0
async function openEdit(m:any){
  const version=++editLoadVersion,s=behavior.value[String(m.movie_id)]||{}
  editMovie.value=m;editSeen.value=!!s.watched;editFav.value=!!s.favorite;editWant.value=!!s.want;editRating.value=typeof s.rating==='number'?String(s.rating):''
  editNote.value='';editError.value='';editOpen.value=true;editLoadingNote.value=true;notesReady.value=false
  try{
    const data=await personalRequest('movie-notes')
    if(version!==editLoadVersion||!editOpen.value)return
    movieNotes.value=data.items;editNote.value=data.items[String(m.movie_id)]?.note||'';notesReady.value=true
  }catch(e){if(version===editLoadVersion&&editOpen.value)editError.value='备注读取失败，可关闭后重开；其它记录仍可保存。'}
  finally{if(version===editLoadVersion)editLoadingNote.value=false}
}
function closeEdit(){if(editSaving.value)return;++editLoadVersion;editLoadingNote.value=false;editOpen.value=false;editMovie.value=null}
async function saveEdit(){
  const m=editMovie.value;if(!m||editSaving.value||editLoadingNote.value)return
  editSaving.value=true;editError.value=''
  try{
    const c=await fetch('/api/v1/auth/csrf',{credentials:'same-origin',signal:controller.signal})
    let cb:any=null;try{cb=await c.json()}catch{cb=null}
    const token=cb?.data?.csrf_token
    if(!c.ok||!token||(cb?.code!==0&&cb?.code!=='OK'))throw new Error('登录状态已失效，请重新登录。')
    const s=behavior.value[String(m.movie_id)]||{}
    const ops:[string,boolean|number|null][]=[]
    if(editSeen.value!==!!s.watched)ops.push(['watched',editSeen.value])
    if(editFav.value!==!!s.favorite)ops.push(['favorite',editFav.value])
    if(editWant.value!==!!s.want)ops.push(['want',editWant.value])
    const cur=typeof s.rating==='number'?s.rating:null
    const next=editRating.value===''?null:Number(editRating.value)
    if(next!==cur)ops.push(['rating',next])
    for(const [kind,val] of ops){
      const payload={event_id:crypto.randomUUID(),movie_id:m.movie_id,kind,action:val===false||val===null?'undo':'set',value:kind==='rating'?val:null,time:new Date().toISOString(),source:'USER_ACTION'}
      const r=await fetch('/api/catalog/behavior',{method:'POST',credentials:'same-origin',signal:controller.signal,headers:{'Content-Type':'application/json','X-CSRF-Token':token},body:JSON.stringify(payload)})
      let rb:any=null;try{rb=await r.json()}catch{rb=null}
      if(!r.ok||rb?.code!=='OK')throw new Error(r.status===409?'与其它状态冲突，请先撤销冲突项。':'保存未确认，请重试。')
    }
    behavior.value=(await api('behavior')).state
    const previous=movieNotes.value[String(m.movie_id)]
    if(notesReady.value&&editNote.value!==(previous?.note||'')){const saved=await personalRequest('movie-notes',{movie_id:m.movie_id,note:editNote.value,version:previous?.version||0},controller.signal);movieNotes.value[String(m.movie_id)]=saved}
    selfBehaviorEvent=true;try{window.dispatchEvent(new Event('mi2-behavior-changed'))}finally{selfBehaviorEvent=false}
    editOpen.value=false;editMovie.value=null
  }catch(e){editError.value=(e as Error).message}finally{editSaving.value=false}
}
function open(m:any){router.push({path:'/movies',query:{from:'behavior',view:tab.value},hash:'#movie/'+m.movie_id})}
async function api(path:string,outerSignal?:AbortSignal){
  const signals=[controller.signal,AbortSignal.timeout(20000)];if(outerSignal)signals.push(outerSignal)
  const r=await fetch('/api/catalog/'+path,{credentials:'same-origin',signal:AbortSignal.any(signals)});let body:any=null;try{body=await r.json()}catch{body=null}
  if(!r.ok)throw new Error(r.status===401?'登录已失效，请重新登录。':(r.status===503||body?.code==='DATA_UNAVAILABLE')?'个人片库缓存正在预热，请稍后重试。':'个人片库暂时不可用，请重试。')
  if(!body||body.code!=='OK'||body.data==null)throw new Error('个人片库响应异常，请重试。')
  return body.data
}
async function load(){
  const ticket=++loadSeq;loadController?.abort();const current=new AbortController();loadController=current
  const signal=current.signal
  busy.value=true;error.value=''
  try{
    const [firstPage,state]=await Promise.all([api('movies?limit=100&sort=id&offset=0',signal),api('behavior',signal)])
    if(ticket!==loadSeq||signal.aborted)return
    const byId=new Map<number,any>()
    const addRows=(rows:any[])=>{for(const item of rows||[])if(item?.movie_id!=null&&!byId.has(item.movie_id))byId.set(item.movie_id,item)}
    const firstItems=firstPage.items||[]
    addRows(firstItems)
    const pageLimit=Number(firstPage.limit)||100
    let reachedEnd=firstItems.length<pageLimit
    let pageIndex=1
    while(!reachedEnd&&pageIndex<MAX_CATALOG_PAGES){
      const batchOffsets:number[]=[]
      for(let i=0;i<3&&pageIndex<MAX_CATALOG_PAGES;i++,pageIndex++)batchOffsets.push(pageIndex*pageLimit)
      const pages=await Promise.all(batchOffsets.map(off=>api('movies?limit='+pageLimit+'&sort=id&offset='+off,signal)))
      if(ticket!==loadSeq||signal.aborted)return
      for(const p of pages){
        const items=p.items||[]
        addRows(items)
        if(items.length<pageLimit)reachedEnd=true
      }
    }
    if(!reachedEnd){
      const statePages=await Promise.all([
        api('movies?limit=100&sort=updated&state=watched',signal).catch(()=>({items:[]})),
        api('movies?limit=100&sort=updated&state=favorite',signal).catch(()=>({items:[]})),
        api('movies?limit=100&sort=updated&state=want',signal).catch(()=>({items:[]})),
      ])
      if(ticket!==loadSeq||signal.aborted)return
      for(const sp of statePages)addRows(sp.items||[])
    }
    movies.value=[...byId.values()]
    behavior.value=state.state||{}
    try{const notes=await personalRequest('movie-notes',undefined,signal);if(ticket===loadSeq&&!signal.aborted){movieNotes.value=notes.items;notesReady.value=true}}catch{if(ticket===loadSeq)notesReady.value=false}
    try{
      const cc=await api('production-countries',signal)
      if(ticket!==loadSeq||signal.aborted)return
      const next:Record<string,string[]>={}
      for(const it of cc.items||[]){
        const isos=(it.countries||[]).map((c:any)=>(ISO2_TO_ISO3 as Record<string,string>)[c.iso2]).filter(Boolean)
        if(isos.length)next[String(it.movie_id)]=isos
      }
      if(Object.keys(next).length)countryMap.value=next
    }catch{}
  }catch(e){if(ticket===loadSeq&&!controller.signal.aborted&&!signal.aborted&&(e as Error).name!=='AbortError')error.value=(e as Error).message}
  finally{if(ticket===loadSeq)busy.value=false}
}
function closeFiltersOnOutside(e:PointerEvent){if(filterOpen.value&&!(e.target as HTMLElement)?.closest?.('.g-rank'))filterOpen.value=false}
function closeFiltersOnEscape(e:KeyboardEvent){if(e.key!=='Escape')return;filterOpen.value=false;if(editOpen.value)closeEdit()}
const refreshBehavior=()=>{if(selfBehaviorEvent)return;api('behavior').then(b=>behavior.value=b.state||{}).catch(()=>{if(!controller.signal.aborted)error.value='个人状态刷新失败，请重试。'})}
onMounted(()=>{load();window.addEventListener('mi2-behavior-changed',refreshBehavior);document.addEventListener('pointerdown',closeFiltersOnOutside);document.addEventListener('keydown',closeFiltersOnEscape)})
onBeforeUnmount(()=>{++loadSeq;loadController?.abort();controller.abort();window.removeEventListener('mi2-behavior-changed',refreshBehavior);document.removeEventListener('pointerdown',closeFiltersOnOutside);document.removeEventListener('keydown',closeFiltersOnEscape)})
</script>
<template>
<div class="my-shell" :style="{'--nav-width':expanded?'280px':'52.5px'}">
  <AppSidebar @expanded-change="expanded=$event"/>
  <main class="my-page">
    <header class="my-head"><h1>我的电影</h1><nav class="my-crumb" aria-label="当前视图"><strong>{{viewLabel}}</strong></nav></header>
    <p v-if="busy" class="my-state" role="status">正在读取你的观影记录…</p>
    <div v-else-if="error" class="my-state" role="alert"><strong>无法读取个人片库</strong><p>{{error}}</p><button @click="load" type="button">重试</button></div>

    <template v-else>
    <section v-if="tab==='insights'" class="insight-workspace" aria-label="我的观影数据">
      <div class="insight-cards">
        <article><small>观看电影</small><strong>{{insights.count}}</strong><span>仅统计已看记录</span></article>
        <article><small>个人平均评分</small><strong>{{insights.avg}}</strong><span>满分 5 分 · 样本 {{insights.rated}} 部</span></article>
        <article><small>评分覆盖率</small><strong>{{insights.coverage}}%</strong><span>{{insights.rated}} / {{insights.count}} 部有评分</span></article>
        <article><small>已看且收藏</small><strong>{{insights.favorite}}</strong><span>收藏与已看相互独立</span></article>
      </div>
      <div class="insight-columns">
        <section class="insight-panel">
          <h2>类型偏好</h2>
          <div v-for="[name,n] in insights.genres" :key="name" class="genre-row"><span>{{name}}</span><div class="track"><div class="fill" :style="{width:(n/insights.maxGenre*100)+'%'}"/></div><em>{{n}}</em></div>
          <p v-if="!insights.genres.length" class="g-empty">还没有已看电影，暂时无法计算类型偏好。</p>
        </section>
        <section class="insight-panel">
          <h2>制作国家</h2>
          <div v-for="[iso,n] in insights.countries" :key="iso" class="country-row"><img v-if="flagUrl(iso)" class="country-flag" :src="flagUrl(iso)!" width="24" height="16" :alt="countryName(iso)"/><span>{{countryName(iso)}}</span><b>{{n}} 部</b></div>
          <p v-if="!insights.countries.length" class="g-empty">还没有可归属制作国家的已看电影。</p>
          
        </section>
      </div>
      <section class="insight-panel trend-panel" aria-label="观看趋势">
        <h2>观看趋势 <small>近 12 个月 · 按观看时间</small></h2>
        <div v-if="watchedMovies.length" class="trend-chart">
          <div v-for="r in trend.rows" :key="r.key" class="trend-col">
            <span class="trend-value">{{r.count||''}}</span>
            <div class="trend-bar" :style="{height:Math.max(4,r.count/trend.max*92)+'px'}"></div>
            <small>{{r.label}}</small>
          </div>
        </div>
        <p v-else class="g-empty">还没有已看记录，暂时无法统计趋势。</p>
      </section>
    </section>

    <section v-show="tab==='globe'" class="globe-workspace" aria-label="世界电影分布">
      <aside class="g-rank" aria-label="制作国家排行">
        <h2>制作国家排行</h2>
        <div class="g-searchrow">
          <input v-model="globeSearch" class="g-search" type="search" placeholder="搜索国家" aria-label="搜索国家"/>
          <button class="g-filter-btn" type="button" :aria-expanded="filterOpen" @click="filterOpen=!filterOpen">筛选<span v-if="activeFilters" class="g-filter-count">{{activeFilters}}</span></button>
        </div>
        <div class="g-tools"><button type="button" @click="resetFilters">全部国家</button><span>已选 {{labels.length}} / {{catalogStats.countries.length}}</span><button type="button" @click="toggleAll">全部勾选</button></div>
        <p class="g-caption"><span>按电影库收录数量排序</span><span>{{visibleRanks.length}} 个国家</span></p>
        <div class="g-list">
          <div v-for="(c,index) in visibleRanks" :key="c.iso3" class="g-row" :class="{on:selected===c.iso3}">
            <button class="g-pick" type="button" :aria-label="'聚焦'+countryName(c.iso3)" @click="choose(c.iso3)">
              <span class="g-num">{{String(index+1).padStart(2,'0')}}</span>
              <img v-if="flagUrl(c.iso3)" class="country-flag" :src="flagUrl(c.iso3)!" :alt="flagAlt(c.iso3)" width="30" height="20"/>
              <span v-else class="country-code-flag">{{c.iso3}}</span>
              <span class="g-name">{{countryName(c.iso3)}}<small>{{c.iso3}}</small></span>
              <span class="g-val">{{c.catalog_count}} 部</span>
            </button>
            <input type="checkbox" :checked="labels.includes(c.iso3)" :aria-label="'在地球上显示'+countryName(c.iso3)+'标签'" @change="toggleLabel(c.iso3,($event.target as HTMLInputElement).checked)"/>
          </div>
          <p v-if="!visibleRanks.length" class="g-empty">没有匹配的国家</p>
        </div>
        <div v-if="filterOpen" class="g-filter" role="dialog" aria-label="筛选国家">
          <div class="g-filter-head"><strong>筛选国家</strong><button type="button" aria-label="关闭筛选" @click="filterOpen=false">×</button></div>
          <label class="g-field">类型偏好<select v-model="filterGenre"><option value="">全部类型</option><option v-for="g in genreOptions" :key="g" :value="g">{{g}}</option></select></label>
          <label class="g-check"><span>收藏过滤<small>只看有收藏记录的国家</small></span><input v-model="onlyFavorite" type="checkbox"/></label>
          <label class="g-check"><span>仅看已勾选<small>只查看已标记的国家</small></span><input v-model="onlyChecked" type="checkbox"/></label>
          <div class="g-filter-foot"><button type="button" @click="resetFilters">重置条件</button><button type="button" class="g-done" @click="filterOpen=false">完成</button></div>
        </div>
      </aside>

      <div class="g-stage" title="全库口径 · 制作国家按公开资料映射，目录尚未入库国家字段">
        <div class="g-sky-b" aria-hidden="true"></div>
        <p class="sr-only">全库口径；制作国家按公开资料映射，电影目录尚未入库国家字段；合拍片按国家重复计入，全库总量按电影去重。</p>
        <MovieGlobe ref="globeRef" :stats="catalogStats" :metric="metric" :selected-iso3="selected" :label-iso3="labels" :auto-rotate="autoRotate" @select="choose"/>
        <div class="g-actions" role="group" aria-label="地球操作">
          <button type="button" @click="toggleRotate">{{autoRotate?'暂停自转':'继续自转'}}</button>
          <button type="button" @click="resetGlobe">重置视角</button>
          <button type="button" :disabled="!labels.length" @click="clearLabels">清空勾选</button>
        </div>
        <p v-if="!movies.length" class="g-empty-stage">电影库暂无影片，地球没有可统计的数据</p>
      </div>

      <aside class="g-detail" aria-label="国家分析">
        <h2>国家分析</h2>
        <div class="g-country">
          <span class="g-country-icon"><img v-if="selectedStat&&flagUrl(selectedStat.iso3)" class="country-flag" :src="flagUrl(selectedStat.iso3)!" :alt="flagAlt(selectedStat.iso3)" width="42" height="28"/><svg v-else viewBox="0 0 24 24"><circle cx="12" cy="12" r="9"/><path d="M3 12h18M12 3c3.4 3 3.4 15 0 18"/></svg></span>
          <div><strong>{{selectedStat?countryName(selectedStat.iso3):'全库概览'}}</strong><small>{{selectedStat?selectedStat.iso3+' · 制作国家':'全库 '+catalogStats.total_unique_movies+' 部 · 制作国家分布'}}</small></div>
        </div>
        <dl class="g-metrics">
          <div><dt>{{selectedStat?'收录':'全库电影总数'}}</dt><dd>{{selectedStat?selectedStat.catalog_count:catalogStats.total_unique_movies}}</dd></div>
          <div><dt>{{selectedStat?'个人已看':'覆盖制作国家'}}</dt><dd>{{selectedStat?selectedStat.watched_count:catalogStats.covered_country_count}}</dd></div>
          <div><dt>{{selectedStat?'个人平均评分':'国家标签'}}</dt><dd>{{selectedStat?(selectedStat.average_rating?.toFixed(2)??'—'):labels.length}}</dd></div>
          <div><dt>{{selectedStat?'收藏':'当前选择'}}</dt><dd>{{selectedStat?selectedStat.favorite_count:(selected?countryName(selected):'全部')}}</dd></div>
        </dl>
        <p class="g-label">类型偏好 <small>{{selectedStat?'该国收录':'全库收录'}}</small></p>
        <div v-for="[name,n] in (selectedStat?detailGenres:overviewGenres)" :key="name" class="genre-row"><span>{{name}}</span><div class="track"><div class="fill" :style="{width:(n/(selectedStat?detailMax:overviewMax)*100)+'%'}"/></div><em>{{n}}</em></div>
        <p v-if="!(selectedStat?detailGenres:overviewGenres).length" class="g-empty">暂无类型数据</p>
        <p class="g-label">代表观影记录 <small>个人已看</small></p>
        <div v-for="m in (selectedStat?selectedStat.movies:personalTop).slice(0,3)" :key="m.movie_id" class="g-movie"><span>{{m.title}}</span><b>{{m.rating==null?'—':m.rating.toFixed(1)}} ★</b></div>
        <p v-if="!(selectedStat?selectedStat.movies:personalTop).length" class="g-empty">还没有已看记录</p>
      </aside>
    </section>

    <section v-if="tab==='seen'&&list.length" class="records" aria-label="观看记录">
      <table class="records-table">
        <thead><tr><th scope="col">影片</th><th scope="col">观看时间</th><th scope="col">评分</th><th scope="col">收藏</th><th scope="col">操作</th></tr></thead>
        <tbody>
          <tr v-for="m in visibleList" :key="m.movie_id">
            <td class="rec-film"><button type="button" class="rec-open" :aria-label="'查看'+m.title+'详情'" @click="open(m)"><img v-if="(movieArtwork[String(m.movie_id)]?.poster||m.poster)&&!failed['p'+m.movie_id]" :src="movieArtwork[String(m.movie_id)]?.poster||m.poster" :alt="m.title+'海报'" loading="lazy" @error="failed['p'+m.movie_id]=true"/><span v-else class="rec-thumb">{{m.title.slice(0,1)}}</span><span><b>{{m.title}}</b><small>{{m.release_year||'年份未知'}}<template v-if="genres(m)"> · {{genres(m)}}</template></small></span></button></td>
            <td>{{watchedAt(m)}}</td>
            <td>{{stateOf(m).rating?'★ '+stateOf(m).rating:'—'}}</td>
            <td>{{stateOf(m).favorite?'已收藏':'—'}}</td>
            <td><button type="button" class="rec-edit" :aria-label="'编辑'+m.title+'的观看记录'" @click="openEdit(m)">编辑</button></td>
          </tr>
        </tbody>
      </table>
      <div v-if="list.length>listLimit" class="my-state" style="margin:16px 0"><button type="button" @click="listLimit+=LIST_STEP">显示更多（已展示 {{visibleList.length}} / {{list.length}} 部）</button></div>
    </section>

    <section v-else-if="tab!=='globe'&&tab!=='insights'&&tab!=='seen'&&list.length" class="my-grid" aria-label="我的电影列表">
      <article v-for="m in visibleList" :key="m.movie_id" class="my-card">
        <button class="my-poster" :class="{empty:!movieArtwork[String(m.movie_id)]||failed['p'+m.movie_id]}" :aria-label="'查看'+m.title+'详情'" @click="open(m)" type="button">
          <img v-if="(movieArtwork[String(m.movie_id)]?.poster||m.poster)&&!failed['p'+m.movie_id]" :src="movieArtwork[String(m.movie_id)]?.poster||m.poster" :alt="m.title+'海报'" loading="lazy" @error="failed['p'+m.movie_id]=true"/>
          <span v-else class="fallback">{{m.title}}</span>
          <span v-if="label(m)" class="badge">{{label(m)}}</span>
        </button>
        <h3>{{m.title}}</h3>
        <p class="meta">{{m.release_year||'年份未知'}}<template v-if="genres(m)"> · {{genres(m)}}</template></p>
        <p v-if="stateOf(m).rating" class="rating">★ {{stateOf(m).rating}}</p>
      </article>
      <div v-if="list.length>listLimit" class="my-state" style="grid-column:1/-1;margin:12px 0"><button type="button" @click="listLimit+=LIST_STEP">显示更多（已展示 {{visibleList.length}} / {{list.length}} 部）</button></div>
    </section>

    <section v-else-if="tab!=='globe'&&tab!=='insights'&&!list.length" class="my-empty">
      <h2>{{tab==='seen'?'还没有已看记录':tab==='favorite'?'收藏夹是空的':'想看清单是空的'}}</h2>
      <p>在电影详情页标记后，这里会出现对应的影片。</p>
      <router-link to="/movies" class="my-link">去电影库看看</router-link>
    </section>
    </template>
  </main>

  <div v-if="editOpen" class="r41-backdrop" @click.self="closeEdit">
    <div class="r41-dialog" role="dialog" aria-modal="true" aria-labelledby="r41Title">
      <div class="r41-head"><div><h2 id="r41Title">编辑观看记录</h2></div><button type="button" class="r41-close" aria-label="关闭" @click="closeEdit">×</button></div>
      <div class="r41-body">
        <p class="r41-film">{{editMovie?.title}}<small>{{editMovie?.release_year}}</small></p>
        <p class="r41-label">记录行为 <small>收藏独立保存；已看与想看不能同时开启</small></p>
        <div class="r41-row"><label><input v-model="editSeen" type="checkbox"/>已看</label><label><input v-model="editFav" type="checkbox"/>收藏</label><label><input v-model="editWant" type="checkbox"/>想看</label></div>
        <label class="r41-label" for="r41Rating">个人评分 <small>可不评分；评分不自动代表已看</small></label>
        <select id="r41Rating" v-model="editRating" class="r41-select"><option value="">暂不评分</option><option v-for="v in ['5','4.5','4','3.5','3','2.5','2','1.5','1','0.5']" :key="v" :value="v">{{Number(v).toFixed(1)}}</option></select>
        <label class="r41-label" for="r41Note">私人备注 <small>按账户保存 · 仅自己可见 · 最多200字</small></label>
        <textarea id="r41Note" class="r41-note" rows="2" maxlength="200" v-model="editNote" :disabled="!notesReady" :placeholder="notesReady?'记录你的观影感受（仅自己可见）':'备注服务暂时不可用'"></textarea>
        <p v-if="editError" class="r41-error" role="alert">{{editError}}</p>
      </div>
      <div class="r41-foot"><button type="button" class="r41-cancel" :disabled="editSaving" @click="closeEdit">取消</button><button type="button" class="r41-save" :disabled="editSaving||editLoadingNote" @click="saveEdit">{{editSaving?'正在保存…':'保存记录调整'}}</button></div>
    </div>
  </div>
</div>
</template>
<style scoped>
:global(body:has(.my-shell)){margin:0;background:var(--mi-bg);color:var(--mi-text)}
.my-shell{--ink:var(--mi-bg);--ink-soft:var(--mi-surface);--paper:var(--mi-text);--paper-dim:var(--mi-text-soft);--muted:var(--mi-text-muted);--line:var(--mi-border);--line-strong:var(--mi-border-strong);--accent:var(--mi-accent);--serif:'Noto Serif SC','Source Han Serif SC','Songti SC',Georgia,'Times New Roman',serif;--sans:Inter,'PingFang SC','Microsoft YaHei',sans-serif;height:100dvh;overflow:hidden;background:var(--ink);color:var(--paper);font-family:var(--sans)}
.my-shell *{box-sizing:border-box}
.my-shell button,.my-shell input,.my-shell select,.my-shell textarea{font:inherit}
.my-shell button{cursor:pointer}
.my-shell a{color:inherit}
.my-shell :focus-visible{outline:2px solid var(--accent);outline-offset:3px}
.my-shell ::selection{background:var(--mi-selection-bg);color:var(--mi-selection-text)}
.sr-only{position:absolute;width:1px;height:1px;padding:0;margin:-1px;overflow:hidden;clip:rect(0 0 0 0);white-space:nowrap;border:0}
.my-head{flex:none;display:flex;align-items:flex-end;justify-content:space-between;gap:16px;margin:0 0 14px}
.my-head h1{font-family:var(--serif);font-size:30px;font-weight:500;line-height:1.1;letter-spacing:-.02em;margin:0;color:var(--paper)}
.my-crumb{flex:none;display:flex;align-items:center;gap:8px;margin:0;color:var(--muted);font-size:12px}
.my-crumb .sep{color:var(--muted);font-size:12px}
.my-crumb strong{color:var(--paper);font-size:12px;font-weight:500}
.my-page{margin-left:var(--nav-width,52.5px);width:calc(100% - var(--nav-width,52.5px));transition:margin-left .25s ease;padding:22px clamp(16px,2.4vw,36px) 18px;height:100dvh;display:flex;flex-direction:column;min-height:0}
.my-grid{flex:1;min-height:0;overflow-y:auto;align-content:start;display:grid;grid-template-columns:repeat(6,minmax(0,1fr));gap:24px 16px;padding:2px 4px 14px 0;scrollbar-width:thin;scrollbar-color:var(--mi-scroll-thumb) transparent}
.my-card{min-width:0}
.my-poster{position:relative;display:block;width:100%;aspect-ratio:2/3;padding:0;border:1px solid var(--line);border-radius:14px;overflow:hidden;background:var(--ink-soft);cursor:pointer;transition:transform .22s,border-color .22s,box-shadow .22s;box-shadow:var(--mi-shadow-sm)}
.my-poster:hover,.my-poster:focus-visible{transform:translateY(-4px);border-color:var(--mi-accent);outline:0;box-shadow:var(--mi-shadow-md)}
.my-poster img{width:100%;height:100%;object-fit:cover;display:block}
.my-poster .fallback{position:absolute;inset:0;display:flex;align-items:center;justify-content:center;padding:14px;text-align:center;font-family:var(--serif);font-weight:500;font-size:17px;line-height:1.25;color:var(--paper);background:var(--ink-soft)}
.badge{position:absolute;top:9px;left:9px;padding:4px 8px;border-radius:999px;background:rgba(12,13,15,.78);border:1px solid rgba(255,255,255,.3);backdrop-filter:blur(3px);font-size:9px;line-height:1.2;color:#fff}
.my-card h3{margin:10px 0 4px;font-size:13px;font-weight:500;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;color:var(--paper)}
.my-card .meta{margin:0;color:var(--muted);font-size:11px;line-height:1.5;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.my-card .rating{margin:6px 0 0;color:var(--accent);font-size:11px;font-variant-numeric:tabular-nums}
.my-state{flex:none;margin:60px 0;color:var(--muted);font-size:13px;text-align:center;line-height:1.7}
.my-state strong{display:block;color:var(--paper);margin-bottom:8px;font-family:var(--serif);font-size:24px;font-weight:500}
.my-state button{margin-top:14px;background:transparent;border:1px solid var(--line-strong);color:var(--paper);border-radius:7px;padding:9px 14px;cursor:pointer;font-size:12px}
.my-empty{margin:auto;max-width:520px;text-align:center;padding:20px 0}
.my-empty h2{margin:0 0 10px;font-family:var(--serif);font-size:30px;font-weight:500}
.my-empty p{margin:0 0 20px;color:var(--muted);font-size:13px;line-height:1.8}
.my-link{display:inline-block;padding:10px 16px;border-radius:7px;border:1px solid var(--line-strong);background:transparent;color:var(--paper);font-size:12px;text-decoration:none;cursor:pointer}
.my-link:hover{border-color:var(--paper)}
.globe-workspace{flex:1;min-height:0;display:grid;grid-template-columns:minmax(220px,260px) minmax(0,1fr) minmax(240px,280px);gap:12px;align-items:stretch}
.g-rank,.g-detail{border:1px solid var(--line);border-radius:14px;background:var(--ink-soft);padding:16px 14px;overflow:auto;min-width:0}
.g-rank{position:relative}
.g-rank h2,.g-detail h2{margin:0 0 12px;font-family:var(--serif);font-size:20px;font-weight:500;letter-spacing:-.01em;color:var(--paper)}

.g-search{width:100%;height:38px;border-radius:7px;border:1px solid var(--line);background:var(--ink);color:var(--paper);padding:0 10px;font-size:12px;outline:0;margin-bottom:10px}
.g-search:focus{border-color:var(--line-strong)}
.g-search::placeholder{color:var(--mi-text-subtle)}
.g-list{display:flex;flex-direction:column;gap:2px}
.g-row{display:flex;align-items:center;gap:6px;border-radius:7px;padding-right:6px;transition:background-color .15s}
.g-row:hover,.g-row.on{background:var(--mi-chip-hover)}
.g-pick{flex:1;min-width:0;display:flex;align-items:center;gap:9px;padding:7px 4px;border:0;background:transparent;color:var(--paper-dim);text-align:left;cursor:pointer}
.g-num{color:var(--muted);font-size:10px;font-variant-numeric:tabular-nums}
.g-name{min-width:0;display:flex;flex-direction:column;font-size:12px;color:var(--paper);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.g-name small{color:var(--muted);font-size:9px;margin-top:1px}
.country-code-flag{flex:none;width:30px;height:20px;display:grid;place-items:center;border:1px solid var(--line);border-radius:2px;color:var(--muted);font-size:9px;letter-spacing:.04em}
.g-val{margin-left:auto;color:var(--muted);font-size:10.5px;white-space:nowrap;font-variant-numeric:tabular-nums}
.g-row input[type=checkbox]{accent-color:var(--accent);width:14px;height:14px}
.country-flag{display:block;object-fit:cover;border-radius:2px}
.g-empty{color:var(--muted);font-size:12px;line-height:1.7;padding:12px 4px}
.g-stage{position:relative;border:1px solid var(--line);border-radius:14px;overflow:hidden;background:var(--mi-surface-raised);min-height:460px;isolation:isolate}
.g-stage:before{content:'';position:absolute;inset:0;z-index:0;display:var(--mi-stage-sky-display);background:url('/m9/globe/assets/mi2-sky-g2b.jpg') center/cover no-repeat;opacity:.16;filter:saturate(.45) brightness(.7);pointer-events:none}
.g-stage:after{content:'';position:absolute;inset:0;z-index:0;background:var(--mi-stage-sky-after);pointer-events:none}
.g-stage>.globe-viewport,.g-stage>.globe-stage{position:absolute;inset:0;z-index:1}
.g-sky-b{position:absolute;inset:-10%;z-index:0;display:var(--mi-stage-sky-display);background:url('/m9/globe/assets/mi2-sky-g2b.jpg') center/cover no-repeat;opacity:.08;pointer-events:none}
.g-stage .globe-stage,.g-stage .globe-viewport{min-height:100%}
.g-actions{position:absolute;top:12px;right:12px;display:flex;gap:7px;z-index:5}
.g-actions button{height:32px;padding:0 11px;border-radius:7px;border:1px solid var(--line-strong);background:var(--mi-surface);backdrop-filter:blur(3px);color:var(--paper);font-size:11px;cursor:pointer;box-shadow:var(--mi-shadow-sm)}
.g-actions button:hover{background:var(--mi-surface-hover)}
.g-actions button:disabled{opacity:.4;cursor:default}
.g-empty-stage{position:absolute;inset:0;display:grid;place-content:center;text-align:center;z-index:4;color:var(--muted);font-size:12px;background:var(--mi-bg)}
.g-detail h2{margin-bottom:14px}
.g-country{display:flex;align-items:center;gap:10px;margin-bottom:14px}
.g-country small{display:block;color:var(--muted);font-size:10px;margin-top:2px}
.g-country strong{font-size:14px;font-weight:500}
.g-metrics{display:grid;grid-template-columns:1fr 1fr;gap:8px;margin:0 0 16px}
.g-metrics div{border:1px solid var(--line);border-radius:8px;padding:8px 10px;min-width:0}
.g-metrics dt{color:var(--muted);font-size:10px;margin-bottom:4px;line-height:1.4}
.g-metrics dd{margin:0;font-size:16px;font-weight:500;font-variant-numeric:tabular-nums;overflow-wrap:anywhere}
.g-label{margin:0 0 8px;color:var(--muted);font-size:10.5px;display:flex;align-items:center;justify-content:space-between;gap:8px}
.g-label small{color:var(--muted);font-size:10px;font-weight:400}
.g-movie{display:flex;justify-content:space-between;gap:10px;padding:7px 0;border-top:1px solid var(--line);font-size:11px;color:var(--paper-dim)}
.g-movie b{color:var(--accent);font-weight:500;white-space:nowrap;font-variant-numeric:tabular-nums}
.insight-workspace{flex:1;min-height:0;overflow-y:auto;display:flex;flex-direction:column;gap:12px}
.insight-cards{display:grid;grid-template-columns:1.15fr 1fr 1fr 1fr;gap:10px}
.insight-cards article{border:1px solid var(--line);border-radius:12px;background:var(--ink-soft);padding:16px 18px;min-width:0}
.insight-cards small{display:block;color:var(--muted);font-size:11px;margin-bottom:8px}
.insight-cards strong{display:block;font-size:30px;font-weight:500;letter-spacing:-.03em;line-height:1.1;font-variant-numeric:tabular-nums;color:var(--paper)}
.insight-cards span{display:block;margin-top:8px;color:var(--muted);font-size:10.5px;line-height:1.5}
.insight-columns{flex:1;min-height:0;display:grid;grid-template-columns:1fr 1fr;gap:12px}
.insight-panel{border:1px solid var(--line);border-radius:12px;background:var(--ink-soft);padding:16px 18px;overflow:auto;min-width:0}
.insight-panel h2{margin:0 0 14px;font-family:var(--serif);font-size:21px;font-weight:500;letter-spacing:-.01em}
.genre-row{display:grid;grid-template-columns:62px 1fr 28px;align-items:center;gap:10px;margin-bottom:10px;font-size:11.5px;color:var(--paper-dim)}
.genre-row .track{height:6px;border-radius:99px;background:var(--mi-bar-track);overflow:hidden}
.genre-row .fill{height:100%;border-radius:99px;background:var(--accent);opacity:.78}
.genre-row em{font-style:normal;color:var(--muted);text-align:right;font-size:10.5px;font-variant-numeric:tabular-nums}
.country-row{display:flex;align-items:center;gap:9px;padding:8px 0;border-top:1px solid var(--line);font-size:11.5px;color:var(--paper-dim)}
.country-row:first-of-type{border-top:0}
.country-row b{margin-left:auto;color:var(--paper-dim);font-weight:500;font-variant-numeric:tabular-nums}
.insight-note{margin:16px 0 0;padding-top:12px;border-top:1px solid var(--line);color:var(--muted);font-size:10.5px;line-height:1.7}
.g-searchrow{display:flex;gap:8px;margin-bottom:10px}
.g-searchrow .g-search{margin-bottom:0;flex:1;min-width:0}
.g-filter-btn{flex:none;height:38px;padding:0 11px;border-radius:7px;border:1px solid var(--line-strong);background:transparent;color:var(--paper);font-size:11px;cursor:pointer;display:flex;align-items:center;gap:6px}
.g-filter-count{background:var(--accent);color:var(--mi-accent-on);border-radius:999px;padding:1px 6px;font-size:10px}
.g-tools{display:flex;align-items:center;justify-content:space-between;gap:8px;padding:6px 0 8px;border-bottom:1px solid var(--line);margin-bottom:8px;color:var(--muted);font-size:10.5px}
.g-tools button{border:1px solid var(--line);background:transparent;color:var(--paper-dim);border-radius:6px;padding:5px 8px;font-size:10.5px;cursor:pointer}
.g-tools button:hover{border-color:var(--line-strong);color:var(--paper)}
.g-caption{display:flex;justify-content:space-between;margin:0 0 8px;color:var(--muted);font-size:10px}
.g-filter{position:absolute;z-index:20;top:84px;left:0;right:0;border:1px solid var(--line-strong);border-radius:10px;background:var(--ink-soft);box-shadow:var(--mi-shadow-lg);padding:12px}
.g-filter-head{display:flex;align-items:center;justify-content:space-between;margin-bottom:10px}
.g-filter-head strong{font-size:12px;font-weight:500}
.g-filter-head button{border:0;background:transparent;color:var(--muted);font-size:18px;cursor:pointer}
.g-field{display:flex;flex-direction:column;gap:6px;font-size:10.5px;color:var(--muted);margin-bottom:10px}
.g-field select{height:32px;border-radius:6px;border:1px solid var(--line);background:var(--ink);color:var(--paper);font-size:11px;padding:0 8px}
.g-check{display:flex;align-items:center;justify-content:space-between;gap:10px;margin-bottom:10px;font-size:11px;color:var(--paper-dim)}
.g-check span{display:flex;flex-direction:column;gap:2px}
.g-check small{color:var(--muted);font-size:9.5px}
.g-filter-foot{display:flex;justify-content:flex-end;gap:8px;padding-top:6px}
.g-filter-foot button{border:1px solid var(--line);background:transparent;color:var(--paper-dim);border-radius:6px;padding:6px 11px;font-size:11px;cursor:pointer}
.g-filter-foot .g-done{border-color:var(--paper);color:var(--paper)}
.g-country-icon{width:42px;height:42px;flex:none;display:grid;place-items:center;border-radius:8px;background:var(--mi-surface-raised);border:1px solid var(--line);color:var(--paper-dim)}
.g-country-icon svg{width:24px;height:24px;fill:none;stroke:currentColor;stroke-width:1.6}
.g-country strong{font-size:14px}
.g-country small{font-size:10px}
.records{flex:1;min-height:0;overflow-y:auto;border:1px solid var(--line);border-radius:12px;background:var(--ink-soft)}
.records-table{width:100%;border-collapse:collapse;font-size:12px}
.records-table th{position:sticky;top:0;background:var(--mi-surface-raised);text-align:left;color:var(--muted);font-weight:500;font-size:10.5px;letter-spacing:.02em;padding:11px 14px;border-bottom:1px solid var(--line)}
.records-table td{padding:9px 14px;border-bottom:1px solid var(--line);color:var(--paper-dim);vertical-align:middle}
.records-table tr:last-child td{border-bottom:0}
.records-table tr:hover td{background:var(--mi-chip-hover)}
.rec-film{width:46%}
.rec-open{display:flex;align-items:center;gap:11px;width:100%;padding:0;border:0;background:none;color:inherit;text-align:left;cursor:pointer}
.rec-open img,.rec-thumb{width:34px;height:48px;flex:none;border-radius:4px;object-fit:cover;border:1px solid var(--line);background:var(--ink);display:grid;place-items:center;color:var(--paper-dim);font-family:var(--serif);font-weight:500}
.rec-open b{display:block;font-size:12px;font-weight:500;color:var(--paper)}
.rec-open small{display:block;margin-top:3px;color:var(--muted);font-size:10.5px}
.rec-tag{display:inline-block;padding:3px 8px;border-radius:999px;background:transparent;border:1px solid var(--line);color:var(--paper-dim);font-size:10px}
.rec-edit{border:1px solid var(--line);background:transparent;color:var(--paper);border-radius:6px;padding:6px 11px;font-size:11px;cursor:pointer}
.rec-edit:hover{border-color:var(--line-strong)}
.r41-backdrop{position:fixed;inset:0;z-index:200;display:grid;place-items:center;padding:20px;background:var(--mi-backdrop);backdrop-filter:blur(3px)}
.r41-dialog{width:min(520px,100%);max-height:88vh;overflow:auto;border:1px solid var(--line-strong);border-radius:14px;background:var(--ink-soft);box-shadow:var(--mi-shadow-lg);color:var(--paper)}
.r41-head{display:flex;align-items:flex-start;justify-content:space-between;gap:16px;padding:18px 20px 14px;border-bottom:1px solid var(--line)}
.r41-head h2{margin:0;font-family:var(--serif);font-size:24px;font-weight:500}
.r41-close{border:0;background:transparent;color:var(--muted);font-size:22px;line-height:1;cursor:pointer}
.r41-body{padding:16px 20px 6px}
.r41-film{margin:0 0 14px;font-size:14px;font-weight:500}
.r41-film small{margin-left:8px;color:var(--muted);font-size:11px;font-weight:400}
.r41-label{display:flex;align-items:center;gap:8px;margin:14px 0 8px;color:var(--muted);font-size:11px}
.r41-label small{color:var(--muted);font-size:10px}
.r41-row{display:flex;gap:20px;flex-wrap:wrap}
.r41-row label{display:flex;align-items:center;gap:7px;font-size:12px;color:var(--paper-dim);cursor:pointer}
.r41-row input{accent-color:var(--accent);width:15px;height:15px}
.r41-select,.r41-note{width:100%;border-radius:7px;border:1px solid var(--line);background:var(--ink);color:var(--paper);font-size:12px;padding:9px 10px;outline:0}
.r41-select:focus,.r41-note:focus{border-color:var(--line-strong)}
.r41-note{resize:vertical;min-height:46px;opacity:.8}
.r41-error{margin:10px 0 0;color:var(--mi-negative);font-size:11px}
.r41-foot{display:flex;justify-content:flex-end;gap:10px;padding:14px 20px 18px;border-top:1px solid var(--line)}
.r41-cancel,.r41-save{border-radius:7px;padding:9px 16px;font-size:12px;cursor:pointer}
.r41-cancel{border:1px solid var(--line);background:transparent;color:var(--paper-dim)}
.r41-save{border:1px solid var(--paper);background:transparent;color:var(--paper)}
.r41-save:disabled,.r41-cancel:disabled{opacity:.45;cursor:default}
.trend-panel{margin-top:0}
.trend-panel h2 small{margin-left:8px;color:var(--muted);font-size:10px;font-weight:400}
.trend-chart{display:flex;align-items:flex-end;gap:6px;height:132px;padding:8px 4px 0}
.trend-col{flex:1;display:flex;flex-direction:column;align-items:center;justify-content:flex-end;gap:5px;min-width:0}
.trend-value{font-size:10px;color:var(--muted);height:14px;font-variant-numeric:tabular-nums}
.trend-bar{width:100%;max-width:24px;border-radius:3px 3px 0 0;background:var(--accent);opacity:.72;border:0}
.trend-col small{color:var(--muted);font-size:9px;white-space:nowrap}
@media(max-width:1200px){.my-grid{grid-template-columns:repeat(4,minmax(0,1fr))}.globe-workspace{grid-template-columns:230px minmax(0,1fr)}.g-detail{grid-column:1/-1;max-height:250px}.g-stage{min-height:380px}.insight-cards{grid-template-columns:1fr 1fr}}
@media(max-width:900px){.globe-workspace{grid-template-columns:1fr;min-height:0;overflow-y:auto}.g-stage{min-height:380px}.g-rank,.g-detail{max-height:none}.insight-columns{grid-template-columns:1fr}.g-detail{grid-column:auto}}
@media(max-width:820px){.my-shell{height:auto;overflow:visible}.my-page{height:auto;min-height:100dvh;padding:20px 16px 40px}.my-grid{grid-template-columns:repeat(3,minmax(0,1fr));gap:18px 12px}.globe-workspace{grid-template-columns:1fr;min-height:0}.g-stage{min-height:340px}.insight-cards{grid-template-columns:1fr 1fr}.insight-columns{grid-template-columns:1fr}}
@media(max-width:600px){.my-page{padding:18px 14px 36px}.my-head{margin-bottom:12px}.my-head h1{font-size:24px}.my-grid{grid-template-columns:repeat(2,minmax(0,1fr));gap:20px 12px}.my-card h3{font-size:12px}.g-rank,.g-detail{padding:14px 12px;border-radius:12px}.g-stage{min-height:300px;border-radius:12px}.g-actions{top:8px;right:8px;gap:5px}.g-actions button{height:29px;padding:0 8px;font-size:10px}.insight-cards{grid-template-columns:1fr 1fr;gap:8px}.insight-cards article{padding:13px 12px;border-radius:10px}.insight-cards strong{font-size:26px}.insight-panel{padding:14px 12px;border-radius:10px}.insight-panel h2{font-size:20px}.records-table{font-size:11px}.records-table th:nth-child(2),.records-table td:nth-child(2){display:none}.records-table th,.records-table td{padding:8px 7px}.r41-dialog{max-height:92vh}}
@media(prefers-reduced-motion:reduce){.my-poster,.my-page{transition:none}.my-poster:hover,.my-poster:focus-visible{transform:none;box-shadow:0 5px 17px #0003}}
</style>
