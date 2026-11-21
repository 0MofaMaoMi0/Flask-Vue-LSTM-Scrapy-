/**
 * URL query contract for the AdminView V2 dashboards (WP-C).
 *
 * The admin cockpit keeps every drill-down context in the URL so a refresh, a
 * shared link or the browser back button restores the same tab, country,
 * sub-view, comparison set and analytics filters. All helpers here are pure so
 * `tools/test-admin-v2-context.mjs` can exercise them without a browser.
 */

export const ADMIN_TABS = [
  'overview', 'catalog', 'users', 'recommend', 'ai', 'health', 'audit', 'settings',
] as const
export type AdminTab = typeof ADMIN_TABS[number]

export const COUNTRY_VIEWS = ['quick', 'deep', 'compare'] as const
export type CountryView = typeof COUNTRY_VIEWS[number]

export interface AdminQuery {
  tab: AdminTab
  view: CountryView
  country: string
  compare: string[]
  genre: string
  decade: string
  yearFrom: number | null
  yearTo: number | null
  minVotes: number
  days: 7 | 30 | 90
  detailOffset: number
}

export const DEFAULT_MIN_VOTES = 50
export const DEFAULT_DAYS = 30
export const MAX_COMPARE = 3

export type RawQuery = Record<string, unknown>

function text(value: unknown): string {
  if (Array.isArray(value)) return value.length ? String(value[0] ?? '') : ''
  return typeof value === 'string' ? value : ''
}

function iso2(value: unknown): string {
  const code = text(value).trim().toUpperCase()
  return /^[A-Z]{2}$/.test(code) ? code : ''
}

function boundedInt(value: unknown, low: number, high: number): number | null {
  const raw = text(value).trim()
  if (!raw || !/^\d{1,6}$/.test(raw)) return null
  const parsed = Number(raw)
  return parsed >= low && parsed <= high ? parsed : null
}

export function defaultQuery(): AdminQuery {
  return {
    tab: 'overview', view: 'quick', country: '', compare: [], genre: '',
    decade: '', yearFrom: null, yearTo: null, minVotes: DEFAULT_MIN_VOTES,
    days: DEFAULT_DAYS, detailOffset: 0,
  }
}

/** Compare list: ISO2 only, de-duplicated, deterministic, max three. */
export function normalizeCompare(value: unknown): string[] {
  const raw = Array.isArray(value) ? value.join(',') : text(value)
  const seen: string[] = []
  for (const part of raw.split(',')) {
    const code = iso2(part)
    if (code && !seen.includes(code)) seen.push(code)
  }
  return seen.slice(0, MAX_COMPARE)
}

export function parseAdminQuery(raw: RawQuery | null | undefined): AdminQuery {
  const query = defaultQuery()
  const source = raw || {}
  const tab = text(source.tab).trim().toLowerCase()
  if ((ADMIN_TABS as readonly string[]).includes(tab)) query.tab = tab as AdminTab
  const view = text(source.view).trim().toLowerCase()
  if ((COUNTRY_VIEWS as readonly string[]).includes(view)) query.view = view as CountryView
  query.country = iso2(source.country)
  query.compare = normalizeCompare(source.compare)
  query.genre = text(source.genre).trim().slice(0, 80)
  const decade = text(source.decade).trim()
  if (/^\d{4}$/.test(decade)) query.decade = decade
  query.yearFrom = boundedInt(source.year_from, 1800, 2199)
  query.yearTo = boundedInt(source.year_to, 1800, 2199)
  if (query.yearFrom != null && query.yearTo != null && query.yearFrom > query.yearTo) {
    const swap = query.yearFrom
    query.yearFrom = query.yearTo
    query.yearTo = swap
  }
  const minVotes = boundedInt(source.min_votes, 0, 1000000)
  query.minVotes = minVotes == null ? DEFAULT_MIN_VOTES : minVotes
  const days = boundedInt(source.days, 7, 90)
  query.days = days === 7 || days === 30 || days === 90 ? days : DEFAULT_DAYS
  query.detailOffset = boundedInt(source.detail_offset, 0, 10000) ?? 0
  return query
}

