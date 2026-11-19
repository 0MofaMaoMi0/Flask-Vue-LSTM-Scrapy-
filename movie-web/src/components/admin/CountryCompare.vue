<script setup lang="ts">
import {computed, onBeforeUnmount, onMounted, ref, watch} from 'vue'
import {countryName} from '../../lib/globe/countryDisplay.js'
import {ISO2_TO_ISO3} from '../../lib/globe/iso2to3.js'
import {formatUtc} from './adminContext'
import type {AdminQuery} from './adminContext'
import {
  coverageOfCountry, fmtDecimal, fmtNumber, fmtPercent, fmtScore,
  loadAnalytics, type AnalyticsPayload, type DeepCountry, type DetailMovie,
} from './analytics'
import MovieCard from './MovieCard.vue'
import './admin-ui.css'

const props = defineProps<{
  compare: string[]
  filters: Pick<AdminQuery, 'genre' | 'decade' | 'yearFrom' | 'yearTo' | 'minVotes'>
}>()
const emit = defineEmits<{
  back: []
  country: [iso2: string]
  add: [iso2: string]
  remove: [iso2: string]
  filters: [value: Partial<AdminQuery>]
  openMovie: [id: number]
}>()
const payload = ref<AnalyticsPayload | null>(null), busy = ref(true), error = ref('')
const mode = ref<'count' | 'share'>('count')
const overlapOpen = ref(false), overlapCodes = ref<string[]>([]), overlapItems = ref<DetailMovie[]>([])
const overlapTotal = ref(0), overlapOffset = ref(0), overlapBusy = ref(false), overlapError = ref('')
const OVERLAP_PAGE = 20
let overlapController: AbortController | null = null
const overlapTable = ref<HTMLElement | null>(null)
const countrySamples = computed(() => new Map((payload.value?.comparison?.country_samples || []).map(item => [item.country_iso2, item])))
function sampleFor(code: string) { return countrySamples.value.get(code) || null }
async function showOverlap() {
  await openOverlap(props.compare)
}
async function openOverlap(codes: string[]) {
  overlapCodes.value = codes.slice(0, 3)
  overlapOffset.value = 0
  overlapOpen.value = true
  await loadOverlap()
  overlapTable.value?.scrollIntoView({behavior: 'smooth', block: 'center'})
}
async function loadOverlap() {
  if (!overlapCodes.value.length) return
  overlapController?.abort()
  const current = new AbortController()
  overlapController = current
  overlapBusy.value = true; overlapError.value = ''
  try {
    const data = await loadAnalytics({
      countries: props.compare,
      overlap: overlapCodes.value,
      genre: props.filters.genre || undefined,
      decade: props.filters.decade || undefined,
      yearFrom: props.filters.yearFrom,
      yearTo: props.filters.yearTo,
      minVotes: props.filters.minVotes,
      limit: OVERLAP_PAGE, offset: overlapOffset.value, top: 1, countryLimit: 1,
    }, current.signal)
    overlapItems.value = data.details?.items || []
    overlapTotal.value = data.details?.total || 0
  } catch (e) {
    if ((e as Error).name !== 'AbortError') overlapError.value = (e as Error).message
  } finally {
    if (overlapController === current) overlapBusy.value = false
  }
}
function overlapPage(delta: number) {
  const next = Math.max(0, overlapOffset.value + delta * OVERLAP_PAGE)
  if (next === overlapOffset.value) return
  overlapOffset.value = next
  loadOverlap()
}
function closeOverlap() {
  overlapOpen.value = false
  overlapController?.abort()
}
const controller = ref<AbortController | null>(null)
const countries = computed(() => payload.value?.comparison?.countries || props.compare)
const rows = computed<DeepCountry[]>(() => payload.value?.country_analysis?.rows || [])
const comparison = computed(() => payload.value?.comparison)
const snapshot = computed(() => formatUtc(payload.value?.snapshot.latest_fetched_at))
const zh = (code: string) => countryName((ISO2_TO_ISO3 as Record<string, string>)[code] || code, code)
const available = computed(() => (payload.value?.countries.compact.items || [])
  .filter(item => item.movie_count_distinct > 0 && !props.compare.includes(item.country_iso2))
  .slice(0, 200))
