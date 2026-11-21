<script setup lang="ts">
/**
 * Page 06 — users and viewing behaviour (WP-D).
 *
 * Read-only. Events, distinct accounts and current states are shown as three
 * separate quantities; nothing here is inferred from another block. Every
 * number comes from `GET /api/admin/users/analytics` (WP-B contract §2).
 */
import {computed, onBeforeUnmount, onMounted, ref, watch} from 'vue'
import DashboardChart from '../../dashboard/DashboardChart.vue'
import {
  DIMENSION_LABELS, EM_DASH, EVENT_KINDS, KIND_LABELS, PREF_DIMENSIONS, PREF_KEYS, SIGNAL_LABELS,
  fmtInt, fmtRatio, fmtScore, fmtUtc, loadUsersAnalytics, opsSignal, parseDays, readPref, statusMeta,
  trendOption, windowRange,
  type Days, type EventKind, type PrefDimension, type ShareRow, type TrendPoint, type UsersAnalytics,
} from './opsApi'
import '../admin-ui.css'

interface AccountRow {
  id: number
  username: string
  status: string
  states: number
  watched: number
  last_event: string | null
  created_at: string | null
}

const props = withDefaults(defineProps<{days?: Days}>(), {days: 30})
const emit = defineEmits<{'open-movie': [id: number]; days: [days: Days]}>()
const DAY_CHOICES: Days[] = [7, 30, 90]

const activeDays = ref<Days>(parseDays(props.days))
const kind = ref<EventKind | ''>('')
const signal = ref<'favorite' | 'want' | 'high_rating'>('favorite')
// The settings tab stores this preference per browser; page 06 honours it.
const basis = ref<'by_user' | 'by_pair'>(readPref(PREF_KEYS.preferenceBasis) === 'by_pair' ? 'by_pair' : 'by_user')

const data = ref<UsersAnalytics | null>(null)
const error = ref('')
const busy = ref(false)
let controller: AbortController | undefined
let ticket = 0

let accountsController: AbortController | undefined
let accountsTicket = 0

const accounts = ref<AccountRow[]>([])
const accountsError = ref('')
const accountsBusy = ref(false)
const accountsOpen = ref(false)
const ACCOUNTS_PAGE = 25
const accountOffset = ref(0)
const pagedAccounts = computed(() => accounts.value.slice(accountOffset.value, accountOffset.value + ACCOUNTS_PAGE))

function stepAccounts(delta: number) {
  const next = accountOffset.value + delta
  if (next < 0 || next >= accounts.value.length) return
  accountOffset.value = next
}

async function load() {
  const current = new AbortController()
  controller?.abort()
  controller = current
  const mine = ++ticket
  busy.value = true
  error.value = ''
  try {
    const result = await loadUsersAnalytics(activeDays.value, kind.value || null, current.signal)
    if (mine === ticket && !current.signal.aborted) data.value = result
  } catch (e) {
    if (current.signal.aborted || mine !== ticket || (e as Error).name === 'AbortError') return
    error.value = (e as Error).message
  } finally {
    if (mine === ticket) busy.value = false
  }
}

async function loadAccounts() {
  const current = new AbortController()
  accountsController?.abort()
  accountsController = current
  const mine = ++accountsTicket
  accountsBusy.value = true
  accountsError.value = ''
  try {
    const response = await fetch('/api/admin/users', {credentials: 'same-origin', signal: opsSignal(current.signal)})
    const body = await response.json().catch(() => null)
    if (mine !== accountsTicket || current.signal.aborted) return
    if (!response.ok || !body || body.code !== 'OK' || !body.data) {
      throw new Error(response.status === 401 ? '登录已失效，请重新登录。' : (response.status === 503 || body?.code === 'DATA_UNAVAILABLE') ? '账号统计来源暂时不可用（DATA_UNAVAILABLE），请稍后重试。' : '账号清单暂时无法读取。')
    }
    accounts.value = Array.isArray(body.data.items) ? body.data.items : []
    if (accountOffset.value >= accounts.value.length) accountOffset.value = 0
  } catch (e) {
    if (current.signal.aborted || mine !== accountsTicket || (e as Error).name === 'AbortError') return
    accountsError.value = (e as Error).message
  } finally {
    if (mine === accountsTicket) accountsBusy.value = false
  }
}

function setDays(value: Days) {
  if (value === activeDays.value) return
  activeDays.value = value
  emit('days', value)
  load()
}

