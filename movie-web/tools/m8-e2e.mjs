import {chromium} from '@playwright/test'
import fs from 'node:fs'
import path from 'node:path'
const out=process.argv[2],control=process.argv[3],base='http://127.0.0.1:5174'
fs.mkdirSync(out,{recursive:true});fs.mkdirSync(control,{recursive:true})
const browser=await chromium.launchPersistentContext(path.join(control,'m8-browser'),{executablePath:'C:/Program Files/Google/Chrome/Application/chrome.exe',headless:true,env:{...process.env,TEMP:control,TMP:control},viewport:{width:1440,height:1000},args:['--disable-breakpad','--disable-crash-reporter']})
const page=await browser.newPage(),checks=[],errors=[];let modelCalls=0
page.on('pageerror',e=>errors.push(e.message));page.on('request',r=>{if(/\/assistant\/(query|verify|test)$/.test(r.url()))modelCalls++})
function check(name,v){checks.push({name,passed:!!v});if(!v)throw Error(name)}
try{
 await browser.clearCookies();await page.goto(base+'/insights');await page.waitForURL('**/login');check('guest_route_guard',true)
 const name='m8web_'+Date.now().toString(36),password='Synthetic_'+crypto.randomUUID()
 await page.goto(base+'/register');await page.locator('#registerName').fill(name);await page.locator('#registerPassword').fill(password);await page.locator('#confirmPassword').fill(password);await page.locator('button.submit').click();await page.locator('#registerMessage').filter({hasText:'注册成功'}).waitFor()
 await page.goto(base+'/login');await page.locator('#loginName').fill(name);await page.locator('#loginPassword').fill(password);await page.locator('button.submit').click();await page.waitForURL('**/home');await page.getByRole('link',{name:'数据洞察'}).click();await page.getByRole('heading',{name:'活跃电影数',exact:true}).waitFor()
 check('real_catalog_two',await page.locator('.notice').innerText().then(t=>t.includes('2部')));check('echarts_rendered',await page.locator('canvas').count()>0);check('accessible_tables',await page.locator('table').count()>0)
 await page.waitForFunction(()=>getComputedStyle(document.querySelector('.insights')).opacity==='1');await page.screenshot({path:path.join(out,'catalog-desktop.png'),fullPage:true})
 await page.getByRole('tab',{name:'个人行为洞察'}).click();await page.getByRole('heading',{name:'收藏影片',exact:true}).waitFor();check('personal_empty_state',(await page.locator('.insights').innerText()).includes('暂无数据'))
 await page.getByRole('tab',{name:'AI用量'}).click();await page.getByRole('heading',{name:'真实账单',exact:true}).waitFor();check('unknown_bill',(await page.locator('.insights').innerText()).includes('未知'));check('no_zero_bill',!(await page.locator('.insights').innerText()).match(/[￥$]0/));await page.waitForFunction(()=>getComputedStyle(document.querySelector('.insights')).opacity==='1');await page.screenshot({path:path.join(out,'usage-empty.png'),fullPage:true})
 await page.getByRole('tab',{name:'推荐评估'}).click();await page.getByRole('heading',{name:'NDCG@10',exact:true}).waitFor();check('insufficient_real_evaluation',(await page.locator('.insights').innerText()).includes('样本不足'));check('no_fake_metric',await page.locator('article strong').allTextContents().then(a=>a.every(t=>t==='N/A')))
 await page.setViewportSize({width:390,height:844});await page.waitForFunction(()=>getComputedStyle(document.querySelector('.insights')).opacity==='1');await page.screenshot({path:path.join(out,'evaluation-mobile.png'),fullPage:true});check('mobile_no_overflow',await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth))
 await page.getByLabel('开始日期（UTC）',{exact:true}).fill('2020-01-01');await page.getByLabel('结束日期（UTC，不含）',{exact:true}).fill('2026-01-01');await page.getByRole('button',{name:'刷新数据',exact:true}).click();await page.getByRole('alert').waitFor();check('invalid_window_visible',(await page.getByRole('alert').innerText()).includes('366'))
 await page.getByLabel('开始日期（UTC）',{exact:true}).fill('');await page.getByLabel('结束日期（UTC，不含）',{exact:true}).fill('');await page.route('**/api/v1/insights/**',r=>r.fulfill({status:503,contentType:'application/json',body:'{"code":"DATA_UNAVAILABLE","data":{"status":"UNAVAILABLE"}}'}));await page.getByRole('button',{name:'刷新数据',exact:true}).click();await page.getByRole('alert').waitFor();check('unavailable_no_fallback',(await page.getByRole('alert').innerText()).includes('暂时不可用')&&await page.locator('article').count()===0);await page.unroute('**/api/v1/insights/**')
 await page.getByRole('button',{name:'重试',exact:true}).click();await page.getByRole('heading',{name:'NDCG@10',exact:true}).waitFor();check('retry_recovers',true)
 check('no_model_calls',modelCalls===0);check('no_browser_errors',errors.length===0)
}finally{fs.writeFileSync(path.join(out,'results.json'),JSON.stringify({all_pass:checks.every(x=>x.passed),checks,errors,modelCalls},null,2));await browser.close()}
console.log(JSON.stringify({all_pass:true,checks:checks.length}))

