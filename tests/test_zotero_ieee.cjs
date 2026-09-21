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
    CreatorTypes: {getID: () => 1},
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
    getMainWindow() { return { openDialog(url, name, features, options) {
      const win = {url,options,closed:false,focus(){this.focused=true;},close(){this.closed=true;}};
      viewers.push(win); return win;
    } }; },
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
  assert.equal(f.viewers[0].url,'chrome://search4paper/content/ieee-login.xhtml');
  assert.match(f.service.status,/尚未验证/);
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

test('login browser and popup contexts retain cookie isolation and native navigation ownership', () => {
  const appended = [], loads = [];
  const controls = new Map(['back','close-popup','site','login-content'].map(id=>[id,{addEventListener(){}}]));
  controls.get('login-content').appendChild = browser => {
    assert.equal(browser.attributes.usercontextid,'42');
    assert.notEqual(browser.attributes.messagemanagergroup,'basicViewer');
    appended.push(browser);
  };
  const window = {arguments:[{userContextId:42}],addEventListener(){},close(){this.closed=true;}};
  const document = {getElementById:id=>controls.get(id),createXULElement:()=>({attributes:{},hidden:false,
    setAttribute(name,value){this.attributes[name]=value;},browsingContext:{sandboxFlags:0x80},
    currentURI:{schemeIs:()=>false},addProgressListener(){},addEventListener(){},remove(){this.removed=true;},
    loadURI(uri,options){loads.push({browser:this,uri,options});}})};
  const scope={window,document,ChromeUtils:{generateQI:()=>()=>{}},Ci:{nsIWebProgress:{NOTIFY_LOCATION:1}},
    Services:{io:{newURI:uri=>uri},scriptSecurityManager:{getSystemPrincipal:()=> 'system'}}};
  vm.runInNewContext(readFileSync(require.resolve('../zotero-plugin/content/ieee-login.js'),'utf8'),scope);
  scope.IEEELogin.init();
  assert.equal(loads.length,1);assert.equal(loads[0].uri,'https://ieeexplore.ieee.org/');
  const info={originAttributes:{userContextId:42},isRemote:false,parent:{}};
  const context=window.browserDOMWindow.createContentWindow('https://example.org/post',info);
  assert.equal(context,appended[1].browsingContext);assert.equal(appended[1].openWindowInfo,info);
  assert.equal(loads.length,1,'Gecko must perform the new-context load, preserving POST rather than converting it to GET');
  assert.equal(appended[0].hidden,true);
  const principal={origin:'https://example.org'},csp={};
  window.browserDOMWindow.openURI('https://example.org/login',info,0,0,principal,csp);
  assert.equal(loads[1].options.triggeringPrincipal,principal);assert.equal(loads[1].options.csp,csp);
  const popup=scope.IEEELogin.browser;scope.IEEELogin.closeBrowser(popup);
  assert.equal(popup.removed,true);assert.equal(appended[1].hidden,false);
  assert.throws(()=>window.browserDOMWindow.createContentWindow(null,{originAttributes:{userContextId:7}}),/Cookie 上下文不一致/);
  window.arguments[0]={userContextId:42,publisher:'ACM',initialURL:'https://dl.acm.org/doi/10.1145/1.2'};
  scope.IEEELogin.init();
  assert.equal(loads.at(-1).uri,'https://dl.acm.org/doi/10.1145/1.2');assert.equal(document.title,'ACM 访问验证');
  assert.equal(appended.at(-1).attributes.usercontextid,'42');
  window.arguments[0].initialURL='https://example.org/doi/10.1145/1.2';
  assert.throws(()=>scope.IEEELogin.init(),/无效的 ACM/);

});

test('login popup actor preserves native navigation and security features without window sizing', () => {
  const calls = [], popup = {};
  const win = {open(...args) { calls.push({receiver:this,args}); return popup; },wrappedJSObject:{}};
  const scope = {JSWindowActorChild:class {}, Cu:{exportFunction(fn,target,options){target[options.defineAs]=fn;}}};
  const source = readFileSync(require.resolve('../zotero-plugin/content/IEEELoginNavigationChild.mjs'),'utf8');
  vm.runInNewContext(source.replace('export class IEEELoginNavigationChild','var IEEELoginNavigationChild = class'),scope);
  const actor = new scope.IEEELoginNavigationChild();actor.contentWindow=win;actor.handleEvent();
  assert.equal(win.wrappedJSObject.open('https://example.org/sso','sso','width=500,height=500,noopener,noreferrer=yes'),popup);
  assert.equal(calls[0].receiver,win);
  assert.deepEqual(calls[0].args,['https://example.org/sso','sso','noopener,noreferrer=yes']);
  win.wrappedJSObject.open('https://example.org/sso','sso');
  assert.equal(calls[1].args[2],'');
});

