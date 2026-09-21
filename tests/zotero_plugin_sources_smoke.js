/* Run only in an explicitly selected, isolated Zotero profile/data directory.
 * Uses the installed XPI and real official sources. Imports one item per conference,
 * including a repeated import, into a new test collection. Does not download PDFs.
 */
async function runSearch4PaperOfficialSourcesSmoke({ expectedDataDir, reportPath,
  cases = [['AAAI', 2025], ['ACL', 2025], ['EMNLP', 2025], ['CVPR', 2025], ['ICCV', 2025], ['ECCV', 2024]], refresh = true }) {
  const assert = (condition, message) => { if (!condition) throw new Error(message); };
  const report = { zotero: Zotero.version, searches: [] };
  let win, ui, originalRequest;
  try {
    assert(expectedDataDir && Zotero.DataDirectory.dir === expectedDataDir, 'Use an explicitly selected isolated data directory');
    document.getElementById('search4paper-open').doCommand();
    for (let i = 0; i < 100; i++) {
      win = [...Services.wm.getEnumerator(null)].find(w => w.location.href === 'chrome://search4paper/content/search.xhtml');
      if (win?.Search4PaperUI && win.document.readyState === 'complete') break;
      await Zotero.Promise.delay(100);
    }
    assert(win?.Search4PaperSources, 'Install the XPI containing official-source adapters');
    ui = win.Search4PaperUI; originalRequest = ui.request;
    const core = win.Search4PaperCore;
    const change = (id, value) => {
      ui.$(id).value = value;
      ui.$(id).dispatchEvent(new win.Event('input', { bubbles: true }));
    };
    const click = element => {
      const r = element.getBoundingClientRect(), x = r.x + r.width / 2, y = r.y + r.height / 2;
      win.windowUtils.sendMouseEvent('mousemove', x, y, 0, 0, 0);
      win.windowUtils.sendMouseEvent('mousedown', x, y, 0, 1, 0);
      win.windowUtils.sendMouseEvent('mouseup', x, y, 0, 1, 0);
    };
    const run = action => ui.operation(action);
    const offline = async () => { throw new Error('Fixture: no retrieval network allowed'); };
    const parent = new Zotero.Collection();
    parent.libraryID = Zotero.Libraries.userLibraryID;
    parent.name = 'search4paper official sources test ' + Date.now();
    await parent.saveTx();
    report.parentID = parent.id;
    ui.refreshCollections();
    for (const [conference, year] of cases) {
      change('conference', conference); change('year', String(year));
      change('terms', 'anomaly detection; time series'); change('operator', 'AND');
      for (const field of core.FIELDS) ui.$('field-' + field).checked = true;
      report.active = { conference, year, requests: 0, started: new Date().toISOString() };
      ui.request = async function (url, signal, type) {
        report.active.requests++; report.active.url = url;
        await IOUtils.writeJSON(reportPath, report);
        return originalRequest.call(this, url, signal, type);
      };
      await run(signal => ui.fetchPapers(signal, refresh));
      assert(ui.loadedConference === conference && ui.loadedYear === year && ui.papers.length > 0, ui.$('status').textContent);
      const path = PathUtils.join(expectedDataDir, 'search4paper', conference, `${year}.json`);
      const saved = core.validateMetadata(await IOUtils.readJSON(path), year, conference);
      const entry = { conference, year, papers: saved.paperCount, requests: report.active.requests,
        missingAbstract: saved.papers.filter(p => !p.abstract.trim()).length,
        defaultAND: ui.candidates.length, candidateIDs: ui.candidates.map(p => p.id) };
      const originalFile = await IOUtils.readUTF8(path);
      ui.request = offline;
      await run(signal => ui.fetchPapers(signal));
      assert(JSON.stringify(entry.candidateIDs) === JSON.stringify(ui.candidates.map(p => p.id)), 'Offline candidates must match');
      assert(ui.$('coverage').textContent.includes('本地名单'), 'Show that local metadata was used');
      await run(signal => ui.fetchPapers(signal, true));
      assert(ui.$('status').textContent.includes('no retrieval network'), 'A refresh failure must be explicit');
      assert(await IOUtils.readUTF8(path) === originalFile, 'A failed refresh must preserve the complete saved metadata');
      await run(signal => ui.fetchPapers(signal));
      if (!ui.candidates.length) {
        change('terms', ui.papers[0].title);
        await run(signal => ui.filterPapers(signal));
      }
      assert(ui.candidates.length > 0, 'Have a real paper for preview/import');
      const selected = ui.candidates[0];
      assert(ui.$('abstract').textContent === (selected.abstract || '来源未提供摘要。') && ui.$('evidence').children.length > 0, 'Show original abstract and match evidence');
      win.focus();
      await new Promise(resolve => win.requestAnimationFrame(resolve));
      click(ui.$('results').querySelector('input[type=checkbox]'));
      assert(ui.selected.has(selected.id) && !ui.$('import').disabled, 'Actual checkbox selection must enable import');
      ui.$('collection').value = String(parent.id);
      ui.$('new-collection').value = `${conference} ${year}`;
      await run(signal => ui.importPapers(signal));
      const result = ui.lastImport?.results[0];
      assert(result?.item && result.status !== 'failed', ui.$('status').textContent);
      const item = result.item, collectionID = ui.lastImport.collectionID;
      assert(item.inCollection(collectionID), 'Import into the requested child collection');
      assert(item.getField('proceedingsTitle') === (selected.publicationTitle || `${core.CONFERENCE_NAMES[conference]} (${conference} ${year})`), 'Correct conference bibliography');
      assert(item.getField('url') === selected.url && item.getField('DOI') === selected.doi, 'Keep source URL and DOI');
      if (core.DATABASE_SOURCES[conference] || core.SYSTEMS_SOURCES[conference]) {
        assert(item.itemTypeID === Zotero.ItemTypes.getID('conferencePaper'), 'New sources import as conferencePaper');
        assert(item.getField('conferenceName') === core.CONFERENCE_NAMES[conference], 'New source conference name');
        assert(item.getCreators().map(c => c.lastName).join(';') === selected.authors.join(';'), 'New source authors');
        assert(item.getField('pages') === (selected.pages || ''), 'New source pages');
        assert(item.getField('abstractNote') === selected.abstract, 'New source original abstract');
      }
      if (conference === 'ICDE') {
        assert(item.itemTypeID === Zotero.ItemTypes.getID('conferencePaper') && item.getField('conferenceName') === 'IEEE International Conference on Data Engineering', 'ICDE type and conferenceName');
        assert(item.getField('extra').includes(`Official URL: ${selected.sourceURL}`), 'Keep ICDE official source even when the item URL is IEEE');
        assert(item.getCreators().map(c => c.lastName).join(';') === selected.authors.join(';'), 'ICDE authors preserved');
      }
      const noteCount = item.getNotes().length;
      await run(signal => ui.importPapers(signal));
      assert(ui.lastImport.results[0].status === 'existing' && ui.lastImport.results[0].item.id === item.id, 'Repeated import must reuse the same item');
      assert(item.getNotes().length === noteCount, 'Do not duplicate provenance notes');
      entry.importedID = item.id; entry.collectionID = collectionID;
      report.searches.push(entry);
      await IOUtils.writeJSON(reportPath, report);
    }
    report.ok = true; delete report.active;
  }
  catch (error) { report.ok = false; report.error = String(error); report.stack = error.stack; }
  finally { if (ui) ui.request = originalRequest; }
  await IOUtils.writeJSON(reportPath, report);
  return report;
}

