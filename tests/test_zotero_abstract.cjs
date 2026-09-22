const {test}=require('node:test');
const assert=require('node:assert/strict');
const vm=require('node:vm');
const {readFileSync}=require('node:fs');

const scope={URL};
vm.runInNewContext(readFileSync(require.resolve('../zotero-plugin/content/abstract.js'),'utf8'),scope);
const service=scope.Search4PaperAbstract;
const paper={id:'Note1',year:2025,title:'Exact Graph Paper',authors:['Ada Example','Jia Li'],abstract:'',url:'https://openreview.net/forum?id=Note1'};

test('OpenReview abstract is accepted only from the exact saved note',async()=>{
  const result=await service.resolve(paper,{signal:new AbortController().signal,request:async()=>({notes:[
    {id:'Other',content:{title:{value:paper.title},abstract:{value:'Wrong'}}},
    {id:'Note1',content:{title:{value:paper.title},abstract:{value:'Verified abstract'}}}
  ]})});
  assert.equal(result.abstract,'Verified abstract');assert.equal(result.source,'OpenReview');
});

test('Semantic Scholar requires exact title, author evidence, year and one match',async()=>{
  const candidate={title:paper.title,abstract:'Semantic abstract',year:2025,authors:[{name:'Ada Example'},{name:'Jia Li'}],url:'https://semanticscholar.org/paper/1'};
  const noOpenReview={...paper,url:'https://example.org/paper'};
  assert.equal((await service.resolve(noOpenReview,{request:async()=>({data:[candidate]})})).abstract,'Semantic abstract');
  for(const changed of [{title:'Different title'},{year:2024},{authors:[{name:'Other Person'}]}]) {
    assert.equal(await service.resolve(noOpenReview,{request:async()=>({data:[{...candidate,...changed}]})}),null);
  }
  assert.equal(await service.resolve(noOpenReview,{request:async()=>({data:[candidate,{...candidate,url:'https://semanticscholar.org/paper/2'}]})}),null);
});

test('existing abstracts are preserved without a request',async()=>{
  const result=await service.resolve({...paper,abstract:'Original'},{request:()=>{throw Error('not called');}});
  assert.equal(result.abstract,'Original');assert.equal(result.source,'原始来源');
});