function refresh() {
  load()
  if (accountsOpen.value || accounts.value.length) loadAccounts()
}

function onAccountsToggle(event: Event) {
  const target = event.target as HTMLDetailsElement
  accountsOpen.value = target.open
  if (target.open && !accounts.value.length && !accountsBusy.value) loadAccounts()
}

watch(() => props.days, value => {
  const next = parseDays(value)
  if (next === activeDays.value) return
  activeDays.value = next
  load()
})
watch(kind, load)

onMounted(load)
onBeforeUnmount(() => {
  ++ticket
  ++accountsTicket
  controller?.abort()
  accountsController?.abort()
})
defineExpose({refresh})

const windowLabel = computed(() => {
  if (!data.value) return ''
  const block = data.value.window
  return (block.timezone || 'UTC') + ' · ' + windowRange(block.from, block.to)
})

const overall = computed(() => statusMeta(data.value?.status))
const kindLabel = computed(() => kind.value ? KIND_LABELS[kind.value] : '全部类型')

function points(rows: {date: string; count: number}[] | undefined): TrendPoint[] {
  return (rows || []).map(row => ({date: row.date.slice(5), value: row.count}))
}
function total(rows: {count: number}[] | undefined): number {
  return (rows || []).reduce((sum, row) => sum + (Number.isFinite(row.count) ? row.count : 0), 0)
}
function peak(rows: {count: number}[] | undefined): number | null {
  const nums = (rows || []).map(row => row.count).filter(value => typeof value === 'number' && Number.isFinite(value))
  return nums.length ? Math.max(...nums) : null
}

const registrationPoints = computed(() => points(data.value?.daily.registrations))
const eventPoints = computed(() => points(data.value?.daily.events))
const activePoints = computed(() => points(data.value?.daily.active_accounts))

const registrationOption = computed(() => trendOption('新增注册', registrationPoints.value, 'bar'))
const eventOption = computed(() => trendOption('行为事件', eventPoints.value))
const activeOption = computed(() => trendOption('行为活跃账户', activePoints.value))

function chartRows(list: TrendPoint[]) {
  return list.map(point => ({name: point.date, value: point.value}))
}

const stateCards = computed(() => {
  const states = data.value?.current_states || {}
  const numeric = (value: unknown) => typeof value === 'number' && Number.isFinite(value) ? value : null
  return [
    {key: 'favorite', label: '收藏', value: numeric(states.favorite)},
    {key: 'want', label: '想看', value: numeric(states.want)},
    {key: 'watched', label: '已看', value: numeric(states.watched)},
    {key: 'rating', label: '评分', value: numeric(states.rating)},
    {key: 'not_interested', label: '不感兴趣', value: numeric(states.not_interested)},
  ]
})

const selectedSignal = computed(() => data.value?.preference.signals?.[signal.value])
const signalStatus = computed(() => statusMeta(selectedSignal.value?.status))
const signalSuppressed = computed(() => selectedSignal.value?.status !== 'available')

const dimensionTables = computed(() => {
  const entry = selectedSignal.value
  if (!entry) return []
  return PREF_DIMENSIONS.map((dimension: PrefDimension) => {
    const rows: ShareRow[] = entry[basis.value]?.[dimension] || []
    const max = rows.reduce((top, row) => Math.max(top, row.count || 0), 0)
    return {
      key: dimension,
      label: DIMENSION_LABELS[dimension],
      rows: rows.slice(0, 8),
      max,
      total: rows.length,
    }
  })
})

const boardGroups = computed(() => {
  const boards = data.value?.boards
  return [
    {key: 'events', title: '窗口事件最多', hint: '含设置与撤销的行为事件数，不等于当前收藏人数', rows: (boards?.most_events || []).slice(0, 10)},
    {key: 'favorite', title: '当前收藏人数最多', hint: '当前状态快照，按去重账户数', rows: (boards?.most_favorited || []).slice(0, 10)},
    {key: 'want', title: '当前想看人数最多', hint: '当前状态快照，按去重账户数', rows: (boards?.most_wanted || []).slice(0, 10)},
  ]
})

const definitions = computed(() => {
  const map = data.value?.definition || {}
  return [
    {key: 'registered', label: '注册口径', text: map.registered},
    {key: 'active', label: '活跃账户口径', text: map.active_accounts},
    {key: 'states', label: '当前状态口径', text: map.current_states},
    {key: 'split', label: '三量分离', text: map.events_vs_states},
  ].filter(item => !!item.text)
})