// Synthetic protocol fixtures, parsed by Zotero's real DOMParser. No library writes or network.
async function runSearch4PaperSourceFixtureSmoke({ expectedDataDir, reportPath }) {
  const report = { checks: [] };
  const assert = (condition, message) => { if (!condition) throw new Error(message); };
  const rejects = async (action, pattern) => {
    try { await action(); } catch (error) { assert(pattern.test(String(error)), String(error)); return; }
    throw new Error('Expected an explicit error');
  };
  try {
    assert(expectedDataDir && Zotero.DataDirectory.dir === expectedDataDir, 'Use isolated data');
    const scope = { DOMParser, URL, URLSearchParams };
    for (const name of ['core', 'sources']) Services.scriptloader.loadSubScriptWithOptions(
      `chrome://search4paper/content/${name}.js`, { target: scope, ignoreCache: true });
    const core = scope.Search4PaperCore, sources = scope.Search4PaperSources;
    const article = '<paper id="1"><title>Time <fixed-case>Series</fixed-case> &amp; Anomalies</title>'
      + '<author><first>Ada</first><last>Example</last><affiliation>Not part of author name</affiliation></author>'
      + '<abstract>Detect <b>anomalies</b> in time series.</abstract><doi>10.1/example</doi><pdf/></paper>';
    const anthology = (conference, track) => `<collection id="2025.${conference}"><volume id="${track}"><meta>`
      + `<year>2025</year><venue>${conference}</venue></meta><frontmatter/>`
      + article + '</volume><volume id="demo">' + article + '</volume></collection>';
    for (const [conference, track] of [['ACL', 'long'], ['EMNLP', 'main']]) {
      const raw = anthology(conference.toLowerCase(), track);
      const papers = sources.parseAnthology(raw, conference, 2025);
      assert(papers.length === 1 && papers[0].title === 'Time Series & Anomalies', 'Parse inline XML and exclude other volumes/front matter');
      assert(papers[0].authors.join() === 'Ada Example', 'Do not include author affiliations');
      assert(papers[0].pdfURL.endsWith(`${papers[0].id}.pdf`), 'Honor implicit Anthology PDF identifiers');
      await rejects(() => sources.parseAnthology(raw, conference, 2024), /其他会议或年份/);
      await rejects(() => sources.parseAnthology(raw.slice(0, -20), conference, 2025), /XML/);
      report.checks.push(`${conference}: XML, scope, authors, PDF, wrong year, truncation`);
    }
    const oai = (verb, body, token = '') => `<OAI-PMH xmlns="http://www.openarchives.org/OAI/2.0/"><${verb}>${body}${token}</${verb}></OAI-PMH>`;
    const set = '<set><setSpec>AAAI:AI25-1</setSpec><setName>AAAI Technical Track on Machine Learning</setName></set>';
    const excludedSet = '<set><setSpec>AAAI:AI25-50</setSpec><setName>AAAI Student Abstract and Poster Program</setName></set>';
    const record = id => `<record><header><identifier>article/${id}</identifier></header><metadata><dc:dc xmlns:dc="http://purl.org/dc/elements/1.1/">`
      + `<dc:title>Time Series Anomaly Detection ${id}</dc:title><dc:creator>Example, Ada</dc:creator>`
      + '<dc:description>&lt;p&gt;An &lt;b&gt;abstract&lt;/b&gt;.&lt;/p&gt;</dc:description>'
      + `<dc:identifier>https://ojs.aaai.org/index.php/AAAI/article/view/${id}</dc:identifier>`
      + `<dc:relation>https://ojs.aaai.org/index.php/AAAI/article/view/${id}/900</dc:relation>`
      + '<dc:source>Proceedings of the AAAI Conference on Artificial Intelligence; Vol. 39 No. 1: AAAI-25 Technical Tracks 1; 1-8</dc:source></dc:dc></metadata></record>';
    const token = (value, cursor, total) => `<resumptionToken cursor="${cursor}" completeListSize="${total}">${value}</resumptionToken>`;
    const responses = [oai('ListSets', set + excludedSet),
      oai('ListRecords', record('1'), token('next', 0, 2)), oai('ListRecords', record('2'), token('', 1, 2))];
    let urls = [], index = 0;
    const aaai = await sources.fetchAccepted(2025, { conference: 'AAAI', request: async url => {
      urls.push(url); return responses[index++];
    } });
    assert(aaai.length === 2 && urls.length === 3 && urls[1].includes('AI25-1') && !urls.some(url => url.includes('AI25-50')), 'Select technical groups and follow all pages');
    assert(aaai[0].abstract === 'An abstract.' && aaai[0].pdfURL.includes('/download/1/900'), 'Normalize OAI HTML and PDF links');
    const special = new DOMParser().parseFromString(oai('ListRecords', record('3').replace('AAAI-25 Technical Tracks 1',
      'AAAI-25 Special Track on AI for Social Impact, Senior Member Presentations, New Faculty Highlights, Journal Track')), 'application/xml');
    assert(sources.parseAAAIRecord(special.querySelector('record'), 2025).id === '3', 'Technical groups can share an issue with excluded tracks');
    for (const [last, pattern] of [[oai('ListRecords', '', token('', 1, 2)), /提前结束/],
      [oai('ListRecords', record('1'), token('', 1, 2)), /重复/],
      [oai('ListRecords', record('2').replace('AAAI-25', 'AAAI-24'), token('', 1, 2)), /其他年份/],
      [oai('ListRecords', record('2'), token('', 1, 3)), /总数发生变化/]]) {
      index = 0;
      await rejects(() => sources.fetchAccepted(2025, { conference: 'AAAI', request: async () =>
        [responses[0], responses[1], last][index++] }), pattern);
    }
    report.checks.push('AAAI: technical groups, pagination, HTML, duplicate, count, wrong-year failures');
    const cvf = '<dl><dt class="ptitle"><a href="/content/CVPR2025/html/Author_Test_CVPR_2025_paper.html">Time Series Anomaly Detection</a></dt>'
      + '<dd><form><input name="query_author" value="Ada Example"></form></dd>'
      + '<dd><a href="/content/CVPR2025/papers/Author_Test_CVPR_2025_paper.pdf">pdf</a><div class="bibref">@InProceedings{key}</div></dd></dl>';
    const papers = sources.parseCVFIndex(cvf, 'CVPR', 2025);
    assert(papers.length === 1 && papers[0].bibtex.includes('@InProceedings') && papers[0].authors[0] === 'Ada Example', 'CVF proceedings parsing');
    await rejects(() => sources.parseCVFIndex(cvf.replaceAll('CVPR2025', 'CVPR2025_workshop'), 'CVPR', 2025), /研讨会/);
    index = 0;
    const program = { count: 2, next: null, results: [
      { name: papers[0].title, abstract: 'A real abstract.' }, { name: papers[0].title, abstract: 'A real abstract.' }] };
    const cvfPapers = await sources.fetchAccepted(2025, { conference: 'CVPR', request: async () => [cvf, program][index++] });
    assert(cvfPapers.length === 1 && index === 2 && cvfPapers[0].abstract === 'A real abstract.', 'Repeated oral/poster entries must not duplicate proceedings papers');
    index = 0;
    await rejects(() => sources.fetchAccepted(2025, { conference: 'CVPR', request: async () =>
      [cvf, { ...program, count: 3 }][index++] }), /不完整/);
    index = 0;
    const detail = `<div id="papertitle">${papers[0].title}</div><div id="abstract">The proceedings abstract.</div>`;
    const detailed = await sources.fetchAccepted(2025, { conference: 'CVPR', request: async () =>
      [cvf, { count: 1, next: null, results: [{ name: 'Different program title', abstract: 'Do not assign me' }] }, detail][index++] });
    assert(index === 3 && detailed[0].abstract === 'The proceedings abstract.', 'Read paper page when program title cannot match exactly');
    index = 0;
    const changedTitle = `<meta name="citation_pdf_url" content="${papers[0].pdfURL}">`
      + detail.replace(papers[0].title, 'The same paper with a corrected official title');
    const corrected = await sources.fetchAccepted(2025, { conference: 'CVPR', request: async () =>
      [cvf, { count: 0, next: null, results: [] }, changedTitle][index++] });
    assert(corrected[0].abstract === 'The proceedings abstract.', 'A matching PDF identifies a paper despite official title corrections');
    index = 0;
    await rejects(() => sources.fetchAccepted(2025, { conference: 'CVPR', request: async () =>
      [cvf, { count: 0, next: null, results: [] }, changedTitle.replace(papers[0].pdfURL, 'https://example.com/other.pdf')][index++] }), /标题不一致/);
    report.checks.push('CVF: proceedings scope, oral/poster duplicates, program count, exact-title mismatch');
    for (const [conference, prefix] of [['ACL', 'P'], ['EMNLP', 'D']]) {
      const old = anthology(conference.toLowerCase(), conference === 'ACL' ? 'long' : 'main')
        .replace(`2025.${conference.toLowerCase()}`, `${prefix}19`).replace('<year>2025</year>', '<year>2019</year>')
        .replace(`volume id="${conference === 'ACL' ? 'long' : 'main'}"`, 'volume id="1"');
      const parsed = sources.parseAnthology(old, conference, 2019);
      assert(parsed.length === 1 && parsed[0].id === `${prefix}19-1001`, 'Legacy IDs use volume digit plus three-digit paper number');
      const retrieved = await sources.fetchAccepted(2019, {conference, request: async url => {
        assert(url.endsWith(`/${prefix}19.xml`), 'Use old collection XML'); return old;
      }});
      assert(retrieved[0].pdfURL.endsWith(`${prefix}19-1001.pdf`), 'Legacy PDF identity');
    }
    index = 0;
    const oldSet = set.replace('AI25-1', 'ML').replace('AI25', 'ML');
    const legacyRecord = record('1').replace('AAAI-25 Technical Tracks 1', 'Vol. 31 No. 1 (2017): Thirty-First AAAI Conference on Artificial Intelligence');
    const archive = '<div class="obj_issue_summary"><h2><a class="title" href="https://ojs.aaai.org/index.php/AAAI/issue/view/1">AAAI-17</a> Vol. 31 No. 1 (2017)</h2></div>';
    const issue = '<div class="sections"><div class="section"><h2>AAAI Technical Track on Machine Learning</h2><h3 class="title">'
      + '<a href="https://ojs.aaai.org/index.php/AAAI/article/view/1">Paper</a></h3></div>'
      + '<div class="section"><h2>AAAI Student Abstract and Poster Program</h2><h3 class="title"><a href="https://ojs.aaai.org/index.php/AAAI/article/view/2">Excluded</a></h3></div></div>';
    const oldAAAI = await sources.fetchAccepted(2017, {conference:'AAAI', request: async url => {
      assert(!url.includes('verb=ListRecords'), 'Legacy proceedings do not traverse unrelated multi-year OAI pages');
      return [oai('ListSets', oldSet + excludedSet), archive, issue, oai('GetRecord', legacyRecord)][index++];
    }});
    assert(oldAAAI.length === 1 && oldAAAI[0].id === '1', 'Old issue sections select technical papers and validate proceedings year');
    index = 0;
    await rejects(() => sources.fetchAccepted(2017, {conference:'AAAI', request: async () =>
      [oai('ListSets', oldSet), archive, issue, oai('GetRecord', record('1'))][index++]}), /其他年份/);
    const oldCVF = cvf.replaceAll('CVPR2025', 'cvpr_2013').replaceAll('/content/', 'content_')
      .replaceAll('query_author', 'query');
    const oldDetail = detail;
    index = 0;
    const early = await sources.fetchAccepted(2013, {conference:'CVPR', request: async () => [oldCVF, oldDetail][index++]});
    assert(index === 2 && early[0].abstract === 'The proceedings abstract.', 'Early CVF reads detail without requesting program JSON');
    assert(early[0].pdfURL.startsWith('https://openaccess.thecvf.com/content_cvpr_2013/papers/'), 'Old relative PDF paths resolve against index origin');
    const dated = cvf.replaceAll('CVPR2025', 'CVPR2019');
    index = 0;
    const dates = await sources.fetchAccepted(2019, {conference:'CVPR', request: async url => {
      assert(!url.includes('day=all'), 'Early date index must not request day=all');
      return ['<a href="CVPR2019.py?day=2019-06-18">Day 1</a>', dated, oldDetail][index++];
    }});
    assert(index === 3 && dates.length === 1, 'Follow official dated proceedings pages');
    index = 0;
    await rejects(() => sources.fetchAccepted(2019, {conference:'CVPR', request:async () =>
      ['<a href="CVPR2019.py?day=2019-06-18">Day 1</a>', '<html>No papers returned</html>'][index++]}), /名单不完整/);
    const ecva = '<dt class="ptitle"><a href="papers/eccv_2024/papers_ECCV/html/4_ECCV_2024_paper.php">Time Series Anomaly Detection</a></dt>'
      + '<dd>Ada Example*, Bob Researcher</dd><dd><a href="papers/eccv_2024/papers_ECCV/papers/00004.pdf">pdf</a>'
      + '<a href="https://link.springer.com/chapter/10.1007/example">DOI</a></dd>';
    index = 0;
    const eccv = await sources.fetchAccepted(2024, {conference:'ECCV', request:async () =>
      [ecva + ecva.replaceAll('2024','2022'), detail][index++]});
    assert(eccv.length === 1 && eccv[0].authors[0] === 'Ada Example' && eccv[0].doi === '10.1007/example', 'ECVA selects exact year, author footnotes and DOI');
    assert(eccv[0].abstract === 'The proceedings abstract.', 'ECVA detail abstract');
    const oldECVA = sources.parseECVAIndex(ecva.replaceAll('2024','2018').replace('Ada Example*, Bob Researcher', 'Example, Ada and Researcher, Bob'), 2018);
    assert(oldECVA[0].authors.join(';') === 'Example, Ada;Researcher, Bob', '2018 uses and between authors, commas inside names');
    const commas = sources.parseECVAIndex(ecva.replaceAll('2024','2020').replace('Ada Example*, Bob Researcher', 'Ada Example,, Bob Researcher'), 2020);
    assert(commas[0].authors.length === 2, 'Observed 2020 doubled separators do not create empty authors');
    report.checks.push('Historical ACL/EMNLP IDs, legacy AAAI issue groups and record years, early CVF dates and abstracts, ECCV year isolation');
    for (const conference of ['CRYPTO', 'EUROCRYPT', 'ASIACRYPT']) {
      const row = (id, invited = false) => `<div class="row"><div>2024</div><div>${conference}</div><div>`
        + `<span class="pub-title"><a href="paper.php?pubkey=${id}">A cryptographic paper</a></span>`
        + (invited ? '<div title="Invited talk/paper">Invited talk</div>' : '')
        + '<div class="authors"><span class="author">Ada Example</span></div><div class="abstract">Index abstract.</div></div></div>';
      const indexHTML = `<h3>Papers from ${conference} 2024</h3>` + row('123') + row('456', true);
      const detailHTML = '<h3>A cryptographic paper</h3><table><tr><th>Conference:</th><td>'
        + `<a href="conf.php?year=2024&amp;venue=${conference.toLowerCase()}">${conference} 2024</a></td></tr>`
        + '<tr><th>Download:</th><td><a href="https://doi.org/10.1007/test">DOI</a>'
        + '<a href="https://eprint.iacr.org/2024/123.pdf">Paper</a></td></tr>'
        + '<tr><th>Presentation:</th><td><a href="https://iacr.org/slides.pdf">Slides</a></td></tr>'
        + '<tr><th>Abstract:</th><td>Official abstract.</td></tr></table><div class="bibtex"><pre>@inproceedings{test}</pre></div>';
      index = 0;
      const fetched = await sources.fetchAccepted(2024, {conference, request:async () => [indexHTML, detailHTML][index++]});
      assert(index === 2 && fetched.length === 1 && fetched[0].source === 'iacr', 'Exclude invited talks and read each official detail');
      assert(fetched[0].doi === '10.1007/test' && fetched[0].abstract === 'Official abstract.' && fetched[0].pdfURL === 'https://eprint.iacr.org/2024/123.pdf', 'IACR DOI, abstract and paper link');
      const noPDF = sources.completeIACRPaper(detailHTML.replace('<a href="https://eprint.iacr.org/2024/123.pdf">Paper</a>', ''), fetched[0], conference, 2024);
      assert(noPDF.pdfURL === '', 'Do not confuse presentation slides with the paper');
      await rejects(() => sources.parseIACRIndex(indexHTML, conference, 2023), /其他会议、年份/);
      await rejects(() => sources.parseIACRIndex(indexHTML.replace('<div>2024</div>', '<div>2023</div>'), conference, 2024), /混入/);
      await rejects(() => sources.completeIACRPaper(detailHTML.replace('year=2024', 'year=2023'), fetched[0], conference, 2024), /身份不一致/);
      index = 0;
      await rejects(() => sources.fetchAccepted(2024, {conference, request:async () =>
        [indexHTML + row('123'), detailHTML, detailHTML][index++]}), /重复/);
      const noAbstract = sources.completeIACRPaper(detailHTML.replace('<tr><th>Abstract:</th><td>Official abstract.</td></tr>', ''), {...fetched[0], abstract:''}, conference, 2024);
      assert(noAbstract.abstract === '', 'Missing historical abstracts stay empty');
    }
    report.checks.push('IACR: three venues, invited-talk exclusion, DOI, paper-versus-slides links, wrong year, duplicate IDs and missing abstracts');
    const icdeRow = (number, links = '') => `<li class="paper-item"><div class="number-column">${number}</div><div class="title">FaScalSQL: A Fast SQL Engine</div><div class="authors"><span class="author-name">Ada Example*</span><span class="affiliation">(University)</span><span class="author-name">Bo Li</span><span class="affiliation">(Institute)</span></div>${links}</li>`;
    const icdePage = rows => `<html><head><title>Accepted Research Papers</title></head><body><h2>Accepted Research Papers for ICDE 2026</h2><ul class="paper-list">${rows}</ul></body></html>`;
    const icdeRaw = icdePage(icdeRow('01'));
    const icde = sources.parseICDE(icdeRaw, 2026);
    assert(icde.length === 1 && icde[0].id === '2026:1' && icde[0].authors.join(';') === 'Ada Example;Bo Li', 'ICDE author names exclude affiliation and corresponding-author marks');
    assert(['abstract','keywords','tldr','doi','pdfURL','ieeeURL','detailURL'].every(key => icde[0][key] === ''), 'ICDE never invents absent metadata');
    const links = '<a href="https://doi.org/10.1109/example.1">DOI</a><a href="https://ieeexplore.ieee.org/document/123">IEEE</a><a href="https://example.org/paper.pdf">PDF</a>';
    const linked = sources.parseICDE(icdePage(icdeRow('1', links)).replace('>FaScalSQL: A Fast SQL Engine</div>', '><a href="detail/1">FaScalSQL: A Fast SQL Engine</a></div>'), 2026)[0];
    assert(linked.doi === '10.1109/example.1' && linked.ieeeURL.endsWith('/document/123') && linked.url === linked.ieeeURL && linked.pdfURL.endsWith('/paper.pdf') && linked.detailURL.endsWith('/research-papers/detail/1'), 'Preserve only explicitly supplied ICDE links');
    let icdeCalls = [];
    const redirected = await sources.fetchAccepted(2026, {conference:'ICDE', request: async url => {
      icdeCalls.push(url);
      return icdeCalls.length === 1 ? '<html><a href="accepted-papers.html">Accepted Research Papers</a></html>' : icdeRaw;
    }});
    assert(icdeCalls.join('|') === 'https://ieee-icde.org/2026/research-papers/|https://icde2026.github.io/accepted-papers.html' && redirected[0].sourceURL === icdeCalls[1], 'Follow the observed official 2026 navigation only');
    for (const [raw, year] of [[icdeRaw,2025], [icdePage(''),2026], [icdePage(icdeRow('1')+icdeRow('3')),2026], [icdeRaw.replace('class="title"','class="unknown"'),2026], [icdeRaw.replaceAll('author-name','unknown'),2026], [icdeRaw.slice(0,-7),2026]]) {
      await rejects(() => sources.parseICDE(raw, year), /ICDE/);
    }
    const legacy = '<html><head><title>Research Papers – IEEE ICDE 2025</title></head><body><h1>Research Papers</h1><p>Session Chair: Not an author</p><p>404 | Real Research Paper</p><p>Ada Example (University (Lab))*; Bo Li (Institute)</p></body></html>';
    const old = sources.parseICDE(legacy, 2025);
    assert(old.length === 1 && old[0].id === '2025:404' && old[0].authors.join(';') === 'Ada Example;Bo Li', 'Legacy numbered research papers only, with nested affiliation parentheses');
    await rejects(() => sources.parseICDE(legacy.replace('<p>Ada Example (University (Lab))*; Bo Li (Institute)</p>',''),2025), /ICDE/);
    report.checks.push('ICDE: official navigation, numbered and structured lists, missing fields, explicit links, authors, malformed/empty/truncated/wrong-year failures');
    const controller = new AbortController(); let calls = 0;
    await rejects(() => sources.fetchAccepted(2025, { conference: 'AAAI', signal: controller.signal,
      request: async () => { calls++; controller.abort(); return responses[0]; } }), /Abort/);
    assert(calls === 1, 'Cancellation stops before a second request');
    for (const [conference, year, pattern] of [['ICCV', 2026, /奇数年/], ['ICCV', 2011, /2013/],
      ['CVPR', 2012, /2013/], ['AAAI', 2009, /2010/], ['EMNLP', 2006, /2007/], ['ECCV', 2025, /偶数年/], ['ECCV', 2016, /2018/]]) {
      await rejects(() => sources.fetchAccepted(year, { conference, request: async () => { throw new Error('Unexpected request'); } }), pattern);
    }
    report.checks.push('Cancellation and unsupported years stop before further requests');
    report.ok = true;
  }
  catch (error) { report.ok = false; report.error = String(error); report.stack = error.stack; }
  await IOUtils.writeJSON(reportPath, report);
  return report;
}
