/**
 * Thin, read-only client for the admin resource-analytics API (WP-C).
 *
 * Only existing `/api/admin/tmdb-supplement/*` GET endpoints are used. Fields
 * that WP-A has not shipped yet are typed as optional and rendered as
 * “接口未就绪”, never as a zero. No write, no TMDB fetch, no model call.
 */

export class AdminRequestError extends Error {
  status: number
  code: string
  constructor(message: string, status = 0, code = '') {
    super(message)
    this.name = 'AdminRequestError'
    this.status = status
    this.code = code
  }
}

function describe(status: number, code: string): string {
  if (status === 401) return '登录已失效，请重新登录。'
  if (status === 403) return '需要管理员权限。'
  if (code === 'CACHE_WARMING') return '统计缓存正在后台预热（CACHE_WARMING），请稍后刷新。'
  if (status === 503 || code === 'DATA_UNAVAILABLE') return '统计来源暂时不可用（DATA_UNAVAILABLE）。'
  if (status === 409) return 'TMDB 身份数据存在冲突，等待人工复核。'
  if (status === 400) return '筛选参数不合法（' + (code || 'INVALID_INPUT') + '）。'
  return '资源分析数据暂时不可用，请稍后手动刷新。'
}

function makeAbortError(): Error {
  if (typeof DOMException !== 'undefined') return new DOMException('The operation was aborted.', 'AbortError')
  const err = new Error('The operation was aborted.')
  err.name = 'AbortError'
  return err
}

/** 25 s ceiling so a stalled large-catalog analytics query cannot freeze a panel. */
export function adminSignal(outer?: AbortSignal): AbortSignal | undefined {
  if (typeof AbortSignal === 'undefined') return outer
  const timeout = typeof AbortSignal.timeout === 'function' ? AbortSignal.timeout(25000) : undefined
  if (!outer) return timeout
  if (timeout && typeof AbortSignal.any === 'function') return AbortSignal.any([outer, timeout])
  return outer
}

interface InFlightEntry {
  controller: AbortController
  subscribers: Set<symbol>
  promise: Promise<unknown>
}

const inFlightAdminGets = new Map<string, InFlightEntry>()

