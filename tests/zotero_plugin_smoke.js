/* Load with Services.scriptloader.loadSubScript() in an isolated Zotero 10 profile.
 * Call runSearch4PaperSmoke({ expectedDataDir, expectedIDs, reportPath }).
 * Uses the installed XPI and live OpenReview; creates test collections and items.
 */
async function runSearch4PaperSmoke({ expectedDataDir, expectedIDs, reportPath }) {
  const assert = (condition, message) => { if (!condition) throw new Error(message); };
  const report = { checks: [], zotero: Zotero.version };
  const record = async (name, details = {}) => {
    report.checks.push({ name, ...details });
    await IOUtils.writeJSON(reportPath, report);
  };
  const waitFor = async predicate => {
    const start = Date.now();
    while (!predicate()) {
      if (Date.now() - start > 180000) throw new Error('Timed out waiting for the plugin');
      await Zotero.Promise.delay(100);
    }
  };
  try {
    assert(expectedDataDir && Zotero.DataDirectory.dir === expectedDataDir, 'Use an explicitly selected isolated test data directory');
    const { AddonManager } = ChromeUtils.importESModule('resource://gre/modules/AddonManager.sys.mjs');
    const addon = await AddonManager.getAddonByID('search4paper@local');
    assert(addon?.isActive, 'XPI must be installed and active');
    document.getElementById('search4paper-open').doCommand();
    let win;
    await waitFor(() => {
      win = [...Services.wm.getEnumerator(null)].find(w => w.location.href === 'chrome://search4paper/content/search.xhtml');
      return win?.Search4PaperUI && win.document.readyState === 'complete';
    });
    const ui = win.Search4PaperUI;
    await record('installed-menu-and-window', { version: addon.version });
    ui.$('fetch').click();
    await waitFor(() => !ui.busy);
    assert(ui.papers.length > 0 && ui.query, ui.$('status').textContent);
    const ids = ui.candidates.map(paper => paper.id).sort();
    if (expectedIDs) assert(JSON.stringify(ids) === JSON.stringify([...expectedIDs].sort()), 'Candidates differ from the Python snapshot reference');
    assert(ui.$('results').children.length === Math.min(ids.length, 100), 'Candidate table must render');
    assert(ui.$('abstract').textContent === ui.candidates[0].abstract, 'Abstract preview must contain source text');
    assert(ui.$('evidence').children.length > 0, 'Preview must display match evidence');
    await record('live-fetch-search-preview', { papers: ui.papers.length, candidateIDs: ids });

    const parent = new Zotero.Collection();
    parent.libraryID = Zotero.Libraries.userLibraryID;
    parent.name = 'search4paper XPI smoke ' + Date.now();
    await parent.saveTx();
    ui.refreshCollections(); ui.$('collection').value = String(parent.id);
    ui.$('new-collection').value = 'ICML 2026 TSAD';
    ui.$('tags').value = 'time series; anomaly detection'; ui.$('include-pdf').checked = false;
    ui.$('select-page').click(); ui.$('import').click();
    await waitFor(() => !ui.busy);
    const first = ui.lastImport;
    assert(first && first.results.length === ids.length && !first.unprocessed, ui.$('status').textContent);
    assert(first.results.every(r => r.item), ui.$('status').textContent);
    const collection = Zotero.Collections.get(first.collectionID);
    assert(collection.parentID === parent.id, 'Must create an actual subcollection');
    assert(collection.getChildItems().filter(i => i.isRegularItem()).length === ids.length, 'All selected parents must belong to the destination');
    for (const { item, paper, status } of first.results) {
      if (status === 'created') {
        assert(item.getField('title') === paper.title, 'Title preserved');
        assert(item.getField('abstractNote') === paper.abstract, 'Abstract preserved');
        assert(JSON.stringify(item.getCreators().map(c => c.lastName)) === JSON.stringify(paper.authors), 'Full author names preserved');
      }
      assert(item.hasTag('time series'), 'Requested tag');
      assert(item.getNotes().length === 1, 'One provenance note');
    }
    await record('native-import-subcollection', { count: first.results.length, collectionID: collection.id });

    const edited = first.results[0].item;
    edited.setField('title', 'USER EDIT: keep this title'); await edited.saveTx();
    const before = (await Zotero.Items.getAll(parent.libraryID)).length;
    ui.$('collection').value = String(parent.id); ui.$('new-collection').value = 'Second collection';
    ui.$('import').click(); await waitFor(() => !ui.busy);
    assert(ui.lastImport.results.every(r => r.status === 'existing'), 'Repeat import must reuse all parents');
    assert((await Zotero.Items.getAll(parent.libraryID)).length === before, 'No duplicate parents or provenance notes');
    assert(edited.getField('title') === 'USER EDIT: keep this title', 'User edits must survive');
    assert(edited.inCollection(collection.id) && edited.inCollection(ui.lastImport.collectionID), 'Existing item belongs to both collections');
    await record('repeat-import-preserves-user-edits');

    for (const field of ['abstract', 'keywords', 'tldr']) ui.$('field-' + field).checked = false;
    ui.$('field-abstract').dispatchEvent(new win.Event('input', { bubbles: true }));
    assert(ui.$('import').disabled, 'Changed filters must disable stale selection import');
    ui.$('filter').click(); await waitFor(() => !ui.busy);
    assert(ui.candidates.length > 0 && ui.candidates.length < ids.length, 'Title-only search must narrow candidates');
    await record('field-filter-and-stale-selection', { titleOnly: ui.candidates.length });

    const request = ui.request;
    ui.request = async () => { throw new Error('Injected HTTP 403'); };
    ui.$('fetch').click(); await waitFor(() => !ui.busy);
    assert(!ui.papers.length && !ui.candidates.length && ui.$('import').disabled, 'Failed refresh cannot expose stale results');
    assert(ui.$('status').textContent.includes('403'), 'Network failure must be visible');
    ui.request = request;
    await record('failed-fetch-clears-stale-results');
    ui.request = async (_url, signal) => new Promise((_resolve, reject) =>
      signal.addEventListener('abort', () => reject(new Error('cancelled')), { once: true }));
    ui.$('fetch').click(); ui.$('cancel').click(); await waitFor(() => !ui.busy);
    assert(ui.$('status').textContent.includes('取消'), 'Cancellation must complete');
    ui.request = request;
    await record('cancel-in-flight-fetch');

    await addon.disable();
    assert(win.closed && !document.getElementById('search4paper-open'), 'Disable must close UI and remove menu');
    await addon.enable();
    await waitFor(() => document.getElementById('search4paper-open'));
    assert(document.querySelectorAll('#search4paper-open').length === 1, 'Enable must add exactly one menu');
    await record('disable-enable-cleanup');
    report.ok = true;
  }
  catch (error) { report.ok = false; report.error = String(error); report.stack = error.stack; }
  await IOUtils.writeJSON(reportPath, report);
  return report;
}

