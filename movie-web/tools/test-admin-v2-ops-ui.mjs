/**
 * Pure-logic checks for the WP-D operations panels (no browser, no network).
 *
 *   F:/Node/node.exe tools/test-admin-v2-ops-ui.mjs
 *
 * The module is transpiled in memory the same way `test-admin-v2-context.mjs`
 * loads the admin context, so the shipped `.ts` is what gets exercised.
 */
import fs from 'node:fs/promises'
import ts from 'typescript'
import assert from 'node:assert/strict'

const root = new URL('../src/components/admin/ops/', import.meta.url)
async function load(name) {
  const source = await fs.readFile(new URL(name, root), 'utf8')
  const js = ts.transpile(source, { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ESNext })
  return import('data:text/javascript;base64,' + Buffer.from(js).toString('base64') + '#' + encodeURIComponent(name))
}

const api = await load('opsApi.ts')
const checks = []
function check(name, fn) {
  fn()
  checks.push(name)
}

function queryOf(url) {
  const [path, search] = url.split('?')
  return { path, params: new URLSearchParams(search || '') }
}

check('days accepts only 7/30/90, otherwise the default', () => {
  assert.equal(api.parseDays(7), 7)
  assert.equal(api.parseDays('90'), 90)
  assert.equal(api.parseDays('30'), 30)
  assert.equal(api.parseDays('45'), 30)
  assert.equal(api.parseDays('7.0'), 30)
  assert.equal(api.parseDays(null), 30)
  assert.equal(api.parseDays(undefined, 90), 90)
})

check('users analytics query keeps kind inside the public enum', () => {
  let { path, params } = queryOf(api.usersQuery(30))
  assert.equal(path, 'users/analytics')
  assert.equal(params.get('days'), '30')
  assert.equal(params.has('kind'), false)
  ;({ params } = queryOf(api.usersQuery(7, 'favorite')))
  assert.equal(params.get('kind'), 'favorite')
  ;({ params } = queryOf(api.usersQuery(7, 'bogus')))
  assert.equal(params.has('kind'), false)
})

check('interest match query normalises the dimension', () => {
  let { path, params } = queryOf(api.interestQuery(90, 'decade'))
  assert.equal(path, 'interest-match')
  assert.equal(params.get('dimension'), 'decade')
  assert.equal(params.get('days'), '90')
  ;({ params } = queryOf(api.interestQuery(30, 'language')))
  assert.equal(params.get('dimension'), 'genre')
})

check('event query clamps pagination and drops invalid filters', () => {
  let { path, params } = queryOf(api.eventsQuery({ limit: 500, offset: -4 }))
  assert.equal(path, 'events')
  assert.equal(params.get('limit'), '100')
  assert.equal(params.get('offset'), '0')
  ;({ params } = queryOf(api.eventsQuery({ kind: 'rating', movieId: 12, from: '2026-11-01', to: '2026-11-18' })))
  assert.equal(params.get('kind'), 'rating')
  assert.equal(params.get('movie_id'), '12')
  assert.equal(params.get('from'), '2026-11-01')
  assert.equal(params.get('to'), '2026-11-18')
  ;({ params } = queryOf(api.eventsQuery({ kind: 'hack', movieId: 0, from: '   ' })))
  assert.equal(params.has('kind'), false)
  assert.equal(params.has('movie_id'), false)
  assert.equal(params.has('from'), false)
})

check('batches query keeps status and started range', () => {
  let { path, params } = queryOf(api.batchesQuery({ status: 'partial', startedFrom: '2026-11-01T00:00:00Z', limit: 20 }))
  assert.equal(path, 'tmdb-supplement/batches')
  assert.equal(params.get('status'), 'partial')
  assert.equal(params.get('started_from'), '2026-11-01T00:00:00Z')
  assert.equal(params.get('limit'), '20')
  ;({ params } = queryOf(api.batchesQuery({ status: 'invented' })))
  assert.equal(params.has('status'), false)
})

