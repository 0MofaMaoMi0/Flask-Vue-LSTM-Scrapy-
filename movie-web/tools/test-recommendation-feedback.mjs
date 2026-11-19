import fs from 'node:fs/promises'
import ts from 'typescript'
import assert from 'node:assert/strict'

const source = await fs.readFile(new URL('../src/services/recommendationFeedback.ts', import.meta.url), 'utf8')
const js = ts.transpile(source, {target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ESNext})
let moduleSequence = 0
async function loadModule() {
  return import('data:text/javascript;base64,' + Buffer.from(js).toString('base64') + '#' + (++moduleSequence))
}
const wait = ms => new Promise(resolve => setTimeout(resolve, ms))
const response = (status, body, headers = {}) => ({
  ok: status >= 200 && status < 300,
  status,
  headers: {get: name => headers[name] ?? headers[name.toLowerCase()] ?? null},
  json: async () => body,
})
const oldFetch = globalThis.fetch
const oldObserver = globalThis.IntersectionObserver
const oldSetTimeout = globalThis.setTimeout
const oldClearTimeout = globalThis.clearTimeout
const oldDateNow = Date.now
const checks = []
function check(name, fn) {
  checks.push({name, ok: true})
  return fn()
}
function csrf(token = 'synth') { return response(200, {data: {csrf_token: token}}) }

