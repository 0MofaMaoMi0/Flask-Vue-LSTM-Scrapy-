<script setup lang="ts">
/**
 * Page 09 — AI service and agent observability (WP-D).
 *
 * Reads `GET /api/admin/ai/analytics` (WP-B contract §5) and falls back to the
 * legacy `/api/admin/ai-usage` shape when the new endpoint is not deployed.
 * Provenance classes are never merged, unknown Token counts stay “未知”, and
 * no prompt, response body, key or endpoint is ever requested or shown.
 */
import {computed, onBeforeUnmount, onMounted, ref, watch} from 'vue'
import DashboardChart from './dashboard/DashboardChart.vue'
import {
  AI_SOURCE_LABELS, fmtInt, fmtRatio, fmtToken, fmtUtc, loadAiAnalytics, loadAiUsageLegacy,
  parseDays, statusMeta, trendOption, windowRange,
  type AiAnalytics, type AiDaily, type AiUsageLegacy, type Days, type TrendPoint,
} from './admin/ops/opsApi'
import './admin/admin-ui.css'

const props = withDefaults(defineProps<{days?: Days}>(), {days: 30})
const emit = defineEmits<{days: [days: Days]}>()
const DAY_CHOICES: Days[] = [7, 30, 90]

const activeDays = ref<Days>(parseDays(props.days))
const data = ref<AiAnalytics | null>(null)
const legacy = ref<AiUsageLegacy | null>(null)
const error = ref('')
const busy = ref(false)
let controller: AbortController | undefined
let ticket = 0

const overall = computed(() => statusMeta(data.value?.status))
const windowLabel = computed(() => {
  const block = data.value?.window
  return block ? (block.timezone || 'UTC') + ' · ' + windowRange(block.from, block.to) : 'UTC 自然日窗口'
})
const tokenStatus = computed(() => statusMeta(data.value?.tokens?.status))

async function load() {
  const current = new AbortController()
  controller?.abort()
  controller = current
  const mine = ++ticket
  busy.value = true
  error.value = ''
  try {
    const next = await loadAiAnalytics(activeDays.value, current.signal)
    if (current.signal.aborted || mine !== ticket) return
    data.value = next
    legacy.value = null
  } catch (e) {
    if (current.signal.aborted || mine !== ticket || (e as Error).name === 'AbortError') return
    const failure = e as Error & {status?: number}
    if (failure.status === 401 || failure.status === 403) {
      error.value = failure.message
      return
    }
    try {
      const fallback = await loadAiUsageLegacy(activeDays.value, current.signal)
      if (current.signal.aborted || mine !== ticket) return
      legacy.value = fallback
      data.value = null
    } catch (inner) {
      if (current.signal.aborted || mine !== ticket || (inner as Error).name === 'AbortError') return
      error.value = failure.message
    }
  } finally {
    if (mine === ticket) busy.value = false
  }
}

function setDays(value: Days) {
  if (value === activeDays.value) return
  activeDays.value = value
  emit('days', value)
  load()
}
function refresh() { load() }

watch(() => props.days, value => {
  const next = parseDays(value)
  if (next === activeDays.value) return
  activeDays.value = next
  load()
})
onMounted(load)
onBeforeUnmount(() => { ++ticket; controller?.abort() })
defineExpose({refresh})

const dailyPoints = computed<TrendPoint[]>(() => {
  const rows: AiDaily[] = data.value?.daily || []
  return rows.map(row => ({date: row.date.slice(5), value: row.calls}))
})
const dailyOption = computed(() => trendOption('派发调用', dailyPoints.value, 'bar'))
function chartRows(list: TrendPoint[]) {
  return list.map(point => ({name: point.date, value: point.value}))
}

