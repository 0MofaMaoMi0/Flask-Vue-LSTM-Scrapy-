<script setup lang="ts">
/**
 * Page 11 — system health and operational anomalies (WP-D).
 *
 * Basic health fields are unchanged; `stats`, `recommendation_tracking` and
 * `ai_failures` come from WP-B. Status is one of the five public classes
 * (normal / abnormal / observing / unknown / not_instrumented) plus the
 * explicit “未采集” case. Thresholds are shown, never hidden.
 */
import {computed, onBeforeUnmount, onMounted, ref} from 'vue'
import {
  EM_DASH, fmtHours, fmtInt, fmtRatio, fmtUtc, loadHealth, statusMeta,
  type Days, type HealthPanelData,
} from './opsApi'
import '../admin-ui.css'

function formatDbTime(value: string | null | undefined): string {
  if (!value) return EM_DASH
  const text = String(value).trim()
  if (!text) return EM_DASH
  if (/(?:[zZ]|[+-]\d{2}:?\d{2})$/.test(text)) return fmtUtc(text)
  const matched = text.match(/^(\d{4}-\d{2}-\d{2})[T ](\d{2}:\d{2})/)
  return matched ? `${matched[1]} ${matched[2]}（DB 本地）` : text
}

withDefaults(defineProps<{days?: Days}>(), {days: 30})
const emit = defineEmits<{navigate: [tab: string]}>()

const data = ref<HealthPanelData | null>(null)
const error = ref('')
const busy = ref(false)
let controller: AbortController | undefined

const BLOCK_LABELS: Record<string, string> = {
  dashboard: '综合总览',
  users_analytics: '用户与行为',
  interest_match: '资源与兴趣匹配',
  recommendation_analytics: '推荐效果',
  ai_analytics: 'AI 服务',
  health: '系统运行',
}

async function load() {
  const current = new AbortController()
  controller?.abort()
  controller = current
  busy.value = true
  error.value = ''
  try {
    data.value = await loadHealth(current.signal)
  } catch (e) {
    if (current.signal.aborted) return
    error.value = (e as Error).message
  } finally {
    if (controller === current) busy.value = false
  }
}

function refresh() { load() }
onMounted(load)
onBeforeUnmount(() => controller?.abort())
defineExpose({refresh})

const generatedAt = computed(() => fmtUtc(data.value?.stats?.as_of))
const tracking = computed(() => data.value?.recommendation_tracking)
const aiFailures = computed(() => data.value?.ai_failures)
const trackingStatus = computed(() => statusMeta(tracking.value?.status))
const aiStatus = computed(() => statusMeta(aiFailures.value?.status))

const lastSuccess = computed(() => {
  const map = data.value?.stats?.last_success || {}
  return Object.keys(BLOCK_LABELS).map(key => ({
    key,
    label: BLOCK_LABELS[key],
    at: map[key] || null,
  }))
})

const LEGEND = [
  {status: 'normal', text: '达到公开阈值，链路正常'},
  {status: 'abnormal', text: '越过固定阈值，需要排查'},
  {status: 'observing', text: '样本不足，仅观察不下结论'},
  {status: 'unknown', text: '查询失败或状态不可判定'},
  {status: 'not_instrumented', text: '对应埋点或账本尚未接入'},
]
</script>

