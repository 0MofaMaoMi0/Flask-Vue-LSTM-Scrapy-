<script setup lang="ts">
import {computed, onBeforeUnmount, onMounted, ref, watch} from 'vue'
import {countryName} from '../../lib/globe/countryDisplay.js'
import {ISO2_TO_ISO3} from '../../lib/globe/iso2to3.js'
import {formatUtc} from './adminContext'
import {
  fmtDecimal, fmtNumber, fmtPercent, fmtScore, loadAnalytics,
  type AnalyticsPayload, type DetailMovie,
} from './analytics'
import MovieCard from './MovieCard.vue'
import './admin-ui.css'

const props = defineProps<{ country?: string; countryLabel?: string }>()
const emit = defineEmits<{openMovie: [id: number]}>()
const payload = ref<AnalyticsPayload | null>(null), busy = ref(true), error = ref(''), offset = ref(0)
const controller = ref<AbortController | null>(null)
const PAGE = 20
const genreInput = ref(''), decadeInput = ref(''), yearFromInput = ref(''), yearToInput = ref(''), minVotesInput = ref('50')
const countryFilter = ref(props.country || '')
const keywordFilter = ref<{id: number; name: string} | null>(null)
const companyFilter = ref<{id: number; name: string} | null>(null)
const assetGlobal = computed<AnalyticsPayload['global'] | null>(() => payload.value?.global ?? null)
const details = computed(() => payload.value?.details)
const coverage = computed(() => payload.value?.coverage)
const snapshot = computed(() => formatUtc(payload.value?.snapshot.latest_fetched_at))
const zh = (code: string) => countryName((ISO2_TO_ISO3 as Record<string, string>)[code] || code, code)
const filterSummary = computed(() => {
  const parts: string[] = []
  if (countryFilter.value) parts.push('国家：' + zh(countryFilter.value) + '（' + countryFilter.value + '）')
  if (genreInput.value) parts.push('类型：' + genreInput.value)
  if (decadeInput.value) parts.push('年代：' + decadeInput.value)
  if (yearFromInput.value) parts.push('年份 ≥' + yearFromInput.value)
  if (yearToInput.value) parts.push('年份 ≤' + yearToInput.value)
  if (keywordFilter.value) parts.push('关键词：' + keywordFilter.value.name)
  if (companyFilter.value) parts.push('公司：' + companyFilter.value.name)
  parts.push('票数门槛 ≥' + minVotesInput.value)
  return parts
})
const genreRows = computed(() => (assetGlobal.value?.top_genres || []).slice(0, 10))
const maxGenre = computed(() => Math.max(1, ...genreRows.value.map(item => item.count)))
const decadeRows = computed(() => (assetGlobal.value?.decade_distribution || []).filter(item => item.count > 0))
const maxDecade = computed(() => Math.max(1, ...decadeRows.value.map(item => item.count)))
const ratingRows = computed(() => assetGlobal.value?.rating_distribution.rating_histogram || [])
const maxRating = computed(() => Math.max(1, ...ratingRows.value.map(item => item.count)))
const languagesOriginal = computed(() => (assetGlobal.value?.original_language || []).slice(0, 8))
const languagesSpoken = computed(() => (assetGlobal.value?.spoken_languages || []).slice(0, 8))
const maxLanguage = computed(() => Math.max(1, ...languagesOriginal.value.map(item => item.count), ...languagesSpoken.value.map(item => item.count)))
const topRated = computed(() => (assetGlobal.value?.top_rated_movies || []).slice(0, 5))
const topVoted = computed(() => (assetGlobal.value?.top_movies || []).slice(0, 5))
const topPopular = computed(() => (assetGlobal.value?.top_popular_movies || []).slice(0, 5))
const assets = computed(() => assetGlobal.value?.assets || {})
const keywordBlock = computed(() => assetGlobal.value?.top_keywords)
const companyBlock = computed(() => assetGlobal.value?.top_companies)
const keywordRows = computed(() => keywordBlock.value?.items || [])
const companyRows = computed(() => companyBlock.value?.items || [])
const creditBlock = computed(() => assetGlobal.value?.credits)
const runtimeBlock = computed(() => assetGlobal.value?.runtime_distribution)
const runtimeRows = computed(() => runtimeBlock.value?.buckets || [])
const genrePairBlock = computed(() => assetGlobal.value?.genre_pairs)
const genrePairs = computed(() => genrePairBlock.value?.pairs || [])
const maxRuntime = computed(() => Math.max(1, ...runtimeRows.value.map(row => row.count)))
const maxPair = computed(() => Math.max(1, ...genrePairs.value.map(row => row.movie_count)))
const coveragePercent = (key: string): number | null => {
  const value = assets.value[key]
  const total = coverage.value?.movies
  return typeof value === 'number' && total ? value / total : null
}
const assetRows = computed(() => [
  {key: 'poster', name: '海报'}, {key: 'overview', name: '简介'},
  {key: 'backdrop', name: '背景图'}, {key: 'runtime', name: '片长'},
])

