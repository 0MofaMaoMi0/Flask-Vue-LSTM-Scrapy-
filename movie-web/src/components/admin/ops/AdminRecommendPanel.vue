<script setup lang="ts">
/**
 * Pages 07 + 08 — recommendation operations and resource/interest match (WP-D).
 *
 * The two surfaces answer different questions and keep different denominators:
 * page 08 is the effective-exposure cohort (visible-v2), page 07 is a
 * descriptive supply/interest/exposure comparison. Neither page may turn CTR
 * into “accuracy”, and the offline evaluation block stays “not evaluable”.
 */
import {computed, onBeforeUnmount, onMounted, ref, watch} from 'vue'
import DashboardChart from '../../dashboard/DashboardChart.vue'
import {
  DIMENSION_LABELS, INTEREST_DIMENSIONS, fmtInt, fmtRatio, fmtUtc, loadInterestMatch,
  loadRecommendationAnalytics, loadRecommendationFeedbackLegacy, parseDays, shareWidth,
  statusMeta, trendOption, windowRange,
  type Days, type InterestDimension, type InterestMatch,
  type RecommendationAnalytics, type RecommendationChannel, type RecommendationFeedbackLegacy,
  type TrendPoint,
} from './opsApi'
import '../admin-ui.css'

const props = withDefaults(defineProps<{days?: Days}>(), {days: 30})
const emit = defineEmits<{days: [days: Days]}>()
const DAY_CHOICES: Days[] = [7, 30, 90]
const VIEWS = [
  {id: 'effect', label: '推荐效果'},
  {id: 'interest', label: '资源与兴趣匹配'},
] as const
type ViewId = typeof VIEWS[number]['id']

const activeDays = ref<Days>(parseDays(props.days))
const view = ref<ViewId>('effect')

const effect = ref<RecommendationAnalytics | null>(null)
const legacy = ref<RecommendationFeedbackLegacy | null>(null)
const effectError = ref('')
const effectBusy = ref(false)
let effectController: AbortController | undefined
let effectTicket = 0

const interest = ref<InterestMatch | null>(null)
const interestError = ref('')
const interestBusy = ref(false)
const dimension = ref<InterestDimension>('genre')
let interestController: AbortController | undefined
let interestTicket = 0

const effectStatus = computed(() => statusMeta(effect.value?.status))
const interestStatus = computed(() => statusMeta(interest.value?.status))
const windowLabel = computed(() => {
  const block = effect.value?.window || interest.value?.window
  return block ? (block.timezone || 'UTC') + ' · ' + windowRange(block.from, block.to) : 'UTC 自然日窗口'
})

async function loadEffect() {
  const current = new AbortController()
  effectController?.abort()
  effectController = current
  const mine = ++effectTicket
  effectBusy.value = true
  effectError.value = ''
  try {
    effect.value = await loadRecommendationAnalytics(activeDays.value, current.signal)
    legacy.value = null
  } catch (e) {
    if (current.signal.aborted || mine !== effectTicket) return
    const failure = e as Error & {status?: number}
    if (failure.status === 401 || failure.status === 403) {
      effectError.value = failure.message
      return
    }
    // Compatibility path: the old feedback endpoint still knows exposures/clicks.
    try {
      legacy.value = await loadRecommendationFeedbackLegacy(activeDays.value, current.signal)
      effect.value = null
    } catch {
      effectError.value = failure.message
    }
  } finally {
    if (mine === effectTicket) effectBusy.value = false
  }
}

async function loadInterest() {
  const current = new AbortController()
  interestController?.abort()
  interestController = current
  const mine = ++interestTicket
  interestBusy.value = true
  interestError.value = ''
  try {
    interest.value = await loadInterestMatch(activeDays.value, dimension.value, current.signal)
  } catch (e) {
    if (current.signal.aborted || mine !== interestTicket) return
    interestError.value = (e as Error).message
  } finally {
    if (mine === interestTicket) interestBusy.value = false
  }
}

function setDays(value: Days) {
  if (value === activeDays.value) return
  activeDays.value = value
  emit('days', value)
  reloadActive()
}
function reloadActive() {
  if (view.value === 'effect') loadEffect()
  else loadInterest()
}
function switchView(next: ViewId) {
  view.value = next
  if (next === 'interest' && !interest.value) loadInterest()
}
function refresh() {
  if (view.value === 'effect') loadEffect()
  else loadInterest()
}
function setDimension(next: InterestDimension) {
  if (next === dimension.value) return
  dimension.value = next
  loadInterest()
}

