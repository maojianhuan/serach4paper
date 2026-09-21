const {test}=require('node:test');
const assert=require('node:assert/strict');
const {readFileSync}=require('node:fs');
const vm=require('node:vm');
const core=require('../zotero-plugin/content/core.js');
const scope={Search4PaperCore:core,URL};
vm.runInNewContext(readFileSync(require.resolve('../zotero-plugin/content/systems-sources.js'),'utf8'),scope);
const sources=scope.Search4PaperSystemsSources;
test('eight official sources reject unverified years before requesting data',async()=>{
 assert.equal(Object.keys(core.SYSTEMS_SOURCES).length,8);
 for(const conference of Object.keys(core.SYSTEMS_SOURCES)){
  await assert.rejects(sources.fetchAccepted(conference,2026,{request(){throw Error('must not fetch');}}),/尚未核实/);
 }
});
test('new source caches and identities are isolated by source/year and retain provenance',()=>{
 for(const c of Object.keys(core.SYSTEMS_SOURCES)){
  const config=core.SYSTEMS_SOURCES[c][2025];
  const p={id:'2025:one',title:'Example',authors:['Ada Example'],source:c.toLowerCase().replace(/ /g,'-'),year:2025,
   venueID:core.venueID(c,2025),abstract:'',keywords:'',tldr:'',doi:'',url:config.sourceURL,pdfURL:'',bibtex:'',
   sourceURL:config.sourceURL,sourceMetadata:{mode:'accepted-papers',urls:[config.sourceURL]}};
  const m={schemaVersion:1,year:2025,venueID:p.venueID,fetchedAt:new Date().toISOString(),paperCount:1,papers:[p]};
  assert.equal(core.validateMetadata(m,2025,c),m);
  assert.throws(()=>core.validateMetadata(m,2026,c),/年份/);
  assert.throws(()=>core.validateMetadata(m,2025,'ICDE'),/会议/);
  assert.ok(core.identities({extra:`Source ID: ${p.source}:${p.id}`}).has(`source:${p.source}:${p.id}`));
  p.sourceMetadata.urls=['https://example.org'];assert.throws(()=>core.validateMetadata(m,2025,c),/来源/);
 }
});
test('duplicate accepted papers merge cycles in source order without losing provenance',()=>{
 const papers=sources.merge([{id:'1',title:'Example',authors:['Ada'],cycles:['first']},
  {id:'2',title:'Second',authors:['Bob'],cycles:['second']},{id:'1',title:'Example',authors:['Ada'],cycles:['second']}]);
 assert.deepEqual(Array.from(papers,p=>p.id),['1','2']);assert.deepEqual(Array.from(papers[0].cycles),['first','second']);
 assert.throws(()=>sources.merge([]),/为空/);
 assert.throws(()=>sources.merge([{id:'1',title:'X',authors:['Ada']},{id:'1',title:'Y',authors:['Ada']}]),/不一致/);
});
