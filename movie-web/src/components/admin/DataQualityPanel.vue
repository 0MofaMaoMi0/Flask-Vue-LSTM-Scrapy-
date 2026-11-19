<script setup lang="ts">
import {computed, onBeforeUnmount, onMounted, ref} from 'vue'
import {useRoute, useRouter} from 'vue-router'
import AdminQuality from '../AdminQuality.vue'
import {formatUtc} from './adminContext'
import {
  fmtNumber, fmtPercent, loadBatches, loadCoverage, loadMissing as loadMissingPage, loadQuality,
  QUALITY_CATEGORIES, QUALITY_TOPICS,
  type BatchesPayload, type MissingRow, type QualityPayload, type QualitySeverityRow, type TopicCoverage,
} from './analytics'
import './admin-ui.css'

const router = useRouter(), route = useRoute()
const coverage = ref<TopicCoverage | null>(null), coverageError = ref('')
const missing = ref<MissingRow[]>([]), missingError = ref(''), missingOffset = ref(0), hasMore = ref(false)
const quality = ref<QualityPayload | null>(null), qualityError = ref('')
const denominator = ref<'active' | 'mapped'>('active')
const problemTopic = ref(''), problemCategory = ref(''), problemOffset = ref(0)
const batches = ref<BatchesPayload | null>(null), batchError = ref(''), batchStatus = ref('')
const busy = ref(false), qualityBusy = ref(false), batchesBusy = ref(false)
const controller = ref<AbortController | null>(null)
let qualityController: AbortController | null = null
let batchesController: AbortController | null = null
let missingController: AbortController | null = null
const LIMIT = 50
const PROBLEM_LIMIT = 20
const BATCH_LIMIT = 10
const TOPICS = ['base', 'countries', 'companies', 'spoken_languages', 'keywords', 'releases', 'credits', 'metrics']
const STATUSES = ['PRESENT', 'EMPTY', 'MISSING', 'NOT_FETCHED', 'FAILED']
const TOPIC_LABELS: Record<string, string> = {
  base: '基础资料', countries: '制作国家', companies: '制作公司', spoken_languages: '对白语言',
  keywords: '关键词', releases: '地区上映', credits: '演职员', metrics: '评分/热度',
  poster: '海报资料', overview: '剧情简介', tmdb_identity: 'TMDB 身份映射',
}
const STATUS_LABELS: Record<string, string> = {
  PRESENT: '已就绪', EMPTY: '上游空值', MISSING: '缺失', NOT_FETCHED: '未采集', FAILED: '采集失败', UNMAPPED: '未关联',
}
const STATUS_HINTS: Record<string, string> = {
  PRESENT: '已正确结构化可查询',
  EMPTY: '上游确实无值，不算失败',
  MISSING: '来源无该字段',
  NOT_FETCHED: '尚未发起补采',
  FAILED: '采集或解析失败',
  UNMAPPED: '在架但无 TMDB 身份，不参与聚合',
}
const BATCH_STATUS_LABELS: Record<string, string> = {
  succeeded: '已完成', partial: '部分成功', failed: '失败', running: '进行中',
  pending: '待执行', completed: '已完成', aborted: '已中止', COMPLETED: '已完成', PARTIAL: '部分成功', FAILED: '失败',
}
const qualityStatuses = computed(() => {
  const statuses = quality.value?.states.statuses?.length ? quality.value.states.statuses : [...STATUSES]
  return statuses.includes('UNMAPPED') ? statuses : [...statuses, 'UNMAPPED']
})
const problemTopics = computed(() => {
  const topics = qualityTopics.value.slice()
  if (!topics.includes('tmdb_identity')) topics.push('tmdb_identity')
  return topics
})