/** Only non-default values are written, so the plain /admin URL stays clean. */
export function serializeAdminQuery(input: Partial<AdminQuery>): Record<string, string> {
  const query = { ...defaultQuery(), ...input }
  const out: Record<string, string> = {}
  if (query.tab !== 'overview') out.tab = query.tab
  if (query.view !== 'quick') out.view = query.view
  if (query.country) out.country = iso2(query.country)
  if (query.compare.length) out.compare = normalizeCompare(query.compare.join(',')).join(',')
  if (query.genre) out.genre = query.genre
  if (query.decade) out.decade = query.decade
  if (query.yearFrom != null) out.year_from = String(query.yearFrom)
  if (query.yearTo != null) out.year_to = String(query.yearTo)
  if (query.minVotes !== DEFAULT_MIN_VOTES) out.min_votes = String(query.minVotes)
  if (query.days !== DEFAULT_DAYS) out.days = String(query.days)
  if (query.detailOffset > 0) out.detail_offset = String(query.detailOffset)
  return Object.fromEntries(Object.entries(out).filter(([, value]) => value !== ''))
}

export function sameQuery(a: AdminQuery, b: AdminQuery): boolean {
  return JSON.stringify(serializeAdminQuery(a)) === JSON.stringify(serializeAdminQuery(b))
}

export function toggleCompare(list: string[], code: string): string[] {
  const target = iso2(code)
  if (!target) return list.slice(0, MAX_COMPARE)
  if (list.includes(target)) return list.filter(item => item !== target)
  if (list.length >= MAX_COMPARE) return list.slice(0, MAX_COMPARE)
  return [...list, target]
}

export type CountryAvailability = 'HAS_DATA' | 'NO_MATCHES' | 'NO_COUNTRY_METADATA' | 'NO_VALID_RATING_SAMPLE'

export interface CountryAvailabilityInput {
  movieCount: number | null | undefined
  catalogState: string | null | undefined
  countryState: string | null | undefined
  ratingState: string | null | undefined
}

/**
 * The three states the requirement insists on separating: no local movies at
 * all, movies without production-country metadata, and movies without a rating
 * sample. They are never folded into a zero.
 */
export function countryAvailability(input: CountryAvailabilityInput): CountryAvailability {
  const catalog = String(input.catalogState || '').toUpperCase()
  const count = typeof input.movieCount === 'number' && Number.isFinite(input.movieCount)
    ? input.movieCount : null
  if (catalog.startsWith('NO_') || count === 0) {
    return catalog === 'NO_COUNTRY_METADATA' ? 'NO_COUNTRY_METADATA' : 'NO_MATCHES'
  }
  if (String(input.countryState || '').toUpperCase() === 'NO_COUNTRY_METADATA') return 'NO_COUNTRY_METADATA'
  if (String(input.ratingState || '').toUpperCase() === 'NO_VALID_RATING_SAMPLE') return 'NO_VALID_RATING_SAMPLE'
  return 'HAS_DATA'
}

export function availabilityLabel(state: CountryAvailability): string {
  switch (state) {
    case 'NO_MATCHES': return '无收录'
    case 'NO_COUNTRY_METADATA': return '无国家资料'
    case 'NO_VALID_RATING_SAMPLE': return '评分样本不足'
    default: return '有数据'
  }
}

export function formatUtc(value: string | null | undefined): string {
  if (!value) return '—'
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return '—'
  const parts = date.toISOString().slice(0, 16).replace('T', ' ')
  return parts + ' UTC'
}

export function formatLocal(value: string | null | undefined): string {
  if (!value) return '—'
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return '—'
  return date.toLocaleString('zh-CN', { hour12: false })
}