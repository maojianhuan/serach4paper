const {test}=require('node:test');
const assert=require('node:assert/strict');
const vm=require('node:vm');
const {readFileSync}=require('node:fs');
const source=readFileSync(require.resolve('../zotero-plugin/content/acm.js'),'utf8');
const pdf=new TextEncoder().encode('%PDF-1.7\n1 0 obj\n<<>>\nendobj\n%%EOF\n').buffer;
function fixture(actions=[]){
 const requests=[],imports=[],writes=[],removed=[],viewers=[],disposed=[];let contexts=0;
 const Zotero={HTTP:{newCookieContext(){contexts++;return {id:987,dispose:()=>disposed.push(987)};},
  async request(method,url,options){requests.push({method,url,options});return actions.length?actions.shift()(options):{response:pdf};}},
  getMainWindow:()=>({openDialog(url,name,features,options){const w={url,name,options,closed:false,focus(){this.focused=true;},close(){this.closed=true;}};viewers.push(w);return w;}}),
  Libraries:{get:()=>({filesEditable:true})},Items:{getAsync:async()=>[]},
  Attachments:{createTemporaryStorageDirectory:async()=>({path:'/temporary/acm'}),importFromFile:async o=>{imports.push(o);return {id:123};}}};
 const scope={URL,AbortController,Uint8Array,PathUtils:{join:(...s)=>s.join('/')},
  IOUtils:{write:async(...args)=>writes.push(args),remove:async(...args)=>removed.push(args)}};
 vm.runInNewContext(source,scope);
 const service=new scope.Search4PaperACM(Zotero);
 const item=(id=1,values={})=>({id,libraryID:1,isRegularItem:()=>true,getAttachments:()=>[],
  getField:f=>({title:'TSINR',DOI:'10.1145/3690624.3709266',url:'',...values})[f]||''});
 return {service,Zotero,item,requests,imports,writes,removed,viewers,disposed,get contexts(){return contexts;}};
}
test('ACM public PDF request uses official endpoint without login, validates and attaches PDF',async()=>{
 const f=fixture(),r=await f.service.download(f.item());assert.equal(r.hasPDF,true);
 assert.equal(f.requests[0].url,'https://dl.acm.org/doi/pdf/10.1145/3690624.3709266?download=true');
 assert.equal(f.requests[0].options.userContextId,987);assert.equal(f.requests[0].options.responseType,'arraybuffer');
 assert.equal(f.requests[0].options.noRetryOnThrottle,true);assert.equal(f.requests[0].options.errorDelayMax,0);
 assert.equal(f.viewers.length,0);assert.equal(f.imports[0].parentItemID,1);assert.equal(f.removed.length,1);
});
test('403 exposes manual verification only; reopening shares context with subsequent downloads',async()=>{
 const f=fixture([()=>{throw {status:403,message:'Cookie=SECRET SAMLResponse=SECRET'};}]);
 const r=await f.service.download(f.item());assert.equal(r.hasPDF,false);assert.equal(r.verificationRequired,true);
 assert.match(r.error,/403/);assert.doesNotMatch(r.error,/SECRET/);assert.equal(f.writes.length,0);assert.equal(f.viewers.length,0);
 f.service.verify();f.service.verify();assert.equal(f.viewers.length,1);assert.equal(f.viewers[0].focused,true);
 assert.equal(f.viewers[0].options.initialURL,'https://dl.acm.org/doi/10.1145/3690624.3709266');
 assert.equal(f.viewers[0].options.publisher,'ACM');assert.equal(f.viewers[0].options.userContextId,f.requests[0].options.userContextId);
 assert.match(f.service.status,/尚未验证/);
 f.viewers[0].close();f.service.verify();assert.equal(f.contexts,1);
 assert.equal((await f.service.download(f.item())).hasPDF,true);assert.equal(f.service.verificationURL,'');
 assert.equal((await f.service.download(f.item(2,{DOI:'10.1145/1.2'}))).hasPDF,true);
 assert.ok(f.requests.every(r=>r.options.userContextId===987));
 await f.service.dispose();assert.deepEqual(f.disposed,[987]);assert.equal(f.viewers[1].closed,true);
 assert.throws(()=>f.service.verify(),/已关闭/);
});
test('successful other ACM paper does not hide pending verification for an earlier failure',async()=>{
 const f=fixture([()=>{throw {status:403};}]);await f.service.download(f.item());
 await f.service.download(f.item(2,{DOI:'10.1145/1.2'}));assert.match(f.service.verificationURL,/3690624/);
});
test('ACM URL/DOI mapping rejects ambiguity and unrelated identifiers before network access',async()=>{
 const f=fixture();
 for(const url of ['https://dl.acm.org/doi/10.1145/1.2','https://dl.acm.org/doi/abs/10.1145/1.2','https://dl.acm.org/doi/pdf/10.1145/1.2?download=true']){
  assert.equal(f.service.paperURL(f.item(1,{DOI:'',url})),'https://dl.acm.org/doi/10.1145/1.2');
 }
 assert.equal(f.service.canHandle(f.item(1,{DOI:'10.1109/123',url:'https://ieeexplore.ieee.org/document/123'})),false);
 for(const values of [{DOI:'10.1145/1.2',url:'https://dl.acm.org/doi/10.1145/2.3'},
  {DOI:'10.1145/1.2',url:'https://secret@dl.acm.org/doi/10.1145/1.2'},
  {DOI:'10.1145/../../other'},{DOI:'10.5555/123'},
  {DOI:'',url:'https://dl.acm.org.evil.test/doi/10.1145/1.2'}]){
  assert.equal((await f.service.download(f.item(1,values))).hasPDF,false);
 }
 assert.equal(f.requests.length,0);
});
test('HTML, truncated PDF, 404, and attachment errors never count as download success',async()=>{
 for(const response of ['<html>Access verification SECRET</html>','%PDF-1.7\ntruncated']){
  const f=fixture([()=>({response:new TextEncoder().encode(response).buffer})]);const r=await f.service.download(f.item());
  assert.equal(r.hasPDF,false);assert.match(r.error,/不是完整 PDF/);assert.doesNotMatch(r.error,/SECRET/);assert.equal(f.imports.length,0);
 }
 const f=fixture([()=>{throw {status:404,message:'secret'};}]);assert.match((await f.service.download(f.item())).error,/404/);assert.equal(f.service.verificationURL,'');
 const failed=fixture();failed.Zotero.Attachments.importFromFile=async()=>{throw Error('attachment write failed');};
 assert.equal((await failed.service.download(failed.item())).hasPDF,false);assert.equal(failed.removed.length,1);
});
test('existing local PDF avoids requests; cancellation/disposal abort in-flight download; concurrent calls rejected',async()=>{
 const f=fixture();f.Zotero.Items.getAsync=async()=>[{attachmentContentType:'application/pdf',isFileAttachment:()=>true,fileExists:async()=>true}];
 assert.equal((await f.service.download(f.item())).reused,true);assert.equal(f.requests.length,0);assert.equal(f.contexts,0);
 const before=new AbortController();before.abort();assert.equal((await f.service.download(f.item(),{signal:before.signal})).hasPDF,false);
 let ready;const started=new Promise(r=>ready=r);
 const g=fixture([options=>new Promise((resolve,reject)=>{options.cancellerReceiver(()=>reject(Error('aborted')));ready();})]);
 const download=g.service.download(g.item());await started;
 assert.throws(()=>g.service.download(g.item()),/正在进行/);
 await g.service.dispose();assert.match((await download).error,/取消/);assert.equal(g.imports.length,0);assert.deepEqual(g.disposed,[987]);
});
