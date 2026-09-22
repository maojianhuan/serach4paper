const {test}=require('node:test');
const assert=require('node:assert/strict');
const vm=require('node:vm');
const {readFileSync}=require('node:fs');

const scope={URL};
vm.runInNewContext(readFileSync(require.resolve('../zotero-plugin/content/openreview.js'),'utf8'),scope);
const resolver=scope.Search4PaperOpenReview;
const item=fields=>({getField:key=>fields[key]||''});
test('OpenReview immutable object URL precedes the note-id endpoint',async()=>{
  const hash='efd32c8bb4880e198213769255f2679f4f3b87fd';
  const response={notes:[{id:'same-title',content:{pdf:{value:'/pdf/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa.pdf'}}},
    {id:'xqjnhRqdK9',content:{pdf:{value:`/pdf/${hash}.pdf`}}}]};
  const urls=await resolver.URLs(item({title:'Federated Graph Anomaly Detection',url:'https://openreview.net/forum?id=xqjnhRqdK9'}),
    {request:async(url)=>{assert.match(url,/type=exact.*content=title/);return response;},signal:new AbortController().signal});
  assert.deepEqual([...urls],[`https://openreview.net/pdf/${hash}.pdf`,'https://openreview.net/pdf?id=xqjnhRqdK9']);
});
test('OpenReview resolver falls back to the exact saved ID and rejects unsafe object URLs',async()=>{
  const response={notes:[{id:'Safe_ID-1',content:{pdf:{value:'https://evil.example/file.pdf'}}}]};
  assert.deepEqual([...(await resolver.URLs(item({title:'Paper',extra:'OpenReview ID: Safe_ID-1'}),{request:async()=>response}))],
    ['https://openreview.net/pdf?id=Safe_ID-1']);
  assert.deepEqual([...(await resolver.URLs(item({title:'Paper',url:'https://openreview.net.example/forum?id=bad'}),{request:async()=>response}))],[]);
});
