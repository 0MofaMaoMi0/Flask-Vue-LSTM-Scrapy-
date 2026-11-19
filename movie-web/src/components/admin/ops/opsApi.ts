/**
 * Read-only client and pure presentation helpers for the admin operations
 * panels (WP-D: pages 06/07/08/09/11/12/13).
 *
 * Contract source: `WP-B/契约-运营分析.md` and `WP-A/result.md §3.3`. It does
 * not import the resource-analytics module at runtime so
 * `tools/test-admin-v2-ops-ui.mjs` can load the pure helpers on their own.
 *
 * Rules encoded here, not in each panel:
 * - unknown values stay unknown (`null` → “未知”/“—”); a zero is only shown
 *   when the backend said zero;
 * - every status string maps to one public label + tone;
 * - only allow-listed query parameters leave the browser.
 */

export type Days = 7 | 30 | 90
export const DAY_OPTIONS: readonly Days[] = [7, 30, 90]
export const DEFAULT_DAYS: Days = 30

export type EventKind = 'favorite' | 'want' | 'watched' | 'rating' | 'not_interested'
export type PrefDimension = 'genre' | 'decade' | 'country'
export type InterestDimension = 'genre' | 'decade' | 'country'

export const EVENT_KINDS: readonly EventKind[] = ['favorite', 'want', 'watched', 'rating', 'not_interested']
export const PREF_DIMENSIONS: readonly PrefDimension[] = ['genre', 'decade', 'country']
export const INTEREST_DIMENSIONS: readonly InterestDimension[] = ['genre', 'decade', 'country']
export const BATCH_STATUSES = ['pending', 'running', 'succeeded', 'partial', 'failed'] as const

export const KIND_LABELS: Record<string, string> = {
  favorite: '收藏', want: '想看', watched: '已看', rating: '评分', not_interested: '不感兴趣',
}
export const SIGNAL_LABELS: Record<string, string> = {
  favorite: '收藏', want: '想看', high_rating: '高评分',
}
export const DIMENSION_LABELS: Record<string, string> = {
  genre: '电影类型', decade: '上映年代', country: '制作国家',
}
export const AI_SOURCE_LABELS: Record<string, string> = {
  REAL: '真实 BYOK', MOCK: 'Mock', UNKNOWN: '来源未知', UNCONFIRMED: '来源未确认',
}

export class OpsRequestError extends Error {
  status: number
  code: string
  constructor(message: string, status = 0, code = '') {
    super(message)
    this.name = 'OpsRequestError'
    this.status = status
    this.code = code
  }
}

function describe(status: number, code: string): string {
  if (status === 401) return '登录已失效，请重新登录后再试。'
  if (status === 403) return '需要管理员权限，当前账号无权查看。'
  if (status === 400) return '筛选参数不合法（' + (code || 'INVALID_INPUT') + '），请调整后重试。'
  if (status === 404) return '该统计接口尚未部署（NOT_FOUND）。'
  if (status === 503) return '统计来源暂时不可用（DATA_UNAVAILABLE），请稍后手动刷新。'
  return '统计接口请求失败，请稍后手动刷新。'
}

/** 20 s ceiling so a stalled statistics query cannot freeze a panel. */
export function opsSignal(outer?: AbortSignal): AbortSignal | undefined {
  if (typeof AbortSignal === 'undefined') return outer
  const timeout = typeof AbortSignal.timeout === 'function' ? AbortSignal.timeout(20000) : undefined
  if (!outer) return timeout
  if (timeout && typeof AbortSignal.any === 'function') return AbortSignal.any([outer, timeout])
  return outer
}