function barWidth(count: number, max: number): string {
  if (!max) return '0%'
  return Math.max(2, Math.min(100, (count / max) * 100)).toFixed(1) + '%'
}
function openMovie(id: number | null | undefined) {
  if (typeof id === 'number' && Number.isInteger(id) && id > 0) emit('open-movie', id)
}
</script>

<template>
  <section class="adm-panel ops-users" aria-labelledby="admin-users-title">
    <h2 id="admin-users-title">用户与行为
      <small>{{ windowLabel || 'UTC 自然日窗口' }}</small>
    </h2>

    <div class="adm-toolbar">
      <div class="adm-field">
        <span>统计窗口</span>
        <div class="adm-seg" role="group" aria-label="统计窗口">
          <button v-for="option in DAY_CHOICES" :key="option" type="button"
                  :aria-pressed="activeDays === option" @click="setDays(option)">近 {{ option }} 天</button>
        </div>
      </div>
      <label class="adm-field">
        <span>行为类型</span>
        <select v-model="kind">
          <option value="">全部类型</option>
          <option v-for="item in EVENT_KINDS" :key="item" :value="item">{{ KIND_LABELS[item] }}</option>
        </select>
      </label>
      <p v-if="data" class="ops-meta">
        <span class="ops-status" :class="'tone-' + overall.tone">{{ overall.label }}</span>
        <span>生成于 {{ fmtUtc(data.as_of) }}</span>
      </p>
    </div>

    <p v-if="error" class="adm-errobox" role="alert">{{ error }}<span v-if="data">（已保留上一份可用快照）</span>
      <button type="button" class="adm-btn ops-inline-btn" @click="refresh">重新读取</button>
    </p>
    <p v-if="busy && !data" class="adm-state" role="status">正在读取用户与行为统计…</p>

    <template v-else-if="data">
      <div class="adm-kpis">
        <div>
          <span>注册总量</span>
          <strong>{{ fmtInt(data.registered?.total) }}</strong>
          <p>其中启用 {{ fmtInt(data.registered?.active) }}；含停用账户，不随窗口变化</p>
        </div>
        <div>
          <span>窗口新增注册</span>
          <strong>{{ fmtInt(data.registered?.new_in_window) }}</strong>
          <p>{{ windowLabel }}</p>
        </div>
        <div>
          <span>行为活跃账户</span>
          <strong>{{ fmtInt(data.sample_size?.active_accounts) }}</strong>
          <p>窗口内有至少一条电影行为事件的去重账户，<b>不是登录 DAU</b></p>
        </div>
        <div>
          <span>窗口行为事件</span>
          <strong>{{ fmtInt(data.sample_size?.events) }}</strong>
          <p>事件条数含设置与撤销；当前筛选：{{ kindLabel }}</p>
        </div>
        <div>
          <span>平均每活跃账户事件</span>
          <strong>{{ data.avg_events_per_active_account?.value == null ? '无活跃账户' : fmtScore(data.avg_events_per_active_account.value, 1) }}</strong>
          <p>事件总数 ÷ 去重活跃账户；与上面的事件筛选一致</p>
        </div>
      </div>

      <div class="ops-states" aria-label="当前状态快照">
        <p class="ops-states-title">当前状态快照
          <span>收藏 / 想看 / 已看 / 评分 / 不感兴趣 · 不随 7/30/90 天窗口变化</span>
        </p>
        <div class="ops-state-grid">
          <div v-for="card in stateCards" :key="card.key">
            <span>{{ card.label }}</span>
            <b>{{ fmtInt(card.value) }}</b>
          </div>
        </div>
        <p class="adm-note">状态数与窗口事件数、去重账户数为三个独立量，不互相推算；统计仅覆盖在架影片。</p>
      </div>

      <h3>每日趋势 <small>UTC 自然日 · 空日期只在查询成功后补 0</small></h3>
      <div class="ops-trends">
        <figure class="ops-trend">
          <figcaption><span>每日新增注册</span><b>{{ fmtInt(total(data.daily?.registrations)) }}</b></figcaption>
          <div class="ops-chart">
            <DashboardChart :option="registrationOption" :rows="chartRows(registrationPoints)"
                            label="每日新增注册趋势" unit="账户"/>
          </div>
        </figure>
        <figure class="ops-trend">
          <figcaption><span>每日行为事件 · {{ kindLabel }}</span><b>{{ fmtInt(total(data.daily?.events)) }}</b></figcaption>
          <div class="ops-chart">
            <DashboardChart :option="eventOption" :rows="chartRows(eventPoints)"
                            label="每日行为事件趋势" unit="事件"/>
          </div>
        </figure>
        <figure class="ops-trend">
          <figcaption><span>每日行为活跃账户</span><b>峰值 {{ fmtInt(peak(data.daily?.active_accounts)) }}</b></figcaption>
          <div class="ops-chart">
            <DashboardChart :option="activeOption" :rows="chartRows(activePoints)"
                            label="每日行为活跃账户趋势" unit="账户"/>
          </div>
        </figure>
      </div>

      <h3>偏好分析 <small>当前状态快照 · 收藏 / 想看 / 高评分分开统计</small></h3>
      <div class="adm-toolbar">
        <div class="adm-field">
          <span>行为信号</span>
          <div class="adm-seg" role="group" aria-label="行为信号">
            <button v-for="item in (['favorite', 'want', 'high_rating'] as const)" :key="item" type="button"
                    :aria-pressed="signal === item" @click="signal = item">{{ SIGNAL_LABELS[item] }}</button>
          </div>
        </div>
        <div class="adm-field">
          <span>统计口径</span>
          <div class="adm-seg" role="group" aria-label="偏好统计口径">
            <button type="button" :aria-pressed="basis === 'by_user'" @click="basis = 'by_user'">按用户去重</button>
            <button type="button" :aria-pressed="basis === 'by_pair'" @click="basis = 'by_pair'">按用户-电影对</button>
          </div>
        </div>
        <p class="ops-meta">
          <span class="ops-status" :class="'tone-' + signalStatus.tone">{{ signalStatus.label }}</span>
          <span>去重账户 {{ fmtInt(selectedSignal?.sample_size?.users) }} · 关联对 {{ fmtInt(selectedSignal?.sample_size?.pairs) }}</span>
        </p>
      </div>

      <div v-if="!selectedSignal" class="adm-empty">该信号暂无数据。</div>
      <div v-else-if="signalSuppressed" class="adm-warnbox" role="status">
        {{ selectedSignal.reason || '去重账户少于公开阈值，细分维度已隐藏。' }}
        不显示细分、也不以 0 填充；其它信号不受影响。
      </div>
      <template v-else>
        <div class="ops-dims">
          <section v-for="table in dimensionTables" :key="table.key" class="ops-dim">
            <h4>{{ table.label }}</h4>
            <p v-if="!table.rows.length" class="adm-empty">该维度暂无可用样本。</p>
            <div v-else class="adm-bars">
              <div v-for="row in table.rows" :key="row.name" class="adm-bar">
                <span :title="row.name">{{ row.name }}</span>
                <i><b :style="{width: barWidth(row.count, table.max)}"/></i>
                <b>{{ row.count }}<small> · {{ fmtRatio(row.share) }}</small></b>
              </div>
            </div>
            <p v-if="table.total > table.rows.length" class="ops-trunc">其余 {{ table.total - table.rows.length }} 项按计数降序省略。</p>
          </section>
        </div>
        <p class="adm-note">
          口径：{{ basis === 'by_user' ? '按用户去重，每个用户在每类别只计 1 次' : '按用户-电影对，每个 (user, movie) 只计 1 次' }}；
          类型为多标签，份额之和可超过 100%。已看不视为喜欢，本区不使用已看信号。
        </p>
      </template>

      <h3>热门互动影片 <small>三个榜单口径不同，不可相加</small></h3>
      <div class="ops-boards">
        <section v-for="group in boardGroups" :key="group.key" class="ops-board">
          <header><h4>{{ group.title }}</h4><span>{{ group.hint }}</span></header>
          <ol v-if="group.rows.length">
            <li v-for="(row, index) in group.rows" :key="row.movie_id">
              <span class="ops-rank">{{ index + 1 }}</span>
              <button type="button" class="ops-movie" :title="row.title" @click="openMovie(row.movie_id)">
                {{ row.title || EM_DASH }}
              </button>
              <b>{{ fmtInt(row.count) }}</b>
            </li>
          </ol>
          <p v-else class="adm-empty">{{ group.key === 'events' ? '该窗口内暂无行为事件。' : '当前没有对应的状态记录。' }}</p>
        </section>
      </div>

      <details v-if="definitions.length" class="ops-disclosure">
        <summary>统计口径与来源</summary>
        <dl class="ops-defs">
          <template v-for="item in definitions" :key="item.key">
            <dt>{{ item.label }}</dt><dd>{{ item.text }}</dd>
          </template>
        </dl>
        <p v-if="data.source" class="adm-note">数据来源：{{ data.source }}</p>
      </details>

      <details class="ops-disclosure ops-accounts" @toggle="onAccountsToggle">
        <summary>只读账号清单 <small>{{ accounts.length ? accounts.length + ' 个账户' : '按需加载' }}</small></summary>
        <p v-if="accountsError" class="adm-errobox" role="alert">{{ accountsError }}<span v-if="accounts.length">（已保留上一份可用快照）</span>
          <button type="button" class="adm-btn ops-inline-btn" @click="loadAccounts">重新读取</button>
        </p>
        <p v-if="accountsBusy && !accounts.length" class="adm-state" role="status">正在读取账号清单…</p>
        <template v-else-if="accounts.length">
          <div class="adm-tablewrap">
            <table>
              <caption class="ops-sr">管理员授权范围内的账号聚合视图</caption>
              <thead>
                <tr><th scope="col">账号</th><th scope="col">状态</th><th scope="col" class="num">状态行</th><th scope="col" class="num">已看</th><th scope="col">最近事件</th><th scope="col">注册时间</th></tr>
              </thead>
              <tbody>
                <tr v-for="row in pagedAccounts" :key="row.id">
                  <td>{{ row.username }}</td>
                  <td>{{ row.status }}</td>
                  <td class="num">{{ row.states }}</td>
                  <td class="num">{{ row.watched }}</td>
                  <td>{{ fmtUtc(row.last_event) }}</td>
                  <td>{{ fmtUtc(row.created_at) }}</td>
                </tr>
              </tbody>
            </table>
          </div>
          <div v-if="accounts.length > ACCOUNTS_PAGE" class="adm-pager">
            <button type="button" class="adm-btn" :disabled="accountOffset === 0 || accountsBusy" @click="stepAccounts(-ACCOUNTS_PAGE)">上一页</button>
            <span>第 {{ accountOffset + 1 }}–{{ accountOffset + pagedAccounts.length }} 个 / 共 {{ fmtInt(accounts.length) }} 个账户</span>
            <button type="button" class="adm-btn" :disabled="accountOffset + ACCOUNTS_PAGE >= accounts.length || accountsBusy" @click="stepAccounts(ACCOUNTS_PAGE)">下一页</button>
          </div>
        </template>
        <p v-else-if="!accountsError" class="adm-empty">当前没有可展示的账户记录。</p>
        <p class="adm-note">仅返回账号聚合字段，不包含行为载荷、评分明细或私人内容。</p>
      </details>
    </template>

    <p v-else class="adm-empty">用户与行为统计暂时不可用，请手动刷新。</p>
  </section>
