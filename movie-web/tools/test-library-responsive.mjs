import fs from 'node:fs/promises'
import assert from 'node:assert/strict'
import {spawn} from 'node:child_process'
import path from 'node:path'
import {fileURLToPath} from 'node:url'
import {chromium} from '@playwright/test'

const here=path.dirname(fileURLToPath(import.meta.url))
const root=path.resolve(here,'..')
const taskDir=path.resolve(root,'../../项目工作日志/06-开发与验证/电影库自适应-20261116')
const shotDir=path.join(taskDir,'截图')
const outDir=path.join(taskDir,'测试输出')
const phase=process.env.UI_UNIFY_PHASE||'before'
const strict=process.env.UI_UNIFY_STRICT==='1'
const review=process.env.UI_UNIFY_REVIEW==='1'
const detailOnly=process.env.UI_UNIFY_DETAIL_ONLY==='1'
await fs.mkdir(shotDir,{recursive:true})
await fs.mkdir(outDir,{recursive:true})

const genres=[
  {genre_id:1,name_zh:'剧情'},{genre_id:2,name_zh:'科幻'},{genre_id:3,name_zh:'犯罪'},
  {genre_id:4,name_zh:'悬疑'},{genre_id:5,name_zh:'冒险'},{genre_id:6,name_zh:'动画'},
]
const posterPaths=['/m9/shaw-poster.jpg','/m9/inception-poster.jpg','/movie-artwork/tmdb-550-poster.jpg','/movie-artwork/tmdb-680-poster.jpg','/movie-artwork/tmdb-13-poster.jpg','/movie-artwork/tmdb-424-poster.jpg']
const backdropPaths=['/m9/shaw-hero.jpg','/m9/inception-hero.jpg','/movie-artwork/tmdb-550-backdrop.jpg','/movie-artwork/tmdb-680-backdrop.jpg','/movie-artwork/tmdb-13-backdrop.jpg','/movie-artwork/tmdb-424-backdrop.jpg']
const titles=[
  ['肖申克的救赎','The Shawshank Redemption',1994,1],
  ['盗梦空间','Inception',2010,2],
  ['搏击俱乐部','Fight Club',1999,3],
  ['星际穿越','Interstellar',2014,4],
  ['阿甘正传','Forrest Gump',1994,5],
  ['辛德勒的名单','Schindler\'s List',1993,6],
  ['教父','The Godfather',1972,3],
  ['低俗小说','Pulp Fiction',1994,3],
  ['千与千寻','Spirited Away',2001,6],
  ['星球大战','Star Wars',1977,5],
  ['指环王：护戒使者','The Lord of the Rings',2001,5],
  ['教父2','The Godfather Part II',1974,3],
]
const ids=[1,2,1015,1011,1005,1018,1007,1008,1020,1019,1006,1002]
const movies=titles.map((row,i)=>({
  movie_id:ids[i],tmdb_id:String(278+i),title:row[0],original_title:row[1],release_year:row[2],
  runtime_minutes:118+i*3,genres:[{genre_id:row[3],name_zh:genres.find(g=>g.genre_id===row[3])?.name_zh||'剧情'}],
  overview:'一段被时间反复检验的故事，人物在选择的边缘寻找自己的答案。',
  poster:posterPaths[i%posterPaths.length],poster_fallback:null,
  ratings:[{source:i%2?'douban':'tmdb',score_value:(8.1+i*.12).toFixed(1),score_scale:10,vote_count:120000+i*7300}],
  assets:[],in_catalog:true,
}))
movies.push(...movies.map((m,i)=>({...m,movie_id:5000+i,title:m.title+" · 夹具",tmdb_id:String(9000+i)})))
const behavior={
  '1':{watched:true,rating:5,favorite:true,watched_at:'2026-10-12T20:30:00+08:00'},
  '2':{watched:true,rating:4.5,favorite:false,watched_at:'2026-09-21T21:10:00+08:00'},
  '1015':{watched:true,rating:4,favorite:true,watched_at:'2026-08-09T19:00:00+08:00'},
  '1011':{want:true},
  '1005':{watched:true,rating:4.5,favorite:true,watched_at:'2026-07-17T22:00:00+08:00'},
  '1018':{watched:true,rating:5,favorite:true,watched_at:'2026-06-02T18:20:00+08:00'},
  '1007':{want:true},
  '1008':{watched:true,rating:4,favorite:false,watched_at:'2026-05-11T20:00:00+08:00'},
}
const countries=[
  {movie_id:1,countries:[{iso2:'US'},{iso2:'CA'}]},
  {movie_id:2,countries:[{iso2:'US'},{iso2:'GB'}]},
  {movie_id:1015,countries:[{iso2:'US'},{iso2:'DE'}]},
  {movie_id:1011,countries:[{iso2:'US'},{iso2:'GB'}]},
  {movie_id:1005,countries:[{iso2:'US'}]},
  {movie_id:1018,countries:[{iso2:'US'}]},
  {movie_id:1007,countries:[{iso2:'US'}]},
  {movie_id:1008,countries:[{iso2:'US'}]},
  {movie_id:1020,countries:[{iso2:'JP'}]},
  {movie_id:1019,countries:[{iso2:'US'}]},
  {movie_id:1006,countries:[{iso2:'NZ'},{iso2:'US'}]},
  {movie_id:1002,countries:[{iso2:'US'}]},
]
const localBoard=movies.slice(0,10).map((m,i)=>({...m,genres:m.genres.map(g=>g.name_zh),key:'local:'+m.movie_id,rank:i+1,in_catalog:true,reasons:['与你收藏的犯罪片相近','同类型高分影片']}))
const externalBoard=[
  {key:'tmdb:550',movie_id:null,tmdb_id:'550',title:'搏击俱乐部',release_year:1999,tmdb_url:'https://www.themoviedb.org/movie/550',poster:posterPaths[2],in_catalog:false,source_rank:3,tmdb_rating:8.4,vote_count:2300000,tmdb_popularity:88.2,genres:['剧情','悬疑'],reasons:['官方热度榜']},
  {key:'tmdb:680',movie_id:null,tmdb_id:'680',title:'低俗小说',release_year:1994,tmdb_url:'https://www.themoviedb.org/movie/680',poster:posterPaths[3],in_catalog:false,source_rank:6,tmdb_rating:8.5,vote_count:2100000,tmdb_popularity:74.5,genres:['犯罪','剧情'],reasons:['官方热度榜']},
  {key:'tmdb:13',movie_id:null,tmdb_id:'13',title:'阿甘正传',release_year:1994,tmdb_url:'https://www.themoviedb.org/movie/13',poster:posterPaths[4],in_catalog:false,source_rank:8,tmdb_rating:8.5,vote_count:2500000,tmdb_popularity:66.7,genres:['剧情'],reasons:['官方热度榜']},
]
const boardItems=movies.map((m,i)=>({...m,genres:m.genres.map(g=>g.name_zh),key:'local:'+m.movie_id,rank:i+1,in_catalog:true,reasons:['同类型影片']}))
const detailMovie={...movies[0],original_language:'en',credits:[
  {credit_id:'c1',person_id:1,name:'弗兰克·德拉邦特',role_type:'crew',job:'Director',character_name:''},
  ...Array.from({length:8},(_,i)=>({credit_id:'cast'+i,person_id:10+i,name:['蒂姆·罗宾斯','摩根·弗里曼','鲍勃·冈顿','威廉姆·赛德勒','克兰西·布朗','吉尔·贝罗斯','詹姆斯·惠特摩','马克·罗斯顿'][i],role_type:'cast',character_name:['安迪','瑞德','典狱长','海伍德','队长','汤米','布鲁克斯','博格斯'][i],profile_path:null})),
],assets:[{asset_type:'poster',is_primary:true,source:'local',url:'/m9/shaw-poster.jpg'},{asset_type:'backdrop',is_primary:true,source:'local',url:'/m9/shaw-hero.jpg'}],credits_has_more:false,ratings:[{source:'tmdb',score_value:'8.7',score_scale:10,vote_count:2860000},{source:'douban',score_value:'9.7',score_scale:10,vote_count:3100000}],external_ids:[{source:'tmdb',external_id:'278'}],tmdb_heat:{tmdb_id:278,status:'OK',popularity:42.8,observed_at:'2026-11-16T01:00:00+08:00'}}
const recommendationChannels=[
  {id:'daily',title:'每日推荐',description:'根据你的收藏、想看与评分，每日稳定更新。',public:false},
  {id:'popular',title:'TMDB 热门',description:'官方热度榜单，包含已入库与库外影片。',public:true},
  {id:'top_rated',title:'高分经典',description:'按公开评分与票数整理的长期高分片单。',public:true},
]
function catalog(data){return {code:0,data}}
function pageOf(url){const limit=Number(url.searchParams.get('limit')||24),offset=Number(url.searchParams.get('offset')||0);return {items:movies.slice(offset,offset+limit),limit,offset}}
const unhandled=[]
const externalRequests=[]
async function routeApi(route){
  const request=route.request(),url=new URL(request.url()),method=request.method()
  if(!url.pathname.startsWith('/api/'))return route.continue()
  if(url.pathname==='/api/v1/auth/csrf')return route.fulfill({status:200,contentType:'application/json',body:JSON.stringify({code:0,data:{csrf_token:'synthetic-csrf'}})})
  if(url.pathname==='/api/v1/auth/me')return route.fulfill({status:200,contentType:'application/json',body:JSON.stringify({code:0,data:{id:2,username:'deepseek',is_admin:false}})})
  if(url.pathname==='/api/v1/account/profile')return route.fulfill({status:200,contentType:'application/json',body:JSON.stringify({code:0,data:{user_id:2,avatar:'',version:1}})})
  if(url.pathname==='/api/v1/account/movie-notes')return route.fulfill({status:200,contentType:'application/json',body:JSON.stringify({code:0,data:{items:{}}})})
  if(url.pathname==='/api/v1/assistant/status')return route.fulfill({status:200,contentType:'application/json',body:JSON.stringify({code:0,data:{configured:false,enabled:false,models:[]}})})
  if(url.pathname==='/api/catalog/behavior'&&method==='GET')return route.fulfill({status:200,contentType:'application/json',body:JSON.stringify(catalog({state:behavior}))})
  if(url.pathname==='/api/catalog/behavior')return route.fulfill({status:200,contentType:'application/json',body:JSON.stringify(catalog({state:behavior}))})
  if(url.pathname==='/api/catalog/genres')return route.fulfill({status:200,contentType:'application/json',body:JSON.stringify(catalog({items:genres}))})
  if(url.pathname==='/api/catalog/production-countries')return route.fulfill({status:200,contentType:'application/json',body:JSON.stringify(catalog({items:countries}))})
  if(url.pathname==='/api/catalog/recommendation-channels')return route.fulfill({status:200,contentType:'application/json',body:JSON.stringify(catalog({items:recommendationChannels}))})
  if(url.pathname==='/api/catalog/recommendations'){
    const channel=url.searchParams.get('channel')||'daily'
    if(url.searchParams.get('limit')==='6')return route.fulfill({status:200,contentType:'application/json',body:JSON.stringify(catalog({items:boardItems.slice(0,3).map((m,i)=>({...m,movie_id:m.movie_id??undefined,key:m.key||'local:'+m.movie_id,reasons:m.reasons||['同类型高分影片']})),period:'2026-11-16'}))})
    return route.fulfill({status:200,contentType:'application/json',body:JSON.stringify(catalog({items:channel==='daily'?boardItems:boardItems.slice().reverse(),period:'2026-11-16',source_as_of:'2026-11-16T01:00:00+08:00',candidate_count:18,status:'OK',limitations:[],tmdb_connection:{configured:true,enabled:false,last_error:false,max_detail_refresh:6}}))})
  }
  if(url.pathname.startsWith('/api/catalog/movies/')&&method==='GET'){
    const id=Number(url.pathname.split('/').pop())
    if(id===1)return route.fulfill({status:200,contentType:'application/json',body:JSON.stringify(catalog(detailMovie))})
    const m=movies.find(x=>x.movie_id===id)
    if(!m)return route.fulfill({status:404,contentType:'application/json',body:JSON.stringify({code:'NOT_FOUND'})})
    return route.fulfill({status:200,contentType:'application/json',body:JSON.stringify(catalog({...m,original_language:'en',credits:[],credits_has_more:false,overview:'离线合成测试数据。',ratings:[],external_ids:[],tmdb_heat:null}))})
  }
  if(url.pathname==='/api/catalog/movies')return route.fulfill({status:200,contentType:'application/json',body:JSON.stringify(catalog(pageOf(url)))})
  if(url.pathname==='/api/catalog/recommendation-clicks'||url.pathname==='/api/catalog/recommendation-impressions')return route.fulfill({status:200,contentType:'application/json',body:JSON.stringify(catalog({ok:true}))})
  unhandled.push(method+' '+url.pathname+url.search)
  return route.fulfill({status:200,contentType:'application/json',body:JSON.stringify(catalog({items:[],state:{}}))})
}
async function waitForServer(base){
  for(let i=0;i<80;i++){
    try{const r=await fetch(base+'/movies',{signal:AbortSignal.timeout(800)});if(r.ok)return}catch{}
    await new Promise(resolve=>setTimeout(resolve,500))
  }
  throw new Error('Vite server did not start')
}
const server=spawn(process.execPath,[path.join(root,'node_modules/vite/bin/vite.js'),'--host','127.0.0.1','--port','16181','--strictPort'],{cwd:root,stdio:'ignore',env:{...process.env,DO_NOT_TRACK:'1'}})
const base='http://127.0.0.1:16181'
let browser,context
const browserTemp=path.join(taskDir,'.browser-tmp')
await fs.mkdir(browserTemp,{recursive:true})
process.env.TMP=browserTemp
process.env.TEMP=browserTemp
const audits=[]
try{
 await waitForServer(base);browser=await chromium.launch({channel:'chrome',headless:true,args:['--no-proxy-server']});context=await browser.newContext({viewport:{width:1440,height:900}})
 await context.addInitScript(()=>localStorage.setItem('mi2_nav_expanded','false'))
 await context.route('**/*',route=>{const u=new URL(route.request().url());if(['127.0.0.1','localhost'].includes(u.hostname))return route.continue();externalRequests.push(u.href);return route.abort()});await context.route('**/api/**',routeApi)
 const page=await context.newPage(),errors=[];page.on('pageerror',e=>errors.push(e.message))
 for(const [route,main,grid,card] of [['/movies','.page','.grid','.film'],['/recommendations','.recommend-main','.poster-grid','.movie-card']]){
  await page.goto(base+route);await page.waitForSelector(card)
  for(const width of [2560,1920,1440,1280,1024,768,390]){
   await page.setViewportSize({width,height:900})
   for(const expanded of [false,true]){
    const toggle=page.locator('.sidebar-toggle');if((await toggle.getAttribute('aria-expanded'))!==String(expanded))await toggle.click();await page.waitForTimeout(350)
    const a=await page.evaluate(({main,grid,card})=>{const p=document.querySelector(main),g=document.querySelector(grid),r=p.getBoundingClientRect(),q=g.getBoundingClientRect(),s=getComputedStyle(p);return {width:innerWidth,left:r.left,right:r.right,gridRight:q.right,expected:r.right-parseFloat(s.paddingRight),columns:getComputedStyle(g).gridTemplateColumns.split(' ').length,cards:g.querySelectorAll(card).length,overflow:document.documentElement.scrollWidth>innerWidth+1}},{main,grid,card})
    assert.equal(a.overflow,false,JSON.stringify({route,expanded,...a}));assert.ok(Math.abs(a.right-width)<2,JSON.stringify(a));assert.ok(Math.abs(a.gridRight-a.expected)<2,JSON.stringify(a));assert.equal(a.cards,24);if(width>=1400)assert.equal(a.cards%a.columns,0,'wide rows must balance '+JSON.stringify(a));audits.push({route,expanded,...a})
    if((width===2560&&expanded)||(width===390&&!expanded))await page.screenshot({path:path.join(shotDir,`balanced-${route.slice(1)}-${width}.png`)})
   }
  }
 }
 await page.goto(base+'/movies');await page.waitForSelector('.film');await page.setViewportSize({width:1024,height:900});await page.locator('.viewtoggle button').filter({hasText:'列表'}).click();await page.waitForSelector('.grid.listmode');assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth+1),false)
 assert.deepEqual(errors,[]);assert.deepEqual(unhandled,[]);assert.deepEqual(externalRequests,[]);await fs.writeFile(path.join(outDir,'balanced-responsive.json'),JSON.stringify({passed:true,audits,listMode:true,errors,unhandled,externalRequests},null,2));console.log('PASS: both routes; 28 live viewport/sidebar combinations; full width and balanced 24-card wide rows; list mode retained; mocked API only')
}finally{if(context)await context.close();if(browser)await browser.close();server.kill();await fs.rm(browserTemp,{recursive:true,force:true}).catch(()=>{})}