function numberOrNull(value: string, low: number, high: number): number | null {
  const text = value.trim()
  if (!text || !/^\d{1,4}$/.test(text)) return null
  const parsed = Number(text)
  return parsed >= low && parsed <= high ? parsed : null
}
function applyFilters() {
  offset.value = 0
  load()
}
function resetFilters() {
  genreInput.value = ''; decadeInput.value = ''; yearFromInput.value = ''; yearToInput.value = ''
  minVotesInput.value = '50'; countryFilter.value = props.country || ''
  keywordFilter.value = null; companyFilter.value = null
  offset.value = 0
  load()
}
function pickGenre(genre: string) {
  genreInput.value = genre; keywordFilter.value = null; companyFilter.value = null; offset.value = 0; load()
}
function pickKeyword(item: {keyword_id: number; name: string}) {
  keywordFilter.value = {id: item.keyword_id, name: item.name}
  companyFilter.value = null
  offset.value = 0; load()
}
function pickCompany(item: {company_id: number; name: string}) {
  companyFilter.value = {id: item.company_id, name: item.name}
  keywordFilter.value = null
  offset.value = 0; load()
}
function clearTextFilters() {
  keywordFilter.value = null; companyFilter.value = null
  offset.value = 0; load()
}
function pickDecade(decade: number | string | null) {
  if (decade == null) return
  const value = Number(decade)
  if (!Number.isFinite(value)) return
  yearFromInput.value = String(value); yearToInput.value = String(value + 9)
  decadeInput.value = ''
  offset.value = 0; load()
}
function page(delta: number) {
  const next = Math.max(0, offset.value + delta * PAGE)
  if (next === offset.value) return
  offset.value = next
  load()
}
const MISSING_LABELS: Record<string, string> = {
  countries: '国家', keywords: '关键词', companies: '公司', credits: '演职员',
  poster: '海报', overview: '简介', releases: '上映', spoken_languages: '语言',
}
function detailMissing(movie: DetailMovie): {topic: string; reason: string; label: string}[] {
  const fields = movie.missing_fields || Object.keys(movie.missing_reasons || {})
  return fields.map(topic => ({
    topic,
    reason: movie.missing_reasons?.[topic] || 'MISSING',
    label: MISSING_LABELS[topic] || topic,
  }))
}

async function load() {
  controller.value?.abort()
  const current = new AbortController()
  controller.value = current
  busy.value = true; error.value = ''
  try {
    const next = await loadAnalytics({
      country: countryFilter.value || undefined,
      genre: genreInput.value.trim() || undefined,
      decade: decadeInput.value || undefined,
      yearFrom: numberOrNull(yearFromInput.value, 1800, 2199),
      yearTo: numberOrNull(yearToInput.value, 1800, 2199),
      minVotes: Math.max(0, Math.min(1000000, Number(minVotesInput.value) || 0)),
      keyword: keywordFilter.value?.id ?? null,
      company: companyFilter.value?.id ?? null,
      top: 10, limit: PAGE, offset: offset.value, countryLimit: 200,
    }, current.signal)
    if (controller.value !== current || current.signal.aborted) return
    payload.value = next
  } catch (e) {
    if (controller.value === current && !current.signal.aborted && (e as Error).name !== 'AbortError') {
      error.value = (e as Error).message
    }
  } finally {
    if (controller.value === current) busy.value = false
  }
}
watch(() => props.country, value => {
  countryFilter.value = value || ''
  offset.value = 0
  load()
})
onMounted(load)
onBeforeUnmount(() => controller.value?.abort())
defineExpose({refresh: () => load()})
</script>

