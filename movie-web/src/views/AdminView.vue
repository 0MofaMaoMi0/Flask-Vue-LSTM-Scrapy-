<script setup lang="ts">
import {computed, ref} from 'vue'
import {useRoute, useRouter} from 'vue-router'
import AppSidebar from '../components/AppSidebar.vue'
import AdminAIAnalysis from '../components/AdminAIAnalysis.vue'
import AdminDashboard from '../components/dashboard/AdminDashboard.vue'
import AdminCatalogPanel from '../components/admin/AdminCatalogPanel.vue'
import AdminUsersPanel from '../components/admin/ops/AdminUsersPanel.vue'
import AdminRecommendPanel from '../components/admin/ops/AdminRecommendPanel.vue'
import AdminHealthPanel from '../components/admin/ops/AdminHealthPanel.vue'
import AdminAuditPanel from '../components/admin/ops/AdminAuditPanel.vue'
import AdminSettingsPanel from '../components/admin/ops/AdminSettingsPanel.vue'
import CountryQuickPanel from '../components/admin/CountryQuickPanel.vue'
import CountryDeepDive from '../components/admin/CountryDeepDive.vue'
import CountryCompare from '../components/admin/CountryCompare.vue'
import {
  parseAdminQuery, sameQuery, serializeAdminQuery, toggleCompare,
  type AdminQuery, type AdminTab,
} from '../components/admin/adminContext'
import {PREF_KEYS, parseDays, readPref} from '../components/admin/ops/opsApi'
import '../components/admin/admin-ui.css'

const router = useRouter(), route = useRoute()
const expanded = ref(localStorage.getItem('mi2_nav_expanded') === 'true')
const tabs: {id: AdminTab, label: string}[] = [
  {id: 'overview', label: '数据驾驶舱'}, {id: 'catalog', label: '电影库治理'},
  {id: 'users', label: '用户与行为'}, {id: 'recommend', label: '推荐效果'},
  {id: 'ai', label: 'AI 服务分析'}, {id: 'health', label: '系统运行'},
  {id: 'audit', label: '操作审计'}, {id: 'settings', label: '全局设置'},
]
const query = computed<AdminQuery>(() => {
  const parsed = parseAdminQuery(route.query as Record<string, unknown>)
  if (route.query.days == null || String(route.query.days).trim() === '') {
    const saved = readPref(PREF_KEYS.defaultDays)
    if (saved) parsed.days = parseDays(saved, parsed.days)
  }
  return parsed
})
const tab = computed(() => query.value.tab)
const view = computed(() => query.value.view)
const filters = computed(() => ({
  genre: query.value.genre, decade: query.value.decade,
  yearFrom: query.value.yearFrom, yearTo: query.value.yearTo, minVotes: query.value.minVotes,
}))
const compare = computed(() => query.value.compare)
const country = computed(() => query.value.country)
const overviewReload = ref(0)
const catalogPanel = ref<InstanceType<typeof AdminCatalogPanel>>()
const usersPanel = ref<InstanceType<typeof AdminUsersPanel>>()
const recommendPanel = ref<InstanceType<typeof AdminRecommendPanel>>()
const healthPanel = ref<InstanceType<typeof AdminHealthPanel>>()
const auditPanel = ref<InstanceType<typeof AdminAuditPanel>>()
const dashboard = ref<InstanceType<typeof AdminDashboard>>()
const quickPanel = ref<InstanceType<typeof CountryQuickPanel>>()
const aiPanel = ref<{refresh?: () => void} | null>(null)
const settingsPanel = ref<{refresh?: () => void} | null>(null)
const panelBindings = computed(() => ({days: query.value.days}))
const panelListeners = {
  days: setDays,
  navigate: selectTabFromEvent,
  'open-movie': openMovie,
}

