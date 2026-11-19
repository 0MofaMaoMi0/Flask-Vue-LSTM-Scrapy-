import {chromium} from '@playwright/test'
import fs from 'node:fs'
import path from 'node:path'
import crypto from 'node:crypto'
import {execFileSync} from 'node:child_process'

const out=process.argv[2], control=process.argv[3], expectedSource=process.argv[4]||'synthetic', base='http://127.0.0.1:5174'
if(!out||!control||/^[cC]:/.test(out)||/^[cC]:/.test(control))throw Error('Explicit non-C paths required')
fs.mkdirSync(out,{recursive:true})
const profile=path.join(control,'m6-implementation','browser-profile')
fs.mkdirSync(profile,{recursive:true})
const context=await chromium.launchPersistentContext(profile,{
 executablePath:'C:/Program Files/Google/Chrome/Application/chrome.exe',headless:true,
 env:{...process.env,TEMP:path.join(control,'m6-implementation'),TMP:path.join(control,'m6-implementation')},
 viewport:{width:1440,height:1000},args:['--disk-cache-dir='+path.join(profile,'cache'),'--disable-breakpad','--disable-crash-reporter']
})
const page=await context.newPage(),checks=[],errors=[]
page.on('pageerror',error=>errors.push(error.name))
const check=(name,passed)=>{checks.push({name,passed:!!passed});if(!passed)throw Error(name)}
const username='m6web_'+Date.now().toString(36)+'_'+crypto.randomBytes(3).toString('hex')
const password=crypto.randomBytes(24).toString('base64url')
const ssh=['-F','none','-i',path.join(control,'private/m6_ed25519'),'-o','BatchMode=yes','-o','StrictHostKeyChecking=yes','-o','HostKeyAlias=192.168.147.131','-o','UserKnownHostsFile='+path.join(control,'private/known_hosts'),'zhang@192.168.147.50']
try{
 await context.clearCookies()
 await page.goto(base+'/movies');await page.waitForURL('**/login')
 check('anonymous_catalog_redirect',new URL(page.url()).pathname==='/login')
 await page.goto(base+'/register')
 await page.locator('#registerName').fill(username)
 await page.locator('#registerPassword').fill(password)
 await page.locator('#confirmPassword').fill(password)
 await page.locator('button.submit').click()
 await page.locator('#registerMessage').filter({hasText:'注册成功'}).waitFor()
 check('vue_real_mysql_registration',true)
 await page.goto(base+'/login')
 await page.locator('#loginName').fill(username)
 await page.locator('#loginPassword').fill(password)
 await page.locator('button.submit').click();await page.waitForURL('**/home')
 check('vue_real_mysql_login',true)
 await page.getByRole('link',{name:'进入电影目录'}).click()
 await page.locator('.movie').first().waitFor()
 check('vue_mysql_catalog_two_films',await page.locator('.movie').count()===2)
 check('formal_mode_no_demo_identity_selector',await page.getByText('合成演示用户',{exact:true}).count()===0)
 const sourceInfo=await context.request.get(base+'/api/catalog/demo-info').then(r=>r.json())
 check('expected_data_source',sourceInfo.source===expectedSource)
 if(expectedSource==='approved_static_copy')check('tmdb_attribution',await page.getByAltText('TMDB',{exact:true}).count()===1)
 check('no_false_database_disclaimer',!(await page.locator('.disclosure').innerText()).includes('真实数据库未接入'))
 const targetTitle=await page.locator('.movie h3').first().innerText()
 await page.getByLabel('电影名称',{exact:true}).fill(targetTitle)
 await page.getByRole('button',{name:'查找电影'}).click()
 await page.waitForFunction(()=>document.querySelectorAll('.movie').length===1)
 check('vue_mysql_search',await page.locator('.movie h3').innerText()===targetTitle)
 await page.locator('.movie').first().click();await page.locator('.detail h2').waitFor()
 await page.getByRole('button',{name:'收藏',exact:true}).click()
 await page.getByRole('button',{name:'撤销收藏',exact:true}).waitFor()
 check('vue_mysql_favorite_write',true)
 await page.reload();await page.getByRole('button',{name:'撤销收藏',exact:true}).waitFor()
 check('favorite_survives_page_reload',true)
 await page.getByLabel('我的评分').selectOption('4.5')
 await page.getByRole('button',{name:'评分',exact:true}).click()
 await page.getByText('当前评分 4.5 / 5',{exact:true}).waitFor()
 check('vue_mysql_rating',true)
 await page.waitForTimeout(800)
 await page.screenshot({path:path.join(out,'m6-catalog-desktop.png'),fullPage:true,animations:'disabled'})
 await page.evaluate(()=>{window.__m6DbStatuses=[];let pending=false;window.__m6DbTimer=setInterval(async()=>{if(pending)return;pending=true;try{const r=await fetch('/api/v1/ready');window.__m6DbStatuses.push(r.status)}catch{window.__m6DbStatuses.push(0)}finally{pending=false}},50)})
 execFileSync('C:/Windows/System32/OpenSSH/ssh.exe',[...ssh,'sudo -n /usr/local/sbin/mi2-m6-operations restart-mysql'],{windowsHide:true,timeout:30000,stdio:'pipe'})
 const databaseStatuses=await page.evaluate(()=>{clearInterval(window.__m6DbTimer);return window.__m6DbStatuses})
 check('real_database_unavailable_reported',databaseStatuses.includes(503))
 let databaseReady=false
 for(let attempt=0;attempt<40;attempt++){try{if((await context.request.get(base+'/api/v1/ready')).status()===200){databaseReady=true;break}}catch{}await page.waitForTimeout(200)}
 check('database_recovers_without_app_restart',databaseReady)
 await page.reload();await page.getByRole('button',{name:'撤销收藏',exact:true}).waitFor()
 check('session_and_behavior_survive_database_restart',true)
 execFileSync('C:/Windows/System32/OpenSSH/ssh.exe',[...ssh,'sudo -n /usr/local/sbin/mi2-m6-operations restart-app'],{windowsHide:true,timeout:30000,stdio:'pipe'})
 let ready=false
 for(let attempt=0;attempt<40;attempt++){
  try{const r=await context.request.get(base+'/api/v1/ready');if(r.status()===200){ready=true;break}}catch{}
  await page.waitForTimeout(200)
 }
 check('database_readiness_after_service_restart',ready)
 await page.reload();await page.getByRole('button',{name:'撤销收藏',exact:true}).waitFor()
 check('session_and_behavior_survive_service_restart',true)
 await page.getByText('当前评分 4.5 / 5',{exact:true}).waitFor()
 check('rating_survives_service_restart',true)
 await page.setViewportSize({width:390,height:844})
 await page.waitForTimeout(800)
 await page.screenshot({path:path.join(out,'m6-catalog-mobile.png'),fullPage:true,animations:'disabled'})
 check('catalog_mobile_no_horizontal_overflow',await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth))
 check('no_browser_javascript_errors',errors.length===0)
 fs.writeFileSync(path.join(out,'browser-results.json'),JSON.stringify({all_pass:true,checks},null,2))
 console.log(JSON.stringify({all_pass:true,checks}))
}catch(error){
 fs.writeFileSync(path.join(out,'browser-results.json'),JSON.stringify({all_pass:false,checks,error:error.name,failed:error.message.split('\n')[0]},null,2))
 throw error
}finally{await context.close()}