const pick = ref('')
const filterSummary = computed(() => {
  const parts: string[] = []
  if (props.filters.genre) parts.push('类型：' + props.filters.genre)
  if (props.filters.decade) parts.push('年代：' + props.filters.decade)
  if (props.filters.yearFrom != null) parts.push('年份 ≥' + props.filters.yearFrom)
  if (props.filters.yearTo != null) parts.push('年份 ≤' + props.filters.yearTo)
  parts.push('票数门槛 ≥' + props.filters.minVotes)
  return parts
})

async function load() {
  if (props.compare.length < 2) { payload.value = null; busy.value = false; return }
  controller.value?.abort()
  const current = new AbortController()
  controller.value = current
  busy.value = true; error.value = ''
  try {
    payload.value = await loadAnalytics({
      countries: props.compare,
      genre: props.filters.genre || undefined,
      decade: props.filters.decade || undefined,
      yearFrom: props.filters.yearFrom,
      yearTo: props.filters.yearTo,
      minVotes: props.filters.minVotes,
      top: 10, limit: 20,
    }, current.signal)
    overlapOpen.value = false
  } catch (e) {
    if ((e as Error).name !== 'AbortError') error.value = (e as Error).message
  } finally {
    if (controller.value === current) busy.value = false
  }
}
function share(count: number, row: DeepCountry): number | null {
  return row.movie_count_distinct ? count / row.movie_count_distinct : null
}
function displayCount(count: number, row: DeepCountry): string {
  return mode.value === 'count' ? fmtNumber(count) : (share(count, row) == null ? '—' : fmtPercent(share(count, row)))
}
function addSelected() {
  if (!pick.value) return
  emit('add', pick.value)
  pick.value = ''
}
function togglePair(index: number) {
  const pair = comparison.value?.pairwise[index]
  if (pair) openOverlap(pair.countries)
}
function sampleWarning(row: DeepCountry): boolean {
  return row.tmdb_avg_rating.valid_samples > 0 && row.tmdb_avg_rating.valid_samples < 20
}
watch(() => [props.compare.join(','), props.filters.genre, props.filters.decade, props.filters.yearFrom, props.filters.yearTo, props.filters.minVotes], load)
onMounted(load)
onBeforeUnmount(() => { controller.value?.abort(); overlapController?.abort() })
</script>

