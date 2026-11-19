<script setup lang="ts">
import {ref,computed,watch,onBeforeUnmount,nextTick} from 'vue'
import {useRoute,useRouter} from 'vue-router'
import M9Sidebar from '../components/M9Sidebar.vue'
import {usePosterGrid} from '../composables/usePosterGrid'
import MovieDetailView from './MovieDetailView.vue'
import MyMoviesView from './MyMoviesView.vue'
import {movieArtwork} from '../services/movieArtwork'
const route=useRoute(),router=useRouter(),expanded=ref(localStorage.getItem('mi2_nav_expanded')==='true')
const items=ref<any[]>([]),genres=ref<any[]>([]),behavior=ref<Record<string,any>>({}),busy=ref(false),error=ref(''),notice=ref(''),failures=ref<Record<string,boolean>>({}),pending=ref<Record<string,boolean>>({})
const keyword=ref(''),genre=ref(''),yearFrom=ref(''),yearTo=ref(''),sort=ref('updated'),mode=ref('grid'),state=ref(''),hasMore=ref(false),loadingMore=ref(false),moreError=ref(''),drawer=ref(false),closeButton=ref<HTMLButtonElement>(),filterButton=ref<HTMLButtonElement>(),sentinel=ref<HTMLElement>()
const draftFrom=ref(''),draftTo=ref(''),draftGenre=ref(''),draftState=ref(''),pageSize=24
const quickGenres=computed(()=>['科幻','剧情','犯罪','悬疑','冒险'].map(name=>genres.value.find(g=>g.name_zh===name)).filter(Boolean))
const visible=computed(()=>items.value.filter(m=>!state.value||behavior.value[String(m.movie_id)]?.[state.value]))
const {gridRef,gridStyle}=usePosterGrid(()=>visible.value.length)
const count=computed(()=>[genre.value,yearFrom.value,yearTo.value,state.value].filter(Boolean).length)
let controller:AbortController|undefined,moreController:AbortController|undefined,serial=0,loadedSig='',observer:IntersectionObserver|undefined
async function api(path:string,signal?:AbortSignal){const r=await fetch('/api/catalog/'+path,{credentials:'same-origin',signal});let body:any=null;try{body=await r.json()}catch{body=null};if(!r.ok)throw new Error(r.status===401?'登录已失效，请重新登录。':r.status===400?'筛选条件无效，请检查年份。':'电影库暂时不可用，请重试。');if(!body||body.code!=='OK'||body.data==null)throw new Error('电影库响应异常，请重试。');return body.data}
function params(off:number){const p=new URLSearchParams({limit:String(pageSize),offset:String(off),sort:sort.value});for(const [k,v] of Object.entries({keyword:keyword.value.trim(),genre_id:genre.value,year_from:yearFrom.value,year_to:yearTo.value,state:state.value}))if(v)p.set(k,v);return p}
function signature(){return JSON.stringify([keyword.value.trim(),genre.value,yearFrom.value,yearTo.value,sort.value,state.value])}
async function load(){controller?.abort();moreController?.abort();controller=new AbortController();const signal=controller.signal,id=++serial;busy.value=true;error.value='';moreError.value='';loadingMore.value=false;try{
 const [data,g,b]=await Promise.all([api('movies?'+params(0),signal),api('genres',signal),api('behavior',signal)])
 const rows=data.items||[]
 if(id===serial){items.value=rows;genres.value=g.items;behavior.value=b.state;hasMore.value=rows.length===pageSize;loadedSig=signature()}
 }catch(e){if(!signal.aborted&&id===serial){error.value=(e as Error).message;items.value=[]}}finally{if(id===serial){busy.value=false;await nextTick();observeSentinel()}}}
async function loadMore(){if(busy.value||loadingMore.value||!hasMore.value)return
 loadingMore.value=true;moreError.value='';moreController=new AbortController()
 try{const data=await api('movies?'+params(items.value.length),moreController.signal)
  const rows=data.items||[],seen=new Set(items.value.map((m:any)=>m.movie_id))
  items.value=[...items.value,...rows.filter((m:any)=>!seen.has(m.movie_id))]
  hasMore.value=rows.length===pageSize
 }catch(e){if(!moreController.signal.aborted)moreError.value='后续影片没有取回来，可以重试。'}
 finally{loadingMore.value=false;await nextTick();observeSentinel()}}
