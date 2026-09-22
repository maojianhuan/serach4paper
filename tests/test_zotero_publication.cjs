const {test}=require('node:test');
const assert=require('node:assert/strict');
const vm=require('node:vm');
const {readFileSync}=require('node:fs');

const scope={};
vm.runInNewContext(readFileSync(require.resolve('../zotero-plugin/content/publication.js'),'utf8'),scope);
const resolver=scope.Search4PaperPublication;

function fixture(records, overrides={}) {
  const fields={title:'Federated Graph Anomaly Detection via Disentangled Representation Learning',date:'2025',DOI:'',
    conferenceName:'The Web Conference',proceedingsTitle:'Proceedings of The Web Conference 2025',...overrides};
  let saves=0;
  const item={getField:key=>fields[key]||'',setField:(key,value)=>{fields[key]=value;},
    getCreators:()=>[{firstName:'Jia',lastName:'Li'},{firstName:'Ada',lastName:'Example'}],isEditable:()=>true,
    saveTx:async()=>{saves++;}};
  const request=async()=>({message:{items:records}});
  return {item,fields,request,saves:()=>saves};
}
function record(overrides={}) {return {DOI:'10.1145/3696410.3714567',
  title:['Federated Graph Anomaly Detection via Disentangled Representation Learning'],
  published:{'date-parts':[[2025]]},'container-title':['Proceedings of the ACM on Web Conference 2025'],
  author:[{given:'Jia',family:'Li'},{given:'Ada',family:'Example'}],...overrides};}

test('strict WWW Crossref match writes the unique ACM DOI',async()=>{
  const f=fixture([record()]);
  assert.equal(await resolver.resolveWWWDOI(f.item,{}, {request:f.request,signal:new AbortController().signal}), '10.1145/3696410.3714567');
  assert.equal(f.fields.DOI,'10.1145/3696410.3714567');assert.equal(f.saves(),1);
});
test('WWW DOI enrichment rejects mismatched or ambiguous metadata without writing',async()=>{
  for(const records of [[record({title:['Different title']})],[record({author:[{given:'Other',family:'Person'}]})],
    [record({published:{'date-parts':[[2024]]}})],[record({'container-title':['Unrelated Conference']})],
    [record(),record({DOI:'10.1145/3696410.3714999'})]]) {
    const f=fixture(records);
    await assert.rejects(()=>resolver.resolveWWWDOI(f.item,{}, {request:f.request,signal:new AbortController().signal}),/未找到|多个/);
    assert.equal(f.fields.DOI,'');assert.equal(f.saves(),0);
  }
});
test('DOI enrichment is limited to WWW items with a missing DOI',async()=>{
  const nonWWW=fixture([record()],{conferenceName:'ICML',proceedingsTitle:'ICML 2025'});
  assert.equal(await resolver.resolveWWWDOI(nonWWW.item,{}, {request:()=>{throw Error('not called');}}),'');
  const existing=fixture([record()],{DOI:'10.1145/1.2'});
  assert.equal(await resolver.resolveWWWDOI(existing.item,{}, {request:()=>{throw Error('not called');}}),'');
});