export async function opsGet<T>(path: string, signal?: AbortSignal): Promise<T> {
  const response = await fetch('/api/admin/' + path.replace(/^\/+/, ''), {
    credentials: 'same-origin',
    headers: { Accept: 'application/json' },
    signal: opsSignal(signal),
  })
  let body: any = null
  try { body = await response.json() } catch { body = null }
  if (!response.ok) {
    throw new OpsRequestError(describe(response.status, body?.code || ''), response.status, body?.code || '')
  }
  if (!body || body.code !== 'OK' || body.data == null) {
    throw new OpsRequestError('管理数据响应格式异常（缺少 data）。', response.status, body?.code || 'BAD_ENVELOPE')
  }
  return body.data as T
}

/* ---------------------------------------------------------------- windows */

export function parseDays(value: unknown, fallback: Days = DEFAULT_DAYS): Days {
  const raw = typeof value === 'number' ? String(value) : String(value ?? '').trim()
  if (raw !== '7' && raw !== '30' && raw !== '90') return fallback
  return Number(raw) as Days
}

/* ---------------------------------------------------------------- queries */

export function usersQuery(days: Days, kind?: EventKind | null): string {
  const params = new URLSearchParams({ days: String(parseDays(days)) })
  if (kind && (EVENT_KINDS as readonly string[]).includes(kind)) params.set('kind', kind)
  return 'users/analytics?' + params.toString()
}

export function interestQuery(days: Days, dimension: InterestDimension = 'genre'): string {
  const params = new URLSearchParams({ days: String(parseDays(days)) })
  params.set('dimension', (INTEREST_DIMENSIONS as readonly string[]).includes(dimension) ? dimension : 'genre')
  return 'interest-match?' + params.toString()
}

export function recommendationQuery(days: Days): string {
  return 'recommendation/analytics?days=' + parseDays(days)
}

export function aiQuery(days: Days): string {
  return 'ai/analytics?days=' + parseDays(days)
}

export interface EventQueryInput {
  limit?: number
  offset?: number
  from?: string | null
  to?: string | null
  kind?: EventKind | null
  movieId?: number | null
}

function bounded(value: unknown, low: number, high: number, fallback: number): number {
  const parsed = Number(value)
  if (!Number.isFinite(parsed)) return fallback
  return Math.min(high, Math.max(low, Math.trunc(parsed)))
}

/** `limit` 1–100, `offset` ≥ 0, dates `YYYY-MM-DD` or ISO-8601 (UTC). */
export function eventsQuery(input: EventQueryInput = {}): string {
  const params = new URLSearchParams()
  params.set('limit', String(bounded(input.limit, 1, 100, 20)))
  params.set('offset', String(bounded(input.offset, 0, 100000, 0)))
  const from = String(input.from ?? '').trim()
  const to = String(input.to ?? '').trim()
  if (from) params.set('from', from)
  if (to) params.set('to', to)
  if (input.kind && (EVENT_KINDS as readonly string[]).includes(input.kind)) params.set('kind', input.kind)
  if (typeof input.movieId === 'number' && Number.isInteger(input.movieId) && input.movieId > 0) {
    params.set('movie_id', String(input.movieId))
  }
  return 'events?' + params.toString()
}

export interface BatchQueryInput {
  limit?: number
  offset?: number
  status?: string | null
  startedFrom?: string | null
  startedTo?: string | null
}

/** `limit` 1–100, `offset` 0–100000, `status` from the public enum. */
export function batchesQuery(input: BatchQueryInput = {}): string {
  const params = new URLSearchParams()
  params.set('limit', String(bounded(input.limit, 1, 100, 10)))
  params.set('offset', String(bounded(input.offset, 0, 100000, 0)))
  if (input.status && (BATCH_STATUSES as readonly string[]).includes(input.status)) params.set('status', input.status)
  const from = String(input.startedFrom ?? '').trim()
  const to = String(input.startedTo ?? '').trim()
  if (from) params.set('started_from', from)
  if (to) params.set('started_to', to)
  return 'tmdb-supplement/batches?' + params.toString()
}

/* ------------------------------------------------------------- formatting */

export const EM_DASH = '—'

