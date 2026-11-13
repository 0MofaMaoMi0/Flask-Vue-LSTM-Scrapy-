<script setup lang="ts">
import {ref,onMounted,onBeforeUnmount,computed,watch} from 'vue'
import {useRoute} from 'vue-router'
import {useAuthStore} from '../stores/auth'
import AccountMenu from './AccountMenu.vue'
const route=useRoute()
function active(to:string){if(to==='/movies')return route.path==='/movies'&&!route.hash;if(to.includes('#behavior'))return route.path==='/movies'&&route.hash==='#behavior';return route.path===to}
const auth=useAuthStore(),expanded=ref(localStorage.getItem('mi2_nav_expanded')==='true')
const emit=defineEmits<{(e:'expanded-change',value:boolean):void}>()
const counts=ref<{seen:number,favorite:number,want:number}>({seen:0,favorite:0,want:0})
const onMovies=computed(()=>route.path==='/movies'&&route.hash==='#behavior')
const view=computed(()=>String(route.query.view||'globe'))
async function loadCounts(){
  try{const r=await fetch('/api/catalog/behavior',{credentials:'same-origin'});if(!r.ok)return;const s=(await r.json()).data.state||{};const c={seen:0,favorite:0,want:0};for(const v of Object.values(s) as any[]){if(v?.watched)c.seen++;if(v?.favorite)c.favorite++;if(v?.want)c.want++}counts.value=c}catch{}
}
onMounted(()=>{emit('expanded-change',expanded.value);loadCounts();window.addEventListener('mi2-behavior-changed',loadCounts);window.addEventListener('focus',loadCounts)})
onBeforeUnmount(()=>{window.removeEventListener('mi2-behavior-changed',loadCounts);window.removeEventListener('focus',loadCounts)})
watch(()=>route.fullPath,loadCounts)
function toggle(){expanded.value=!expanded.value;localStorage.setItem('mi2_nav_expanded',String(expanded.value));emit('expanded-change',expanded.value)}
const baseLinks=[{to:'/home',label:'首页',icon:'M3 10l9-7 9 7v10H3z M9 20v-7h6v7'}, {to:'/movies',label:'电影库',icon:'M4 3h16v18H4z M4 8h16 M4 16h16 M8 3v18 M16 3v18'}, {to:'/recommendations',label:'推荐频道',icon:'M3 6h18M3 12h18M3 18h18M8 3v6M16 9v6M10 15v6'}, {to:'/ai',label:'AI 探索',icon:'M12 3l2.5 6.5L21 12l-6.5 2.5L12 21l-2.5-6.5L3 12l6.5-2.5z'}, {to:'/movies#behavior',label:'我的电影',icon:'M5 4h14v17l-7-5-7 5z'}]
const links=computed(()=>auth.user?.is_admin?[...baseLinks,{to:'/admin',label:'数据驾驶舱',icon:'M4 20V11 M10 20V5 M16 20v-7 M21 20H3'}]:baseLinks)
const subItems=[
  {view:'globe',label:'世界电影分布',icon:'M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18Zm0 0c3.4 3 3.4 15 0 18M3 12h18'},
  {view:'seen',label:'观看记录',icon:'M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18Zm0 5v5l3.2 2',count:'seen' as const},
  {view:'insights',label:'观影数据',icon:'M4 20V10M10 20V4M16 20v-7M22 20H2'},
  {view:'favorite',label:'收藏清单',icon:'M20 5c-3-3-6-1-8 1-2-2-5-4-8-1-4 4 1 9 8 15 7-6 12-11 8-15z',count:'favorite' as const},
  {view:'want',label:'想看清单',icon:'M6 4h12v17l-6-4.5L6 21z',count:'want' as const}
]
</script>
<template><div class="sidebar-scope" :class="{'is-collapsed':!expanded}"><aside class="sidebar" aria-label="主导航"><div class="sidebar-head"><router-link to="/home" class="sidebar-brand" aria-label="MovieInsight 首页"><strong>Movie<span>Insight</span></strong><small>AI POWERED MOVIE DISCOVERY</small></router-link><button class="sidebar-toggle" :aria-expanded="expanded" :aria-label="expanded?'收起导航':'展开导航'" @click="toggle"><svg width="22" height="22" viewBox="0 0 24 24"><path d="M4 6h16M4 12h16M4 18h16"/></svg></button></div><div class="sidebar-content"><nav class="primary-nav"><router-link v-for="item in links" :key="item.to" :to="item.to" class="nav-item" active-class="route-match" :class="{'is-active':active(item.to)}" :title="item.label" :aria-label="item.label"><span class="nav-icon"><svg viewBox="0 0 24 24"><path :d="item.icon"/></svg></span><span class="item-label">{{item.label}}</span></router-link></nav>
<div v-if="onMovies" class="route-context"><div class="route-context-title"><span>我的电影</span><span class="context-tag">个人空间</span></div><nav class="context-nav" aria-label="我的电影子导航"><router-link v-for="s in subItems" :key="s.label" :to="{path:'/movies',hash:'#behavior',query:{view:s.view}}" class="sub-item" :class="{'is-active':view===s.view}" :aria-current="view===s.view?'page':undefined" :title="s.label" :aria-label="s.label"><span class="nav-icon"><svg viewBox="0 0 24 24"><path :d="s.icon"/></svg></span><span class="item-label">{{s.label}}</span><span v-if="s.count&&counts[s.count]" class="sub-count">{{counts[s.count]}}</span></router-link></nav></div>
</div><div class="sidebar-footer"><AccountMenu :collapsed="!expanded"/></div></aside></div></template>
<style scoped>
/* P7.3 sidebar and P8.4 palette copied from the supplied shell. */
.sidebar{position:fixed;inset:0 auto 0 0;width:var(--expanded);height:100vh;display:flex;flex-direction:column;z-index:30;overflow:hidden;background:linear-gradient(180deg,#151526 0%,#131323 55%,#11111d 100%);border-right:1px solid rgba(203,166,255,.09);box-shadow:8px 0 34px rgba(0,0,0,.18);transition:width .25s ease}
.sidebar-head{min-height:87px;padding:18px 12px 12px 18px;display:flex;align-items:flex-start;gap:8px}.sidebar-brand{display:flex;flex-direction:column;min-width:0;white-space:nowrap;overflow:hidden;padding:3px 0 0 0;flex:1}.sidebar-brand strong{font:800 21px/1.14 Georgia,'Times New Roman',serif;letter-spacing:-.6px;color:#f2ecff}.sidebar-brand strong span{color:#bd92ff}.sidebar-brand small{color:#a49bb8;font-size:8.5px;letter-spacing:1.1px;margin-top:4px}.sidebar-toggle{height:42px;width:42px;min-width:42px;border-radius:12px;display:grid;place-items:center;border:1px solid transparent;background:transparent;color:#c2a8ec;transition:background .2s,border-color .2s,transform .2s}.sidebar-toggle:hover{background:#2a2340;border-color:#76599f}.sidebar-toggle svg{transition:transform .22s ease}.sidebar-toggle:active svg{transform:scale(.88)}
.sidebar-content{flex:1;min-height:0;overflow-y:auto;overflow-x:hidden;scrollbar-width:thin;scrollbar-color:#453454 transparent;padding:0 9px 8px}
.primary-nav,.context-nav{display:flex;flex-direction:column;gap:4px}
.nav-item,.sub-item,.footer-item{display:flex;align-items:center;gap:12px;min-height:43px;padding:10px 12px;background:transparent;border:1px solid transparent;border-radius:13px;text-align:left;white-space:nowrap;color:#c3bdd4;transition:background .16s,color .16s,border-color .16s}
.nav-item{width:100%;font-size:14px;font-weight:550}.nav-item:hover,.sub-item:hover,.footer-item:hover{background:rgba(196,157,255,.07);color:#fff}.nav-item.is-active,.sub-item.is-active{color:#f9f6ff;background:linear-gradient(100deg,#34274eaa,#29223888);border-color:rgba(196,156,255,.16)}
.nav-icon{width:22px;min-width:22px;height:22px;display:grid;place-items:center;color:#bba0ec}.nav-icon svg{display:block;width:20px;height:20px}.nav-item.is-active .nav-icon,.sub-item.is-active .nav-icon{color:#e1c9ff}.item-label{overflow:hidden;text-overflow:ellipsis}
.route-context{margin:17px 0 8px;opacity:1}.route-context-title{font-size:10px;color:#8d82a6;letter-spacing:1px;font-weight:750;display:flex;align-items:center;gap:8px;padding:9px 10px 7px}.context-tag{font-size:9px;letter-spacing:.4px;color:#a996c9;background:#2a2340;border:1px solid #4b3b6b;border-radius:99px;padding:2px 6px;font-weight:600}.sub-item{width:100%;min-height:40px;font-size:12px;gap:10px;padding:9px 10px}.sub-item .nav-icon{width:20px;min-width:20px}.sub-count{margin-left:auto;font-size:11px;font-weight:700;color:#d0b3f3;border-radius:20px;padding:3px 7px;background:#302842}
.sidebar-footer{padding:10px 9px 14px;display:flex;flex-direction:column;gap:5px}.footer-item{width:100%;min-height:42px;font-size:13px}.footer-item.is-active{background:rgba(177,140,244,.12);color:#eee0ff}.nav-icon.profile{background:#3a2b58;border-radius:50%;color:#dfceff}

 .is-collapsed .sidebar{width:var(--collapsed)} .is-collapsed .sidebar-head{min-height:72px;padding:8px 0 10px;justify-content:center} .is-collapsed .sidebar-brand{display:none} .is-collapsed .sidebar-toggle{height:44px;width:42px;min-width:42px;margin:auto;border:1px solid rgba(198,165,255,.72);background:rgba(46,35,72,.28);border-radius:13px}
 .is-collapsed .sidebar-content{padding:0 3px 8px} .is-collapsed .nav-item, .is-collapsed .sub-item, .is-collapsed .footer-item{justify-content:center;gap:0;width:44px;max-width:44px;min-height:43px;padding:10px 0;border-radius:12px;margin:0 auto} .is-collapsed .nav-item .item-label, .is-collapsed .sub-item .item-label, .is-collapsed .footer-item .item-label, .is-collapsed .sub-count{display:none} .is-collapsed .route-context{display:none!important} .is-collapsed .sidebar-footer{padding:8px 3px 12px}

.sidebar-scope{--collapsed:52.5px;--expanded:280px}.sidebar{background:linear-gradient(180deg,#141624 0%,#111320 58%,#0d101c 100%)}.sidebar a{text-decoration:none}.sidebar svg{fill:none;stroke:currentColor;stroke-width:1.6;stroke-linecap:round;stroke-linejoin:round}.sidebar-brand{color:inherit}.sidebar-brand:focus-visible,.nav-item:focus-visible,.sidebar-toggle:focus-visible{outline:2px solid #c2a6f4;outline-offset:-2px}.nav-item.is-active{background:rgba(140,105,190,.19);border-color:rgba(197,163,244,.19)}@media(prefers-reduced-motion:reduce){.sidebar,.nav-item,.sidebar-toggle{transition:none}}
</style>