async function loadMissing(parentSignal?: AbortSignal) {
  missingController?.abort()
  const current = new AbortController()
  missingController = current
  const signal = parentSignal && typeof AbortSignal.any === 'function'
    ? AbortSignal.any([parentSignal, current.signal])
    : current.signal
  missingError.value = ''
  try {
    const page = await loadMissingPage(missingOffset.value, LIMIT, signal)
    if (missingController !== current || parentSignal?.aborted) return
    missing.value = page.items || []
    hasMore.value = Boolean(page.has_more)
  } catch (e) {
    if ((e as Error).name !== 'AbortError' && missingController === current) missingError.value = (e as Error).message
  }
}
async function loadQualityData() {
  qualityController?.abort()
  const current = new AbortController()
  qualityController = current
  qualityBusy.value = true; qualityError.value = ''
  try {
    quality.value = await loadQuality({
      denominator: denominator.value, top: 20, limit: PROBLEM_LIMIT, offset: problemOffset.value,
      topic: problemTopic.value || undefined, category: problemCategory.value || undefined,
    }, current.signal)
  } catch (e) {
    if ((e as Error).name !== 'AbortError') {
      quality.value = null
      qualityError.value = (e as Error).message
    }
  } finally {
    if (qualityController === current) qualityBusy.value = false
  }
}
async function loadBatchesData() {
  batchesController?.abort()
  const current = new AbortController()
  batchesController = current
  batchesBusy.value = true; batchError.value = ''
  try {
    batches.value = await loadBatches({limit: BATCH_LIMIT, offset: 0, status: batchStatus.value || undefined}, current.signal)
  } catch (e) {
    if ((e as Error).name !== 'AbortError') { batches.value = null; batchError.value = (e as Error).message }
  } finally {
    if (batchesController === current) batchesBusy.value = false
  }
}
async function load() {
  busy.value = true
  controller.value?.abort()
  const current = new AbortController()
  controller.value = current
  coverageError.value = ''
  try {
    coverage.value = await loadCoverage(current.signal)
  } catch (e) {
    if ((e as Error).name !== 'AbortError') coverageError.value = (e as Error).message
  }
  await Promise.all([loadMissing(current.signal), loadQualityData(), loadBatchesData()])
  if (controller.value === current) busy.value = false
}
function page(delta: number) {
  const next = Math.max(0, missingOffset.value + delta * LIMIT)
  if (next === missingOffset.value) return
  missingOffset.value = next
  loadMissing()
}
function problemPage(delta: number) {
  const next = Math.max(0, problemOffset.value + delta * PROBLEM_LIMIT)
  if (next === problemOffset.value) return
  problemOffset.value = next
  loadQualityData()
}
function applyProblemFilters() {
  problemOffset.value = 0
  loadQualityData()
}
function setDenominator(mode: 'active' | 'mapped') {
  if (denominator.value === mode) return
  denominator.value = mode
  problemOffset.value = 0
  loadQualityData()
}
function coverageCount(topic: string, status: string): number {
  return coverage.value?.topics?.[topic]?.[status] ?? 0
}
const qualityTopics = computed(() => {
  const present = Object.keys(quality.value?.topics || {})
  const ordered = QUALITY_TOPICS.filter(topic => present.includes(topic))
  return [...ordered, ...present.filter(topic => !(QUALITY_TOPICS as readonly string[]).includes(topic))]
})
const problemTotal = computed(() => quality.value?.problems?.total ?? 0)
const severity = computed(() => quality.value?.severity)
function topicCount(topic: string, status: string): number {
  return Number((quality.value?.topics?.[topic] as Record<string, unknown> | undefined)?.[status] || 0)
}
function severityName(row: QualitySeverityRow): string {
  if (row.country_iso2) return row.name ? row.country_iso2 + ' · ' + row.name : row.country_iso2
  if (row.genre_key) return row.name || row.genre_key
  if (row.decade != null) return row.name || row.decade + 's'
  return row.name
}
function goAudit() {
  router.push({path: '/admin', query: {...route.query, tab: 'audit'}})
}
onMounted(load)
onBeforeUnmount(() => {
  controller.value?.abort()
  missingController?.abort()
  qualityController?.abort()
  batchesController?.abort()
})
defineExpose({refresh: load})
</script>