const statusChips = computed(() => {
  const totals = data.value?.totals
  if (!totals) return []
  return [
    {key: 'success', label: '成功', value: totals.success, tone: 'ok'},
    {key: 'failed', label: '失败', value: totals.failed, tone: 'bad'},
    {key: 'interrupted', label: '中断', value: totals.interrupted, tone: 'warn'},
    {key: 'timeout', label: '超时', value: totals.timeout, tone: 'warn'},
    {key: 'unknown', label: '未知状态', value: totals.unknown, tone: 'muted'},
  ]
})

const interruptedOrTimeout = computed(() => {
  const totals = data.value?.totals
  if (!totals || (totals.interrupted == null && totals.timeout == null)) return null
  return (totals.interrupted ?? 0) + (totals.timeout ?? 0)
})

const provenanceRows = computed(() => (data.value?.provenance || []).map(row => ({
  ...row,
  label: AI_SOURCE_LABELS[row.source] || row.source,
})))

const dailyBreakdown = computed(() => (data.value?.daily || []).filter(row => row.calls > 0))
const legacyRows = computed(() => (legacy.value?.groups || []).map(row => ({
  ...row,
  label: AI_SOURCE_LABELS[row.provenance] || row.provenance,
})))
</script>

<template>
  <section class="ops-ai adm-panel" aria-labelledby="ai-analysis-title">
    <h2 id="ai-analysis-title">AI 服务分析 <small>{{ windowLabel }}</small></h2>

    <div class="adm-toolbar">
      <div class="adm-field">
        <span>统计窗口</span>
        <div class="adm-seg" role="group" aria-label="统计窗口">
          <button v-for="option in DAY_CHOICES" :key="option" type="button"
                  :aria-pressed="activeDays === option" @click="setDays(option)">近 {{ option }} 天</button>
        </div>
      </div>
      <p v-if="data" class="ops-meta">
        <span class="ops-status" :class="'tone-' + overall.tone">{{ overall.label }}</span>
        <span>生成于 {{ fmtUtc(data.as_of) }}</span>
        <span v-if="data.sample_size?.calls != null">窗口调用 {{ fmtInt(data.sample_size.calls) }} 次</span>
      </p>
    </div>

    <p v-if="error" class="adm-errobox" role="alert">{{ error }}<span v-if="data || legacy">（已保留上一份可用快照）</span>
      <button type="button" class="adm-btn ops-inline-btn" @click="refresh">重新读取</button>
    </p>
    <p v-if="busy && !data && !legacy" class="adm-state" role="status">正在读取 AI 用量账本…</p>

    <template v-else-if="data">
      <p v-if="data.status === 'unavailable'" class="adm-warnbox" role="status">
        {{ data.reason || 'AI 用量账本不可用。' }} 调用数、状态与 Token 均保持不可用，不以 0 代替。
      </p>

      <div class="adm-kpis">
        <div><span>派发调用</span><strong>{{ fmtInt(data.totals.calls) }}</strong><p>不含 NOT_DISPATCHED；按服务端 UTC 派发时间统计</p></div>
        <div><span>成功率</span><strong>{{ fmtRatio(data.totals.success_rate) }}</strong><p>成功 = SUCCEEDED / VERIFIED</p></div>
        <div><span>失败</span><strong>{{ fmtInt(data.totals.failed) }}</strong><p>失败 = FAILED / ERROR</p></div>
        <div><span>中断 + 超时</span><strong>{{ fmtInt(interruptedOrTimeout) }}</strong><p>中断含 CANCELLED / INTERRUPTED / ABORTED / REJECTED</p></div>
        <div><span>未知状态</span><strong>{{ fmtInt(data.totals.unknown) }}</strong><p>UNKNOWN / DISPATCHED / RESERVED 等，不当作成功</p></div>
      </div>

      <p class="ops-chips" aria-label="调用状态明细">
        <span v-for="chip in statusChips" :key="chip.key" class="ops-status" :class="'tone-' + chip.tone">
          {{ chip.label }} {{ fmtInt(chip.value) }}
        </span>
      </p>

      <h3>来源分解 <small>真实 / Mock / 未知分开统计，绝不混算</small></h3>
      <div class="adm-tablewrap">
        <table>
          <caption class="ops-sr">按调用来源分解的调用数、状态与成功率</caption>
          <thead>
            <tr>
              <th scope="col">来源</th><th scope="col" class="num">调用</th><th scope="col" class="num">成功</th>
              <th scope="col" class="num">失败</th><th scope="col" class="num">中断</th><th scope="col" class="num">超时</th>
              <th scope="col" class="num">未知状态</th><th scope="col" class="num">成功率</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="row in provenanceRows" :key="row.source">
              <th scope="row">{{ row.label }}</th>
              <td class="num">{{ fmtInt(row.calls) }}</td>
              <td class="num">{{ fmtInt(row.success) }}</td>
              <td class="num">{{ fmtInt(row.failed) }}</td>
              <td class="num">{{ fmtInt(row.interrupted) }}</td>
              <td class="num">{{ fmtInt(row.timeout) }}</td>
              <td class="num">{{ fmtInt(row.unknown) }}</td>
              <td class="num">{{ fmtRatio(row.success_rate) }}</td>
            </tr>
          </tbody>
        </table>
      </div>
      <p class="adm-note">REAL 仅指 kind=BYOK_AGENT 且 paid_request=true 且模型非 mock*；Mock 与未知来源不并入真实调用。</p>

      <h3>每日趋势 <small>UTC 自然日</small></h3>
      <div class="ops-trend">
        <div class="ops-chart">
          <DashboardChart :option="dailyOption" :rows="chartRows(dailyPoints)" label="每日 AI 派发调用" unit="次"/>
        </div>
      </div>
      <details v-if="dailyBreakdown.length" class="ops-disclosure">
        <summary>每日来源明细</summary>
        <div class="adm-tablewrap">
          <table>
            <caption class="ops-sr">每日按来源拆分的调用数</caption>
            <thead><tr><th scope="col">日期（UTC）</th><th scope="col" class="num">调用</th><th scope="col" class="num">真实</th><th scope="col" class="num">Mock</th><th scope="col" class="num">来源未知</th></tr></thead>
            <tbody>
              <tr v-for="row in dailyBreakdown" :key="row.date">
                <td>{{ row.date }}</td><td class="num">{{ fmtInt(row.calls) }}</td>
                <td class="num">{{ fmtInt(row.real) }}</td><td class="num">{{ fmtInt(row.mock) }}</td>
                <td class="num">{{ fmtInt(row.unknown_provenance) }}</td>
              </tr>
            </tbody>
          </table>
        </div>
      </details>

      <h3>模型用量 <small>按模型与来源分组</small></h3>
      <div v-if="data.models.length" class="adm-tablewrap">
        <table>
          <caption class="ops-sr">按模型与来源的调用、成功率和已知 Token</caption>
          <thead>
            <tr>
              <th scope="col">模型</th><th scope="col">来源</th><th scope="col" class="num">调用</th>
              <th scope="col" class="num">成功率</th><th scope="col" class="num">已知输入</th>
              <th scope="col" class="num">已知输出</th><th scope="col" class="num">未知用量调用</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="row in data.models" :key="row.model + '-' + row.source">
              <td>{{ row.model || '—' }}</td>
              <td>{{ AI_SOURCE_LABELS[row.source] || row.source }}</td>
              <td class="num">{{ fmtInt(row.calls) }}</td>
              <td class="num">{{ fmtRatio(row.success_rate) }}</td>
              <td class="num">{{ fmtToken(row.known_input_tokens) }}</td>
              <td class="num">{{ fmtToken(row.known_output_tokens) }}</td>
              <td class="num">{{ fmtInt(row.unknown_usage_calls) }}</td>
            </tr>
          </tbody>
        </table>
      </div>
      <p v-else class="adm-empty">该窗口没有模型派发记录。</p>

      <h3>Token 用量 <small>已知小计与未知用量分开</small></h3>
      <div class="ops-token">
        <p class="ops-meta">
          <span class="ops-status" :class="'tone-' + tokenStatus.tone">{{ tokenStatus.label }}</span>
          <span>状态：{{ data.tokens.status }}</span>
        </p>
        <div class="ops-token-grid">
          <div><span>已知输入</span><b>{{ fmtToken(data.tokens.known_input_tokens) }}</b></div>
          <div><span>已知输出</span><b>{{ fmtToken(data.tokens.known_output_tokens) }}</b></div>
          <div><span>已知合计</span><b>{{ fmtToken(data.tokens.known_total_tokens) }}</b></div>
          <div class="ops-token-unknown"><span>未知用量调用</span><b>{{ fmtInt(data.tokens.unknown_usage_calls) }}</b></div>
        </div>
        <p class="adm-note">
          {{ data.tokens.definition || '已知 Token 为已上报小计，未知用量不计 0。' }}
          未知分项保持“未知”，不会当作 0 计入合计。
        </p>
      </div>

      <div class="ops-grid3">
        <section class="ops-block">
          <h3>缓存命中输入</h3>
          <p class="ops-meta">
            <span class="ops-status" :class="'tone-' + statusMeta(data.cached_input.status).tone">{{ statusMeta(data.cached_input.status).label }}</span>
          </p>
          <p v-if="data.cached_input.status === 'available'" class="ops-body">
            已知缓存命中 {{ fmtToken(data.cached_input.tokens) }} Token · {{ fmtInt(data.cached_input.reported_calls) }} 次调用已上报
          </p>
          <p v-else class="ops-body">{{ data.cached_input.reason || '账本未记录缓存命中输入；属于输入 Token 的子集，不重复计入。' }}</p>
        </section>
        <section class="ops-block">
          <h3>错误类型与延迟</h3>
          <p class="ops-meta">
            <span class="ops-status" :class="'tone-' + statusMeta(data.errors.status).tone">错误类型 {{ statusMeta(data.errors.status).label }}</span>
            <span class="ops-status" :class="'tone-' + statusMeta(data.latency.status).tone">延迟 {{ statusMeta(data.latency.status).label }}</span>
          </p>
          <p class="ops-body">{{ data.errors.reason || '账本无错误类型字段' }}；{{ data.latency.reason || '账本无请求计时字段' }}。</p>
        </section>
        <section class="ops-block">
          <h3>Agent 观察</h3>
          <p class="ops-meta">
            <span class="ops-status" :class="'tone-' + statusMeta(data.agent.status).tone">{{ statusMeta(data.agent.status).label }}</span>
          </p>
          <p class="ops-body">
            工具调用 {{ fmtInt(data.agent.tool_calls) }} · 有效电影卡片 {{ fmtInt(data.agent.valid_movie_cards) }}。
            {{ data.agent.reason || '无工具调用/卡片返回事件表' }}。
          </p>
        </section>
      </div>

      <p class="adm-note">
        本页只读查询已有账本，不触发模型测试或付费调用；绝不展示 prompt、响应正文、API Key、端点或用户身份。
        BYOK 费用由用户自行承担，已知 Token 小计不等于账单。
      </p>
    </template>

    <template v-else-if="legacy">
      <p class="adm-warnbox" role="status">
        新版统计接口暂不可用，当前显示兼容旧接口 <code>/api/admin/ai-usage</code> 的账本聚合；
        未知用量与未知 Token 仍按未知处理。
      </p>
      <p v-if="legacy.status !== 'AVAILABLE'" class="adm-empty">AI 用量账本不可用，暂不展示统计值。</p>
      <template v-else>
        <div class="adm-kpis">
          <div><span>派发调用</span><strong>{{ fmtInt(legacy.calls) }}</strong><p>不含未派发调用</p></div>
          <div><span>已知合计 Token</span><strong>{{ fmtToken(legacy.known_total_tokens) }}</strong><p>已上报小计，不补 0</p></div>
          <div><span>未知用量调用</span><strong>{{ fmtInt(legacy.unknown_calls) }}</strong><p>用量不完整的请求</p></div>
          <div><span>未派发</span><strong>{{ fmtInt(legacy.not_dispatched_calls) }}</strong><p>不计入调用与 Token</p></div>
        </div>
        <div class="adm-tablewrap">
          <table>
            <caption class="ops-sr">兼容旧接口：按来源的调用与 Token</caption>
            <thead>
              <tr>
                <th scope="col">来源</th><th scope="col" class="num">派发</th><th scope="col" class="num">未知用量</th>
                <th scope="col" class="num">已知输入</th><th scope="col" class="num">已知输出</th>
                <th scope="col" class="num">已知合计</th><th scope="col" class="num">缓存命中输入</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="row in legacyRows" :key="row.provenance">
                <th scope="row">{{ row.label }}</th>
                <td class="num">{{ fmtInt(row.calls) }}</td>
                <td class="num">{{ fmtInt(row.unknown_calls) }}</td>
                <td class="num">{{ fmtToken(row.known_input_tokens) }}</td>
                <td class="num">{{ fmtToken(row.known_output_tokens) }}</td>
                <td class="num">{{ fmtToken(row.known_total_tokens) }}</td>
                <td class="num">{{ fmtToken(row.known_cached_input_tokens) }}</td>
              </tr>
            </tbody>
          </table>
        </div>
        <p class="adm-note">缓存命中输入是输入 Token 的子集，不重复累加；旧接口的“来源未确认”不等于真实调用。</p>
      </template>
    </template>

    <p v-else class="adm-empty">AI 服务统计暂时不可用，请手动刷新。</p>
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
.ops-chips{display:flex;flex-wrap:wrap;gap:6px;margin:0 0 6px}
.ops-trend{margin:0;border:1px solid var(--mi-border);border-radius:10px;background:var(--mi-surface-warm);padding:10px 12px 6px}
.ops-chart{height:160px;min-width:0}
.ops-token{border:1px solid var(--mi-border);border-radius:10px;background:var(--mi-surface-warm);padding:12px 14px}
.ops-token-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(120px,1fr));gap:8px;margin:10px 0 0}
.ops-token-grid div{display:flex;flex-direction:column;gap:4px;padding:8px 10px;border:1px solid var(--mi-border-subtle);border-radius:8px;background:var(--mi-surface)}
.ops-token-grid span{color:var(--mi-text-secondary);font-size:10.5px}
.ops-token-grid b{font-size:16px;font-weight:500;color:var(--mi-text);font-variant-numeric:tabular-nums}
.ops-token-unknown b{color:var(--mi-warning)}
.ops-grid3{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:14px;margin-top:16px}
.ops-block{border:1px solid var(--mi-border);border-radius:10px;background:var(--mi-surface-warm);padding:4px 14px 12px;min-width:0}
.ops-block h3{margin-top:14px}
.ops-body{margin:4px 0 0;color:var(--mi-text-soft);font-size:11.5px;line-height:1.7}
.ops-disclosure{margin-top:16px;border-top:1px solid var(--mi-border-subtle);padding-top:12px}
.ops-disclosure summary{cursor:pointer;font-size:12px;color:var(--mi-text-soft)}
.ops-sr{position:absolute;width:1px;height:1px;overflow:hidden;clip:rect(0,0,0,0);white-space:nowrap}
/* Legibility floor: functional micro-text stays at 11px or above. */
.ops-ai :is(.ops-status, .ops-token-grid span, .ops-meta){font-size:11px}
.ops-ai .ops-body {font-size: 12px}
.ops-ai :is(button,summary):focus-visible{outline:2px solid var(--mi-accent);outline-offset:2px}
/* AdminView already frames the AI tab in `.panel`; drop our frame there. */
:global(.panel) > .ops-ai{border:0;background:transparent;padding:0;margin:0}
@media (max-width: 1100px){
  .ops-grid3{grid-template-columns:minmax(0,1fr)}
}
</style>