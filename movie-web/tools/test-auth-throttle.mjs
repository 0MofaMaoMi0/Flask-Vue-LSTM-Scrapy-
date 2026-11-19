import fs from 'node:fs/promises'
import path from 'node:path'
import assert from 'node:assert/strict'
import ts from 'typescript'
import {fileURLToPath} from 'node:url'
import {createServer} from 'vite'
import vue from '@vitejs/plugin-vue'
import {flagIsosPlugin} from './vite-plugin-flag-isos.mjs'
import {chromium} from '@playwright/test'

const HERE = path.dirname(fileURLToPath(import.meta.url))
const WEB = path.resolve(HERE, '..')
const TASK = path.resolve(WEB, '../../项目工作日志/06-开发与验证/登录限流修复-20261115')
const CACHE = path.join(TASK, '.auth-test-cache')
await fs.mkdir(CACHE, {recursive: true})
const checks = []
const authSource = await fs.readFile(path.join(WEB, 'src/api/auth.ts'), 'utf8')
const authJs = ts.transpile(authSource, {target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ESNext})
let authSeq = 0
async function loadAuth() {
  return import('data:text/javascript;base64,' + Buffer.from(authJs).toString('base64') + '#' + (++authSeq))
}
const response = (status, body, headers = {}) => ({
  ok: status >= 200 && status < 300,
  status,
  headers: {get: name => headers[name] ?? headers[name.toLowerCase()] ?? null},
  json: async () => body,
})
const oldFetch = globalThis.fetch
function pass(name, extra = {}) { checks.push({name, passed: true, ...extra}) }

try {
  const auth = await loadAuth()
  let calls = []
  globalThis.fetch = async url => {
    calls.push(String(url))
    return response(429, {code: 42910}, {'Retry-After': '7'})
  }
  await assert.rejects(() => auth.call('login', 'POST', {}), error => {
    assert.ok(error instanceof auth.AuthError)
    assert.equal(error.status, 429)
    assert.equal(error.retryAfterSeconds, 7)
    assert.ok(error.retryAfterAt >= Date.now() + 6000)
    assert.equal(error.retryAfterSource, 'seconds')
    return true
  })
  const state = auth.getAuthThrottle()
  assert.equal(state.message, '操作过于频繁，请稍后重试')
  assert.equal(state.source, 'login')
  assert.equal(state.retryAfterSeconds, 7)
  assert.equal(calls.length, 1, 'a 429 must not retry automatically')
  pass('429 seconds metadata and no automatic retry')

  const dateAuth = await loadAuth()
  let dateCalls = 0
  const retryDate = new Date(Date.now() + 5000).toUTCString()
  globalThis.fetch = async () => { dateCalls++; return response(429, {code: 42910}, {'Retry-After': retryDate}) }
  await assert.rejects(() => dateAuth.call('me'), error => {
    assert.ok(error instanceof dateAuth.AuthError)
    assert.equal(error.retryAfterSource, 'http-date')
    assert.ok(error.retryAfterSeconds >= 3 && error.retryAfterSeconds <= 5)
    assert.ok(error.retryAfterAt >= Date.now() + 3000)
    return true
  })
  assert.equal(dateCalls, 1)
  pass('HTTP-date Retry-After metadata')

  const hugeAuth = await loadAuth()
  globalThis.fetch = async () => response(429, {code: 42910}, {'Retry-After': '1' + '0'.repeat(308)})
  await assert.rejects(() => hugeAuth.call('me'), error => {
    assert.ok(error instanceof hugeAuth.AuthError)
    assert.equal(Number.isFinite(error.retryAfterAt), true)
    assert.ok(error.retryAfterAt <= Date.now() + 86_400_000 + 1000)
    assert.ok(error.retryAfterSeconds <= 86_400)
    return true
  })
  assert.equal(Number.isFinite(hugeAuth.getAuthThrottle().retryAfterAt), true)
  pass('huge numeric Retry-After is capped to a finite deadline')

  const invalidAuth = await loadAuth()
  globalThis.fetch = async () => response(429, {code: 42910}, {'Retry-After': 'not-a-date'})
  await assert.rejects(() => invalidAuth.call('csrf'), error => {
    assert.ok(error instanceof invalidAuth.AuthError)
    assert.equal(error.retryAfterAt, undefined)
    assert.equal(error.retryAfterSeconds, undefined)
    return true
  })
  const invalidState = invalidAuth.getAuthThrottle()
  assert.ok(invalidState)
  assert.equal(invalidState.retryAfterAt, undefined)
  assert.equal(invalidState.message, '操作过于频繁，请稍后重试')
  pass('invalid Retry-After keeps honest no-deadline state')

  const mapAuth = await loadAuth()
  globalThis.fetch = async () => response(401, {code: 401})
  await assert.rejects(() => mapAuth.call('me'), error => {
    assert.ok(error instanceof mapAuth.AuthError)
    assert.equal(error.status, 401)
    assert.equal(error.message, '未登录或账号密码不正确')
    return true
  })
  pass('401 mapping unchanged')

  const reviewAuth = await loadAuth()
  globalThis.fetch = async () => response(403, {code: 'REVIEW_READ_ONLY'})
  await assert.rejects(() => reviewAuth.call('me'), error => {
    assert.ok(error instanceof reviewAuth.AuthError)
    assert.equal(error.status, 403)
    assert.match(error.message, /只读验收环境/)
    return true
  })
  pass('403 review mapping unchanged')

  const successAuth = await loadAuth()
  globalThis.fetch = async () => response(200, {code: 0, data: {id: 1, username: 'synthetic'}})
  await successAuth.call('me')
  assert.equal(successAuth.getAuthThrottle(), null)
  pass('successful auth call clears display-only throttle state')

  globalThis.fetch = oldFetch
} finally {
  globalThis.fetch = oldFetch
}

