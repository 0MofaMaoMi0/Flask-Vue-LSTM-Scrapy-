<script setup lang="ts">
import {computed, onBeforeUnmount, onMounted, ref, watch} from 'vue'
import {countryName, countryEnglish} from '../../lib/globe/countryDisplay.js'
import {ISO2_TO_ISO3} from '../../lib/globe/iso2to3.js'
import {formatUtc} from './adminContext'
import type {AdminQuery} from './adminContext'
import {
  coverageOfCountry, fmtDecimal, fmtNumber, fmtPercent, fmtScore, loadAnalytics,
  DETAIL_SORTS, MISSING_TOPICS,
  type AnalyticsPayload, type DeepCountry, type DetailMovie,
} from './analytics'
import AdminScatter from './AdminScatter.vue'
import MovieCard from './MovieCard.vue'
import './admin-ui.css'

const props = defineProps<{
  country: string
  filters: Pick<AdminQuery, 'genre' | 'decade' | 'yearFrom' | 'yearTo' | 'minVotes'>
  compare: string[]
  detailOffset?: number
}>()
const emit = defineEmits<{
  back: []
  filters: [value: Partial<AdminQuery>]
  'update:detail-offset': [offset: number]
  compare: [iso2: string]
  openMovie: [id: number]
  quality: []
  compareView: []
}>()
const payload = ref<AnalyticsPayload | null>(null), busy = ref(true), error = ref(''), offset = ref(Math.max(0, props.detailOffset ?? 0))
const controller = ref<AbortController | null>(null)
const PAGE = 20
const iso2 = computed(() => props.country)
const row = computed<DeepCountry | null>(() => payload.value?.country_analysis?.rows?.[0] || null)
const details = computed(() => payload.value?.details)
const zh = (code: string) => countryName((ISO2_TO_ISO3 as Record<string, string>)[code] || code, code)
const english = (code: string) => countryEnglish((ISO2_TO_ISO3 as Record<string, string>)[code] || code, code)
const snapshot = computed(() => formatUtc(payload.value?.snapshot.latest_fetched_at))
const oldest = computed(() => formatUtc(payload.value?.snapshot.earliest_fetched_at))

const genreInput = ref(props.filters.genre)
const decadeInput = ref(props.filters.decade)
const yearFromInput = ref(props.filters.yearFrom == null ? '' : String(props.filters.yearFrom))
const yearToInput = ref(props.filters.yearTo == null ? '' : String(props.filters.yearTo))
const minVotesInput = ref(String(props.filters.minVotes))
const keywordFilter = ref<{id: number; name: string} | null>(null)
const companyFilter = ref<{id: number; name: string} | null>(null)
const missingInput = ref<string[]>([])
const sortInput = ref('')
const orderInput = ref<'asc' | 'desc'>('desc')
const MISSING_LABELS: Record<string, string> = {
  countries: '制作国家', keywords: '关键词', companies: '制作公司', credits: '演职员',
  poster: '海报', overview: '简介', releases: '地区上映', spoken_languages: '对白语言',
}
const REASON_LABELS: Record<string, string> = {
  EMPTY: '上游空值', FAILED: '采集失败', MISSING: '来源缺失', NOT_FETCHED: '未采集',
}
const detailChips = computed(() => {
  const parts: string[] = []
  if (keywordFilter.value) parts.push('关键词：' + keywordFilter.value.name)
  if (companyFilter.value) parts.push('公司：' + companyFilter.value.name)
  if (missingInput.value.length) parts.push('缺失：' + missingInput.value.map(topic => MISSING_LABELS[topic] || topic).join(' / '))
  if (sortInput.value) parts.push('排序：' + sortLabel(sortInput.value) + ' ' + (orderInput.value === 'asc' ? '升序' : '降序'))
  return parts
})
function sortLabel(value: string): string {
  return ({title: '片名', year: '年份', rating: '评分', votes: '票数', popularity: '热度'} as Record<string, string>)[value] || value
}
function resetOffset() {
  if (offset.value !== 0) {
    offset.value = 0
    emit('update:detail-offset', 0)
  }
}
function pickKeyword(item: {keyword_id: number; name: string}) {
  keywordFilter.value = {id: item.keyword_id, name: item.name}
  resetOffset()
  load()
}
function pickCompany(item: {company_id: number; name: string}) {
  companyFilter.value = {id: item.company_id, name: item.name}
  resetOffset()
  load()
}
function clearDetailFilters() {
  keywordFilter.value = null
  companyFilter.value = null
  missingInput.value = []
  sortInput.value = ''
  orderInput.value = 'desc'
  resetOffset()
  load()
}
function toggleMissing(topic: string) {
  const current = missingInput.value
  missingInput.value = current.includes(topic) ? current.filter(item => item !== topic) : [...current, topic]
  resetOffset()
  load()
}
function detailMissing(movie: DetailMovie): {topic: string; reason: string; label: string}[] {
  const fields = movie.missing_fields || Object.entries(movie.missing_reasons || {}).map(([topic]) => topic)
  return fields.map(topic => {
    const reason = movie.missing_reasons?.[topic] || 'MISSING'
    return {topic, reason, label: MISSING_LABELS[topic] || topic}
  })
}