export function fmtInt(value: number | null | undefined): string {
  return typeof value === 'number' && Number.isFinite(value) ? value.toLocaleString('zh-CN') : EM_DASH
}

/** Token counts: `null` means “not reported”, which is never a zero. */
export function fmtToken(value: number | null | undefined): string {
  return typeof value === 'number' && Number.isFinite(value) ? value.toLocaleString('zh-CN') : '未知'
}

export function fmtRatio(value: number | null | undefined, digits = 1): string {
  if (typeof value !== 'number' || !Number.isFinite(value)) return EM_DASH
  return (value * 100).toFixed(digits) + '%'
}

export function fmtScore(value: number | null | undefined, digits = 1): string {
  return typeof value === 'number' && Number.isFinite(value) ? value.toFixed(digits) : EM_DASH
}

export function fmtHours(value: number | null | undefined): string {
  if (typeof value !== 'number' || !Number.isFinite(value)) return EM_DASH
  if (value < 1) return Math.round(value * 60) + ' 分钟'
  return value.toFixed(1) + ' 小时'
}

/** Backend `items[].at` keeps a Z-less UTC isoformat; display stays UTC. */
function utcDate(value: string): Date {
  const text = String(value).trim()
  const zoned = /(?:[zZ]|[+-]\d{2}:?\d{2})$/.test(text)
  return new Date(zoned ? text : text + 'Z')
}

/** UTC wall clock kept explicit so cross-day aggregates are never misread. */
export function fmtUtc(value: string | null | undefined): string {
  if (!value) return EM_DASH
  const date = utcDate(String(value))
  if (Number.isNaN(date.getTime())) return String(value)
  return date.toISOString().slice(0, 16).replace('T', ' ') + ' UTC'
}

/** Legacy event rows (`items[].at`) carry no `Z`; they are still UTC. */
export function fmtUtcNaive(value: string | null | undefined): string {
  if (!value) return EM_DASH
  const date = utcDate(String(value))
  if (Number.isNaN(date.getTime())) return String(value)
  return date.toISOString().slice(0, 16).replace('T', ' ') + ' UTC'
}

export function fmtDate(value: string | null | undefined): string {
  if (!value) return EM_DASH
  const date = utcDate(String(value))
  if (Number.isNaN(date.getTime())) return String(value).slice(0, 10) || EM_DASH
  return date.toISOString().slice(0, 10)
}

export function windowRange(from: string | null | undefined, to: string | null | undefined): string {
  if (!from && !to) return '未指定区间'
  return fmtDate(from) + ' → ' + fmtDate(to)
}

/* ---------------------------------------------------------------- status */

export type Tone = 'ok' | 'warn' | 'bad' | 'muted'

export interface StatusMeta { label: string; tone: Tone; note?: string }

const STATUS_META: Record<string, StatusMeta> = {
  available: { label: '可用', tone: 'ok' },
  complete: { label: '完整', tone: 'ok' },
  normal: { label: '正常', tone: 'ok' },
  succeeded: { label: '成功', tone: 'ok' },
  partial: { label: '部分缺失', tone: 'warn' },
  observing: { label: '观察中', tone: 'warn' },
  running: { label: '执行中', tone: 'warn' },
  insufficient_sample: { label: '样本不足', tone: 'warn' },
  suppressed: { label: '已隐藏（小样本）', tone: 'warn' },
  pending: { label: '等待中', tone: 'muted' },
  not_evaluable: { label: '不可评价', tone: 'muted' },
  not_called: { label: '无调用', tone: 'muted' },
  not_instrumented: { label: '未采集', tone: 'muted' },
  unknown: { label: '未知', tone: 'muted' },
  unavailable: { label: '不可用', tone: 'bad' },
  abnormal: { label: '异常', tone: 'bad' },
  failed: { label: '失败', tone: 'bad' },
}

export function statusMeta(status: string | null | undefined): StatusMeta {
  const key = String(status ?? '').trim().toLowerCase()
  return STATUS_META[key] || { label: key || '未知', tone: 'muted' }
}