export async function adminGet<T>(path: string, signal?: AbortSignal): Promise<T> {
  if (signal?.aborted) throw makeAbortError()
  const cleanPath = path.replace(/^\//, '')
  const url = '/api/admin/' + cleanPath
  let entry = inFlightAdminGets.get(url)
  if (!entry) {
    const controller = new AbortController()
    const subscribers = new Set<symbol>()
    const promise = (async () => {
      const response = await fetch(url, {
        credentials: 'same-origin',
        signal: adminSignal(controller.signal),
      })
      let body: any = null
      try { body = await response.json() } catch { body = null }
      if (!response.ok) {
        throw new AdminRequestError(describe(response.status, body?.code || ''), response.status, body?.code || '')
      }
      if (!body || body.code !== 'OK' || body.data == null) {
        throw new AdminRequestError(
          body?.code === 'DATA_UNAVAILABLE' || body?.code === 'CACHE_WARMING'
            ? describe(503, body.code)
            : '管理数据响应格式异常（缺少 data）。',
          response.status,
          body?.code || 'BAD_ENVELOPE',
        )
      }
      return body.data
    })().finally(() => {
      if (inFlightAdminGets.get(url)?.controller === controller) {
        inFlightAdminGets.delete(url)
      }
    })
    entry = { controller, subscribers, promise }
    inFlightAdminGets.set(url, entry)
  }

  const activeEntry = entry
  const token = Symbol('adminGet')
  activeEntry.subscribers.add(token)

  return new Promise<T>((resolve, reject) => {
    const onAbort = () => {
      activeEntry.subscribers.delete(token)
      if (activeEntry.subscribers.size === 0) {
        inFlightAdminGets.delete(url)
        activeEntry.controller.abort()
      }
      reject(makeAbortError())
    }
    if (signal) {
      if (signal.aborted) {
        onAbort()
        return
      }
      signal.addEventListener('abort', onAbort, { once: true })
    }
    activeEntry.promise.then(
      value => {
        signal?.removeEventListener('abort', onAbort)
        activeEntry.subscribers.delete(token)
        if (signal?.aborted) {
          reject(makeAbortError())
          return
        }
        resolve(value as T)
      },
      error => {
        signal?.removeEventListener('abort', onAbort)
        activeEntry.subscribers.delete(token)
        if (signal?.aborted) {
          reject(makeAbortError())
          return
        }
        reject(error)
      },
    )
  })
}

export interface GenreRow { genre: string; genre_id: number | null; count: number }
export interface DecadeRow { decade: number | null; name: string; count: number }
export interface RatingSummary {
  source: string
  scale: string
  min_votes: number
  valid_samples: number
  total_votes: number
  unweighted_mean: number | null
  vote_weighted_mean: number | null
  below_min_votes_movies: number
}
export interface MovieSummary {
  movie_id: number
  tmdb_id: number | null
  title: string
  release_year: number | null
  vote_average: number | null
  vote_count: number | null
  popularity: number | null
}
export interface CountryQuickRow {
  country_iso2: string
  country_name: string
  movie_count_distinct: number
  share_of_filtered_catalog: number | null
  rated_movie_count: number
  tmdb_avg_rating: RatingSummary
  top_genres: GenreRow[]
  decade_distribution: DecadeRow[]
  /** New per-country coverage fields appear only after WP-A ships them. */
  coverage_share?: number | null
  coverage?: Record<string, number | null> | null
  assets?: Record<string, number | null> | null
}
export interface CreditRankRow { person_id: number; name: string; movie_count: number; top_movies: string[] }
export interface CreditBlock {
  status: string
  topic_status: Record<string, number>
  movies_with_credits: number
  directors: CreditRankRow[]
  cast: CreditRankRow[]
  note?: string
}
export interface KeywordBlock {
  status: string
  movies_total: number
  movies_with_keywords: number
  items: { keyword_id: number; name: string; movie_count: number }[]
  note?: string
}
export interface CompanyBlock {
  status: string
  movies_total: number
  movies_with_companies: number
  items: { company_id: number; name: string; origin_country: string | null; movie_count: number }[]
  note?: string
}
export interface RuntimeBucket { bucket: string; min_minutes: number | null; max_minutes: number | null; count: number }
export interface RuntimeBlock {
  status: string
  movies_total: number
  movies_with_runtime: number
  unknown: number
  buckets: RuntimeBucket[]
  note?: string
}
export interface GenrePairBlock {
  status: string
  movies_total: number
  movies_with_genres: number
  movies_with_multiple_genres: number
  pairs: { genres: string[]; genre_ids?: number[]; movie_count: number }[]
  denominator?: { movies_with_multiple_genres: number; note?: string }
  note?: string
}
export interface ReleaseWindow {
  status: string
  covered_movies: number
  median_lag_days: number | null
  samples: { movie_id: number; country_release_date: string; earliest_release_date: string; lag_days: number }[]
  note?: string
}
export interface DeepCountry extends CountryQuickRow {
  genre_by_decade: { genre: string; genre_id: number | null; total: number; decades: { decade: number | null; count: number }[] }[]
  languages: {
    original_language: { code: string; count: number }[]
    spoken_languages: { code: string; count: number }[]
    note?: string
  }
  companies: { company_id: number; name: string; origin_country: string | null; movie_count: number }[]
  keywords: { keyword_id: number; name: string; movie_count: number }[]
  credits: CreditBlock
  release_window: ReleaseWindow
  rating_distribution: {
    rating_histogram: { name: string; count: number }[]
    vote_count_quantiles: Quantiles
    popularity_quantiles: Quantiles
    note?: string
  }
  top_movies: MovieSummary[]
  top_rated_movies: MovieSummary[]
}
export interface Quantiles { samples: number; p25: number | null; p50: number | null; p75: number | null }
export interface DetailMovie {
  movie_id: number
  tmdb_id: number | null
  title: string
  release_year: number | null
  decade: number | null
  genres: string[]
  countries: string[]
  country_known: boolean
  original_language: string | null
  spoken_languages: string[]
  vote_average: number | null
  vote_count: number | null
  popularity: number | null
  rating_sample_valid: boolean
  rating_observation_count: number
  observed_at: string | null
  latest_fetched_at: string | null
  topic_status: Record<string, string>
  /** WP-A detail filters: which topics are unavailable, and why. */
  missing_fields?: string[]
  missing_reasons?: Record<string, string>
}
export interface DetailFilters {
  keyword?: number | null
  company?: number | null
  missing?: string[]
  sort?: string | null
  order?: string | null
  overlap?: string[]
}
export interface CountriesBlock {
  total: number
  offset: number
  limit: number
  returned: number
  has_more: boolean
  next_offset: number | null
  truncated: boolean
  rows: CountryQuickRow[]
  compact: {
    total: number
    returned: number
    truncated: boolean
    items: { country_iso2: string; country_name: string; movie_count_distinct: number; share_of_filtered_catalog: number | null; rated_movie_count: number }[]
    note?: string
  }
  denominator: { movies: number; note?: string }
}
export interface ComparisonBlock {
  countries: string[]
  union_movie_count: number
  overlap_movie_count: number
  overlap_ratio_of_union: number | null
  pairwise: { countries: string[]; intersection_movie_count: number; union_movie_count: number; jaccard: number | null; shared_movie_ids: number[] }[]
  country_samples?: {
    country_iso2: string
    movie_count_distinct: number
    rated_movie_count: number
    tmdb_avg_rating: RatingSummary
  }[]
  all_countries_intersection_movie_count?: number
  all_countries_intersection_movie_ids?: number[]
  note?: string
}
export interface CoverageBlock {
  movies: number
  countries: { known: number; unknown: number; share_known: number | null; topic_status: Record<string, number> }
  ratings: { valid_samples: number; unknown: number; share_valid: number | null; min_votes: number }
  genres: { movies_with_genres: number }
  keywords: { movies_with_keywords: number }
  companies: { movies_with_companies: number }
  credits: { movies_with_credits: number }
  releases: { movies_with_releases: number }
  topics: Record<string, Record<string, number>>
  assets: Record<string, number | string | null>
}
export interface AnalyticsPayload {
  contract: string
  filters: Record<string, unknown>
  snapshot: { observed_at: string | null; latest_fetched_at: string | null; earliest_fetched_at: string | null; note?: string }
  scope: {
    documents_seen: number
    documents_skipped_after_observed_at: number
    documents_used: number
    duplicate_documents_merged: number
    movies_total: number
    movies_matched: number
    excluded?: number
    excluded_unknown_release_year?: number
    excluded_unknown_observed_at?: number
    repository?: { documents_loaded: number; cap: number; truncated: boolean }
    catalog?: {
      active_movies?: number | null
      tmdb_mapped?: number | null
      unmapped?: number | null
      status?: string
      source?: string
      as_of?: string | null
      note?: string
      /** Legacy keys kept for the first WP-A draft. */
      active?: number
      mapped?: number
    } | null
    cache?: {
      hit: boolean
      status: string
      loaded_at: string | null
      watermark?: { document_count: number; max_fetched_at: string | null; max_updated_at: string | null }
      ttl_seconds?: number
      age_seconds?: number
      load_count?: number
      note?: string
    } | null
  }
  coverage: CoverageBlock
  states: { catalog: string; countries: string; ratings: string; note?: string }
  global: {
    movie_count_distinct: number
    movies_with_countries: number
    movies_country_unknown: number
    rated_movie_count: number
    tmdb_avg_rating: RatingSummary
    top_genres: GenreRow[]
    decade_distribution: DecadeRow[]
    original_language: { code: string; count: number }[]
    spoken_languages: { code: string; count: number }[]
    top_movies: MovieSummary[]
    top_rated_movies: MovieSummary[]
    top_popular_movies: MovieSummary[]
    rating_distribution: { rating_histogram: { name: string; count: number }[]; vote_count_quantiles: Quantiles; popularity_quantiles: Quantiles; note?: string }
    assets: Record<string, number | string | null>
    /** WP-A global extensions. */
    top_keywords?: KeywordBlock
    top_companies?: CompanyBlock
    credits?: CreditBlock
    runtime_distribution?: RuntimeBlock
    genre_pairs?: GenrePairBlock
  }
  countries: CountriesBlock
  country_analysis: { denominator: number; rows: DeepCountry[]; note?: string } | null
  comparison: ComparisonBlock | null
  details: {
    total: number; limit: number; offset: number; returned: number; has_more: boolean
    total_before_detail_filters?: number
    applied_filters?: { keyword?: number | null; company?: number | null; missing?: string[]; sort?: string | null; order?: string | null; overlap?: string[] }
    items: DetailMovie[]; note?: string
  }
  issues: string[] | null
  notes: string[]
  /** WP-A additions, rendered defensively when present. */
  quality?: unknown
  batches?: unknown
}

export const MISSING_TOPICS = [
  'countries', 'keywords', 'companies', 'credits', 'poster', 'overview', 'releases', 'spoken_languages',
] as const
export const DETAIL_SORTS = ['title', 'year', 'rating', 'votes', 'popularity'] as const

export interface AnalyticsFilters extends DetailFilters {
  country?: string
  countries?: string[]
  detailCountry?: string | string[]
  genre?: string
  decade?: string
  yearFrom?: number | null
  yearTo?: number | null
  minVotes?: number
  top?: number
  limit?: number
  offset?: number
  countryLimit?: number
  countryOffset?: number
}

/** Pure query builder, covered by tools/test-admin-v2-context.mjs. */
export function analyticsQuery(filters: AnalyticsFilters): string {
  const params = new URLSearchParams()
  const country = (filters.country || '').trim().toUpperCase()
  if (/^[A-Z]{2}$/.test(country)) params.set('country', country)
  const compare = (filters.countries || [])
    .map(code => String(code).trim().toUpperCase())
    .filter((code, index, all) => /^[A-Z]{2}$/.test(code) && all.indexOf(code) === index)
    .slice(0, 3)
  if (compare.length) params.set('countries', compare.join(','))
  const rawDetailCountries = Array.isArray(filters.detailCountry)
    ? filters.detailCountry
    : filters.detailCountry
      ? String(filters.detailCountry).split(',')
      : []
  const detailCountry = rawDetailCountries
    .map(code => String(code).trim().toUpperCase())
    .filter((code, index, all) => /^[A-Z]{2}$/.test(code) && all.indexOf(code) === index)
    .slice(0, 3)
  if (detailCountry.length) params.set('detail_country', detailCountry.join(','))
  if (filters.genre) params.set('genre', filters.genre)
  if (filters.decade) params.set('decade', filters.decade)
  if (filters.yearFrom != null) params.set('year_from', String(filters.yearFrom))
  if (filters.yearTo != null) params.set('year_to', String(filters.yearTo))
  if (filters.minVotes != null) params.set('min_votes', String(filters.minVotes))
  if (filters.top != null) params.set('top', String(filters.top))
  if (filters.limit != null) params.set('limit', String(filters.limit))
  if (filters.offset != null) params.set('offset', String(filters.offset))
  if (filters.countryLimit != null) params.set('country_limit', String(filters.countryLimit))
  if (filters.countryOffset != null) params.set('country_offset', String(filters.countryOffset))
  if (filters.keyword != null && Number.isFinite(filters.keyword) && filters.keyword > 0) params.set('keyword', String(filters.keyword))
  if (filters.company != null && Number.isFinite(filters.company) && filters.company > 0) params.set('company', String(filters.company))
  const missing = (filters.missing || []).filter((topic, index, all) => (MISSING_TOPICS as readonly string[]).includes(topic) && all.indexOf(topic) === index)
  if (missing.length) params.set('missing', missing.join(','))
  if (filters.sort && (DETAIL_SORTS as readonly string[]).includes(filters.sort)) {
    params.set('sort', filters.sort)
    params.set('order', filters.order === 'asc' ? 'asc' : 'desc')
  }
  const overlap = (filters.overlap || [])
    .map(code => String(code).trim().toUpperCase())
    .filter((code, index, all) => /^[A-Z]{2}$/.test(code) && all.indexOf(code) === index)
    .slice(0, 3)
  if (overlap.length) params.set('overlap', overlap.join(','))
  return params.toString()
}

export function loadAnalytics(filters: AnalyticsFilters, signal?: AbortSignal): Promise<AnalyticsPayload> {
  const query = analyticsQuery(filters)
  return adminGet<AnalyticsPayload>('tmdb-supplement/analytics' + (query ? '?' + query : ''), signal)
}

export interface TopicCoverage {
  documents: number
  topics: Record<string, Record<string, number>>
  statuses: string[]
  observed_at: string | null
}
export function loadCoverage(signal?: AbortSignal): Promise<TopicCoverage> {
  return adminGet<TopicCoverage>('tmdb-supplement/coverage', signal)
}

export interface MissingRow {
  movie_id: number
  tmdb_id: number | null
  topic: string | null
  status: string | null
  attempt_at: string | null
  error_code: string | null
}
export interface MissingPage {
  limit: number; offset: number; returned: number; has_more: boolean; items: MissingRow[]
}
export function loadMissing(offset: number, limit = 50, signal?: AbortSignal): Promise<MissingPage> {
  return adminGet<MissingPage>(`tmdb-supplement/missing?limit=${limit}&offset=${offset}`, signal)
}

export const QUALITY_TOPICS = [
  'base', 'countries', 'credits', 'keywords', 'companies', 'spoken_languages',
  'releases', 'metrics', 'poster', 'overview', 'tmdb_identity',
] as const
export const QUALITY_CATEGORIES = ['NOT_FETCHED', 'MISSING', 'EMPTY', 'PRESENT', 'FAILED', 'UNMAPPED'] as const

export interface QualityTopicCount {
  NOT_FETCHED?: number
  MISSING?: number
  EMPTY?: number
  PRESENT?: number
  FAILED?: number
  UNMAPPED?: number
  total: number
  counted_movies: number
  present_ratio: number | null
}
export interface QualitySeverityTopic { topic: string; movies: number }
export interface QualitySeverityRow {
  name: string
  movies: number
  missing_movies: number
  missing_ratio: number | null
  top_missing_topics: QualitySeverityTopic[]
  country_iso2?: string
  genre_key?: string
  decade?: number | null
}
export interface QualityProblem {
  movie_id: number
  tmdb_id: number | null
  title: string
  release_year: number | null
  topic: string
  category: string
  actionable: boolean
  latest_fetched_at: string | null
  topic_observed_at: string | null
  source: string
}
export interface QualityPayload {
  contract: string
  consumes?: string
  catalog: {
    active_movies: number | null; tmdb_mapped: number | null; unmapped: number | null
    mapped_documents_loaded?: number | null; mapped_documents_seen?: number | null
    source?: string; as_of?: string | null; note?: string
  }
  denominator: { mode: 'active' | 'mapped'; movies: number | null; note?: string }
  states: { statuses: string[]; unmapped: number; note?: string }
  topics: Record<string, QualityTopicCount>
  assets?: {
    movies: number; movies_with_base: number; poster_present: number; poster_missing: number
    overview_present: number; overview_missing: number; backdrop_present: number; note?: string
  }
  severity?: {
    definition: string
    by_country: QualitySeverityRow[]
    by_genre: QualitySeverityRow[]
    by_decade: QualitySeverityRow[]
    unknown_country_movies: number
    note?: string
  }
  problems: {
    total: number; limit: number; offset: number; returned: number; has_more: boolean
    topic_filter?: string | null; category_filter?: string | null
    items: QualityProblem[]; note?: string
  }
  as_of?: string | null
  scope?: { repository?: { documents_loaded: number; cap: number; truncated: boolean }; unmapped_loaded?: number; unmapped_cap?: number }
  cache?: AnalyticsPayload['scope']['cache']
  issues?: string[]
  notes?: string[]
}
export interface QualityFilters {
  denominator?: 'active' | 'mapped'
  top?: number
  limit?: number
  offset?: number
  topic?: string
  category?: string
}
export function qualityQuery(filters: QualityFilters): string {
  const params = new URLSearchParams()
  if (filters.denominator === 'mapped') params.set('denominator', 'mapped')
  if (filters.top != null) params.set('top', String(filters.top))
  if (filters.limit != null) params.set('limit', String(filters.limit))
  if (filters.offset != null) params.set('offset', String(filters.offset))
  if (filters.topic && (QUALITY_TOPICS as readonly string[]).includes(filters.topic)) params.set('topic', filters.topic)
  if (filters.category && (QUALITY_CATEGORIES as readonly string[]).includes(filters.category)) params.set('category', filters.category)
  return params.toString()
}
export function loadQuality(filters: QualityFilters, signal?: AbortSignal): Promise<QualityPayload> {
  const query = qualityQuery(filters)
  return adminGet<QualityPayload>('tmdb-supplement/quality' + (query ? '?' + query : ''), signal)
}

export interface BatchCounts {
  target_documents?: number | null; success_count?: number | null; failed_count?: number | null
  skipped_count?: number | null; executed_operations?: number | null
}
export interface BatchItemStatus {
  inserted?: number | null; updated?: number | null; skipped?: number | null
  review?: number | null; error?: number | null; total?: number | null
}
export interface BatchItem {
  batch_id: number; source: string; mode: string; status: string
  started_at: string | null; finished_at: string | null
  source_snapshot_ref?: string | null
  reported_counts?: BatchCounts | null
  counts_source?: string
  item_status?: BatchItemStatus | null
  error_codes?: { error_code: string; count: number }[] | null
  error_summary?: string | null
  missing_fields?: string[]
  note?: string
}
export interface BatchesPayload {
  total: number; limit: number; offset: number; returned: number; has_more: boolean
  statuses: string[]
  source: string
  items: BatchItem[]
}
export interface BatchFilters { limit?: number; offset?: number; status?: string }
export function batchesQuery(filters: BatchFilters): string {
  const params = new URLSearchParams()
  if (filters.limit != null) params.set('limit', String(filters.limit))
  if (filters.offset != null) params.set('offset', String(filters.offset))
  if (filters.status) params.set('status', filters.status)
  return params.toString()
}
export function loadBatches(filters: BatchFilters, signal?: AbortSignal): Promise<BatchesPayload> {
  const query = batchesQuery(filters)
  return adminGet<BatchesPayload>('tmdb-supplement/batches' + (query ? '?' + query : ''), signal)
}

const MAX_POSTER_CACHE = 300
const posterCache = new Map<number, Promise<string | null>>()

/**
 * Representative-movie cards need a poster, but the analytics kernel
 * deliberately returns no raw payload. The public catalog detail endpoint is
 * the existing, read-only source; failures degrade to a styled placeholder.
 */
export function loadPoster(movieId: number): Promise<string | null> {
  if (!Number.isFinite(movieId) || movieId <= 0) return Promise.resolve(null)
  const cached = posterCache.get(movieId)
  if (cached) {
    posterCache.delete(movieId)
    posterCache.set(movieId, cached)
    return cached
  }
  const request = (async () => {
    try {
      const signal = typeof AbortSignal !== 'undefined' && typeof AbortSignal.timeout === 'function'
        ? AbortSignal.timeout(10000)
        : undefined
      const response = await fetch('/api/catalog/movies/' + movieId, { credentials: 'same-origin', signal })
      if (!response.ok) return null
      const body = await response.json().catch(() => null)
      if (!body || body.code !== 'OK') return null
      const assets = body?.data?.assets
      if (!Array.isArray(assets)) return null
      const poster = assets.find((asset: any) => asset?.asset_type === 'poster' && asset?.url)
      return typeof poster?.url === 'string' ? poster.url : null
    } catch { return null }
  })().then(url => {
    if (url === null) posterCache.delete(movieId)
    return url
  })
  if (posterCache.size >= MAX_POSTER_CACHE) {
    const oldestKey = posterCache.keys().next().value
    if (oldestKey !== undefined) posterCache.delete(oldestKey)
  }
  posterCache.set(movieId, request)
  return request
}

export function coverageOfCountry(row: CountryQuickRow | undefined | null): number | null {
  if (!row) return null
  const candidates: unknown[] = [
    row.coverage_share,
    row.coverage?.poster,
    row.coverage?.overall,
    row.assets?.poster_share,
  ]
  for (const candidate of candidates) {
    if (typeof candidate === 'number' && Number.isFinite(candidate)) {
      return candidate > 1 ? candidate / 100 : candidate
    }
  }
  return null
}

const numberFormat = new Intl.NumberFormat('zh-CN')

export function fmtNumber(value: number | null | undefined): string {
  return typeof value === 'number' && Number.isFinite(value) ? numberFormat.format(value) : '—'
}
export function fmtDecimal(value: number | null | undefined, digits = 2): string {
  return typeof value === 'number' && Number.isFinite(value) ? value.toFixed(digits) : '—'
}
export function fmtPercent(value: number | null | undefined, digits = 1, fromRatio = true): string {
  if (typeof value !== 'number' || !Number.isFinite(value)) return '—'
  return (fromRatio ? value * 100 : value).toFixed(digits) + '%'
}
export function fmtScore(value: number | null | undefined): string {
  return typeof value === 'number' && Number.isFinite(value) ? value.toFixed(1) : '—'
}