function observeSentinel(){observer?.disconnect();if(!sentinel.value||!hasMore.value||busy.value||loadingMore.value)return
 observer=new IntersectionObserver(entries=>{if(entries.some(entry=>entry.isIntersecting))loadMore()},{rootMargin:'640px 0px'})
 observer.observe(sentinel.value)}
function sync(){const q=route.query;keyword.value=String(q.q||'');genre.value=String(q.genre||'');yearFrom.value=String(q.year_from||'');yearTo.value=String(q.year_to||'');sort.value=['updated','release','id'].includes(String(q.sort))?String(q.sort):'updated';mode.value=q.view==='list'?'list':'grid';state.value=['favorite','want','watched'].includes(String(q.state))?String(q.state):'';if(route.hash.startsWith('#movie/')||route.hash==='#behavior')return;if(items.value.length&&signature()===loadedSig){nextTick(restoreScroll);return}load()}
watch(()=>route.fullPath,sync,{immediate:true})
function apply(){const query:Record<string,string>={};for(const [k,v] of Object.entries({q:keyword.value.trim(),genre:genre.value,year_from:yearFrom.value,year_to:yearTo.value,sort:sort.value,view:mode.value,state:state.value}))if(v)query[k]=v;router.push({path:'/movies',query})}
function reset(){keyword.value='';genre.value='';yearFrom.value='';yearTo.value='';state.value='';sort.value='updated';apply()}
function selectGenre(id:string){genre.value=id;apply()}
async function openFilters(){draftFrom.value=yearFrom.value;draftTo.value=yearTo.value;draftGenre.value=genre.value;draftState.value=state.value;drawer.value=true;await nextTick();closeButton.value?.focus()}
function closeFilters(){drawer.value=false;filterButton.value?.focus()}
function filter(){yearFrom.value=draftFrom.value;yearTo.value=draftTo.value;genre.value=draftGenre.value;state.value=draftState.value;closeFilters();apply()}
function focusTrap(e:KeyboardEvent){if(e.key==='Escape'){closeFilters();return}if(e.key!=='Tab')return;const nodes=Array.from((e.currentTarget as HTMLElement).querySelectorAll<HTMLElement>('button,input,select'));const first=nodes[0],last=nodes[nodes.length-1];if(e.shiftKey&&document.activeElement===first){e.preventDefault();last?.focus()}else if(!e.shiftKey&&document.activeElement===last){e.preventDefault();first?.focus()}}
function posterOf(m:any){return m.poster||movieArtwork[String(m.movie_id)]?.poster||''}
function posterError(e:Event,m:any){const img=e.target as HTMLImageElement;if(m.poster_fallback&&img.getAttribute('src')!==m.poster_fallback){img.src=m.poster_fallback}else{img.style.visibility='hidden'}}
function detail(id:number){sessionStorage.setItem('mi2_library_scroll',String(scrollY));router.push({path:'/movies',query:{...route.query,from:'library'},hash:'#movie/'+id})}
async function act(id:number,kind:string){if(pending.value[id])return;pending.value[id]=true;notice.value='';try{const c=await fetch('/api/v1/auth/csrf',{credentials:'same-origin'});let cb:any=null;try{cb=await c.json()}catch{cb=null};const token=cb?.data?.csrf_token;if(!c.ok||!token||(cb?.code!==0&&cb?.code!=='OK'))throw new Error('登录已失效，请重新登录。');const undo=!!behavior.value[id]?.[kind];const r=await fetch('/api/catalog/behavior',{method:'POST',credentials:'same-origin',headers:{'Content-Type':'application/json','X-CSRF-Token':token},body:JSON.stringify({event_id:crypto.randomUUID(),movie_id:id,kind,action:undo?'undo':'set',value:null,time:new Date().toISOString(),source:'USER_ACTION'})});let rb:any=null;try{rb=await r.json()}catch{rb=null};if(!r.ok||rb?.code!=='OK')throw new Error(r.status===409?'行为状态冲突，请到详情页调整。':'保存未确认，请刷新状态后重试。');behavior.value=(await api('behavior')).state;notice.value='已保存';window.dispatchEvent(new Event('mi2-behavior-changed'))}catch(e){notice.value=(e as Error).message}finally{pending.value[id]=false}}
function restoreScroll(){const saved=sessionStorage.getItem('mi2_library_scroll');if(!saved)return;window.scrollTo(0,Number(saved));sessionStorage.removeItem('mi2_library_scroll')}
watch(busy,async value=>{if(!value&&!route.hash){await nextTick();restoreScroll()}})
const onBehavior=()=>{if(state.value&&!route.hash)load();else api('behavior').then(b=>behavior.value=b.state).catch(()=>{notice.value='个人状态刷新失败，请重试。'})}
window.addEventListener('mi2-behavior-changed',onBehavior)
onBeforeUnmount(()=>{controller?.abort();moreController?.abort();observer?.disconnect();window.removeEventListener('mi2-behavior-changed',onBehavior)})
</script>
<template>
<MovieDetailView v-if="route.hash.startsWith('#movie/')"/><MyMoviesView v-else-if="route.hash==='#behavior'"/><div v-else class="library-shell" :style="{'--nav-width':expanded?'280px':'52.5px'}"><M9Sidebar @expanded-change="expanded=$event"/><main class="page">
<h1 class="visually-hidden">全部电影</h1>
<div class="rail">
<form class="filters" aria-label="电影筛选和排序" @submit.prevent="apply()">
<label>电影名称<input v-model="keyword" type="search" placeholder="搜索电影名称" maxlength="255"/></label>
<label>类型<select v-model="genre"><option value="">全部类型</option><option v-for="g in genres" :key="g.genre_id" :value="String(g.genre_id)">{{g.name_zh}}</option></select></label>
<label>起始年份<input v-model="yearFrom" type="number" min="1800" max="2199" placeholder="不限"/></label>
<label>截止年份<input v-model="yearTo" type="number" min="1800" max="2199" placeholder="不限"/></label>
<label>我的状态<select v-model="state"><option value="">全部</option><option value="favorite">收藏</option><option value="want">想看</option><option value="watched">已看</option></select></label>
<label>排序<select v-model="sort"><option value="updated">最近更新</option><option value="release">上映日期</option><option value="id">目录顺序</option></select></label>
<div class="viewfield"><span class="field-label">浏览</span><div class="viewtoggle" role="group" aria-label="浏览方式"><button type="button" :aria-pressed="mode==='grid'" aria-label="网格视图" title="网格" @click="mode='grid';apply()"><svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" aria-hidden="true"><rect x="4" y="4" width="7" height="7" rx="1"/><rect x="13" y="4" width="7" height="7" rx="1"/><rect x="4" y="13" width="7" height="7" rx="1"/><rect x="13" y="13" width="7" height="7" rx="1"/></svg></button><button type="button" :aria-pressed="mode==='list'" aria-label="列表视图" title="列表" @click="mode='list';apply()"><svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" aria-hidden="true"><path d="M4 6h16M4 12h16M4 18h16"/></svg></button></div></div>
<button class="apply" type="submit">应用筛选</button>
<button class="resetlink" type="button" @click="reset">重置</button>
</form>
</div><div class="active-tags"><span v-if="keyword" class="tag">片名：{{keyword}}</span><span v-if="genre" class="tag">{{genres.find(g=>String(g.genre_id)===genre)?.name_zh}}</span><span v-if="yearFrom||yearTo" class="tag">{{yearFrom||'不限'}} — {{yearTo||'不限'}}</span><span v-if="state" class="tag">我的状态：{{state==='favorite'?'收藏':state==='want'?'想看':'已看'}}</span></div>
<div v-if="busy" class="status" role="status">正在寻找故事…</div><div v-else-if="error" class="status" role="alert"><p>{{error}}</p><button class="filterbtn" @click="load">重试</button></div>
<template v-else><section v-if="visible.length" ref="gridRef" :style="gridStyle" class="grid" :class="{listmode:mode==='list'}" aria-label="电影海报"><article v-for="m in visible" :key="m.movie_id" class="film"><div class="film-poster"><img v-if="posterOf(m)&&!failures[m.movie_id]" class="poster-art" :src="posterOf(m)" @error="posterError($event,m)" :alt="m.title+'海报'" loading="lazy"/><span v-else class="poster-name">{{m.title}}</span><button class="poster-click" :aria-label="'查看'+m.title+'详情'" @click="detail(m.movie_id)"></button></div><div class="film-info"><h2 class="film-name"><button @click="detail(m.movie_id)">{{m.title}}</button></h2><div class="film-meta">{{m.release_year||'年份未知'}} · {{m.genres?.map((g:any)=>g.name_zh).join(' / ')||'类型待补充'}}</div><div class="film-source">{{m.ratings?.length?((m.ratings[0].source==='douban'?'豆瓣':m.ratings[0].source.toUpperCase())+' '+m.ratings[0].score_value+' / '+m.ratings[0].score_scale):'暂无来源评分'}}</div><div class="tags-line"><span v-for="kind in ['favorite','want','watched']" v-show="behavior[m.movie_id]?.[kind]" :key="kind" class="behavior">{{kind==='favorite'?'已收藏':kind==='want'?'想看':'已看'}}</span></div><p class="list-desc">{{m.overview||'暂无故事简介。'}}</p><div class="list-actions"><button @click="detail(m.movie_id)">查看详情</button><button :disabled="pending[m.movie_id]" @click="act(m.movie_id,'want')">{{behavior[m.movie_id]?.want?'取消想看':'想看'}}</button></div></div></article></section><div v-else class="empty" style="display:block"><strong>没有找到匹配的电影</strong><span>试试其他片名或调整筛选条件。</span><br/><button @click="reset">清除全部条件</button></div></template>
<div v-if="visible.length" ref="sentinel" class="scroll-sentinel" aria-hidden="true"></div>
<p v-if="visible.length&&loadingMore" class="loadmore" role="status">正在载入更多…</p>
<p v-else-if="visible.length&&moreError" class="loadmore err" role="alert">{{moreError}}<button @click="loadMore">重试</button></p>
<p v-else-if="visible.length&&!hasMore" class="loadmore end" role="status">已加载全部 {{items.length}} 部影片</p>
<p class="notefoot">电影资料来自已授权静态 TMDB 副本。This product uses the TMDB API but is not endorsed or certified by TMDB. <a href="https://www.themoviedb.org/" target="_blank" rel="noopener">TMDB</a></p><p role="status" aria-live="polite">{{notice}}</p>
</main><div v-if="drawer" class="backdrop show" @click.self="closeFilters"><aside class="drawer" role="dialog" aria-modal="true" aria-labelledby="filterTitle" @keydown="focusTrap"><div class="drawer-head"><h2 id="filterTitle">筛选电影</h2><button ref="closeButton" class="close" aria-label="关闭筛选" @click="closeFilters">×</button></div><div class="group"><div class="group-title">上映年份</div><label>起始年份<input v-model="draftFrom" type="number" min="1800" max="2199"/></label><label>结束年份<input v-model="draftTo" type="number" min="1800" max="2199"/></label></div><div class="group"><label class="group-title">电影类型<select v-model="draftGenre"><option value="">全部类型</option><option v-for="g in genres" :key="g.genre_id" :value="String(g.genre_id)">{{g.name_zh}}</option></select></label></div><div class="group"><label class="group-title">我的状态（当前结果页）<select v-model="draftState"><option value="">全部</option><option value="favorite">收藏</option><option value="want">想看</option><option value="watched">已看</option></select></label></div><div class="drawer-actions"><button @click="draftFrom='';draftTo='';draftGenre='';draftState=''">重置</button><button class="apply" @click="filter">查看结果</button></div></aside></div></div>
</template>
<style scoped>
:global(body:has(.library-shell)){margin:0;background:#0c0d0f;color:#fff}
.library-shell{--ink:#0c0d0f;--ink-soft:#141519;--paper:#fff;--paper-dim:rgba(255,255,255,.82);--muted:rgba(255,255,255,.58);--border:rgba(241,236,226,.14);--border-strong:rgba(241,236,226,.26);--accent:#c8a46a;--serif:'Noto Serif SC','Source Han Serif SC','Songti SC',Georgia,'Times New Roman',serif;--sans:Inter,'PingFang SC','Microsoft YaHei',sans-serif;position:relative;z-index:1;min-height:100vh;background:var(--ink);color:var(--paper);font-family:var(--sans)}
.library-shell *{box-sizing:border-box}
.library-shell button,.library-shell input,.library-shell select{font:inherit}
.library-shell button{cursor:pointer}
.library-shell a{color:inherit}
.library-shell :focus-visible{outline:2px solid var(--accent);outline-offset:3px}
.library-shell ::selection{background:var(--accent);color:var(--ink)}
.page{margin-left:var(--nav-width,52.5px);width:calc(100% - var(--nav-width,52.5px));max-width:none;min-width:0;container:movie-library / inline-size;padding:40px clamp(22px,3.2vw,56px) 72px;margin-right:auto}
.viewtoggle{display:flex;gap:6px;flex:none}
.viewtoggle button{font-size:12px;padding:9px 14px;border:1px solid var(--border);border-radius:8px;color:var(--muted);background:transparent;transition:background-color .16s,border-color .16s,color .16s}
.viewtoggle button:hover{color:var(--paper);border-color:var(--border-strong)}
.viewtoggle button[aria-pressed="true"]{color:var(--paper);border-color:var(--border-strong);background:rgba(255,255,255,.07)}
.searchbar{display:flex;gap:16px;align-items:center;margin-bottom:14px}
.searchwrap{display:flex;align-items:center;gap:13px;width:min(650px,100%);height:48px;background:var(--ink-soft);border:1px solid var(--border);border-radius:10px;padding:5px 5px 5px 15px}
.searchwrap:focus-within{border-color:var(--border-strong)}
.searchwrap svg{flex:none;color:var(--muted)}
.searchwrap input{border:0;outline:0;min-width:0;flex:1;background:transparent;color:var(--paper);font-size:13px}
.searchwrap input::placeholder{color:var(--muted)}
.searchwrap button{height:37px;border:1px solid var(--paper);border-radius:7px;background:transparent;color:var(--paper);font-weight:500;padding:0 20px;font-size:12px;transition:background-color .16s,border-color .16s}
.searchwrap button:hover{background:rgba(255,255,255,.08)}
.chips{display:flex;align-items:center;flex-wrap:wrap;gap:7px}
.chip{border:1px solid var(--border);border-radius:8px;background:transparent;color:var(--muted);padding:8px 12px;font-size:12px;transition:background-color .16s,border-color .16s,color .16s}
.chip:hover{color:var(--paper);border-color:var(--border-strong)}
.chip[aria-pressed="true"]{background:rgba(255,255,255,.07);border-color:var(--border-strong);color:var(--paper)}
.right-tools{display:flex;gap:9px;align-items:center;margin-left:auto}
.filterbtn{background:transparent;border:1px solid var(--border-strong);color:var(--paper);border-radius:8px;font-size:12px;padding:9px 13px;white-space:nowrap}
.filterbtn span{font-size:11px;color:var(--muted);margin-left:5px;font-variant-numeric:tabular-nums}
.sortby{background:transparent;border:1px solid var(--border);border-radius:8px;color:var(--paper);font-size:12px;height:38px;padding:0 12px;min-width:152px}
.sortby option{background:var(--ink-soft);color:var(--paper)}
.resetbtn{background:none;border:0;color:var(--muted);font-size:12px;padding:8px 4px;white-space:nowrap}
.resetbtn:hover{color:var(--paper)}
.active-tags{display:flex;gap:7px;flex-wrap:wrap;margin:0 0 10px}
.active-tags:empty{display:none}
.tag{border-radius:999px;border:1px solid var(--border);background:transparent;color:var(--paper-dim);font-size:11px;padding:5px 10px}
.tag button{border:0;background:none;color:var(--paper-dim);padding:0 0 0 8px}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(min(100%,172px),1fr));gap:28px 16px;align-items:start;margin-top:18px}
.film{min-width:0}
.film-poster{width:100%;aspect-ratio:2/3;border-radius:14px;overflow:hidden;border:1px solid rgba(255,255,255,.16);background:var(--ink-soft);position:relative;box-shadow:0 5px 17px #0003;transition:transform .22s ease,box-shadow .22s ease,border-color .22s ease;isolation:isolate}
.film-poster:hover,.film-poster:focus-within{border-color:rgba(241,236,226,.38);transform:translateY(-4px);box-shadow:0 16px 35px #0009}
.poster-art{width:100%;height:100%;object-fit:cover}
.film-poster:after{content:"";position:absolute;inset:52% 0 0;background:linear-gradient(0deg,rgba(12,13,15,.9) 0%,rgba(12,13,15,.42) 54%,transparent 100%);pointer-events:none;z-index:1}
.poster-name{position:absolute;left:8%;right:8%;bottom:18%;z-index:2;font:500 clamp(18px,1.8vw,27px)/1.08 var(--serif);letter-spacing:-.01em;color:var(--paper);text-shadow:0 3px 15px #05060ac9;word-break:normal;overflow-wrap:normal}

.poster-click{position:absolute;inset:0;border:0;background:transparent;z-index:3;width:100%;height:100%;cursor:pointer}
.poster-click:focus-visible{outline:3px solid var(--accent);outline-offset:-5px}
.film-info{padding:12px 3px 0}
.film-name{font-size:14px;font-weight:500;line-height:1.45;white-space:nowrap;text-overflow:ellipsis;overflow:hidden;margin:0}
.film-name button{border:0;background:none;padding:0;color:var(--paper);text-align:left;max-width:100%;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.film-meta{color:var(--muted);font-size:11px;line-height:1.6;margin-top:5px}
.film-source{color:var(--muted);font-size:10px;margin-top:7px;font-variant-numeric:tabular-nums}
.film-source.demo{color:var(--muted)}
.list-desc{display:none;color:var(--muted);font-size:12px;line-height:1.75;margin-top:9px}
.list-actions{display:none;gap:8px;margin-top:15px}
.list-actions button{border-radius:8px;border:1px solid var(--border);background:transparent;color:var(--paper);padding:9px 13px;font-size:12px}
.list-actions button:first-child{border-color:var(--paper)}
.tags-line{display:flex;gap:5px;flex-wrap:wrap;margin-top:7px}
.behavior{font-size:9px;color:var(--accent);border-radius:999px;border:1px solid rgba(200,164,106,.34);padding:3px 7px;line-height:1}
.grid.listmode{display:flex;flex-direction:column;gap:10px;margin-top:18px}
.listmode .film{display:flex;align-items:stretch;width:100%;gap:18px;padding:12px;border:1px solid var(--border);border-radius:12px;background:var(--ink-soft)}
.listmode .film-poster{width:104px;flex:none}
.listmode .film-info{flex:1;min-width:0;padding:3px 0}
.listmode .film-name{font-size:17px}
.listmode .list-desc{display:block}
.listmode .list-actions{display:flex}
.listmode .poster-name{font-size:12px}

.listmode .film-poster:hover{transform:none}
.empty{display:none;padding:72px 24px;text-align:center;border:1px dashed var(--border-strong);border-radius:14px;color:var(--muted)}
.empty strong{font-family:var(--serif);font-size:22px;font-weight:500;display:block;margin-bottom:9px;color:var(--paper)}
.empty button{padding:10px 14px;color:var(--paper);border:1px solid var(--border-strong);background:transparent;border-radius:8px;margin-top:15px}
.notefoot{margin:39px 0 0;color:var(--muted);font-size:11px;line-height:1.8;border-top:1px solid var(--border);padding-top:17px}
.notefoot strong{color:var(--paper-dim);font-weight:500}
.notefoot a{color:var(--paper-dim)}
.status{padding:70px 20px;text-align:center;color:var(--muted)}
.status .filterbtn{margin-top:16px}
.backdrop{display:none;position:fixed;inset:0;background:rgba(4,5,7,.72);z-index:60;backdrop-filter:blur(4px)}
.backdrop.show{display:block}
.drawer{position:absolute;right:0;top:0;bottom:0;width:min(400px,95vw);overflow-y:auto;background:var(--ink-soft);border-left:1px solid var(--border);box-shadow:-16px 0 48px #0008;padding:26px}
.drawer-head{display:flex;align-items:center;justify-content:space-between;gap:16px;margin-bottom:28px}
.drawer-head h2{margin:0;font-family:var(--serif);font-size:26px;font-weight:500}
.close{width:37px;height:37px;border-radius:50%;background:transparent;color:var(--paper);border:1px solid var(--border-strong);font-size:21px}
.group{margin:0 0 25px}
.group-title{color:var(--paper-dim);font-size:12px;font-weight:500;margin-bottom:11px}
.drawer label{display:flex;flex-direction:column;gap:9px;margin:12px 0;font-size:12px;color:var(--muted)}
.drawer input,.drawer select{width:100%;background:var(--ink);color:var(--paper);border:1px solid var(--border);border-radius:8px;padding:11px}
.options{display:flex;flex-wrap:wrap;gap:8px}
.options button{background:transparent;border:1px solid var(--border);border-radius:8px;color:var(--muted);padding:10px 12px;font-size:12px}
.options button[aria-pressed="true"]{border-color:var(--border-strong);background:rgba(255,255,255,.07);color:var(--paper)}
.drawer-actions{display:flex;gap:10px;border-top:1px solid var(--border);padding-top:18px}
.drawer-actions button{flex:1;border-radius:8px;padding:12px;border:1px solid var(--border);background:transparent;color:var(--paper)}
.drawer-actions .apply{border-color:var(--paper);color:var(--paper)}
.scroll-sentinel{height:1px;margin-top:6px}
.loadmore{display:flex;align-items:center;justify-content:center;gap:12px;margin:26px 0 4px;min-height:20px;color:var(--muted);font-size:12px;line-height:1.6;font-variant-numeric:tabular-nums}
.loadmore button{border:1px solid var(--border);background:transparent;color:var(--paper);border-radius:8px;padding:7px 14px;font-size:12px}
.loadmore button:hover{border-color:var(--border-strong);background:rgba(255,255,255,.06)}
.loadmore.err{color:var(--paper-dim)}
.loadmore.end{letter-spacing:.01em}
@media(max-width:1120px){.grid{grid-template-columns:repeat(auto-fill,minmax(150px,1fr));gap:24px 14px}}
@media(max-width:760px){.page{padding:26px 16px 56px}.viewtoggle{align-self:flex-start}.searchbar{align-items:flex-start;flex-direction:column;gap:9px}.searchwrap{height:46px}.right-tools{width:100%;margin-left:0}.sortby{flex:1;min-width:0}.grid{grid-template-columns:repeat(2,minmax(0,1fr));gap:24px 12px}.film-name{font-size:13px}.listmode .film{gap:12px;padding:10px}.listmode .film-poster{width:88px}.drawer{padding:22px 18px}.notefoot{margin-top:30px}}
@media(prefers-reduced-motion:reduce){.film-poster,.viewtoggle button,.chip,.searchwrap button{transition:none}.film-poster:hover,.film-poster:focus-within{transform:none;box-shadow:0 5px 17px #0003}}

/* 排版重构：把三行分散的控制件收成一条贴顶索引栏，内容上移；卡片按目录编号与等宽数字对齐 */
.page{max-width:none;padding:32px clamp(20px,3vw,48px) 64px}
.rail{position:sticky;top:0;z-index:30;display:flex;align-items:center;gap:16px;padding:12px 0 14px;background:var(--ink);border-bottom:1px solid var(--border);margin-bottom:0}
.searchbar{flex:0 1 300px;margin:0;display:block}
.searchwrap{height:40px;padding:4px 4px 4px 12px;gap:10px;border-radius:8px;width:100%}
.searchwrap input{font-size:13px}
.searchwrap button{height:32px;padding:0 16px;border-radius:6px;font-size:12px}
.chips{flex:1 1 auto;min-width:0;flex-wrap:nowrap;overflow-x:auto;gap:4px;padding:2px 0;scrollbar-width:none}
.chips::-webkit-scrollbar{display:none}
.chip{border-color:transparent;border-radius:6px;padding:8px 12px;white-space:nowrap;font-size:12px}
.chip:hover{background:rgba(255,255,255,.04);border-color:transparent}
.chip[aria-pressed="true"]{background:rgba(255,255,255,.08);border-color:rgba(255,255,255,.16)}
.right-tools{gap:8px;margin-left:auto;flex:none}
.filterbtn,.sortby{height:36px;border-radius:8px;font-size:12px}
.filterbtn{padding:0 12px}
.sortby{min-width:132px;padding:0 10px}
.resetbtn{padding:8px 2px}
.viewtoggle{gap:0;border:1px solid var(--border);border-radius:8px;overflow:hidden;margin-left:4px}
.viewtoggle button{border:0;border-radius:0;padding:8px 12px;font-size:11.5px;background:transparent}
.viewtoggle button+button{border-left:1px solid var(--border)}
.viewtoggle button[aria-pressed="true"]{background:rgba(255,255,255,.08)}
.active-tags{margin:14px 0 0}
.grid{margin-top:22px;gap:32px 20px;grid-template-columns:repeat(auto-fill,minmax(min(100%,180px),1fr))}
.film-info{padding:14px 2px 0}
.film-name{font-size:14px;line-height:1.4}
.film-meta{margin-top:4px;font-size:11.5px}
.film-source{margin-top:6px;font-size:11px}
.loadmore{margin:30px 0 0}
.notefoot{margin-top:32px}
@media(max-width:1120px){.grid{grid-template-columns:repeat(auto-fill,minmax(150px,1fr));gap:28px 16px}.rail{gap:12px}.searchbar{flex-basis:260px}}
/* 去掉页头两块后：内容从顶部起排，标题只保留给读屏与文档结构 */
.page{padding-top:24px}
.visually-hidden{position:absolute;width:1px;height:1px;padding:0;margin:-1px;overflow:hidden;clip:rect(0 0 0 0);white-space:nowrap;border:0}
@media(max-width:760px){.page{padding-top:16px}}
@media(max-width:760px){
 .page{padding:20px 16px 48px}
 .rail{position:static;flex-direction:column;align-items:stretch;gap:10px;padding:12px 0 14px}
 .searchbar{flex:1 1 auto}
 .searchwrap{height:42px}
 .chips{flex:none;width:100%;padding-bottom:2px}
 .right-tools{width:100%;margin-left:0;flex-wrap:wrap}
 .sortby{flex:1;min-width:0}
 .viewtoggle{margin-left:0}
 .grid{grid-template-columns:repeat(2,minmax(0,1fr));gap:26px 12px;margin-top:18px}
 .film-info{padding-top:11px}
 .loadmore{margin-top:26px}
}

/* Adapt to the space remaining after the sidebar, including live resizes. */
@container movie-library (max-width:900px){
 .rail{flex-wrap:wrap;position:static}
 .searchbar{flex:1 1 240px;min-width:0}
 .chips{order:3;flex:1 0 100%;width:100%}
 .right-tools{max-width:100%;flex-wrap:wrap;margin-left:0}
}
@container movie-library (max-width:540px){
 .rail{flex-direction:column;align-items:stretch}
 .searchbar{flex:0 0 auto}
 .chips{order:0;flex:0 0 auto}
 .grid:not(.listmode){grid-template-columns:repeat(2,minmax(0,1fr));gap:26px 12px}
 .right-tools{width:100%}
 .film-info{min-width:0}
}
@container movie-library (max-width:230px){.grid:not(.listmode){grid-template-columns:minmax(0,1fr)}}
@media(max-width:760px){.page{margin-left:52.5px;width:calc(100% - 52.5px)}}

/* 与推荐页筛选栏统一外观：34px 控件、6px 圆角、同节奏间距与发丝线 */
.rail{padding:10px 0 14px;gap:10px}
.searchwrap{height:34px;padding:2px 2px 2px 10px;border-radius:6px}
.searchwrap svg{width:16px;height:16px}
.searchwrap input{font-size:12px}
.searchwrap button{height:28px;padding:0 12px;border-radius:4px;font-size:11.5px}
.chip{height:34px;padding:0 10px;font-size:12px}
.filterbtn,.sortby{height:34px;border-radius:6px;font-size:12px}
.sortby{min-width:124px;padding:0 8px}
.viewtoggle{border-radius:6px}
.viewtoggle button{height:34px;padding:0 10px;font-size:11.5px;display:grid;place-items:center}
.right-tools{gap:6px}.sortby{background:var(--ink-soft);border-color:var(--border)}
.sortby:focus{border-color:var(--border-strong);outline:none}

/* 与推荐页一致的筛选交互：字段表单 + 应用筛选 */
.rail{display:block;padding:10px 0 12px}
.filters{display:grid;grid-template-columns:repeat(auto-fit,minmax(118px,1fr));gap:8px 10px;align-items:end;margin:0}
.filters label{display:flex;flex-direction:column;gap:4px;color:var(--muted);font-size:10.5px;min-width:0}
.filters input,.filters select{height:34px;width:100%;background:var(--ink-soft);border:1px solid var(--border);border-radius:6px;padding:4px 8px;color:var(--paper);font-size:12px}
.filters input::placeholder{color:var(--muted)}
.filters input:focus,.filters select:focus{border-color:var(--border-strong);outline:none}
.filters .viewfield{display:flex;flex-direction:column;gap:4px}
.filters .field-label{color:var(--muted);font-size:10.5px}
.filters .viewtoggle{margin:0;height:34px}
.filters .viewtoggle button{padding:0 10px}
.filters .apply,.filters .resetlink{height:34px;border-radius:6px;font-size:11.5px}
.filters .apply{background:transparent;border:1px solid var(--paper);color:var(--paper);padding:0 12px}
.filters .apply:hover{background:rgba(255,255,255,.08)}
.filters .resetlink{background:none;border:0;color:var(--muted);padding:0 4px;white-space:nowrap}
.filters .resetlink:hover{color:var(--paper)}
/* 浏览切换：图标分段控件，与表单控件同高 */
.filters .viewtoggle{height:34px;align-self:end;border:1px solid var(--border);border-radius:6px;overflow:hidden;background:var(--ink-soft)}
.filters .viewtoggle button{width:38px;height:100%;padding:0;border:0;background:transparent;color:var(--muted);display:grid;place-items:center}
.filters .viewtoggle button+button{border-left:1px solid var(--border)}
.filters .viewtoggle button:hover{color:var(--paper)}
.filters .viewtoggle button[aria-pressed="true"]{background:rgba(255,255,255,.08);color:var(--paper)}
.filters .viewtoggle button:focus-visible{outline:2px solid var(--accent);outline-offset:-3px}</style>