</template>

<style scoped>
.ops-meta{display:flex;flex-wrap:wrap;align-items:center;gap:8px 12px;margin:0;color:var(--mi-text-muted);font-size:11px}
.ops-status{display:inline-flex;align-items:center;gap:6px;border:1px solid var(--mi-border);border-radius:999px;padding:2px 8px;font-size:10.5px;color:var(--mi-text-secondary);white-space:nowrap}
.ops-status::before{content:"";width:5px;height:5px;border-radius:50%;background:currentColor;opacity:.9}
.ops-status.tone-ok{color:var(--mi-positive);border-color:var(--mi-positive-border)}
.ops-status.tone-warn{color:var(--mi-warning);border-color:var(--mi-accent-border)}
.ops-status.tone-bad{color:var(--mi-negative);border-color:var(--mi-negative-border)}
.ops-status.tone-muted{color:var(--mi-text-secondary)}
.ops-inline-btn{margin-left:10px;padding:3px 9px;font-size:11px}
.ops-states{border:1px solid var(--mi-border);border-radius:10px;padding:12px 14px;margin:0 0 6px;background:var(--mi-surface-warm)}
.ops-states-title{margin:0 0 10px;font-size:11.5px;color:var(--mi-accent-text)}
.ops-states-title span{color:var(--mi-text-muted);margin-left:8px;font-size:11px}
.ops-state-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(96px,1fr));gap:8px}
.ops-state-grid div{display:flex;flex-direction:column;gap:4px;padding:8px 10px;border:1px solid var(--mi-border-subtle);border-radius:8px;background:var(--mi-surface)}
.ops-state-grid span{color:var(--mi-text-secondary);font-size:10.5px}
.ops-state-grid b{font-size:17px;font-weight:500;color:var(--mi-text);font-variant-numeric:tabular-nums}
.ops-trends{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:12px;margin:0 0 4px}
.ops-trend{margin:0;border:1px solid var(--mi-border);border-radius:10px;background:var(--mi-surface-warm);padding:10px 12px 6px;min-width:0}
.ops-trend figcaption{display:flex;align-items:baseline;justify-content:space-between;gap:10px;margin:0 0 6px;font-size:11px;color:var(--mi-text-secondary)}
.ops-trend figcaption b{color:var(--mi-text);font-weight:500;font-size:14px;font-variant-numeric:tabular-nums}
.ops-chart{height:142px;min-width:0}
.ops-dims{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:14px 18px;margin:0 0 4px}
.ops-dim{min-width:0}
.ops-dim h4{margin:0 0 8px;font-size:11.5px;font-weight:500;color:var(--mi-text-soft);border-bottom:1px solid var(--mi-border-subtle);padding-bottom:6px}
.ops-dim .adm-bar small{color:var(--mi-text-muted);font-size:10px;margin-left:2px}
.ops-trunc{margin:8px 0 0;color:var(--mi-text-muted);font-size:10.5px}
.ops-boards{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:14px}
.ops-board{border:1px solid var(--mi-border);border-radius:10px;background:var(--mi-surface-warm);padding:12px 14px;min-width:0}
.ops-board header h4{margin:0 0 3px;font-size:12.5px;font-weight:500;color:var(--mi-text)}
.ops-board header span{display:block;color:var(--mi-text-muted);font-size:10.5px;line-height:1.6;margin-bottom:8px}
.ops-board ol{list-style:none;margin:0;padding:0;display:flex;flex-direction:column;gap:2px}
.ops-board li{display:grid;grid-template-columns:16px minmax(0,1fr) auto;align-items:baseline;gap:8px;padding:5px 0;border-bottom:1px solid var(--mi-border-subtle)}
.ops-board li:last-child{border-bottom:0}
.ops-rank{color:var(--mi-text-muted);font-size:10.5px;font-variant-numeric:tabular-nums}
.ops-movie{border:0;background:transparent;padding:0;font:inherit;font-size:12px;color:var(--mi-text);text-align:left;cursor:pointer;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.ops-movie:hover{color:var(--mi-accent-text);text-decoration:underline;text-underline-offset:3px}
.ops-board li > b{color:var(--mi-accent-text);font-weight:500;font-size:12px;font-variant-numeric:tabular-nums}
.ops-disclosure{margin-top:18px;border-top:1px solid var(--mi-border-subtle);padding-top:12px}
.ops-disclosure summary{cursor:pointer;font-size:12px;color:var(--mi-text-soft)}
.ops-disclosure summary small{color:var(--mi-text-muted);font-size:10.5px;margin-left:6px}
.ops-defs{display:grid;grid-template-columns:max-content minmax(0,1fr);gap:6px 14px;margin:12px 0 0;font-size:11.5px;line-height:1.7}
.ops-defs dt{color:var(--mi-text-secondary)}
.ops-defs dd{margin:0;color:var(--mi-text-soft)}
.ops-sr{position:absolute;width:1px;height:1px;overflow:hidden;clip:rect(0,0,0,0);white-space:nowrap}
/* Legibility floor: functional micro-text stays at 11px or above. */
.ops-users :is(.ops-status, .ops-meta, .ops-state-grid span, .ops-states-title span, .ops-rank,
  .ops-board header span, .ops-trunc, .ops-disclosure summary small, .ops-dim .adm-bar small,
  .ops-inline-btn) {font-size: 11px}
.ops-users .ops-states-title {font-size: 12px}
.ops-users .ops-rank {color: var(--mi-text-muted)}
.ops-users .ops-defs {font-size: 12px}
.ops-users .ops-meta {font-size: 11.5px}
.ops-users :is(button,select,summary):focus-visible{outline:2px solid var(--mi-accent);outline-offset:2px}
@media (max-width: 1100px){
  .ops-trends,.ops-dims,.ops-boards{grid-template-columns:minmax(0,1fr)}
}
</style>