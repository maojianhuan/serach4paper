/* Load in an isolated Zotero profile with the installed XPI.
 * Exercises search and metadata only; does not import items or download PDFs.
 */
async function runSearch4PaperConferenceSmoke({ expectedDataDir, reportPath,
  cases = [['ICML', 2026], ['NeurIPS', 2025], ['ICLR', 2026], ['ICLR', 2025]] }) {
  const assert = (condition, message) => { if (!condition) throw new Error(message); };
  const report = { zotero: Zotero.version, searches: [] };
  const waitFor = async predicate => {
    const start = Date.now();
    while (!predicate()) {
      if (Date.now() - start > 180000) throw new Error('Timed out waiting for search');
      await Zotero.Promise.delay(100);
    }
  };
  let ui, request;
  try {
    assert(expectedDataDir && Zotero.DataDirectory.dir === expectedDataDir, 'Use an explicitly selected isolated data directory');
    const beforeItems = (await Zotero.Items.getAll(Zotero.Libraries.userLibraryID)).length;
    document.getElementById('search4paper-open').doCommand();
    let win;
    await waitFor(() => {
      win = [...Services.wm.getEnumerator(null)].find(w => w.location.href === 'chrome://search4paper/content/search.xhtml');
      return win?.Search4PaperUI && win.document.readyState === 'complete';
    });
    ui = win.Search4PaperUI; request = ui.request;
    assert(JSON.stringify([...ui.$('conference').options].map(o => o.value)) === JSON.stringify(['ICML', 'NeurIPS', 'ICLR']), 'Conference selector must contain exactly the three requested choices');
    const change = (id, value) => {
      ui.$(id).value = value;
      ui.$(id).dispatchEvent(new win.Event('input', { bubbles: true }));
    };
    const click = async id => { ui.$(id).click(); await waitFor(() => !ui.busy); };
    const unavailableNetwork = async () => { throw new Error('Search must use saved metadata without network access'); };
    change('conference', 'ICML'); change('year', '2026');
    change('terms', 'anomaly detection'); change('context', 'time series');
    for (const field of win.Search4PaperCore.FIELDS) ui.$('field-' + field).checked = true;
    const oldPath = PathUtils.join(expectedDataDir, 'search4paper', 'ICML', '2026.json');
    if (await IOUtils.exists(oldPath)) {
      ui.request = unavailableNetwork;
      await click('fetch');
      assert(ui.papers.length > 0 && ui.loadedConference === 'ICML', ui.$('status').textContent);
      report.existingICMLMetadata = true;
      ui.request = request;
    }
    for (const [conference, year] of cases) {
      change('conference', conference); change('year', String(year));
      if (ui.papers.length && (ui.loadedConference !== conference || ui.loadedYear !== year)) {
        await click('filter');
        assert(ui.$('status').textContent.includes('会议或年份已更改') && ui.filtersDirty, 'Changed source must not filter the previous list');
      }
      const venueID = `${conference}.cc/${year}/Conference`;
      const urls = [];
      report.activeSearch = { conference, year, pages: [] };
      ui.request = async function (url, signal) {
        assert(new URL(url).searchParams.get('venueid') === venueID, 'Every request must use the selected venue');
        urls.push(url);
        const payload = await request.call(this, url, signal);
        report.activeSearch.pages.push({ offset: new URL(url).searchParams.get('offset'), count: payload.count, received: payload.notes?.length });
        await IOUtils.writeJSON(reportPath, report);
        return payload;
      };
      await click('refresh-papers');
      assert(ui.papers.length > 0 && ui.loadedConference === conference && ui.loadedYear === year, ui.$('status').textContent);
      assert(ui.papers.every(p => p.venueID === venueID && p.year === year), 'Results must belong to the selected venue');
      assert(ui.candidates.length > 0, 'Default topic must produce previewable candidates in this live test');
      assert(ui.$('results').children.length === Math.min(ui.candidates.length, 100), 'Existing pagination must render');
      assert(ui.$('abstract').textContent === ui.candidates[0].abstract && ui.$('evidence').children.length > 0, 'Abstract and matching evidence must remain visible');
      const ids = ui.candidates.map(p => p.id);
      ui.$('select-page').click();
      if (conference !== 'ICML') assert(ui.$('import').disabled, 'New venues are search-only');
      const path = PathUtils.join(expectedDataDir, 'search4paper', conference, `${year}.json`);
      const saved = win.Search4PaperCore.validateMetadata(await IOUtils.readJSON(path), year, conference);
      ui.request = unavailableNetwork;
      await click('fetch');
      assert(ui.$('coverage').textContent.includes(`${conference} ${year}`) && ui.$('coverage').textContent.includes('本地名单'), 'Saved data must be labelled with the selected venue');
      assert(JSON.stringify(ui.candidates.map(p => p.id)) === JSON.stringify(ids), 'Offline search must reproduce the same candidates');
      for (const field of ['abstract', 'keywords', 'tldr']) ui.$('field-' + field).checked = false;
      ui.$('field-abstract').dispatchEvent(new win.Event('input', { bubbles: true }));
      await click('filter');
      assert(ui.candidates.length <= ids.length && ui.candidates.every(p => p.evidence.every(hit => hit.field === 'title')), 'Existing field filter must remain effective');
      for (const field of ['abstract', 'keywords', 'tldr']) ui.$('field-' + field).checked = true;
      ui.$('field-abstract').dispatchEvent(new win.Event('input', { bubbles: true }));
      await click('filter');
      assert(JSON.stringify(ui.candidates.map(p => p.id)) === JSON.stringify(ids), 'Restoring fields must restore candidate matches');
      report.searches.push({ conference, year, venueID, pages: urls.length, papers: saved.paperCount, candidateIDs: ids });
      await IOUtils.writeJSON(reportPath, report);
    }
    for (const { conference, year, papers } of report.searches) {
      const saved = await IOUtils.readJSON(PathUtils.join(expectedDataDir, 'search4paper', conference, `${year}.json`));
      win.Search4PaperCore.validateMetadata(saved, year, conference);
      assert(saved.paperCount === papers, 'Searching another source must not replace a previous saved list');
    }
    assert((await Zotero.Items.getAll(Zotero.Libraries.userLibraryID)).length === beforeItems, 'Search must not create Zotero items');
    delete report.activeSearch;
    report.ok = true;
  }
  catch (error) { report.ok = false; report.error = String(error); report.stack = error.stack; }
  finally { if (ui) ui.request = request; }
  await IOUtils.writeJSON(reportPath, report);
  return report;
}
