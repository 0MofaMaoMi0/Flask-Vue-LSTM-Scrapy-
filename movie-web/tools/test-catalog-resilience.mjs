import fs from 'node:fs/promises'
import ts from 'typescript'
import assert from 'node:assert/strict'

const helperSource = await fs.readFile(new URL('../src/services/catalogResilience.ts', import.meta.url), 'utf8')
const helperJs = ts.transpile(helperSource, { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ESNext })
const mod = await import('data:text/javascript;base64,' + Buffer.from(helperJs).toString('base64'))

const checks = []
async function check(name, fn) {
  await fn()
  checks.push(name)
}

const oldFetch = globalThis.fetch
const oldSessionStorage = globalThis.sessionStorage

try {
  await check('fetchJsonWithTimeout distinguishes timeout (even when fetch throws AbortError) from outer abort', async () => {
    globalThis.fetch = (_input, init) => new Promise((_resolve, reject) => {
      init.signal.addEventListener('abort', () => reject(new DOMException('Aborted', 'AbortError')), { once: true })
    })

    await assert.rejects(
      () => mod.fetchJsonWithTimeout('/api/catalog/recommendations', {
        timeoutMs: 25,
        timeoutMessage: '推荐频道读取超时，请稍后重试。',
      }),
      (err) => err.name === 'TimeoutError' && err.timedOut === true && err.message.includes('超时')
    )

    const outer = new AbortController()
    setTimeout(() => outer.abort(), 15)
    await assert.rejects(
      () => mod.fetchJsonWithTimeout('/api/catalog/recommendations', {
        signal: outer.signal,
        timeoutMs: 500,
        timeoutMessage: '推荐频道读取超时，请稍后重试。',
      }),
      (err) => err.name === 'AbortError' && outer.signal.aborted === true
    )
  })

  await check('fetchJsonWithTimeout also enforces timeout during response.json() parsing', async () => {
    globalThis.fetch = async () => ({
      ok: true,
      status: 200,
      json: () => new Promise(() => {}),
    })
    await assert.rejects(
      () => mod.fetchJsonWithTimeout('/api/catalog/recommendations', {
        timeoutMs: 25,
        timeoutMessage: '推荐频道读取超时，请稍后重试。',
      }),
      (err) => err.name === 'TimeoutError' && err.timedOut === true
    )
  })

  await check('createInFlightDeduper deduplicates concurrent identical requests and clears after settle', async () => {
    const deduper = mod.createInFlightDeduper()
    let calls = 0
    let release
    const factory = () => {
      calls++
      return new Promise((resolve) => { release = resolve })
    }
    const p1 = deduper.run('user1::daily', factory)
    const p2 = deduper.run('user1::daily', factory)
    assert.equal(p1, p2)
    assert.equal(calls, 1)
    assert.equal(deduper.size, 1)
    release({ ok: true })
    assert.deepEqual(await Promise.all([p1, p2]), [{ ok: true }, { ok: true }])
    assert.equal(deduper.size, 0)
    const nextRes = await deduper.run('user1::daily', async () => { calls++; return 'next' })
    assert.equal(nextRes, 'next')
    assert.equal(calls, 2)
  })

  await check('recMetaCache sessionStorage persistence enforces TTL and user isolation', async () => {
    const store = new Map()
    globalThis.sessionStorage = {
      getItem: (k) => (store.has(k) ? store.get(k) : null),
      setItem: (k, v) => store.set(k, String(v)),
      removeItem: (k) => store.delete(k),
    }
    const baseNow = 1_700_000_000_000
    mod.writeRecMetaStorage({
      userKey: 'codex',
      authEpoch: 1,
      updatedAt: baseNow,
      channels: [{ id: 'daily', title: '每日为你推荐' }],
      genres: [{ genre_id: 1, name_zh: '剧情' }],
      languages: [{ code: 'zh', count: 100 }],
    })
    assert.equal(mod.readRecMetaStorage('deepseek', 1, baseNow + 1000), null)
    const hit = mod.readRecMetaStorage('codex', 2, baseNow + 299_999)
    assert.ok(hit)
    assert.equal(hit.channels[0].id, 'daily')
    assert.equal(hit.authEpoch, 2)
    assert.equal(mod.readRecMetaStorage('codex', 2, baseNow + mod.REC_META_TTL_MS), null)
    assert.equal(store.has(mod.REC_META_STORAGE_KEY), false)
  })

  await check('buildGlobeCatalogStats combines full 5597 catalog baseline with personal overlays and merges duplicate iso3', async () => {
    const iso2ToIso3 = { US: 'USA', CN: 'CHN', HK: 'CHN', JP: 'JPN' }
    const globeSummary = {
      total_unique_movies: 5597,
      covered_country_count: 95,
      overview_genres: [['剧情', 3100], ['喜剧', 1800]],
      genre_options: ['剧情', '喜剧', '动作'],
      countries: [
        { iso2: 'US', catalog_count: 2500, genres: [['剧情', 1100], ['动作', 800]] },
        { iso2: 'cn', catalog_count: 420, genres: [['剧情', 260], ['喜剧', 120]] },
        { iso2: 'HK', catalog_count: 180, genres: [['动作', 90], ['剧情', 70]] },
        { iso2: 'XX', catalog_count: 99, genres: [['未知', 99]] },
      ],
    }

    const emptyPersonal = mod.buildGlobeCatalogStats({
      globeSummary,
      userMovies: [],
      behavior: {},
      countryMap: {},
      iso2ToIso3,
    })
    assert.equal(emptyPersonal.total_unique_movies, 5597)
    assert.equal(emptyPersonal.covered_country_count, 2)
    assert.equal(emptyPersonal.countries.length, 2)
    const chnBaseline = emptyPersonal.countries.find((c) => c.iso3 === 'CHN')
    assert.equal(chnBaseline.catalog_count, 600)
    assert.deepEqual(chnBaseline.genres[0], ['剧情', 330])

    const withPersonal = mod.buildGlobeCatalogStats({
      globeSummary,
      userMovies: [
        { movie_id: 1, title: 'Film A' },
        { movie_id: 1, title: 'Film A' },
        { movie_id: 2, title: 'Film B' },
        { movie_id: 3, title: 'Want Only' },
      ],
      behavior: {
        '1': { watched: true, favorite: true, rating: 4.5 },
        '2': { watched: true, favorite: false, rating: 5 },
        '3': { want: true },
      },
      countryMap: {
        '1': ['USA', 'USA'],
        '2': ['USA', 'CHN'],
        '3': ['JPN'],
      },
      iso2ToIso3,
    })
    const usa = withPersonal.countries.find((c) => c.iso3 === 'USA')
    assert.equal(usa.catalog_count, 2500)
    assert.equal(usa.watched_count, 2)
    assert.equal(usa.favorite_count, 1)
    assert.equal(usa.average_rating, 4.75)
    assert.deepEqual(usa.movies.map((m) => m.movie_id), [2, 1])

    const nullFallback = mod.buildGlobeCatalogStats({
      globeSummary: null,
      userMovies: [{ movie_id: 1, title: 'Film A' }],
      behavior: { '1': { watched: true } },
      countryMap: { '1': ['USA'] },
      iso2ToIso3,
    })
    assert.equal(nullFallback.total_unique_movies, 0)
    assert.deepEqual(nullFallback.countries, [])
  })

  await check('mapQualityError handles 503, IMPORT_UNAVAILABLE, CACHE_WARMING, DATA_UNAVAILABLE', async () => {
    assert.ok(mod.mapQualityError(503, 'IMPORT_UNAVAILABLE').includes('IMPORT_UNAVAILABLE'))
    assert.ok(mod.mapQualityError(503, 'CACHE_WARMING').includes('CACHE_WARMING'))
    assert.ok(mod.mapQualityError(503, 'DATA_UNAVAILABLE').includes('DATA_UNAVAILABLE'))
    assert.ok(mod.mapQualityError(503).includes('503'))
    assert.ok(mod.mapQualityError(401).includes('登录'))
    assert.ok(mod.mapQualityError(403).includes('管理员'))
  })

  await check('Vue SFC sources keep required concurrency, globe, and 503 resilience contracts', async () => {
    const recVue = await fs.readFile(new URL('../src/views/RecommendationView.vue', import.meta.url), 'utf8')
    assert.equal(recVue.includes('@click="load"'), false, 'retry/reload buttons must not pass Event to load')
    assert.equal(recVue.includes('@click="refreshCurrent"'), true)
    assert.equal(recVue.includes(".name!=='AbortError'"), false, 'must not swallow AbortError timeouts')
    assert.equal(recVue.includes('REC_CACHE_TTL_MS=300_000'), true)
    assert.equal(recVue.includes('REC_DEBOUNCE_MS=180'), true)

    const myVue = await fs.readFile(new URL('../src/views/MyMoviesView.vue', import.meta.url), 'utf8')
    assert.equal(myVue.includes('MAX_CATALOG_PAGES'), false, 'must remove 10-page catalog fetch')
    assert.equal(myVue.includes("api('globe-summary'"), true)
    assert.equal(myVue.includes('!movies.length'), false, 'globe empty state must check catalogStats.total_unique_movies')
    assert.equal(myVue.includes('!catalogStats.total_unique_movies'), true)

    const qualVue = await fs.readFile(new URL('../src/components/AdminQuality.vue', import.meta.url), 'utf8')
    assert.equal(qualVue.includes('Promise.allSettled'), true)
    assert.equal(qualVue.includes(".name!=='AbortError'"), false)
    const reportStart = qualVue.indexOf('<template v-if="report">')
    const runDivIdx = qualVue.indexOf('<div v-if="run"')
    assert.ok(reportStart !== -1 && runDivIdx > reportStart)
    const between = qualVue.slice(reportStart, runDivIdx)
    const openTemplates = (between.match(/<template\b/g) || []).length
    const closeTemplates = (between.match(/<\/template>/g) || []).length
    assert.equal(openTemplates, closeTemplates, 'run status must render outside report v-if')
  })

  console.log(JSON.stringify({ all_pass: true, checks }, null, 2))
} finally {
  globalThis.fetch = oldFetch
  globalThis.sessionStorage = oldSessionStorage
}