<template>
  <section class="adm-panel ops-health" aria-labelledby="admin-health-title">
    <h2 id="admin-health-title">系统运行 <small>只读健康快照<span v-if="generatedAt"> · 生成于 {{ generatedAt }}</span></small></h2>

    <p v-if="error" class="adm-errobox" role="alert">{{ error }}
      <button type="button" class="adm-btn ops-inline-btn" @click="refresh">重新读取</button>
    </p>
    <p v-else-if="busy && !data" class="adm-state" role="status">正在读取系统健康信息…</p>

    <template v-else-if="data">
      <h3>基础健康</h3>
      <div class="ops-facts">
        <div><span>数据库时间</span><b>{{ formatDbTime(data.db_time) }}</b></div>
        <div><span>在架影片总数</span><b>{{ fmtInt(data.movies) }}</b></div>
        <div><span>M7 助手库</span><b>{{ data.m7_configured ? '已配置' : '未配置' }}</b></div>
        <div><span>当前管理员</span><b>{{ data.admin || '—' }}</b></div>
        <div><span>目录 schema</span><b>{{ data.config?.schema || '—' }}</b></div>
        <div><span>行为 schema</span><b>{{ data.config?.activity_schema || '—' }}</b></div>
      </div>
      <p class="adm-note">仅展示服务端只读健康字段；不返回环境变量值、连接串、密钥或堆栈。</p>

      <h3>运营异常卡片 <small>阈值固定且公开</small></h3>
      <div class="ops-cards">
        <section class="ops-card" aria-labelledby="tracking-title">
          <header>
            <div>
              <h4 id="tracking-title">推荐曝光断档</h4>
              <p class="ops-card-source">{{ tracking?.source || 'recommendation_impressions' }}</p>
            </div>
            <span class="ops-status" :class="'tone-' + trackingStatus.tone">{{ trackingStatus.label }}</span>
          </header>
          <p v-if="tracking?.reason" class="ops-card-reason">{{ tracking.reason }}</p>
          <dl class="ops-metrics">
            <div><dt>7 天有效曝光</dt><dd>{{ fmtInt(tracking?.impressions_7d) }}</dd></div>
            <div><dt>24 小时曝光</dt><dd>{{ fmtInt(tracking?.impressions_24h) }}</dd></div>
            <div><dt>最近曝光</dt><dd>{{ fmtUtc(tracking?.last_impression_at) }}</dd></div>
            <div><dt>距今</dt><dd>{{ fmtHours(tracking?.hours_since_last_impression) }}</dd></div>
          </dl>
          <p class="ops-rule">规则：{{ tracking?.rule || '7 天窗口有效曝光 ≥10 且最近一条 ≤24h 判 normal。' }}</p>
          <div class="adm-actions">
            <button type="button" class="adm-btn" @click="emit('navigate', 'recommend')">查看推荐效果</button>
          </div>
        </section>

        <section class="ops-card" aria-labelledby="ai-failure-title">
          <header>
            <div>
              <h4 id="ai-failure-title">AI 失败率</h4>
              <p class="ops-card-source">{{ aiFailures?.source || 'M7 usage 账本' }}</p>
            </div>
            <span class="ops-status" :class="'tone-' + aiStatus.tone">{{ aiStatus.label }}</span>
          </header>
          <p v-if="aiFailures?.reason" class="ops-card-reason">{{ aiFailures.reason }}</p>
          <dl class="ops-metrics">
            <div><dt>7 天调用</dt><dd>{{ fmtInt(aiFailures?.calls) }}</dd></div>
            <div><dt>失败</dt><dd>{{ fmtInt(aiFailures?.failed) }}</dd></div>
            <div><dt>失败率</dt><dd>{{ fmtRatio(aiFailures?.failure_rate) }}</dd></div>
            <div><dt>样本</dt><dd>{{ fmtInt(aiFailures?.sample_size) }}</dd></div>
          </dl>
          <p class="ops-rule">规则：{{ aiFailures?.rule || '7 天调用 <10 判 observing；失败率 ≥50% 判 abnormal。' }}</p>
          <div class="adm-actions">
            <button type="button" class="adm-btn" @click="emit('navigate', 'ai')">查看 AI 服务分析</button>
          </div>
        </section>
      </div>

      <div class="ops-legend" aria-label="健康状态图例">
        <template v-for="item in LEGEND" :key="item.status">
          <span class="ops-status" :class="'tone-' + statusMeta(item.status).tone">{{ statusMeta(item.status).label }}</span>
          <span class="ops-legend-text">{{ item.text }}</span>
        </template>
      </div>

      <h3>统计生成时刻 <small>进程内记录，重启清空</small></h3>
      <template v-if="data.stats">
        <div class="ops-stats-meta">
          <span>进程启动 <b>{{ fmtUtc(data.stats.process_started_at) }}</b></span>
          <span>已记录区块 <b>{{ fmtInt(data.stats.sample_size?.blocks_recorded) }}</b></span>
        </div>
        <div class="adm-tablewrap">
          <table>
            <caption class="ops-sr">各管理统计接口最近一次成功返回时间</caption>
            <thead><tr><th scope="col">统计区块</th><th scope="col">最近成功返回（UTC）</th></tr></thead>
            <tbody>
              <tr v-for="row in lastSuccess" :key="row.key">
                <th scope="row">{{ row.label }}</th>
                <td>{{ row.at ? fmtUtc(row.at) : '本次进程尚未生成' }}</td>
              </tr>
            </tbody>
          </table>
        </div>
        <p class="adm-note">{{ data.stats.definition || '各管理统计接口最近一次成功返回时间；进程重启后清空，不代表数据库快照时间。' }}</p>
      </template>
      <p v-else class="adm-empty">当前后端未返回进程内生成时刻。</p>

      <div class="adm-actions">
        <button type="button" class="adm-btn" @click="emit('navigate', 'catalog')">查看电影库治理</button>
        <button type="button" class="adm-btn" @click="emit('navigate', 'audit')">查看操作审计</button>
      </div>
      <p class="adm-note">异常仅描述依赖与埋点的可观测状态，不代表用户数据受损；本页不提供任何写操作。</p>
    </template>

    <p v-else class="adm-empty">系统健康信息暂时不可用，请手动刷新。</p>
  </section>
