/* Offline fixtures, run with Zotero's DOMParser; no live network required. */
async function runSearch4PaperSystemsFixtures(fixtures) {
  const a=Search4PaperSystemsSources,c=Search4PaperCore;let assertions=0;
  const check=(ok,msg)=>{assertions++;if(!ok)throw Error(msg);};
  const throws=(fn,msg)=>{let failed=false;try{fn();}catch{failed=true;}check(failed,msg);};
  const lists=[];
  for(const [name,file] of [['FAST','fast'],['NSDI','nsdi'],['OSDI','osdi'],['USENIX Security','security']]){
    const config=c.SYSTEMS_SOURCES[name][2025];
    const p=a.parseUSENIX(fixtures[file+'-2025.html'],name,2025,config.sourceURL,{mode:'final-program',urls:[config.sourceURL]});
    check(p.length===2,'only USENIX research papers '+name);check(p.every(p=>p.abstract),'abstracts '+name);
    lists.push([name,p]);
  }
  const fast=a.completeUSENIX(lists[0][1][0],fixtures['usenix-detail.html']);
  check(fast.authors.length===4&&fast.authors[0]==='Jing Liu','citation authors');
  check(fast.pdfURL==='https://www.usenix.org/system/files/fast25-liu-jing.pdf'&&fast.pages==='1-18','official PDF/pages');
  const braces=fixtures['usenix-detail.html'].replace('Fast, Transparent','{Fast}, Transparent');
  check(a.completeUSENIX(lists[0][1][0],braces).title===fast.title,'BibTeX protective braces');
  throws(()=>a.completeUSENIX(lists[0][1][0],fixtures['usenix-detail.html'].replace('content="2025"','content="2015"')),'reject another publication year');
  const missing={...fast,title:"Gotta Detect 'Em All: Fake Base Station and Multi-Step Attack Detection in Cellular Networks",detailURL:'https://www.usenix.org/conference/usenixsecurity25/presentation/mubasshir',authors:['Kazi Samin Mubasshir','Imtiaz Karim','Elisa Bertino']};
  const completed=a.completeUSENIX(missing,fixtures['usenix-no-citation.html']);
  check(completed.pdfURL==='https://www.usenix.org/system/files/usenixsecurity25-mubasshir.pdf','missing citation tags: official final paper only, not appendix/slides');
  check(completed.authors.length===3&&completed.pages==='', 'missing citation tags preserve list authors and leave missing pages empty');
  const ccs=a.parseCCS(fixtures['ccs-2025.json'],2025);check(ccs.length===2&&ccs[0].cycles[0]==='firstCycle'&&ccs[1].cycles[0]==='secondCycle','CCS two cycles');
  check(ccs[0].title==='Split Unlearning'&&ccs[0].authors[0]==='Yanna Jiang'&&ccs[0].doi,'CCS clean title/authors/DOI');
  const duplicate=JSON.parse(fixtures['ccs-2025.json']);duplicate.secondCycle.push(duplicate.firstCycle[0]);duplicate.posters=[{title:'Not research'}];
  const merged=a.parseCCS(duplicate,2025);check(merged.length===2&&merged[0].cycles.length===2,'CCS cycle dedup and ignore posters');
  delete duplicate.firstCycle;throws(()=>a.parseCCS(duplicate,2025),'CCS missing cycle fails');
  lists.push(['CCS',ccs]);
  const ndss=a.parseNDSS(fixtures['ndss-2025.html'],2025);check(ndss.length===2&&ndss[0].authors[0]==='Tongxin Wei','NDSS title/authors');
  check(ndss[0].sourceMetadata.cycles.length===2,'NDSS aggregate cycle provenance');
  throws(()=>a.parseNDSS(fixtures['ndss-2025.html'].replace('(1)','(99)'),2025),'NDSS incomplete cycle count fails');
  const ndoc=new DOMParser().parseFromString(fixtures['ndss-2025.html'],'text/html');ndoc.querySelector('.pt-cv-wrapper').appendChild(ndoc.querySelector('.pt-cv-content-item').cloneNode(true));
  check(a.parseNDSS(ndoc.documentElement.outerHTML,2025).length===2,'NDSS de-duplicate');lists.push(['NDSS',ndss]);
  const sig=a.parseSIGCOMM(fixtures['sigcomm-2025.html'],2025);check(sig.length===1&&sig[0].authors.length===16,'SIGCOMM full papers only, grouped authors');lists.push(['SIGCOMM',sig]);
  const fse=a.parseFSE(fixtures['fse-2025.html'],2025);check(fse.length===2&&fse.every(p=>p.track==='Research Papers'&&p.doi),'FSE only Research Papers and DOI');lists.push(['FSE',fse]);
  throws(()=>a.parseFSE(fixtures['fse-2025.html'].replaceAll('Research Papers','Industry Papers'),2025),'FSE wrong track fails');
  // Simulate a not-yet-published final Security program and two official homepage-linked cycles.
  const conf=c.SYSTEMS_SOURCES['USENIX Security'][2025],base=conf.sourceURL.replace(/technical-sessions$/,'');
  const cycles=[base+'cycle1-accepted-papers',base+'cycle2-accepted-papers'];
  const home=cycles.map(u=>`<a href="${u}">Accepted papers</a>`).join('')+'<a href="https://example.org/cycle3-accepted-papers">Unrelated</a>';
  check(a.securityCycleURLs(home,2025).length===2,'only same conference official cycles');
  const security=lists[3][1],requests=[];
  const detail=p=>`<meta name="citation_title" content="${p.title.replace(/&/g,'&amp;').replace(/"/g,'&quot;')}">`+p.authors.map(n=>`<meta name="citation_author" content="${n}">`).join('');
  const accepted=await a.fetchAccepted('USENIX Security',2025,{request:async url=>{
    requests.push(url);if(url===conf.sourceURL)throw Object.assign(Error('unpublished'),{status:404});
    if(url===base)return home;if(cycles.includes(url))return fixtures['security-2025.html'];
    return detail(security.find(p=>p.detailURL===url));
  }});
  check(accepted.length===2&&accepted.every(p=>p.cycles.length===2),'Security aggregate cycles and de-duplicate');
  check(accepted[0].sourceMetadata.mode==='accepted-cycles'&&accepted[0].sourceMetadata.urls.join()===cycles.join()&&accepted[0].retrievalWarning,'Security provenance is acceptance lists, not final proceedings');
  let failed=false;try{await a.fetchAccepted('USENIX Security',2025,{request:async()=>'<html>Malformed</html>'});}catch{failed=true;}check(failed,'malformed final page must not silently fall back');
  for(const [name,papers] of lists){
    const meta={schemaVersion:1,year:2025,venueID:c.venueID(name,2025),fetchedAt:new Date().toISOString(),paperCount:papers.length,papers};
    check(c.validateMetadata(JSON.parse(JSON.stringify(meta)),2025,name).paperCount===papers.length,'cache roundtrip '+name);
    check(!!c.matchPaper(papers[0],{terms:[papers[0].title],operator:'AND',fields:['title']}),'known title searchable '+name);
    check(papers.every(p=>typeof p.abstract==='string'&&typeof p.keywords==='string'&&typeof p.tldr==='string'),'missing optional metadata '+name);
    check(c.identities({extra:`Source ID: ${papers[0].source}:${papers[0].id}`}).has(`source:${papers[0].source}:${papers[0].id}`),'repeat import identity '+name);
  }
  return {ok:true,assertions};
}
