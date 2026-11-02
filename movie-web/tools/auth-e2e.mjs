import {fileURLToPath} from 'node:url';
import {chromium} from '@playwright/test';import fs from 'node:fs';import path from 'node:path';import {randomUUID} from 'node:crypto';
process.chdir(path.resolve(path.dirname(fileURLToPath(import.meta.url)),'..'));
if(!process.env.MI2_E2E_BASE_URL)throw Error('Run tools/run-auth-e2e.mjs to guarantee isolated test database');
const base=process.env.MI2_E2E_BASE_URL;
const out=path.resolve('../../项目工作日志/04-巡检与评估报告/2026-11-02-MovieInsight2-Phase1B');fs.mkdirSync(out,{recursive:true});
const context=await chromium.launchPersistentContext(path.resolve('../.local-cache/auth-browser'),{executablePath:'C:/Program Files/Google/Chrome/Application/chrome.exe',headless:true,viewport:{width:1440,height:900},args:['--disk-cache-dir='+path.resolve('../.local-cache/auth-browser-cache')],env:{...process.env,TEMP:path.resolve('../.local-cache/tmp'),TMP:path.resolve('../.local-cache/tmp')}});
await context.clearCookies();
const page=await context.newPage(),results=[],responses=[];page.on('response',r=>{if(r.url().includes('/api/v1/'))responses.push({endpoint:new URL(r.url()).pathname,status:r.status()})});let old=0;page.on('request',r=>{if(r.url().includes('/api/user/'))old++});const check=(name,ok)=>{results.push({name,ok});if(!ok)throw Error(name)};
try{
 await page.goto(base+'/');await page.waitForSelector('.static-welcome');check('static Welcome back',await page.locator('.static-welcome').innerText()==='Welcome back.');
 await page.waitForFunction(()=>[...document.querySelectorAll('.poster img')].every(x=>x.complete&&x.naturalWidth>0));check('32 posters decode',await page.locator('.poster img').evaluateAll(xs=>new Set(xs.map(x=>x.getAttribute('src'))).size)===32);
 await page.goto(base+'/home');await page.waitForURL('**/login');check('guard rejects anonymous',true);
 await page.goto(base+'/register');await page.waitForSelector('#registerForm');check('no real name collection',await page.locator('#realName').count()===0);
 const name='browser_'+randomUUID().slice(0,12),password='isolated-browser-test-password';
 await page.locator('#registerName').fill(name);await page.locator('#registerPassword').fill(password);await page.locator('#confirmPassword').fill(password);
 let [response]=await Promise.all([page.waitForResponse(r=>r.url().endsWith('/api/v1/auth/register')),page.locator('#registerForm button.submit').click()]);check('register 201',response.status()===201);await page.waitForFunction(()=>document.querySelector('#registerMessage')?.textContent?.includes('注册成功'));
 await page.getByRole('link',{name:'去登录'}).click();await page.locator('#loginName').fill(name);await page.locator('#loginPassword').fill(password);
 [response]=await Promise.all([page.waitForResponse(r=>r.url().endsWith('/api/v1/auth/login')),page.locator('#loginForm button.submit').click()]);check('login 200',response.status()===200);await page.waitForURL('**/home');
 await page.reload();await page.waitForSelector('.home-placeholder');check('refresh identity',(await page.locator('.home-placeholder').innerText()).includes(name));
 const tab=await context.newPage();await tab.goto(base+'/home');await tab.waitForSelector('.home-placeholder');check('new tab identity',(await tab.locator('.home-placeholder').innerText()).includes(name));await tab.close();
 check('no sensitive persistent storage',await page.evaluate(()=>[...Object.keys(localStorage),...Object.keys(sessionStorage)].every(k=>k==='movieinsight-username')));
 const sid=(await context.cookies()).find(c=>c.name==='mi2_sid');check('HttpOnly SameSite cookie',!!sid?.httpOnly&&sid.sameSite==='Lax');
 await page.route('**/api/v1/auth/logout',r=>r.abort(),{times:1});
 await page.getByRole('button',{name:'退出登录'}).click();await page.waitForFunction(()=>document.querySelector('[role=alert]')?.textContent?.includes('退出未确认'));check('failed logout reports unconfirmed revocation',true);
 await page.getByRole('button',{name:'退出登录'}).click();await page.waitForURL('**/login');check('logout me 401',await page.evaluate(async()=>(await fetch('/api/v1/auth/me')).status)===401);
 const replay=await context.request.get(base+'/api/v1/auth/me',{headers:{Cookie:`mi2_sid=${sid.value}`}});check('SID replay rejected',replay.status()===401);
 for(const viewport of [{width:1440,height:900},{width:390,height:844}]){await page.setViewportSize(viewport);for(const route of ['/','/login','/register']){await page.goto(base+route);await page.waitForTimeout(450);check(`${viewport.width} no overflow ${route}`,await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));await page.screenshot({path:path.join(out,`${viewport.width}-${route.slice(1)||'welcome'}.png`),fullPage:true})}}
 await page.emulateMedia({reducedMotion:'reduce'});check('reduced motion',await page.locator('.track').first().evaluate(e=>getComputedStyle(e).animationName)==='none');check('no legacy auth',old===0);
}catch(e){results.push({name:'failure',ok:false,error:e.message,alerts:await page.locator('[role=alert]').allTextContents(),responses})}finally{fs.writeFileSync(path.join(out,'browser-results.json'),JSON.stringify(results,null,2));await context.close();console.log(JSON.stringify(results));if(results.some(x=>!x.ok))process.exitCode=1}