watch(() => props.days, value => {
  const next = parseDays(value)
  if (next === activeDays.value) return
  activeDays.value = next
  reloadActive()
})
onMounted(loadEffect)
onBeforeUnmount(() => {
  effectController?.abort()
  interestController?.abort()
})
defineExpose({refresh})

function points(list: {date: string; impressions: number; clicks: number}[] | undefined): {impressions: TrendPoint[]; clicks: TrendPoint[]} {
  const rows = list || []
  return {
    impressions: rows.map(row => ({date: row.date.slice(5), value: row.impressions})),
    clicks: rows.map(row => ({date: row.date.slice(5), value: row.clicks})),
  }
}
const dailyPoints = computed(() => points(effect.value?.daily))
const impressionOption = computed(() => trendOption('有效曝光', dailyPoints.value.impressions, 'bar'))
const clickOption = computed(() => trendOption('有效点击', dailyPoints.value.clicks, 'bar'))
function chartRows(list: TrendPoint[]) {
  return list.map(point => ({name: point.date, value: point.value}))
}

const channels = computed<RecommendationChannel[]>(() => effect.value?.channels || [])
const funnelSteps = computed(() => {
  const labels: Record<string, string> = {impression: '曝光', click: '点击', favorite: '收藏', want: '想看', watched: '已看'}
  return (effect.value?.funnel.available_steps || []).map(step => labels[step] || step)
})

function categoryBars() {
  const rows = interest.value?.categories || []
  const top = rows.reduce((max, row) => Math.max(
    max,
    row.supply.share || 0,
    row.interest.share || 0,
    row.exposure.share || 0,
  ), 0)
  return rows.map(row => ({
    ...row,
    widths: {
      supply: shareWidth(row.supply.share, top),
      interest: shareWidth(row.interest.share, top),
      exposure: shareWidth(row.exposure.share, top),
    },
  }))
}
const interestRows = computed(() => categoryBars())
const interestSuppressed = computed(() => interest.value?.status === 'not_instrumented')
const exposureDown = computed(() => interest.value?.exposure?.status === 'unavailable')
</script>