function numberOrNull(value: string): number | null {
  const text = value.trim()
  if (!text || !/^\d{1,4}$/.test(text)) return null
  const parsed = Number(text)
  return parsed >= 1800 && parsed <= 2199 ? parsed : null
}
function applyFilters() {
  emit('filters', {
    genre: genreInput.value.trim(), decade: decadeInput.value, yearFrom: numberOrNull(yearFromInput.value),
    yearTo: numberOrNull(yearToInput.value), minVotes: Math.max(0, Math.min(1000000, Number(minVotesInput.value) || 0)),
    detailOffset: 0,
  })
}
function resetFilters() {
  genreInput.value = ''; decadeInput.value = ''; yearFromInput.value = ''; yearToInput.value = ''
  minVotesInput.value = '50'
  emit('filters', {genre: '', decade: '', yearFrom: null, yearTo: null, minVotes: 50, detailOffset: 0})
}
const activeFilters = computed(() => {
  const parts: string[] = []
  if (props.filters.genre) parts.push('类型：' + props.filters.genre)
  if (props.filters.decade) parts.push('年代：' + props.filters.decade)
  if (props.filters.yearFrom != null) parts.push('年份 ≥' + props.filters.yearFrom)
  if (props.filters.yearTo != null) parts.push('年份 ≤' + props.filters.yearTo)
  parts.push('票数门槛 ≥' + props.filters.minVotes)
  return parts
})
const inCompare = computed(() => props.compare.includes(iso2.value))
const compareFull = computed(() => props.compare.length >= 3 && !inCompare.value)

const topGenres = computed(() => (row.value?.top_genres || []).slice(0, 10))
const maxGenre = computed(() => Math.max(1, ...topGenres.value.map(item => item.count)))
const decades = computed(() => (row.value?.decade_distribution || []).filter(item => item.count > 0))
const maxDecade = computed(() => Math.max(1, ...decades.value.map(item => item.count)))
const matrix = computed(() => row.value?.genre_by_decade || [])
const matrixDecades = computed(() => {
  const set = new Set<number>()
  for (const line of matrix.value) for (const cell of line.decades) if (cell.decade != null) set.add(cell.decade)
  return [...set].sort((a, b) => a - b)
})
const matrixMax = computed(() => {
  let max = 0
  for (const line of matrix.value) for (const cell of line.decades) max = Math.max(max, cell.count)
  return max
})
function matrixValue(line: { decades: { decade: number | null; count: number }[] }, decade: number): number {
  return line.decades.find(cell => cell.decade === decade)?.count || 0
}
function heat(value: number): string {
  if (!value) return 'adm-heat-0'
  const ratio = matrixMax.value ? value / matrixMax.value : 0
  if (ratio > .75) return 'adm-heat-4'
  if (ratio > .5) return 'adm-heat-3'
  if (ratio > .25) return 'adm-heat-2'
  return 'adm-heat-1'
}
const histogram = computed(() => row.value?.rating_distribution.rating_histogram || [])
const maxHistogram = computed(() => Math.max(1, ...histogram.value.map(item => item.count)))
const quantiles = computed(() => row.value?.rating_distribution.vote_count_quantiles)
const popQuantiles = computed(() => row.value?.rating_distribution.popularity_quantiles)
const topRated = computed(() => (row.value?.top_rated_movies || []).slice(0, 5))
const keywords = computed(() => (row.value?.keywords || []).slice(0, 10))
const companies = computed(() => (row.value?.companies || []).slice(0, 10))
const directors = computed(() => (row.value?.credits.directors || []).slice(0, 8))
const cast = computed(() => (row.value?.credits.cast || []).slice(0, 8))
const release = computed(() => row.value?.release_window)
const posterCoverage = computed(() => coverageOfCountry(row.value))
const scatterPoints = computed(() => (details.value?.items || [])
  .filter(movie => movie.rating_sample_valid && movie.vote_average != null && movie.popularity != null)
  .map(movie => ({
    x: movie.vote_average as number, y: movie.popularity as number,
    label: movie.title, meta: movie.release_year ? String(movie.release_year) : '',
  })))
