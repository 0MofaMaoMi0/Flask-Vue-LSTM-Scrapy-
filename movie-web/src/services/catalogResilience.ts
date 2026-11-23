export type SwrGlobalBus = {
  behaviorEpoch: number
  authEpoch: number
  listening: boolean
}

export function getSwrBus(): SwrGlobalBus {
  const bus: SwrGlobalBus = ((globalThis as any).__mi2SwrBus ??= {
    behaviorEpoch: 1,
    authEpoch: 1,
    listening: false,
  })
  if (!bus.listening && typeof window !== 'undefined') {
    bus.listening = true
    const bumpBehavior = () => {
      bus.behaviorEpoch++
    }
    window.addEventListener('mi2-behavior-changed', bumpBehavior)
    window.addEventListener('mi2-tmdb-status', bumpBehavior)
  }
  return bus
}

export class RequestTimeoutError extends Error {
  readonly timedOut = true
  constructor(message = '请求超时，请稍后重试。') {
    super(message)
    this.name = 'TimeoutError'
  }
}

export class HttpResponseError extends Error {
  readonly status: number
  readonly code: string
  constructor(message: string, status: number, code = '') {
    super(message)
    this.status = status
    this.code = code
  }
}

export async function fetchJsonWithTimeout<T = any>(
  input: RequestInfo | URL,
  options: RequestInit & { timeoutMs?: number; timeoutMessage?: string } = {}
): Promise<{ response: Response; body: T | null }> {
  const {
    timeoutMs = 20_000,
    timeoutMessage = '请求超时，请稍后重试。',
    signal: outerSignal,
    ...init
  } = options
  if (outerSignal?.aborted) {
    throw new DOMException('Aborted', 'AbortError')
  }
  const req = new AbortController()
  let timedOut = false
  const timer = setTimeout(() => {
    timedOut = true
    req.abort()
  }, timeoutMs)
  const onAbort = () => req.abort()
  outerSignal?.addEventListener('abort', onAbort, { once: true })
  try {
    const response = await fetch(input, { ...init, signal: req.signal })
    let body: T | null = null
    try {
      body = await new Promise<T | null>((resolve, reject) => {
        if (req.signal.aborted) {
          reject(new DOMException('Aborted', 'AbortError'))
          return
        }
        const onReqAbort = () => reject(new DOMException('Aborted', 'AbortError'))
        req.signal.addEventListener('abort', onReqAbort, { once: true })
        response.json().then(
          (data) => {
            req.signal.removeEventListener('abort', onReqAbort)
            resolve(data as T)
          },
          (err) => {
            req.signal.removeEventListener('abort', onReqAbort)
            reject(err)
          }
        )
      })
    } catch {
      if (timedOut) throw new RequestTimeoutError(timeoutMessage)
      if (outerSignal?.aborted) throw new DOMException('Aborted', 'AbortError')
      body = null
    }
    if (timedOut) throw new RequestTimeoutError(timeoutMessage)
    return { response, body }
  } catch (e) {
    if (e instanceof RequestTimeoutError) throw e
    if (timedOut || (e as Error)?.name === 'TimeoutError') {
      throw new RequestTimeoutError(timeoutMessage)
    }
    throw e
  } finally {
    clearTimeout(timer)
    outerSignal?.removeEventListener('abort', onAbort)
  }
}

export function createInFlightDeduper<T = any>() {
  const map = new Map<string, Promise<T>>()
  return {
    get(key: string): Promise<T> | undefined {
      return map.get(key)
    },
    has(key: string): boolean {
      return map.has(key)
    },
    run(key: string, factory: () => Promise<T>): Promise<T> {
      const existing = map.get(key)
      if (existing) return existing
      const promise = factory().finally(() => {
        if (map.get(key) === promise) {
          map.delete(key)
        }
      })
      map.set(key, promise)
      return promise
    },
    delete(key: string): void {
      map.delete(key)
    },
    clear(): void {
      map.clear()
    },
    get size(): number {
      return map.size
    },
  }
}

export const REC_META_STORAGE_KEY = 'mi2_rec_meta_cache_v1'
export const REC_META_TTL_MS = 300_000

export type RecMetaCache = {
  userKey: string
  authEpoch: number
  updatedAt: number
  channels: any[]
  genres: any[]
  languages: { code: string; count: number }[]
}

export function readRecMetaStorage(
  userKey: string,
  authEpoch: number,
  now = Date.now()
): RecMetaCache | null {
  if (typeof sessionStorage === 'undefined') return null
  try {
    const raw = sessionStorage.getItem(REC_META_STORAGE_KEY)
    if (!raw) return null
    const parsed = JSON.parse(raw)
    if (!parsed || typeof parsed !== 'object') return null
    if (parsed.userKey !== userKey) return null
    if (!Array.isArray(parsed.channels) || parsed.channels.length === 0) return null
    if (typeof parsed.updatedAt !== 'number' || !Number.isFinite(parsed.updatedAt)) return null
    if (now - parsed.updatedAt >= REC_META_TTL_MS) {
      sessionStorage.removeItem(REC_META_STORAGE_KEY)
      return null
    }
    return {
      userKey,
      authEpoch,
      updatedAt: parsed.updatedAt,
      channels: parsed.channels,
      genres: Array.isArray(parsed.genres) ? parsed.genres : [],
      languages: Array.isArray(parsed.languages) ? parsed.languages : [],
    }
  } catch {
    return null
  }
}

