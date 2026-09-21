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
    ui.$('refresh-papers').click();
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
    ui.$('tags').value = 'time series; anomaly detection';
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
    ui.$('refresh-papers').click(); await waitFor(() => !ui.busy);
    assert(!ui.papers.length && !ui.candidates.length && ui.$('import').disabled, 'Failed refresh cannot expose stale results');
    assert(ui.$('status').textContent.includes('403'), 'Network failure must be visible');
    ui.request = request;
    await record('failed-fetch-clears-stale-results');
    ui.request = async (_url, signal) => new Promise((_resolve, reject) =>
      signal.addEventListener('abort', () => reject(new Error('cancelled')), { once: true }));
    ui.$('refresh-papers').click(); ui.$('cancel').click(); await waitFor(() => !ui.busy);
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

// Run after the live smoke test, and again after restarting Zotero. All network
// requests here are blocked or replaced with fixtures in the plugin window.
// Temporarily corrupts the isolated profile's saved list, then restores it.
async function runSearch4PaperMetadataSmoke({ expectedDataDir, expectedIDs, reportPath }) {
  const assert = (condition, message) => { if (!condition) throw new Error(message); };
  const report = { checks: [], zotero: Zotero.version };
  const record = name => report.checks.push(name);
  const waitFor = async (predicate, description) => {
    const start = Date.now();
    while (!predicate()) {
      if (Date.now() - start > 30000) throw new Error('Timed out waiting for ' + description);
      await Zotero.Promise.delay(50);
    }
  };
  const open = async () => {
    document.getElementById('search4paper-open').doCommand();
    let win;
    await waitFor(() => {
      win = [...Services.wm.getEnumerator(null)].find(w => w.location.href === 'chrome://search4paper/content/search.xhtml');
      return win?.Search4PaperUI && win.document.readyState === 'complete';
    }, 'plugin window to open');
    return win;
  };
  let win, ui, originalRequest, path, savedText;
  const createdPaths = [];
  try {
    assert(expectedDataDir && Zotero.DataDirectory.dir === expectedDataDir, 'Use an explicitly selected isolated test data directory');
    path = PathUtils.join(expectedDataDir, 'search4paper', 'ICML', '2026.json');
    savedText = await IOUtils.readUTF8(path);
    const saved = JSON.parse(savedText);
    win = await open(); ui = win.Search4PaperUI; originalRequest = ui.request;
    win.Search4PaperCore.validateMetadata(saved, 2026);
    report.paperCount = saved.paperCount; report.fetchedAt = saved.fetchedAt;
    let requests = 0;
    const offline = async () => { requests++; throw new Error('Injected offline HTTP 403'); };
    const click = async id => { ui.$(id).click(); await waitFor(() => !ui.busy, id + ': ' + ui.$('status').textContent); };
    const assertLoaded = () => {
      assert(ui.papers.length === saved.paperCount && ui.loadedYear === 2026, ui.$('status').textContent);
      assert(ui.$('coverage').textContent.includes('本地名单'), 'Local source must be visible');
      assert(ui.$('coverage').textContent.includes(new Date(saved.fetchedAt).toLocaleString()), 'Original retrieval time must remain visible');
      if (expectedIDs) assert(JSON.stringify(ui.candidates.map(p => p.id).sort()) === JSON.stringify([...expectedIDs].sort()), 'Local search must retain reference matches');
    };
    ui.request = offline;
    await click('fetch'); assertLoaded();
    assert(requests === 0, 'A saved list must not require a network request');
    const ids = ui.candidates.map(p => p.id);
    await click('filter');
    assert(JSON.stringify(ui.candidates.map(p => p.id)) === JSON.stringify(ids) && !requests, 'Offline filtering must preserve matches');
    record('offline-read-and-filter');

    win.close(); await waitFor(() => win.closed, 'plugin window to close');
    // Wait for the native window-close event before opening the same named dialog.
    await Zotero.Promise.delay(200);
    win = await open(); ui = win.Search4PaperUI; originalRequest = ui.request;
    ui.request = offline;
    await click('fetch'); assertLoaded();
    assert(requests === 0, 'Reopening the dialog must load from disk');
    record('reopen-window-keeps-metadata-and-timestamp');

    await click('refresh-papers');
    assert(requests === 1 && ui.$('status').textContent.includes('403'), 'Manual refresh must request the network and expose failure');
    assert(!ui.papers.length && ui.$('import').disabled, 'Failed refresh must not display stale results');
    assert(await IOUtils.readUTF8(path) === savedText, 'Failed refresh must preserve the previous file byte for byte');
    await click('fetch'); assertLoaded();
    assert(requests === 1, 'The old file must remain explicitly reloadable offline');
    record('failed-refresh-preserves-saved-list');

    ui.request = async (_url, signal) => new Promise((_resolve, reject) =>
      signal.addEventListener('abort', () => reject(new Error('cancelled')), { once: true }));
    ui.$('refresh-papers').click(); ui.$('cancel').click(); await waitFor(() => !ui.busy);
    assert(ui.$('status').textContent.includes('取消') && await IOUtils.readUTF8(path) === savedText, 'Cancelled refresh must preserve saved metadata');
    record('cancel-preserves-saved-list');

    ui.request = offline;
    for (const broken of ['{incomplete', JSON.stringify({ ...saved, year: 2025 })]) {
      await IOUtils.writeUTF8(path, broken);
      await click('fetch');
      assert(!ui.papers.length && ui.$('status').textContent.includes('读取本地元数据失败'), 'Invalid local data must fail explicitly');
      assert(requests === 1 && await IOUtils.readUTF8(path) === broken, 'Invalid files must not trigger silent downloads or replacement');
    }
    await IOUtils.writeUTF8(path, savedText, { tmpPath: path + '.tmp' });
    record('invalid-local-data-fails-without-network');

    const fixture = (year, title = 'Anomaly Detection for Time Series') => ({
      id: 'metadata-fixture-' + year, number: 1, content: {
        venueid: { value: `ICML.cc/${year}/Conference` }, title: { value: title },
        authors: { value: ['Test Author'] }, abstract: { value: 'Synthetic test record, not a real paper.' }
      }
    });
    for (const year of [2099, 2100]) {
      const target = PathUtils.join(expectedDataDir, 'search4paper', 'ICML', `${year}.json`);
      assert(!await IOUtils.exists(target), 'Test year must not already contain saved data');
      createdPaths.push(target);
      ui.$('year').value = String(year);
      let calls = 0;
      ui.request = async url => {
        calls++;
        assert(new URL(url).searchParams.get('venueid') === `ICML.cc/${year}/Conference`, 'Selected year must be requested');
        return { count: 1, notes: [fixture(year)] };
      };
      await click('fetch');
      assert(calls === 1 && ui.papers.length === 1 && ui.loadedYear === year, 'Missing year must be fetched and saved');
      win.Search4PaperCore.validateMetadata(await IOUtils.readJSON(target), year);
      const before = await IOUtils.readUTF8(target);
      calls = 0;
      ui.request = async () => {
        if (++calls === 1) return { count: 2, notes: [fixture(year)] };
        throw new Error('Injected second-page failure');
      };
      await click('refresh-papers');
      assert(calls === 2 && !ui.papers.length && await IOUtils.readUTF8(target) === before, 'A partial refresh must never replace the saved list');
      ui.request = async () => ({ count: 1, notes: [fixture(year, 'Updated Anomaly Detection for Time Series')] });
      await click('refresh-papers');
      const updated = await IOUtils.readJSON(target);
      assert(updated.papers[0].title.startsWith('Updated ') && updated.fetchedAt !== JSON.parse(before).fetchedAt, 'Successful refresh replaces content and retrieval time');
      assert(!await IOUtils.exists(target + '.tmp'), 'Completed write must not leave its temporary file');
      const updatedText = await IOUtils.readUTF8(target);
      await IOUtils.makeDirectory(target + '.tmp');
      try {
        await click('refresh-papers');
        assert(!ui.papers.length && ui.$('status').textContent.includes('保存本地元数据失败'), 'Disk write failure must be visible');
        assert(await IOUtils.readUTF8(target) === updatedText, 'Failed disk write must preserve the complete previous file');
      }
      finally { await IOUtils.remove(target + '.tmp'); }
    }
    assert(await IOUtils.readUTF8(path) === savedText, 'Fetching other years must not change the 2026 list');
    record('first-fetch-year-isolation-and-complete-refresh');
    record('disk-write-failure-preserves-previous-file');
    ui.$('year').value = '2026'; ui.request = offline;
    await click('fetch'); assertLoaded();
    report.ok = true;
  }
  catch (error) { report.ok = false; report.error = String(error); report.stack = error.stack; }
  finally {
    if (ui) ui.request = originalRequest;
    if (savedText !== undefined) await IOUtils.writeUTF8(path, savedText, { tmpPath: path + '.tmp' });
    for (const target of createdPaths) await IOUtils.remove(target, { ignoreAbsent: true });
  }
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
      authors: ['Test Author'], year: 2026, venueID: 'ICML.cc/2026/Conference', abstract: 'Not a real research paper.',
      doi: '', url: 'https://example.org/search4paper-test', pdfURL, bibtex: '', evidence: [] };
    ui.candidates = [paper]; ui.selected = new Set([paper.id]); ui.page = 0;
    ui.query = { terms: ['fixture'], operator: 'AND', fields: ['title'] }; ui.filtersDirty = false;
    ui.render(); ui.preview(paper);
    ui.$('collection').value = ''; ui.$('new-collection').value = 'XPI PDF fixture';
    await ui.operation(signal => ui.importPapers(signal));
    const item = ui.lastImport.results[0].item;
    assert(!item.getAttachments().length, 'Search import must not retrieve PDF');
    await Zotero.getActiveZoteroPane().selectItems([item.id]);
    ui.switchPage('fulltext');
    await ui.operation(signal => ui.getFullText(signal));
    assert(ui.lastPDFs?.[0]?.hasPDF, ui.$('fulltext-status').textContent);
    const attachments = await Zotero.Items.getAsync(item.getAttachments());
    assert(attachments.length === 1, 'Exactly one stored attachment');
    const actual = await IOUtils.read(await attachments[0].getFilePathAsync());
    const expected = await IOUtils.read(fixturePath);
    assert(actual.length === expected.length && actual.every((value, i) => value === expected[i]), 'Stored PDF bytes must equal the fixture');
    await ui.operation(signal => ui.getFullText(signal));
    assert(item.getAttachments().length === 1 && ui.lastPDFs[0].hasPDF, 'Repeat retrieval must reuse the PDF');
    report.ok = true; report.bytes = actual.length; report.itemID = item.id;
  }
  catch (error) { report.ok = false; report.error = String(error); report.stack = error.stack; }
  await IOUtils.writeJSON(reportPath, report);
  return report;
}