export function toneClass(tone: Tone): string {
  return 'tone-' + tone
}

/* ----------------------------------------------------------------- prefs */

export const PREF_PREFIX = 'mi2_admin_pref_'
export const PREF_KEYS = {
  defaultDays: 'default_days',
  preferenceBasis: 'preference_basis',
} as const

function storage(): Storage | null {
  try {
    return typeof localStorage === 'undefined' ? null : localStorage
  } catch {
    return null
  }
}

export function readPref(key: string): string | null {
  const store = storage()
  if (!store) return null
  try { return store.getItem(PREF_PREFIX + key) } catch { return null }
}

export function writePref(key: string, value: string): void {
  const store = storage()
  if (!store) return
  try { store.setItem(PREF_PREFIX + key, value) } catch { /* private mode: keep working */ }
}

export function clearPrefs(): void {
  const store = storage()
  if (!store) return
  try {
    for (const key of Object.keys(PREF_KEYS)) {
      store.removeItem(PREF_PREFIX + PREF_KEYS[key as keyof typeof PREF_KEYS])
    }
  } catch { /* ignore */ }
}

/* ------------------------------------------------------------------ data */

export interface WindowBlock { days: number; from: string; to: string; timezone: string }
export interface DailyCount { date: string; count: number }
export interface BoardRow { movie_id: number; title: string; count: number }
export interface ShareRow { name: string; count: number; share: number | null }

export interface PreferenceSignal {
  status: string
  reason?: string | null
  definition?: string
  sample_size?: { users?: number | null; pairs?: number | null }
  by_user: Partial<Record<PrefDimension, ShareRow[]>>
  by_pair: Partial<Record<PrefDimension, ShareRow[]>>
}

export interface UsersAnalytics {
  window: WindowBlock
  as_of: string
  status: string
  source?: string
  sample_size?: Record<string, number | null>
  kind_filter?: string | null
  definition?: Record<string, string>
  registered: { total: number; active: number; new_in_window: number }
  daily: { registrations: DailyCount[]; events: DailyCount[]; active_accounts: DailyCount[] }
  current_states: Record<string, number | string | null>
  avg_events_per_active_account: { value: number | null; status: string; definition?: string }
  preference: {
    status: string
    source?: string
    reason?: string | null
    definition?: string
    sample_size?: { users?: number | null; pairs?: number | null }
    signals: Record<string, PreferenceSignal>
  }
  boards: {
    status: string
    definition?: string
    most_events: BoardRow[]
    most_favorited: BoardRow[]
    most_wanted: BoardRow[]
  }
}

export interface InterestCategory {
  key: string
  name: string
  status: string
  supply: { movies: number; share: number | null }
  interest: {
    favorite_pairs: number; want_pairs: number; high_rating_pairs: number
    positive_pairs: number; users: number; share: number | null
  }
  exposure: { impressions: number; share: number | null }
  clicks: { clicks: number; ctr: number | null }
  sample_size?: { movies?: number; interest_pairs?: number; impressions?: number; clicks?: number }
}

export interface InterestMatch {
  window: WindowBlock
  as_of: string
  dimension: InterestDimension
  status: string
  source?: string
  reason?: string | null
  definition?: Record<string, string>
  thresholds?: { min_interest_pairs: number; min_impressions: number }
  sample_size?: { positive_pairs?: number | null; impressions?: number | null }
  supply: { active_movies: number | null; classified_movies: number | null; unknown_movies: number | null }
  interest: {
    signal_counts: Record<string, number>
    any_positive_pairs: number | null
    users: number | null
    attributable_pairs?: number | null
    unattributable_pairs?: number | null
  }
  exposure: {
    impressions: number | null; attributable_impressions: number | null
    unattributable_impressions: number | null; clicks: number | null; status?: string
  }
  categories: InterestCategory[]
}

