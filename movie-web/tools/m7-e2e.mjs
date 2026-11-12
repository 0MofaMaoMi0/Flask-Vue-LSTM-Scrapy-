import {chromium} from '@playwright/test'
import fs from 'node:fs'
import path from 'node:path'
const out=process.argv[2],control=process.argv[3],base='http://127.0.0.1:5174'
fs.mkdirSync(out,{recursive:true})
const context=await chromium.launchPersistentContext(path.join(control,'m7-browser'),{executablePath:'C:/Program Files/Google/Chrome/Application/chrome.exe',headless:true,env:{...process.env,TEMP:control,TMP:control},viewport:{width:1440,height:1000},args:['--disable-breakpad','--disable-crash-reporter']})
const page=await context.newPage(),checks=[]
const check=(name,value)=>{checks.push({name,passed:!!value});if(!value)throw Error(name)}
try{
 await context.clearCookies();await page.goto(base+'/register')
 const name='m7web_'+Date.now().toString(36),password='Synthetic_'+crypto.randomUUID()
 await page.locator('#registerName').fill(name);await page.locator('#registerPassword').fill(password);await page.locator('#confirmPassword').fill(password);await page.locator('button.submit').click();await page.locator('#registerMessage').filter({hasText:'注册成功'}).waitFor()
 await page.goto(base+'/login');await page.locator('#loginName').fill(name);await page.locator('#loginPassword').fill(password);await page.locator('button.submit').click();await page.waitForURL('**/home')
 await page.goto(base+'/movies');await page.locator('.movie').first().waitFor()
 await page.getByRole('button',{name:'打开电影助手',exact:true}).click();check('assistant_entry',await page.getByRole('heading',{name:'电影助手',exact:true}).isVisible())
 await page.getByLabel('你想看什么？',{exact:true}).fill('推荐电影');await page.locator('.assistant').getByRole('button',{name:'查找电影',exact:true}).click();await page.locator('.assistant li').first().waitFor();check('real_mysql_recommendation',await page.locator('.assistant li').count()>0)
 check('local_cost_disclosure',(await page.locator('.assistant').innerText()).includes('本地结果'))
 await page.locator('.assistant li button').first().click();await page.getByText('AI不可用，已降级',{exact:false}).waitFor();check('explicit_ai_fallback',true)
 await page.getByRole('button',{name:'开始新对话',exact:true}).click();await page.getByLabel('你想看什么？',{exact:true}).fill('2099年之后的电影');await page.locator('.assistant').getByRole('button',{name:'查找电影',exact:true}).click();await page.getByText('当前目录没有符合条件的影片。',{exact:false}).waitFor();check('honest_empty',await page.locator('.assistant li').count()===0)
 await page.screenshot({path:path.join(out,'assistant-desktop.png'),fullPage:true})
 await page.setViewportSize({width:390,height:844});await page.screenshot({path:path.join(out,'assistant-mobile.png'),fullPage:true});check('mobile_no_overflow',await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth))
}finally{
 fs.writeFileSync(path.join(out,'browser-results.json'),JSON.stringify({all_pass:checks.every(c=>c.passed),checks},null,2));await context.close()
}
console.log(JSON.stringify({all_pass:true,checks:checks.length}))