// Serve an actual PDF fixture over HTTPS, and compare Zotero's stored bytes with
// the fixture. This exercises native attachment APIs without mocking them.
async function runSearch4PaperPDFSmoke({ expectedDataDir, pdfURL, fixturePath, reportPath }) {
  const assert = (condition, message) => { if (!condition) throw new Error(message); };
  const report = {};
  try {
    assert(expectedDataDir && Zotero.DataDirectory.dir === expectedDataDir, 'Use an isolated test data directory');
    document.getElementById('search4paper-open').doCommand();
    await Zotero.Promise.delay(500);
    const win = [...Services.wm.getEnumerator(null)].find(w => w.location.href === 'chrome://search4paper/content/search.xhtml');
    const ui = win.Search4PaperUI;
    const paper = { id: 'search4paper-pdf-fixture-' + Date.now(), title: 'search4paper native PDF fixture',
      authors: ['Test Author'], year: 2026, venueID: 'TEST FIXTURE', abstract: 'Not a real research paper.',
      doi: '', url: 'https://example.org/search4paper-test', pdfURL, bibtex: '', evidence: [] };
    ui.candidates = [paper]; ui.selected = new Set([paper.id]); ui.page = 0;
    ui.query = { terms: ['fixture'], context: [], fields: ['title'] }; ui.filtersDirty = false;
    ui.render(); ui.preview(paper); ui.$('include-pdf').checked = true;
    ui.$('collection').value = ''; ui.$('new-collection').value = 'XPI PDF fixture';
    await ui.operation(signal => ui.importPapers(signal));
    assert(ui.lastPDFs?.[0]?.hasPDF, ui.$('status').textContent);
    const item = ui.lastImport.results[0].item;
    const attachments = await Zotero.Items.getAsync(item.getAttachments());
    assert(attachments.length === 1, 'Exactly one stored attachment');
    const actual = await IOUtils.read(await attachments[0].getFilePathAsync());
    const expected = await IOUtils.read(fixturePath);
    assert(actual.length === expected.length && actual.every((value, i) => value === expected[i]), 'Stored PDF bytes must equal the fixture');
    await ui.operation(signal => ui.importPapers(signal));
    assert(item.getAttachments().length === 1 && ui.lastPDFs[0].hasPDF, 'Repeat import must reuse the PDF');
    report.ok = true; report.bytes = actual.length; report.itemID = item.id;
  }
  catch (error) { report.ok = false; report.error = String(error); report.stack = error.stack; }
  await IOUtils.writeJSON(reportPath, report);
  return report;
}