async function load() {
  if (!/^[A-Z]{2}$/.test(iso2.value)) return
  controller.value?.abort()
  const current = new AbortController()
  controller.value = current
  busy.value = true; error.value = ''
  try {
    payload.value = await loadAnalytics({
      countries: [iso2.value],
      detailCountry: iso2.value,
      genre: props.filters.genre || undefined,
      decade: props.filters.decade || undefined,
      yearFrom: props.filters.yearFrom,
      yearTo: props.filters.yearTo,
      minVotes: props.filters.minVotes,
      keyword: keywordFilter.value?.id ?? null,
      company: companyFilter.value?.id ?? null,
      missing: missingInput.value,
      sort: sortInput.value || null,
      order: orderInput.value,
      top: 10, limit: PAGE, offset: offset.value,
    }, current.signal)
  } catch (e) {
    if ((e as Error).name !== 'AbortError') error.value = (e as Error).message
  } finally {
    if (controller.value === current) busy.value = false
  }
}
function page(delta: number) {
  const next = Math.max(0, offset.value + delta * PAGE)
  if (next === offset.value) return
  offset.value = next
  emit('update:detail-offset', next)
  load()
}
watch(() => [props.country, props.filters.genre, props.filters.decade, props.filters.yearFrom, props.filters.yearTo, props.filters.minVotes], () => {
  offset.value = Math.max(0, props.detailOffset ?? 0)
  load()
})
watch(() => props.detailOffset, value => {
  const next = Math.max(0, value ?? 0)
  if (next === offset.value) return
  offset.value = next
  load()
})
watch(() => props.filters, value => {
  genreInput.value = value.genre; decadeInput.value = value.decade
  yearFromInput.value = value.yearFrom == null ? '' : String(value.yearFrom)
  yearToInput.value = value.yearTo == null ? '' : String(value.yearTo)
  minVotesInput.value = String(value.minVotes)
})
onMounted(load)
onBeforeUnmount(() => controller.value?.abort())
</script>

