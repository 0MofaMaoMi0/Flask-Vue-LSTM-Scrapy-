<script setup lang="ts">
import {computed} from 'vue'
import {fmtNumber, fmtPercent, type AnalyticsPayload} from './analytics'
import {formatUtc} from './adminContext'
import './admin-ui.css'

interface Catalog {
  total: number; tmdb_mapped: number
  countries?: { iso2: string; count: number }[]
  quality?: Record<string, number | null>
}
interface Behavior { events: number; active_users: number }
interface Users { accounts_active: number; new_in_window: number }
interface Ai {
  status: string; real_calls?: number | null; mock_calls?: number | null
  unknown_provenance_calls?: number | null
  real_input_tokens?: number | null; real_output_tokens?: number | null
  known_input_tokens?: number | null; known_output_tokens?: number | null
}
interface RecommendationBlock {
  status?: string; source?: string; sample_size?: number | null; as_of?: string
  impressions?: number | null; clicks?: number | null; ctr?: number | null
  accounts?: number | null; channels?: unknown[]; reason?: string | null
}
interface SystemBlock {
  status?: string; as_of?: string; generated_at?: Record<string, string>
  ai_failures?: number | null; ai_failure_status?: string
  process_started_at?: string; note?: string
}
interface DashboardLike {
  as_of: string
  window?: { days: number; timezone?: string }
  catalog: Catalog
  behavior: Behavior
  users: Users
  ai: Ai
  recommendation?: RecommendationBlock | null
  system?: SystemBlock | null
}
interface FeedbackLike {
  impressions?: number | null; clicks?: number | null; ctr?: number | null
  channels?: unknown[]; as_of?: string; window?: { days?: number }
}
const props = defineProps<{
  dashboard: DashboardLike | null
  analytics: AnalyticsPayload | null | undefined
  feedback: FeedbackLike | null | undefined
  recommendationStatus: 'loading' | 'ready' | 'error'
  analyticsStatus: 'loading' | 'ready' | 'error'
  analyticsError?: string
  days: number
}>()
const emit = defineEmits<{navigate: [tab: string]}>()

const snapshot = computed(() => props.dashboard ? formatUtc(props.dashboard.as_of) : '—')
const analyticsSnapshot = computed(() => props.analytics ? formatUtc(props.analytics.snapshot.latest_fetched_at) : '—')
const windowLabel = computed(() => `近 ${props.days} 天 · UTC`)
const scopeCatalog = computed(() => props.analytics?.scope.catalog)
const catalogUnavailable = computed(() => scopeCatalog.value?.status === 'UNAVAILABLE')
const activeMovies = computed(() => catalogUnavailable.value ? null
  : scopeCatalog.value?.active_movies ?? scopeCatalog.value?.active ?? props.dashboard?.catalog.total ?? null)
const mappedMovies = computed(() => catalogUnavailable.value ? null
  : scopeCatalog.value?.tmdb_mapped ?? scopeCatalog.value?.mapped ?? props.dashboard?.catalog.tmdb_mapped ?? null)
const unmappedMovies = computed(() => {
  if (catalogUnavailable.value) return null
  if (scopeCatalog.value?.unmapped != null) return scopeCatalog.value.unmapped
  const total = activeMovies.value, mapped = mappedMovies.value
  return total != null && mapped != null ? Math.max(0, total - mapped) : null
})
const cacheLine = computed(() => {
  const cache = props.analytics?.scope.cache
  if (!cache) return ''
  return `文档缓存${cache.hit ? '命中' : '冷载'} · 载入 ${formatUtc(cache.loaded_at)} · 水位 ${formatUtc(cache.watermark?.max_updated_at || cache.watermark?.max_fetched_at)}`
})
const dashboardRecommendation = computed<RecommendationBlock | null>(() => {
  const block = props.dashboard?.recommendation
  if (!block) return null
  return block.status === 'available' || block.impressions != null ? block : null
})
const recommended = computed(() => dashboardRecommendation.value || props.feedback || null)
const recommendedSource = computed(() => dashboardRecommendation.value
  ? '有效曝光统计'
  : '兼容推荐反馈')
const recommendationNote = computed(() => {
  if (dashboardRecommendation.value) return '窗口有效曝光（visible-v2）'
  if (props.recommendationStatus === 'error') return '推荐接口读取失败，不补 0'
  if (props.recommendationStatus === 'loading') return '正在读取推荐快照'
  return '兼容旧反馈口径'
})
const countryCount = computed(() => props.analytics?.countries?.total
  ?? props.dashboard?.catalog.countries?.length ?? null)
