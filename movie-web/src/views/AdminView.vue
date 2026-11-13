<script setup lang="ts">
import {ref,watch,onMounted} from 'vue'
import {useRouter} from 'vue-router'
import M9Sidebar from '../components/M9Sidebar.vue'
import AdminDashboard from '../components/dashboard/AdminDashboard.vue'
import AdminImports from '../components/AdminImports.vue'
import AdminQuality from '../components/AdminQuality.vue'
import AdminAIAnalysis from '../components/AdminAIAnalysis.vue'
import AdminImportAudit from '../components/AdminImportAudit.vue'
const router=useRouter(),expanded=ref(localStorage.getItem('mi2_nav_expanded')==='true')
type Tab='overview'|'catalog'|'users'|'recommend'|'ai'|'health'|'audit'|'settings'
const tabs:{id:Tab,label:string}[]=[{id:'overview',label:'数据驾驶舱'},{id:'catalog',label:'电影库治理'},{id:'users',label:'用户与行为'},{id:'recommend',label:'推荐效果'},{id:'ai',label:'AI 服务分析'},{id:'health',label:'系统运行'},{id:'audit',label:'操作审计'},{id:'settings',label:'全局设置'}]
const tab=ref<Tab>('overview'),busy=ref(true),error=ref(''),dashboard=ref<InstanceType<typeof AdminDashboard>>()
const aiReport=ref<InstanceType<typeof AdminAIAnalysis>>(),importAudit=ref<InstanceType<typeof AdminImportAudit>>()
const catalog=ref<any[]>([]),users=ref<any[]>([]),events=ref<any[]>([]),health=ref<any>(null),feedback=ref<any>(null)
const eventsError=ref('')
async function loadEvents(){eventsError.value='';try{events.value=(await api('events')).items||[]}catch(e){eventsError.value=(e as Error).message}}
async function api(path:string){const r=await fetch('/api/admin/'+path,{credentials:'same-origin'});if(r.status===403){router.replace('/home');throw new Error('需要管理员权限。')}if(!r.ok)throw new Error(r.status===401?'登录已失效，请重新登录。':'管理数据暂时不可用。');return (await r.json()).data}
async function load(){
  busy.value=true;error.value=''
  try{
    if(tab.value==='overview')return
    else if(tab.value==='catalog')catalog.value=(await api('catalog')).items||[]
    else if(tab.value==='users')users.value=(await api('users')).items||[]
    else if(tab.value==='audit')await loadEvents()
    else if(tab.value==='health')health.value=await api('health')
    else if(tab.value==='recommend')feedback.value=await api('recommendation-feedback?days=30&k=10')
    else if(tab.value==='ai')return
  }catch(e){error.value=(e as Error).message}finally{busy.value=false}
}
onMounted(load);watch(tab,load)
function refresh(){if(tab.value==='overview'){dashboard.value?.refresh();return}if(tab.value==='ai'){aiReport.value?.refresh();return}if(tab.value==='audit'){importAudit.value?.refresh();loadEvents();return}load()}
function when(v:string|null|undefined){if(!v)return '—';const d=new Date(v);return isNaN(d.getTime())?'—':d.toLocaleString('zh-CN',{hour12:false})}
</script>
<template>
<div class="admin-shell" :style="{'--nav-width':expanded?'280px':'52.5px'}">
  <M9Sidebar @expanded-change="expanded=$event"/>
  <main class="admin-page">
    <header class="admin-head"><div><h1>数据驾驶舱</h1><p>电影资产 · 用户行为 · 智能服务</p></div><button class="ghost" type="button" :disabled="busy" @click="refresh">刷新数据</button></header>
    <nav class="admin-tabs" aria-label="管理分区"><button v-for="t in tabs" :key="t.id" type="button" :class="{on:tab===t.id}" :aria-pressed="tab===t.id" @click="tab=t.id">{{t.label}}</button></nav>
    <p v-if="busy" class="admin-state">正在读取管理数据…</p>
    <p v-else-if="error" class="admin-state error" role="alert">{{error}}</p>
    <template v-else>
      <AdminDashboard v-if="tab==='overview'" ref="dashboard"/>
      <section v-else-if="tab==='catalog'" class="panel">
        <AdminQuality/><AdminImports/><h2>电影库治理 <small>{{catalog.length}} 部在架影片</small></h2>
        <div class="tablewrap"><table><thead><tr><th>影片</th><th>年份</th><th>评分</th><th>类型</th><th>制作国家</th><th>演职员</th><th>媒体</th></tr></thead><tbody>
          <tr v-for="m in catalog" :key="m.movie_id"><td>{{m.title}}</td><td>{{m.year||'—'}}</td><td>{{m.rating??'—'}}</td><td>{{m.genres.join(' / ')||'—'}}</td><td>{{m.countries.join(' / ')||'—'}}</td><td>{{m.credits}}</td><td>{{m.assets}}</td></tr>
        </tbody></table></div>
      </section>
      <section v-else-if="tab==='users'" class="panel">
        <h2>用户与行为 <small>{{users.length}} 个账号</small></h2>
        <div class="tablewrap"><table><thead><tr><th>账号</th><th>状态</th><th>状态行</th><th>已看</th><th>最近事件</th><th>注册时间</th></tr></thead><tbody>
          <tr v-for="u in users" :key="u.id"><td>{{u.username}}</td><td>{{u.status}}</td><td>{{u.states}}</td><td>{{u.watched}}</td><td>{{when(u.last_event)}}</td><td>{{when(u.created_at)}}</td></tr>
        </tbody></table></div>
      </section>
      <section v-else-if="tab==='audit'" class="panel">
        <AdminImportAudit ref="importAudit"/><h2>用户行为审计 <small>最近 {{events.length}} 条行为事件</small></h2>
        <p v-if="eventsError" class="admin-state error" role="alert">{{eventsError}}</p>
        <div v-else class="tablewrap"><table><thead><tr><th>时间</th><th>账号</th><th>影片</th><th>行为</th><th>动作</th></tr></thead><tbody>
          <tr v-for="(e,i) in events" :key="i"><td>{{when(e.at)}}</td><td>{{e.user}}</td><td>{{e.title}}</td><td>{{e.kind||'—'}}</td><td>{{e.action||'—'}}</td></tr>
        </tbody></table></div>
        <p class="note">只展示事件类型与对象，不返回事件载荷、评分明细或任何私人内容。</p>
      </section>
      <section v-else-if="tab==='health'&&health" class="panel">
        <h2>系统运行</h2>
        <div class="rows"><div><span>数据库时间</span><b>{{when(health.db_time)}}</b></div><div><span>影片总数</span><b>{{health.movies}}</b></div><div><span>M7 助手库</span><b>{{health.m7_configured?'已配置':'未配置'}}</b></div><div><span>当前管理员</span><b>{{health.admin}}</b></div><div><span>目录 schema</span><b>{{health.config?.schema||'—'}}</b></div><div><span>行为 schema</span><b>{{health.config?.activity_schema||'—'}}</b></div></div>
      </section>
      <section v-else-if="tab==='ai'" class="panel">
        <AdminAIAnalysis ref="aiReport"/>
      </section>
      <section v-else-if="tab==='recommend'" class="panel">
        <h2>推荐效果 <small>过去 30 天 · 曝光与点击真实计数</small></h2>
        <div v-if="busy" class="empty">正在读取曝光数据…</div>
        <template v-else-if="feedback">
          <div class="kpis">
            <div><span>曝光</span><strong>{{feedback.impressions}}</strong></div>
            <div><span>点击</span><strong>{{feedback.clicks}}</strong></div>
            <div><span>CTR</span><strong>{{feedback.ctr==null?'—':(feedback.ctr*100).toFixed(2)+'%'}}</strong></div>
          </div>
          <div v-if="feedback.channels.length" class="tablewrap"><table><thead><tr><th>频道</th><th>曝光</th><th>点击</th><th>CTR</th></tr></thead><tbody><tr v-for="c in feedback.channels" :key="c.channel"><td>{{c.channel}}</td><td>{{c.impressions}}</td><td>{{c.clicks}}</td><td>{{c.ctr==null?'—':(c.ctr*100).toFixed(2)+'%'}}</td></tr></tbody></table></div>
          <p v-else class="empty">过去 30 天还没有曝光记录；访问推荐频道后会自动采集。</p>
          <h3 class="sub">排序指标 <small>K={{feedback.ranking.k}} · 当前偏好重合度（非转化归因）</small></h3>
          <p v-if="feedback.ranking.status==='OK'" class="metric-line">Precision {{feedback.ranking.precision_at_k?.toFixed(3)}} · Recall {{feedback.ranking.recall_at_k?.toFixed(3)}} · NDCG {{feedback.ranking.ndcg_at_k?.toFixed(3)}}<span>（{{feedback.ranking.users}} 个用户 / {{feedback.ranking.batches}} 次榜单）</span></p>
          <p v-else class="empty">样本不足，暂不计算排序指标（需要曝光用户同时具备已看/收藏/评分≥4 的真值）。</p>
          <p class="note">仅统计新口径：卡片至少50%进入视口持续1秒，或用户主动点击。CTR 按同批曝光归属去重点击；排序指标逐榜单计算并包含零命中，不代表推荐带来的转化。数据保留 180 天。</p>
        </template>
        <p v-else class="empty">推荐效果数据暂时不可用。</p>
      </section>
      <section v-else-if="tab==='settings'" class="panel">
        <h2>全局设置</h2>
        <p class="empty">当前没有可在线修改的全局配置。</p>
        <p class="note">管理员白名单由服务端环境变量 <code>MI2_ADMIN_USERS</code> 控制；目录/行为 schema、数据库连接等均为只读环境配置，修改需要重新部署。</p>
      </section>
    </template>
  </main>