check('status labels never turn unknown into normal', () => {
  assert.deepEqual(api.statusMeta('available'), { label: '可用', tone: 'ok' })
  assert.equal(api.statusMeta('normal').tone, 'ok')
  assert.equal(api.statusMeta('abnormal').tone, 'bad')
  assert.equal(api.statusMeta('observing').tone, 'warn')
  assert.equal(api.statusMeta('not_instrumented').label, '未采集')
  assert.equal(api.statusMeta('not_evaluable').tone, 'muted')
  assert.equal(api.statusMeta('unknown').label, '未知')
  assert.equal(api.statusMeta(null).label, '未知')
  assert.equal(api.statusMeta('brand-new-status').tone, 'muted')
  for (const status of ['available', 'insufficient_sample', 'suppressed', 'not_instrumented',
    'unavailable', 'not_evaluable', 'observing', 'normal', 'abnormal', 'unknown']) {
    assert.ok(api.statusMeta(status).label.length > 0, status)
  }
})

check('token formatting keeps unknown unknown', () => {
  assert.equal(api.fmtToken(null), '未知')
  assert.equal(api.fmtToken(undefined), '未知')
  assert.equal(api.fmtToken(0), '0')
  assert.equal(api.fmtToken(1200), '1,200')
  assert.equal(api.fmtInt(null), '—')
  assert.equal(api.fmtInt(0), '0')
  assert.equal(api.fmtRatio(null), '—')
  assert.equal(api.fmtRatio(0), '0.0%')
  assert.equal(api.fmtRatio(0.1234, 2), '12.34%')
  assert.equal(api.fmtHours(null), '—')
  assert.equal(api.fmtHours(0.5), '30 分钟')
  assert.equal(api.fmtHours(3.2), '3.2 小时')
})

check('UTC stamps are explicit and invalid input degrades to a dash', () => {
  assert.equal(api.fmtUtc('2026-11-18T13:05:00Z'), '2026-11-18 13:05 UTC')
  assert.equal(api.fmtUtc('2026-11-18T03:00:00'), '2026-11-18 03:00 UTC')
  assert.equal(api.fmtUtc(null), '—')
  assert.equal(api.fmtUtc('not-a-date'), 'not-a-date')
  assert.equal(api.fmtUtcNaive('2026-11-18T12:00:00'), '2026-11-18 12:00 UTC')
  assert.equal(api.fmtDate('2026-11-18T13:05:00Z'), '2026-11-18')
})

check('trend options keep nulls as gaps, never zeros', () => {
  const points = [
    { date: '2026-11-16', value: 0 },
    { date: '2026-11-17', value: null },
    { date: '2026-11-18', value: 5 },
  ]
  const option = api.trendOption('行为事件', points)
  assert.deepEqual(option.xAxis.data, ['2026-11-16', '2026-11-17', '2026-11-18'])
  assert.deepEqual(option.series[0].data, [0, null, 5])
  assert.equal(option.series[0].type, 'line')
  const bars = api.trendOption('调用', points, 'bar')
  assert.equal(bars.series[0].type, 'bar')
  assert.equal(bars.xAxis.boundaryGap, true)
})

check('share widths stay within 0-100%', () => {
  assert.equal(api.shareWidth(0.5, 1), '50.00%')
  assert.equal(api.shareWidth(null, 1), '0%')
  assert.equal(api.shareWidth(2, 1), '100.00%')
  assert.equal(api.shareWidth(0.2, 0), '0%')
})

check('browser preference keys are namespaced and safe without storage', () => {
  assert.equal(api.PREF_PREFIX, 'mi2_admin_pref_')
  assert.equal(api.readPref('default_days'), null)
  assert.doesNotThrow(() => api.writePref('default_days', '90'))
  assert.doesNotThrow(() => api.clearPrefs())
})

console.log(`ok — ${checks.length} checks`)
for (const name of checks) console.log('  · ' + name)