function metadataItem(fields = {}) {
  const values = {title:'UMGAD: Unsupervised Multiplex Graph Anomaly Detection',date:'2025',DOI:'',
    url:'https://ieee-icde.org/2025/research-papers/',extra:'Source ID: icde:2025:1',conferenceName:'IEEE International Conference on Data Engineering',...fields};
  return {libraryID:1,values,saves:0,getField:f=>values[f]||'',setField:(f,v)=>{values[f]=v;},
    getCreators:()=>[{creatorTypeID:1,lastName:'Xiang Li',fieldMode:1}],async saveTx(){this.saves++;}};
}
const metadataRecord = {DOI:'10.1109/ICDE65448.2025.00278',title:['UMGAD: Unsupervised Multiplex Graph Anomaly Detection'],
  author:[{given:'Xiang',family:'Li'}],published:{'date-parts':[[2025,5,19]]},
  resource:{primary:{URL:'https://ieeexplore.ieee.org/document/11113091/'}}};
function metadataFixture(records, doi = false) {
  const f=fixture([()=>({response:{message:doi?records[0]:{items:records}}})]);
  f.Zotero.Libraries.get=()=>({editable:true,filesEditable:true});return f;
}
test('IEEE metadata lookup writes a verified URL and missing DOI, preserving original source without cookies',async()=>{
  const f=metadataFixture([metadataRecord]),item=metadataItem();f.service.login();
  assert.equal(f.service.canResolve(item),true);
  assert.equal(await f.service.resolvePaperURL(item),true);
  assert.equal(item.values.url,metadataRecord.resource.primary.URL);assert.equal(item.values.DOI,metadataRecord.DOI);
  assert.match(item.values.extra,/Source ID: icde:2025:1\nOriginal URL: https:/);assert.equal(item.saves,1);
  assert.equal(f.requests[0].options.userContextId,undefined);
  assert.match(f.requests[0].url,/query.bibliographic=/);
  assert.equal(await f.service.resolvePaperURL(item),false);assert.equal(f.requests.length,1);
});
test('DOI lookup uses exact endpoint and rejects metadata inconsistent with the item',async()=>{
  const f=metadataFixture([metadataRecord],true),item=metadataItem({DOI:metadataRecord.DOI});
  await f.service.resolvePaperURL(item);assert.match(f.requests[0].url,/works\/10.1109%2FICDE/);
  const bad=metadataFixture([{...metadataRecord,DOI:'10.1109/other'}],true);
  await assert.rejects(bad.service.resolvePaperURL(metadataItem({DOI:metadataRecord.DOI})),/未找到/);
});
test('ambiguous, mismatched, absent and unsafe IEEE metadata never changes the item',async()=>{
  for (const records of [[],[metadataRecord,{...metadataRecord,DOI:'10.1109/other'}],
    [{...metadataRecord,title:['Different title']}],[{...metadataRecord,author:[{given:'Other',family:'Li'}]}],
    [{...metadataRecord,published:{'date-parts':[[2024]]}}],
    [{...metadataRecord,resource:{primary:{URL:'https://example.org/document/1'}}}]]) {
    const f=metadataFixture(records),item=metadataItem(),before={...item.values};
    await assert.rejects(f.service.resolvePaperURL(item),/IEEE 地址补全/);
    assert.deepEqual(item.values,before);assert.equal(item.saves,0);
  }
});
test('lookup cancellation, malformed responses, write failure and read-only libraries do not leave metadata changes',async()=>{
  const f=metadataFixture([metadataRecord]),item=metadataItem(),before={...item.values};
  const controller=new AbortController();controller.abort();
  await assert.rejects(f.service.resolvePaperURL(item,{signal:controller.signal}));assert.equal(f.requests.length,0);
  item.saveTx=async()=>{throw new Error('save failure');};
  await assert.rejects(f.service.resolvePaperURL(item),/save failure/);assert.deepEqual(item.values,before);
  const readOnly=metadataFixture([metadataRecord]);readOnly.Zotero.Libraries.get=()=>({editable:false});
  await assert.rejects(readOnly.service.resolvePaperURL(metadataItem()),/不可编辑/);
  const malformed=fixture([()=>({response:{message:{}}})]);
  await assert.rejects(malformed.service.resolvePaperURL(metadataItem()),/格式无效/);
});