function update(patch: Partial<AdminQuery>, replace = false) {
  const next = { ...query.value, ...patch }
  if (sameQuery(next, query.value)) return
  const target = { path: '/admin', query: serializeAdminQuery(next) }
  if (replace) router.replace(target)
  else router.push(target)
}
function selectTab(next: AdminTab) {
  update({ tab: next }, false)
}
function selectTabFromEvent(next: string) {
  if (tabs.some(item => item.id === next)) selectTab(next as AdminTab)
}
function changeCountry(iso2: string) {
  update({ country: iso2, view: 'quick' }, true)
}
function closeQuick() {
  update({ country: '', view: 'quick' }, true)
}
function openMovie(id: number) {
  router.push({ path: '/movies', query: { from: 'admin' }, hash: '#movie/' + id })
}
function toggleCountry(code: string) {
  const compareList = toggleCompare(query.value.compare, code)
  update({ compare: compareList }, true)
}
function onFilters(patch: Partial<AdminQuery>) {
  update({ ...patch, detailOffset: 0 }, true)
}
function setDays(value: number) {
  if (value === 7 || value === 30 || value === 90) update({ days: value })
}
function resetFilters() {
  update({ genre: '', decade: '', yearFrom: null, yearTo: null, minVotes: 50, detailOffset: 0 }, true)
}
function refresh() {
  if (tab.value === 'overview') {
    dashboard.value?.refresh()
    quickPanel.value?.refresh()
    overviewReload.value++
    return
  }
  const targets: Record<string, {refresh?: () => void} | undefined> = {
    catalog: catalogPanel.value, users: usersPanel.value, recommend: recommendPanel.value,
    health: healthPanel.value, audit: auditPanel.value, ai: aiPanel.value ?? undefined,
    settings: settingsPanel.value ?? undefined,
  }
  targets[tab.value]?.refresh?.()
}
const activeFilterChips = computed(() => {
  const parts: string[] = []
  if (query.value.genre) parts.push('类型：' + query.value.genre)
  if (query.value.decade) parts.push('年代：' + query.value.decade)
  if (query.value.yearFrom != null) parts.push('年份 ≥' + query.value.yearFrom)
  if (query.value.yearTo != null) parts.push('年份 ≤' + query.value.yearTo)
  if (query.value.minVotes !== 50) parts.push('票数门槛 ≥' + query.value.minVotes)
  return parts
})
</script>

<template>
  <div class="admin-shell" :style="{'--nav-width': expanded ? '280px' : '52.5px'}">
    <AppSidebar @expanded-change="expanded = $event"/>
    <main class="admin-page">
      <header class="admin-head">
        <div><h1>数据驾驶舱</h1><p>电影资产 · 用户行为 · 智能服务</p></div>
        <button class="ghost" type="button" @click="refresh">刷新数据</button>
      </header>
      <nav class="admin-tabs" aria-label="管理分区">
        <button v-for="t in tabs" :key="t.id" type="button" :class="{on: tab === t.id}" :aria-pressed="tab === t.id" @click="selectTab(t.id)">{{ t.label }}</button>
      </nav>

      <div v-if="tab === 'overview' && view === 'quick' && (country || compare.length || activeFilterChips.length)" class="ctx-bar" aria-label="当前分析上下文">
        <span class="ctx-label">分析上下文</span>
        <button v-if="country" type="button" class="ctx-chip" @click="changeCountry(country)">国家：{{ country }}</button>
        <span v-for="code in compare" :key="code" class="ctx-chip">对比：{{ code }}<button type="button" :aria-label="'移除 ' + code" @click="toggleCountry(code)">✕</button></span>
        <span v-for="part in activeFilterChips" :key="part" class="ctx-chip muted">{{ part }}</span>
        <button v-if="compare.length >= 2" type="button" class="ctx-link" @click="update({view: 'compare'})">打开三国比较 →</button>
        <button v-if="country" type="button" class="ctx-link" @click="update({view: 'deep'})">进入国家专题 →</button>
        <button v-if="activeFilterChips.length" type="button" class="ctx-link" @click="resetFilters">清除筛选</button>
      </div>

      <AdminDashboard
        v-if="tab === 'overview' && view === 'quick'"
        ref="dashboard"
        :days="query.days"
        :country="country"
        @navigate="selectTabFromEvent"
        @select-country="changeCountry"
        @days="setDays"/>

      <CountryDeepDive
        v-else-if="tab === 'overview' && view === 'deep' && country"
        :key="'deep-' + country + '-' + overviewReload"
        :country="country"
        :filters="filters"
        :compare="compare"
        :detail-offset="query.detailOffset"
        @back="update({view: 'quick', country: '', detailOffset: 0}, true)"
        @filters="onFilters"
        @update:detail-offset="update({detailOffset: $event}, true)"
        @compare="toggleCountry"
        @compare-view="update({view: 'compare'})"
        @open-movie="openMovie"
        @quality="selectTab('catalog')"/>

      <CountryCompare
        v-else-if="tab === 'overview' && view === 'compare'"
        :key="'compare-' + compare.join(',') + '-' + overviewReload"
        :compare="compare"
        :filters="filters"
        @back="update({view: 'quick'})"
        @country="update({country: $event, view: 'deep'})"
        @add="toggleCountry"
        @remove="toggleCountry"
        @filters="onFilters"
        @open-movie="openMovie"/>

      <section v-else-if="tab === 'overview'" class="admin-state">
        <p>请先从世界地图选择一个国家，或打开三国比较。</p>
        <button type="button" class="ghost" @click="update({view: 'quick'})">返回总览</button>
      </section>

      <AdminCatalogPanel
        v-else-if="tab === 'catalog'"
        ref="catalogPanel"
        :country="country"
        :country-label="country"
        @open-movie="openMovie"/>
      <AdminUsersPanel v-else-if="tab === 'users'" ref="usersPanel" v-bind="panelBindings" v-on="panelListeners"/>
      <AdminRecommendPanel v-else-if="tab === 'recommend'" ref="recommendPanel" v-bind="panelBindings" v-on="panelListeners"/>
      <AdminHealthPanel v-else-if="tab === 'health'" ref="healthPanel" v-bind="panelBindings" v-on="panelListeners"/>
      <AdminAuditPanel v-else-if="tab === 'audit'" ref="auditPanel" v-bind="panelBindings" v-on="panelListeners"/>
      <AdminSettingsPanel v-else-if="tab === 'settings'" ref="settingsPanel" v-bind="panelBindings" v-on="panelListeners"/>
      <section v-else-if="tab === 'ai'" class="panel">
        <AdminAIAnalysis ref="aiPanel" v-bind="panelBindings" v-on="panelListeners"/>
      </section>

      <CountryQuickPanel
        v-if="tab === 'overview' && view === 'quick' && country"
        ref="quickPanel"
        :iso2="country"
        :filters="filters"
        :compare="compare"
        @close="closeQuick"
        @deep="update({view: 'deep', country: $event})"
        @compare="toggleCountry"
        @all-movies="update({tab: 'catalog', country: $event, view: 'quick'})"
        @quality="selectTab('catalog')"
        @open-movie="openMovie"/>
    </main>
  </div>