<template>
  <section class="compare-view" aria-labelledby="compare-title">
    <header class="compare-head">
      <div>
        <h2 id="compare-title">电影资源结构比较 <small>{{ countries.length }} 国</small></h2>
        <p class="compare-meta">{{ filterSummary.join(' · ') }} · 快照 {{ snapshot }} · 同一筛选与同一评分口径</p>
      </div>
      <div class="adm-actions" style="margin:0">
        <button type="button" class="adm-btn" @click="emit('back')">← 返回地图</button>
        <div class="adm-seg" role="group" aria-label="绝对数量或结构占比">
          <button type="button" :aria-pressed="mode==='count'" @click="mode='count'">绝对数量</button>
          <button type="button" :aria-pressed="mode==='share'" @click="mode='share'">结构占比</button>
        </div>
      </div>
    </header>

    <section class="adm-panel">
      <h2>选择国家 <small>2–3 国；同一国家不可重复</small></h2>
      <div class="adm-chips">
        <span v-for="code in compare" :key="code" class="adm-chip">
          {{ zh(code) }} <small>{{ code }}</small>
          <button type="button" :aria-label="'移除 ' + zh(code)" @click="emit('remove', code)">✕</button>
        </span>
      </div>
      <div class="adm-toolbar">
        <label class="adm-field"><span>加入国家</span>
          <select v-model="pick">
            <option value="">选择国家…</option>
            <option v-for="item in available" :key="item.country_iso2" :value="item.country_iso2">{{ item.country_iso2 }} · {{ item.country_name }}（{{ item.movie_count_distinct }} 部）</option>
          </select>
        </label>
        <button type="button" class="adm-btn primary" :disabled="!pick || compare.length >= 3" @click="addSelected">加入比较</button>
        <button type="button" class="adm-btn" @click="emit('back')">更换筛选</button>
      </div>
      <p class="adm-note">比较最多 3 国；合拍片可同时存在于多个国家集合，因此跨国重合数单独计算，绝不把各国数量直接相加当合集规模。</p>
    </section>

    <p v-if="error" class="adm-errobox" role="alert">{{ error }} <button type="button" class="link-btn" @click="load">重试</button></p>
    <p v-else-if="compare.length < 2" class="adm-empty">至少选择 2 个国家后才能比较。</p>
    <p v-else-if="busy && !rows.length" class="adm-state">正在读取国家比较数据…</p>

    <template v-if="comparison && rows.length">
      <section class="adm-panel" aria-labelledby="compare-overlap">
        <h2 id="compare-overlap">重合与规模 <small>去重口径 · {{ countries.length }} 国</small></h2>
        <div class="adm-kpis">
          <div><span>并集电影数（去重）</span><strong>{{ fmtNumber(comparison.union_movie_count) }}<small>部</small></strong><p>不是各国数量相加</p></div>
          <div><span>跨国重合电影数</span><strong>{{ fmtNumber(comparison.overlap_movie_count) }}<small>部</small></strong><p>同时属于至少 2 国</p><button type="button" class="link-btn" @click="showOverlap">查看全部重合影片 ↓</button></div>
          <div><span>全组共同重合</span><strong>{{ comparison.all_countries_intersection_movie_count == null ? '—' : fmtNumber(comparison.all_countries_intersection_movie_count) }}<small v-if="comparison.all_countries_intersection_movie_count != null">部</small></strong><p>同时属于所比全部国家</p><button v-if="comparison.all_countries_intersection_movie_count" type="button" class="link-btn" @click="openOverlap(countries)">查看共同影片 ↓</button></div>
          <div><span>重合占并集</span><strong>{{ comparison.overlap_ratio_of_union == null ? '—' : fmtPercent(comparison.overlap_ratio_of_union) }}</strong><p>合拍/多地制作导致</p></div>
        </div>
        <div class="adm-tablewrap">
          <table>
            <thead><tr><th>国家对</th><th class="num">交集</th><th class="num">并集</th><th class="num">Jaccard</th><th>重合明细</th></tr></thead>
            <tbody>
              <tr v-for="(pair, index) in comparison.pairwise" :key="pair.countries.join('-')">
                <td>{{ pair.countries.map(code => zh(code)).join(' × ') }}</td>
                <td class="num">{{ pair.intersection_movie_count }}</td>
                <td class="num">{{ pair.union_movie_count }}</td>
                <td class="num">{{ pair.jaccard == null ? '—' : pair.jaccard.toFixed(3) }}</td>
                <td><button type="button" class="link-btn" :disabled="!pair.intersection_movie_count" @click="togglePair(index)">查看重合影片</button></td>
              </tr>
            </tbody>
          </table>
        </div>
        <div v-if="overlapOpen" ref="overlapTable" class="pair-detail overlap-detail">
          <div class="overlap-head">
            <div>
              <p class="adm-sub">重合明细：{{ overlapCodes.map(code => zh(code)).join(' × ') }}</p>
              <p class="adm-note">按当前筛选与共同国家分页返回；共 {{ fmtNumber(overlapTotal) }} 部，行点击进入既有电影详情。</p>
            </div>
            <button type="button" class="link-btn" @click="closeOverlap">收起</button>
          </div>
          <p v-if="overlapError" class="adm-errobox" role="alert">{{ overlapError }} <button type="button" class="link-btn" @click="loadOverlap">重试</button></p>
          <p v-else-if="overlapBusy && !overlapItems.length" class="adm-state">正在读取重合影片…</p>
          <div v-else-if="overlapItems.length" class="adm-tablewrap">
            <table>
              <thead><tr><th>片名</th><th>年份</th><th>共同国家</th><th class="num">TMDB 评分</th><th class="num">票数</th></tr></thead>
              <tbody>
                <tr v-for="movie in overlapItems" :key="movie.movie_id">
                  <td><button type="button" class="link-btn" @click="emit('openMovie', movie.movie_id)">{{ movie.title }}</button></td>
                  <td>{{ movie.release_year || '—' }}</td>
                  <td>{{ movie.countries.map(code => zh(code)).join(' / ') || '—' }}</td>
                  <td class="num">{{ fmtScore(movie.vote_average) }}</td>
                  <td class="num">{{ fmtNumber(movie.vote_count) }}</td>
                </tr>
              </tbody>
            </table>
          </div>
          <p v-else class="adm-empty">该组合没有重合影片。</p>
          <div v-if="overlapItems.length" class="overlap-pager">
            <button type="button" class="adm-btn" :disabled="overlapOffset === 0 || overlapBusy" @click="overlapPage(-1)">上一页</button>
            <span>第 {{ Math.floor(overlapOffset / OVERLAP_PAGE) + 1 }} 页 · 每页 {{ OVERLAP_PAGE }} 部</span>
            <button type="button" class="adm-btn" :disabled="overlapOffset + OVERLAP_PAGE >= overlapTotal || overlapBusy" @click="overlapPage(1)">下一页</button>
          </div>
        </div>
      </section>

      <section class="adm-panel">
        <h2>国家指标对照 <small>评分附有效样本数</small></h2>
        <div class="compare-grid">
          <article v-for="row in rows" :key="row.country_iso2" class="country-card">
            <header>
              <div><h3>{{ zh(row.country_iso2) }} <small>{{ row.country_iso2 }}</small></h3><p>{{ row.country_name }}</p></div>
              <button type="button" class="link-btn" @click="emit('country', row.country_iso2)">专题 →</button>
            </header>
            <div class="adm-kpis small">
              <div><span>唯一影片</span><strong>{{ fmtNumber(sampleFor(row.country_iso2)?.movie_count_distinct ?? row.movie_count_distinct) }}</strong><p>占筛选全库 {{ row.share_of_filtered_catalog == null ? '—' : fmtPercent(row.share_of_filtered_catalog) }}</p></div>
              <div><span>有效评分样本</span><strong>{{ fmtNumber(sampleFor(row.country_iso2)?.tmdb_avg_rating.valid_samples ?? row.tmdb_avg_rating.valid_samples) }}</strong><p>票数 ≥{{ row.tmdb_avg_rating.min_votes }}</p></div>
              <div><span>平均 TMDB 评分</span><strong>{{ fmtScore(sampleFor(row.country_iso2)?.tmdb_avg_rating.unweighted_mean ?? row.tmdb_avg_rating.unweighted_mean) }}</strong><p>简单平均（每片等权）</p></div>
              <div><span>海报覆盖</span><strong>{{ coverageOfCountry(row) == null ? '—' : fmtPercent(coverageOfCountry(row)) }}</strong><p>{{ coverageOfCountry(row) == null ? '暂无分国统计' : '按本国影片计算' }}</p></div>
            </div>
            <p v-if="sampleWarning(row)" class="adm-warnbox">小样本警示：有效评分样本仅 {{ row.tmdb_avg_rating.valid_samples }} 部，均值不稳定，不宜据此下结论。</p>
            <p v-if="!row.tmdb_avg_rating.valid_samples" class="adm-empty">暂无有效评分样本，不显示 0 分。</p>
            <h3>类型 TOP5 <small>多标签 · {{ mode === 'count' ? '作品数' : '占本国影片比' }}</small></h3>
            <div class="adm-bars">
              <div v-for="genre in row.top_genres.slice(0, 5)" :key="genre.genre" class="adm-bar">
                <span>{{ genre.genre }}</span><i><b :style="{width: (genre.count / Math.max(1, row.top_genres[0]?.count || 1) * 100) + '%'}"/></i><b>{{ displayCount(genre.count, row) }}</b>
              </div>
            </div>
            <p v-if="!row.top_genres.length" class="adm-empty">暂无类型资料。</p>
            <h3>年代结构 <small>{{ mode === 'count' ? '作品数' : '占本国影片比' }}</small></h3>
            <div class="adm-bars">
              <div v-for="item in row.decade_distribution.filter(d => d.count > 0)" :key="String(item.decade)" class="adm-bar">
                <span>{{ item.name }}</span><i><b :style="{width: (item.count / Math.max(1, ...row.decade_distribution.map(d => d.count)) * 100) + '%'}"/></i><b>{{ displayCount(item.count, row) }}</b>
              </div>
            </div>
            <h3>关键词 TOP5 / 制作公司 TOP5</h3>
            <div class="adm-inline vertical">
              <span>关键词 <b>{{ row.keywords.slice(0, 5).map(item => item.name).join('、') || '暂无资料' }}</b></span>
              <span>制作公司 <b>{{ row.companies.slice(0, 5).map(item => item.name).join('、') || '暂无资料' }}</b></span>
            </div>
            <h3>代表影片 TOP3 <small>按投票数</small></h3>
            <div class="adm-poster-row compact">
              <MovieCard v-for="(movie, index) in row.top_movies.slice(0, 3)" :key="movie.movie_id" :movie="movie" :rank="index + 1" @open="emit('openMovie', $event)"/>
            </div>
          </article>
        </div>
        <p class="adm-note">差异为描述性比较，不自动宣称因果或真实产业实力；评分差异必须结合有效样本数阅读。类型/年代多标签计数之和可能超过影片数，占比分母为该国在当前筛选下的去重影片数。</p>
      </section>
    </template>

    <section v-else-if="!busy && !error && compare.length >= 2" class="adm-panel">
      <p class="adm-empty">当前筛选下所选国家没有可比较的影片；可放宽类型/年代/票数门槛。</p>
    </section>
  </section>