// Exercises the actual popup and hit testing, rather than assigning select.value.
// Creates a parent/child collection and one clearly labelled fixture item in the
// isolated library. No OpenReview request or PDF download is made.
async function runSearch4PaperDropdownSmoke({ expectedDataDir, reportPath }) {
  const assert = (condition, message) => { if (!condition) throw new Error(message); };
  const report = { zotero: Zotero.version, selections: [] };
  const waitFor = async (predicate, message) => {
    const start = Date.now();
    while (!predicate()) {
      if (Date.now() - start > 5000) throw new Error(message);
      await Zotero.Promise.delay(50);
    }
  };
  let win;
  try {
    assert(expectedDataDir && Zotero.DataDirectory.dir === expectedDataDir, 'Use an explicitly selected isolated test data directory');
    const previous = [...Services.wm.getEnumerator(null)].find(w => w.location.href === 'chrome://search4paper/content/search.xhtml');
    previous?.close();
    if (previous) await waitFor(() => previous.closed, 'Previous plugin window did not close');
    document.getElementById('search4paper-open').doCommand();
    await waitFor(() => {
      win = [...Services.wm.getEnumerator(null)].find(w => w.location.href === 'chrome://search4paper/content/search.xhtml');
      return win && !win.closed && win.Search4PaperUI && win.document.readyState === 'complete';
    }, 'Plugin window did not open');
    win.resizeTo(980, 720); win.focus();
    await waitFor(() => win.document.hasFocus(), 'Plugin window did not receive focus');
    await new Promise(resolve => win.requestAnimationFrame(() => win.requestAnimationFrame(resolve)));
    const ui = win.Search4PaperUI;
    const click = element => {
      const r = element.getBoundingClientRect();
      const x = r.x + r.width / 2, y = r.y + r.height / 2;
      win.windowUtils.sendMouseEvent('mousemove', x, y, 0, 0, 0);
      win.windowUtils.sendMouseEvent('mousedown', x, y, 0, 1, 0);
      win.windowUtils.sendMouseEvent('mouseup', x, y, 0, 1, 0);
    };
    const selectByMouse = async (id, value) => {
      const select = ui.$(id), previous = select.value;
      const index = [...select.options].findIndex(option => option.value === value);
      assert(index >= 0, `Missing option ${id}: ${value}`);
      let changed = false;
      const onChange = event => { changed = event.isTrusted; };
      select.addEventListener('change', onChange);
      try {
        click(select);
        let popup;
        await waitFor(() => {
          popup = win.document.getElementById('ContentSelectDropdownPopup');
          return popup?.state === 'open';
        }, `${id}: dropdown did not open`);
        const item = [...popup.querySelectorAll('menuitem')].find(item => item.value === String(index));
        assert(item && item.label === select.options[index].label, `${id}: option label missing from popup`);
        item.scrollIntoView({ block: 'nearest' });
        await Zotero.Promise.delay(50);
        click(item);
        await waitFor(() => select.value === value && popup.state === 'closed', `${id}: clicking the visible option did not select ${value}`);
        assert(previous === value || changed, `${id}: selection must fire a trusted change event`);
        report.selections.push({ id, value, label: select.options[index].label });
      }
      finally { select.removeEventListener('change', onChange); }
    };
    for (const conference of ['NeurIPS', 'ICLR', 'AAAI', 'ACL', 'CVPR', 'ICCV', 'EMNLP', 'ECCV', 'CRYPTO', 'EUROCRYPT', 'ASIACRYPT', 'ICML']) {
      ui.filtersDirty = false;
      await selectByMouse('conference', conference);
      assert(ui.filtersDirty, 'Conference selection must invalidate the previous search');
    }
    for (const operator of ['OR', 'AND']) {
      ui.filtersDirty = false;
      await selectByMouse('operator', operator);
      assert(ui.filtersDirty, 'Changing keyword relation must invalidate the previous search');
      assert(ui.$('query-expression').textContent.includes(` ${operator} `), 'Expression must show the selected keyword relation');
    }
    const parent = new Zotero.Collection();
    parent.name = 'search4paper dropdown fixture ' + Date.now();
    parent.libraryID = Zotero.Libraries.userLibraryID;
    await parent.saveTx();
    const child = new Zotero.Collection();
    child.name = '时间序列异常检测'; child.libraryID = parent.libraryID; child.parentID = parent.id;
    await child.saveTx();
    ui.refreshCollections();
    for (const value of [String(parent.id), String(child.id), '', String(child.id)]) await selectByMouse('collection', value);
    click(ui.$('refresh'));
    assert(ui.$('collection').value === String(child.id), 'Refreshing collections must keep the selected child');

    ui.$('new-collection').value = '';
    report.imports = [];
    for (const [conference, expectedTitle] of [
      ['ICML', 'International Conference on Machine Learning (ICML 2026)'],
      ['NeurIPS', 'Advances in Neural Information Processing Systems (NeurIPS 2026)'],
      ['ICLR', 'International Conference on Learning Representations (ICLR 2026)']
    ]) {
      await selectByMouse('conference', conference);
      const marker = 'DropdownFixture' + conference + Date.now();
      const paper = { id: marker, title: `${conference} dropdown regression fixture: time series anomaly detection`, year: 2026,
        venueID: `${conference}.cc/2026/Conference`, authors: ['Test Author'], doi: '',
        url: 'https://example.org/' + marker, abstract: 'Synthetic UI test fixture, not a real paper.', evidence: [] };
      ui.query = { terms: ['anomaly detection', 'time series'], operator: 'AND', fields: ['title'] };
      ui.candidates = [paper]; ui.selected.clear(); ui.filtersDirty = false;
      ui.render(); ui.preview(paper);
      assert(ui.$('import').disabled, 'Previewing a paper does not select it for import');
      const checkbox = ui.$('results').querySelector('input[type=checkbox]');
      let trusted = false;
      checkbox.addEventListener('change', event => { trusted = event.isTrusted; }, { once: true });
      click(checkbox);
      assert(trusted && ui.selected.has(marker) && !ui.$('import').disabled, `${conference}: checking a paper must enable import`);
      click(ui.$('import'));
      await waitFor(() => !ui.busy, 'Fixture import did not finish');
      assert(ui.lastImport?.collectionID === child.id, ui.$('status').textContent);
      assert(ui.lastImport.results.length === 1 && ui.lastImport.results[0].paper.id === marker, 'Import must use the selected paper');
      const item = ui.lastImport.results[0].item;
      assert(item?.inCollection(child.id), 'Fixture must be imported into the child selected through the popup');
      assert(item.getField('proceedingsTitle') === expectedTitle, 'Bibliography must name the correct conference');
      report.imports.push({ conference, itemID: item.id, proceedingsTitle: item.getField('proceedingsTitle') });
    }
    report.importedInto = child.id;
    report.ok = true;
  }
  catch (error) { report.ok = false; report.error = String(error); report.stack = error.stack; }
  finally { win?.document.getElementById('ContentSelectDropdownPopup')?.hidePopup(); }
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
    const paper = { id: marker, title: 'Import failure fixture', year: 2026, venueID: 'ICML.cc/2026/Conference',
      authors: ['Test Author'], doi: '', url: 'https://example.org/' + marker, abstract: '', evidence: [] };
    const options = { name: marker, query: { terms: ['fixture'], operator: 'AND', fields: ['title'] } };
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
