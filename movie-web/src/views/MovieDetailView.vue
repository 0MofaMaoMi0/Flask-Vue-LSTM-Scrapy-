<script setup lang="ts">
import {ref,computed,watch,onMounted,onBeforeUnmount,nextTick} from 'vue'
import {useRoute,useRouter} from 'vue-router'
import AppSidebar from '../components/AppSidebar.vue'
import {movieArtwork} from '../services/movieArtwork'
import {assistantReturnTarget} from '../services/movieNavigation'
import {ISO2_TO_ISO3} from '../lib/globe/iso2to3.js'
import {countryName} from '../lib/globe/countryDisplay.js'
const route=useRoute(),router=useRouter(),expanded=ref(localStorage.getItem('mi2_nav_expanded')==='true')
const movie=ref<any>(null),state=ref<any>({}),recommendations=ref<any[]>([]),busy=ref(true),saving=ref(false),error=ref(''),actionError=ref(''),notice=ref(''),rating=ref('4'),posterFailed=ref(false),backdropFailed=ref(false),behaviorReady=ref(false),countries=ref<string[]>([])
const id=computed(()=>route.hash.slice(7)),art=computed(()=>movieArtwork[id.value])
const tmdbId=computed(()=>((movie.value?.external_ids||[]).find((x:any)=>x.source==='tmdb')||{}).external_id||'')
// TMDB 热度由详情接口按当前影片的 TMDB 映射返回；缺失或非法值不展示。
const tmdbHeat=computed(()=>{const row=movie.value?.tmdb_heat,popularity=row?.popularity,expected=Number(tmdbId.value);if(!row||!Number.isSafeInteger(expected)||Number(row.tmdb_id)!==expected||!['OK','STALE'].includes(row.status)||typeof popularity!=='number'||!Number.isFinite(popularity)||popularity<0)return null;return {popularity,observed:typeof row.observed_at==='string'?row.observed_at:'',stale:row.status==='STALE'}})
const mediaUrl=(a?:any)=>a?.url||(a?.source_path?.startsWith('http')?'/api/media/poster?u='+encodeURIComponent(a.source_path):undefined)
const asset=computed(()=>{const list=movie.value?.assets||[];const pick=(type:string)=>list.find((a:any)=>a.asset_type===type&&a.is_primary)||list.find((a:any)=>a.asset_type===type);return {poster:mediaUrl(pick('poster'))||art.value?.poster,backdrop:mediaUrl(pick('backdrop'))||art.value?.backdrop}})
const photoFailures=ref<Record<string,boolean>>({})
function photo(c:any){
 const path=c.profile_path
 if(typeof path!=='string'||!/^\/[A-Za-z0-9_-]+\.(?:jpg|jpeg|png|webp)$/i.test(path))return ''
 return '/api/media/poster?u='+encodeURIComponent('https://image.tmdb.org/t/p/w185'+path)
}
const photoKey=(c:any)=>String(c.person_id)+':'+String(c.profile_path)
const CAST_PREVIEW_LIMIT=9
const visibleCredits=computed(()=>[...directors.value,...cast.value].slice(0,CAST_PREVIEW_LIMIT))
const missingPhotos=computed(()=>visibleCredits.value.filter(c=>!photo(c)||photoFailures.value[photoKey(c)]).length)