<template>
  <section class="adm-panel ops-recommend" aria-labelledby="admin-recommend-title">
    <h2 id="admin-recommend-title">{{ view === 'effect' ? '推荐效果' : '资源与兴趣匹配' }}
      <small>{{ windowLabel }}</small>
    </h2>

    <div class="adm-toolbar">
      <div class="adm-field">
        <span>分析视图</span>
        <div class="adm-seg" role="group" aria-label="分析视图">
          <button v-for="item in VIEWS" :key="item.id" type="button"
                  :aria-pressed="view === item.id" @click="switchView(item.id)">{{ item.label }}</button>
        </div>
      </div>
      <div class="adm-field">
        <span>统计窗口</span>
        <div class="adm-seg" role="group" aria-label="统计窗口">
          <button v-for="option in DAY_CHOICES" :key="option" type="button"
                  :aria-pressed="activeDays === option" @click="setDays(option)">近 {{ option }} 天</button>
        </div>
      </div>
      <p v-if="view === 'effect' && effect" class="ops-meta">
        <span class="ops-status" :class="'tone-' + effectStatus.tone">{{ effectStatus.label }}</span>
        <span>生成于 {{ fmtUtc(effect.as_of) }}</span>
      </p>
      <p v-else-if="view === 'interest' && interest" class="ops-meta">
        <span class="ops-status" :class="'tone-' + interestStatus.tone">{{ interestStatus.label }}</span>
        <span>{{ DIMENSION_LABELS[interest.dimension] || interest.dimension }} · 生成于 {{ fmtUtc(interest.as_of) }}</span>
      </p>
    </div>

    <!-- 页 08：推荐效果 -->
    <template v-if="view === 'effect'">
      <p v-if="effectError" class="adm-errobox" role="alert">{{ effectError }}
        <button type="button" class="adm-btn ops-inline-btn" @click="refresh">重新读取</button>
      </p>
      <p v-else-if="effectBusy && !effect && !legacy" class="adm-state" role="status">正在读取推荐曝光与点击…</p>

      <template v-else-if="effect">
        <p v-if="effect.status === 'unavailable'" class="adm-warnbox" role="status">
          {{ effect.reason || '推荐曝光/点击表暂不可用。' }} 曝光、点击、CTR 与账户数保持不可用，不以 0 代替。
        </p>

        <div class="adm-kpis">
          <div><span>有效曝光</span><strong>{{ fmtInt(effect.totals.impressions) }}</strong><p>board_version = visible-v2：≥50% 视口 1 秒或主动点击</p></div>
          <div><span>去重有效点击</span><strong>{{ fmtInt(effect.totals.clicks) }}</strong><p>同一 (request_id, position, user_id) 曝光之后的点击</p></div>
          <div><span>CTR</span><strong>{{ fmtRatio(effect.totals.ctr, 2) }}</strong><p>有效点击 ÷ 有效曝光 · <b>不是推荐准确率</b></p></div>
          <div><span>参与账户</span><strong>{{ fmtInt(effect.accounts?.value ?? effect.totals.accounts) }}</strong><p>窗口内至少一次有效曝光的去重账户</p></div>
          <div><span>有记录的频道</span><strong>{{ fmtInt(effect.totals.channels) }}</strong><p>仅列出窗口内真实出现过的频道，不补造</p></div>
          <div><span>无法归属的点击</span><strong>{{ fmtInt(effect.totals.unattributed_clicks) }}</strong><p>点击存在但找不到对应有效曝光，未计入 CTR</p></div>
        </div>

        <h3>每日趋势 <small>UTC · 曝光与点击分开计数</small></h3>
        <div class="ops-trends">
          <figure class="ops-trend">
            <figcaption><span>每日有效曝光</span></figcaption>
            <div class="ops-chart">
              <DashboardChart :option="impressionOption" :rows="chartRows(dailyPoints.impressions)" label="每日有效曝光" unit="曝光"/>
            </div>
          </figure>
          <figure class="ops-trend">
            <figcaption><span>每日去重有效点击</span></figcaption>
            <div class="ops-chart">
              <DashboardChart :option="clickOption" :rows="chartRows(dailyPoints.clicks)" label="每日去重有效点击" unit="点击"/>
            </div>
          </figure>
        </div>

        <h3>频道排行 <small>只显示有记录的频道</small></h3>
        <div v-if="channels.length" class="adm-tablewrap">
          <table>
            <caption class="ops-sr">按频道的有效曝光、去重点击与 CTR</caption>
            <thead><tr><th scope="col">频道</th><th scope="col" class="num">有效曝光</th><th scope="col" class="num">去重点击</th><th scope="col" class="num">CTR</th><th scope="col">样本状态</th></tr></thead>
            <tbody>
              <tr v-for="row in channels" :key="row.channel">
                <td>{{ row.channel }}</td>
                <td class="num">{{ fmtInt(row.impressions) }}</td>
                <td class="num">{{ fmtInt(row.clicks) }}</td>
                <td class="num">{{ fmtRatio(row.ctr, 2) }}</td>
                <td><span class="ops-status" :class="'tone-' + statusMeta(row.status).tone">{{ statusMeta(row.status).label }}</span></td>
              </tr>
            </tbody>
          </table>
        </div>
        <p v-else class="adm-empty">该窗口没有记录到任何频道的有效曝光；不会补造频道或 0 值。</p>
        <p class="adm-note">单频道曝光少于 10 条标为“样本不足”，不据此宣布上升或下降。</p>

        <div class="ops-grid2">
          <section class="ops-block">
            <h3>行为漏斗 <small>曝光 → 点击</small></h3>
            <p class="ops-meta">
              <span class="ops-status" :class="'tone-' + statusMeta(effect.funnel.status).tone">{{ statusMeta(effect.funnel.status).label }}</span>
              <span v-if="funnelSteps.length">可用于：{{ funnelSteps.join(' → ') }}</span>
            </p>
            <p class="ops-body">{{ effect.funnel.reason || '后续收藏/想看/已看归因不可用。' }}</p>
            <p class="adm-note">收藏/想看/已看没有被归因到推荐曝光：行为事件未记录 request_id/position，且没有明确的归因窗口。时间先后不等于因果。</p>
          </section>
          <section class="ops-block">
            <h3>离线算法评估 <small>不产出 Recall@K / NDCG@K</small></h3>
            <p class="ops-meta">
              <span class="ops-status" :class="'tone-' + statusMeta(effect.offline_evaluation.status).tone">{{ statusMeta(effect.offline_evaluation.status).label }}</span>
            </p>
            <p class="ops-body">{{ effect.offline_evaluation.reason }}</p>
            <p class="adm-note">没有可信相关性标签与评估数据集时显示“不可评价”，也不把 CTR 当作准确率。</p>
          </section>
        </div>

        <details v-if="effect.definition" class="ops-disclosure">
          <summary>统计口径</summary>
          <dl class="ops-defs">
            <template v-for="(text, key) in effect.definition" :key="key">
              <dt>{{ key === 'exposure' ? '有效曝光' : key === 'click' ? '点击' : key === 'ctr' ? 'CTR' : key === 'retention' ? '保留期' : key }}</dt>
              <dd>{{ text }}</dd>
            </template>
          </dl>
          <p class="adm-note">样本：{{ fmtInt(effect.sample_size?.impressions) }} 次曝光 / {{ fmtInt(effect.sample_size?.clicks) }} 次点击。</p>
        </details>
      </template>

      <template v-else-if="legacy">
        <p class="adm-warnbox" role="status">
          新版统计接口暂不可用，当前显示兼容旧接口 <code>/recommendation-feedback</code> 的曝光与点击计数；
          漏斗与离线评估分区在兼容模式下不可用。
        </p>
        <div class="adm-kpis">
          <div><span>曝光</span><strong>{{ fmtInt(legacy.impressions) }}</strong><p>旧口径，与新接口分开说明</p></div>
          <div><span>点击</span><strong>{{ fmtInt(legacy.clicks) }}</strong><p>同批曝光归属的去重点击</p></div>
          <div><span>CTR</span><strong>{{ fmtRatio(legacy.ctr, 2) }}</strong><p>不是推荐准确率</p></div>
        </div>
        <div v-if="legacy.channels?.length" class="adm-tablewrap">
          <table>
            <caption class="ops-sr">兼容旧接口的频道曝光</caption>
            <thead><tr><th scope="col">频道</th><th scope="col" class="num">曝光</th><th scope="col" class="num">点击</th><th scope="col" class="num">CTR</th></tr></thead>
            <tbody>
              <tr v-for="row in legacy.channels" :key="row.channel">
                <td>{{ row.channel }}</td><td class="num">{{ fmtInt(row.impressions) }}</td>
                <td class="num">{{ fmtInt(row.clicks) }}</td><td class="num">{{ fmtRatio(row.ctr, 2) }}</td>
              </tr>
            </tbody>
          </table>
        </div>
        <p v-else class="adm-empty">旧接口在该窗口没有曝光记录。</p>
        <p class="adm-note">
          旧版“偏好重合度”排序指标样本：{{ fmtInt(legacy.ranking?.users) }} 个用户 / {{ fmtInt(legacy.ranking?.batches) }} 次榜单；
          该指标不是正式推荐准确率，本页不展示 Recall/NDCG 数值。
        </p>
      </template>

      <p v-else class="adm-empty">推荐效果数据暂时不可用，请手动刷新。</p>
    </template>

    <!-- 页 07：资源与兴趣匹配 -->
    <template v-else>
      <p v-if="interestError" class="adm-errobox" role="alert">{{ interestError }}
        <button type="button" class="adm-btn ops-inline-btn" @click="refresh">重新读取</button>
      </p>
      <p v-else-if="interestBusy && !interest" class="adm-state" role="status">正在读取供给与兴趣匹配…</p>

      <template v-else-if="interest">
        <div class="adm-toolbar">
          <div class="adm-field">
            <span>对比维度</span>
            <div class="adm-seg" role="group" aria-label="对比维度">
              <button v-for="item in INTEREST_DIMENSIONS" :key="item" type="button"
                      :aria-pressed="dimension === item" @click="setDimension(item)">{{ DIMENSION_LABELS[item] }}</button>
            </div>
          </div>
          <p class="ops-meta">
            <span>阈值：正向兴趣对 ≥{{ interest.thresholds?.min_interest_pairs ?? 3 }} 且曝光 ≥{{ interest.thresholds?.min_impressions ?? 10 }}</span>
          </p>
        </div>

        <p v-if="interestSuppressed" class="adm-warnbox" role="status">
          {{ interest.reason || '该维度首批未启用。' }} 供给、兴趣与曝光数值不返回，也不以 0 填充。
        </p>
        <template v-else>
          <p v-if="interest.status !== 'available'" class="adm-warnbox" role="status">
            {{ interest.reason || '正向兴趣对或有效曝光低于公开阈值。' }} 仅展示描述性计数，不下结论。
          </p>
          <p v-if="exposureDown" class="adm-warnbox" role="status">
            推荐曝光/点击表不可用：本页仅展示供给与兴趣，曝光与 CTR 列保持不可用。
          </p>

          <div class="adm-kpis">
            <div><span>在架电影</span><strong>{{ fmtInt(interest.supply.active_movies) }}</strong><p>供给份额分母；其中已分类 {{ fmtInt(interest.supply.classified_movies) }}，未分类 {{ fmtInt(interest.supply.unknown_movies) }}</p></div>
            <div><span>正向兴趣对</span><strong>{{ fmtInt(interest.interest.any_positive_pairs) }}</strong><p>收藏 / 想看 / 评分≥4 的去重 (user, movie) 对</p></div>
            <div><span>兴趣账户</span><strong>{{ fmtInt(interest.interest.users) }}</strong><p>窗口内产生正向兴趣的去重账户</p></div>
            <div><span>有效曝光</span><strong>{{ fmtInt(interest.exposure.impressions) }}</strong><p>其中可归属类别 {{ fmtInt(interest.exposure.attributable_impressions) }}</p></div>
          </div>

          <p class="ops-signals">
            <span>收藏 <b>{{ fmtInt(interest.interest.signal_counts?.favorite) }}</b></span>
            <span>想看 <b>{{ fmtInt(interest.interest.signal_counts?.want) }}</b></span>
            <span>高评分 <b>{{ fmtInt(interest.interest.signal_counts?.high_rating) }}</b></span>
            <span>兴趣对可归属类别 <b>{{ fmtInt(interest.interest.attributable_pairs) }}</b></span>
            <span>归属不到类别 <b>{{ fmtInt(interest.interest.unattributable_pairs) }}</b></span>
          </p>

          <h3>供给 / 兴趣 / 曝光对比 <small>只做描述性比较，不判断“短缺”或“推荐无效”</small></h3>
          <div v-if="interestRows.length" class="adm-tablewrap">
            <table class="ops-match-table">
              <caption class="ops-sr">各类别的供给份额、正向兴趣份额、曝光份额与 CTR</caption>
              <thead>
                <tr>
                  <th scope="col">类别</th>
                  <th scope="col">供给（影片 / 份额）</th>
                  <th scope="col">正向兴趣（对 / 份额）</th>
                  <th scope="col">曝光（份额）</th>
                  <th scope="col" class="num">点击 / CTR</th>
                  <th scope="col">样本状态</th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="row in interestRows" :key="row.key">
                  <th scope="row">{{ row.name }}</th>
                  <td>
                    <span class="ops-cell-value">{{ fmtInt(row.supply.movies) }} · {{ fmtRatio(row.supply.share) }}</span>
                    <i class="ops-sharebar" aria-hidden="true"><b :style="{width: row.widths.supply}"/></i>
                  </td>
                  <td>
                    <span class="ops-cell-value">{{ fmtInt(row.interest.positive_pairs) }} · {{ fmtRatio(row.interest.share) }}</span>
                    <i class="ops-sharebar" aria-hidden="true"><b :style="{width: row.widths.interest}"/></i>
                    <small>收藏 {{ row.interest.favorite_pairs }} / 想看 {{ row.interest.want_pairs }} / 高分 {{ row.interest.high_rating_pairs }} · {{ row.interest.users }} 账户</small>
                  </td>
                  <td>
                    <span class="ops-cell-value">{{ fmtInt(row.exposure.impressions) }} · {{ fmtRatio(row.exposure.share) }}</span>
                    <i class="ops-sharebar" aria-hidden="true"><b :style="{width: row.widths.exposure}"/></i>
                  </td>
                  <td class="num">{{ fmtInt(row.clicks.clicks) }} · {{ fmtRatio(row.clicks.ctr) }}</td>
                  <td><span class="ops-status" :class="'tone-' + statusMeta(row.status).tone">{{ statusMeta(row.status).label }}</span></td>
                </tr>
              </tbody>
            </table>
          </div>
          <p v-else class="adm-empty">该维度没有可对比的类别。</p>

          <p class="adm-note">
            {{ interest.definition?.notice || '仅描述性对比，不输出“短缺/推荐无效”等结论。' }}
            供给份额分母为全部在架影片（含未分类），曝光份额分母为可归属类别的有效曝光；多标签类别份额之和可超过 100%。
          </p>

          <details v-if="interest.definition" class="ops-disclosure">
            <summary>口径与样本</summary>
            <dl class="ops-defs">
              <template v-for="(text, key) in interest.definition" :key="key">
                <dt>{{ key === 'supply' ? '供给' : key === 'interest' ? '兴趣' : key === 'exposure' ? '曝光' : key === 'click' ? '点击' : key === 'notice' ? '说明' : key }}</dt>
                <dd>{{ text }}</dd>
              </template>
            </dl>
            <p class="adm-note">样本：正向兴趣对 {{ fmtInt(interest.sample_size?.positive_pairs) }} / 有效曝光 {{ fmtInt(interest.sample_size?.impressions) }}。</p>
          </details>
        </template>
      </template>

      <p v-else class="adm-empty">资源与兴趣匹配数据暂时不可用，请手动刷新。</p>
    </template>
  </section>