</div>
</template>
<style scoped>
.admin-shell{min-height:100dvh;background:#080a13;color:#f2eff9;font-family:Inter,"Microsoft YaHei",sans-serif}
.admin-page{margin-left:var(--nav-width);padding:20px clamp(18px,2.3vw,38px) 22px;transition:margin-left .25s ease;min-width:0;box-sizing:border-box;container:admin-content / inline-size;display:flex;flex-direction:column;min-height:100dvh}
.admin-head{flex-shrink:0;display:flex;justify-content:space-between;align-items:flex-end;gap:16px;margin-bottom:14px}
.admin-head p{color:#a79bb9;font-size:11px;margin:6px 0 0}
.admin-head h1{margin:0;font-size:26px;letter-spacing:-.6px}
.ghost{border:1px solid #ffffff2b;background:#1c1a29;color:#e8e1f6;border-radius:9px;padding:9px 14px;font:inherit;font-size:12px;cursor:pointer}
.ghost:hover{background:#272338}
.admin-tabs{flex-shrink:0;display:flex;flex-wrap:wrap;gap:6px;border-bottom:1px solid #ffffff12;padding-bottom:10px;margin-bottom:14px}
.admin-tabs button{border:1px solid transparent;background:transparent;color:#a9a0bd;border-radius:8px;padding:8px 12px;font:inherit;font-size:12.5px;cursor:pointer}
.admin-tabs button:hover{background:#ffffff08;color:#e6dcf7}
.admin-tabs button.on{background:#2f2547;border-color:#9a7ad066;color:#f2e9ff}
.admin-state{padding:60px 10px;text-align:center;color:#a79ec0}
.admin-state.error{color:#ffb3c7}
.panel{border:1px solid #ffffff12;border-radius:16px;background:linear-gradient(160deg,#151222,#0f0e18);padding:20px 22px}
.panel h2{font-size:17px;margin:0 0 16px}
.panel h2 small{margin-left:8px;color:#8d859f;font-size:11.5px;font-weight:400}
.kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));gap:12px;margin-bottom:20px}
.kpis article{border:1px solid #ffffff10;border-radius:12px;background:#191627;padding:14px 15px}
.kpis small{display:block;color:#8d859f;font-size:11px}
.kpis strong{display:block;margin-top:7px;font-size:22px;letter-spacing:-.5px}
.split{display:grid;grid-template-columns:1fr 1fr;gap:20px}
.rows div{display:flex;justify-content:space-between;gap:18px;padding:10px 0;border-bottom:1px solid #ffffff0c;font-size:12.5px}
.rows span{color:#9189a6}.rows b{color:#eee5fb;font-weight:620}
.note{color:#827a96;font-size:11.5px;line-height:1.8;margin:12px 0 0}
.empty{color:#b3a9c8;font-size:13px;margin:0 0 6px}
.tablewrap{overflow:auto;border:1px solid #ffffff0e;border-radius:12px}
table{width:100%;border-collapse:collapse;font-size:12.5px;min-width:640px}
th{position:sticky;top:0;background:#1a1728;text-align:left;color:#928aa6;font-weight:600;font-size:11px;letter-spacing:.3px;padding:10px 12px;border-bottom:1px solid #ffffff12;white-space:nowrap}
td{padding:9px 12px;border-bottom:1px solid #ffffff0a;color:#ddd5ea;vertical-align:top}
tr:last-child td{border-bottom:0}
tr:hover td{background:#ffffff05}
code{font-family:ui-monospace,Consolas,monospace;font-size:11px;color:#cfc0ee;background:#241f33;padding:1px 5px;border-radius:4px}
.admin-shell :focus-visible{outline:2px solid #c4a8ef;outline-offset:3px}
@media(max-width:1000px){.split{grid-template-columns:1fr}}
.kpis{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:12px;margin:0 0 18px}.kpis div{border:1px solid #ffffff10;border-radius:10px;padding:14px 16px;background:#0d0b14}.kpis span{display:block;color:#9b90ad;font-size:11px;margin-bottom:6px}.kpis strong{font-size:24px;font-weight:750;letter-spacing:-.02em}.sub{font-size:13px;margin:22px 0 10px}.sub small{color:#8d859f;font-size:11px;font-weight:400;margin-left:6px}.metric-line{font-size:14px;color:#e8e1f2;margin:0}.metric-line span{color:#948aa6;font-size:12px;margin-left:8px}</style>