const posterCoverage = computed(() => {
  const coverage = props.analytics?.coverage?.assets
  const movies = props.analytics?.coverage?.movies
  if (!coverage || !movies) return null
  const posters = coverage.poster
  return typeof posters === 'number' ? posters / movies : null
})
const knownTokens = computed(() => {
  const ai = props.dashboard?.ai
  if (!ai || ai.status !== 'AVAILABLE') return null
  const input = ai.known_input_tokens ?? ai.real_input_tokens
  const output = ai.known_output_tokens ?? ai.real_output_tokens
  if (input == null && output == null) return null
  return (input || 0) + (output || 0)
})
const failedTopics = computed(() => {
  const topics = props.analytics?.coverage?.topics
  if (!topics) return null
  let failed = 0
  for (const counts of Object.values(topics)) failed += Number(counts?.FAILED || 0)
  return failed
})
const ctr = computed(() => {
  const data = recommended.value
  if (!data) return null
  if (typeof data.ctr === 'number') return data.ctr
  if (typeof data.impressions === 'number' && data.impressions > 0 && typeof data.clicks === 'number') {
    return data.clicks / data.impressions
  }
  return null
})
const aiAvailable = computed(() => props.dashboard?.ai.status === 'AVAILABLE')
const aiFailures = computed(() => props.dashboard?.system?.ai_failures ?? null)
const aiFailureStatus = computed(() => props.dashboard?.system?.ai_failure_status || '')
const systemGenerated = computed(() => formatUtc(props.dashboard?.system?.generated_at?.catalog || props.dashboard?.system?.as_of))
</script>

