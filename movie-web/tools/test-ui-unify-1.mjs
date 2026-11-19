import fs from 'node:fs/promises'
import assert from 'node:assert/strict'
import {spawn} from 'node:child_process'
import path from 'node:path'
import {fileURLToPath} from 'node:url'
import {chromium} from 'playwright'

const here=path.dirname(fileURLToPath(import.meta.url))
const root=path.resolve(here,'..')
const taskDir=path.resolve(root,'../../项目工作日志/06-开发与验证/全站Unslop统一-20261116/员工1-电影页面')
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
const boardItems=[...localBoard.slice(0,2),externalBoard[0],...localBoard.slice(2,5),externalBoard[1],...localBoard.slice(5,8),externalBoard[2],...localBoard.slice(8,10)]
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
const server=spawn(process.execPath,[path.join(root,'node_modules/vite/bin/vite.js'),'--host','127.0.0.1','--port','16171','--strictPort'],{cwd:root,stdio:'ignore',env:{...process.env,DO_NOT_TRACK:'1'}})
const base='http://127.0.0.1:16171'
let browser,context
const browserTemp=path.join(taskDir,'.browser-tmp')
await fs.mkdir(browserTemp,{recursive:true})
process.env.TMP=browserTemp
process.env.TEMP=browserTemp
const audits=[]
const PASS_MESSAGE='PASS: Library/Recommendation/MovieDetail/MyMovies rendered with isolated API mocks; cards, detail, globe, insights and seen table present; no external requests; screenshots saved.'
try{
  await waitForServer(base)
  browser=await chromium.launch({channel:'chrome',headless:true,timeout:30000,args:['--no-sandbox','--disable-gpu','--use-gl=angle','--use-angle=swiftshader','--no-proxy-server']})
  context=await browser.newContext({viewport:{width:1440,height:900},deviceScaleFactor:1})
  await context.addInitScript(()=>{try{localStorage.setItem('mi2_nav_expanded','false')}catch{}})
  await context.route('**/*',async route=>{
    const u=new URL(route.request().url())
    if(u.hostname==='127.0.0.1'||u.hostname==='localhost')return route.continue()
    externalRequests.push(route.request().url())
    return route.abort()
  })
  await context.route('**/api/**',routeApi)
  const page=await context.newPage()
  const pageErrors=[]
  page.on('pageerror',e=>pageErrors.push(e.message))
  const record=async(name,selector)=>{
    await page.waitForSelector(selector,{timeout:20000})
    await page.waitForTimeout(500)
    const audit=await page.evaluate(()=>{
      const h1=document.querySelector('h1'),main=document.querySelector('main'),body=getComputedStyle(document.body)
      const pick=el=>el?getComputedStyle(el):null
      const h1s=pick(h1),mainStyle=pick(main)
      const cards=[...document.querySelectorAll('.film,.movie-card,.my-card,.section')]
      const poster=document.querySelector('.film-poster,.board-poster,.my-poster')
      const boxes={}
      for(const sel of ['.page','.head h1','.searchwrap','.toolbar','.recommend-main','.channel-nav','.filters','.poster-grid','.detail-content','.hero','.hero-grid','.poster','.content','.my-page','.my-head','.globe-workspace','.g-stage','.g-rank','.g-detail','.insight-cards','.insight-columns','.trend-chart','.records-table']){
        const el=document.querySelector(sel)
        if(!el)continue
        const r=el.getBoundingClientRect()
        boxes[sel]={x:Math.round(r.x),y:Math.round(r.y),w:Math.round(r.width),h:Math.round(r.height)}
      }
      const sidebar=document.querySelector('.sidebar'),heroPoster=document.querySelector('.poster img')
      const sidebarStyle=pick(sidebar),posterStyle=pick(heroPoster),posterRect=heroPoster?.getBoundingClientRect()
      const posterAncestors=[]
      for(let node=heroPoster?.parentElement;node&&node!==document.documentElement&&posterAncestors.length<6;node=node.parentElement){
        const cs=pick(node),r=node.getBoundingClientRect()
        posterAncestors.push({tag:node.tagName,cls:node.className,display:cs?.display,visibility:cs?.visibility,opacity:cs?.opacity,overflow:cs?.overflow,clipPath:cs?.clipPath,rect:{x:Math.round(r.x),y:Math.round(r.y),w:Math.round(r.width),h:Math.round(r.height)}})
      }
      const posterCenter=posterRect?{x:posterRect.x+posterRect.width/2,y:posterRect.y+posterRect.height/2}:null
      const topNode=posterCenter?document.elementFromPoint(posterCenter.x,posterCenter.y):null
      return {
        posterCount:document.querySelectorAll('.film-poster,.board-poster,.my-poster').length,
        posterRadius:poster?getComputedStyle(poster).borderRadius:'',
        objectMarks:(document.body.innerText.match(/\[object Object\]/g)||[]).length,
        genres:[...document.querySelectorAll('.movie-card .genre')].map(el=>el.textContent?.trim()),
        externalLabels:[...document.querySelectorAll('.external')].map(el=>el.textContent?.trim()),
        heroPoster:heroPoster?{src:heroPoster.getAttribute('src'),currentSrc:heroPoster.currentSrc,complete:heroPoster.complete,naturalWidth:heroPoster.naturalWidth,naturalHeight:heroPoster.naturalHeight,rect:posterRect?{x:Math.round(posterRect.x),y:Math.round(posterRect.y),w:Math.round(posterRect.width),h:Math.round(posterRect.height)}:null,opacity:posterStyle?.opacity,visibility:posterStyle?.visibility,display:posterStyle?.display,objectFit:posterStyle?.objectFit,background:pick(document.querySelector('.poster'))?.backgroundColor,ancestors:posterAncestors,topAtCenter:topNode?{tag:topNode.tagName,cls:topNode.className}:null}:null,
        sidebar:sidebar?{width:Math.round(sidebar.getBoundingClientRect().width),height:Math.round(sidebar.getBoundingClientRect().height),position:sidebarStyle?.position,backgroundColor:sidebarStyle?.backgroundColor,backgroundImage:sidebarStyle?.backgroundImage,borderRightColor:sidebarStyle?.borderRightColor}:null,
        globeCanvas:(()=>{const c=document.querySelector('.g-stage canvas');if(!c)return null;const r=c.getBoundingClientRect();return {w:Math.round(r.width),h:Math.round(r.height)}})(),
        boxes,
        url:location.pathname+location.search+location.hash,
        title:document.title,
        h1:h1?.textContent?.trim()||'',
        bodyBackground:body.backgroundColor,bodyColor:body.color,bodyFont:body.fontFamily,
        h1Font:h1s?.fontFamily||'',h1Size:h1s?.fontSize||'',h1Weight:h1s?.fontWeight||'',
        mainBackground:mainStyle?.backgroundColor||'',
        cardCount:cards.length,
        cardRadius:cards[0]?getComputedStyle(cards[0]).borderRadius:'',
        verticalOverflow:document.documentElement.scrollHeight>innerHeight,
        horizontalOverflow:document.documentElement.scrollWidth>innerWidth+1,
        scrollWidth:document.documentElement.scrollWidth,innerWidth,
      }
    })
    audits.push({name,...audit})
    return audit
  }
  const shot=async(name)=>{
    await page.evaluate(()=>window.scrollTo(0,0))
    await page.screenshot({path:path.join(shotDir,`${phase}-${name}.png`),fullPage:false})
  }
  const open=async(url,selector,name,width=1440,height=900)=>{
    await page.setViewportSize({width,height})
    await page.goto(base+url,{waitUntil:'domcontentloaded',timeout:20000})
    return record(name,selector)
  }
  const prepareDetailPoster=async()=>{
    try{
      await page.waitForFunction(()=>{
        const img=document.querySelector('.poster img')
        if(!img)return false
        const r=img.getBoundingClientRect(),cs=getComputedStyle(img)
        return img.complete&&img.naturalWidth>0&&r.width>0&&r.height>0&&cs.opacity!=='0'&&cs.visibility!=='hidden'&&cs.display!=='none'
      },undefined,{timeout:8000})
    }catch{console.log('DETAIL: image-ready wait timed out; collecting measured state')}
    await page.evaluate(async()=>{
      const img=document.querySelector('.poster img')
      if(img?.decode)await img.decode().catch(()=>{})
      await new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)))
    })
    await page.waitForTimeout(250)
    return page.evaluate(()=>{
      const img=document.querySelector('.poster img'),poster=document.querySelector('.poster')
      if(!img)return null
      const r=img.getBoundingClientRect(),cs=getComputedStyle(img)
      const ancestors=[]
      for(let node=img.parentElement;node&&node!==document.documentElement&&ancestors.length<6;node=node.parentElement){
        const s=getComputedStyle(node),b=node.getBoundingClientRect()
        ancestors.push({tag:node.tagName,cls:node.className,display:s.display,visibility:s.visibility,opacity:s.opacity,overflow:s.overflow,clipPath:s.clipPath,rect:{x:Math.round(b.x),y:Math.round(b.y),w:Math.round(b.width),h:Math.round(b.height)}})
      }
      const center={x:r.x+r.width/2,y:r.y+r.height/2},top=document.elementFromPoint(center.x,center.y)
      return {src:img.getAttribute('src'),currentSrc:img.currentSrc,complete:img.complete,naturalWidth:img.naturalWidth,naturalHeight:img.naturalHeight,rect:{x:Math.round(r.x),y:Math.round(r.y),w:Math.round(r.width),h:Math.round(r.height)},opacity:cs.opacity,visibility:cs.visibility,display:cs.display,objectFit:cs.objectFit,posterBackground:poster?getComputedStyle(poster).backgroundColor:null,ancestors,topAtCenter:top?{tag:top.tagName,cls:top.className}:null}
    })
  }
  const visit=async(url,selector,name,width=1440,height=900)=>{
    const audit=await open(url,selector,name,width,height)
    await shot(name)
    return audit
  }

  if(review&&detailOnly){
    const detailReview=await open('/movies?from=library#movie/1','#movie-title','detail-1440-review',1440,900)
    const posterVisibility=await prepareDetailPoster()
    audits[audits.length-1].posterVisibility=posterVisibility
    detailReview.posterVisibility=posterVisibility
    await page.screenshot({path:path.join(shotDir,'detail-review-visible.png'),fullPage:false})
    assert.ok(posterVisibility&&posterVisibility.complete&&posterVisibility.naturalWidth>0,`poster decode ${JSON.stringify(posterVisibility)}`)
    assert.ok(posterVisibility.rect.w>0&&posterVisibility.rect.h>0,`poster rect ${JSON.stringify(posterVisibility.rect)}`)
    assert.ok(posterVisibility.opacity!=='0'&&posterVisibility.visibility!=='hidden'&&posterVisibility.display!=='none',`poster style ${JSON.stringify(posterVisibility)}`)
    assert.ok(posterVisibility.topAtCenter?.tag==='IMG',`poster covered by ${JSON.stringify(posterVisibility.topAtCenter)}`)
    assert.ok(posterVisibility.ancestors.every(a=>a.display!=='none'&&a.visibility!=='hidden'&&a.opacity!=='0'),`hidden ancestor ${JSON.stringify(posterVisibility.ancestors)}`)
    assert.equal(detailReview.horizontalOverflow,false,`detail overflow ${detailReview.scrollWidth}/${detailReview.innerWidth}`)
    assert.equal(pageErrors.length,0,pageErrors.join('\n'))
    assert.deepEqual(unhandled,[])
    assert.deepEqual(externalRequests,[])
    console.log('DETAIL PASS: poster decoded and painted '+JSON.stringify(posterVisibility.rect)+'; screenshot detail-review-visible.png')
  }else if(review){
    const recReview=await visit('/recommendations?channel=daily','.movie-card','recommendations-390-review',390,844)
    const detailReview=await visit('/movies?from=library#movie/1','#movie-title','detail-1440-review',1440,900)
    assert.equal(recReview.objectMarks,0,'recommendation contains [object Object]')
    assert.ok(recReview.genres.length>0&&recReview.genres.every(x=>!x.includes('[object')),`recommendation genres ${JSON.stringify(recReview.genres)}`)
    assert.ok(recReview.externalLabels.length>0&&recReview.externalLabels.every(x=>x==='TMDB'),`external labels ${JSON.stringify(recReview.externalLabels)}`)
    assert.equal(recReview.horizontalOverflow,false,`recommendation overflow ${recReview.scrollWidth}/${recReview.innerWidth}`)
    assert.equal(detailReview.objectMarks,0,'detail contains [object Object]')
    assert.ok(detailReview.heroPoster?.complete&&detailReview.heroPoster.naturalWidth>0,`detail poster ${JSON.stringify(detailReview.heroPoster)}`)
    assert.equal(detailReview.horizontalOverflow,false,`detail overflow ${detailReview.scrollWidth}/${detailReview.innerWidth}`)
    assert.ok(recReview.sidebar?.width===53&&detailReview.sidebar?.width===53,`sidebar widths ${JSON.stringify([recReview.sidebar,detailReview.sidebar])}`)
    await page.locator('.sidebar-toggle').click()
    await page.waitForTimeout(350)
    const expanded=await page.evaluate(()=>{
      const sidebar=document.querySelector('.sidebar'),content=document.querySelector('.detail-content')
      return {sidebarWidth:Math.round(sidebar?.getBoundingClientRect().width||0),contentX:Math.round(content?.getBoundingClientRect().x||0)}
    })
    audits[audits.length-1].sidebarExpanded=expanded
    assert.ok(expanded.sidebarWidth>=279&&expanded.contentX>=279,`expanded sidebar ${JSON.stringify(expanded)}`)
    assert.equal(pageErrors.length,0,pageErrors.join('\n'))
    assert.deepEqual(unhandled,[])
    assert.deepEqual(externalRequests,[])
    console.log('REVIEW PASS: recommendation genres are strings with no [object Object]; detail poster loaded from local asset; both states match the mocked API contract.')
  }else{
    const library=await visit('/movies','.film-poster','library-1440')
    const recommendation=await visit('/recommendations?channel=daily','.movie-card','recommendations-1440')
    const detail=await visit('/movies?from=library#movie/1','#movie-title','detail-1440')
    const globe=await visit('/movies?view=globe#behavior','.globe-workspace','mymovies-globe-1440')
    const insights=await visit('/movies?view=insights#behavior','.insight-workspace','mymovies-insights-1440')
    const seen=await visit('/movies?view=seen#behavior','.records-table','mymovies-seen-1440')
    const libraryNarrow=await visit('/movies','.film-poster','library-390',390,844)
    const recommendationNarrow=await visit('/recommendations?channel=daily','.movie-card','recommendations-390',390,844)
    const detailNarrow=await visit('/movies?from=library#movie/1','#movie-title','detail-390',390,844)
    const globeNarrow=await visit('/movies?view=globe#behavior','.globe-workspace','mymovies-globe-390',390,844)
    const seenNarrow=await visit('/movies?view=seen#behavior','.records-table','mymovies-seen-390',390,844)
    const insightsNarrow=await visit('/movies?view=insights#behavior','.trend-chart','mymovies-insights-390',390,844)

    if(strict){
      assert.equal(await page.locator('.my-state').count(),0)
      for(const a of audits){assert.equal(a.horizontalOverflow,false,`${a.name} has horizontal overflow ${a.scrollWidth}/${a.innerWidth}`)}
      assert.ok(library.cardCount>=12,`library cards ${library.cardCount}`)
      assert.ok(recommendation.cardCount>=12,`recommendation cards ${recommendation.cardCount}`)
      assert.equal(detail.h1,'肖申克的救赎')
      assert.ok(globe&&insights&&seen)
      assert.ok(globe.globeCanvas&&globe.globeCanvas.w>500&&globe.globeCanvas.h>400,`globe canvas ${JSON.stringify(globe.globeCanvas)}`)
      await page.goto(base+'/movies?view=seen#behavior',{waitUntil:'domcontentloaded'})
      await page.waitForSelector('.records-table')
      assert.equal(await page.locator('.records-table tbody tr').count(),6)
      await page.goto(base+'/movies?view=insights#behavior',{waitUntil:'domcontentloaded'})
      await page.waitForSelector('.trend-chart')
      assert.equal(await page.locator('.trend-chart .trend-col').count(),12)
      assert.equal(pageErrors.length,0,pageErrors.join('\n'))
      assert.deepEqual(unhandled,[])
      assert.deepEqual(externalRequests,[])
      console.log(PASS_MESSAGE)
    }else{
      console.log(`CAPTURED ${audits.length} page states at 1440/390; external=${externalRequests.length}; unhandled=${unhandled.length}; pageErrors=${pageErrors.length}`)
    }
  }
  await fs.writeFile(path.join(outDir,detailOnly?'audit-detail-visibility.json':review?'audit-review.json':`audit-${phase}.json`),JSON.stringify({phase,review,detailOnly,audits,pageErrors,unhandled,externalRequests},null,2))
}finally{
  if(context)await context.close()
  if(browser)await browser.close()
  server.kill()
  await fs.rm(browserTemp,{recursive:true,force:true}).catch(()=>{})
}