<template>
  <section class="deep-view" aria-labelledby="deep-title">
    <header class="deep-head">
      <div>
        <h2 id="deep-title">{{ zh(iso2) }}<span class="deep-en">{{ row?.country_name || english(iso2) }}</span></h2>
        <p class="deep-meta">TMDB 补采国家专题 · 快照 {{ snapshot }} · 最早采集 {{ oldest }}</p>
      </div>
      <div class="adm-actions" style="margin:0">
        <button type="button" class="adm-btn" @click="emit('back')">← 返回地图</button>
        <button type="button" class="adm-btn" :disabled="compareFull" @click="emit('compare', iso2)">{{ inCompare ? '从对比中移除' : '加入国家对比' }}</button>
        <button type="button" class="adm-btn primary" @click="emit('compareView')">打开三国比较</button>
      </div>
    </header>

    <div class="adm-panel filter-panel">
      <div class="adm-toolbar">
        <label class="adm-field"><span>类型（多标签，精确名或 ID）</span><input v-model="genreInput" type="text" placeholder="如 Animation" @keyup.enter="applyFilters"></label>
        <label class="adm-field"><span>年代</span>
          <select v-model="decadeInput">
            <option value="">全部年代</option>
            <option v-for="value in ['1950','1960','1970','1980','1990','2000','2010','2020']" :key="value" :value="value">{{ value }} 年代</option>
          </select>
        </label>
        <label class="adm-field"><span>年份从</span><input v-model="yearFromInput" type="text" inputmode="numeric" placeholder="1900" @keyup.enter="applyFilters"></label>
        <label class="adm-field"><span>年份到</span><input v-model="yearToInput" type="text" inputmode="numeric" placeholder="2026" @keyup.enter="applyFilters"></label>
        <label class="adm-field"><span>TMDB 票数门槛</span><input v-model="minVotesInput" type="text" inputmode="numeric" @keyup.enter="applyFilters"></label>
        <button type="button" class="adm-btn primary" @click="applyFilters">应用筛选</button>
        <button type="button" class="adm-btn" @click="resetFilters">一键重置</button>
      </div>
      <div class="adm-chips">
        <span v-for="part in activeFilters" :key="part" class="adm-chip">{{ part }}</span>
      </div>
      <div class="detail-filters">
        <div class="detail-filter-line">
          <span class="detail-label">明细条件（仅 F）</span>
          <label class="adm-field inline"><span>排序</span>
            <select v-model="sortInput" @change="resetOffset(); load()">
              <option value="">默认 movie_id</option>
              <option v-for="value in DETAIL_SORTS" :key="value" :value="value">{{ sortLabel(value) }}</option>
            </select>
          </label>
          <label class="adm-field inline"><span>方向</span>
            <select v-model="orderInput" :disabled="!sortInput" @change="resetOffset(); load()">
              <option value="desc">降序</option><option value="asc">升序</option>
            </select>
          </label>
          <button v-if="detailChips.length" type="button" class="adm-btn" @click="clearDetailFilters">清除明细条件</button>
        </div>
        <div class="missing-picks" role="group" aria-label="按资料缺失过滤明细">
          <span class="detail-label">缺失任一项</span>
          <label v-for="topic in MISSING_TOPICS" :key="topic" class="missing-pick" :class="{on: missingInput.includes(topic)}">
            <input type="checkbox" :checked="missingInput.includes(topic)" @change="toggleMissing(topic)">
            {{ MISSING_LABELS[topic] }}
          </label>
        </div>
        <div v-if="detailChips.length" class="adm-chips">
          <span v-for="part in detailChips" :key="part" class="adm-chip detail-chip">{{ part }}</span>
        </div>
      </div>
      <p class="adm-note">一套筛选联动 A–F 全部模块；筛选作用于制作国家、类型、上映年份范围与评分门槛，年代筛选过滤的是影片发行年份，不是统计观察日期。</p>
    </div>

    <p v-if="error" class="adm-errobox" role="alert">{{ error }} <button type="button" class="link-btn" @click="load">重试</button></p>
    <p v-else-if="busy && !row" class="adm-state">正在读取 {{ zh(iso2) }} 的深度统计…</p>

    <template v-if="row">
      <section class="adm-panel" aria-labelledby="deep-a">
        <h2 id="deep-a">A. 国家总体 <small>当前在架唯一影片与资料覆盖</small></h2>
        <div class="adm-kpis">
          <div><span>唯一影片数</span><strong>{{ fmtNumber(row.movie_count_distinct) }}<small>部</small></strong><p>按 movie_id 去重，合拍片只计一次</p></div>
          <div><span>占筛选全库</span><strong>{{ row.share_of_filtered_catalog == null ? '—' : fmtPercent(row.share_of_filtered_catalog) }}</strong><p>分母 {{ fmtNumber(payload?.country_analysis?.denominator) }} 部（含国家未知）</p></div>
          <div><span>有效平均 TMDB 评分</span><strong>{{ fmtDecimal(row.tmdb_avg_rating.unweighted_mean) }}</strong><p>{{ fmtNumber(row.tmdb_avg_rating.valid_samples) }} 个达标样本 · 简单平均</p></div>
          <div><span>海报资料覆盖</span><strong>{{ posterCoverage == null ? '暂未拆分' : fmtPercent(posterCoverage) }}</strong><p>{{ posterCoverage == null ? '按全库统计见「数据质量」' : '按该国影片口径' }}</p></div>
          <div><span>演职员资料</span><strong>{{ row.credits.status === 'PRESENT' ? fmtNumber(row.credits.movies_with_credits) + ' 部' : '暂无资料' }}</strong><p>主题状态 {{ row.credits.status }}</p></div>
          <div><span>多国合拍比例</span><strong>暂未提供</strong><p>跨国合拍重合见「三国比较」</p></div>
        </div>
        <p class="adm-note">“收录/补采时间”使用采集时间字段（UTC），与“上映年份”严格区分；这里只描述库内结构，不代表该国真实产量。</p>
      </section>

      <div class="adm-two-col">
        <section class="adm-panel" aria-labelledby="deep-b1">
          <h2 id="deep-b1">B. 类型 TOP10 <small>多标签，合计可超过影片数</small></h2>
          <div v-if="topGenres.length" class="adm-bars">
            <div v-for="item in topGenres" :key="item.genre" class="adm-bar">
              <span>{{ item.genre }}</span><i><b :style="{width: (item.count / maxGenre * 100) + '%'}"/></i><b>{{ item.count }}</b>
            </div>
          </div>
          <p v-else class="adm-empty">当前筛选下暂无类型资料。</p>
        </section>
        <section class="adm-panel" aria-labelledby="deep-b2">
          <h2 id="deep-b2">B. 上映年代结构 <small>库内结构，非真实产量</small></h2>
          <div v-if="decades.length" class="adm-bars">
            <div v-for="item in decades" :key="String(item.decade)" class="adm-bar">
              <span>{{ item.name }}</span><i><b :style="{width: (item.count / maxDecade * 100) + '%'}"/></i><b>{{ item.count }}</b>
            </div>
          </div>
          <p v-else class="adm-empty">当前筛选下暂无年代资料。</p>
        </section>
      </div>

      <section class="adm-panel" aria-labelledby="deep-b3">
        <h2 id="deep-b3">B. 类型 × 年代交叉矩阵 <small>行=类型，列=年代，单元格为影片数</small></h2>
        <div v-if="matrix.length" class="adm-matrix">
          <table>
            <thead><tr><th>类型</th><th v-for="decade in matrixDecades" :key="decade" class="num">{{ decade }}s</th><th class="num">合计</th></tr></thead>
            <tbody>
              <tr v-for="line in matrix" :key="line.genre">
                <td>{{ line.genre }}</td>
                <td v-for="decade in matrixDecades" :key="decade" class="cell" :class="heat(matrixValue(line, decade))">{{ matrixValue(line, decade) || '·' }}</td>
                <td class="num">{{ line.total }}</td>
              </tr>
            </tbody>
          </table>
        </div>
        <p v-else class="adm-empty">当前筛选下暂无可交叉统计的类型与年代资料。</p>
        <p class="adm-note">仅交叉统计已有类型事实的影片；无类型资料影片不进入矩阵，分母见顶部影片数。</p>
      </section>

      <section class="adm-panel" aria-labelledby="deep-c">
        <h2 id="deep-c">C. 口碑与热度 <small>TMDB 评分与热度指数，非站内评分 / 播放量</small></h2>
        <div class="adm-two-col">
          <div>
            <h3>评分区间分布 <small>仅有效样本</small></h3>
            <div v-if="histogram.length" class="adm-bars">
              <div v-for="item in histogram" :key="item.name" class="adm-bar">
                <span>{{ item.name }}</span><i><b :style="{width: (item.count / maxHistogram * 100) + '%'}"/></i><b>{{ item.count }}</b>
              </div>
            </div>
            <p v-else class="adm-empty">暂无有效评分样本。</p>
          </div>
          <div>
            <h3>投票量与热度分层 <small>p25 / p50 / p75</small></h3>
            <div class="adm-inline vertical">
              <span>票数 p25 <b>{{ quantiles?.p25 ?? '—' }}</b></span>
              <span>票数 p50 <b>{{ quantiles?.p50 ?? '—' }}</b></span>
              <span>票数 p75 <b>{{ quantiles?.p75 ?? '—' }}</b></span>
              <span>热度 p25 <b>{{ fmtDecimal(popQuantiles?.p25) }}</b></span>
              <span>热度 p50 <b>{{ fmtDecimal(popQuantiles?.p50) }}</b></span>
              <span>热度 p75 <b>{{ fmtDecimal(popQuantiles?.p75) }}</b></span>
            </div>
            <p class="adm-note">热度是 TMDB popularity 指数，不是播放次数；分位仅基于有效评分样本。</p>
          </div>
        </div>
        <h3>评分 × 热度散点 <small>当前明细页样本</small></h3>
        <AdminScatter :points="scatterPoints" label="TMDB 评分与热度散点，当前明细页样本" x-name="TMDB 评分" y-name="热度指数"/>
        <h3>达标高分榜 TOP5 <small>票数 ≥{{ filters.minVotes }}，按评分降序</small></h3>
        <div v-if="topRated.length" class="adm-poster-row">
          <MovieCard v-for="(movie, index) in topRated" :key="movie.movie_id" :movie="movie" :rank="index + 1" @open="emit('openMovie', $event)"/>
        </div>
        <p v-else class="adm-empty">没有达到票数门槛的高分影片。</p>
      </section>

      <section class="adm-panel" aria-labelledby="deep-d">
        <h2 id="deep-d">D. 主题与产业 <small>只对补采覆盖的样本得结论</small></h2>
        <div class="adm-two-col">
          <div>
            <h3>电影关键词 TOP10 <small>按 TMDB keyword_id 合并</small></h3>
            <div v-if="keywords.length" class="adm-bars">
              <div v-for="item in keywords" :key="item.keyword_id" class="adm-bar clickable" role="button" tabindex="0" :title="'筛选含关键词「' + item.name + '」的明细'" @click="pickKeyword(item)" @keyup.enter="pickKeyword(item)">
                <span>{{ item.name }}</span><i><b :style="{width: (item.movie_count / Math.max(1, keywords[0]?.movie_count || 1) * 100) + '%'}"/></i><b>{{ item.movie_count }}</b>
              </div>
            </div>
            <p v-else class="adm-empty">当前筛选下暂无关键词资料。</p>
            <p class="adm-note">点击关键词只会筛选 F 明细，不改变 A–E 聚合。</p>
            <h3>制作公司 TOP10 <small>作品数 · 点击下钻</small></h3>
            <div v-if="companies.length" class="adm-bars">
              <div v-for="item in companies" :key="item.company_id" class="adm-bar clickable" role="button" tabindex="0" :title="'筛选该公司制作的明细'" @click="pickCompany(item)" @keyup.enter="pickCompany(item)">
                <span>{{ item.name }}</span><i><b :style="{width: (item.movie_count / Math.max(1, companies[0]?.movie_count || 1) * 100) + '%'}"/></i><b>{{ item.movie_count }}</b>
              </div>
            </div>
            <p v-else class="adm-empty">当前筛选下暂无制作公司资料。</p>
          </div>
          <div>
            <h3>原始语言 vs 对白语言 <small>两者不合并</small></h3>
            <div class="adm-two-col compact">
              <div>
                <p class="mini-title">原始语言</p>
                <div v-if="row.languages.original_language.length" class="adm-bars">
                  <div v-for="item in row.languages.original_language.slice(0, 8)" :key="item.code" class="adm-bar">
                    <span>{{ item.code }}</span><i><b :style="{width: (item.count / Math.max(1, row.languages.original_language[0]?.count || 1) * 100) + '%'}"/></i><b>{{ item.count }}</b>
                  </div>
                </div>
                <p v-else class="adm-empty">暂无原始语言资料。</p>
              </div>
              <div>
                <p class="mini-title">对白语言</p>
                <div v-if="row.languages.spoken_languages.length" class="adm-bars">
                  <div v-for="item in row.languages.spoken_languages.slice(0, 8)" :key="item.code" class="adm-bar">
                    <span>{{ item.code }}</span><i><b :style="{width: (item.count / Math.max(1, row.languages.spoken_languages[0]?.count || 1) * 100) + '%'}"/></i><b>{{ item.count }}</b>
                  </div>
                </div>
                <p v-else class="adm-empty">暂无对白语言资料。</p>
              </div>
            </div>
            <h3>导演 <small>取 crew job=Director</small></h3>
            <div class="adm-tablewrap" v-if="directors.length">
              <table><thead><tr><th>导演</th><th class="num">作品数</th><th>代表作</th></tr></thead>
                <tbody><tr v-for="person in directors" :key="person.person_id"><td>{{ person.name }}</td><td class="num">{{ person.movie_count }}</td><td><small>{{ person.top_movies.slice(0, 3).join(' / ') }}</small></td></tr></tbody>
              </table>
            </div>
            <p v-else class="adm-empty">演职员资料不足：{{ row.credits.status }}。</p>
            <h3>主要演员 <small>按作品数</small></h3>
            <div class="adm-tablewrap" v-if="cast.length">
              <table><thead><tr><th>演员</th><th class="num">作品数</th><th>代表作</th></tr></thead>
                <tbody><tr v-for="person in cast" :key="person.person_id"><td>{{ person.name }}</td><td class="num">{{ person.movie_count }}</td><td><small>{{ person.top_movies.slice(0, 3).join(' / ') }}</small></td></tr></tbody>
              </table>
            </div>
            <p v-else class="adm-empty">当前筛选下暂无演员资料。</p>
          </div>
        </div>
        <p class="adm-note">导演/演员排行分母为“有演职员资料”的 {{ fmtNumber(row.credits.movies_with_credits) }} 部影片（{{ row.credits.status }}），不是国家全部影片；缺资料影片不进入排行。关键词/公司可点击，仅筛选 F 明细。</p>
      </section>

      <section class="adm-panel" aria-labelledby="deep-e">
        <h2 id="deep-e">E. 发行资料 <small>仅展示已有地区上映记录</small></h2>
        <template v-if="release && release.status === 'PRESENT'">
          <div class="adm-inline">
            <span>有该国上映记录 <b>{{ fmtNumber(release?.covered_movies) }} 部</b></span>
            <span>相对全球最早上映中位差 <b>{{ release?.median_lag_days == null ? '—' : release?.median_lag_days + ' 天' }}</b></span>
          </div>
          <div class="adm-tablewrap" style="margin-top:10px" v-if="release?.samples.length">
            <table><thead><tr><th>影片</th><th>该国最早上映</th><th>全球最早</th><th class="num">差（天）</th><th></th></tr></thead>
              <tbody><tr v-for="item in release?.samples.slice(0, 10)" :key="item.movie_id">
                <td>#{{ item.movie_id }}</td><td>{{ item.country_release_date }}</td><td>{{ item.earliest_release_date }}</td><td class="num">{{ item.lag_days }}</td>
                <td><button type="button" class="link-btn" @click="emit('openMovie', item.movie_id)">详情</button></td>
              </tr></tbody>
            </table>
          </div>
        </template>
        <p v-else class="adm-empty">该国暂无地区上映记录；这只说明“没有记录”，不能认定影片未在该国发行。</p>
        <p class="adm-note">地区上映记录不用于推断制作国家；时间差为本国最早上映日相对全球最早上映日，缺失不插值。</p>
      </section>

      <section class="adm-panel" aria-labelledby="deep-f">
        <h2 id="deep-f">F. 电影明细 <small>后端分页 · 共 {{ fmtNumber(details?.total) }} 部</small></h2>
        <div class="adm-tablewrap">
          <table>
            <thead><tr><th>片名</th><th>年份</th><th>类型</th><th>制作国家</th><th class="num">TMDB 评分</th><th class="num">票数</th><th class="num">热度</th><th>缺失主题 / 原因</th></tr></thead>
            <tbody>
              <tr v-for="movie in details?.items || []" :key="movie.movie_id">
                <td><button type="button" class="link-btn" @click="emit('openMovie', movie.movie_id)">{{ movie.title }}</button><br><small>#{{ movie.movie_id }} · TMDB {{ movie.tmdb_id ?? '—' }}</small></td>
                <td>{{ movie.release_year || '—' }}</td>
                <td><small>{{ movie.genres.slice(0, 3).join(' / ') || '—' }}</small></td>
                <td><small>{{ movie.countries.join(' / ') || '国家未知' }}</small></td>
                <td class="num">{{ movie.rating_sample_valid ? fmtScore(movie.vote_average) : '样本不足' }}</td>
                <td class="num">{{ movie.vote_count ?? '—' }}</td>
                <td class="num">{{ fmtDecimal(movie.popularity) }}</td>
                <td>
                  <template v-if="detailMissing(movie).length">
                    <small v-for="item in detailMissing(movie)" :key="item.topic" class="missing-tag" :class="'reason-' + item.reason.toLowerCase()">{{ item.label }} · {{ REASON_LABELS[item.reason] || item.reason }}</small>
                  </template>
                  <small v-else class="missing-ok">关键主题齐全</small>
                </td>
              </tr>
            </tbody>
          </table>
        </div>
        <p v-if="!details?.items.length" class="adm-empty">当前筛选下没有影片。</p>
        <div class="adm-pager">
          <button type="button" class="adm-btn" :disabled="offset === 0 || busy" @click="page(-1)">上一页</button>
          <span>第 {{ Math.floor(offset / PAGE) + 1 }} 页 · 每页 {{ PAGE }} 部 · {{ sortInput ? sortLabel(sortInput) + (orderInput === 'asc' ? ' 升序' : ' 降序') : 'movie_id 升序' }}</span>
          <button type="button" class="adm-btn" :disabled="!details?.has_more || busy" @click="page(1)">下一页</button>
        </div>
        <p class="adm-note">表内可按片名/年份/评分/票数/热度排序；缺失影片在排序时始终排在末尾。缺失原因区分 EMPTY（上游确认无值）与 FAILED/MISSING/NOT_FETCHED（需要处理）。明细条件只作用于 F，A–E 仍按顶部全局筛选。点击片名进入既有电影详情。</p>
        <div class="adm-actions">
          <button type="button" class="adm-btn" @click="emit('quality')">去数据质量定位缺失</button>
        </div>
      </section>
    </template>

    <section v-else-if="!busy && !error" class="adm-panel">
      <p class="adm-empty">当前筛选范围内没有 {{ zh(iso2) }} 的影片；可放宽类型/年代/票数门槛后重试，或返回地图查看其他国家。</p>
    </section>
  </section>