<template>
  <section class="assets-view" aria-labelledby="assets-title">
    <header class="assets-head">
      <div>
        <h2 id="assets-title">内容主题与资产覆盖</h2>
        <p class="assets-meta">来源：全库 TMDB 补采资料 · 快照 {{ snapshot }} · TMDB 评分与站内豆瓣评分分开</p>
      </div>
      <button type="button" class="adm-btn" :disabled="busy" @click="load">刷新</button>
    </header>

    <div class="adm-panel filter-panel">
      <div class="adm-toolbar">
        <label class="adm-field"><span>国家 ISO2</span><input v-model="countryFilter" type="text" maxlength="2" placeholder="全部国家" @keyup.enter="applyFilters"></label>
          <label class="adm-field"><span>类型</span><input v-model="genreInput" type="text" placeholder="如 Drama" @keyup.enter="applyFilters"></label>
        <label class="adm-field"><span>年代</span>
          <select v-model="decadeInput">
            <option value="">全部年代</option>
            <option v-for="value in ['1950','1960','1970','1980','1990','2000','2010','2020']" :key="value" :value="value">{{ value }}</option>
          </select>
        </label>
          <label class="adm-field"><span>年份从</span><input v-model="yearFromInput" type="text" inputmode="numeric" @keyup.enter="applyFilters"></label>
          <label class="adm-field"><span>年份到</span><input v-model="yearToInput" type="text" inputmode="numeric" @keyup.enter="applyFilters"></label>
        <label class="adm-field"><span>票数门槛</span><input v-model="minVotesInput" type="text" inputmode="numeric" @keyup.enter="applyFilters"></label>
        <button type="button" class="adm-btn primary" @click="applyFilters">应用</button>
        <button type="button" class="adm-btn" @click="resetFilters">清空组合过滤</button>
      </div>
      <div class="adm-chips"><span v-for="part in filterSummary" :key="part" class="adm-chip">{{ part }}</span></div>
      <div v-if="keywordFilter || companyFilter" class="adm-chips">
        <span v-if="keywordFilter" class="adm-chip">关键词：{{ keywordFilter.name }}<button type="button" aria-label="清除关键词" @click="keywordFilter = null; offset = 0; load()">✕</button></span>
        <span v-if="companyFilter" class="adm-chip">公司：{{ companyFilter.name }}<button type="button" aria-label="清除公司" @click="companyFilter = null; offset = 0; load()">✕</button></span>
        <button type="button" class="link-btn" @click="clearTextFilters">清除文本下钻</button>
      </div>
      <p class="adm-note">分类维度可下钻：点击下方类型/年代条目会写入筛选并刷新明细。点击影片进入既有电影详情。</p>
    </div>

    <p v-if="error" class="adm-errobox" role="alert">{{ error }}<span v-if="payload">（已保留上一份可用快照）</span> <button type="button" class="link-btn" @click="load">重试</button></p>
    <p v-else-if="busy && !payload" class="adm-state">正在读取全库资源结构…</p>
    <p v-if="payload?.scope.catalog?.status === 'UNAVAILABLE'" class="adm-warnbox" role="status">在架目录计数暂不可用；当前按已载入的 TMDB 补采快照展示。</p>
    <p v-if="payload?.scope.repository?.truncated" class="adm-warnbox" role="status">文档库规模较大，当前统计按受控容量上限（{{ fmtNumber(payload.scope.repository.documents_loaded) }} / {{ fmtNumber(payload.scope.repository.cap) }} 份）截断计算。</p>

    <template v-if="assetGlobal">
      <section class="adm-panel">
        <h2>资产概况 <small>当前筛选范围</small></h2>
        <div class="adm-kpis">
          <div><span>影片数</span><strong>{{ fmtNumber(assetGlobal.movie_count_distinct) }}<small>部</small></strong><p>按 movie_id 去重</p></div>
          <div><span>有制作国家资料</span><strong>{{ fmtNumber(assetGlobal.movies_with_countries) }}<small>部</small></strong><p>国家未知 {{ fmtNumber(assetGlobal.movies_country_unknown) }} 部</p></div>
          <div><span>有效 TMDB 评分样本</span><strong>{{ fmtNumber(assetGlobal.rated_movie_count) }}<small>部</small></strong><p>票数 ≥{{ assetGlobal.tmdb_avg_rating.min_votes }}</p></div>
          <div><span>平均 TMDB 评分</span><strong>{{ fmtScore(assetGlobal.tmdb_avg_rating.unweighted_mean) }}</strong><p>简单平均 · {{ fmtNumber(assetGlobal.tmdb_avg_rating.valid_samples) }} 样本</p></div>
          <div><span>有类型资料</span><strong>{{ fmtNumber(coverage?.genres.movies_with_genres) }}<small>部</small></strong><p>多标签，合计可超影片数</p></div>
          <div><span>有海报</span><strong>{{ coveragePercent('poster') == null ? '—' : fmtPercent(coveragePercent('poster')) }}</strong><p>{{ typeof assets.poster === 'number' ? fmtNumber(assets.poster) : '—' }} 部 · 资料关联覆盖</p></div>
        </div>
        <div class="asset-bars">
          <div v-for="item in assetRows" :key="item.key" class="asset-row">
            <span>{{ item.name }}</span>
              <progress :value="coveragePercent(item.key) ?? 0" max="1" :aria-label="item.name + ' 覆盖率'"/>
            <b>{{ coveragePercent(item.key) == null ? '不可用' : fmtPercent(coveragePercent(item.key)) }}</b>
          </div>
        </div>
        <p class="adm-note">覆盖率为“有该资料”的影片占当前筛选影片数；来源 JSON 存在不等于已结构化可查询。预算/票房 0 视为未知，不估算利润。</p>
      </section>

      <div class="adm-two-col">
        <section class="adm-panel">
          <h2>类型 TOP10 <small>点击下钻</small></h2>
          <div v-if="genreRows.length" class="adm-bars">
            <div v-for="item in genreRows" :key="item.genre" class="adm-bar clickable" role="button" tabindex="0" @click="pickGenre(item.genre)" @keyup.enter="pickGenre(item.genre)">
              <span>{{ item.genre }}</span><i><b :style="{width: (item.count / maxGenre * 100) + '%'}"/></i><b>{{ item.count }}</b>
            </div>
          </div>
            <p v-else class="adm-empty">当前筛选下暂无类型资料。</p>
            <p class="adm-note">类型为多标签计数，合计可超过影片总数。</p>
        </section>
        <section class="adm-panel">
          <h2>上映年代 <small>点击下钻</small></h2>
          <div v-if="decadeRows.length" class="adm-bars">
            <div v-for="item in decadeRows" :key="String(item.decade)" class="adm-bar clickable" role="button" tabindex="0" @click="pickDecade(item.decade)" @keyup.enter="pickDecade(item.decade)">
              <span>{{ item.name }}</span><i><b :style="{width: (item.count / maxDecade * 100) + '%'}"/></i><b>{{ item.count }}</b>
            </div>
          </div>
            <p v-else class="adm-empty">当前筛选下暂无年代资料。</p>
            <p class="adm-note">仅描述库内上映年代结构，不代表真实产量。</p>
        </section>
      </div>

      <div class="adm-two-col">
        <section class="adm-panel">
            <h2>原始语言 vs 对白语言 <small>不合并</small></h2>
          <div class="adm-two-col compact">
            <div>
              <p class="mini-title">原始语言</p>
              <div class="adm-bars">
                <div v-for="item in languagesOriginal" :key="item.code" class="adm-bar"><span>{{ item.code }}</span><i><b :style="{width: (item.count / maxLanguage * 100) + '%'}"/></i><b>{{ item.count }}</b></div>
              </div>
            </div>
            <div>
              <p class="mini-title">对白语言</p>
              <div class="adm-bars">
                <div v-for="item in languagesSpoken" :key="item.code" class="adm-bar"><span>{{ item.code }}</span><i><b :style="{width: (item.count / maxLanguage * 100) + '%'}"/></i><b>{{ item.count }}</b></div>
              </div>
            </div>
          </div>
        </section>
        <section class="adm-panel">
            <h2>TMDB 评分分布 <small>仅有效样本</small></h2>
          <div class="adm-bars">
            <div v-for="item in ratingRows" :key="item.name" class="adm-bar"><span>{{ item.name }}</span><i><b :style="{width: (item.count / maxRating * 100) + '%'}"/></i><b>{{ item.count }}</b></div>
          </div>
          <div class="adm-inline" style="margin-top:10px">
              <span>票数 p50 <b>{{ assetGlobal.rating_distribution.vote_count_quantiles.p50 ?? '—' }}</b></span>
            <span>热度 p50 <b>{{ fmtDecimal(assetGlobal.rating_distribution.popularity_quantiles.p50) }}</b></span>
          </div>
            <p class="adm-note">popularity 是 TMDB 热度指数，不是播放量；评分来源为 TMDB，与原豆瓣站内评分不混用。</p>
        </section>
      </div>

      <section class="adm-panel">
            <h2>榜单 <small>门槛与排序说明</small></h2>
        <div class="adm-three-col">
          <div>
              <h3>高分榜 <small>票数门槛 · 评分降序</small></h3>
            <div class="adm-poster-row one"><MovieCard v-for="(movie, index) in topRated" :key="movie.movie_id" :movie="movie" :rank="index + 1" @open="emit('openMovie', $event)"/></div>
              <p v-if="!topRated.length" class="adm-empty">暂无达标高分影片。</p>
          </div>
          <div>
              <h3>投票最多 <small>vote_count 降序</small></h3>
            <div class="adm-poster-row one"><MovieCard v-for="(movie, index) in topVoted" :key="movie.movie_id" :movie="movie" :rank="index + 1" @open="emit('openMovie', $event)"/></div>
              <p v-if="!topVoted.length" class="adm-empty">暂无投票资料。</p>
          </div>
          <div>
              <h3>热度最高 <small>popularity 降序</small></h3>
            <div class="adm-poster-row one"><MovieCard v-for="(movie, index) in topPopular" :key="movie.movie_id" :movie="movie" :rank="index + 1" @open="emit('openMovie', $event)"/></div>
              <p v-if="!topPopular.length" class="adm-empty">暂无热度资料。</p>
          </div>
        </div>
          <p class="adm-note">高分榜只含达到当前票数门槛的影片；小众候选按“高分 + 低热度”阅读时必须同时看票数，热度不是观看次数。</p>
      </section>

      <section class="adm-panel">
        <h2>主题与产业 <small>全库结构 · 分母与状态明示</small></h2>
        <div class="adm-two-col">
          <div>
            <h3>全局关键词 TOP10 <small>点击下钻明细</small></h3>
            <p v-if="keywordBlock" class="block-meta">状态 {{ keywordBlock.status }} · 分母 {{ fmtNumber(keywordBlock.movies_with_keywords) }} / {{ fmtNumber(keywordBlock.movies_total) }} 部有关键词资料</p>
            <div v-if="keywordRows.length" class="adm-bars">
              <div v-for="item in keywordRows.slice(0, 10)" :key="item.keyword_id" class="adm-bar clickable" role="button" tabindex="0" :title="'筛选含关键词「' + item.name + '」的明细'" @click="pickKeyword(item)" @keyup.enter="pickKeyword(item)">
                <span>{{ item.name }}</span><i><b :style="{width: (item.movie_count / Math.max(1, keywordRows[0]?.movie_count || 1) * 100) + '%'}"/></i><b>{{ item.movie_count }}</b>
              </div>
            </div>
            <p v-else class="adm-empty">关键词资料：{{ keywordBlock?.status || '暂不可用' }}；不虚构排行。</p>

            <h3>全局制作公司 TOP10 <small>点击下钻明细</small></h3>
            <p v-if="companyBlock" class="block-meta">状态 {{ companyBlock.status }} · 分母 {{ fmtNumber(companyBlock.movies_with_companies) }} / {{ fmtNumber(companyBlock.movies_total) }} 部有公司资料</p>
            <div v-if="companyRows.length" class="adm-bars">
              <div v-for="item in companyRows.slice(0, 10)" :key="item.company_id" class="adm-bar clickable" role="button" tabindex="0" :title="'筛选该公司制作的明细'" @click="pickCompany(item)" @keyup.enter="pickCompany(item)">
                <span>{{ item.name }}</span><i><b :style="{width: (item.movie_count / Math.max(1, companyRows[0]?.movie_count || 1) * 100) + '%'}"/></i><b>{{ item.movie_count }}</b>
              </div>
            </div>
            <p v-else class="adm-empty">公司资料：{{ companyBlock?.status || '暂不可用' }}；公司 origin_country 不是影片制作国家。</p>

            <h3>影片时长区间 <small>6 桶 + 未知</small></h3>
            <p v-if="runtimeBlock" class="block-meta">状态 {{ runtimeBlock.status }} · 分母 {{ fmtNumber(runtimeBlock.movies_with_runtime) }} / {{ fmtNumber(runtimeBlock.movies_total) }} 部 · 未知 {{ fmtNumber(runtimeBlock.unknown) }} 部</p>
            <div v-if="runtimeRows.length" class="adm-bars">
              <div v-for="item in runtimeRows" :key="item.bucket" class="adm-bar">
                <span>{{ item.bucket }} 分钟</span><i><b :style="{width: (item.count / maxRuntime * 100) + '%'}"/></i><b>{{ item.count }}</b>
              </div>
            </div>
            <p v-else class="adm-empty">片长资料：{{ runtimeBlock?.status || '暂不可用' }}；0/缺失视为未知，不补 0。</p>
          </div>
          <div>
            <h3>导演 / 演员库内排行</h3>
            <p v-if="creditBlock" class="block-meta">状态 {{ creditBlock.status }} · 分母 {{ fmtNumber(creditBlock.movies_with_credits) }} 部有演职员资料</p>
            <template v-if="creditBlock && (creditBlock.directors.length || creditBlock.cast.length)">
              <div class="adm-tablewrap">
                <table><thead><tr><th>人物</th><th>角色</th><th class="num">作品数</th></tr></thead>
                  <tbody>
                    <tr v-for="person in creditBlock.directors.slice(0, 6)" :key="'d' + person.person_id"><td>{{ person.name }}</td><td>导演</td><td class="num">{{ person.movie_count }}</td></tr>
                    <tr v-for="person in creditBlock.cast.slice(0, 6)" :key="'c' + person.person_id"><td>{{ person.name }}</td><td>演员</td><td class="num">{{ person.movie_count }}</td></tr>
                  </tbody>
                </table>
              </div>
              <p class="adm-note">缺演职员资料的影片不进入分母；排行不是全库人口统计。</p>
            </template>
            <p v-else class="adm-empty">演职员资料：{{ creditBlock?.status || '暂不可用' }}。</p>

            <h3>类型共现 TOP <small>多标签</small></h3>
            <p v-if="genrePairBlock" class="block-meta">状态 {{ genrePairBlock.status }} · 分母 {{ fmtNumber(genrePairBlock.movies_with_multiple_genres) }} 部多类型影片 / {{ fmtNumber(genrePairBlock.movies_with_genres) }} 部有类型资料</p>
            <div v-if="genrePairs.length" class="adm-bars">
              <div v-for="item in genrePairs.slice(0, 8)" :key="item.genres.join('+')" class="adm-bar"><span>{{ item.genres.join(' + ') }}</span><i><b :style="{width: (item.movie_count / maxPair * 100) + '%'}"/></i><b>{{ item.movie_count }}</b></div>
            </div>
            <p v-else class="adm-empty">类型共现：{{ genrePairBlock?.status || '暂不可用' }}；单类型影片不进入共现分母。</p>
          </div>
        </div>
      </section>

      <section class="adm-panel">
        <h2>本地电影明细检索 <small>后端分页 · 共 {{ fmtNumber(details?.total) }} 部</small></h2>
        <div class="adm-tablewrap">
          <table>
            <thead><tr><th>片名</th><th>年份</th><th>类型</th><th>制作国家</th><th class="num">TMDB 评分</th><th class="num">票数</th><th class="num">热度</th><th>资料缺失</th></tr></thead>
            <tbody>
              <tr v-for="movie in (details?.items || []).slice(0, PAGE)" :key="movie.movie_id">
                <td><button type="button" class="link-btn" @click="emit('openMovie', movie.movie_id)">{{ movie.title }}</button><br><small>#{{ movie.movie_id }} · TMDB {{ movie.tmdb_id ?? '—' }}</small></td>
                <td>{{ movie.release_year || '—' }}</td>
                <td><small>{{ movie.genres.slice(0, 3).join(' / ') || '—' }}</small></td>
                <td><small>{{ movie.countries.join(' / ') || '国家未知' }}</small></td>
                <td class="num">{{ movie.rating_sample_valid ? fmtScore(movie.vote_average) : '样本不足' }}</td>
                <td class="num">{{ movie.vote_count ?? '—' }}</td>
                <td class="num">{{ fmtDecimal(movie.popularity) }}</td>
                <td>
                  <template v-if="detailMissing(movie).length">
                    <small v-for="item in detailMissing(movie)" :key="item.topic" class="missing-tag" :class="'reason-' + item.reason.toLowerCase()">{{ item.label }} · {{ item.reason === 'EMPTY' ? '上游空值' : item.reason === 'FAILED' ? '采集失败' : item.reason === 'NOT_FETCHED' ? '未采集' : '来源缺失' }}</small>
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
          <span>第 {{ Math.floor(offset / PAGE) + 1 }} 页 · 每页 {{ PAGE }} 部 · movie_id 升序</span>
          <button type="button" class="adm-btn" :disabled="!details?.has_more || busy" @click="page(1)">下一页</button>
        </div>
        <p class="adm-note">明细固定 movie_id 升序；关键词/公司下钻只作用于明细表，不改变上方全局聚合。缺失原因区分 EMPTY（上游确认无值）与 FAILED/MISSING/NOT_FETCHED（需要处理）。</p>
      </section>
    </template>
  </section>
