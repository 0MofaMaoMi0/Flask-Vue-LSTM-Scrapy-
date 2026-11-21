<script setup lang="ts">
import {computed, onBeforeUnmount, onMounted, ref, watch} from 'vue'
import {countryName, countryEnglish} from '../../lib/globe/countryDisplay.js'
import {ISO2_TO_ISO3} from '../../lib/globe/iso2to3.js'
import {
  availabilityLabel, countryAvailability, formatUtc,
} from './adminContext'
import type {AdminQuery} from './adminContext'
import {
  coverageOfCountry, fmtDecimal, fmtNumber, fmtPercent,
  loadAnalytics, type AnalyticsPayload, type DeepCountry,
} from './analytics'
import MovieCard from './MovieCard.vue'
import './admin-ui.css'

const props = defineProps<{
  iso2: string
  filters: Pick<AdminQuery, 'genre' | 'decade' | 'yearFrom' | 'yearTo' | 'minVotes'>
  compare: string[]
}>()
const emit = defineEmits<{
  close: []
  deep: [iso2: string]
  compare: [iso2: string]
  allMovies: [iso2: string]
  quality: []
  openMovie: [id: number]
}>()
const payload = ref<AnalyticsPayload | null>(null), busy = ref(true), error = ref('')
const controller = ref<AbortController | null>(null)
const zh = (code: string) => countryName((ISO2_TO_ISO3 as Record<string, string>)[code] || code, code)
const english = (code: string) => countryEnglish((ISO2_TO_ISO3 as Record<string, string>)[code] || code, code)
const row = computed<DeepCountry | null>(() => payload.value?.country_analysis?.rows?.[0] || null)
const availability = computed(() => countryAvailability({
  movieCount: row.value?.movie_count_distinct,
  catalogState: payload.value?.states.catalog,
  countryState: payload.value?.states.countries,
  ratingState: payload.value?.states.ratings,
}))
const sampleRate = computed(() => {
  if (!row.value || !row.value.movie_count_distinct) return null
  return row.value.rated_movie_count / row.value.movie_count_distinct
})
const inCompare = computed(() => props.compare.includes(props.iso2))
const compareFull = computed(() => props.compare.length >= 3 && !inCompare.value)
const topMovies = computed(() => (row.value?.top_movies || []).slice(0, 5))
const topGenres = computed(() => (row.value?.top_genres || []).slice(0, 5))
const maxGenre = computed(() => Math.max(1, ...topGenres.value.map(item => item.count)))
const decades = computed(() => (row.value?.decade_distribution || []).filter(item => item.count > 0))
const maxDecade = computed(() => Math.max(1, ...decades.value.map(item => item.count)))
const posterCoverage = computed(() => coverageOfCountry(row.value))
const snapshot = computed(() => formatUtc(payload.value?.snapshot.latest_fetched_at))
const filterSummary = computed(() => {
  const parts: string[] = []
  if (props.filters.genre) parts.push('类型 ' + props.filters.genre)
  if (props.filters.decade) parts.push(props.filters.decade + ' 年代')
  if (props.filters.yearFrom != null) parts.push('≥' + props.filters.yearFrom)
  if (props.filters.yearTo != null) parts.push('≤' + props.filters.yearTo)
  parts.push('票数 ≥' + props.filters.minVotes)
  return parts.join(' · ')
})

