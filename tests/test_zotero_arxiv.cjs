const {test}=require('node:test');
const assert=require('node:assert/strict');
const vm=require('node:vm');
const {readFileSync}=require('node:fs');

const ATOM='http://www.w3.org/2005/Atom';
function node(name,value='',children=[]){
  return {name,textContent:value,children,getElementsByTagNameNS(_ns,wanted){
    return this.children.flatMap(child=>[...(child.name===wanted?[child]:[]),...child.getElementsByTagNameNS(_ns,wanted)]);
  }};
}
function entry({id='https://arxiv.org/abs/2502.08942v3',title='Cluster Aware Graph Anomaly Detection',authors=['Lecheng Zheng','John Birge'],published='2025-02-13T00:00:00Z'}={}){
  return node('entry','',[node('id',id),node('title',title),node('published',published),
    ...authors.map(name=>node('author','',[node('name',name)]))]);
}
function load(entries=[]){
  const doc={querySelector:()=>null,getElementsByTagNameNS(_ns,name){return name==='entry'?entries:[];}};
  class Parser { parseFromString(){ return doc; } }
  const scope={URL,URLSearchParams,DOMParser:Parser};
  vm.runInNewContext(readFileSync(require.resolve('../zotero-plugin/content/arxiv.js'),'utf8'),scope);
  return scope.Search4PaperArxiv;
}

test('explicit arXiv links always resolve to the unversioned latest PDF',()=>{
  const arxiv=load();
  assert.equal(arxiv.explicitPDF('https://arxiv.org/abs/2502.08942v3'),'https://arxiv.org/pdf/2502.08942');
  assert.equal(arxiv.explicitPDF('https://www.arxiv.org/pdf/2502.08942v2.pdf'),'https://arxiv.org/pdf/2502.08942');
  assert.equal(arxiv.explicitPDF('https://example.org/pdf/2502.08942'), '');
});

const item=(fields={},authors=['Lecheng Zheng','John Birge'])=>({
  getField:name=>({title:'Cluster Aware Graph Anomaly Detection',date:'2025',url:'',...fields})[name]||'',
  getCreators:()=>authors.map(lastName=>({lastName}))
});

test('API matching requires exact normalized title, authors, year and a unique result',async()=>{
  let requested='';
  const arxiv=load([entry()]);
  const found=await arxiv.resolve(item(),{}, {signal:new AbortController().signal,request:async url=>(requested=url,'fixture')});
  assert.equal(found.pdfURL,'https://arxiv.org/pdf/2502.08942');
  assert.equal(found.arxivID,'2502.08942');
  const query=new URL(requested).searchParams;
  assert.match(query.get('search_query'),/^ti:"Cluster Aware/);
  assert.equal(query.get('sortBy'),'lastUpdatedDate');

  for(const entries of [
    [entry({title:'A Different Graph Anomaly Paper'})],
    [entry({authors:['Unrelated Author','Another Person']})],
    [entry({published:'2021-01-01T00:00:00Z'})],
    [entry(),entry({id:'https://arxiv.org/abs/2502.09999'})]
  ]) assert.equal(await load(entries).resolve(item(),{}, {request:async()=>'',signal:new AbortController().signal}),null);
});

test('title-only lookup is skipped when Zotero has no authors',async()=>{
  let requests=0;
  const result=await load([entry()]).resolve(item({},[]),{}, {request:async()=>{requests++;},signal:new AbortController().signal});
  assert.equal(result,null);assert.equal(requests,0);
});