export interface RecommendationChannel {
  channel: string; impressions: number; clicks: number; ctr: number | null; status?: string
}
export interface RecommendationAnalytics {
  window: WindowBlock
  as_of: string
  status: string
  source?: string
  reason?: string | null
  sample_size?: { impressions?: number | null; clicks?: number | null }
  definition?: Record<string, string>
  totals: {
    impressions: number | null; clicks: number | null; ctr: number | null
    channels: number | null; accounts: number | null; unattributed_clicks: number | null
  }
  channels: RecommendationChannel[]
  daily: { date: string; impressions: number; clicks: number; ctr: number | null }[]
  accounts?: { value: number | null; status: string; definition?: string }
  funnel: { status: string; reason?: string | null; available_steps?: string[] }
  offline_evaluation: { status: string; reason?: string | null }
}

export interface RecommendationFeedbackLegacy {
  impressions: number
  clicks: number
  ctr: number | null
  channels: RecommendationChannel[]
  ranking: {
    status: string; k: number
    precision_at_k?: number | null; recall_at_k?: number | null; ndcg_at_k?: number | null
    users?: number; batches?: number
  }
  as_of?: string
  window?: { days: number }
  status?: string
}

export interface AiStatusCounts {
  calls: number
  success: number
  failed: number
  interrupted: number
  timeout: number
  unknown: number
  success_rate: number | null
}
export interface AiProvenance extends AiStatusCounts {
  source: string
  known_input_tokens?: number | null
  known_output_tokens?: number | null
  unknown_usage_calls?: number | null
}
export interface AiDaily {
  date: string; calls: number
  real: number; mock: number; unknown_provenance: number
  success?: number; failed?: number; unknown?: number
}
export interface AiModel {
  model: string; source: string; calls: number; success: number
  success_rate: number | null
  known_input_tokens: number | null
  known_output_tokens: number | null
  unknown_usage_calls: number | null
  known_cached_input_tokens?: number | null
  cached_reported_calls?: number
}
export interface AiAnalytics {
  window: WindowBlock
  as_of: string
  status: string
  source?: string
  reason?: string | null
  sample_size?: { calls?: number | null }
  definition?: Record<string, string>
  totals: AiStatusCounts & { status: string }
  provenance: AiProvenance[]
  daily: AiDaily[]
  models: AiModel[]
  tokens: {
    status: string
    known_input_tokens: number | null
    known_output_tokens: number | null
    known_total_tokens: number | null
    unknown_usage_calls: number | null
    definition?: string
  }
  cached_input: { status: string; tokens: number | null; reported_calls: number | null; definition?: string; reason?: string }
  errors: { status: string; reason?: string }
  latency: { status: string; reason?: string }
  agent: { status: string; tool_calls?: number | null; valid_movie_cards?: number | null; reason?: string }
}

export interface AiGroupLegacy {
  provenance: string
  calls: number
  unknown_calls: number
  known_input_tokens: number | null
  known_output_tokens: number | null
  known_total_tokens: number | null
  known_cached_input_tokens: number | null
  input_reported_calls: number
  output_reported_calls: number
  cached_input_reported_calls: number
}
export interface AiUsageLegacy {
  status: string
  calls: number
  unknown_calls: number
  known_input_tokens: number | null
  known_output_tokens: number | null
  known_total_tokens: number | null
  not_dispatched_calls: number
  groups: AiGroupLegacy[]
  models: { name: string; provenance: string; calls: number; unknown_calls: number; known_input_tokens: number | null; known_output_tokens: number | null }[]
  statuses: { name: string; calls: number }[]
  daily: { date: string; provenance: string; calls: number }[]
}

