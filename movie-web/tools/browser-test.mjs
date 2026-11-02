import {chromium} from '@playwright/test';
import fs from 'node:fs';
const out='../../项目工作日志/06-开发与验证/login-v3.7';fs.mkdirSync(out,{recursive:true});
const browser=await chromium.launch({executablePath:'C:/Program Files/Google/Chrome/Application/chrome.exe',headless:true,args:['--disk-cache-dir='+process.cwd()+'/../.local-cache/chrome']});
const page=await browser.newPage({viewport:{width:1440,height:900}});const results=[];const check=(name,ok)=>{results.push({name,ok});if(!ok)throw Error(name)};
await page.goto('http://127.0.0.1:5173');await page.waitForTimeout(1000);
check('all local posters loaded',await page.locator('img.ready').count()===2*fs.readdirSync('public/posters').filter(name=>name.endsWith('.jpg')).length);check('32 unique posters in seamless copies',await page.locator('.poster img').evaluateAll(imgs=>new Set(imgs.map(img=>img.getAttribute('src'))).size)===32);
await page.screenshot({path:out+'/desktop-welcome.png'});
await page.getByRole('button',{name:'登 录',exact:true}).click();await page.waitForTimeout(800);
await page.locator('button.submit').click();check('empty login',await page.locator('[role=alert]').innerText()==='请输入用户名和密码');
await page.locator('#loginPassword').fill('test-only');await page.locator('.showpass').click();check('password visibility',await page.locator('#loginPassword').getAttribute('type')==='text');
await page.locator('#loginName').fill('test-only');
let calls=0;await page.route('**/api/user/login',async r=>{calls++;await new Promise(x=>setTimeout(x,500));await r.fulfill({json:{code:40003,msg:'账号或密码错误'}})});
await page.locator('button.submit').dblclick({force:true});await page.waitForTimeout(700);check('duplicate prevention',calls===1);check('business error',await page.locator('[role=alert]').innerText()==='账号或密码错误');
await page.unroute('**/api/user/login');await page.route('**/api/user/login',r=>r.abort());await page.locator('button.submit').click();await page.waitForTimeout(300);check('network error',(await page.locator('[role=alert]').innerText()).includes('网络异常'));
await page.screenshot({path:out+'/desktop-login.png'});
await page.goto('http://127.0.0.1:5173/register');await page.locator('#registerName').fill('test-only');await page.locator('#realName').fill('测试');await page.locator('#registerPassword').fill('test-password');await page.locator('#confirmPassword').fill('different');await page.locator('button.submit').click();check('confirm mismatch',(await page.locator('[role=alert]').innerText()).includes('不一致'));
await page.screenshot({path:out+'/desktop-register.png'});
for(const route of ['/','/login','/register']){await page.setViewportSize({width:390,height:844});await page.goto('http://127.0.0.1:5173'+route);await page.waitForTimeout(800);check('mobile no horizontal overflow '+route,await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));await page.screenshot({path:out+'/mobile-'+(route.slice(1)||'welcome')+'.png',fullPage:true})}
await page.emulateMedia({reducedMotion:'reduce'});check('reduced motion',await page.locator('.track').first().evaluate(el=>getComputedStyle(el).animationName)==='none');
fs.writeFileSync(out+'/browser-results.json',JSON.stringify(results,null,2));console.log(JSON.stringify(results));await browser.close();