<template>
  <section class="quality-view" aria-labelledby="quality-view-title">
    <header class="quality-head">
      <div>
        <h2 id="quality-view-title">数据质量诊断</h2>
        <p class="quality-meta">来源：TMDB 资料质量与补采批次 · 快照 {{ formatUtc(quality?.as_of || coverage?.observed_at) }} · 只读诊断，不触发补采</p>
      </div>
      <button type="button" class="adm-btn" :disabled="busy" @click="load">手动刷新</button>
    </header>

    <section class="adm-panel">
      <div class="quality-title-row">
        <h2>主题覆盖矩阵 <small>{{ fmtNumber(quality?.denominator.movies) }} 部 {{ quality?.denominator.mode === 'mapped' ? '已关联' : '在架' }}影片</small></h2>
        <div class="adm-seg" role="group" aria-label="分母口径">
          <button type="button" :aria-pressed="denominator === 'active'" @click="setDenominator('active')">在架 active</button>
          <button type="button" :aria-pressed="denominator === 'mapped'" @click="setDenominator('mapped')">已关联 mapped</button>
        </div>
      </div>
      <p v-if="qualityError" class="adm-errobox" role="alert">{{ qualityError }} <button type="button" class="link-btn" @click="loadQualityData">重试</button></p>
      <p v-else-if="qualityBusy && !quality" class="adm-state">正在读取质量矩阵…</p>
      <template v-else-if="quality">
        <div class="adm-tablewrap">
          <table>
            <thead><tr><th>主题</th><th v-for="status in qualityStatuses" :key="status" class="num">{{ STATUS_LABELS[status] || status }}</th><th class="num">合计</th><th class="num">已就绪率</th></tr></thead>
            <tbody>
              <tr v-for="topic in qualityTopics" :key="topic">
                <td>{{ TOPIC_LABELS[topic] || topic }}<br><small>{{ topic }}</small></td>
                <td v-for="status in qualityStatuses" :key="status" class="num" :class="{'weak': !topicCount(topic, status)}">{{ fmtNumber(topicCount(topic, status)) }}</td>
                <td class="num">{{ fmtNumber(quality.topics[topic]?.total) }}</td>
                <td class="num">{{ quality.topics[topic]?.present_ratio == null ? '—' : fmtPercent(quality.topics[topic]?.present_ratio) }}</td>
              </tr>
            </tbody>
          </table>
        </div>
        <div class="adm-inline status-hints">
          <span v-for="status in qualityStatuses" :key="status">{{ STATUS_LABELS[status] || status }} <b>{{ STATUS_HINTS[status] || '' }}</b></span>
        </div>
        <p class="adm-note">六态严格区分：EMPTY 是上游成功但无值，不等于失败；UNMAPPED 只在 mapped 分母外单列。矩阵只诊断，不执行修复。</p>
        <div v-if="quality.assets" class="asset-quality">
          <span>海报 <b>{{ fmtNumber(quality.assets.poster_present) }}</b> / 缺 {{ fmtNumber(quality.assets.poster_missing) }}</span>
          <span>简介 <b>{{ fmtNumber(quality.assets.overview_present) }}</b> / 缺 {{ fmtNumber(quality.assets.overview_missing) }}</span>
          <span>背景图 <b>{{ fmtNumber(quality.assets.backdrop_present) }}</b></span>
        </div>
      </template>
      <p v-else class="adm-empty">质量矩阵暂不可用；不展示未确认字段。</p>
    </section>

    <section v-if="quality" class="adm-panel">
      <h2>按国家 / 类型 / 年代的严重度 <small>缺失率 = 缺资料影片 / 该组影片</small></h2>
      <p class="adm-note">{{ severity?.definition || '按缺失影片占比排序，仅统计有分组资料的影片。' }} 国家未知 {{ fmtNumber(severity?.unknown_country_movies) }} 部。</p>
      <div class="severity-grid">
        <div v-for="group in [{key:'country', title:'国家', rows: severity?.by_country || []}, {key:'genre', title:'类型', rows: severity?.by_genre || []}, {key:'decade', title:'年代', rows: severity?.by_decade || []}]" :key="group.key">
          <h3>{{ group.title }} <small>TOP {{ group.rows.length }}</small></h3>
          <div v-if="group.rows.length" class="adm-tablewrap">
            <table>
              <thead><tr><th>分组</th><th class="num">影片</th><th class="num">缺资料</th><th class="num">缺失率</th><th>主要缺失</th></tr></thead>
              <tbody>
                <tr v-for="row in group.rows" :key="severityName(row)">
                  <td>{{ severityName(row) }}</td>
                  <td class="num">{{ fmtNumber(row.movies) }}</td>
                  <td class="num">{{ fmtNumber(row.missing_movies) }}</td>
                  <td class="num">{{ row.missing_ratio == null ? '—' : fmtPercent(row.missing_ratio) }}</td>
                  <td><small>{{ (row.top_missing_topics || []).slice(0, 3).map(item => (TOPIC_LABELS[item.topic] || item.topic) + ' ' + item.movies).join(' · ') || '—' }}</small></td>
                </tr>
              </tbody>
            </table>
          </div>
          <p v-else class="adm-empty">该维度暂无严重度数据（样本不足或字段未提供）。</p>
        </div>
      </div>
    </section>

    <section v-if="quality" class="adm-panel">
      <div class="quality-title-row">
        <h2>问题清单 <small>共 {{ fmtNumber(problemTotal) }} 条 · 第 {{ Math.floor(problemOffset / PROBLEM_LIMIT) + 1 }} 页</small></h2>
        <div class="problem-filters">
          <label class="adm-field inline"><span>主题</span>
            <select v-model="problemTopic" @change="applyProblemFilters">
              <option value="">全部主题</option>
              <option v-for="topic in problemTopics" :key="topic" :value="topic">{{ TOPIC_LABELS[topic] || topic }}</option>
            </select>
          </label>
          <label class="adm-field inline"><span>类别</span>
            <select v-model="problemCategory" @change="applyProblemFilters">
              <option value="">全部类别</option>
              <option v-for="category in QUALITY_CATEGORIES" :key="category" :value="category">{{ STATUS_LABELS[category] || category }}</option>
            </select>
          </label>
        </div>
      </div>
      <p v-if="qualityBusy" class="adm-state">正在读取问题清单…</p>
      <div v-else class="adm-tablewrap">
        <table>
          <thead><tr><th>影片</th><th>主题</th><th>类别</th><th>可处理</th><th>最近采集</th></tr></thead>
          <tbody>
            <tr v-for="item in quality.problems.items" :key="item.movie_id + '-' + item.topic + '-' + item.category">
              <td><button type="button" class="link-btn" @click="router.push({path:'/movies', query:{from:'admin'}, hash:'#movie/' + item.movie_id})">{{ item.title }}</button><br><small>#{{ item.movie_id }} · TMDB {{ item.tmdb_id ?? '—' }} · {{ item.release_year || '—' }}</small></td>
              <td>{{ TOPIC_LABELS[item.topic] || item.topic }}<br><small>{{ item.topic }}</small></td>
              <td><span class="category-tag" :class="'cat-' + item.category.toLowerCase()">{{ STATUS_LABELS[item.category] || item.category }}</span></td>
              <td>{{ item.actionable ? '待处理' : '非问题（上游空值）' }}</td>
              <td>{{ formatUtc(item.topic_observed_at || item.latest_fetched_at) }}<br><small>{{ item.source }}</small></td>
            </tr>
          </tbody>
        </table>
      </div>
      <p v-if="!quality.problems.items.length" class="adm-empty">当前过滤条件下没有问题记录。</p>
      <div class="adm-pager">
        <button type="button" class="adm-btn" :disabled="problemOffset === 0 || qualityBusy" @click="problemPage(-1)">上一页</button>
        <span>每页 {{ PROBLEM_LIMIT }} 条 · 共 {{ fmtNumber(problemTotal) }} 条</span>
        <button type="button" class="adm-btn" :disabled="problemOffset + PROBLEM_LIMIT >= problemTotal || qualityBusy" @click="problemPage(1)">下一页</button>
      </div>
      <p class="adm-note">actionable=false 表示上游确认无值（EMPTY），保留以便核对但不计入可修复问题。清单只读，修复必须走受控授权流程。</p>
    </section>

    <section class="adm-panel">
      <div class="quality-title-row">
        <h2>最近补采批次 <small>{{ batches?.total != null ? '共 ' + fmtNumber(batches.total) + ' 批' : '加载中' }}</small></h2>
        <div class="problem-filters">
          <label class="adm-field inline"><span>状态</span>
            <select v-model="batchStatus" @change="loadBatchesData">
              <option value="">全部状态</option>
              <option v-for="status in (batches?.statuses || Object.keys(BATCH_STATUS_LABELS))" :key="status" :value="status">{{ BATCH_STATUS_LABELS[status] || status }}</option>
            </select>
          </label>
          <button type="button" class="adm-btn" @click="goAudit">操作审计 →</button>
        </div>
      </div>
      <p v-if="batchError" class="adm-errobox" role="alert">{{ batchError }} <button type="button" class="link-btn" @click="loadBatchesData">重试</button></p>
      <p v-else-if="batchesBusy && !batches" class="adm-state">正在读取批次…</p>
      <div v-else-if="batches?.items.length" class="adm-tablewrap">
        <table>
          <thead><tr><th>批次</th><th>状态</th><th>开始 / 结束</th><th class="num">目标</th><th class="num">成功 / 失败 / 跳过</th><th>错误码</th></tr></thead>
          <tbody>
            <tr v-for="batch in batches.items" :key="batch.batch_id">
              <td>#{{ batch.batch_id }}<br><small>{{ batch.source }} · {{ batch.mode }}</small></td>
              <td><span class="batch-tag" :class="'batch-' + batch.status.toLowerCase()">{{ BATCH_STATUS_LABELS[batch.status] || batch.status }}</span></td>
              <td>{{ formatUtc(batch.started_at) }}<br><small>{{ formatUtc(batch.finished_at) }}</small></td>
              <td class="num">{{ fmtNumber(batch.reported_counts?.target_documents) }}</td>
              <td class="num">{{ fmtNumber(batch.reported_counts?.success_count) }} / {{ fmtNumber(batch.reported_counts?.failed_count) }} / {{ fmtNumber(batch.reported_counts?.skipped_count) }}</td>
              <td><small>{{ (batch.error_codes || []).slice(0, 3).map(item => item.error_code + ' ×' + item.count).join(' · ') || batch.error_summary || '—' }}</small></td>
            </tr>
          </tbody>
        </table>
      </div>
      <p v-else-if="batches" class="adm-empty">当前没有可展示的补采批次。</p>
      <p class="adm-note">批次计数优先使用任务报告的真实计数；报告缺失时显示 item_status 推导值并标注来源。完整批次日志在“操作审计”。</p>
    </section>

    <details class="legacy-quality-fold" :open="!quality">
      <summary>原始补采主题覆盖与待处理缺失队列 <small>底层文档与队列明细（按需展开）</small></summary>
      <section class="adm-panel">
        <h2>主题覆盖 <small>{{ fmtNumber(coverage?.documents) }} 份 TMDB 补采文档</small></h2>
        <p v-if="coverageError" class="adm-errobox" role="alert">{{ coverageError }}</p>
        <p v-else-if="busy && !coverage" class="adm-state">正在读取主题覆盖…</p>
        <template v-else-if="coverage">
          <div class="adm-tablewrap">
            <table>
              <thead><tr><th>主题</th><th v-for="status in STATUSES" :key="status" class="num">{{ STATUS_LABELS[status] }}</th><th class="num">合计</th></tr></thead>
              <tbody>
                <tr v-for="topic in TOPICS" :key="topic">
                  <td>{{ TOPIC_LABELS[topic] || topic }}<br><small>{{ topic }}</small></td>
                  <td v-for="status in STATUSES" :key="status" class="num" :class="{'weak': !coverageCount(topic, status)}">{{ fmtNumber(coverageCount(topic, status)) }}</td>
                  <td class="num">{{ fmtNumber(STATUSES.reduce((sum, status) => sum + coverageCount(topic, status), 0)) }}</td>
                </tr>
              </tbody>
            </table>
          </div>
          <p class="adm-note">来源 JSON 存在不等于已结构化可查询；这里只做诊断，不执行修复。</p>
        </template>
      </section>

      <section class="adm-panel">
        <h2>待处理缺失队列 <small>分页 · 第 {{ Math.floor(missingOffset / LIMIT) + 1 }} 页</small></h2>
        <p v-if="missingError" class="adm-errobox" role="alert">{{ missingError }} <button type="button" class="link-btn" @click="loadMissing()">重试</button></p>
        <div v-else class="adm-tablewrap">
          <table>
            <thead><tr><th>movie_id</th><th>TMDB ID</th><th>主题</th><th>状态</th><th>最近尝试</th><th>错误码</th></tr></thead>
            <tbody>
              <tr v-for="row in missing" :key="row.movie_id + '-' + (row.topic || '') + '-' + (row.status || '')">
                <td>{{ row.movie_id }}</td><td>{{ row.tmdb_id ?? '—' }}</td>
                <td>{{ TOPIC_LABELS[row.topic || ''] || row.topic || '—' }}</td>
                <td>{{ STATUS_LABELS[row.status || ''] || row.status || '—' }}</td>
                <td>{{ formatUtc(row.attempt_at) }}</td>
                <td><small>{{ row.error_code || '—' }}</small></td>
              </tr>
            </tbody>
          </table>
        </div>
        <p v-if="!missing.length && !missingError" class="adm-empty">当前没有待处理缺失记录。</p>
        <div class="adm-pager">
          <button type="button" class="adm-btn" :disabled="missingOffset === 0 || busy" @click="page(-1)">上一页</button>
          <span>每页 {{ LIMIT }} 条</span>
          <button type="button" class="adm-btn" :disabled="!hasMore || busy" @click="page(1)">下一页</button>
        </div>
        <p class="adm-note">队列只返回定位所需字段（身份、主题、状态、最近尝试、错误码），不含原始载荷。</p>
      </section>
    </details>

    <AdminQuality />
  </section>