const server = await createServer({
  configFile: false,
  root: WEB,
  cacheDir: path.join(CACHE, 'vite'),
  plugins: [vue(), flagIsosPlugin()],
  server: {host: '127.0.0.1', port: 15976, strictPort: true},
  logLevel: 'error',
})
await server.listen()
const address = server.httpServer.address()
const base = `http://127.0.0.1:${address.port}`
const chrome = 'C:/Program Files/Google/Chrome/Application/chrome.exe'
const browser = await chromium.launch({
  executablePath: chrome,
  headless: true,
  env: {...process.env, TEMP: CACHE, TMP: CACHE, TMPDIR: CACHE},
  args: [`--disk-cache-dir=${path.join(CACHE, 'chrome')}`, '--no-first-run', '--disable-background-networking'],
})
const context = await browser.newContext({locale: 'zh-CN', timezoneId: 'Asia/Shanghai'})

async function newPage() {
  const page = await context.newPage()
  await page.addInitScript(() => {
    const ids = new Set()
    const delays = new Map()
    const set = window.setInterval.bind(window)
    const clear = window.clearInterval.bind(window)
    window.__testIntervalIds = ids
    window.__testIntervalDelays = delays
    window.setInterval = (fn, delay, ...args) => {const id = set(fn, delay, ...args); ids.add(id); delays.set(id, delay); return id}
    window.clearInterval = id => {ids.delete(id); delays.delete(id); return clear(id)}
  })
  return page
}

async function apiPage(mode) {
  const page = await newPage()
  const calls = []
  const pageErrors = []
  page.on('pageerror', error => pageErrors.push(String(error)))
  await page.route('**/api/v1/**', async route => {
    const request = route.request()
    const pathname = new URL(request.url()).pathname
    calls.push({path: pathname, method: request.method()})
    if (pathname.endsWith('/api/v1/auth/csrf')) {
      if (mode === 'init429') return route.fulfill({status: 429, headers: {'Content-Type': 'application/json', 'Retry-After': '2'}, body: JSON.stringify({code: 42910, message: 'too many requests'})})
      if (mode === 'init429Invalid') return route.fulfill({status: 429, headers: {'Content-Type': 'application/json'}, body: JSON.stringify({code: 42910, message: 'too many requests'})})
      return route.fulfill({status: 200, headers: {'Content-Type': 'application/json'}, body: JSON.stringify({code: 0, data: {csrf_token: 'synthetic-csrf'}})})
    }
    if (pathname.endsWith('/api/v1/auth/me')) return route.fulfill({status: 401, headers: {'Content-Type': 'application/json'}, body: JSON.stringify({code: 401})})
    if (pathname.endsWith('/api/v1/auth/login')) {
      if (mode === 'login429') return route.fulfill({status: 429, headers: {'Content-Type': 'application/json', 'Retry-After': '2'}, body: JSON.stringify({code: 42910, message: 'too many requests'})})
      return route.fulfill({status: 401, headers: {'Content-Type': 'application/json'}, body: JSON.stringify({code: 401})})
    }
    return route.fulfill({status: 404, headers: {'Content-Type': 'application/json'}, body: JSON.stringify({code: 404})})
  })
  await page.goto(base + '/login', {waitUntil: 'domcontentloaded'})
  try {await page.waitForSelector('#loginForm', {timeout: 8000})} catch (error) {
    console.error('LOGIN_PAGE_DEBUG', {url: page.url(), calls, pageErrors, body: (await page.content()).slice(0, 1200)})
    throw error
  }
  return {page, calls}
}

