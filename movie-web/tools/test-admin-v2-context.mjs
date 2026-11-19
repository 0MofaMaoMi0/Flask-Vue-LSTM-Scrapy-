import fs from 'node:fs/promises'
import ts from 'typescript'
import assert from 'node:assert/strict'

const root = new URL('../src/components/admin/', import.meta.url)
async function load(name) {
  const source = await fs.readFile(new URL(name, root), 'utf8')
  const js = ts.transpile(source, {target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ESNext})
  return import('data:text/javascript;base64,' + Buffer.from(js).toString('base64') + '#' + encodeURIComponent(name))
}
const context = await load('adminContext.ts')
const analytics = await load('analytics.ts')

const checks = []
function check(name, fn) {
  fn()
  checks.push(name)
}

check('defaults keep the plain /admin URL clean', () => {
  const query = context.parseAdminQuery({})
  assert.equal(query.tab, 'overview')
  assert.equal(query.view, 'quick')
  assert.deepEqual(query.compare, [])
  assert.equal(query.minVotes, 50)
  assert.equal(query.days, 30)
  assert.deepEqual(context.serializeAdminQuery(query), {})
})

check('parses tab, view, country, compare and filters', () => {
  const query = context.parseAdminQuery({
    tab: 'catalog', view: 'deep', country: 'jp', compare: 'cn,JP,us,CN',
    genre: 'Animation', decade: '2010', year_from: '1990', year_to: '2020', min_votes: '100', days: '90',
  })
  assert.equal(query.tab, 'catalog')
  assert.equal(query.view, 'deep')
  assert.equal(query.country, 'JP')
  assert.deepEqual(query.compare, ['CN', 'JP', 'US'])
  assert.equal(query.genre, 'Animation')
  assert.equal(query.decade, '2010')
  assert.equal(query.yearFrom, 1990)
  assert.equal(query.yearTo, 2020)
  assert.equal(query.minVotes, 100)
  assert.equal(query.days, 90)
})

check('drops invalid codes, tabs, views and out-of-range numbers', () => {
  const query = context.parseAdminQuery({
    tab: 'nope', view: 'weird', country: 'JPN', compare: 'C1,?x,', year_from: '1700', year_to: '99999',
    min_votes: '-3', days: '45', decade: '19x0',
  })
  assert.equal(query.tab, 'overview')
  assert.equal(query.view, 'quick')
  assert.equal(query.country, '')
  assert.deepEqual(query.compare, [])
  assert.equal(query.yearFrom, null)
  assert.equal(query.yearTo, null)
  assert.equal(query.minVotes, 50)
  assert.equal(query.days, 30)
  assert.equal(query.decade, '')
})

check('swaps an inverted year range', () => {
  const query = context.parseAdminQuery({year_from: '2020', year_to: '1990'})
  assert.equal(query.yearFrom, 1990)
  assert.equal(query.yearTo, 2020)
})

check('serializes only non-default context and round-trips', () => {
  const query = context.parseAdminQuery({
    tab: 'overview', view: 'compare', country: 'JP', compare: 'CN,JP',
    genre: 'Drama', year_from: '2000', min_votes: '80', days: '7', detail_offset: '40',
  })
  assert.deepEqual(context.serializeAdminQuery(query), {
    view: 'compare', country: 'JP', compare: 'CN,JP', genre: 'Drama',
    year_from: '2000', min_votes: '80', days: '7', detail_offset: '40',
  })
  const again = context.parseAdminQuery(context.serializeAdminQuery(query))
  assert.deepEqual(context.serializeAdminQuery(again), context.serializeAdminQuery(query))
})

check('comparison list caps at three and toggles', () => {
  assert.deepEqual(context.normalizeCompare('cn,jp,us,gb'), ['CN', 'JP', 'US'])
  assert.deepEqual(context.toggleCompare(['CN', 'JP'], 'us'), ['CN', 'JP', 'US'])
  assert.deepEqual(context.toggleCompare(['CN', 'JP', 'US'], 'GB'), ['CN', 'JP', 'US'])
  assert.deepEqual(context.toggleCompare(['CN', 'JP'], 'cn'), ['JP'])
})

