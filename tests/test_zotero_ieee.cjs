const {test} = require('node:test');
const assert = require('node:assert/strict');
const vm = require('node:vm');
const {readFileSync} = require('node:fs');
const source = readFileSync(require.resolve('../zotero-plugin/content/ieee.js'), 'utf8');
const pdf = new TextEncoder().encode('%PDF-1.7\n1 0 obj\n<<>>\nendobj\n%%EOF\n').buffer;
function fixture(responses = []) {
  let time = 0, live = 0, maxLive = 0, sequence = 0;
  const requests = [], imports = [], writes = [], removed = [], viewers = [], disposed = [], sleeps = [];
  const context = {id: 123456, dispose: () => disposed.push(context.id)};
  const Zotero = {
    HTTP: {
      newCookieContext() { sequence++; return context; },
      async request(method, url, options) {
        const start = time;
        live++; maxLive = Math.max(maxLive, live);
        requests.push({method,url,options,start});
        // Model synchronous setup time as well as awaited network time.
        time += 3;
        try {
          const action = responses.shift();
          return action ? await action(options) : {response: pdf};
        } finally {live--;}
      }
    },
    openInViewer(url, options) {
      const win = {url,options,closed:false,focus(){this.focused=true;},close(){this.closed=true;}};
      viewers.push(win); return win;
    },
    Libraries: {get: () => ({filesEditable:true})},
    Items: {getAsync: async ids => ids.map(() => ({attachmentContentType:'application/pdf',isFileAttachment:()=>true,fileExists:async()=>true}))},
    Attachments: {
      createTemporaryStorageDirectory: async () => ({path:'/tmp/ieee-test'}),
      importFromFile: async options => { imports.push(options); return {id:100}; }
    }
  };
  const scope = {URL,AbortController,Uint8Array,Date,
    PathUtils:{join:(...s)=>s.join('/')},
    IOUtils:{write:async(path,bytes)=>writes.push({path,bytes}), remove:async(path,options)=>removed.push({path,options})}};
  vm.runInNewContext(source,scope);
  const service = new scope.Search4PaperIEEE(Zotero, {now:()=>time, sleep:async ms=>{sleeps.push(ms);time+=ms;}});
  const item = (id, url = `https://ieeexplore.ieee.org/document/${id}`, existing = false) => ({id,libraryID:1,
    isRegularItem:()=>true,getField:field=>field==='title'?`Paper ${id}`:url,getAttachments:()=>existing?[100]:[]});
  return {service,Zotero,item,requests,imports,writes,removed,viewers,disposed,sleeps,
    get maxLive(){return maxLive;},get contexts(){return sequence;},get time(){return time;}};
}

test('explicit login shares one context across visible viewers and requests; disposal removes it', async()=>{
  const f=fixture(); assert.throws(()=>f.service.download([f.item(1)]),/先启动/);
  f.service.login();f.service.login();
  assert.equal(f.contexts,1);assert.equal(f.viewers.length,1);assert.equal(f.viewers[0].focused,true);
  assert.equal(f.viewers[0].url,'https://ieeexplore.ieee.org/');
  assert.equal(f.viewers[0].options.allowJavaScript,true);assert.match(f.service.status,/尚未验证/);
  f.viewers[0].close();f.service.login();assert.equal(f.contexts,1);
  await f.service.download([f.item(1)]);
  assert.equal(f.requests[0].options.userContextId,f.viewers[1].options.userContextId);
  assert.equal(f.requests[0].options.responseType,'arraybuffer');
  assert.equal(f.requests[0].options.errorDelayMax,0);assert.equal(f.requests[0].options.noRetryOnThrottle,true);
  await f.service.dispose();assert.deepEqual(f.disposed,[123456]);assert.equal(f.viewers[1].closed,true);
  assert.throws(()=>f.service.login(),/已关闭/);
});