</template>

<style scoped>
.admin-shell{min-height:100dvh;background:var(--mi-bg);color:var(--mi-text);font-family:var(--sans)}
.admin-page{margin-left:var(--nav-width);padding:24px clamp(20px,2.6vw,40px) 32px;transition:margin-left .2s ease;min-width:0;box-sizing:border-box;container:admin-content / inline-size;display:flex;flex-direction:column;min-height:100dvh}
.admin-head{flex-shrink:0;display:flex;justify-content:space-between;align-items:flex-end;gap:16px;margin-bottom:16px}
.admin-head p{color:var(--mi-text-secondary);font-size:12px;margin:6px 0 0}
.admin-head h1{margin:0;font-family:var(--serif);font-size:30px;font-weight:500;letter-spacing:.01em;color:var(--mi-text)}
.ghost{border:1px solid var(--mi-border-strong);background:transparent;color:var(--mi-text-soft);border-radius:8px;padding:8px 14px;font:inherit;font-size:12px;cursor:pointer;transition:background-color .18s ease,border-color .18s ease,color .18s ease}
.ghost:hover{background:var(--mi-surface-hover);border-color:var(--mi-border-strong);color:var(--mi-text)}
.admin-tabs{flex-shrink:0;display:flex;flex-wrap:wrap;gap:4px;border-bottom:1px solid var(--mi-border);padding-bottom:8px;margin-bottom:20px}
.admin-tabs button{border:1px solid transparent;background:transparent;color:var(--mi-text-muted);border-radius:6px;padding:7px 12px;font:inherit;font-size:12.5px;cursor:pointer;transition:background-color .18s ease,color .18s ease}
.admin-tabs button:hover{background:var(--mi-surface-hover);color:var(--mi-text)}
.admin-tabs button.on{background:var(--mi-surface);border-color:var(--mi-border-strong);color:var(--mi-text);box-shadow:var(--mi-shadow-sm)}
.admin-state{padding:56px 10px;text-align:center;color:var(--mi-text-muted)}
.admin-state .ghost{margin-top:12px}
.admin-state.error{color:var(--mi-negative)}
.ctx-bar{flex-shrink:0;display:flex;flex-wrap:wrap;align-items:center;gap:7px;margin:-6px 0 14px;padding:8px 10px;border:1px solid var(--mi-border);border-radius:9px;background:var(--mi-surface-warm)}
.ctx-label{font-size:10px;letter-spacing:.08em;text-transform:uppercase;color:var(--mi-text-subtle);margin-right:2px}
.ctx-chip{display:inline-flex;align-items:center;gap:5px;border:1px solid var(--mi-badge-border);background:var(--mi-badge-bg);color:var(--mi-badge-text);border-radius:999px;padding:2px 9px;font-size:11px;cursor:default;font:inherit}
button.ctx-chip{cursor:pointer}
button.ctx-chip:hover{border-color:var(--mi-accent);color:var(--mi-text)}
.ctx-chip.muted{border-color:var(--mi-border);background:transparent;color:var(--mi-text-muted)}
.ctx-chip button{border:0;background:transparent;color:inherit;font:inherit;line-height:1;cursor:pointer;padding:0}
.ctx-link{border:0;background:transparent;color:var(--mi-accent);font:inherit;font-size:11px;cursor:pointer;padding:2px 4px}
.ctx-link:hover{color:var(--mi-text);text-decoration:underline;text-underline-offset:2px}
.panel{border:1px solid var(--mi-border);border-radius:14px;background:var(--mi-surface);padding:20px 22px;box-shadow:var(--mi-shadow-sm)}
.panel h2{font-family:var(--serif);font-size:18px;font-weight:500;margin:0 0 14px;color:var(--mi-text)}
.panel h2 small{margin-left:8px;color:var(--mi-text-muted);font-size:11.5px;font-weight:400}
.kpis{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:12px;margin:0 0 18px}
.kpis div{border:1px solid var(--mi-border);border-radius:10px;padding:14px 16px;background:var(--mi-surface-raised)}
.kpis span{display:block;color:var(--mi-text-muted);font-size:11px;margin-bottom:6px}
.kpis strong{font-size:24px;font-weight:500;letter-spacing:-.01em;font-variant-numeric:tabular-nums;color:var(--mi-text)}
.sub{font-size:13px;margin:22px 0 10px;font-weight:500;color:var(--mi-text)}
.sub small{color:var(--mi-text-muted);font-size:11px;font-weight:400;margin-left:6px}
.metric-line{font-size:13px;color:var(--mi-text-soft);margin:0}
.metric-line span{color:var(--mi-text-muted);font-size:12px;margin-left:8px}
.split{display:grid;grid-template-columns:1fr 1fr;gap:20px}
.rows div{display:flex;justify-content:space-between;gap:18px;padding:10px 0;border-bottom:1px solid var(--mi-border-subtle);font-size:12.5px}
.rows span{color:var(--mi-text-muted)}.rows b{color:var(--mi-text-ivory);font-weight:500}
.note{color:var(--mi-text-subtle);font-size:11.5px;line-height:1.8;margin:12px 0 0}
.empty{color:var(--mi-text-secondary);font-size:13px;margin:0 0 6px}
.tablewrap{overflow:auto;border:1px solid var(--mi-border);border-radius:10px}
table{width:100%;border-collapse:collapse;font-size:12.5px;min-width:640px;font-variant-numeric:tabular-nums}
th{position:sticky;top:0;background:var(--mi-surface-raised);text-align:left;color:var(--mi-text-muted);font-weight:500;font-size:11px;padding:10px 12px;border-bottom:1px solid var(--mi-border);white-space:nowrap}
td{padding:9px 12px;border-bottom:1px solid var(--mi-border-subtle);color:var(--mi-text-soft);vertical-align:top}
tr:last-child td{border-bottom:0}
tr:hover td{background:var(--mi-chip-hover)}
code{font-family:ui-monospace,Consolas,monospace;font-size:11px;color:var(--mi-accent-text);background:var(--mi-accent-soft);padding:1px 5px;border-radius:4px}
.admin-shell :focus-visible{outline:2px solid var(--mi-accent);outline-offset:3px}
:deep(.cinema-dashboard){--panel:var(--mi-surface);--ink:var(--mi-text);--soft:var(--mi-text-secondary)}
:deep(.cinema-dashboard .screen-panel){background:var(--mi-surface);border-color:var(--mi-border)}
:deep(.cinema-dashboard .summary-strip){border-color:var(--mi-border)}
:deep(.cinema-dashboard .summary-strip article){border-color:var(--mi-border)}
:deep(.cinema-dashboard .periods){background:var(--mi-surface-raised);border-color:var(--mi-border)}
:deep(.cinema-dashboard .periods button[aria-pressed=true]){background:var(--mi-surface);color:var(--mi-text);box-shadow:var(--mi-shadow-sm)}
@media(max-width:1000px){.split{grid-template-columns:1fr}}
@media(max-width:700px){.admin-page{padding:18px 14px 24px}.admin-head h1{font-size:26px}.admin-head{align-items:flex-start}.panel{padding:16px}.admin-tabs{gap:2px}.admin-tabs button{padding:6px 9px;font-size:12px}}
</style>