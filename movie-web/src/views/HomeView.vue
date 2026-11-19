<script setup lang="ts">
import {ref,onMounted,computed,onBeforeUnmount,nextTick,watch} from 'vue'
import {useRouter} from 'vue-router'
import {useAuthStore} from '../stores/auth'
import M9Sidebar from '../components/M9Sidebar.vue'
import {movieArtwork as art} from '../services/movieArtwork'
import {createFeedbackBoard,vFeedback} from '../services/recommendationFeedback'
const RAIL=13
const router=useRouter(),auth=useAuthStore(),behavior=ref<Record<string,any>>({}),featuredId=ref(0),loading=ref(true),error=ref(''),logoutError=ref(''),failed=ref<Record<string,boolean>>({}),posterLoaded=ref<Record<string,boolean>>({}),heroLoaded=ref(false),sidebarExpanded=ref(localStorage.getItem('mi2_nav_expanded')==='true')
const dailyItems=ref<any[]>([]),popularItems=ref<any[]>([]),homeBoards:Record<string,ReturnType<typeof createFeedbackBoard>>={}
const channels=computed(()=>[{id:'personal',title:'每日为你推荐',note:'同日稳定 · 根据你的收藏、想看与评分',items:dailyItems.value},{id:'popular',title:'TMDB 热门',note:'官方榜单 · 含库外影片',items:popularItems.value}])
const localMovies=computed(()=>{const seen=new Set<number>(),out:any[]=[];for(const movie of [...popularItems.value,...dailyItems.value]){const id=localId(movie);if(id!==null&&!seen.has(id)){seen.add(id);out.push(movie)}}return out})
const film=computed(()=>localMovies.value.find(m=>m.movie_id===featuredId.value)||localMovies.value[0]||null)
const railHasItems=computed(()=>channels.value.some(c=>c.items.length>0))
watch(()=>channels.value.map(c=>c.id+':'+c.items.length).join('|'),()=>nextTick(syncAllArrows))
const posterFailures=ref<Record<string,boolean>>({})
function poster(m:any){
  const assets=(m.assets||[]).filter((a:any)=>a.asset_type==='poster').map((a:any)=>{
    const p=a.source_path
    return typeof p==='string'&&p.startsWith('/')&&!p.startsWith('//')?'https://image.tmdb.org/t/p/w500'+p:p
  })
  const id=localId(m)
  const candidates=[m.poster,m.poster_fallback,...assets,id!==null?art[String(id)]?.poster:undefined]
    .filter((p:any):p is string=>typeof p==='string'&&(p.startsWith('/')||/^https?:\/\//.test(p))&&!p.startsWith('//'))
    .map(p=>/^https?:\/\//.test(p)?'/api/media/poster?u='+encodeURIComponent(p):p)
  return [...new Set(candidates)].find(p=>!posterFailures.value[cardKey(m)+':'+p])
}
function posterError(movie:any,event:Event){
  const src=(event.target as HTMLImageElement).getAttribute('src')
  if(src)posterFailures.value[cardKey(movie)+':'+src]=true
  posterLoaded.value[cardKey(movie)]=false
}
function backdrop(m:any){const a=(m.assets||[]).find((x:any)=>x.asset_type==='backdrop'&&x.is_primary)||(m.assets||[]).find((x:any)=>x.asset_type==='backdrop');const p=a?.source_path;if(p?.startsWith('/'))return '/api/media/poster?u='+encodeURIComponent('https://image.tmdb.org/t/p/w1280'+p);return p?.startsWith('http')?'/api/media/poster?u='+encodeURIComponent(p):(localId(m)!==null?art[String(localId(m))]?.backdrop:undefined)||poster(m)}

const counts=computed(()=>{const c={seen:0,want:0,fav:0};for(const s of Object.values(behavior.value||{}) as any[]){if(s?.watched)c.seen++;if(s?.want)c.want++;if(s?.favorite)c.fav++}return c})
function mark(movie:any){const id=localId(movie);if(id===null)return '';const s=behavior.value[String(id)];if(!s)return '';if(s.watched)return '已看';if(s.want)return '想看';if(s.favorite)return '已收藏';return ''}
function genres(movie:any){return (movie?.genres||[]).map((g:any)=>typeof g==='string'?g:g.name_zh).join(' / ')}
function finite(value:unknown){
  if(typeof value==='number')return Number.isFinite(value)?value:null
  if(typeof value==='string'&&/^\d+(?:\.\d+)?$/.test(value.trim())){const n=Number(value);return Number.isFinite(n)?n:null}
  return null
}
function localId(movie:any):number|null{
  const id=movie?.movie_id
  return Number.isSafeInteger(id)&&id>0&&movie?.in_catalog!==false?id:null
}
function tmdbId(movie:any):number|null{
  const raw=movie?.tmdb_id
  let id:number
  if(typeof raw==='number')id=raw
  else if(typeof raw==='string'&&/^\d{1,10}$/.test(raw))id=Number(raw)
  else return null
  return Number.isSafeInteger(id)&&id>0&&id<=9999999999?id:null
}
function cardKey(movie:any):string{
  const local=localId(movie)
  if(local!==null)return 'local:'+local
  const tmdb=tmdbId(movie)
  return tmdb!==null?'tmdb:'+tmdb:''
}
function isExternal(movie:any){return localId(movie)===null&&tmdbId(movie)!==null}
function externalUrl(movie:any){
  const id=tmdbId(movie)
  return id===null?'':`https://www.themoviedb.org/movie/${id}`
}
function normalizeItems(items:any):any[]{
  const out:any[]=[],seen=new Set<string>()
  for(const raw of Array.isArray(items)?items:[]){
    if(!raw||typeof raw!=='object')continue
    const movie={...raw},key=cardKey(movie)
    if(!key||seen.has(key))continue
    seen.add(key);out.push(movie)
    if(out.length>=RAIL)break
  }
  return out
}
function tmdbRating(movie:any){
  const direct=finite(movie?.tmdb_rating)
  if(direct!==null&&direct>0&&direct<=10)return direct.toFixed(1)
  const ratings=Array.isArray(movie?.ratings)?movie.ratings:[]
  for(const rating of ratings){
    if(rating?.source!=='tmdb'||finite(rating?.score_scale)!==10)continue
    const votes=finite(rating?.vote_count)
    if(votes===null||votes<=0||!Number.isInteger(votes))continue
    const raw=rating?.score_value
    if(typeof raw==='string'&&!/^\d+(?:\.\d+)?$/.test(raw.trim()))continue
    const score=finite(raw)
    if(score!==null&&score>0&&score<=10)return score.toFixed(1)
  }
  return ''
}
function runtimeLabel(movie:any){
  const minutes=finite(movie?.runtime_minutes)
  return minutes!==null&&Number.isInteger(minutes)&&minutes>=1&&minutes<=1440?`${minutes} 分钟`:''
}
const HOME_REQUEST_TIMEOUT_MS=8000
class HomeRequestError extends Error {
  status?:number
  timedOut:boolean
  constructor(message:string,options:{status?:number;timedOut?:boolean}={}){super(message);this.name='HomeRequestError';this.status=options.status;this.timedOut=options.timedOut===true}
}
let loadController:AbortController|undefined
let loadSequence=0
const lifeController=new AbortController()
let raf=0
let unmounted=false
const railErrors=ref<Record<string,string>>({})
const hasRailError=computed(()=>Object.keys(railErrors.value).length>0)
const railRows:Record<string,HTMLElement|null>={}
const arrowState=ref<Record<string,{left:boolean;right:boolean}>>({})
let railFrame=0
function syncArrows(id:string){
  const row=railRows[id]
  if(!row){
    if(id in arrowState.value){const next={...arrowState.value};delete next[id];arrowState.value=next}
    return
  }
  const remaining=row.scrollWidth-row.clientWidth
  const next={left:row.scrollLeft>1,right:remaining>1&&row.scrollLeft<remaining-1}
  const current=arrowState.value[id]
  if(current&&current.left===next.left&&current.right===next.right)return
  arrowState.value={...arrowState.value,[id]:next}
}
function syncAllArrows(){for(const id of Object.keys(railRows))syncArrows(id)}
function scheduleArrows(){if(railFrame)cancelAnimationFrame(railFrame);railFrame=requestAnimationFrame(()=>{railFrame=0;syncAllArrows()})}
const railRefs=new Map<string,(el:any)=>void>()
function railRef(id:string){
  let callback=railRefs.get(id)
  if(!callback){callback=(el:any)=>setRailRow(id,el);railRefs.set(id,callback)}
  return callback
}
function setRailRow(id:string,el:any){
  railRows[id]=typeof HTMLElement!=='undefined'&&el instanceof HTMLElement?el:null
  if(el)nextTick(()=>syncArrows(id))
}
function prefersReducedMotion(){return typeof window!=='undefined'&&window.matchMedia?.('(prefers-reduced-motion: reduce)').matches===true}
function scrollRail(id:string,direction:number){
  const row=railRows[id]
  if(!row)return
  const card=row.querySelector<HTMLElement>('.filmcard')
  const styles=getComputedStyle(row)
  const gap=Number.parseFloat(styles.columnGap||styles.gap||'20')||20
  const context=card?.getBoundingClientRect().width||160
  const distance=Math.max(80,row.clientWidth-context-gap)
  row.scrollBy({left:direction*distance,behavior:prefersReducedMotion()?'auto':'smooth'})
  scheduleArrows()
}
function isUnauthorized(error:unknown){return error instanceof HomeRequestError&&error.status===401}
function clearBoards(){for(const b of Object.values(homeBoards))b.dispose();for(const key of Object.keys(homeBoards))delete homeBoards[key]}
async function api(path:string,signal:AbortSignal=lifeController.signal):Promise<any>{
  if(signal.aborted)throw new HomeRequestError('请求已取消。')
  const request=new AbortController()
  let timedOut=false
  const timer=setTimeout(()=>{timedOut=true;request.abort()},HOME_REQUEST_TIMEOUT_MS)
  const onAbort=()=>request.abort()
  signal.addEventListener('abort',onAbort,{once:true})
  try{
    const response=await fetch('/api/catalog/'+path,{credentials:'same-origin',signal:request.signal})
    if(!response.ok)throw new HomeRequestError(response.status===401?'登录已失效，请重新登录。':'电影目录暂时不可用，请重试。',{status:response.status})
    return (await response.json()).data
  }catch(error){
    if(timedOut)throw new HomeRequestError('请求超时，请重新加载。',{timedOut:true})
    if(error instanceof HomeRequestError)throw error
    if(signal.aborted)throw new HomeRequestError('请求已取消。')
    throw new HomeRequestError('电影目录暂时不可用，请重试。')
  }finally{
    clearTimeout(timer)
    signal.removeEventListener('abort',onAbort)
  }
}
function railFailure(id:string,error:unknown,otherOk:boolean){
  const label=id==='personal'?'每日推荐':'TMDB 热门'
  let message:string
  if(isUnauthorized(error))message='登录已失效，请重新登录。'
  else if(error instanceof HomeRequestError&&error.timedOut)message=`${label}读取超时，已停止等待。`
  else message=`${label}暂时无法读取，请稍后重试。`
  return otherOk?`${message} 已保留另一条推荐，可重新加载。`:message
}
async function load(){
  const sequence=++loadSequence
  loadController?.abort()
  const current=new AbortController()
  loadController=current
  clearBoards()
  error.value=''
  railErrors.value={}
  failed.value={}
  posterFailures.value={}
  posterLoaded.value={}
  heroLoaded.value=false
  loading.value=true
  try{
    const [dailyResult,popularResult]=await Promise.allSettled([
      api('recommendations?channel=daily&limit='+RAIL,current.signal),
      api('recommendations?channel=popular&scope=all&limit='+RAIL,current.signal),
    ])
    if(sequence!==loadSequence||current.signal.aborted)return
    const daily=dailyResult.status==='fulfilled'?dailyResult.value:null
    const popular=popularResult.status==='fulfilled'?popularResult.value:null
    const dailyError=dailyResult.status==='rejected'?dailyResult.reason:null
    const popularError=popularResult.status==='rejected'?popularResult.reason:null
    if(isUnauthorized(dailyError)||isUnauthorized(popularError)){error.value='登录已失效，请重新登录。';return}
    if(!daily&&!popular){error.value='电影目录暂时不可用，请重试。';return}
    dailyItems.value=normalizeItems(daily?.items)
    popularItems.value=normalizeItems(popular?.items)
    const saved=String(sessionStorage.getItem('mi2_home_selected')||'')
    featuredId.value=localMovies.value.find(m=>String(m.movie_id)===saved)?.movie_id||localMovies.value.find(m=>art[String(m.movie_id)]?.backdrop)?.movie_id||localMovies.value[0]?.movie_id||0
    const nextErrors:Record<string,string>={}
    if(dailyError)nextErrors.personal=railFailure('personal',dailyError,!!popular)
    if(popularError)nextErrors.popular=railFailure('popular',popularError,!!daily)
    railErrors.value=nextErrors
    for(const c of channels.value)if(!nextErrors[c.id])homeBoards[c.id]=createFeedbackBoard({channel:c.id==='personal'?'daily':c.id,context:'home',items:c.items})
    loading.value=false
    const wanted=localMovies.value.map(m=>m.movie_id)
    const [details,state]=await Promise.all([Promise.all(wanted.map(id=>api('movies/'+id,current.signal).catch(()=>null))),api('behavior',current.signal).catch(()=>null)])
    if(sequence!==loadSequence||current.signal.aborted)return
    if(state)behavior.value=state.state||{}
    const byId=new Map<number,any>();for(const d of details)if(d)byId.set(d.movie_id,d)
    const mergeDetails=(items:any[])=>items.map(m=>{const id=localId(m);return id!==null&&byId.has(id)?{...m,...byId.get(id)}:m})
    dailyItems.value=mergeDetails(dailyItems.value)
    popularItems.value=mergeDetails(popularItems.value)
  }catch(e){
    if(sequence!==loadSequence||current.signal.aborted)return
    error.value=isUnauthorized(e)?'登录已失效，请重新登录。':'电影目录暂时不可用，请重试。'
  }finally{
    if(sequence===loadSequence){loading.value=false;if(loadController===current)loadController=undefined}
  }
}
function clickCard(movie:any){
  const channel=channels.value.find(c=>c.items.includes(movie))
  if(channel)homeBoards[channel.id]?.click(channel.items.indexOf(movie)+1)
}
function navigateLocal(id:number){
  sessionStorage.setItem('mi2_home_selected',String(id))
  sessionStorage.setItem('mi2_home_position',String(window.scrollY))
  router.push('/movies?from=home#movie/'+id)
}
function openCard(movie:any){
  clickCard(movie)
  const id=localId(movie)
  if(id!==null)navigateLocal(id)
}
function detail(id:number){
  const movie=channels.value.flatMap(c=>c.items).find(m=>localId(m)===id)
  if(movie)clickCard(movie)
  navigateLocal(id)
}
function openAI(){window.dispatchEvent(new Event('mi2-open-assistant'))}
async function logout(){try{await auth.logout();router.replace('/login')}catch{logoutError.value='退出未确认，请重试。'}}
const refreshBehavior=()=>api('behavior',lifeController.signal).then(b=>behavior.value=b.state||{}).catch(()=>{})
onMounted(async()=>{window.addEventListener('mi2-behavior-changed',refreshBehavior);window.addEventListener('resize',scheduleArrows);await load();nextTick(syncAllArrows);if(!unmounted)raf=requestAnimationFrame(()=>window.scrollTo(0,Number(sessionStorage.getItem('mi2_home_position')||0)))})
onBeforeUnmount(()=>{unmounted=true;loadSequence++;loadController?.abort();lifeController.abort();if(raf)cancelAnimationFrame(raf);if(railFrame)cancelAnimationFrame(railFrame);for(const id of Object.keys(railRows))delete railRows[id];railRefs.clear();window.removeEventListener('mi2-behavior-changed',refreshBehavior);window.removeEventListener('resize',scheduleArrows);clearBoards()})
</script>
<template>
  <div class="cinema-source" :style="{'--navigation-width':sidebarExpanded?'280px':'52.5px'}">
    <M9Sidebar @expanded-change="sidebarExpanded=$event"/>
    <main class="source-content">
      <div v-if="loading" class="source-state source-loading" role="status">
        <h1>正在选片</h1>
        <p>从本地电影目录整理本期片单。</p>
      </div>
      <div v-else-if="error" class="source-state" role="alert">
        <h1>暂时无法打开影院</h1>
        <p>{{error}}</p>
        <div class="state-actions">
          <button class="action primary" @click="load">重新加载</button>
          <router-link class="action secondary" to="/login">重新登录</router-link>
        </div>
      </div>
      <div v-else-if="film || railHasItems" class="site" data-version="A">
        <section v-if="film" class="hero" :class="{loaded:heroLoaded}">
          <div class="image-fallback">背景剧照暂不可用</div>
          <img v-if="backdrop(film)&&!failed['hero'+cardKey(film)]" class="backdrop" :src="backdrop(film)" :alt="film.title+'电影背景剧照'" @load="heroLoaded=true" @error="failed['hero'+cardKey(film)]=true"/>
          <div class="hero-shade"/>
          <div class="hero-copy">
            <h1>{{film.title}}</h1>
            <div v-if="film.original_title" class="title-en">{{film.original_title}}</div>
            <div class="hero-meta">
              <span v-if="film.release_year">{{film.release_year}}</span>
              <span v-if="runtimeLabel(film)">{{runtimeLabel(film)}}</span>
              <span v-if="tmdbRating(film)">TMDB {{tmdbRating(film)}}</span>
              <span v-if="genres(film)">{{genres(film)}}</span>
              <span v-else>类型暂未提供</span>
            </div>
            <p class="synopsis">{{film.overview||'暂无影片简介。'}}</p>
            <div class="hero-actions">
              <button class="action primary" @click="openAI">
                <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 3l2.5 6.5L21 12l-6.5 2.5L12 21l-2.5-6.5L3 12l6.5-2.5z"/></svg>AI 探索电影
              </button>
              <button class="action secondary" @click="detail(film.movie_id)">
                <svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="12" r="9"/><path d="M12 11v6M12 7v1"/></svg>了解影片
              </button>
            </div>
          </div>
          <figure class="hero-poster" :class="{loaded:posterLoaded[cardKey(film)]}">
            <img v-if="poster(film)" :key="poster(film)" :src="poster(film)" :alt="film.title+'电影海报'" @load="posterLoaded[cardKey(film)]=true" @error="posterError(film,$event)"/>
            <div v-else class="hero-poster-fallback"><span>电影海报</span><strong>{{film.title}}</strong><span>海报暂未显示</span></div>
            <figcaption>电影海报 · {{film.title}}</figcaption>
          </figure>
          <div class="hero-index">MovieInsight · 选片</div>
        </section>

        <section v-for="channel in channels" :key="channel.id" class="library" :data-channel="channel.id">
          <div class="rail-head">
            <div class="rail-heading"><span class="section-rule"/><h2>{{channel.title}}</h2></div>
            <div class="rail-meta">
              <small>{{channel.note}}</small>
              <router-link :to="{path:'/recommendations',query:{channel:channel.id==='personal'?'daily':channel.id}}">全部推荐</router-link>
            </div>
          </div>
          <div v-if="!railErrors[channel.id]" class="filmrow-shell">
            <div class="filmrow" :ref="railRef(channel.id)" @scroll="syncArrows(channel.id)">
              <article v-for="(movie,index) in channel.items" v-feedback="{board:homeBoards[channel.id],position:index+1}" :key="cardKey(movie)" :data-card-key="cardKey(movie)" class="filmcard" :class="{lead:index===0}">
                <component :is="isExternal(movie)?'a':'button'" class="poster-shell" :class="{loaded:posterLoaded[cardKey(movie)]}" :href="isExternal(movie)?externalUrl(movie):undefined" :target="isExternal(movie)?'_blank':undefined" :rel="isExternal(movie)?'noopener noreferrer':undefined" :type="isExternal(movie)?undefined:'button'" :aria-label="isExternal(movie)?'在 TMDB 新标签页查看'+movie.title+'（库外影片）':'查看'+movie.title+'详情'" @click="openCard(movie)">
                  <span class="marker" v-if="mark(movie)">{{mark(movie)}}</span>
                  <span v-if="isExternal(movie)" class="external-mark">TMDB</span>
                  <img v-if="poster(movie)" :key="poster(movie)" :src="poster(movie)" :alt="movie.title+'电影海报'" loading="lazy" @load="posterLoaded[cardKey(movie)]=true" @error="posterError(movie,$event)"/>
                  <span class="poster-fallback">
                    <span class="fallback-label">电影海报</span>
                    <strong class="large">{{movie.title}}</strong>
                    <span class="fallback-note">海报暂未显示</span>
                  </span>
                </component>
                <div class="film-body">
                  <h3 class="film-title">{{movie.title}}</h3>
                  <p class="film-meta">{{movie.release_year}}<template v-if="genres(movie)"> · {{genres(movie)}}</template></p>
                </div>
              </article>
            </div>
            <button v-if="arrowState[channel.id]?.left" type="button" class="rail-arrow left" :aria-label="'向左滚动'+channel.title" @click="scrollRail(channel.id,-1)">
              <svg viewBox="0 0 24 24" aria-hidden="true"><path d="m15 5-7 7 7 7"/></svg>
            </button>
            <button v-if="arrowState[channel.id]?.right" type="button" class="rail-arrow right" :aria-label="'向右滚动'+channel.title" @click="scrollRail(channel.id,1)">
              <svg viewBox="0 0 24 24" aria-hidden="true"><path d="m9 5 7 7-7 7"/></svg>
            </button>
          </div>
          <div v-if="railErrors[channel.id]" class="channel-error" role="alert">
            <span>{{railErrors[channel.id]}}</span>
            <button type="button" class="inline-retry" @click="load">重新加载</button>
          </div>
          <p v-else-if="!channel.items.length" class="channel-empty">{{channel.id==='popular'?'暂无已验证且符合条件的 TMDB 热度影片。':'暂无符合当前偏好的推荐，试试完善评分或想看清单。'}}</p>
        </section>

        <section class="below">
          <div class="below-head">
            <router-link class="text-link" to="/recommendations">探索全部推荐频道</router-link>
          </div>
          <div class="divider"/>
          <div class="below-intro">
            <h2>电影不止于观看。</h2>
            <p class="below-lead">从了解一部电影，到与你的 AI 电影助手探讨电影故事，再到收藏、评分与发现喜欢的类型。这里是 MovieInsight 的个人电影空间。</p>
          </div>
          <div class="features">
            <router-link to="/ai" class="feature primary-feature">
              <svg class="feature-icon" viewBox="0 0 24 24" aria-hidden="true"><path d="M12 3l2.5 6.5L21 12l-6.5 2.5L12 21l-2.5-6.5L3 12l6.5-2.5z"/></svg>
              <div><b>AI 电影探索</b><p>理解自然语言需求，逐步发现合适的电影。需要时由用户自行连接模型 API Key。</p></div>
            </router-link>
            <router-link to="/movies" class="feature">
              <svg class="feature-icon" viewBox="0 0 24 24" aria-hidden="true"><rect x="4" y="3" width="16" height="18" rx="1"/><path d="M4 8h16M4 16h16M8 3v18M16 3v18"/></svg>
              <div><b>电影目录</b><p>搜索、筛选、查看可信电影资料及来源。</p></div>
            </router-link>
            <router-link to="/movies#behavior" class="feature">
              <svg class="feature-icon" viewBox="0 0 24 24" aria-hidden="true"><path d="M20 5c-3-3-6-1-8 1-2-2-5-4-8-1-4 4 1 9 8 15 7-6 12-11 8-15z"/></svg>
              <div><b>我的电影</b><p>管理想看、已看、收藏与个人观影偏好。</p></div>
            </router-link>
          </div>
          <footer class="footer">
            <span>© MovieInsight 2.0</span>
            <div><router-link to="/insights">数据洞察</router-link><button @click="logout">退出登录</button></div>
          </footer>
          <p v-if="logoutError" role="alert" class="logout-error">{{logoutError}}</p>
        </section>
      </div>
      <section v-else class="source-state source-empty">
        <template v-if="hasRailError">
          <h1>部分推荐暂时不可用</h1>
          <p v-for="(message,id) in railErrors" :key="id">{{message}}</p>
          <div class="state-actions"><button class="action primary" @click="load">重新加载</button><router-link class="action secondary" to="/login">重新登录</router-link></div>
        </template>
        <template v-else>
          <h1>暂无符合当前状态的推荐影片</h1>
          <p>换一个入口，或从完整目录开始找片。</p>
          <div class="state-actions"><button class="action primary" @click="load">刷新推荐</button><router-link class="action secondary" to="/movies">浏览全部电影</router-link></div>
        </template>
      </section>
    </main>
  </div>
</template>

<style scoped>
.cinema-source{--ink:#0c0d0f;--ink-soft:#141519;--paper:#fff;--paper-dim:#fff;--muted:#fff;--line:rgba(241,236,226,.16);--line-strong:rgba(241,236,226,.28);--accent:#c8a46a;--serif:'Noto Serif SC','Source Han Serif SC','Songti SC',Georgia,'Times New Roman',serif;--sans:Inter,'PingFang SC','Microsoft YaHei',sans-serif;color:var(--paper);background:var(--ink);min-height:100vh;font-family:var(--sans)}
.cinema-source *{box-sizing:border-box}
.cinema-source button{font:inherit;cursor:pointer}
.cinema-source a{color:inherit}
.cinema-source :focus-visible{outline:2px solid var(--accent);outline-offset:3px}
.cinema-source ::selection{background:var(--accent);color:var(--ink)}
.source-content{margin-left:var(--navigation-width);min-width:0;min-height:100vh;background:var(--ink)}
.site{background:var(--ink)}
.hero{position:relative;min-height:960px;display:grid;grid-template-columns:minmax(0,1fr) minmax(280px,340px);align-items:center;gap:64px;padding:48px 64px 56px;overflow:hidden;border-bottom:1px solid var(--line);background:var(--ink-soft)}
.hero .backdrop{position:absolute;inset:0;width:100%;height:100%;object-fit:cover;object-position:64% 42%;opacity:.58;filter:saturate(.7) contrast(1.05) brightness(.62);z-index:0}
.hero .image-fallback{position:absolute;inset:0;z-index:0;display:flex;align-items:flex-end;justify-content:flex-end;padding:48px 64px;background:var(--ink-soft);color:var(--muted);font-size:12px}
.hero.loaded .image-fallback{display:none}
.hero-shade{position:absolute;inset:0;z-index:1;background:linear-gradient(90deg,rgba(12,13,15,.97) 0%,rgba(12,13,15,.88) 42%,rgba(12,13,15,.42) 74%,rgba(12,13,15,.78) 100%),linear-gradient(0deg,var(--ink) 0%,transparent 34%)}
.hero-copy{position:relative;z-index:2;max-width:620px;align-self:center;padding:20px 0}
.section-rule{display:inline-block;flex:none;width:32px;height:1px;background:var(--accent)}
.hero h1{font-family:var(--serif);font-size:clamp(48px,5.2vw,76px);font-weight:500;line-height:1.02;letter-spacing:-.02em;margin:0;color:#fff;text-wrap:balance}
.title-en{font-size:14px;line-height:1.5;color:#fff;margin-top:14px;letter-spacing:.01em}
.hero-meta{display:flex;align-items:center;flex-wrap:wrap;gap:10px 16px;margin-top:22px;color:#fff;font-size:12px;line-height:1.5;font-variant-numeric:tabular-nums}
.hero-meta span+span::before{content:"·";color:rgba(255,255,255,.55);margin-right:16px}
.synopsis{max-width:560px;margin:22px 0 0;color:#fff;font-size:15px;line-height:1.9}
.hero-actions{display:flex;align-items:center;flex-wrap:wrap;gap:12px;margin-top:30px}
.action{min-height:44px;display:inline-flex;align-items:center;justify-content:center;gap:9px;padding:0 18px;border:1px solid transparent;border-radius:8px;font-size:13px;font-weight:500;line-height:1.2;text-decoration:none;transition:background-color .16s ease,border-color .16s ease,color .16s ease}
.action.primary{background:transparent;color:var(--paper);border-color:var(--paper)}
.action.primary:hover{background:rgba(241,236,226,.08);border-color:var(--paper)}
.action.secondary{background:transparent;color:var(--paper);border-color:var(--paper)}
.action.secondary:hover{border-color:var(--paper);background:rgba(241,236,226,.08)}
.action svg{width:17px;height:17px;fill:none;stroke:currentColor;stroke-width:1.5;stroke-linecap:round;stroke-linejoin:round}
.hero-poster{position:relative;z-index:3;justify-self:end;align-self:center;width:100%;max-width:340px;margin:0;background:var(--ink);border:1px solid var(--line-strong);box-shadow:0 16px 36px rgba(0,0,0,.32)}
.hero-poster img{display:block;width:100%;aspect-ratio:2/3;object-fit:cover;opacity:0;transition:opacity .35s ease}
.hero-poster.loaded img{opacity:1}
.hero-poster-fallback{display:flex;flex-direction:column;justify-content:space-between;aspect-ratio:2/3;padding:22px;background:var(--ink-soft);color:var(--paper-dim);font-size:11px}
.hero-poster-fallback strong{font-family:var(--serif);font-size:26px;line-height:1.25;font-weight:500;color:var(--paper)}
.hero-poster figcaption{padding:10px 12px;border-top:1px solid var(--line);color:var(--muted);font-size:10px;line-height:1.4}
.hero-index{position:absolute;z-index:2;left:64px;bottom:20px;color:var(--muted);font-size:10px;line-height:1.4;letter-spacing:.02em}
.library{position:relative;padding:0 64px;background:var(--ink)}
.rail-head{display:flex;align-items:flex-end;justify-content:space-between;gap:24px;padding:32px 0 16px;border-top:1px solid var(--line)}
.library:first-of-type .rail-head{border-top:0}
.rail-heading{display:flex;align-items:center;gap:12px;min-width:0}
.rail-head h2{font-family:var(--serif);font-size:28px;font-weight:500;line-height:1.15;letter-spacing:-.01em;margin:0}
.rail-meta{display:flex;align-items:baseline;gap:18px;flex-wrap:wrap;justify-content:flex-end}
.rail-meta small{color:var(--muted);font-size:12px;line-height:1.5}
.rail-meta a{color:var(--paper-dim);font-size:12px;line-height:1.5;text-decoration-color:var(--line-strong);text-underline-offset:4px}
.rail-meta a:hover{color:var(--paper)}
.filmrow-shell{position:relative}
.filmrow{display:flex;align-items:flex-start;gap:20px;overflow-x:auto;margin-top:-8px;padding:12px 0 24px;scrollbar-width:none;-ms-overflow-style:none}
.filmrow::-webkit-scrollbar{display:none}
.rail-arrow{position:absolute;z-index:3;top:12px;bottom:72px;width:96px;display:grid;place-items:center;padding:0;border:0;border-radius:10px;background:transparent;color:var(--paper);cursor:pointer;opacity:.92;transition:opacity .16s ease,background .16s ease}
.rail-arrow.left{left:0;justify-items:start;padding-left:10px;background:linear-gradient(90deg,rgba(12,13,15,.92),rgba(12,13,15,.58) 58%,rgba(12,13,15,0))}
.rail-arrow.right{right:0;justify-items:end;padding-right:10px;background:linear-gradient(270deg,rgba(12,13,15,.92),rgba(12,13,15,.58) 58%,rgba(12,13,15,0))}
.rail-arrow:hover{opacity:1}
.rail-arrow svg{width:28px;height:28px;fill:none;stroke:currentColor;stroke-width:1.6;stroke-linecap:round;stroke-linejoin:round}
.rail-arrow:focus-visible{outline:2px solid var(--paper);outline-offset:-3px}
.filmcard{width:160px;flex:none;min-width:0}
.filmcard.lead{width:320px;display:grid;grid-template-columns:140px minmax(0,1fr);gap:20px;align-items:end;padding-right:20px;border-right:1px solid var(--line)}
.poster-shell{position:relative;display:block;width:100%;aspect-ratio:2/3;padding:0;overflow:hidden;border:1px solid #ffffff28;border-radius:14px;background:#171927;color:var(--paper);text-align:left;text-decoration:none;box-shadow:0 5px 17px #0003;transition:transform .22s ease,box-shadow .22s ease,border-color .22s ease}
.poster-shell:hover,.poster-shell:focus-within{border-color:#ae9ae57c;transform:translateY(-4px);box-shadow:0 16px 35px #0009}
.poster-shell img{display:block;width:100%;height:100%;object-fit:cover;opacity:0;transition:opacity .3s ease}
.poster-shell.loaded img{opacity:1}
.poster-fallback{position:absolute;inset:0;display:flex;flex-direction:column;justify-content:space-between;padding:14px;background:var(--ink-soft);color:var(--paper-dim)}
.poster-shell.loaded .poster-fallback{display:none}
.poster-fallback .large{font-family:var(--serif);font-size:20px;font-weight:500;line-height:1.25;color:var(--paper)}
.fallback-label,.fallback-note{font-size:10px;line-height:1.4;color:var(--muted)}
.marker{position:absolute;z-index:2;top:8px;left:8px;padding:5px 8px;border:1px solid var(--line-strong);border-radius:2px;background:rgba(12,13,15,.86);color:var(--paper);font-size:10px;line-height:1.2}
.external-mark{position:absolute;z-index:2;right:8px;bottom:8px;max-width:calc(100% - 16px);padding:6px 10px;border:1px solid rgba(255,255,255,.34);border-radius:999px;background:rgba(20,23,31,.78);box-shadow:inset 0 1px 0 rgba(255,255,255,.25),0 3px 10px rgba(0,0,0,.22);color:#fff;text-shadow:0 1px 3px rgba(0,0,0,.6);font-family:var(--sans);font-size:10px;line-height:1.2;letter-spacing:.02em;white-space:nowrap;pointer-events:none}
@supports ((backdrop-filter:blur(1px)) or (-webkit-backdrop-filter:blur(1px))){.external-mark{background:linear-gradient(145deg,rgba(255,255,255,.2),rgba(255,255,255,.05) 48%,rgba(255,255,255,.12)),rgba(18,21,29,.38);-webkit-backdrop-filter:blur(3px) saturate(150%);backdrop-filter:blur(3px) saturate(150%)}}
.poster-shell:focus-visible{outline:2px solid var(--paper);outline-offset:3px}
.film-body{min-width:0;padding-top:10px}
.filmcard.lead .film-body{padding:0 0 2px}
.film-title{font-size:13px;line-height:1.5;font-weight:500;color:var(--paper);margin:0 0 4px;overflow-wrap:anywhere}
.filmcard.lead .film-title{font-family:var(--serif);font-size:24px;line-height:1.2;font-weight:500;letter-spacing:-.01em;margin-bottom:8px}
.film-meta{color:var(--muted);font-size:11px;line-height:1.5;margin:0;font-variant-numeric:tabular-nums}
.filmcard.lead .film-meta{font-size:12px}
.channel-error{display:flex;align-items:center;justify-content:space-between;gap:16px;padding:16px 0 24px;color:var(--paper-dim);font-size:13px;line-height:1.7}
.inline-retry{padding:8px 12px;border:1px solid var(--line-strong);border-radius:6px;background:transparent;color:var(--paper);font-size:12px}
.inline-retry:hover{border-color:var(--paper);background:rgba(241,236,226,.06)}
.channel-empty{padding:4px 0 28px;color:var(--muted);font-size:13px;line-height:1.8}
.below{padding:64px;border-top:1px solid var(--line);background:var(--ink)}
.below-head{display:flex;align-items:baseline;justify-content:space-between;gap:24px;margin-bottom:20px}
.text-link{color:var(--paper-dim);font-size:12px;line-height:1.5;text-decoration-color:var(--line-strong);text-underline-offset:4px}
.text-link:hover{color:var(--paper)}
.divider{height:1px;background:var(--line);margin-bottom:32px}
.below-intro{display:grid;grid-template-columns:minmax(0,1fr) minmax(280px,400px);align-items:end;gap:64px;margin-bottom:40px}
.below h2{font-family:var(--serif);font-size:clamp(30px,3.4vw,48px);font-weight:500;line-height:1.1;letter-spacing:-.02em;margin:0;color:var(--paper)}
.below-lead{color:var(--paper-dim);font-size:13px;line-height:1.9;margin:0}
.features{display:grid;grid-template-columns:1.2fr 1fr 1fr;border-top:1px solid var(--line)}
.feature{display:flex;flex-direction:column;justify-content:space-between;gap:24px;min-height:180px;padding:24px 24px 24px 0;border-right:1px solid var(--line);color:var(--paper-dim);text-decoration:none;transition:color .16s ease}
.feature+.feature{padding-left:24px}
.feature:last-child{padding-right:0;border-right:0}
.feature:hover{color:var(--paper)}
.feature-icon{width:22px;height:22px;fill:none;stroke:var(--accent);stroke-width:1.5;stroke-linecap:round;stroke-linejoin:round}
.feature b{display:block;color:var(--paper);font-size:15px;font-weight:500;line-height:1.5;margin-bottom:8px}
.feature p{font-size:12px;line-height:1.8;margin:0;color:var(--muted)}
.footer{display:flex;align-items:center;justify-content:space-between;gap:16px;border-top:1px solid var(--line);margin-top:64px;padding:20px 0 40px;color:var(--muted);font-size:11px;line-height:1.5}
.footer div{display:flex;align-items:center;gap:20px}
.footer a,.footer button{padding:0;border:0;background:none;color:inherit;text-decoration:none}
.footer a:hover,.footer button:hover{color:var(--paper)}
.logout-error{margin:8px 0 0;color:#d99a8f;font-size:12px}
.source-state{min-height:70vh;display:flex;flex-direction:column;align-items:flex-start;justify-content:center;gap:14px;padding:96px 64px;background:var(--ink)}
.source-state h1{font-family:var(--serif);font-size:clamp(34px,4vw,54px);font-weight:500;line-height:1.1;letter-spacing:-.02em;margin:0;color:var(--paper)}
.source-state p{max-width:520px;margin:0;color:var(--paper-dim);font-size:14px;line-height:1.8}
.state-actions{display:flex;align-items:center;flex-wrap:wrap;gap:12px;margin-top:14px}
@media (max-width:1100px){.hero,.library,.below,.source-state{padding-left:40px;padding-right:40px}.hero{grid-template-columns:minmax(0,1fr) 280px;gap:40px}.hero-index{left:40px}.hero-copy{max-width:none}}
@media (max-width:760px){
  .source-content{overflow-x:hidden}
  .hero{display:block;min-height:0;padding:0 20px 32px;overflow:hidden}
  .hero .backdrop{top:0;bottom:auto;height:260px;object-position:58% 34%}
  .hero .image-fallback{inset:0 0 auto;height:260px;padding:20px;align-items:flex-start}
  .hero-shade{background:linear-gradient(0deg,var(--ink) 0%,rgba(12,13,15,.55) 36%,rgba(12,13,15,.10) 100%)}
  .hero-copy{padding:150px 0 0;max-width:none}
  .hero h1{font-size:clamp(34px,11vw,46px);max-width:calc(100% - 108px)}
  .hero-poster{display:block;position:absolute;top:16px;right:20px;width:92px;max-width:92px;margin:0;box-shadow:0 12px 24px rgba(0,0,0,.32)}
  .hero-poster figcaption{display:none}
  .hero-index{display:none}
  .hero-meta{margin-top:14px;gap:8px 12px}
  .hero-meta span+span::before{margin-right:12px}
  .synopsis{margin-top:14px;font-size:14px;line-height:1.75;display:-webkit-box;-webkit-line-clamp:3;-webkit-box-orient:vertical;overflow:hidden}
  .hero-actions{margin-top:20px}
  .library{padding:0 20px}
  .rail-head{display:block;padding:24px 0 12px}
  .rail-head h2{font-size:24px}
  .rail-meta{margin-top:8px;justify-content:flex-start;gap:8px 16px}
  .filmrow{gap:14px;padding-bottom:18px}
  .rail-arrow{top:10px;bottom:84px;width:48px}
  .rail-arrow.left{padding-left:4px}
  .rail-arrow.right{padding-right:4px}
  .rail-arrow svg{width:24px;height:24px}
  .filmcard{width:132px}
  .filmcard.lead{width:262px;grid-template-columns:104px minmax(0,1fr);gap:12px;padding-right:12px}
  .filmcard.lead .film-title{font-size:20px}
  .below{padding:40px 20px}
  .below-head{margin-bottom:16px}
  .below-intro{display:block;margin-bottom:28px}
  .below h2{font-size:34px;margin-bottom:14px}
  .below-lead{max-width:520px}
  .features{display:block;border-top:0}
  .feature{min-height:0;padding:20px 0;border-top:1px solid var(--line);border-right:0;gap:16px}
  .feature+.feature{padding-left:0}
  .footer{margin-top:40px;padding-bottom:28px}
  .footer div{gap:14px}
  .source-state{min-height:65vh;padding:64px 20px}
}
@media (prefers-reduced-motion:reduce){.poster-shell,.poster-shell img,.hero-poster img,.action,.feature,.rail-arrow{transition:none}}
@media (prefers-reduced-motion:reduce){.poster-shell:hover,.poster-shell:focus-within{transform:none;box-shadow:none}}
</style>