export function writeRecMetaStorage(entry: RecMetaCache): void {
  if (typeof sessionStorage === 'undefined') return
  try {
    sessionStorage.setItem(REC_META_STORAGE_KEY, JSON.stringify(entry))
  } catch {
    // Ignore storage quota or private-browsing errors; in-memory cache remains active.
  }
}

export function clearRecMetaStorage(): void {
  if (typeof sessionStorage === 'undefined') return
  try {
    sessionStorage.removeItem(REC_META_STORAGE_KEY)
  } catch {
    // Ignore storage removal errors.
  }
}

export type GlobeSummaryCountry = {
  iso2: string
  catalog_count: number
  genres: [string, number][]
}

export type GlobeSummaryResponse = {
  total_unique_movies: number
  covered_country_count: number
  overview_genres: [string, number][]
  genre_options: string[]
  countries: GlobeSummaryCountry[]
}

export function buildGlobeCatalogStats(params: {
  globeSummary: GlobeSummaryResponse | null
  userMovies: any[]
  behavior: Record<string, any>
  countryMap: Record<string, string[]>
  iso2ToIso3: Record<string, string>
}) {
  const { globeSummary, userMovies, behavior, countryMap, iso2ToIso3 } = params
  if (!globeSummary) {
    return {
      total_unique_movies: 0,
      covered_country_count: 0,
      countries: [] as any[],
    }
  }
  const map = new Map<string, any>()
  const ensureCountry = (iso3: string) => {
    let c = map.get(iso3)
    if (!c) {
      c = {
        iso3,
        catalog_count: 0,
        watched_count: 0,
        favorite_count: 0,
        ratings: [] as number[],
        movies: [] as Array<{ movie_id: number; title: string; rating: number | null }>,
        genreMap: new Map<string, number>(),
      }
      map.set(iso3, c)
    }
    return c
  }

  for (const entry of globeSummary.countries || []) {
    const iso2 = String(entry?.iso2 || '').trim().toUpperCase()
    const iso3 = iso2ToIso3[iso2]
    if (!iso3) continue
    const c = ensureCountry(iso3)
    c.catalog_count += Number(entry.catalog_count) || 0
    for (const pair of entry.genres || []) {
      if (!Array.isArray(pair) || pair.length < 2) continue
      const genreName = String(pair[0] || '')
      const count = Number(pair[1]) || 0
      if (!genreName || count <= 0) continue
      c.genreMap.set(genreName, (c.genreMap.get(genreName) || 0) + count)
    }
  }

  const seenMovieIds = new Set<string>()
  for (const m of userMovies || []) {
    if (m?.movie_id == null) continue
    const key = String(m.movie_id)
    if (seenMovieIds.has(key)) continue
    seenMovieIds.add(key)
    const s = behavior?.[key] || {}
    if (!s.watched && !s.favorite) continue
    const isos = [...new Set(countryMap?.[key] || [])]
    for (const iso of isos) {
      if (!iso) continue
      const c = ensureCountry(iso)
      if (s.watched) {
        c.watched_count++
        c.movies.push({
          movie_id: m.movie_id,
          title: m.title,
          rating: typeof s.rating === 'number' ? s.rating : null,
        })
        if (typeof s.rating === 'number') c.ratings.push(s.rating)
      }
      if (s.favorite) {
        c.favorite_count++
      }
    }
  }

  const countries = [...map.values()].map((c: any) => ({
    ...c,
    movies: c.movies.slice().sort((a: any, b: any) => (b.rating ?? -1) - (a.rating ?? -1)),
    average_rating: c.ratings.length
      ? Math.round((c.ratings.reduce((a: number, b: number) => a + b, 0) / c.ratings.length) * 100) / 100
      : null,
    rating_sample_count: c.ratings.length,
    genres: [...c.genreMap.entries()].sort((a: [string, number], b: [string, number]) => b[1] - a[1]),
  }))

  return {
    total_unique_movies: Number(globeSummary.total_unique_movies) || 0,
    covered_country_count: countries.length,
    countries,
  }
}

const QUALITY_ERROR_MAP: Record<string, string> = {
  QUALITY_SCOPE_CHANGED: '目录发生变化，请刷新盘点后重新确认。',
  QUALITY_RUN_ACTIVE: '已有核验任务运行，请等待完成。',
  AUTHENTICATION_REQUIRED: '登录已失效，请重新登录。',
  FORBIDDEN: '需要管理员权限。',
  DATA_UNAVAILABLE: '质量盘点服务暂时不可用（DATA_UNAVAILABLE），请稍后重试。',
  IMPORT_UNAVAILABLE: '质量盘点服务暂时不可用（IMPORT_UNAVAILABLE），请稍后重试。',
  CACHE_WARMING: '质量盘点缓存正在预热（CACHE_WARMING），请稍后重试。',
  REQUEST_VERIFICATION_FAILED: '安全验证已失效，请刷新页面后重试。',
}

export function mapQualityError(status: number, bodyCode?: unknown): string {
  const code = typeof bodyCode === 'string' ? bodyCode : ''
  if (code && QUALITY_ERROR_MAP[code]) return QUALITY_ERROR_MAP[code]
  if (status === 503) return '质量盘点服务暂时不可用或正在预热（503），请稍后重试。'
  if (status === 401) return '登录已失效，请重新登录。'
  if (status === 403) return '需要管理员权限。'
  return '核验未完成：' + (code || status)
}