export interface HealthPanelData {
  db_time?: string | null
  movies?: number | null
  m7_configured?: boolean
  admin?: string
  config?: { schema?: string; activity_schema?: string }
  stats?: {
    status: string
    source?: string
    as_of?: string
    sample_size?: { blocks_recorded?: number }
    process_started_at?: string | null
    last_success?: Record<string, string | null>
    definition?: string
  }
  recommendation_tracking?: {
    status: string
    source?: string
    sample_size?: number | null
    as_of?: string
    rule?: string
    definition?: string
    reason?: string | null
    last_impression_at?: string | null
    hours_since_last_impression?: number | null
    impressions_7d?: number | null
    impressions_24h?: number | null
  }
  ai_failures?: {
    status: string
    source?: string
    sample_size?: number | null
    as_of?: string
    rule?: string
    definition?: string
    reason?: string | null
    calls?: number | null
    failed?: number | null
    failure_rate?: number | null
  }
}

export interface BatchItemStatus {
  inserted?: number; updated?: number; skipped?: number
  review?: number; error?: number; total?: number
}
export interface BatchItem {
  batch_id: number
  source?: string
  mode?: string | null
  status: string
  started_at?: string | null
  finished_at?: string | null
  reported_counts?: Record<string, number | null> | null
  counts_source?: string
  item_status?: BatchItemStatus | null
  error_codes?: { error_code: string; count: number }[] | null
  error_summary?: string | null
  missing_fields?: string[]
  note?: string
}
export interface BatchesPage {
  total: number
  limit: number
  offset: number
  returned: number
  has_more: boolean
  statuses?: string[]
  source?: string
  items: BatchItem[]
}

export interface EventRow {
  at: string | null
  user: string
  title: string | null
  movie_id?: number | null
  kind?: string | null
  action?: string | null
}
export interface EventsPage {
  items: EventRow[]
  total: number
  limit: number
  offset: number
  has_more: boolean
  filters?: { from?: string | null; to?: string | null; kind?: string | null; movie_id?: number | null }
  status?: string
  source?: string
  sample_size?: number
  as_of?: string
  definition?: string
}

export interface MetricsDefinitions {
  version: string
  as_of: string
  status: string
  source?: string
  timezone: string
  windows: number[]
  constants: Record<string, string | number | boolean | null>
  definitions: {
    key: string; title: string; definition: string
    source?: string; unit?: string; status?: string
  }[]
  notes?: string[]
}

/* ---------------------------------------------------------------- loaders */

/**
 * A 200 envelope with the wrong body must not crash a panel: the caller turns
 * this into the panel-level error box instead of a blank screen.
 */
function badShape(message: string): never {
  throw new OpsRequestError('管理数据响应格式异常：' + message, 200, 'BAD_SHAPE')
}

function hasKeys(value: unknown, keys: string[]): boolean {
  return !!value && typeof value === 'object' && keys.every(key => key in (value as Record<string, unknown>))
}