test('IEEE downloads preserve order, never overlap, and start >=10 seconds apart across batches', async()=>{
  const f=fixture();f.service.login();
  const first=f.service.download([f.item(3),f.item(1)]);
  assert.throws(()=>f.service.download([f.item(9)]),/正在进行/);
  assert.equal((await first).every(r=>r.hasPDF),true);
  await f.service.download([f.item(2)]);
  assert.deepEqual(f.imports.map(x=>x.parentItemID),[3,1,2]);assert.equal(f.maxLive,1);
  for(let i=1;i<f.requests.length;i++) assert.ok(f.requests[i].start-f.requests[i-1].start>=10000);
  assert.equal(f.requests[0].url,'https://ieeexplore.ieee.org/stampPDF/getPDF.jsp?arnumber=3');
  assert.equal(f.writes.length,3);assert.equal(f.removed.length,3);
  assert.match(f.service.status,/访问已验证/);
});

test('403 and login HTML fail per item without attachment writes or secret leakage; next start still waits', async()=>{
  const f=fixture([async()=>{throw {status:403,message:'SAMLResponse=SECRET'};},async()=>({response:new TextEncoder().encode('<html>Login</html>').buffer})]);
  f.service.login();const results=await f.service.download([f.item(1),f.item(2),f.item(3)]);
  assert.match(results[0].error,/HTTP 403/);assert.doesNotMatch(results[0].error,/SECRET|SAMLResponse/);
  assert.match(results[1].error,/不是完整 PDF/);assert.equal(results[2].hasPDF,true);
  assert.deepEqual(f.imports.map(x=>x.parentItemID),[3]);
  assert.ok(f.requests[1].start-f.requests[0].start>=10000);
  assert.ok(f.requests[2].start-f.requests[1].start>=10000);
});

test('truncated PDF is rejected even when it has a PDF header', async()=>{
  const f=fixture([async()=>({response:new TextEncoder().encode('%PDF-1.7\ntruncated').buffer})]);f.service.login();
  assert.equal((await f.service.download([f.item(1)]))[0].hasPDF,false);assert.equal(f.writes.length,0);
});

test('only supported HTTPS IEEE article/stamp URLs are accepted; other sources do not receive the context', async()=>{
  const f=fixture();f.service.login();
  for(const url of ['https://example.org/document/1','http://ieeexplore.ieee.org/document/1','https://user:pass@ieeexplore.ieee.org/document/1','https://ieeexplore.ieee.org:444/document/1','https://ieeexplore.ieee.org/','https://ieeexplore.ieee.org/stamp/stamp.jsp?arnumber=abc']) {
    const r=await f.service.download([f.item(1,url)]);assert.equal(r[0].hasPDF,false);
  }
  assert.equal(f.requests.length,0);
  assert.match(f.service.pdfURL('https://ieeexplore.ieee.org/abstract/document/42'),/arnumber=42$/);
  assert.match(f.service.pdfURL('https://ieeexplore.ieee.org/stamp/stamp.jsp?tp=&arnumber=42'),/arnumber=42$/);
});

test('existing PDFs are reused without HTTP or artificial delays', async()=>{
  const f=fixture();f.service.login();const r=await f.service.download([f.item(1,undefined,true)]);
  assert.equal(r[0].reused,true);assert.equal(f.requests.length,0);assert.equal(f.sleeps.length,0);
});

test('cancellation while waiting does not start another request', async()=>{
  const f=fixture();f.service.login();const controller=new AbortController();
  const r=await f.service.download([f.item(1),f.item(2),f.item(3)],{signal:controller.signal,onProgress:p=>{
    if(p.phase==='waiting' && p.index===2)controller.abort();
  }});
  assert.equal(f.requests.length,1);assert.equal(r.length,2);assert.match(r[1].error,/取消/);
});

test('disposal cancels the active HTTP request before disposing the cookie context', async()=>{
  const f=fixture([options=>new Promise((resolve,reject)=>options.cancellerReceiver(()=>reject(new Error('cancelled'))))]);
  f.service.login();let started;
  const reached=new Promise(resolve=>{started=resolve;});
  const task=f.service.download([f.item(1),f.item(2)],{onProgress:p=>{if(p.phase==='downloading')started();}});
  await reached;await f.service.dispose();const r=await task;
  assert.equal(f.requests.length,1);assert.match(r[0].error,/取消/);assert.deepEqual(f.disposed,[123456]);
});