</template>

<style scoped>
.assets-view{min-width:0}
.assets-head{display:flex;justify-content:space-between;align-items:flex-end;gap:16px;flex-wrap:wrap;margin:0 0 14px}
.assets-head h2{margin:2px 0 4px;font-family:var(--serif);font-size:23px;font-weight:500;color:var(--mi-text-ivory)}
.assets-meta{margin:0;font-size:11px;color:var(--mi-text-muted);line-height:1.7}
.filter-panel{padding:15px 17px}
.asset-bars{display:flex;flex-direction:column;gap:8px;margin-top:12px}
.asset-row{display:grid;grid-template-columns:70px 1fr 64px;align-items:center;gap:10px;font-size:11.5px;color:var(--mi-text-soft)}
.asset-row progress{width:100%;height:5px;border:0;border-radius:3px;overflow:hidden;background:var(--mi-bar-track);accent-color:var(--mi-bar-fill)}
.asset-row progress::-webkit-progress-bar{background:var(--mi-bar-track)}
.asset-row progress::-webkit-progress-value{background:var(--mi-bar-fill)}
.asset-row b{font-weight:500;color:var(--mi-text);text-align:right}
.adm-bar.clickable{cursor:pointer;border-radius:5px;padding:2px 4px;margin:-2px -4px;transition:background-color .15s ease}
.adm-bar.clickable:hover{background:var(--mi-chip-hover)}
.adm-bar.clickable:focus-visible{outline:2px solid var(--mi-accent);outline-offset:2px}
.mini-title{margin:0 0 6px;font-size:11px;color:var(--mi-text-secondary);letter-spacing:.04em}
.adm-two-col.compact{gap:12px}
.adm-poster-row.one{grid-template-columns:repeat(auto-fill,minmax(112px,148px))}
.link-btn{border:0;background:transparent;color:var(--mi-accent-text);font:inherit;font-size:12px;cursor:pointer;padding:0;text-align:left;text-decoration:underline;text-underline-offset:2px}
.link-btn:hover{color:var(--mi-text)}
.block-meta{margin:0 0 7px;font-size:11px;color:var(--mi-text-muted);line-height:1.5}
.missing-tag{display:inline-block;font-size:11px;line-height:1.5;border-radius:3px;padding:0 4px;margin:0 3px 2px 0;background:var(--mi-surface-raised);color:var(--mi-text-secondary);white-space:nowrap}
.missing-tag.reason-empty{color:var(--mi-text-muted);background:var(--mi-surface-raised)}
.missing-tag.reason-failed{color:var(--mi-negative);background:var(--mi-negative-soft)}
.missing-tag.reason-missing,.missing-tag.reason-not_fetched{color:var(--mi-warning);background:var(--mi-warning-soft)}
.missing-ok{color:var(--mi-text-muted);font-size:11px}
</style>