try {
  const init = await apiPage('init429')
  await init.page.waitForFunction(() => document.querySelector('#loginMessage')?.textContent?.includes('操作过于频繁'))
  assert.equal(await init.page.locator('#loginForm button.submit').isDisabled(), true)
  assert.match(await init.page.locator('#loginMessage').innerText(), /秒后重试/)
  assert.equal(init.calls.filter(x => x.path.endsWith('/csrf')).length, 1)
  const duringIds = await init.page.evaluate(() => [...window.__testIntervalIds].map(id => ({id, delay: window.__testIntervalDelays.get(id)})))
  assert.ok(duringIds.some(x => x.delay === 250), JSON.stringify(duringIds))
  // Use the component's own SPA back button so unmount, not a page reload,
  // proves the countdown interval is cleared; Welcome may start its own timer.
  await init.page.locator('button.back').click()
  await init.page.waitForTimeout(120)
  const afterIds = await init.page.evaluate(() => [...window.__testIntervalIds].map(id => ({id, delay: window.__testIntervalDelays.get(id)})))
  assert.equal(afterIds.some(x => x.delay === 250), false, JSON.stringify({duringIds, afterIds}))
  pass('actual LoginView initialization csrf429 starts and unmount clears its timer')

  const countdown = await apiPage('init429')
  await countdown.page.waitForFunction(() => document.querySelector('#loginMessage')?.textContent?.includes('操作过于频繁'))
  assert.equal(await countdown.page.locator('#loginForm button.submit').isDisabled(), true)
  await countdown.page.setViewportSize({width: 390, height: 844})
  assert.equal(await countdown.page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth), true)
  await countdown.page.evaluate(() => document.getElementById('loginForm').requestSubmit())
  await countdown.page.waitForTimeout(150)
  assert.equal(countdown.calls.filter(x => x.path.endsWith('/auth/login')).length, 0)
  await countdown.page.waitForTimeout(2300)
  assert.equal(await countdown.page.locator('#loginForm button.submit').isEnabled(), true)
  assert.equal(countdown.calls.filter(x => x.path.endsWith('/csrf')).length, 1)
  pass('actual LoginView countdown suppresses submit, restores, and does not auto-retry')

  const invalid = await apiPage('init429Invalid')
  await invalid.page.waitForFunction(() => document.querySelector('#loginError')?.textContent?.includes('稍后手动重试'))
  assert.equal(await invalid.page.locator('#loginForm button.submit').isEnabled(), true)
  assert.equal(await invalid.page.locator('#loginMessage').innerText(), '')
  pass('actual LoginView without Retry-After shows honest manual-retry prompt, no fake countdown')

  const submit = await apiPage('login429')
  await submit.page.locator('#loginName').fill('synthetic-user')
  await submit.page.locator('#loginPassword').fill('synthetic-password')
  await submit.page.locator('#loginForm button.submit').click()
  await submit.page.waitForFunction(() => document.querySelector('#loginMessage')?.textContent?.includes('秒后重试'))
  assert.equal(await submit.page.locator('#loginForm button.submit').isDisabled(), true)
  assert.equal(submit.calls.filter(x => x.path.endsWith('/auth/login')).length, 1)
  await submit.page.evaluate(() => document.getElementById('loginForm').requestSubmit())
  await submit.page.waitForTimeout(150)
  assert.equal(submit.calls.filter(x => x.path.endsWith('/auth/login')).length, 1)
  await submit.page.waitForTimeout(2300)
  assert.equal(await submit.page.locator('#loginForm button.submit').isEnabled(), true)
  assert.equal(submit.calls.filter(x => x.path.endsWith('/auth/login')).length, 1)
  pass('actual LoginView submit429 countdown disables and does not auto-retry')

  console.log(JSON.stringify({all_pass: true, checks}, null, 2))
} finally {
  await context.close()
  await browser.close()
  await server.close()
  globalThis.fetch = oldFetch
}