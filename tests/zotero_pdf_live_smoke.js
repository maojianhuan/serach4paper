/* Live, opt-in audit: installed XPI, empty isolated library, no saved login state.
 * Load in Zotero's Run JavaScript window and call runSearch4PaperPDFLiveSmoke({
 *   expectedDataDir, reportPath, only: undefined
 * }). Conference enumeration always comes from the installed plugin registry.
 * Metadata may be reused from normal source/year caches. Downloads are sequential;
 * one selected paper per venue, no alternate-paper fishing. Test items/files are erased.
 */
async function runSearch4PaperPDFLiveSmoke({ expectedDataDir, reportPath, only }) {
  if (!expectedDataDir || Zotero.DataDirectory.dir !== expectedDataDir) throw Error('Use the specified isolated data directory');
  if ((await Zotero.Items.getAll(Zotero.Libraries.userLibraryID, true, false)).some(i => i.isRegularItem())) throw Error('Audit requires an empty test library');
  Zotero.getMainWindow().document.getElementById('search4paper-open').doCommand();
  let win;
  for (let i=0;i<100;i++) {
    win=[...Services.wm.getEnumerator(null)].find(w=>w.location.href==='chrome://search4paper/content/search.xhtml'&&!w.closed);
    if(win?.Search4PaperUI&&win.document.readyState==='complete')break;
    await Zotero.Promise.delay(100);
  }
  const ui=win.Search4PaperUI,core=win.Search4PaperCore;
  if(ui.ieee.context || ui.acm.context)throw Error('Restart Zotero: audit requires fresh sessions without prior login');
  // This opt-in entry point is restricted to an empty isolated profile.
  Services.cookies.removeAll();
  Services.cache2.clear();
  const names=core.CONFERENCES.filter(c=>core.CONFERENCE_CATEGORIES[c].type==='会议');
  if(only?.some(c=>!names.includes(c)))throw Error('Unknown conference');
  const report=[],originalRequest=Zotero.HTTP.request,originalDownload=Zotero.HTTP.download;
  let events=[], activeDOI="", subscriptionHost="";
  const host=u=>{try{return new URL(typeof u==='string'?u:u.spec).hostname;}catch{return '';}};
  function instrument(original,download) {
    return async function(...args) {
      const input=download?args[0]:args[1];
      try {
        const response=await original.apply(this,args), finalURL=response?.responseURL||response?.url||input;
        events.push({host:host(finalURL),status:response?.status||'OK',pdf:download||args[2]?.responseType==='arraybuffer'});
        // Diagnostic only, not a resolver: the publisher explicitly labels this chapter paywalled.
        if(!download && activeDOI && host(finalURL)==='link.springer.com'
          && decodeURIComponent(String(finalURL)).includes(activeDOI)
          && response.getResponseHeader?.('Content-Type')?.includes('text/html')) {
          const body=typeof response.response?.text==='function' ? await response.response.text()
            : typeof response.response==='string' ? response.response : '';
          if(body.includes('This is a preview of subscription content'))subscriptionHost=host(finalURL);
        }
        return response;
      }
      catch(e){events.push({host:host(input),status:e.status||e.xmlhttp?.status||'NETWORK_ERROR',pdf:download||args[2]?.responseType==='arraybuffer'});throw e;}
    };
  }
  Zotero.HTTP.request=instrument(originalRequest,false);Zotero.HTTP.download=instrument(originalDownload,true);
  try {
    for(const conference of names.filter(c=>!only||only.includes(c))) {
      const row={conference,year:null,paper:'',paperSource:'',pdfSource:'',httpStatus:'',result:'SOURCE_UNAVAILABLE'};
      report.push(row);let imported,selectedOverride,pane;
      events=[];activeDOI="";subscriptionHost="";
      try {
        const dir=PathUtils.join(expectedDataDir,'search4paper',conference);
        const caches=[];
        if(await IOUtils.exists(dir))for(const path of await IOUtils.getChildren(dir)) {
          const match=PathUtils.filename(path).match(/^(\d{4})\.json$/);if(!match)continue;
          const year=Number(match[1]),metadata=core.validateMetadata(await IOUtils.readJSON(path),year,conference);
          caches.push({year,hasPDF:metadata.papers.some(p=>p.pdfURL)});
        }
        // Prefer an already verified year with an explicit PDF, then the newest year.
        caches.sort((a,b)=>Number(b.hasPDF)-Number(a.hasPDF)||b.year-a.year);
        const configured=Object.keys(core.SYSTEMS_SOURCES[conference]||core.DATABASE_SOURCES[conference]||{}).map(Number);
        row.year=caches[0]?.year||(configured.length?Math.max(...configured):2025);
        ui.$('conference').value=conference;ui.$('year').value=String(row.year);
        ui.$('terms').value='paper';ui.$('operator').value='OR';ui.$('field-title').checked=true;
        const signal=new AbortController().signal;
        await ui.fetchPapers(signal);
        const paper=ui.papers.find(p=>p.pdfURL)||ui.papers[0];
        if(!paper)throw Error('No official research papers');
        activeDOI=paper.doi;
        row.paper=paper.title;row.paperSource=paper.sourceURL||paper.url;row.pdfSource=host(paper.pdfURL);
        row.result='FAIL';row.httpStatus='retrieving';await IOUtils.writeJSON(reportPath,report);
        const result=await win.Search4PaperImport.papers(Zotero,[paper],{query:{terms:[paper.title],operator:'AND',fields:['title']},signal});
        imported=result.results[0]?.item;
        if(!imported||result.results[0].status!=='created')throw Error('Expected one new imported item');
        ui.lastImport=result;ui.switchPage('fulltext');
        pane=Zotero.getActiveZoteroPane();selectedOverride=pane.getSelectedItems;pane.getSelectedItems=()=>[imported];
        events=[];
        await ui.getFullText(signal);
        const outcome=ui.lastPDFs[0];
        const attachments=await Zotero.Items.getAsync(imported.getAttachments());
        let valid=false;
        for(const attachment of attachments) {
          if(!attachment.isFileAttachment())continue;
          const path=await attachment.getFilePathAsync();if(!path)continue;
          const signature=await IOUtils.read(path,{maxBytes:8});
          if(String.fromCharCode(...signature).startsWith('%PDF-')) {
            valid=true;row.pdfSource=host(attachment.getField('url'))||events.filter(e=>e.pdf&&Number(e.status)>=200&&Number(e.status)<300).at(-1)?.host||row.pdfSource;break;
          }
        }
        row.pdfSource=events.filter(e=>e.pdf).at(-1)?.host||row.pdfSource||'unresolved';
        row.httpStatus=[...new Set(events.map(e=>`${e.host}: ${e.status}`))].join('; ')||'No PDF HTTP candidate resolved';
        if(outcome?.hasPDF&&valid&&events.some(e=>Number(e.status)>=200&&Number(e.status)<300)) {row.result='PASS';row.httpStatus+='; verified %PDF-';}
        else if(outcome?.hasPDF&&!valid)row.httpStatus+='; attachment is not a PDF';
        else if(outcome?.verificationRequired)row.httpStatus+='; ACM manual browser verification needed (403 does not prove a paywall)';
        else if(subscriptionHost) {row.result='MANUAL_INSTITUTIONAL_TEST_REQUIRED';row.pdfSource=subscriptionHost;row.httpStatus+='; publisher explicitly marks subscription content; existing resolver found no public PDF';}
        else row.httpStatus+='; existing resolver did not obtain a PDF';
      }
      catch(e) {
        // Diagnostics contain status/host only: never serialize HTTP responses or auth data.
        row.httpStatus=[...new Set(events.map(e=>`${e.host}: ${e.status}`))].join('; ')||String(e.message).replace(/https?:\/\/\S+/g,u=>host(u)).slice(0,200);
      }
      finally {
        if(pane&&selectedOverride)pane.getSelectedItems=selectedOverride;
        if(imported?.id)await Zotero.Items.erase(imported.id);
        ui.lastImport=null;
        await IOUtils.writeJSON(reportPath,report);
      }
    }
  }
  finally {Zotero.HTTP.request=originalRequest;Zotero.HTTP.download=originalDownload;}
  return report;
}