</template>

<style scoped>
.deep-view{min-width:0}
.deep-head{display:flex;justify-content:space-between;align-items:flex-end;gap:16px;flex-wrap:wrap;margin:2px 0 16px}
.deep-head h2{margin:2px 0 4px;font-family:var(--serif);font-size:26px;font-weight:500;color:#f3eee4}
.deep-en{margin-left:10px;font-size:11px;letter-spacing:.06em;color:#a9a294;font-family:var(--sans)}
.deep-meta{margin:0;font-size:10.5px;color:#8f8a80;line-height:1.7}
.filter-panel{padding:16px 18px}
.filter-panel .adm-note{margin-top:4px}
.vertical{flex-direction:column;gap:6px;align-items:flex-start}
.mini-title{margin:0 0 6px;font-size:10.5px;color:#a9a294;letter-spacing:.04em}
.adm-two-col.compact{gap:12px}
.link-btn{border:0;background:transparent;color:#dcc49b;font:inherit;font-size:12px;cursor:pointer;padding:0;text-align:left;text-decoration:underline;text-underline-offset:2px}
.link-btn:hover{color:#fff}
.adm-bar.clickable{cursor:pointer;border-radius:5px;padding:2px 4px;margin:-2px -4px;transition:background-color .15s ease}
.adm-bar.clickable:hover,.adm-bar.clickable:focus-visible{background:rgba(175,145,231,.10)}
.adm-bar.clickable:focus-visible{outline:2px solid #c8a46a;outline-offset:2px}
.detail-filters{border-top:1px dashed rgba(241,236,226,.10);margin-top:12px;padding-top:10px}
.detail-filter-line{display:flex;align-items:flex-end;gap:10px;flex-wrap:wrap}
.detail-label{font-size:10.5px;color:#8f83a0;align-self:center;white-space:nowrap}
.adm-field.inline{flex:0 0 auto}
.adm-field.inline select{padding:6px 8px}
.missing-picks{display:flex;align-items:center;gap:6px;flex-wrap:wrap;margin-top:9px}
.missing-pick{display:inline-flex;align-items:center;gap:5px;border:1px solid rgba(241,236,226,.14);border-radius:999px;padding:4px 9px;font-size:10.5px;color:#a9a294;cursor:pointer;background:#131219}
.missing-pick input{accent-color:#9e81c6;margin:0}
.missing-pick.on{border-color:#8e72b8;color:#e7dcf7;background:#1d1827}
.detail-chip{border-color:#8e72b8;color:#d9c9ef}
.missing-tag{display:inline-block;font-size:10px;line-height:1.5;border-radius:3px;padding:0 4px;margin:0 3px 2px 0;background:#22202a;color:#b7a9c6;white-space:nowrap}
.missing-tag.reason-empty{color:#9b9388;background:#20201f}
.missing-tag.reason-failed{color:#e7b2a8;background:#2d2020}
.missing-tag.reason-missing,.missing-tag.reason-not_fetched{color:#dcc49b;background:#2a2419}
.missing-ok{color:#8f8a80;font-size:10px}
@media(max-width:760px){.deep-head{align-items:flex-start}}
</style>