</template>

<style scoped>
.ops-inline-btn{margin-left:10px;padding:3px 9px;font-size:11px}
.ops-facts{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(290px,100%),1fr));gap:8px 16px}
.ops-facts div{display:flex;align-items:baseline;justify-content:space-between;gap:12px;min-width:0;padding:9px 12px;border:1px solid rgba(241,236,226,.10);border-radius:9px;background:#191a1f;font-size:11.5px}
.ops-facts span{color:#a9a294;flex-shrink:0}
.ops-facts b{color:#f0eadf;font-weight:500;font-variant-numeric:tabular-nums;text-align:right;overflow-wrap:anywhere}
.ops-cards{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:14px}
.ops-card{border:1px solid rgba(241,236,226,.10);border-radius:10px;background:#191a1f;padding:14px 16px;min-width:0}
.ops-card header{display:flex;align-items:flex-start;justify-content:space-between;gap:12px}
.ops-card h4{margin:0;font-size:13px;font-weight:500;color:#efe9de}
.ops-card-source{margin:3px 0 0;color:#8f8a80;font-size:10.5px}
.ops-card-reason{margin:10px 0 0;color:#dcc49b;font-size:11.5px;line-height:1.7}
.ops-metrics{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:8px 14px;margin:12px 0 0}
.ops-metrics div{display:flex;flex-direction:column;gap:3px}
.ops-metrics dt{color:#a9a294;font-size:10.5px}
.ops-metrics dd{margin:0;color:#f0eadf;font-size:14px;font-variant-numeric:tabular-nums}
.ops-rule{margin:12px 0 0;color:#8f8a80;font-size:10.5px;line-height:1.7}
.ops-card .adm-actions{margin-top:10px}
.ops-status{display:inline-flex;align-items:center;gap:6px;border:1px solid rgba(241,236,226,.16);border-radius:999px;padding:2px 8px;font-size:10.5px;color:#a9a294;white-space:nowrap}
.ops-status::before{content:"";width:5px;height:5px;border-radius:50%;background:currentColor;opacity:.9}
.ops-status.tone-ok{color:#a9c4ae;border-color:rgba(156,191,166,.32)}
.ops-status.tone-warn{color:#dcc49b;border-color:rgba(200,164,106,.32)}
.ops-status.tone-bad{color:#e0a79c;border-color:rgba(232,160,148,.34)}
.ops-status.tone-muted{color:#a9a294}
.ops-legend{display:grid;grid-template-columns:max-content minmax(0,1fr);align-items:center;gap:6px 12px;margin:16px 0 0;max-width:620px}
.ops-legend-text{color:#8f8a80;font-size:10.5px;line-height:1.6}
.ops-stats-meta{display:flex;flex-wrap:wrap;gap:6px 18px;font-size:11px;color:#8f8a80;margin:0 0 8px}
.ops-stats-meta b{color:#c9c2b8;font-weight:500;margin-left:4px}
/* Legibility floor: functional micro-text stays at 11px or above. */
.ops-health :is(.ops-status, .ops-card-source, .ops-metrics dt, .ops-rule, .ops-legend-text, .ops-stats-meta){font-size:11px}
.ops-health :is(button,summary):focus-visible{outline:2px solid #c8a46a;outline-offset:2px}
@media (max-width: 1100px){
  .ops-cards{grid-template-columns:minmax(0,1fr)}
}
</style>