export async function loadUsersAnalytics(days: Days, kind?: EventKind | null, signal?: AbortSignal) {
  const data = await opsGet<UsersAnalytics>(usersQuery(days, kind), signal)
  if (!hasKeys(data, ['window', 'registered', 'daily', 'boards'])) return badShape('缺少 window / registered / daily。')
  return data
}
export async function loadInterestMatch(days: Days, dimension: InterestDimension, signal?: AbortSignal) {
  const data = await opsGet<InterestMatch>(interestQuery(days, dimension), signal)
  if (!hasKeys(data, ['dimension', 'supply', 'interest', 'categories'])) return badShape('缺少 dimension / supply / categories。')
  return data
}
export async function loadRecommendationAnalytics(days: Days, signal?: AbortSignal) {
  const data = await opsGet<RecommendationAnalytics>(recommendationQuery(days), signal)
  if (!hasKeys(data, ['totals', 'channels', 'daily', 'funnel'])) return badShape('缺少 totals / channels / funnel。')
  return data
}
export async function loadRecommendationFeedbackLegacy(days: Days, signal?: AbortSignal) {
  const data = await opsGet<RecommendationFeedbackLegacy>('recommendation-feedback?days=' + parseDays(days) + '&k=10', signal)
  if (!hasKeys(data, ['impressions', 'clicks'])) return badShape('旧接口缺少 impressions / clicks。')
  return data
}
export async function loadAiAnalytics(days: Days, signal?: AbortSignal) {
  const data = await opsGet<AiAnalytics>(aiQuery(days), signal)
  if (!hasKeys(data, ['totals', 'provenance', 'tokens'])) return badShape('缺少 totals / provenance / tokens。')
  return data
}
export async function loadAiUsageLegacy(days: Days, signal?: AbortSignal) {
  const data = await opsGet<AiUsageLegacy>('ai-usage?days=' + parseDays(days), signal)
  if (!hasKeys(data, ['status', 'groups'])) return badShape('旧用量接口缺少 status / groups。')
  return data
}
export async function loadHealth(signal?: AbortSignal) {
  const data = await opsGet<HealthPanelData>('health', signal)
  if (!('db_time' in data) && !('movies' in data) && !data.stats) return badShape('缺少 db_time / movies / stats。')
  return data
}
export async function loadBatches(input: BatchQueryInput, signal?: AbortSignal) {
  const data = await opsGet<BatchesPage>(batchesQuery(input), signal)
  if (!Array.isArray(data.items)) return badShape('缺少 items 数组。')
  return data
}
export async function loadEvents(input: EventQueryInput, signal?: AbortSignal) {
  const data = await opsGet<EventsPage>(eventsQuery(input), signal)
  if (!Array.isArray(data.items)) return badShape('缺少 items 数组。')
  return data
}
export async function loadMetricsDefinitions(signal?: AbortSignal) {
  const data = await opsGet<MetricsDefinitions>('metrics/definitions', signal)
  if (!hasKeys(data, ['version', 'definitions']) || !Array.isArray(data.definitions)) {
    return badShape('缺少 version / definitions。')
  }
  return data
}

/* ----------------------------------------------------------- chart option */

export interface TrendPoint { date: string; value: number | null }

/**
 * Shared ECharts option for the daily trend cards. `null` stays a gap, never a
 * zero, so a failed day cannot masquerade as “no activity”.
 */
export function trendOption(
  name: string,
  points: TrendPoint[],
  kind: 'line' | 'bar' = 'line',
): Record<string, any> {
  const dates = points.map(point => point.date)
  const values = points.map(point => (typeof point.value === 'number' && Number.isFinite(point.value) ? point.value : null))
  const axis = {
    type: 'category',
    data: dates,
    boundaryGap: kind === 'bar',
    axisTick: { show: false },
    axisLine: { lineStyle: { color: 'rgba(241,236,226,.16)' } },
    axisLabel: { color: '#8f8a80', fontSize: 9, interval: 'auto', hideOverlap: true },
  }
  const valueAxis = {
    type: 'value',
    minInterval: 1,
    splitLine: { lineStyle: { color: 'rgba(241,236,226,.07)' } },
    axisLabel: { color: '#8f8a80', fontSize: 9 },
  }
  return {
    grid: { left: 34, right: 8, top: 10, bottom: 20, containLabel: false },
    xAxis: axis,
    yAxis: valueAxis,
    tooltip: { trigger: 'axis', confine: true },
    series: [kind === 'bar'
      ? {
        name, type: 'bar', data: values, barMaxWidth: 14,
        itemStyle: { color: '#9e81c6', borderRadius: [2, 2, 0, 0] },
      }
      : {
        name, type: 'line', data: values, smooth: false, symbol: 'none',
        lineStyle: { width: 2, color: '#b9a0f4' },
        areaStyle: { color: 'rgba(158,129,198,.10)' },
      }],
  }
}

/** Share of `value` against the largest share in the set, for bar widths. */
export function shareWidth(value: number | null | undefined, max: number): string {
  if (typeof value !== 'number' || !Number.isFinite(value) || !(max > 0)) return '0%'
  return Math.max(0, Math.min(100, (value / max) * 100)).toFixed(2) + '%'
}