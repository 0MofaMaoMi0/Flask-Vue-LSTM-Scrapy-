import fs from 'node:fs/promises'
import ts from 'typescript'
import assert from 'node:assert/strict'
const src=await fs.readFile(new URL('../src/services/recommendationFeedback.ts',import.meta.url),'utf8')
const js=ts.transpile(src,{target:ts.ScriptTarget.ES2022,module:ts.ModuleKind.ESNext})
const {createFeedbackBoard,vFeedback}=await import('data:text/javascript;base64,'+Buffer.from(js).toString('base64'))
let tokens=0,posts=[],reject=false,fail=false
const oldFetch=globalThis.fetch
globalThis.fetch=async(url,options)=>{
 if(String(url).endsWith('/csrf'))return {ok:true,json:async()=>({data:{csrf_token:'t'+(++tokens)}})}
 posts.push({url,body:JSON.parse(options.body),token:options.headers['X-CSRF-Token']})
 if(fail)throw new Error('offline')
 if(reject){reject=false;return {ok:false,status:403,json:async()=>({code:'REQUEST_VERIFICATION_FAILED'})}}
 return {ok:true,status:201,json:async()=>({code:'OK'})}
}
const board=()=>createFeedbackBoard({channel:'daily',context:'test',items:[{movie_id:1},{movie_id:null,tmdb_id:'123'}]})
let b=board();assert.equal(await b.record(2),true);assert.equal(posts[0].body.items[0].tmdb_id,123)
assert.equal(await b.record(2),true);assert.equal(posts.length,1)
await b.click(2);assert.equal(posts.at(-1).url,'/api/catalog/recommendation-clicks');assert.equal(tokens,2)
reject=true;b=board();assert.equal(await b.record(1),true);assert.equal(posts.length,4)
fail=true;b=board();const before=posts.length;await b.click(1);assert.equal(posts.length,before+1);fail=false
b=board();const parallel=await Promise.all([b.record(1),b.record(2)]);assert.deepEqual(parallel,[true,true]);assert.notEqual(posts.at(-1).token,posts.at(-2).token)
b=board();b.dispose();assert.equal(await b.record(1),false)
let callback,timer,disconnected=false,recorded=0
const oldObserver=globalThis.IntersectionObserver,oldSet=globalThis.setTimeout,oldClear=globalThis.clearTimeout
globalThis.IntersectionObserver=class{constructor(cb){callback=cb}observe(){}disconnect(){disconnected=true}}
globalThis.setTimeout=cb=>{timer=cb;return 1};globalThis.clearTimeout=()=>{timer=undefined}
const el={};vFeedback.mounted(el,{value:{board:{record(){recorded++}},position:1}})
callback([{isIntersecting:true,intersectionRatio:0.6}]);assert.equal(recorded,0)
callback([{isIntersecting:false,intersectionRatio:0}]);assert.equal(timer,undefined)
callback([{isIntersecting:true,intersectionRatio:0.6}]);timer();assert.equal(recorded,1);assert.ok(disconnected)
vFeedback.beforeUnmount(el)
globalThis.fetch=oldFetch;globalThis.IntersectionObserver=oldObserver;globalThis.setTimeout=oldSet;globalThis.clearTimeout=oldClear
console.log('PASS: numeric external ID, dedup, fresh CSRF, explicit CSRF retry, no ambiguous retry/click, disposal, visibility dwell, cleanup')