</template>

<style scoped>
.ops-meta{display:flex;flex-wrap:wrap;align-items:center;gap:8px 12px;margin:0;color:#8f8a80;font-size:11px}
.ops-status{display:inline-flex;align-items:center;gap:6px;border:1px solid rgba(241,236,226,.16);border-radius:999px;padding:2px 8px;font-size:10.5px;color:#a9a294;white-space:nowrap}
.ops-status::before{content:"";width:5px;height:5px;border-radius:50%;background:currentColor;opacity:.9}
.ops-status.tone-ok{color:#a9c4ae;border-color:rgba(156,191,166,.32)}
.ops-status.tone-warn{color:#dcc49b;border-color:rgba(200,164,106,.32)}
.ops-status.tone-bad{color:#e0a79c;border-color:rgba(232,160,148,.34)}
.ops-status.tone-muted{color:#a9a294}
.ops-inline-btn{margin-left:10px;padding:3px 9px;font-size:11px}
.ops-trends{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:12px;margin:0 0 4px}
.ops-trend{margin:0;border:1px solid rgba(241,236,226,.10);border-radius:10px;background:#191a1f;padding:10px 12px 6px;min-width:0}
.ops-trend figcaption{margin:0 0 6px;font-size:11px;color:#a9a294}
.ops-chart{height:150px;min-width:0}
.ops-grid2{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:16px;margin-top:6px}
.ops-block{border:1px solid rgba(241,236,226,.10);border-radius:10px;background:#191a1f;padding:4px 14px 12px;min-width:0}
.ops-block h3{margin-top:14px}
.ops-body{margin:4px 0 0;color:#c9c2b8;font-size:11.5px;line-height:1.7}
.ops-signals{display:flex;flex-wrap:wrap;gap:6px 18px;margin:0 0 4px;color:#a9a294;font-size:11px}
.ops-signals b{color:#e9e3d7;font-weight:500;margin-left:4px;font-variant-numeric:tabular-nums}
.ops-cell-value{font-variant-numeric:tabular-nums;color:#ded8cc}
.ops-sharebar{display:block;height:3px;margin-top:5px;background:#242030;border-radius:2px;overflow:hidden;max-width:180px}
.ops-sharebar b{display:block;height:100%;background:#9e81c6;border-radius:2px}
.ops-match-table td small{display:block;margin-top:4px;color:#8f8a80;font-size:10px;line-height:1.5}
.ops-match-table th[scope="row"]{font-weight:500;color:#efe9de;white-space:nowrap}
.ops-disclosure{margin-top:18px;border-top:1px solid rgba(241,236,226,.08);padding-top:12px}
.ops-disclosure summary{cursor:pointer;font-size:12px;color:#c9c2b8}
.ops-defs{display:grid;grid-template-columns:max-content minmax(0,1fr);gap:6px 14px;margin:12px 0 0;font-size:11.5px;line-height:1.7}
.ops-defs dt{color:#a9a294}
.ops-defs dd{margin:0;color:#c9c2b8}
.ops-sr{position:absolute;width:1px;height:1px;overflow:hidden;clip:rect(0,0,0,0);white-space:nowrap}
/* Legibility floor: functional micro-text stays at 11px or above. */
.ops-recommend :is(.ops-status, .ops-meta, .ops-signals, .ops-match-table td small, .ops-inline-btn){font-size:11px}
.ops-recommend .ops-body {font-size: 12px}
.ops-recommend .ops-signals {font-size: 11.5px}
.ops-recommend .ops-meta {font-size: 11.5px}
.ops-recommend :is(button,summary):focus-visible{outline:2px solid #c8a46a;outline-offset:2px}
@media (max-width: 1100px){
  .ops-trends,.ops-grid2{grid-template-columns:minmax(0,1fr)}
}
</style>