async function load() {
  if (!/^[A-Z]{2}$/.test(props.iso2)) return
  controller.value?.abort()
  const current = new AbortController()
  controller.value = current
  if (row.value && row.value.country_iso2 !== props.iso2) payload.value = null
  busy.value = true; error.value = ''
  try {
    const next = await loadAnalytics({
      countries: [props.iso2],
      genre: props.filters.genre || undefined,
      decade: props.filters.decade || undefined,
      yearFrom: props.filters.yearFrom,
      yearTo: props.filters.yearTo,
      minVotes: props.filters.minVotes,
      top: 10, limit: 10,
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
function onKey(event: KeyboardEvent) { if (event.key === 'Escape') emit('close') }
onMounted(() => { load(); window.addEventListener('keydown', onKey) })
onBeforeUnmount(() => { controller.value?.abort(); window.removeEventListener('keydown', onKey) })
watch(() => [props.iso2, props.filters.genre, props.filters.decade, props.filters.yearFrom, props.filters.yearTo, props.filters.minVotes], load)
defineExpose({refresh: load})
</script>

<template>
  <Teleport to="body">
    <div class="quick-backdrop" role="presentation" @click.self="emit('close')">
      <aside class="quick-drawer" role="dialog" aria-modal="true" :aria-label="zh(iso2) + ' 国家速览'">
        <header class="quick-head">
          <div>
            <h2>{{ zh(iso2) }}<span class="quick-en">{{ row?.country_name && row.country_name !== iso2 ? row.country_name : english(iso2) }}</span></h2>
            <p class="quick-meta">{{ filterSummary }} · 快照 {{ snapshot }}</p>
          </div>
          <button class="quick-close" type="button" aria-label="关闭国家速览" @click="emit('close')">✕</button>
        </header>

        <div class="quick-body">
          <p v-if="error" class="adm-errobox" role="alert">{{ error }}<span v-if="row">（已保留上一份可用快照）</span> <button type="button" class="link" @click="load">重试</button></p>
          <p v-if="busy && !row" class="adm-state">正在读取 {{ iso2 }} 的国家统计…</p>
          <template v-else-if="row || !error">
            <p class="state-strip" :class="{muted: availability !== 'HAS_DATA'}">
              <span class="adm-badge" :class="{muted: availability !== 'HAS_DATA'}">{{ availabilityLabel(availability) }}</span>
              <span v-if="availability === 'NO_MATCHES'">当前筛选范围内没有收录该国影片；这不是 0 分，而是没有样本。</span>
              <span v-else-if="availability === 'NO_COUNTRY_METADATA'">影片存在但缺少制作国家资料，需补采后才能归入该国。</span>
              <span v-else-if="availability === 'NO_VALID_RATING_SAMPLE'">有影片但没有达到投票门槛的有效评分样本。</span>
              <span v-else>制作国家、影片与评分样本均可用。</span>
            </p>

            <div class="adm-kpis">
              <div><span>唯一电影数</span><strong>{{ fmtNumber(row?.movie_count_distinct) }}<small>部</small></strong><p>按 movie_id 去重</p></div>
              <div><span>占筛选全库</span><strong>{{ row?.share_of_filtered_catalog != null ? fmtPercent(row.share_of_filtered_catalog) : '—' }}</strong><p>分母含国家未知影片</p></div>
              <div><span>有效平均 TMDB 评分</span><strong>{{ fmtDecimal(row?.tmdb_avg_rating.unweighted_mean) }}</strong><p>{{ fmtNumber(row?.tmdb_avg_rating.valid_samples) }} 个达标样本 · 简单平均</p></div>
              <div><span>评分样本率</span><strong>{{ sampleRate == null ? '—' : fmtPercent(sampleRate) }}</strong><p>票数门槛 ≥{{ payload?.filters?.min_votes ?? filters.minVotes }}</p></div>
            </div>

            <div class="adm-two-col">
              <section>
                <h3>类型 TOP5 <small>多标签，合计可超过电影数</small></h3>
                <div v-if="topGenres.length" class="adm-bars">
                  <div v-for="genre in topGenres" :key="genre.genre" class="adm-bar">
                    <span>{{ genre.genre }}</span><i><b :style="{width: (genre.count / maxGenre * 100) + '%'}"/></i><b>{{ genre.count }}</b>
                  </div>
                </div>
                <p v-else class="adm-empty">暂无类型资料。</p>
              </section>
              <section>
                <h3>年代分布 <small>库内上映年代结构，非真实产量</small></h3>
                <div v-if="decades.length" class="adm-bars">
                  <div v-for="item in decades" :key="String(item.decade)" class="adm-bar">
                    <span>{{ item.name }}</span><i><b :style="{width: (item.count / maxDecade * 100) + '%'}"/></i><b>{{ item.count }}</b>
                  </div>
                </div>
                <p v-else class="adm-empty">暂无年代资料。</p>
              </section>
            </div>

            <h3>代表影片 TOP5 <small>按 TMDB 投票数降序</small></h3>
            <div v-if="topMovies.length" class="adm-poster-row">
              <MovieCard v-for="(movie, index) in topMovies" :key="movie.movie_id" :movie="movie" :rank="index + 1" @open="emit('openMovie', $event)"/>
            </div>
            <p v-else class="adm-empty">当前筛选下没有可展示的影片。</p>

            <h3>资料覆盖与缺失</h3>
            <div class="adm-inline">
              <span>海报覆盖 <b>{{ posterCoverage == null ? '暂未按国家拆分' : fmtPercent(posterCoverage) }}</b></span>
              <span>演职员资料 <b>{{ row?.credits.status === 'PRESENT' ? fmtNumber(row.credits.movies_with_credits) + ' 部有资料' : (row?.credits.status || '暂不可用') }}</b></span>
              <span>地区上映 <b>{{ row?.release_window.status === 'PRESENT' ? fmtNumber(row.release_window.covered_movies) + ' 部有记录' : '暂无记录（≠未发行）' }}</b></span>
              <span>关键词 / 公司 <b>{{ (row?.keywords?.length || 0) + ' / ' + (row?.companies?.length || 0) + ' 个可排行' }}</b></span>
            </div>
            <p class="adm-note">当前仅统计演职员与地区上映覆盖，不把“有排行”当作全覆盖；已有地区上映记录不代表其他国家未发行。</p>

            <div class="adm-actions">
              <button type="button" class="adm-btn primary" @click="emit('deep', iso2)">深入分析该国家</button>
              <button type="button" class="adm-btn" :disabled="compareFull" :title="compareFull ? '最多比较 3 国' : ''" @click="emit('compare', iso2)">
                {{ inCompare ? '从对比中移除' : '加入国家对比' }}
              </button>
              <button type="button" class="adm-btn" @click="emit('allMovies', iso2)">查看全部电影</button>
              <button type="button" class="adm-btn" @click="emit('quality')">数据质量定位</button>
            </div>
            <p class="adm-note">对比上限 3 国；合拍片可能同时属于多个国家，比较页会显示跨国重合数，不把各国数量相加当合集。</p>
          </template>
        </div>
      </aside>
    </div>
  </Teleport>
</template>

<style scoped>
.quick-backdrop{position:fixed;inset:0;z-index:80;background:var(--mi-backdrop);display:flex;justify-content:flex-end;backdrop-filter:blur(2px)}
.quick-drawer{width:min(620px,100vw);height:100%;background:var(--mi-surface);border-left:1px solid var(--mi-border-strong);box-shadow:var(--mi-shadow-lg);display:flex;flex-direction:column;color:var(--mi-text-soft)}
.quick-head{display:flex;justify-content:space-between;align-items:flex-start;gap:16px;padding:22px 24px 16px;border-bottom:1px solid var(--mi-border);flex-shrink:0}
.quick-head h2{margin:2px 0 4px;font-family:var(--serif);font-size:23px;font-weight:500;color:var(--mi-text-ivory)}
.quick-en{margin-left:10px;font-size:11px;letter-spacing:.06em;color:var(--mi-text-secondary);font-family:var(--sans)}
.quick-meta{margin:0;font-size:10.5px;color:var(--mi-text-muted);line-height:1.6}
.quick-close{border:1px solid var(--mi-border-strong);background:transparent;color:var(--mi-text-secondary);border-radius:8px;width:30px;height:30px;font-size:13px;cursor:pointer;flex:none}
.quick-close:hover{background:var(--mi-surface-hover);color:var(--mi-text)}
.quick-body{padding:18px 24px 28px;overflow:auto;flex:1;min-height:0}
.state-strip{display:flex;gap:8px;align-items:flex-start;font-size:11.5px;line-height:1.7;color:var(--mi-text-soft);margin:0 0 14px;flex-wrap:wrap}
.state-strip.muted{color:var(--mi-text-secondary)}
.link{border:0;background:transparent;color:var(--mi-accent-text);font:inherit;font-size:11.5px;cursor:pointer;text-decoration:underline;text-underline-offset:2px;padding:0}
.quick-drawer :is(button,input,select,a,summary):focus-visible{outline:2px solid var(--mi-accent);outline-offset:2px}
.quick-drawer ::selection{background:var(--mi-selection-bg);color:var(--mi-selection-text)}
</style>