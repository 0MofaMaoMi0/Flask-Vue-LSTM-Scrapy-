import fs from 'node:fs/promises'
import ts from 'typescript'
import assert from 'node:assert/strict'
const src=await fs.readFile(new URL('../src/services/movieNavigation.ts',import.meta.url),'utf8')
const js=ts.transpile(src,{target:ts.ScriptTarget.ES2022,module:ts.ModuleKind.ESNext})
const {assistantMovieTarget,assistantReturnTarget}=await import('data:text/javascript;base64,'+Buffer.from(js).toString('base64'))
const route='/recommendations?channel=weekly&year_to=2000'
const floating=assistantMovieTarget(2994,'floating',route)
assert.equal(floating.hash,'#movie/2994');assert.equal(assistantReturnTarget(floating.query.from,floating.query.return_to),route)
const explore=assistantMovieTarget(2995,'explore','/ai');assert.equal(explore.hash,'#movie/2995');assert.equal(assistantReturnTarget(explore.query.from,explore.query.return_to),'/ai')
for(const unsafe of ['https://evil.test','//evil.test','/movies-evil','/login','javascript:alert(1)',null,['/ai']])assert.equal(assistantReturnTarget('ai',unsafe),'/ai')
console.log('PASS: correct movie IDs, independent AI origins, filter restoration, unsafe return targets rejected')