const tmdbMovieUrl=computed(()=>{const value=String(tmdbId.value);return /^[1-9]\d*$/.test(value)&&Number.isSafeInteger(Number(value))?`https://www.themoviedb.org/movie/${value}`:''})
const tmdbCastUrl=computed(()=>tmdbMovieUrl.value?tmdbMovieUrl.value+'/cast':'')
const posterFallback=computed(()=>mediaUrl((movie.value?.assets||[]).find((a:any)=>a.asset_type==='poster'&&a.source==='tmdb')))
function onPosterError(e:Event){const img=e.target as HTMLImageElement;const fb=posterFallback.value;if(fb&&!img.dataset.fb){img.dataset.fb='1';img.src=fb}else{posterFailed.value=true}}
function preferredCredits(rows:any[]){const official=rows.filter(c=>c.source==='tmdb');return official.length?official:rows}
const directors=computed(()=>preferredCredits((movie.value?.credits||[]).filter((c:any)=>c.role_type==='crew'&&c.job==='Director')))
const cast=computed(()=>preferredCredits((movie.value?.credits||[]).filter((c:any)=>c.role_type==='cast'))),genreText=computed(()=>(movie.value?.genres||[]).map((g:any)=>g.name_zh).join(' / ')),sourceRatings=computed(()=>movie.value?.ratings||[])
const castStrip=ref<HTMLElement>(),castArrows=ref({left:false,right:false})
function syncCastArrows(){const el=castStrip.value;if(!el){castArrows.value={left:false,right:false};return}const max=el.scrollWidth-el.clientWidth;castArrows.value={left:el.scrollLeft>1,right:max>1&&el.scrollLeft<max-1}}
function scrollCast(direction:number){const el=castStrip.value;if(!el)return;const reduced=window.matchMedia('(prefers-reduced-motion: reduce)').matches;el.scrollBy({left:direction*Math.max(140,el.clientWidth-152),behavior:reduced?'auto':'smooth'})}
watch(visibleCredits,()=>nextTick(syncCastArrows),{flush:'post'})
watch(busy,()=>nextTick(syncCastArrows),{flush:'post'})
onMounted(()=>{window.addEventListener('resize',syncCastArrows);nextTick(syncCastArrows)})
const actions=[{kind:'want',name:'想看',path:'M12 5v14M5 12h14'},{kind:'watched',name:'已看',path:'m5 12 4 4L19 6'},{kind:'favorite',name:'收藏',path:'M20.8 4.6a5.5 5.5 0 0 0-7.8 0L12 5.7l-1.1-1.1a5.5 5.5 0 0 0-7.8 7.8L12 21l8.8-8.6a5.5 5.5 0 0 0 0-7.8Z'},{kind:'not_interested',name:'不感兴趣',path:'M5 5l14 14M21 12a9 9 0 1 1-18 0 9 9 0 0 1 18 0'}]
let controller:AbortController|undefined,serial=0,disposed=false
async function api(path:string,signal?:AbortSignal){const r=await fetch('/api/catalog/'+path,{credentials:'same-origin',signal});let body:any=null;try{body=await r.json()}catch{body=null};if(!r.ok)throw new Error(r.status===404?'没有找到这部电影。':r.status===401?'登录已失效，请重新登录。':'资料暂时无法读取，请重试。');if(!body||body.code!=='OK'||body.data==null)throw new Error('电影资料响应异常，请重试。');return body.data}
async function readBehavior(signal?:AbortSignal){const target=id.value;const b=await api('behavior',signal);if(disposed||signal?.aborted||target!==id.value)return;state.value=b.state[target]||{};rating.value=String(state.value.rating??4);behaviorReady.value=true}
async function load(){controller?.abort();controller=new AbortController();const signal=controller.signal,n=++serial;busy.value=true;error.value='';movie.value=null;notice.value='';actionError.value='';posterFailed.value=false;backdropFailed.value=false;photoFailures.value={};behaviorReady.value=false;state.value={};recommendations.value=[];countries.value=[];try{if(!/^[1-9]\d*$/.test(id.value))throw new Error('电影编号无效。');const data=await api('movies/'+id.value,signal);if(disposed||n!==serial)return;if(data.credits_has_more){const all:any[]=[];let offset=0;while(true){const page=await api('movies/'+id.value+'/credits?limit=100&offset='+offset,signal);if(disposed||n!==serial)return;all.push(...page.items);if(!page.has_more)break;if(!Number.isSafeInteger(page.next_offset)||page.next_offset<=offset)throw new Error('演职员分页异常，请重试。');offset=page.next_offset}data.credits=all}movie.value=data;document.title=(data.title||'电影详情')+' · MovieInsight';try{await readBehavior(signal)}catch{if(!disposed&&!signal.aborted&&n===serial)actionError.value='个人状态读取失败，请刷新状态后操作。'}try{const cc=await api('production-countries',signal);if(!disposed&&n===serial){const row=(cc.items||[]).find((x:any)=>String(x.movie_id)===id.value);countries.value=((row?.countries)||[]).map((c:any)=>(ISO2_TO_ISO3 as Record<string,string>)[c.iso2]).filter(Boolean)}}catch{}try{const r=await api('recommendations?limit=6',signal);if(!disposed&&n===serial)recommendations.value=r.items.filter((m:any)=>String(m.movie_id)!==id.value).slice(0,3)}catch{}}catch(e){if(!disposed&&!signal.aborted&&n===serial)error.value=(e as Error).message}finally{if(!disposed&&n===serial)busy.value=false}}
watch(id,load,{immediate:true})
function back(){if(route.query.from==='ai'||route.query.from==='floating'){router.push(assistantReturnTarget(route.query.from,route.query.return_to));return}if(route.query.from==='recommendations'){const query={...route.query};delete query.from;router.push({path:'/recommendations',query});return}if(route.query.from==='admin'){router.push('/admin');return}if(route.query.from==='home'){router.push('/home');return}if(route.query.from==='behavior'){router.push({path:'/movies',hash:'#behavior',query:{view:route.query.view}});return}const query={...route.query};delete query.from;router.push({path:'/movies',query,hash:''})}
function openMovie(mid:number){router.push({path:'/movies',query:route.query,hash:'#movie/'+mid})}
async function refreshState(){try{await readBehavior(controller?.signal);actionError.value='';notice.value='状态已刷新'}catch{if(!disposed)actionError.value='状态仍无法确认，请稍后重试。'}}
async function act(kind:string,undo=false,value:number|null=null){if(saving.value||!behaviorReady.value)return;const target=id.value;saving.value=true;notice.value='';actionError.value='';try{const csrf=await fetch('/api/v1/auth/csrf',{credentials:'same-origin'});let cb:any=null;try{cb=await csrf.json()}catch{cb=null};const token=cb?.data?.csrf_token;if(!csrf.ok||!token||(cb?.code!==0&&cb?.code!=='OK'))throw new Error('登录已失效，请重新登录。');const r=await fetch('/api/catalog/behavior',{method:'POST',credentials:'same-origin',headers:{'Content-Type':'application/json','X-CSRF-Token':token},body:JSON.stringify({event_id:crypto.randomUUID(),movie_id:Number(target),kind,action:undo?'undo':'set',value,time:new Date().toISOString(),source:'USER_ACTION'})});let rb:any=null;try{rb=await r.json()}catch{rb=null};if(!r.ok||rb?.code!=='OK')throw new Error(r.status===409?'行为状态冲突，请先撤销已看或不感兴趣，再操作。':'保存结果未确认，请刷新状态；不会自动重复提交。');if(!disposed&&target===id.value){await readBehavior(controller?.signal);notice.value=undo?'已撤销':'已保存';window.dispatchEvent(new Event('mi2-behavior-changed'))}}catch(e){if(!disposed&&target===id.value){actionError.value=(e as Error).message;behaviorReady.value=false}}finally{if(!disposed)saving.value=false}}
onBeforeUnmount(()=>{disposed=true;++serial;controller?.abort();window.removeEventListener('resize',syncCastArrows)})
</script>
<template>
<div class="detail-shell" :style="{'--nav-width':expanded?'280px':'52.5px'}"><AppSidebar @expanded-change="expanded=$event"/><div class="detail-content"><div class="container breadcrumb"><button @click="back">{{route.query.from==='behavior'?'返回我的电影':route.query.from==='ai'?'返回 AI 探索':route.query.from==='floating'?'返回原页面':route.query.from==='recommendations'?'返回推荐频道':route.query.from==='home'?'返回影院首页':route.query.from==='admin'?'返回数据驾驶舱':'返回电影库'}}</button><span class="divider">›</span><strong>{{movie?.title||'电影详情'}}</strong></div>
<div v-if="busy" role="status" class="loading">正在读取电影资料…</div><div v-else-if="error" role="alert" class="loading"><h1>暂时无法展示</h1><p>{{error}}</p><button class="btn" @click="load">重试</button></div>
<template v-else-if="movie"><section class="hero" aria-labelledby="movie-title"><img v-if="asset.backdrop&&!backdropFailed" class="backdrop-art" :src="asset.backdrop" alt="" @error="backdropFailed=true"/><div class="container hero-grid"><div class="poster"><img v-if="asset.poster&&!posterFailed" :src="asset.poster" :alt="movie.title+'海报'" @error="onPosterError"/><span v-else>{{movie.title}}</span></div><div class="hero-copy"><h1 id="movie-title">{{movie.title}}</h1><div class="en">{{movie.original_title||''}}</div><div class="meta"><span>{{movie.release_year||'年份未知'}}</span><span class="sep">·</span><span>{{movie.runtime_minutes?movie.runtime_minutes+' 分钟':'片长未知'}}</span><span v-for="g in movie.genres" :key="g.genre_id" class="genre">{{g.name_zh}}</span></div><p class="hero-intro">{{movie.overview||'暂无剧情简介。'}}</p><div class="cta"><button v-for="a in actions" :key="a.kind" class="btn" :class="{primary:a.kind==='want',danger:a.kind==='not_interested'}" :disabled="saving||!behaviorReady" :aria-pressed="!!state[a.kind]" @click="act(a.kind,!!state[a.kind])"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6"><path :d="a.path"/></svg>{{state[a.kind]?'撤销':''}}{{a.name}}</button><a v-if="tmdbMovieUrl" class="btn tmdb-link" :href="tmdbMovieUrl" target="_blank" rel="noopener noreferrer" :aria-label="'在 TMDB 查看'+movie.title+'（新标签页打开）'" title="新标签页打开"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" aria-hidden="true"><path d="M14 3h7v7M21 3 10 14M10 3H5a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-5"/></svg>在 TMDB 查看</a></div><div class="banner-state" role="status" aria-live="polite">{{saving?'正在保存…':notice||'记录你的观影选择'}}</div><p v-if="actionError" role="alert" class="action-error">{{actionError}} <button class="textlink" :disabled="saving" @click="refreshState">刷新状态</button></p></div></div></section>
<div class="container content"><main class="main"><section class="section"><h2>剧情简介</h2><p class="overview">{{movie.overview||'暂无剧情简介。'}}</p></section><section class="section"><div class="section-heading"><h2>演员阵容</h2><a v-if="tmdbCastUrl" class="cast-more" :href="tmdbCastUrl" target="_blank" rel="noopener">查看更多 →</a></div><div v-if="visibleCredits.length" class="cast-carousel"><div ref="castStrip" class="cast-strip" tabindex="0" role="group" aria-label="演员阵容，横向滚动查看" @scroll="syncCastArrows"><article v-for="c in visibleCredits" :key="c.credit_id" class="credit"><img v-if="photo(c)&&!photoFailures[photoKey(c)]" class="credit-photo" :src="photo(c)" :alt="c.name+'照片'" loading="lazy" @error="photoFailures[photoKey(c)]=true"/><span v-else class="avatar" :aria-label="c.name+'暂无照片'" role="img">{{c.name.slice(0,1)}}</span><b>{{c.name}}</b><small>{{c.character_name||(c.role_type==='crew'?'导演':'演员')}}</small></article><a v-if="tmdbCastUrl" class="cast-full" :href="tmdbCastUrl" target="_blank" rel="noopener">完整演职员表</a></div><button v-if="castArrows.left" type="button" class="cast-arrow left" aria-label="向左滚动演职员" @click="scrollCast(-1)"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="m15 5-7 7 7 7"/></svg></button><button v-if="castArrows.right" type="button" class="cast-arrow right" aria-label="向右滚动演职员" @click="scrollCast(1)"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="m9 5 7 7-7 7"/></svg></button></div><p v-else class="muted">演职员资料暂未提供。</p><p v-if="missingPhotos" class="muted">展示的 {{missingPhotos}} 位演职人员暂无可用照片，姓名与职务仍可查看。</p></section><section class="section"><div class="section-heading"><h2>继续探索</h2><span class="muted">本地推荐</span></div><div v-for="m in recommendations" :key="m.movie_id" class="recommend"><button v-if="movieArtwork[String(m.movie_id)]" class="recommend-poster" :aria-label="'查看'+m.title+'海报详情'" @click="openMovie(m.movie_id)"><img :src="movieArtwork[String(m.movie_id)]!.poster" :alt="m.title+'海报'" loading="lazy"/></button><div><b>{{m.title}}</b><small>{{m.reasons?.join('；')}}</small><button :aria-label="'查看'+m.title+'详情'" @click="openMovie(m.movie_id)">查看电影信息</button></div></div><p v-if="!recommendations.length" class="muted">暂无推荐候选。</p></section><section class="section"><h2>来源评分</h2><div v-for="r in sourceRatings" :key="r.source" class="info-row"><span>{{r.source}}</span><strong>{{r.score_value??'暂无'}} / {{r.score_scale}}<small class="score-sub"> · {{r.vote_count??'未知'}} 票</small></strong></div><p v-if="!sourceRatings.length" class="muted">暂无来源评分。</p></section></main><aside class="facts-sidebar"><section class="section"><h2>影片信息</h2><div class="info-row"><span>上映年份</span><strong>{{movie.release_year||'未知'}}</strong></div><div class="info-row"><span>片长</span><strong>{{movie.runtime_minutes?movie.runtime_minutes+' 分钟':'未知'}}</strong></div><div class="info-row"><span>原始语言</span><strong>{{movie.original_language||'未知'}}</strong></div><div class="info-row"><span>类型</span><strong>{{genreText||'待补充'}}</strong></div><div class="info-row"><span>制作国家</span><strong>{{countries.length?countries.map((c:string)=>countryName(c)).join(' / '):'未知'}}</strong></div><div v-if="tmdbHeat" class="info-row"><span>TMDB 热度</span><strong>{{tmdbHeat.popularity.toFixed(1)}}<small v-if="tmdbHeat.stale" class="score-sub"> · 已过期</small></strong></div><div v-if="tmdbHeat&&tmdbHeat.observed" class="info-row"><span>热度采集于</span><strong>{{tmdbHeat.observed.slice(0,10)}}</strong></div><div v-for="x in movie.external_ids" :key="x.source" class="info-row"><span>{{x.source}} ID</span><strong>{{x.external_id}}</strong></div></section><section class="section"><h2>我的评分</h2><div class="score-stats"><div class="score-value">{{state.rating??'—'}}</div><div class="score-sub">满分 5 星<br/>你的个人评分</div></div><div class="stars" role="group" aria-label="快捷评分"><button v-for="n in 5" :key="n" class="star" :class="{active:Number(state.rating)>=n}" :aria-label="n+'星'" :disabled="saving||!behaviorReady" @click="act('rating',false,n)"><svg width="18" height="18" viewBox="0 0 24 24" fill="currentColor"><path d="m12 2 3 6.5 7 1-5 5 1.2 7L12 18l-6.2 3.5 1.2-7-5-5 7-1Z"/></svg></button></div><form class="rating-form" @submit.prevent="act('rating',false,Number(rating))"><label>精确评分<select v-model="rating" :disabled="saving||!behaviorReady"><option v-for="n in 10" :key="n" :value="String(n/2)">{{n/2}} 星</option></select></label><button class="btn" :disabled="saving||!behaviorReady">保存评分</button></form><div class="mini-links"><button class="textlink" :disabled="saving||!behaviorReady||!state.rating" @click="act('rating',true)">清除我的评分</button></div></section></aside></div><footer class="container footer">电影资料来自已授权静态 TMDB 副本。This product uses the TMDB API but is not endorsed or certified by TMDB. <a href="https://www.themoviedb.org/" target="_blank" rel="noopener">TMDB</a></footer></template>
</div></div>
</template>
<style scoped>
:global(body:has(.detail-shell)){margin:0;background:#0c0d0f;color:#fff}
.detail-shell{--ink:#0c0d0f;--ink-soft:#141519;--paper:#fff;--paper-dim:rgba(255,255,255,.82);--muted:rgba(255,255,255,.58);--line:rgba(241,236,226,.14);--line-strong:rgba(241,236,226,.26);--accent:#c8a46a;--serif:'Noto Serif SC','Source Han Serif SC','Songti SC',Georgia,'Times New Roman',serif;--sans:Inter,'PingFang SC','Microsoft YaHei',sans-serif;position:relative;z-index:1;min-height:100vh;background:var(--ink);color:var(--paper);font-family:var(--sans)}
.detail-shell *{box-sizing:border-box}
.detail-shell button,.detail-shell select{font:inherit}
.detail-shell button{cursor:pointer}
.detail-shell button:disabled{opacity:.45;cursor:default}
.detail-shell :focus-visible{outline:2px solid var(--accent);outline-offset:3px}
.detail-shell a{color:inherit}
.detail-shell ::selection{background:var(--accent);color:var(--ink)}
.detail-content{margin-left:var(--nav-width);width:calc(100% - var(--nav-width));transition:margin-left .25s ease;min-width:0}
.container{width:min(1500px,calc(100% - clamp(36px,6vw,108px)));margin:0 auto}
.breadcrumb{height:58px;display:flex;align-items:center;gap:10px;color:var(--muted);font-size:12px;min-width:0}
.breadcrumb button{border:0;background:transparent;color:var(--paper-dim);cursor:pointer;padding:8px 0;white-space:nowrap}
.breadcrumb .divider{opacity:.5}
.breadcrumb strong{font-weight:500;color:var(--paper);white-space:nowrap;overflow:hidden;text-overflow:ellipsis;min-width:0}
.hero{position:relative;min-height:560px;isolation:isolate;background:var(--ink-soft);overflow:hidden;border-bottom:1px solid var(--line)}
.backdrop-art{position:absolute;inset:0;width:100%;height:100%;object-fit:cover;object-position:center 38%;z-index:-2;opacity:.72;filter:saturate(.72) contrast(1.04) brightness(.62)}
.hero:after{content:"";position:absolute;z-index:-1;inset:0;background:linear-gradient(90deg,var(--ink) 0%,rgba(12,13,15,.94) 34%,rgba(12,13,15,.42) 76%,rgba(12,13,15,.72) 100%),linear-gradient(0deg,var(--ink) 0%,transparent 46%)}
.hero-grid{display:grid;grid-template-columns:minmax(200px,280px) minmax(0,1fr);gap:48px;align-items:end;min-height:560px;padding:32px 0 54px}
.poster{width:100%;max-width:280px;aspect-ratio:2/3;border:1px solid rgba(255,255,255,.3);border-radius:14px;overflow:hidden;box-shadow:0 18px 48px #000c;background:var(--ink-soft);display:grid;place-items:center}
.poster img{width:100%;height:100%;object-fit:cover;display:block;grid-area:1/1;opacity:1;transition:none}
.hero-copy{padding-bottom:4px;max-width:780px;min-width:0}


.hero h1{font-family:var(--serif);font-size:clamp(46px,5.2vw,74px);font-weight:500;line-height:1.04;margin:0 0 10px;letter-spacing:-.025em;color:var(--paper);overflow-wrap:anywhere;text-wrap:balance}
.en{font-size:15px;color:var(--paper-dim);letter-spacing:.01em;margin-bottom:20px;line-height:1.5}
.meta{display:flex;flex-wrap:wrap;align-items:center;gap:9px 12px;color:var(--paper);font-size:12px;line-height:1.5;font-variant-numeric:tabular-nums}
.meta .sep{color:var(--muted)}
.genre{display:inline-flex;padding:4px 9px;border:1px solid var(--line-strong);background:rgba(12,13,15,.44);border-radius:999px;color:var(--paper);font-size:11px;backdrop-filter:blur(3px)}
.hero-intro{margin:20px 0 22px;color:var(--paper-dim);font-size:14px;line-height:1.9;max-width:640px;display:-webkit-box;-webkit-line-clamp:3;-webkit-box-orient:vertical;overflow:hidden;white-space:pre-line}
.cta{display:flex;flex-wrap:wrap;gap:9px;align-items:center}
.btn{border-radius:8px;border:1px solid var(--line-strong);background:transparent;color:var(--paper);padding:11px 15px;display:inline-flex;align-items:center;justify-content:center;gap:8px;font-weight:500;font-size:12px;transition:background-color .16s,border-color .16s,color .16s}
.btn:hover{background:rgba(255,255,255,.07);border-color:var(--paper)}
.btn.primary{background:transparent;color:var(--paper);border-color:var(--paper)}
.btn.primary:hover{background:rgba(255,255,255,.08)}
.btn[aria-pressed=true]{background:rgba(200,164,106,.12);border-color:var(--accent);color:var(--paper)}
.btn.danger[aria-pressed=true]{background:rgba(178,94,109,.12);border-color:rgba(220,142,152,.7)}
.btn svg{width:16px;height:16px;flex:none}
.tmdb-link{text-decoration:none}
.banner-state{font-size:11px;color:var(--muted);margin-top:15px;line-height:1.6}
.action-error{color:#e7b2bf;font-size:12px;line-height:1.8}
.textlink{border:0;padding:0;background:none;color:var(--paper-dim);font-size:12px;text-decoration:underline;text-underline-offset:3px;text-decoration-color:var(--line-strong)}
.textlink:hover{color:var(--paper)}
.content{display:grid;grid-template-columns:minmax(0,1fr) 320px;gap:32px;padding:34px 0 72px}
.main{min-width:0}
.facts-sidebar{min-width:0}
.section{background:var(--ink-soft);border:1px solid var(--line);border-radius:14px;padding:26px 27px;margin-bottom:16px;min-width:0}
.section h2{font-family:var(--serif);font-size:24px;font-weight:500;letter-spacing:-.01em;margin:0 0 16px;color:var(--paper)}
.section h3{font-size:15px;font-weight:500;margin:0 0 14px}
.overview{font-size:14px;color:var(--paper-dim);line-height:1.95;margin:0;white-space:pre-line}
.mini-links{display:flex;gap:16px;align-items:center;flex-wrap:wrap;margin-top:17px}
.section-heading{display:flex;align-items:center;justify-content:space-between;gap:12px}
.cast-more{font-size:12px;color:var(--paper-dim);text-decoration:none;white-space:nowrap}
.cast-more:hover{color:var(--paper);text-decoration:underline;text-underline-offset:3px}
.cast-carousel{position:relative}
.cast-strip{display:flex;gap:12px;overflow-x:auto;padding:2px 2px 10px;scrollbar-width:none;-ms-overflow-style:none}
.cast-strip::-webkit-scrollbar{display:none}
.cast-arrow{position:absolute;z-index:3;top:2px;bottom:10px;height:auto;width:64px;display:grid;place-items:center;padding:0;border:0;border-radius:12px;color:#fff;cursor:pointer;opacity:.92;transition:opacity .16s ease}
.cast-arrow.left{left:2px;justify-items:start;padding-left:10px;background:linear-gradient(90deg,rgba(12,13,15,.92),rgba(12,13,15,.58) 58%,transparent)}
.cast-arrow.right{right:2px;justify-items:end;padding-right:10px;background:linear-gradient(270deg,rgba(12,13,15,.92),rgba(12,13,15,.58) 58%,transparent)}
.cast-arrow:hover{opacity:1}
.cast-arrow svg{display:block;width:28px;height:28px;fill:none;stroke:currentColor;stroke-width:1.6;stroke-linecap:round;stroke-linejoin:round}
.cast-arrow:focus-visible{outline:2px solid #fff;outline-offset:-3px}
@media(max-width:600px){.cast-arrow{width:48px}.cast-arrow svg{width:24px;height:24px}}
@media(prefers-reduced-motion:reduce){.cast-arrow{transition:none}}
.credit{display:flex;flex-direction:column;gap:0;align-items:stretch;border:1px solid var(--line);border-radius:12px;padding:0;background:var(--ink-soft);flex:0 0 140px;min-width:0;min-height:243px;overflow:hidden}
.credit-photo,.avatar{flex:none;display:grid;place-items:center;width:100%;height:175px;border-radius:0;background:#1d1e22;color:var(--paper-dim);font:500 36px var(--serif);object-fit:cover;object-position:center 24%;border:0}
.credit b{display:block;padding:10px 10px 0;font-size:13px;line-height:1.4;font-weight:600;white-space:normal;overflow-wrap:anywhere;color:var(--paper)}
.credit small{display:block;padding:4px 10px 12px;font-size:11px;line-height:1.4;color:var(--muted);white-space:normal;overflow-wrap:anywhere}
.cast-full{align-self:flex-end;flex:0 0 auto;color:var(--paper-dim);font-size:11px;text-decoration:underline;text-underline-offset:3px;padding:0 6px 6px;white-space:nowrap}
.recommend{display:flex;align-items:center;gap:14px;padding:12px;background:rgba(12,13,15,.3);border-radius:10px;border:1px solid var(--line);max-width:620px;margin-bottom:10px}
.recommend .recommend-poster{padding:0;background:none;border:0;flex:none;border-radius:6px;overflow:hidden}
.recommend img{width:72px;aspect-ratio:2/3;object-fit:cover;border-radius:6px;display:block}
.recommend b{font-size:13px;display:block;font-weight:500;color:var(--paper)}
.recommend small{font-size:11px;color:var(--muted);margin:6px 0 9px;display:block;line-height:1.6}
.recommend button{padding:7px 10px;border:1px solid var(--line-strong);border-radius:6px;background:transparent;color:var(--paper);font-size:11px}
.muted{color:var(--muted);font-size:12px;line-height:1.8}
.facts-sidebar .section{padding:24px}
.info-row{display:flex;justify-content:space-between;gap:20px;border-bottom:1px solid var(--line);padding:12px 0;font-size:12px;line-height:1.6}
.info-row span{color:var(--muted)}
.info-row strong{color:var(--paper-dim);font-weight:500;text-align:right;overflow-wrap:anywhere}
.info-row:last-child{border:0}
.score-stats{display:flex;align-items:center;gap:13px;padding:10px 0 16px}
.score-value{font-family:var(--serif);font-size:38px;line-height:1;letter-spacing:-.03em;font-weight:500;color:var(--accent);font-variant-numeric:tabular-nums}
.score-sub{font-size:11px;color:var(--muted);line-height:1.7}
.stars{display:flex;flex-wrap:wrap;gap:6px;margin-top:10px}
.star{border:1px solid var(--line-strong);border-radius:7px;padding:7px 9px;color:var(--muted);background:transparent;font-size:18px;line-height:1}
.star.active{background:rgba(200,164,106,.12);color:var(--accent);border-color:rgba(200,164,106,.55)}
.rate{margin-top:15px;color:var(--muted);font-size:12px}
.rating-form{display:flex;flex-wrap:wrap;gap:12px;align-items:end;margin-top:18px}
.rating-form label{font-size:11px;color:var(--muted);display:flex;flex-direction:column;gap:8px}
.rating-form select{border:1px solid var(--line);border-radius:7px;background:var(--ink);color:var(--paper);padding:9px}
.rating-form .btn{font-size:12px;padding:9px 12px}




.footer{padding:26px 0 40px;color:var(--muted);font-size:11px;line-height:1.8;border-top:1px solid var(--line)}
.footer a{color:var(--paper-dim)}
.loading{padding:90px 30px;text-align:center;min-height:70vh;color:var(--muted);font-size:13px}
.loading h1{font-family:var(--serif);font-size:36px;font-weight:500;color:var(--paper)}
.loading .btn{margin-top:12px}
@media(max-width:1100px){.hero-grid{grid-template-columns:minmax(180px,240px) minmax(0,1fr);gap:34px}.content{grid-template-columns:minmax(0,1fr) 290px;gap:22px}.section{padding:23px 22px}}
@media(max-width:900px){.container{width:calc(100% - 36px)}.hero{min-height:0}.hero-grid{grid-template-columns:1fr;gap:24px;align-items:start;min-height:0;padding:28px 0 40px}.poster{width:180px}.hero h1{font-size:42px}.content{grid-template-columns:1fr;gap:16px;padding:24px 0 52px}.section{padding:22px 20px}.facts-sidebar .section{padding:22px 20px}.breadcrumb{height:54px;gap:8px}}
@media(max-width:600px){.container{width:calc(100% - 28px)}.breadcrumb{height:50px;font-size:11px;gap:7px}.poster{width:148px}.hero-grid{gap:20px;padding:24px 0 34px}.hero h1{font-size:34px;letter-spacing:-.02em}.en{font-size:13px;margin-bottom:14px}.meta{gap:7px 10px;font-size:11px}.genre{font-size:10px;padding:4px 8px}.hero-intro{display:block;font-size:13px;line-height:1.8;overflow:visible;margin:16px 0 18px;max-width:none}.cta{gap:7px}.btn{min-height:40px;padding:9px 11px;font-size:11px}.banner-state{font-size:10px}.content{padding-top:20px}.section{padding:20px 16px;border-radius:12px;margin-bottom:12px}.section h2{font-size:21px}.section-heading{align-items:flex-start}.cast-more{font-size:11px}.credit{flex-basis:140px;padding:0}.recommend{gap:11px;padding:10px}.recommend img{width:62px}.score-value{font-size:34px}}
@media(prefers-reduced-motion:reduce){*,*:before,*:after{transition:none!important}.detail-content{transition:none}}
</style>
