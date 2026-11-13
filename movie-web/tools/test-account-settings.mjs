import fs from 'node:fs/promises'
import ts from 'typescript'
import assert from 'node:assert/strict'
const src=await fs.readFile(new URL('../src/services/accountSettings.ts',import.meta.url),'utf8')
const js=ts.transpile(src,{target:ts.ScriptTarget.ES2022,module:ts.ModuleKind.ESNext})
const {ACCOUNT_SETTINGS,isAccountSettingsTab,accountSettingsTarget,settingsReturnTarget}=await import('data:text/javascript;base64,'+Buffer.from(js).toString('base64'))
for(const tab of ACCOUNT_SETTINGS){assert.equal(isAccountSettingsTab(tab),true);assert.deepEqual(accountSettingsTarget(tab),{path:'/settings',query:{tab,return_to:'/home'}})}
for(const bad of ['constructor','__proto__','https://evil.invalid','/admin',undefined,null,['account'],{tab:'privacy'}]){assert.equal(isAccountSettingsTab(bad),false);assert.throws(()=>accountSettingsTarget(bad))}
console.log('PASS: supported settings categories route internally; unknown fields, objects and external URLs rejected')

for(const path of ['/home','/ai','/movies?genre=科幻#behavior','/recommendations?channel=weekly','/admin'])assert.equal(settingsReturnTarget(path),path)
for(const bad of ['https://evil.invalid','//evil.invalid','/\\evil.invalid','/settings?return_to=https://evil.invalid','/login','/movies-evil',null,['/ai']])assert.equal(settingsReturnTarget(bad),'/home')
assert.equal(settingsReturnTarget('/settings?tab=tmdb&return_to=%2Frecommendations%3Fchannel%3Dweekly'),'/recommendations?channel=weekly')
assert.equal(settingsReturnTarget('/ai?x=1&settings=tmdb'),'/ai?x=1')
assert.equal(accountSettingsTarget('ai','/movies?genre=scifi').query.return_to,'/movies?genre=scifi')
console.log('PASS: independent settings route, source-page restoration and legacy-loop protection')
