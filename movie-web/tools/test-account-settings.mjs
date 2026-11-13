import fs from 'node:fs/promises'
import ts from 'typescript'
import assert from 'node:assert/strict'
const src=await fs.readFile(new URL('../src/services/accountSettings.ts',import.meta.url),'utf8')
const js=ts.transpile(src,{target:ts.ScriptTarget.ES2022,module:ts.ModuleKind.ESNext})
const {ACCOUNT_SETTINGS,isAccountSettingsTab,accountSettingsTarget}=await import('data:text/javascript;base64,'+Buffer.from(js).toString('base64'))
for(const tab of ACCOUNT_SETTINGS){assert.equal(isAccountSettingsTab(tab),true);assert.deepEqual(accountSettingsTarget(tab),{path:'/ai',query:{settings:tab}})}
for(const bad of ['constructor','__proto__','https://evil.invalid','/admin',undefined,null,['account'],{tab:'privacy'}]){assert.equal(isAccountSettingsTab(bad),false);assert.throws(()=>accountSettingsTarget(bad))}
console.log('PASS: supported settings categories route internally; unknown fields, objects and external URLs rejected')
