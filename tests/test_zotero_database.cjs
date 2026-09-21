const {test}=require('node:test');
const assert=require('node:assert/strict');
const {readFileSync}=require('node:fs');
const vm=require('node:vm');
const core=require('../zotero-plugin/content/core.js');
const scope={Search4PaperCore:core,URL,URLSearchParams};
vm.runInNewContext(readFileSync(require.resolve('../zotero-plugin/content/database-sources.js'),'utf8'),scope);
const db=scope.Search4PaperDatabaseSources;
const config=core.DATABASE_SOURCES.KDD[2025];
function note(id,cycle){return {id,invitations:[config.cycles[cycle]+'/-/Submission'],content:{
  title:{value:'Anomaly Detection in Graphs'},authors:{value:['Ada Example']},venue:{value:config.acceptedVenues[cycle]},
  abstract:{value:'Research on time series'},keywords:{value:['graph learning']},TLDR:{value:'Robust detection'},pdf:{value:'/pdf/hash.pdf'}}};}
test('KDD paginates each verified Research Track cycle and merges stable IDs',async()=>{
  const requests=[];
  const papers=await db.fetchKDDOpenReview(2025,{request:async url=>{
    const p=new URL(url).searchParams,cycle=config.cycles.findIndex(c=>p.get('invitation')===c+'/-/Submission');
    assert.ok(cycle>=0);assert.equal(p.get('content.venue'),config.acceptedVenues[cycle]);
    requests.push([cycle,Number(p.get('offset'))]);
    return {count:2,notes:[note(Number(p.get('offset'))?'cycle-'+cycle:'shared',cycle)]};
  }});
  assert.deepEqual(requests,[[0,0],[0,1],[1,0],[1,1]]);assert.equal(papers.length,3);
  assert.equal(papers[0].cycles.length,2);
  for(const field of ['abstract','keywords','tldr'])assert.ok(core.matchPaper(papers[0],{terms:[papers[0][field]],operator:'AND',fields:[field]}));
  assert.equal(papers[0].sourceMetadata.cycles.length,2);
});
test('KDD rejects non-research, rejected, archive uploads, malformed and partial lists',async()=>{
  for(const changed of [p=>p.invitations=['KDD.org/2025/ADS_Track_August/-/Submission'],
    p=>p.content.venue.value='Submitted to KDD 2025 Research Track August',
    p=>p.invitations=['OpenReview.net/Archive/-/Direct_Upload']]){
    const p=note('1',0);changed(p);assert.throws(()=>db.normalizeKDD(p,2025,0),/非 KDD/);
  }
  await assert.rejects(db.fetchKDDOpenReview(2025,{request:async()=>({count:2,notes:[note('repeated',0)]})}),/重复/);
  await assert.rejects(db.fetchKDDOpenReview(2025,{request:async()=>({count:2,notes:[]})}),/分页不完整/);
});
test('new sources reject unverified years before network access',async()=>{
  for(const conference of Object.keys(core.DATABASE_SOURCES)){
    await assert.rejects(db.fetchAccepted(conference,2026,{request:()=>{throw new Error('must not request');}}),/尚未核实/);
  }
});
test('new source caches preserve mapping and isolate conference/year; invalid VLDB issue fails',()=>{
  for(const conference of Object.keys(core.DATABASE_SOURCES)){
    const config=core.DATABASE_SOURCES[conference][2025];
    const paper={source:conference.toLowerCase(),id:'2025:one',year:2025,venueID:core.venueID(conference,2025),
      title:'Example',authors:['Ada Example'],abstract:'',keywords:'',tldr:'',doi:'',bibtex:'',pdfURL:'',
      url:config.sourceURL,sourceURL:config.sourceURL,sourceMetadata:config,cycles:config.cycles,volume:config.volume,issue:1};
    const meta={schemaVersion:1,year:2025,venueID:paper.venueID,fetchedAt:new Date().toISOString(),paperCount:1,papers:[paper]};
    assert.equal(core.validateMetadata(meta,2025,conference),meta);
    assert.throws(()=>core.validateMetadata(meta,2026,conference),/年份/);
    assert.throws(()=>core.validateMetadata(meta,2025,'ICDE'),/会议/);
    const key=`source:${paper.source}:${paper.id}`;assert.ok(core.identities({extra:`Source ID: ${paper.source}:${paper.id}`}).has(key));
    const bad=JSON.parse(JSON.stringify(meta));bad.papers[0].sourceMetadata.sourceURL='https://example.org';
    assert.throws(()=>core.validateMetadata(bad,2025,conference),/来源/);
    if(conference==='VLDB'){paper.issue=13;assert.throws(()=>core.validateMetadata(meta,2025,conference),/其他会议年度/);}
  }
});