check('three country states stay separate', () => {
  assert.equal(context.countryAvailability({movieCount: 0, catalogState: 'NO_MATCHES', countryState: 'NO_MATCHES', ratingState: 'NO_MATCHES'}), 'NO_MATCHES')
  assert.equal(context.countryAvailability({movieCount: 4, catalogState: 'HAS_MOVIES', countryState: 'NO_COUNTRY_METADATA', ratingState: 'NO_VALID_RATING_SAMPLE'}), 'NO_COUNTRY_METADATA')
  assert.equal(context.countryAvailability({movieCount: 4, catalogState: 'HAS_MOVIES', countryState: 'PARTIAL', ratingState: 'NO_VALID_RATING_SAMPLE'}), 'NO_VALID_RATING_SAMPLE')
  assert.equal(context.countryAvailability({movieCount: 4, catalogState: 'HAS_MOVIES', countryState: 'PARTIAL', ratingState: 'PARTIAL'}), 'HAS_DATA')
})

check('UTC timestamps are labelled, unknown values are em dash', () => {
  assert.equal(context.formatUtc('2026-11-18T12:34:56Z'), '2026-11-18 12:34 UTC')
  assert.equal(context.formatUtc(null), '—')
  assert.equal(context.formatLocal('not-a-date'), '—')
})

check('analytics query only sends valid, documented parameters', () => {
  assert.equal(analytics.analyticsQuery({}), '')
  assert.equal(
    analytics.analyticsQuery({country: 'jp', countries: ['cn', 'CN', 'jp', 'us', 'gb'], detailCountry: 'jp', genre: 'Drama', decade: '2010', yearFrom: 1990, yearTo: 2020, minVotes: 100, top: 10, limit: 20, offset: 40, countryLimit: 200, keyword: 12, company: 34, missing: ['poster', 'keywords', 'bogus', 'poster'], sort: 'rating', order: 'asc', overlap: ['cn', 'JP', 'us', 'gb']}),
    'country=JP&countries=CN%2CJP%2CUS&detail_country=JP&genre=Drama&decade=2010&year_from=1990&year_to=2020&min_votes=100&top=10&limit=20&offset=40&country_limit=200&keyword=12&company=34&missing=poster%2Ckeywords&sort=rating&order=asc&overlap=CN%2CJP%2CUS',
  )
  assert.equal(
    analytics.analyticsQuery({countries: ['jp'], detailCountry: ['jp', 'us', 'JP'], minVotes: 50, top: 10, limit: 20, offset: 0}),
    'countries=JP&detail_country=JP%2CUS&min_votes=50&top=10&limit=20&offset=0',
  )
  assert.equal(analytics.analyticsQuery({keyword: -1, company: 0, missing: ['nope'], sort: 'bogus', overlap: ['X1'], detailCountry: 'X1'}), '')
  assert.equal(analytics.analyticsQuery({country: 'JPN', countries: ['X1', 'Z']}), '')
})

check('country coverage extraction tolerates three field shapes', () => {
  assert.equal(analytics.coverageOfCountry({coverage_share: 0.42}), 0.42)
  assert.equal(analytics.coverageOfCountry({coverage: {poster: 0.5}}), 0.5)
  assert.equal(analytics.coverageOfCountry({assets: {poster_share: 63}}), 0.63)
  assert.equal(analytics.coverageOfCountry({}), null)
  assert.equal(analytics.coverageOfCountry(null), null)
})

check('number formatting never invents a zero for missing data', () => {
  assert.equal(analytics.fmtNumber(null), '—')
  assert.equal(analytics.fmtNumber(0), '0')
  assert.equal(analytics.fmtPercent(null), '—')
  assert.equal(analytics.fmtPercent(0.256, 1), '25.6%')
  assert.equal(analytics.fmtScore(null), '—')
  assert.equal(analytics.fmtScore(8.25), '8.3')
})

console.log(`test-admin-v2-context: ${checks.length} checks passed`)
for (const name of checks) console.log('  ok  ' + name)