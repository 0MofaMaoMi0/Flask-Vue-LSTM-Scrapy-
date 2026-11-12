import {chromium} from '@playwright/test'
import fs from 'node:fs'
import path from 'node:path'
const out=process.argv[2],control=process.argv[3],base='http://127.0.0.1:5174'
fs.mkdirSync(out,{recursive:true})
const context=await chromium.launchPersistentContext(path.join(control,'m7-v2-browser'),{executablePath:'C:/Program Files/Google/Chrome/Application/chrome.exe',headless:true,env:{...process.env,TEMP:control,TMP:control},viewport:{width:1440,height:1000},args:['--disable-breakpad','--disable-crash-reporter']})
const page=await context.newPage(),checks=[],errors=[];let generationRequests=0,verificationRequests=0
page.on('pageerror',e=>errors.push(e.name));page.on('request',r=>{if(/\/assistant\/(query|test|explain)$/.test(r.url()))generationRequests++;if(r.url().endsWith('/assistant/verify'))verificationRequests++})
const check=(name,value)=>{checks.push({name,passed:!!value});if(!value)throw Error(name)}
try{
 await context.clearCookies();await page.goto(base+'/register');const name='byokweb_'+Date.now().toString(36),password='Synthetic_'+crypto.randomUUID()
 await page.locator('#registerName').fill(name);await page.locator('#registerPassword').fill(password);await page.locator('#confirmPassword').fill(password);await page.locator('button.submit').click();await page.locator('#registerMessage').filter({hasText:'注册成功'}).waitFor()
 await page.goto(base+'/login');await page.locator('#loginName').fill(name);await page.locator('#loginPassword').fill(password);await page.locator('button.submit').click();await page.waitForURL('**/home');await page.goto(base+'/movies');await page.locator('.movie').first().waitFor()
 await page.getByRole('button',{name:'打开电影助手',exact:true}).click();check('local_before_byok',(await page.locator('.assistant').innerText()).includes('未连接个人模型'))
 await page.locator('.model-settings summary').click();await page.locator('#byok-key').fill('SYNTHETIC_BROWSER_KEY_NOT_REAL');await page.locator('.check').filter({hasText:'我同意向所选服务商'}).locator('input').check();await page.getByRole('button',{name:'保存并启用个人模型',exact:true}).click();await page.getByText('已短期连接。',{exact:false}).waitFor()
 check('save_does_not_dispatch_model',generationRequests===0&&verificationRequests===0)
 check('key_input_cleared',await page.locator('#byok-key').inputValue()==='')
 check('no_key_in_browser_storage',await page.evaluate(()=>!JSON.stringify([localStorage,sessionStorage,document.cookie]).includes('SYNTHETIC_BROWSER_KEY_NOT_REAL')))
 const config=await context.request.get(base+'/api/v1/assistant/status').then(r=>r.json());check('user_model_enabled',config.data.enabled&&config.data.model==='deepseek-flash');check('unlimited_call_policy',config.data.daily_calls===null&&config.data.daily_calls_remaining===null);check('no_key_in_status',!JSON.stringify(config).includes('SYNTHETIC_BROWSER_KEY_NOT_REAL'))
 check('paid_test_explicit_consent',await page.getByRole('button',{name:'发送模型测试（可能计费）',exact:true}).isDisabled())
 await page.getByRole('button',{name:'断开连接并移除 Key',exact:true}).click();await page.getByText('已断开并移除短期托管密钥',{exact:false}).waitFor();check('disconnect_clears_server_key',!(await context.request.get(base+'/api/v1/assistant/status').then(r=>r.json())).data.configured)
 await page.getByLabel('你想看什么？',{exact:true}).fill('推荐电影');await page.locator('.assistant').getByRole('button',{name:'查找电影',exact:true}).click();await page.locator('.assistant li').first().waitFor();check('local_stream_fallback',await page.locator('.assistant li').count()>0)
 await page.screenshot({path:path.join(out,'byok-desktop.png'),fullPage:true});await page.setViewportSize({width:390,height:844});await page.screenshot({path:path.join(out,'byok-mobile.png'),fullPage:true});check('mobile_no_overflow',await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));check('no_javascript_errors',errors.length===0)
}finally{fs.writeFileSync(path.join(out,'browser-results.json'),JSON.stringify({all_pass:checks.every(c=>c.passed),checks,real_provider_calls:false,scope:'real Vue/Flask/MySQL configuration + local SSE; no model test or metadata request sent'},null,2));await context.close()}
console.log(JSON.stringify({all_pass:true,checks:checks.length}))
