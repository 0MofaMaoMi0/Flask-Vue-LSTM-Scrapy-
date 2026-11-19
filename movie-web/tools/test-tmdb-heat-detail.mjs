import fs from 'node:fs/promises'
import assert from 'node:assert/strict'
import {spawn} from 'node:child_process'
import path from 'node:path'
import {fileURLToPath} from 'node:url'
import {chromium} from '@playwright/test'

const here=path.dirname(fileURLToPath(import.meta.url))
const root=path.resolve(here,'..')
const taskDir=path.resolve(root,'../../项目工作日志/06-开发与验证/首页热门库外-20261116')
const shotDir=path.join(taskDir,'截图')
await fs.mkdir(shotDir,{recursive:true})
const home=await fs.readFile(path.join(root,'src/views/HomeView.vue'),'utf8')
const detail=await fs.readFile(path.join(root,'src/views/MovieDetailView.vue'),'utf8')

// Home contract: one RAIL=13 feeds both requests; popular is mixed scope=all
// while daily stays local. Identity and poster state must not use movie_id as
// a shared key because external cards carry movie_id=null.
assert.match(home,/const RAIL=13/)
assert.equal((home.match(/recommendations\?channel=daily&limit='\+RAIL/g)||[]).length,1)
assert.equal((home.match(/recommendations\?channel=popular&scope=all&limit='\+RAIL/g)||[]).length,1)
assert.doesNotMatch(home,/scope=local/)
assert.doesNotMatch(home,/mi2_tmdb_heat/)
assert.doesNotMatch(home,/slice\(0,\s*(?:6|8)\)/)
assert.doesNotMatch(home,/\bmovies=ref/)
assert.match(home,/const dailyItems=ref<any\[\]>\(\[\]\),popularItems=ref<any\[\]>\(\[\]\)/)
assert.match(home,/const channels=computed\(\(\)=>\[\{id:'personal'.*items:dailyItems\.value/)
assert.match(home,/items:popularItems\.value/)
assert.match(home,/:key="cardKey\(movie\)"/)
assert.match(home,/in_catalog!==false/)
assert.match(home,/https:\/\/www\.themoviedb\.org\/movie\//)
assert.match(home,/target="?isExternal\(movie\)/)
assert.doesNotMatch(home,/tmdb_url|window\.open/)
assert.match(home,/position:index\+1/)
assert.doesNotMatch(detail,/mi2_tmdb_heat/)
assert.match(detail,/movie\.value\?\.tmdb_heat/)

const daily=Array.from({length:13},(_,i)=>({movie_id:i+1,tmdb_id:1001+i,title:'每日影片 '+(i+1),original_title:'Daily '+i,release_year:2026-i%3,genres:[],assets:[],poster:null,poster_fallback:null}))
const localPopular=Array.from({length:10},(_,i)=>({movie_id:101+i,tmdb_id:2001+i,title:'热门影片 '+(i+1),original_title:'Popular '+i,release_year:2025-i%3,genres:[],assets:[],poster:null,poster_fallback:null}))
const external=[
  {movie_id:null,tmdb_id:'3001',in_catalog:false,title:'库外影片 1',original_title:'Outside 1',release_year:2024,genres:[],assets:[],poster:'/posters/01.jpg',poster_fallback:null},
  {movie_id:null,tmdb_id:'3002',in_catalog:false,title:'库外影片 2',original_title:'Outside 2',release_year:2023,genres:[],assets:[],poster:'/posters/missing.jpg',poster_fallback:null},
  {movie_id:null,tmdb_id:'3003',in_catalog:false,title:'库外影片 3',original_title:'Outside 3',release_year:2022,genres:[],assets:[],poster:'/posters/03.jpg',poster_fallback:null},
]
const mixedPopular=[localPopular[0],external[0],localPopular[1],localPopular[2],localPopular[3],external[1],localPopular[4],localPopular[5],localPopular[6],external[2],localPopular[7],localPopular[8],localPopular[9]]
const expectedMixedTitles=mixedPopular.map(m=>m.title)
const externalOnly=Array.from({length:13},(_,i)=>({movie_id:null,tmdb_id:String(4001+i),in_catalog:false,title:'全库外 '+(i+1),original_title:'All external '+i,release_year:2021,genres:[],assets:[],poster:null,poster_fallback:null}))
let scenario='mixed'
const byId=new Map([...localPopular,...daily].map(m=>[m.movie_id,m]))
const heatById=new Map([[101,{tmdb_id:2001,status:'OK',popularity:12.34,observed_at:'2026-11-16T00:00:00+00:00',source:'popular'}],[102,{tmdb_id:2002,status:'STALE',popularity:77.7,observed_at:'2026-11-16T01:00:00+00:00',source:'trending_day'}],[103,null]])
const behaviorState={'null':{watched:true},'101':{want:true}}
function detailData(id){
  const movie=byId.get(id)||{movie_id:id,title:'测试影片 '+id,release_year:2026}
  return {...movie,original_language:'en',runtime_minutes:120,overview:'离线合成测试数据。',credits:[],ratings:[],external_ids:[{source:'tmdb',external_id:String(2000+(id-100))}],tmdb_heat:heatById.get(id)??null}
}
const unhandled=[]
function catalog(data){return {code:0,data}}
async function routeApi(route){
  const request=route.request()
  const url=new URL(request.url())
  const method=request.method()
  if(!url.pathname.startsWith('/api/'))return route.continue()
  if(url.pathname==='/api/v1/auth/csrf')return route.fulfill({status:200,contentType:'application/json',body:JSON.stringify({code:0,data:{csrf_token:'synthetic-csrf'}})})
  if(url.pathname==='/api/v1/auth/me')return route.fulfill({status:200,contentType:'application/json',body:JSON.stringify({code:0,data:{id:1,username:'deepseek',is_admin:false}})})
  if(url.pathname==='/api/v1/account/profile')return route.fulfill({status:200,contentType:'application/json',body:JSON.stringify({code:0,data:{user_id:1,avatar:'',version:1}})})
  if(url.pathname==='/api/v1/assistant/status')return route.fulfill({status:200,contentType:'application/json',body:JSON.stringify({code:0,data:{configured:false,enabled:false,models:[]}})})
  if(url.pathname==='/api/catalog/behavior')return route.fulfill({status:200,contentType:'application/json',body:JSON.stringify(catalog({state:behaviorState}))})
  if(url.pathname==='/api/catalog/recommendations'){
    const channel=url.searchParams.get('channel')||'daily'
    if(channel==='daily'&&scenario==='externalOnly')return route.fulfill({status:503,contentType:'application/json',body:JSON.stringify({code:'UNAVAILABLE',data:null})})
    const items=channel==='popular'?(scenario==='externalOnly'?externalOnly:mixedPopular):daily
    return route.fulfill({status:200,contentType:'application/json',body:JSON.stringify(catalog({items}))})
  }
  if(url.pathname==='/api/catalog/movies'||url.pathname==='/api/catalog/genres')return route.fulfill({status:200,contentType:'application/json',body:JSON.stringify(catalog({items:url.pathname.endsWith('genres')?[]:localPopular,limit:13,offset:0}))})
  if(url.pathname.startsWith('/api/catalog/movies/')&&method==='GET'){
    const id=Number(url.pathname.split('/').pop());return route.fulfill({status:200,contentType:'application/json',body:JSON.stringify(catalog(detailData(id)))})
  }
  if(url.pathname==='/api/catalog/production-countries')return route.fulfill({status:200,contentType:'application/json',body:JSON.stringify(catalog({items:[]}))})
  if(url.pathname.includes('recommendation-'))return route.fulfill({status:200,contentType:'application/json',body:JSON.stringify(catalog({ok:true}))})
  unhandled.push(method+' '+url.pathname+url.search)
  return route.fulfill({status:200,contentType:'application/json',body:JSON.stringify(catalog({items:[],state:{}}))})
}

async function waitForServer(base){
  for(let i=0;i<60;i++){
    try{const response=await fetch(base+'/home',{signal:AbortSignal.timeout(800)});if(response.ok)return}catch{}
    await new Promise(resolve=>setTimeout(resolve,500))
  }
  throw new Error('Vite server did not start')
}
const server=spawn(process.execPath,[path.join(root,'node_modules/vite/bin/vite.js'),'--host','127.0.0.1','--port','5189','--strictPort'],{cwd:root,stdio:'ignore',env:{...process.env,DO_NOT_TRACK:'1'}})
const base='http://127.0.0.1:5189'
let browser,context
const browserTemp=path.join(taskDir,'.browser-tmp')
await fs.mkdir(browserTemp,{recursive:true})
process.env.TMP=browserTemp
process.env.TEMP=browserTemp
try{
  await waitForServer(base)
  browser=await chromium.launch({channel:'chrome',headless:true,timeout:30000,args:['--no-sandbox','--disable-gpu','--no-proxy-server']})
  context=await browser.newContext({viewport:{width:1440,height:1000},deviceScaleFactor:1})
  await context.addInitScript(()=>{try{sessionStorage.setItem('mi2_tmdb_heat',JSON.stringify({101:{popularity:999,observed:'2020-01-01'},102:{popularity:999,observed:'2020-01-01'},103:{popularity:999,observed:'2020-01-01'}}))}catch{}})
  await context.route('**/api/**',routeApi)
  const page=await context.newPage()
  const pageErrors=[]
  page.on('pageerror',error=>pageErrors.push(error.message))
  page.on('console',message=>{if(message.type()!=='error')return;const url=message.location().url;if(url.includes('/posters/missing.jpg')||(url.includes('/api/catalog/recommendations')&&message.text().includes('503')))return;pageErrors.push(message.text())})
  await page.goto(base+'/home',{waitUntil:'domcontentloaded',timeout:15000})
  await page.waitForFunction(()=>document.querySelectorAll('.filmcard').length===26,{timeout:10000})
  await page.waitForSelector('.library[data-channel="personal"] .rail-arrow.right')
  await page.waitForSelector('.library[data-channel="popular"] .rail-arrow.right')

  const heroTitle=await page.locator('.hero h1').innerText()
  assert.ok([...daily,...localPopular].some(movie=>movie.title===heroTitle),'hero stays a local catalog movie')
  const popularCards=page.locator('.library[data-channel="popular"] .filmcard')
  assert.equal(await popularCards.count(),13,'popular keeps exactly 13 cards')
  assert.equal((await page.locator('.library[data-channel="popular"] .rail-meta small').innerText()).includes('含库外影片'),true,'popular note describes mixed scope')
  assert.deepEqual(await page.locator('.library[data-channel="popular"] .film-title').allInnerTexts(),expectedMixedTitles,'popular preserves backend order')
  const keys=await popularCards.evaluateAll(nodes=>nodes.map(node=>node.getAttribute('data-card-key')))
  assert.equal(new Set(keys).size,13,'all popular cards have unique business keys')
  assert.deepEqual(keys.filter(key=>key?.startsWith('tmdb:')),['tmdb:3001','tmdb:3002','tmdb:3003'])
  assert.equal(await page.locator('.library[data-channel="popular"] a.poster-shell').count(),3,'three external cards use links')
  assert.equal(await page.locator('.library[data-channel="popular"] button.poster-shell').count(),10,'ten local cards stay buttons')
  const links=await page.locator('.library[data-channel="popular"] a.poster-shell').evaluateAll(nodes=>nodes.map(node=>({href:node.getAttribute('href'),target:node.getAttribute('target'),rel:node.getAttribute('rel'),aria:node.getAttribute('aria-label'),title:node.closest('.filmcard')?.querySelector('.film-title')?.textContent})))
  assert.deepEqual(links.map(link=>link.href),['https://www.themoviedb.org/movie/3001','https://www.themoviedb.org/movie/3002','https://www.themoviedb.org/movie/3003'])
  for(const link of links){assert.equal(link.target,'_blank');assert.equal(link.rel,'noopener noreferrer');assert.match(link.aria,/库外影片/)}
  assert.equal(await page.locator('.library[data-channel="popular"] .external-mark').count(),3,'external badge appears per external card')
  assert.equal(await page.locator('[data-card-key="local:101"] .marker').innerText(),'想看')
  assert.equal(await page.locator('[data-card-key="tmdb:3001"] .marker').count(),0,'null movie_id cannot inherit behavior')

  await page.waitForFunction(()=>document.querySelector('[data-card-key="tmdb:3001"] .poster-shell')?.classList.contains('loaded'),null,{timeout:10000})
  const posterState=await page.evaluate(()=>{
    const card=key=>document.querySelector(`[data-card-key="${key}"] .poster-shell`)
    return {good:!!card('tmdb:3001')?.classList.contains('loaded'),bad:!!card('tmdb:3002')?.classList.contains('loaded'),badFallback:getComputedStyle(document.querySelector('[data-card-key="tmdb:3002"] .poster-fallback')).display}
  })
  assert.deepEqual(posterState,{good:true,bad:false,badFallback:'flex'},'poster load/failure state is isolated per external key')

  const titleOverlap=await page.evaluate(()=>{
    const arrows=[...document.querySelectorAll('.rail-arrow')].map(el=>el.getBoundingClientRect())
    const titles=[...document.querySelectorAll('.film-title')].map(el=>el.getBoundingClientRect())
    return arrows.reduce((count,arrow)=>count+titles.filter(title=>arrow.left<title.right&&arrow.right>title.left&&arrow.top<title.bottom&&arrow.bottom>title.top).length,0)
  })
  assert.equal(titleOverlap,0,'scroll controls do not cover film titles')
  const rowScroll=async channel=>page.locator(`.library[data-channel="${channel}"] .filmrow`).evaluate(el=>el.scrollLeft)
  const personalRight=page.locator('.library[data-channel="personal"] .rail-arrow.right')
  const personalLeft=page.locator('.library[data-channel="personal"] .rail-arrow.left')
  assert.equal(await rowScroll('personal'),0)
  assert.equal(await rowScroll('popular'),0)
  await personalRight.click()
  await page.waitForTimeout(550)
  assert.ok(await rowScroll('personal')>0,'personal rail scrolls forward')
  assert.equal(await rowScroll('popular'),0,'popular rail stays independent')
  await personalLeft.click()
  await page.waitForTimeout(550)
  assert.equal(await rowScroll('personal'),0,'personal rail returns to the left boundary')
  assert.equal(await personalLeft.count(),0,'left arrow hides at the left boundary')
  assert.equal(await personalRight.count(),1,'right arrow remains at the left boundary')

  await page.locator('[data-card-key="local:104"] .poster-shell').click()
  await page.waitForFunction(()=>location.pathname==='/movies'&&location.hash==='#movie/104')
  const localUrl=new URL(page.url())
  assert.equal(localUrl.pathname,'/movies','local card keeps the existing detail path')
  assert.equal(localUrl.search,'?from=home','local card keeps the existing home-origin query')
  assert.equal(localUrl.hash,'#movie/104','local card keeps the existing movie hash')
  await page.goto(base+'/home',{waitUntil:'domcontentloaded'})
  await page.waitForFunction(()=>document.querySelectorAll('.filmcard').length===26,{timeout:10000})

  await page.setViewportSize({width:390,height:844})
  await page.evaluate(()=>window.scrollTo(0,0))
  await page.waitForTimeout(350)
  assert.equal(await page.locator('.library[data-channel="personal"] .rail-arrow.right').count(),1,'narrow rail keeps its right control')
  assert.equal(await page.locator('.library[data-channel="popular"] .rail-arrow.right').count(),1,'both narrow rails keep controls')
  const narrowTitleOverlap=await page.evaluate(()=>[...document.querySelectorAll('.rail-arrow')].reduce((count,arrowEl)=>{const arrow=arrowEl.getBoundingClientRect();return count+[...document.querySelectorAll('.film-title')].filter(titleEl=>{const title=titleEl.getBoundingClientRect();return arrow.left<title.right&&arrow.right>title.left&&arrow.top<title.bottom&&arrow.bottom>title.top}).length},0))
  assert.equal(narrowTitleOverlap,0,'narrow scroll controls do not cover film titles')
  await page.screenshot({path:path.join(shotDir,'home-mixed-narrow.png'),fullPage:false})
  await page.setViewportSize({width:1440,height:1000})
  await page.waitForTimeout(300)
  await page.evaluate(()=>window.scrollTo(0,0))
  await page.screenshot({path:path.join(shotDir,'home-mixed-desktop.png'),fullPage:false})

  scenario='externalOnly'
  await page.goto(base+'/home',{waitUntil:'domcontentloaded'})
  await page.waitForFunction(()=>document.querySelectorAll('.filmcard').length===13,{timeout:10000})
  assert.equal(await page.locator('.site').count(),1,'external-only still renders the page')
  assert.equal(await page.locator('.hero').count(),0,'external-only does not invent a hero')
  assert.equal(await page.locator('.library[data-channel="popular"] a.poster-shell').count(),13)
  assert.equal(await page.locator('.library[data-channel="popular"] button.poster-shell').count(),0)
  assert.equal(await page.locator('.channel-error').count(),1,'daily failure is shown without blanking the page')
  const externalHrefs=await page.locator('.library[data-channel="popular"] a.poster-shell').evaluateAll(nodes=>nodes.map(node=>node.getAttribute('href')))
  assert.equal(externalHrefs.every(href=>/^https:\/\/www\.themoviedb\.org\/movie\/\d+$/.test(href)),true)
  await page.screenshot({path:path.join(shotDir,'home-external-only-desktop.png'),fullPage:false})

  scenario='mixed'
  // Direct detail entry: stale sessionStorage must be ignored, IDs must not
  // share heat, and a missing observation must not render a zero/placeholder.
  await page.goto(base+'/movies#movie/101',{waitUntil:'domcontentloaded'})
  await page.waitForFunction(()=>document.body.innerText.includes('TMDB 热度'))
  let text=await page.locator('body').innerText()
  assert.match(text,/12\.3/)
  assert.match(text,/2026-11-16/)
  assert.doesNotMatch(text,/999/)
  await page.screenshot({path:path.join(shotDir,'detail-desktop.png'),fullPage:false})

  await page.goto(base+'/movies#movie/102',{waitUntil:'domcontentloaded'})
  await page.waitForFunction(()=>document.body.innerText.includes('77.7'))
  text=await page.locator('body').innerText()
  assert.match(text,/已过期/)
  assert.doesNotMatch(text,/999|12\.3/)

  await page.goto(base+'/movies#movie/103',{waitUntil:'domcontentloaded'})
  await page.waitForFunction(()=>document.body.innerText.includes('热门影片 3'))
  text=await page.locator('body').innerText()
  assert.doesNotMatch(text,/TMDB 热度|热度采集于/)
  assert.equal(pageErrors.length,0,pageErrors.join('\n'))
  assert.deepEqual(unhandled,[])
  console.log('PASS: mixed 13-card popular rail with 3 external links; unique keys/order/local detail, isolated poster failures, independent arrows, external-only page without hero; detail heat checks; screenshots saved')
}finally{
  if(context)await context.close()
  if(browser)await browser.close()
  server.kill()
  await fs.rm(browserTemp,{recursive:true,force:true}).catch(()=>{})
}