async function runSearch4PaperImportFailureSmoke({ expectedDataDir, reportPath }) {
  const assert = (condition, message) => { if (!condition) throw new Error(message); };
  const report = {};
  try {
    assert(expectedDataDir && Zotero.DataDirectory.dir === expectedDataDir, 'Use an isolated test data directory');
    const win = [...Services.wm.getEnumerator(null)].find(w => w.location.href === 'chrome://search4paper/content/search.xhtml');
    const importer = win.Search4PaperImport;
    const libraryID = Zotero.Libraries.userLibraryID;
    const marker = 'search4paper-failure-fixture-' + Date.now();
    const paper = { id: marker, title: 'Import failure fixture', year: 2026, venueID: 'TEST FIXTURE',
      authors: ['Test Author'], doi: '', url: 'https://example.org/' + marker, abstract: '', evidence: [] };
    const options = { name: marker, query: { terms: ['fixture'], context: [], fields: ['title'] } };
    const before = (await Zotero.Items.getAll(libraryID)).length;
    const setNote = Zotero.Item.prototype.setNote;
    let failed;
    try {
      Zotero.Item.prototype.setNote = function (html) {
        if (html.includes(marker)) throw new Error('Injected provenance write failure');
        return setNote.call(this, html);
      };
      failed = await importer.papers(Zotero, [paper], options);
    }
    finally { Zotero.Item.prototype.setNote = setNote; }
    assert(failed.results[0].status === 'failed', 'Failure must be reported');
    assert((await Zotero.Items.getAll(libraryID)).length === before, 'Parent must roll back with failed note');
    assert(!Zotero.Collections.getByLibrary(libraryID, true).some(c => c.name === marker), 'New destination must roll back too');
    report.atomicRollback = true;

    for (let i = 0; i < 2; i++) {
      const duplicate = new Zotero.Item('conferencePaper'); duplicate.libraryID = libraryID;
      duplicate.setField('title', 'Ambiguous fixture ' + i);
      duplicate.setField('extra', 'OpenReview ID: ' + marker);
      await duplicate.saveTx();
    }
    const ambiguous = await importer.papers(Zotero, [paper, { ...paper, id: marker + '-next' }], options);
    assert(ambiguous.results.length === 1 && ambiguous.results[0].status === 'failed' && ambiguous.unprocessed === 1, 'Ambiguous match must stop the remaining batch');
    assert((await Zotero.Items.getAll(libraryID)).length === before + 2, 'Ambiguous match must not write anything');
    report.ambiguousMatchStopsBatch = true;

    const controller = new AbortController();
    const cancelled = await importer.papers(Zotero, [{ ...paper, id: marker + '-a' }, { ...paper, id: marker + '-b' }], {
      ...options, signal: controller.signal,
      onProgress(done) { if (done === 0) controller.abort(); }
    });
    assert(cancelled.results.every(r => r.status !== 'failed'), 'Cancellation is not an import failure');
    assert(cancelled.unprocessed >= 1, 'Cancellation must stop subsequent papers');
    report.cancelStopsBatch = true; report.ok = true;
  }
  catch (error) { report.ok = false; report.error = String(error); report.stack = error.stack; }
  await IOUtils.writeJSON(reportPath, report);
  return report;
}