</template>

<style scoped>
.quality-view{min-width:0}
.quality-head{display:flex;justify-content:space-between;align-items:flex-end;gap:16px;flex-wrap:wrap;margin:0 0 14px}
.quality-head h2{margin:2px 0 4px;font-family:var(--serif);font-size:23px;font-weight:500;color:#f3eee4}
.quality-meta{margin:0;font-size:11px;color:#8f8a80;line-height:1.7}
.quality-title-row{display:flex;justify-content:space-between;align-items:flex-end;gap:12px;flex-wrap:wrap}
.quality-title-row h2{margin-bottom:8px}
.problem-filters{display:flex;gap:8px;align-items:flex-end;flex-wrap:wrap;margin-bottom:8px}
.problem-filters .adm-field.inline select{padding:5px 8px}
.status-hints{display:flex;gap:14px;flex-wrap:wrap;margin-top:8px}
.status-hints span{font-size:11px;color:#8f83a0}
.status-hints b{color:#b7a9c6;font-weight:400}
.asset-quality{display:flex;gap:18px;flex-wrap:wrap;margin-top:8px;font-size:11px;color:#a9a294}
.asset-quality b{color:#e6dff0;font-weight:500}
.severity-grid{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:14px}
.severity-grid > div{min-width:0}
.severity-grid h3{margin:2px 0 7px;font-size:11.5px;color:#d8d1c6;font-weight:500}
.severity-grid h3 small{color:#8f83a0;font-weight:400}
.severity-grid table{min-width:0;width:100%;font-size:11.5px}
.severity-grid th,.severity-grid td{padding:6px 7px}
.severity-grid td:first-child,.severity-grid td:last-child{white-space:normal;overflow-wrap:anywhere}
.category-tag,.batch-tag{display:inline-block;font-size:11px;border-radius:3px;padding:1px 6px;background:#23212b;color:#c3b7d2;white-space:nowrap}
.cat-empty{color:#a39a8e;background:#22211f}
.cat-failed{color:#e7b2a8;background:#2d2020}
.cat-missing,.cat-not_fetched{color:#dcc49b;background:#2a2419}
.cat-unmapped{color:#c7b7df;background:#241f2d}
.batch-completed,.batch-succeeded{color:#b7d6b1;background:#1c271c}
.batch-failed{color:#e7b2a8;background:#2d2020}
.batch-partial{color:#dcc49b;background:#2a2419}
.legacy-quality-fold{margin:0 0 14px;border:1px solid rgba(255,255,255,.07);border-radius:10px;background:rgba(16,15,20,.65);padding:10px 14px}
.legacy-quality-fold > summary{cursor:pointer;list-style:none;display:flex;align-items:center;justify-content:space-between;gap:10px;font-size:12.5px;font-weight:500;color:#cfc7bb;user-select:none}
.legacy-quality-fold > summary::-webkit-details-marker{display:none}
.legacy-quality-fold > summary::after{content:'展开 ▾';font-size:11px;color:#9d91b0;flex-shrink:0}
.legacy-quality-fold[open] > summary::after{content:'收起 ▴'}
.legacy-quality-fold > summary small{font-size:11px;font-weight:400;color:#8f8a80;margin-left:8px}
.legacy-quality-fold[open] > summary{margin-bottom:12px;padding-bottom:8px;border-bottom:1px solid rgba(255,255,255,.06)}
.weak{color:#625d55!important}
.link-btn{border:0;background:transparent;color:#dcc49b;font:inherit;font-size:11.5px;cursor:pointer;padding:0;text-decoration:underline;text-underline-offset:2px}
@media(max-width:1100px){.severity-grid{grid-template-columns:minmax(0,1fr)}}
</style>