</template>

<style scoped>
.compare-view{min-width:0}
.compare-head{display:flex;justify-content:space-between;align-items:flex-end;gap:16px;flex-wrap:wrap;margin:2px 0 16px}
.compare-head h2{margin:2px 0 4px;font-family:var(--serif);font-size:26px;font-weight:500;color:#f3eee4}
.compare-meta{margin:0;font-size:10.5px;color:#8f8a80;line-height:1.7}
.compare-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(300px,1fr));gap:14px}
.country-card{border:1px solid rgba(241,236,226,.10);border-radius:10px;background:#191a1f;padding:14px 15px;min-width:0}
.country-card header{display:flex;justify-content:space-between;align-items:flex-start;gap:10px}
.country-card h3{font-size:15px;margin:0;color:#efe9de;font-weight:500}
.country-card h3 small{font-size:10px;color:#a9a294;margin-left:5px;letter-spacing:.05em}
.country-card header p{margin:3px 0 0;font-size:10px;color:#8f8a80}
.country-card .adm-kpis{grid-template-columns:repeat(2,minmax(0,1fr))}
.country-card .adm-kpis>div{border:0;background:transparent;padding:0}
.adm-kpis.small strong{font-size:18px}
.adm-poster-row.compact{grid-template-columns:repeat(3,minmax(0,1fr))}
.adm-inline.vertical{flex-direction:column;gap:6px;align-items:flex-start}
.pair-detail{margin-top:12px;border-top:1px solid rgba(241,236,226,.08);padding-top:10px}
.link-btn{border:0;background:transparent;color:#dcc49b;font:inherit;font-size:11.5px;cursor:pointer;padding:0;text-decoration:underline;text-underline-offset:2px}
.link-btn:hover:not(:disabled){color:#fff}
.link-btn:disabled{opacity:.45;cursor:default}
.overlap-head{display:flex;justify-content:space-between;align-items:flex-start;gap:12px}
.overlap-head .adm-note{margin:2px 0 8px}
.overlap-pager{display:flex;align-items:center;gap:12px;flex-wrap:wrap;margin-top:10px;font-size:10.5px;color:#9e8fae}
.overlap-pager .adm-btn{padding:5px 10px;font-size:11px}
</style>