<template>
  <section class="admin-kpi-groups" aria-label="管理员驾驶舱关键指标分组">
    <div v-if="analyticsStatus === 'error'" class="adm-warnbox" role="status">
      资源分析接口暂不可用：{{ analyticsError || '未知原因' }}。本页保留基础快照，地图退回旧国家统计口径。
    </div>

    <article class="kpi-group">
      <header>
        <div><h2>电影资产</h2><p>全库当前快照 · 不随 7/30/90 天窗口变化</p></div>
        <button type="button" class="group-link" @click="emit('navigate', 'catalog')">电影库治理 →</button>
      </header>
      <div class="kpi-grid">
        <div>
          <span>在架影片</span><strong>{{ catalogUnavailable ? '暂不可用' : fmtNumber(activeMovies) }}<small v-if="!catalogUnavailable">部</small></strong>
          <p class="kpi-source">全库在架口径</p><p class="kpi-time">{{ analyticsSnapshot }}</p>
        </div>
        <div>
          <span>TMDB 身份关联率</span><strong>{{ catalogUnavailable || activeMovies == null ? '—' : fmtPercent((mappedMovies || 0) / Math.max(1, activeMovies)) }}</strong>
          <p class="kpi-source">{{ fmtNumber(mappedMovies) }} 部已关联</p><p class="kpi-time">占比按在架口径</p>
        </div>
        <div>
          <span>制作国家 / 地区</span><strong>{{ fmtNumber(countryCount) }}<small>个</small></strong>
          <p class="kpi-source">TMDB 补采国家口径</p><p class="kpi-time">{{ analyticsSnapshot }}</p>
        </div>
        <div>
          <span>海报资料覆盖</span><strong>{{ posterCoverage == null ? '暂不可用' : fmtPercent(posterCoverage) }}</strong>
          <p class="kpi-source">已关联影片资料覆盖</p><p class="kpi-time">按有海报影片计</p>
        </div>
      </div>
      <p class="scope-line">
        在架 {{ fmtNumber(activeMovies) }} / 已关联 {{ fmtNumber(mappedMovies) }} /
        未关联 {{ fmtNumber(unmappedMovies) }} 不参与 TMDB 国家与评分统计。
      </p>
      <p v-if="cacheLine" class="cache-line">{{ cacheLine }}</p>
    </article>

    <article class="kpi-group">
      <header>
        <div><h2>用户</h2><p>窗口指标随 7/30/90 天变化 · 事件按 UTC 日期聚合</p></div>
        <button type="button" class="group-link" @click="emit('navigate', 'users')">用户与行为 →</button>
      </header>
      <div class="kpi-grid">
        <div>
          <span>启用账户数</span><strong>{{ fmtNumber(dashboard?.users.accounts_active) }}<small>个</small></strong>
          <p class="kpi-source">全库当前快照</p><p class="kpi-time">{{ snapshot }}</p>
        </div>
        <div>
          <span>窗口新增账户</span><strong>{{ fmtNumber(dashboard?.users.new_in_window) }}<small>个</small></strong>
          <p class="kpi-source">窗口内注册</p><p class="kpi-time">{{ windowLabel }}</p>
        </div>
        <div>
          <span>窗口行为活跃账户</span><strong>{{ fmtNumber(dashboard?.behavior.active_users) }}<small>人</small></strong>
          <p class="kpi-source">至少一条电影行为</p><p class="kpi-time">{{ windowLabel }}</p>
        </div>
        <div>
          <span>窗口行为事件</span><strong>{{ fmtNumber(dashboard?.behavior.events) }}<small>次</small></strong>
          <p class="kpi-source">含设置与撤销</p><p class="kpi-time">{{ windowLabel }}</p>
        </div>
      </div>
    </article>

    <article class="kpi-group">
      <header>
        <div><h2>推荐</h2><p>曝光与点击真实计数 · 与窗口联动</p></div>
        <button type="button" class="group-link" @click="emit('navigate', 'recommend')">推荐效果 →</button>
      </header>
      <div class="kpi-grid">
        <div>
          <span>有效曝光</span><strong>{{ fmtNumber(recommended?.impressions) }}</strong>
          <p class="kpi-source">{{ recommendedSource }}</p><p class="kpi-time">{{ recommendationNote }}</p>
        </div>
        <div>
          <span>去重点击</span><strong>{{ fmtNumber(recommended?.clicks) }}</strong>
          <p class="kpi-source">按同批曝光归属</p><p class="kpi-time">{{ windowLabel }}</p>
        </div>
        <div>
          <span>CTR</span><strong>{{ fmtPercent(ctr) }}</strong>
          <p class="kpi-source">点击 ÷ 有效曝光</p><p class="kpi-time">不是推荐准确率</p>
        </div>
        <div>
          <span>可分析频道</span><strong>{{ recommended?.channels ? fmtNumber(recommended.channels.length) : '暂无' }}</strong>
          <p class="kpi-source">仅统计有埋点频道</p><p class="kpi-time">{{ recommendationStatus === 'error' ? '读取失败' : '不补造频道' }}</p>
        </div>
      </div>
      <p class="scope-line">来源 {{ recommendedSource }} · 快照 {{ formatUtc(recommended?.as_of || dashboard?.as_of) }}。没有记录时保持“—”，不补 0。</p>
    </article>

    <article class="kpi-group wide">
      <header>
        <div><h2>AI 服务</h2><p>真实 / Mock / 来源未知分开统计 · 未知 Token 不计零</p></div>
        <button type="button" class="group-link" @click="emit('navigate', 'ai')">AI 服务分析 →</button>
      </header>
      <div class="kpi-grid">
        <div>
          <span>真实派发请求</span><strong>{{ aiAvailable ? fmtNumber(dashboard?.ai.real_calls) : '暂不可得' }}</strong>
          <p class="kpi-source">AI 用量账本</p><p class="kpi-time">{{ windowLabel }}</p>
        </div>
        <div>
          <span>Mock 调用</span><strong>{{ aiAvailable ? fmtNumber(dashboard?.ai.mock_calls) : '暂不可得' }}</strong>
          <p class="kpi-source">与真实派发独立</p><p class="kpi-time">{{ windowLabel }}</p>
        </div>
        <div>
          <span>来源未确认</span><strong>{{ aiAvailable ? fmtNumber(dashboard?.ai.unknown_provenance_calls) : '暂不可得' }}</strong>
          <p class="kpi-source">不并入真实调用</p><p class="kpi-time">{{ windowLabel }}</p>
        </div>
        <div>
          <span>AI 失败调用</span><strong>{{ aiFailures == null ? (aiFailureStatus === 'not_instrumented' ? '未采集' : '—') : fmtNumber(aiFailures) }}<small v-if="aiFailures != null">次</small></strong>
          <p class="kpi-source">账本失败状态汇总</p><p class="kpi-time">{{ windowLabel }}</p>
        </div>
      </div>
      <p class="scope-line">已知 Token 合计 {{ knownTokens == null ? '未知' : fmtNumber(knownTokens) }}；未知用量保留为未知，不按 0 计入账单。</p>
    </article>

    <article class="kpi-group wide">
      <header>
        <div><h2>数据状态</h2><p>采集时效、缓存命中与来源可用性</p></div>
        <button type="button" class="group-link" @click="emit('navigate', 'catalog')">数据质量 →</button>
      </header>
      <div class="kpi-grid">
        <div>
          <span>最后成功采集</span><strong class="small-value">{{ analyticsSnapshot }}</strong>
          <p class="kpi-source">最近一次补采快照</p><p class="kpi-time">UTC</p>
        </div>
        <div>
          <span>TMDB 未关联</span><strong>{{ fmtNumber(unmappedMovies) }}<small>部</small></strong>
          <p class="kpi-source">不参与国家与评分</p><p class="kpi-time">{{ analyticsSnapshot }}</p>
        </div>
        <div>
          <span>采集失败主题计数</span><strong>{{ failedTopics == null ? '暂不可用' : fmtNumber(failedTopics) }}</strong>
          <p class="kpi-source">补采主题覆盖汇总</p><p class="kpi-time">上游空值不计失败</p>
        </div>
        <div>
          <span>统计生成时刻</span><strong class="small-value">{{ systemGenerated }}</strong>
          <p class="kpi-source">进程内统计快照</p><p class="kpi-time">{{ analyticsStatus === 'ready' ? '资源分析可用' : analyticsStatus === 'loading' ? '读取中' : '来源失败' }}</p>
        </div>
      </div>
      <p class="scope-line">{{ cacheLine || '文档缓存状态暂未返回。' }}</p>
    </article>
  </section>