try {
  await check('eight visible positions share one CSRF and one batch POST', async () => {
    const {createFeedbackBoard} = await loadModule()
    let tokens = 0, csrfCalls = 0
    const posts = []
    globalThis.fetch = async (url, options = {}) => {
      if (String(url).endsWith('/csrf')) { csrfCalls++; return csrf('t' + (++tokens)) }
      posts.push({url: String(url), body: JSON.parse(options.body), token: options.headers['X-CSRF-Token']})
      return response(201, {code: 'OK'})
    }
    const items = Array.from({length: 8}, (_, i) => ({movie_id: i + 1}))
    const board = createFeedbackBoard({channel: 'daily', context: 'test', items})
    const requests = items.map((_, i) => board.record(i + 1))
    assert.equal(requests[1], board.record(2))
    assert.deepEqual(await Promise.all(requests), Array(8).fill(true))
    await wait(80)
    assert.equal(csrfCalls, 1)
    assert.equal(posts.length, 1)
    assert.equal(posts[0].body.items.length, 8)
    assert.deepEqual(posts[0].body.items.map(x => x.position), [1,2,3,4,5,6,7,8])
    assert.equal(tokens, 1)
  })

  await check('51st, fractional, and NaN positions are rejected; 1..50 still batch', async () => {
    const {createFeedbackBoard} = await loadModule()
    let tokens = 0
    const posts = []
    globalThis.fetch = async (url, options = {}) => {
      if (String(url).endsWith('/csrf')) return csrf('t' + (++tokens))
      posts.push(JSON.parse(options.body))
      return response(201, {code: 'OK'})
    }
    const items = Array.from({length: 51}, (_, i) => ({movie_id: i + 1}))
    const board = createFeedbackBoard({channel: 'daily', context: 'test', items})
    assert.equal(await board.record(51), false)
    assert.equal(await board.record(1.5), false)
    assert.equal(await board.record(Number.NaN), false)
    const valid = await Promise.all(items.slice(0, 50).map((_, i) => board.record(i + 1)))
    assert.deepEqual(valid, Array(50).fill(true))
    assert.equal(posts.length, 1)
    assert.equal(posts[0].items.length, 50)
    assert.equal(posts[0].items[0].position, 1)
    assert.equal(posts[0].items.some(item => item.position === 51), false)
  })

  await check('two batches use fresh tokens and stay serial', async () => {
    const {createFeedbackBoard} = await loadModule()
    let tokens = 0
    const posts = []
    globalThis.fetch = async (url, options = {}) => {
      if (String(url).endsWith('/csrf')) return csrf('t' + (++tokens))
      posts.push({body: JSON.parse(options.body), token: options.headers['X-CSRF-Token']})
      return response(201, {code: 'OK'})
    }
    const board = createFeedbackBoard({channel: 'daily', context: 'test', items: [{movie_id: 1}, {movie_id: null, tmdb_id: '123'}, {movie_id: 3}, {movie_id: 4}]})
    assert.deepEqual(await Promise.all([board.record(1), board.record(2)]), [true, true])
    await wait(80)
    assert.deepEqual(await Promise.all([board.record(3), board.record(4)]), [true, true])
    await wait(80)
    assert.equal(tokens, 2)
    assert.equal(posts.length, 2)
    assert.equal(posts[0].token, 't1')
    assert.equal(posts[1].token, 't2')
    assert.equal(posts[0].body.items[1].tmdb_id, 123)
    assert.equal(posts[0].body.items[1].movie_id, null)
    assert.deepEqual(posts[1].body.items.map(x => x.position), [3, 4])
  })

  await check('boards never merge batches or request ids', async () => {
    const {createFeedbackBoard} = await loadModule()
    let tokens = 0
    const posts = []
    globalThis.fetch = async (url, options = {}) => {
      if (String(url).endsWith('/csrf')) return csrf('t' + (++tokens))
      posts.push(JSON.parse(options.body))
      return response(201, {code: 'OK'})
    }
    const a = createFeedbackBoard({channel: 'daily', context: 'a', items: [{movie_id: 1}]})
    const b = createFeedbackBoard({channel: 'popular', context: 'b', items: [{movie_id: 2}]})
    const result = await Promise.all([a.record(1), b.record(1)])
    await wait(80)
    assert.deepEqual(result, [true, true])
    assert.equal(posts.length, 2)
    assert.equal(posts[0].request_id === posts[1].request_id, false)
    assert.equal(posts[0].context, 'a')
    assert.equal(posts[1].context, 'b')
  })

  await check('click waits for successful exposure and uses its own CSRF', async () => {
    const {createFeedbackBoard} = await loadModule()
    let tokens = 0
    const posts = []
    globalThis.fetch = async (url, options = {}) => {
      if (String(url).endsWith('/csrf')) return csrf('t' + (++tokens))
      posts.push({url: String(url), token: options.headers['X-CSRF-Token']})
      return response(201, {code: 'OK'})
    }
    const board = createFeedbackBoard({channel: 'daily', context: 'test', items: [{movie_id: 1}]})
    const click = board.click(1)
    await wait(20)
    assert.equal(posts.length, 0, 'click must not overtake exposure')
    assert.equal(await click, undefined)
    await wait(50)
    assert.deepEqual(posts.map(x => x.url), ['/api/catalog/recommendation-impressions', '/api/catalog/recommendation-clicks'])
    assert.equal(tokens, 2)
  })

  await check('failed exposure never sends its click', async () => {
    const {createFeedbackBoard} = await loadModule()
    let tokens = 0
    const posts = []
    globalThis.fetch = async (url, options = {}) => {
      if (String(url).endsWith('/csrf')) return csrf('t' + (++tokens))
      posts.push(String(url))
      return response(503, {code: 'UNAVAILABLE'})
    }
    const board = createFeedbackBoard({channel: 'daily', context: 'test', items: [{movie_id: 1}]})
    await board.click(1)
    await wait(80)
    assert.deepEqual(posts, ['/api/catalog/recommendation-impressions'])
  })

  await check('explicit CSRF 403 retries exactly once with a new token', async () => {
    const {createFeedbackBoard} = await loadModule()
    let tokens = 0, csrfCalls = 0, posts = 0
    globalThis.fetch = async (url, options = {}) => {
      if (String(url).endsWith('/csrf')) { csrfCalls++; return csrf('t' + (++tokens)) }
      posts++
      if (posts === 1) return response(403, {code: 'REQUEST_VERIFICATION_FAILED'})
      return response(201, {code: 'OK'})
    }
    const board = createFeedbackBoard({channel: 'daily', context: 'test', items: [{movie_id: 1}]})
    assert.equal(await board.record(1), true)
    assert.equal(csrfCalls, 2)
    assert.equal(posts, 2)
    assert.equal(tokens, 2)
  })

  await check('unknown transport is not retried', async () => {
    const {createFeedbackBoard} = await loadModule()
    let tokens = 0, posts = 0
    globalThis.fetch = async (url, options = {}) => {
      if (String(url).endsWith('/csrf')) return csrf('t' + (++tokens))
      posts++
      throw new Error('offline')
    }
    const board = createFeedbackBoard({channel: 'daily', context: 'test', items: [{movie_id: 1}]})
    assert.equal(await board.record(1), false)
    assert.equal(tokens, 1)
    assert.equal(posts, 1)
  })

  await check('dispose before the batch timer sends nothing and settles false', async () => {
    const {createFeedbackBoard} = await loadModule()
    let csrfCalls = 0
    globalThis.fetch = async url => { csrfCalls++; return csrf() }
    const board = createFeedbackBoard({channel: 'daily', context: 'test', items: [{movie_id: 1}]})
    const pending = board.record(1)
    board.dispose()
    assert.equal(await pending, false)
    await wait(80)
    assert.equal(csrfCalls, 0)
  })

  await check('dispose during CSRF prevents the POST', async () => {
    const {createFeedbackBoard} = await loadModule()
    let resolveCsrf, posts = 0
    globalThis.fetch = async (url, options = {}) => {
      if (String(url).endsWith('/csrf')) return new Promise(resolve => {resolveCsrf = resolve})
      posts++
      return response(201, {code: 'OK'})
    }
    const board = createFeedbackBoard({channel: 'daily', context: 'test', items: [{movie_id: 1}]})
    const pending = board.record(1)
    await wait(70)
    assert.equal(typeof resolveCsrf, 'function')
    board.dispose()
    resolveCsrf(csrf())
    assert.equal(await pending, false)
    assert.equal(posts, 0)
  })

  await check('queued disposed board does not POST after an earlier request', async () => {
    const {createFeedbackBoard} = await loadModule()
    let resolveFirst, posts = 0, csrfCalls = 0
    globalThis.fetch = async (url, options = {}) => {
      if (String(url).endsWith('/csrf')) {
        csrfCalls++
        if (csrfCalls === 1) return new Promise(resolve => {resolveFirst = resolve})
        return csrf('second')
      }
      posts++
      return response(201, {code: 'OK'})
    }
    const first = createFeedbackBoard({channel: 'daily', context: 'first', items: [{movie_id: 1}]})
    const second = createFeedbackBoard({channel: 'daily', context: 'second', items: [{movie_id: 2}]})
    const firstPending = first.record(1)
    await wait(70)
    const secondPending = second.record(1)
    await wait(60)
    second.dispose()
    resolveFirst(csrf('first'))
    assert.equal(await firstPending, true)
    assert.equal(await secondPending, false)
    assert.equal(posts, 1)
  })

  await check('pre-throttle entries cannot adopt a post-cooldown generation', async () => {
    const {createFeedbackBoard} = await loadModule()
    const realSetTimeout = globalThis.setTimeout
    const realClearTimeout = globalThis.clearTimeout
    const realNow = Date.now
    let fakeNow = realNow()
    let nextTimer = 1
    let timers = []
    globalThis.Date.now = () => fakeNow
    globalThis.setTimeout = (fn, ms) => {
      const timer = {id: nextTimer++, fn, due: fakeNow + ms}
      timers.push(timer)
      return timer.id
    }
    globalThis.clearTimeout = id => {timers = timers.filter(timer => timer.id !== id)}
    let csrfCalls = 0, posts = 0
    globalThis.fetch = async url => {
      if (String(url).endsWith('/csrf')) {
        csrfCalls++
        return csrfCalls === 1 ? response(429, {code: 42910}, {'Retry-After': '1'}) : csrf('after-cooldown')
      }
      posts++
      return response(201, {code: 'OK'})
    }
    try {
      const oldBoard = createFeedbackBoard({channel: 'daily', context: 'old', items: [{movie_id: 1}]})
      const triggerBoard = createFeedbackBoard({channel: 'daily', context: 'trigger', items: [{movie_id: 2}]})
      const oldPending = oldBoard.record(1)
      const oldTimer = timers[0]
      const triggerPending = triggerBoard.record(1)
      const triggerTimer = timers[1]
      assert.ok(oldTimer && triggerTimer)
      // Trigger the 429 without running the old board's 50ms timer.
      timers = timers.filter(timer => timer !== triggerTimer)
      fakeNow = Math.max(fakeNow, triggerTimer.due)
      triggerTimer.fn()
      assert.equal(await triggerPending, false)
      assert.equal(csrfCalls, 1)
      assert.equal(posts, 0)
      // The cooldown has expired, but the old queued entry is still old-generation.
      fakeNow += 2000
      timers = timers.filter(timer => timer !== oldTimer)
      oldTimer.fn()
      assert.equal(await oldPending, false)
      await Promise.resolve()
      assert.equal(csrfCalls, 1)
      assert.equal(posts, 0)
    } finally {
      globalThis.setTimeout = realSetTimeout
      globalThis.clearTimeout = realClearTimeout
      globalThis.Date.now = realNow
    }
  })

  await check('HTTP 429 from CSRF starts cooldown, drops new and queued work', async () => {
    const {createFeedbackBoard} = await loadModule()
    let csrfCalls = 0, posts = 0
    globalThis.fetch = async (url, options = {}) => {
      if (String(url).endsWith('/csrf')) {
        csrfCalls++
        return csrfCalls === 1 ? response(429, {code: 42910}, {'Retry-After': '1'}) : csrf('after-cooldown')
      }
      posts++
      return response(201, {code: 'OK'})
    }
    const board = createFeedbackBoard({channel: 'daily', context: 'test', items: [{movie_id: 1}]})
    assert.equal(await board.record(1), false)
    assert.equal(csrfCalls, 1)
    assert.equal(posts, 0)
    const queued = createFeedbackBoard({channel: 'daily', context: 'queued', items: [{movie_id: 2}]})
    assert.equal(await queued.record(1), false)
    assert.equal(csrfCalls, 1)
    await wait(1100)
    assert.equal(await queued.record(1), true)
    assert.equal(csrfCalls, 2)
    assert.equal(posts, 1)
  })

  await check('HTTP 429 from POST also cools down and does not replay the old batch', async () => {
    const {createFeedbackBoard} = await loadModule()
    let csrfCalls = 0, posts = 0
    globalThis.fetch = async (url, options = {}) => {
      if (String(url).endsWith('/csrf')) { csrfCalls++; return csrf('t' + csrfCalls) }
      posts++
      if (posts === 1) return response(429, {code: 42910}, {'Retry-After': '1'})
      return response(201, {code: 'OK'})
    }
    const board = createFeedbackBoard({channel: 'daily', context: 'test', items: [{movie_id: 1}]})
    assert.equal(await board.record(1), false)
    assert.equal(posts, 1)
    assert.equal(await board.record(2), false)
    assert.equal(csrfCalls, 1)
    await wait(1100)
    const fresh = createFeedbackBoard({channel: 'daily', context: 'fresh', items: [{movie_id: 3}]})
    assert.equal(await fresh.record(1), true)
    assert.equal(csrfCalls, 2)
    assert.equal(posts, 2)
  })

  await check('HTTP-date Retry-After and conservative missing-header backoff', async () => {
    const {createFeedbackBoard} = await loadModule()
    let csrfCalls = 0, posts = 0
    let header = new Date(Date.now() + 900).toUTCString()
    globalThis.fetch = async (url, options = {}) => {
      if (String(url).endsWith('/csrf')) { csrfCalls++; return csrf('t' + csrfCalls) }
      posts++
      return posts === 1 ? response(429, {code: 42910}, header ? {'Retry-After': header} : {}) : response(201, {code: 'OK'})
    }
    const board = createFeedbackBoard({channel: 'daily', context: 'test', items: [{movie_id: 1}]})
    assert.equal(await board.record(1), false)
    await wait(1000)
    const fresh = createFeedbackBoard({channel: 'daily', context: 'fresh', items: [{movie_id: 2}]})
    assert.equal(await fresh.record(1), true)
    assert.equal(csrfCalls, 2)

    const {createFeedbackBoard: createMissing} = await loadModule()
    header = null
    let missingCsrf = 0
    globalThis.fetch = async (url, options = {}) => {
      if (String(url).endsWith('/csrf')) { missingCsrf++; return missingCsrf === 1 ? response(429, {code: 42910}) : csrf('after-backoff') }
      return response(201, {code: 'OK'})
    }
    const missing = createMissing({channel: 'daily', context: 'test', items: [{movie_id: 1}]})
    assert.equal(await missing.record(1), false)
    const realNow = Date.now()
    const originalNow = Date.now
    Date.now = () => realNow + 59_000
    assert.equal(await missing.record(1), false)
    Date.now = () => realNow + 61_000
    const freshMissing = createMissing({channel: 'daily', context: 'fresh', items: [{movie_id: 2}]})
    assert.equal(await freshMissing.record(1), true)
    Date.now = originalNow
    assert.equal(missingCsrf, 2)

    const {createFeedbackBoard: createHuge} = await loadModule()
    let hugeCsrf = 0
    globalThis.fetch = async (url, options = {}) => {
      if (String(url).endsWith('/csrf')) {
        hugeCsrf++
        return hugeCsrf === 1
          ? response(429, {code: 42910}, {'Retry-After': '1' + '0'.repeat(308)})
          : csrf('after-huge-cap')
      }
      return response(201, {code: 'OK'})
    }
    const huge = createHuge({channel: 'daily', context: 'huge', items: [{movie_id: 1}]})
    assert.equal(await huge.record(1), false)
    const hugeNow = Date.now()
    const hugeOriginal = Date.now
    Date.now = () => hugeNow + 86_400_001
    const hugeFresh = createHuge({channel: 'daily', context: 'huge-fresh', items: [{movie_id: 2}]})
    assert.equal(await hugeFresh.record(1), true)
    Date.now = hugeOriginal
    assert.equal(hugeCsrf, 2)
  })

  await check('one-second visibility dwell and observer cleanup remain intact', async () => {
    const {vFeedback} = await loadModule()
    let callback, timer, disconnected = false, recorded = 0
    globalThis.IntersectionObserver = class {constructor(cb) {callback = cb} observe() {} disconnect() {disconnected = true}}
    globalThis.setTimeout = cb => {timer = cb; return 1}
    globalThis.clearTimeout = () => {timer = undefined}
    const el = {}
    vFeedback.mounted(el, {value: {board: {record() {recorded++}}, position: 1}})
    callback([{isIntersecting: true, intersectionRatio: 0.6}])
    assert.equal(recorded, 0)
    callback([{isIntersecting: false, intersectionRatio: 0}])
    assert.equal(timer, undefined)
    callback([{isIntersecting: true, intersectionRatio: 0.6}])
    timer()
    assert.equal(recorded, 1)
    assert.ok(disconnected)
    vFeedback.beforeUnmount(el)
  })

  console.log(JSON.stringify({all_pass: true, checks}, null, 2))
} finally {
  globalThis.fetch = oldFetch
  globalThis.IntersectionObserver = oldObserver
  globalThis.setTimeout = oldSetTimeout
  globalThis.clearTimeout = oldClearTimeout
  Date.now = oldDateNow
}