</template>

<style scoped>
.admin-kpi-groups{display:grid;grid-template-columns:repeat(6,minmax(0,1fr));gap:14px;margin:0 0 18px;font-variant-numeric:tabular-nums}
.kpi-group{grid-column:span 2;border:1px solid #ffffff10;border-radius:12px;background:#11111d;padding:15px 17px;min-width:0;display:flex;flex-direction:column}
.kpi-group.wide{grid-column:span 3}
.kpi-group header{display:flex;justify-content:space-between;align-items:flex-start;gap:12px;margin-bottom:12px;flex-wrap:wrap}
.kpi-group h2{font-size:13.5px;font-weight:600;margin:0;color:#e6dff0;letter-spacing:0}
.kpi-group header p{font-size:11px;color:#9e8fae;margin:4px 0 0;line-height:1.55}
.group-link{border:0;background:transparent;color:#c1a7ef;font:inherit;font-size:11px;cursor:pointer;padding:2px 0;white-space:nowrap}
.group-link:hover{color:#fff;text-decoration:underline;text-underline-offset:3px}
.kpi-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:12px 14px;flex:1}
.kpi-group.wide .kpi-grid{grid-template-columns:repeat(4,minmax(0,1fr))}
.kpi-grid>div{min-width:0}
.kpi-grid span{display:block;font-size:11px;color:#a99aba;margin-bottom:5px;line-height:1.35}
.kpi-grid strong{display:block;font-size:clamp(18px,1.55vw,24px);font-weight:600;letter-spacing:-.02em;color:#ede8f5;line-height:1.2;overflow-wrap:anywhere;font-variant-numeric:tabular-nums}
.kpi-grid strong small{font-size:11px;color:#aa9dbe;font-weight:400;margin-left:3px;letter-spacing:0}
.kpi-grid strong.small-value{font-size:12.5px;font-weight:500;line-height:1.45;word-break:keep-all}
.kpi-source,.kpi-time{font-size:11px;color:#9287a4;margin:0;line-height:1.5;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.kpi-source{margin-top:6px}
.kpi-time{color:#867c98;margin-top:2px}
.scope-line{margin:12px 0 0;font-size:11px;line-height:1.65;color:#a99aba;border-top:1px solid #ffffff0e;padding-top:8px}
.cache-line{margin:5px 0 0;font-size:11px;line-height:1.55;color:#9287a4;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
@media(max-width:1280px){
 .admin-kpi-groups{grid-template-columns:repeat(2,minmax(0,1fr))}
 .kpi-group,.kpi-group.wide{grid-column:span 1}
}
@media(max-width:720px){
 .admin-kpi-groups{grid-template-columns:minmax(0,1fr)}
 .kpi-group.wide .kpi-grid{grid-template-columns:repeat(2,minmax(0,1fr))}
 .kpi-source,.kpi-time{white-space:normal}
}
</style>