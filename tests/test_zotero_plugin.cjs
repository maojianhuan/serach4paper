const { test } = require('node:test');
const assert = require('node:assert/strict');
const { readFileSync } = require('node:fs');
const vm = require('node:vm');
const core = require('../zotero-plugin/content/core.js');

const query = { terms: ['anomaly detection', 'time series'], operator: 'AND', fields: core.FIELDS };
function note(id, changes = {}) {
  return { id, number: Number(id), content: {
    venueid: { value: 'ICML.cc/2026/Conference' }, title: { value: 'Anomaly Detection for Time-Series' },
    authors: { value: ['Family Given', 'Research Consortium'] }, abstract: { value: 'An abstract.' },
    pdf: { value: '/pdf?id=' + id }, ...changes
  } };
}
const fetchWith = request => core.fetchAccepted(2026, { request });

test('selecting current results enables import for all supported conferences', () => {
  const controls = new Map(['conference', 'cancel', 'filter', 'import', 'select-page', 'clear',
    'previous', 'next', 'source', 'pdf-link'].map(id => [id, {}]));
  const context = { window: { arguments: [{ Zotero: {} }], addEventListener() {} },
    document: { querySelectorAll: () => [...controls.values()] } };
  vm.runInNewContext(readFileSync(require.resolve('../zotero-plugin/content/search.js'), 'utf8'), context);
  const ui = context.Search4PaperUI;
  ui.$ = id => controls.get(id);
  ui.papers = ui.candidates = [{ id: 'selected-paper' }];
  for (const conference of core.CONFERENCES) {
    ui.$('conference').value = conference;
    ui.selected = new Set(['selected-paper']); ui.filtersDirty = false; ui.busy = false;
    ui.controls();
    assert.equal(ui.$('import').disabled, false, `${conference}: selected current results can be imported`);
    ui.selected.clear(); ui.controls();
    assert.equal(ui.$('import').disabled, true, 'Preview without a selection cannot be imported');
    assert.match(ui.$('import').title, /勾选.*复选框/);
    ui.selected.add('selected-paper'); ui.filtersDirty = true; ui.controls();
    assert.equal(ui.$('import').disabled, true, 'Changed search conditions still block stale imports');
    assert.match(ui.$('import').title, /搜索论文或应用筛选/);
    ui.filtersDirty = false; ui.busy = true; ui.controls();
    assert.equal(ui.$('import').disabled, true, 'An ongoing operation blocks another import');
  }
});

test('only the twelve requested conferences are available; OpenReview venue IDs remain unchanged', async () => {
  assert.deepEqual(core.CONFERENCES, ['ICML', 'NeurIPS', 'ICLR', 'AAAI', 'ACL', 'CVPR', 'ICCV', 'EMNLP', 'ECCV', 'CRYPTO', 'EUROCRYPT', 'ASIACRYPT']);
  for (const conference of core.OPENREVIEW_CONFERENCES) {
    for (const year of [2025, 2026]) assert.equal(core.venueID(conference, year), `${conference}.cc/${year}/Conference`);
  }
  let requests = 0;
  for (const conference of ['KDD', 'NIPS', '', 'constructor', '../ICML']) {
    await assert.rejects(core.fetchAccepted(2026, { conference, request: async () => { requests++; } }), /请选择/);
  }
  assert.equal(requests, 0);
});

test('every page and normalized paper retain the selected conference and year', async () => {
  for (const conference of core.OPENREVIEW_CONFERENCES) for (const year of [2025, 2026]) {
    const venueID = `${conference}.cc/${year}/Conference`, offsets = [];
    const papers = await core.fetchAccepted(year, { conference, request: async url => {
      const params = new URL(url).searchParams;
      assert.equal(params.get('venueid'), venueID);
      assert.equal(params.get('term'), venueID);
      assert.equal(params.get('content'), 'venueid');
      assert.equal(params.get('type'), 'exact');
      offsets.push(params.get('offset'));
      return { count: 2, notes: [note(String(offsets.length), { venueid: { value: venueID } })] };
    } });
    assert.deepEqual(offsets, ['0', '1']);
    assert.ok(papers.every(p => p.year === year && p.venueID === venueID));
    assert.ok(papers.every(p => core.matchPaper(p, query)), 'Matching must remain unchanged across venues');
  }
});

test('cross-conference and cross-year API records cannot enter the selected list', async () => {
  for (const conference of core.OPENREVIEW_CONFERENCES) {
    const other = conference === 'ICML' ? 'ICLR' : 'ICML';
    for (const venue of [`${other}.cc/2026/Conference`, `${conference}.cc/2025/Conference`,
      `${conference}.cc/2026/Conference/Rejected`, `${conference}.cc/2026/Workshop`]) {
      await assert.rejects(core.fetchAccepted(2026, { conference, request: async () =>
        ({ count: 1, notes: [note('1', { venueid: { value: venue } })] }) }), /其他会议或未录用/);
    }
  }
});

test('unavailable venue reports the requested conference and year without a fallback', async () => {
  let requests = 0;
  await assert.rejects(core.fetchAccepted(2100, { conference: 'NeurIPS', request: async () => {
    requests++; return { count: 0, notes: [] };
  } }), /尚未获得 NeurIPS 2100/);
  assert.equal(requests, 1);
});

test('AND requires all keywords and allows them to occur in different fields', () => {
  assert.equal(core.matchPaper({ title: 'Anomaly Detection for Images' }, query), null);
  const result = core.matchPaper({ title: 'Anomaly Detection', abstract: 'We study multivariate time series.' }, query);
  assert.deepEqual(result.evidence.map(hit => [hit.field, hit.term]), [
    ['title', 'anomaly detection'], ['abstract', 'time series']
  ]);
});

test('matching retains existing stems, inflections, accents and punctuation semantics', () => {
  for (const [text, term] of [['Time-Series Anomalies Detection', 'time serie anomaly detect'],
    ['SELF-EVOLVING AGENTS', 'self evolving agent'], ['Détection', 'detection']]) {
    assert.ok(core.phraseMatches(text, term), `${term}: ${text}`);
  }
  assert.equal(core.phraseMatches('for', 'forecasting'), false);
  assert.equal(core.phraseMatches('time and series', 'time series'), false);
});

test('AND and OR select the expected papers while fields only define where to look', () => {
  const papers = [
    { id: 'both-title', title: 'Time Series Anomaly Detection' },
    { id: 'anomaly-only', title: 'Anomaly Detection for Images' },
    { id: 'series-only', title: 'Forecasting Time Series' },
    { id: 'split-fields', title: 'Anomaly Detection', abstract: 'We study time series.' },
    { id: 'unrelated', title: 'Graph Representations' }
  ];
  const ids = changes => papers.filter(p => core.matchPaper(p, { ...query, ...changes })).map(p => p.id);
  assert.deepEqual(ids({ operator: 'AND' }), ['both-title', 'split-fields']);
  assert.deepEqual(ids({ operator: 'OR' }), ['both-title', 'anomaly-only', 'series-only', 'split-fields']);
  assert.deepEqual(ids({ operator: 'AND', fields: ['title'] }), ['both-title']);
  assert.deepEqual(ids({ operator: 'OR', fields: ['abstract'] }), ['split-fields']);
  assert.deepEqual(ids({ operator: 'OR', fields: ['keywords'] }), []);
});

test('a multiword keyword is a phrase and cannot be assembled across fields', () => {
  for (const operator of ['AND', 'OR']) {
    const q = { terms: ['time series'], operator, fields: core.FIELDS };
    assert.equal(core.matchPaper({ title: 'time', abstract: 'series' }, q), null);
    assert.equal(core.matchPaper({ title: 'time and series' }, q), null);
    assert.ok(core.matchPaper({ abstract: 'multivariate time-series models' }, q));
  }
});

test('invalid query never silently searches everything', () => {
  assert.throws(() => core.validateQuery({ ...query, terms: [] }));
  assert.throws(() => core.validateQuery({ ...query, terms: ['???'] }));
  assert.throws(() => core.validateQuery({ ...query, fields: [] }));
  assert.throws(() => core.validateQuery({ ...query, fields: ['unknown'] }));
  assert.throws(() => core.validateQuery({ ...query, operator: 'NOT' }), /AND.*OR/);
  assert.throws(() => core.validateQuery({ ...query, operator: undefined }), /AND.*OR/);
  assert.deepEqual(core.splitTerms('a; a；b; '), ['a', 'b']);
});

test('complete pagination keeps the exact accepted venue on every request', async () => {
  const offsets = [];
  const papers = await fetchWith(async url => {
    const p = new URL(url).searchParams;
    assert.equal(p.get('venueid'), 'ICML.cc/2026/Conference');
    assert.equal(p.get('type'), 'exact');
    assert.equal(p.get('content'), 'venueid');
    offsets.push(p.get('offset'));
    return { count: 2, notes: [note(p.get('offset') === '0' ? '2' : '1')] };
  });
  assert.deepEqual(offsets, ['0', '1']);
  assert.deepEqual(papers.map(p => p.id), ['1', '2']);
  assert.deepEqual(papers[0].authors, ['Family Given', 'Research Consortium']);
  assert.equal(papers[0].pdfURL, 'https://openreview.net/pdf?id=1');
});

test('wrong venue including rejected and workshop records is rejected', async () => {
  for (const venue of ['ICML.cc/2026/Conference/Rejected', 'ICML.cc/2026/Workshop', 'ICML.cc/2025/Conference']) {
    await assert.rejects(fetchWith(async () => ({ count: 1, notes: [note('1', { venueid: { value: venue } })] })), /其他会议或未录用/);
  }
});

test('empty public list is unavailable, not proof of no relevant papers', async () => {
  await assert.rejects(fetchWith(async () => ({ count: 0, notes: [] })), /尚未获得/);
});

test('early end, duplicate IDs and changing totals fail without partial results', async () => {
  for (const second of [{ count: 2, notes: [] }, { count: 2, notes: [note('1')] }, { count: 3, notes: [note('2')] }]) {
    let calls = 0;
    await assert.rejects(fetchWith(async () => ++calls === 1 ? { count: 2, notes: [note('1')] } : second));
    assert.equal(calls, 2);
  }
});

test('malformed source records and response counts fail explicitly', async () => {
  for (const response of [{ notes: [] }, { count: '1', notes: [note('1')] },
    { count: 1, notes: [note('1', { authors: { value: 'Unsplit; string' } })] },
    { count: 1, notes: [note('1', { title: { value: '' } })] }]) {
    await assert.rejects(fetchWith(async () => response));
  }
});

test('paper PDF links cannot contain executable or local-file schemes', () => {
  for (const pdf of ['javascript:alert(1)', 'file:///etc/passwd', 'data:text/html,test']) {
    assert.throws(() => core.normalizeNote(note('1', { pdf: { value: pdf } }), 2026), /PDF 地址/);
  }
});

test('optional null fields stay empty and legacy TL;DR remains searchable', () => {
  const paper = core.normalizeNote(note('1', { abstract: { value: null }, keywords: null,
    'TL;DR': { value: 'Anomaly detection for time series' } }), 2026);
  assert.equal(paper.abstract, '');
  assert.equal(paper.keywords, '');
  assert.ok(core.matchPaper(paper, { ...query, fields: ['tldr'] }));
});

test('cancellation stops pagination and cannot publish a partial list', async () => {
  const controller = new AbortController();
  let calls = 0;
  await assert.rejects(core.fetchAccepted(2026, { signal: controller.signal, request: async () => {
    calls++; controller.abort(); return { count: 2, notes: [note('1')] };
  } }), { name: 'AbortError' });
  assert.equal(calls, 1);
});

test('HTTP failure is not retried or converted into an empty success', async () => {
  let calls = 0;
  await assert.rejects(fetchWith(async () => { calls++; throw new Error('HTTP 403'); }), /403/);
  assert.equal(calls, 1);
});

function metadata() {
  return { schemaVersion: 1, year: 2026, venueID: 'ICML.cc/2026/Conference',
    fetchedAt: '2026-09-20T12:34:56.000Z', paperCount: 1, papers: [core.normalizeNote(note('1'), 2026)] };
}

test('saved metadata round-trips without changing paper fields, retrieval time or matches', () => {
  const original = metadata();
  const restored = core.validateMetadata(JSON.parse(JSON.stringify(original)), 2026);
  assert.deepEqual(restored, original);
  assert.deepEqual(core.matchPaper(restored.papers[0], query), core.matchPaper(original.papers[0], query));
});

test('local metadata is isolated by both conference and year; existing ICML files still load', () => {
  const oldICML = metadata();
  assert.equal(core.validateMetadata(oldICML, 2026, 'ICML'), oldICML);
  for (const conference of core.CONFERENCES) {
    const saved = metadata();
    saved.venueID = core.venueID(conference, 2026);
    saved.papers[0] = samplePaper(conference, 2026);
    assert.equal(core.validateMetadata(saved, 2026, conference), saved);
    assert.throws(() => core.validateMetadata(saved, 2025, conference), /年份不一致/);
    for (const other of core.CONFERENCES.filter(c => c !== conference)) {
      assert.throws(() => core.validateMetadata(saved, 2026, other), /会议或年份不一致/);
      const wrongPaper = { ...saved, papers: [{ ...saved.papers[0], venueID: core.venueID(other, 2026) }] };
      assert.throws(() => core.validateMetadata(wrongPaper, 2026, conference), /会议、年份不一致/);
    }
  }
});

test('saved lists must have the expected schema, venue, year, timestamp and complete count', () => {
  for (const change of [{ schemaVersion: 2 }, { venueID: 'ICML.cc/2026/Workshop' }, { year: 2025 },
    { fetchedAt: '' }, { fetchedAt: null }, { papers: null }, { paperCount: 0, papers: [] },
    { paperCount: 2 }, { paperCount: '1' }]) {
    assert.throws(() => core.validateMetadata({ ...metadata(), ...change }, 2026));
  }
  assert.throws(() => core.validateMetadata(null, 2026));
  for (const year of ['2026', 2026.5, 0, 2101, NaN, Infinity]) assert.throws(() => core.validateYear(year));
});

test('local metadata rejects duplicates, wrong-venue papers and unusable fields', () => {
  const duplicate = metadata();
  duplicate.papers.push({ ...duplicate.papers[0] }); duplicate.paperCount = 2;
  assert.throws(() => core.validateMetadata(duplicate, 2026), /重复/);
  for (const change of [{ id: '' }, { title: ' ' }, { abstract: null }, { authors: 'Author' },
    { authors: [null] }, { year: 2025 }, { venueID: 'ICML.cc/2026/Conference/Rejected' }]) {
    const saved = metadata(); Object.assign(saved.papers[0], change);
    assert.throws(() => core.validateMetadata(saved, 2026));
  }
});

test('saved metadata cannot replace source links with executable or local URLs', () => {
  for (const change of [{ url: 'javascript:alert(1)' }, { url: 'https://openreview.net/forum?id=another' },
    { pdfURL: 'file:///etc/passwd' }, { pdfURL: 'javascript:alert(1)' }, { pdfURL: 'not a URL' }]) {
    const saved = metadata(); Object.assign(saved.papers[0], change);
    assert.throws(() => core.validateMetadata(saved, 2026), /链接/);
  }
});

test('identities match existing Python imports and preserve case-sensitive OpenReview IDs', () => {
  const a = core.identities({ id: 'PaperA', doi: 'https://doi.org/10.1234/ABC' });
  const b = core.identities({ extra: 'OpenReview ID: PaperA\nDOI: 10.1234/abc', url: 'https://openreview.net/forum?id=PaperA' });
  assert.deepEqual([...a].sort(), [...b].sort());
  assert.notDeepEqual([...core.identities({ id: 'PaperA' })], [...core.identities({ id: 'papera' })]);
  assert.equal(core.identities({ url: 'https://example.com/forum?id=PaperA' }).size, 0);
  assert.equal(core.identities({ url: 'a user-entered URL' }).size, 0);
});

test('evidence note records keywords and their operator without interpreting HTML', () => {
  for (const operator of ['AND', 'OR']) {
    const html = core.evidenceNote({ ...core.normalizeNote(note('1'), 2026),
      evidence: [{ field: 'abstract', term: '<script>', text: '<img src=x onerror=alert(1)>' }], bibtex: '@x{a&b}' }, { ...query, operator });
    assert.ok(html.includes('&lt;img'));
    assert.ok(html.includes('a&amp;b'));
    assert.ok(html.includes('Keywords: anomaly detection; time series'));
    assert.ok(html.includes(`Match: ${operator}`));
    assert.equal(html.includes('<script>'), false);
  }
});

function samplePaper(conference, year = 2025) {
  if (core.OPENREVIEW_CONFERENCES.includes(conference)) {
    return core.normalizeNote(note('1', { venueid: { value: core.venueID(conference, year) } }), year, conference);
  }
  const source = conference === 'AAAI' ? 'aaai' : ['ACL', 'EMNLP'].includes(conference) ? 'acl' : conference === 'ECCV' ? 'ecva' : ['CRYPTO', 'EUROCRYPT', 'ASIACRYPT'].includes(conference) ? 'iacr' : 'cvf';
  const id = source === 'aaai' ? '33933' : source === 'iacr' ? '34309' : source === 'acl'
    ? `${year}.${conference.toLowerCase()}-${conference === 'ACL' ? 'long' : 'main'}.1`
    : source === 'ecva' ? `/papers/eccv_${year}/papers_ECCV/html/Example_ECCV_${year}_paper.php` : `/content/${conference}${year}/html/Author_Example_${conference}_${year}_paper.html`;
  const url = source === 'aaai' ? `https://ojs.aaai.org/index.php/AAAI/article/view/${id}`
    : source === 'iacr' ? `https://www.iacr.org/cryptodb/data/paper.php?pubkey=${id}`
    : source === 'acl' ? `https://aclanthology.org/${id}/` : source === 'ecva' ? `https://www.ecva.net${id}` : `https://openaccess.thecvf.com${id}`;
  return { id, source, year, venueID: core.venueID(conference, year), number: null, url,
    title: 'Time Series Anomaly Detection', authors: ['Example Author'], abstract: 'An example abstract.',
    doi: '', keywords: '', tldr: '', pdfURL: '', bibtex: '' };
}

test('official-source IDs cannot collide with OpenReview IDs and recognize existing source URLs', () => {
  for (const conference of ['AAAI', 'ACL', 'CVPR', 'ICCV', 'EMNLP', 'ECCV', 'CRYPTO', 'EUROCRYPT', 'ASIACRYPT']) {
    const paper = samplePaper(conference);
    const expected = `source:${paper.source}:${paper.id}`;
    assert.deepEqual([...core.identities(paper)], [expected]);
    assert.deepEqual([...core.identities({ url: paper.url })], [expected]);
    assert.deepEqual([...core.identities({ extra: `Source ID: ${paper.source}:${paper.id}` })], [expected]);
    assert.equal(core.identities({ id: paper.id }).has(expected), false);
    assert.equal(core.identities({ url: paper.url.replace(new URL(paper.url).hostname, 'example.com') }).size, 0);
  }
  assert.deepEqual([...core.identities({ url: 'https://aclanthology.org/2025.acl-long.1.pdf' })], ['source:acl:2025.acl-long.1']);
  assert.deepEqual([...core.identities({ url: 'https://ojs.aaai.org/index.php/AAAI/article/download/33933/36088' })], ['source:aaai:33933']);
});

test('new-source cache records keep valid source identities and reject substituted links', () => {
  for (const conference of ['AAAI', 'ACL', 'CVPR', 'ICCV', 'EMNLP', 'ECCV', 'CRYPTO', 'EUROCRYPT', 'ASIACRYPT']) {
    const saved = { schemaVersion: 1, year: 2025, venueID: core.venueID(conference, 2025),
      fetchedAt: '2026-09-20T12:34:56.000Z', paperCount: 1, papers: [samplePaper(conference)] };
    assert.deepEqual(core.validateMetadata(JSON.parse(JSON.stringify(saved)), 2025, conference), saved);
    for (const change of [{ source: 'openreview' }, { url: 'javascript:alert(1)' },
      { url: 'https://example.com/paper' }, { pdfURL: 'file:///local.pdf' }, { year: 2024 }]) {
      assert.throws(() => core.validateMetadata({ ...saved, papers: [{ ...saved.papers[0], ...change }] }, 2025, conference));
    }
  }
});

test('historical proceedings keep canonical cache and duplicate identities', () => {
  for (const [conference, year, source, id, origin] of [
    ['ACL', 2019, 'acl', 'P19-1001', 'https://aclanthology.org'],
    ['EMNLP', 2007, 'acl', 'D07-1001', 'https://aclanthology.org'],
    ['CVPR', 2013, 'cvf', '/content_cvpr_2013/html/Example_paper.html', 'https://openaccess.thecvf.com'],
    ['ICCV', 2019, 'cvf', '/content_ICCV_2019/html/Example_paper.html', 'https://openaccess.thecvf.com'],
    ['ECCV', 2024, 'ecva', '/papers/eccv_2024/papers_ECCV/html/4_ECCV_2024_paper.php', 'https://www.ecva.net']
  ]) {
    const paper = { ...samplePaper(conference, year), source, id,
      url: source === 'acl' ? `${origin}/${id}/` : origin + id };
    const saved = { ...metadata(), year, venueID: paper.venueID, papers: [paper] };
    assert.equal(core.validateMetadata(saved, year, conference), saved);
    assert.deepEqual([...core.identities({url:paper.url})], [`source:${source}:${id}`]);
    assert.throws(() => core.validateMetadata({...saved, papers:[{...paper, id:id.replace(String(year),String(year+1)) + 'invalid'}]}, year, conference), /无效/);
  }
});

test('IACR identities recognize both official hosts without numeric-ID collisions', () => {
  const key = 'source:iacr:34309';
  for (const host of ['iacr.org', 'www.iacr.org']) {
    assert.deepEqual([...core.identities({url:`https://${host}/cryptodb/data/paper.php?pubkey=34309`})], [key]);
  }
  assert.deepEqual([...core.identities({extra:'Source ID: iacr:34309'})], [key]);
  assert.equal(core.identities({source:'aaai',id:'34309'}).has(key), false);
  assert.equal(core.identities({id:'34309'}).has(key), false);
  assert.equal(core.identities({url:'https://example.com/cryptodb/data/paper.php?pubkey=34309'}).has(key), false);
});

function pdfFixture(attempts, { direct = true, existing = false, signal } = {}) {
  const scope = {};
  vm.runInNewContext(readFileSync(require.resolve('../zotero-plugin/content/import.js'), 'utf8'), scope);
  const calls = [], item = { id: 123, getAttachments: () => existing ? [456] : [] };
  const attachment = { attachmentContentType: 'application/pdf', isFileAttachment: () => true, fileExists: async () => true };
  const Zotero = {
    Items: { getAsync: async ids => ids.length ? [attachment] : [] },
    Attachments: {
      canFindFileForItem: () => !existing,
      getFileResolvers: actual => { assert.equal(actual, item); calls.push('resolve'); return [{ url: 'https://example.org/native.pdf' }]; },
      addFileFromURLs: async (actual, resolvers, options) => {
        assert.equal(actual, item);
        options.onBeforeRequest();
        calls.push(resolvers[0]?.url || 'empty');
        const result = await attempts.shift()(options);
        if (result) existing = true;
        return result ? attachment : false;
      }
    }
  };
  return { calls, item, run: () => scope.Search4PaperImport.findPDFs(Zotero,
    [{item, paper:{title:'Example paper', pdfURL:direct ? 'https://example.org/direct.pdf' : '', url:'https://example.org/paper'}}], {signal}) };
}
const rejectPDF = options => { assert.equal(options.onRequestError({status:403,message:'Access denied'}), false); return false; };

test('PDF 403 proceeds to native full-text lookup and clears the earlier error on success', async () => {
  const f = pdfFixture([rejectPDF, () => true]);
  const [result] = await f.run();
  assert.deepEqual(f.calls, ['https://example.org/direct.pdf','resolve','https://example.org/native.pdf']);
  assert.equal(result.hasPDF, true); assert.equal(result.error, '');
});

test('successful direct PDF download does not invoke native lookup', async () => {
  const f = pdfFixture([() => true]);
  assert.equal((await f.run())[0].hasPDF, true);
  assert.deepEqual(f.calls, ['https://example.org/direct.pdf']);
});

test('both PDF stages failing preserves the item and reports both causes', async () => {
  const f = pdfFixture([rejectPDF, () => false]);
  const [result] = await f.run();
  assert.equal(result.itemID, f.item.id); assert.equal(result.hasPDF, false);
  assert.match(result.error, /直接下载：HTTP 403: Access denied/);
  assert.match(result.error, /Zotero 原生全文查找：未找到可用全文/);
});

test('a thrown direct download error also proceeds to native lookup', async () => {
  const f = pdfFixture([() => { throw new Error('Network failed'); }, () => true]);
  assert.equal((await f.run())[0].hasPDF, true);
  assert.equal(f.calls.includes('resolve'), true);
});

test('missing direct link invokes native lookup once; existing PDFs need no download', async () => {
  const native = pdfFixture([() => true], {direct:false});
  assert.equal((await native.run())[0].hasPDF, true);
  assert.deepEqual(native.calls, ['resolve','https://example.org/native.pdf']);
  const existing = pdfFixture([], {existing:true});
  assert.equal((await existing.run())[0].hasPDF, true); assert.deepEqual(existing.calls, []);
});

test('cancelling after direct PDF failure prevents native lookup', async () => {
  const controller = new AbortController();
  const f = pdfFixture([options => { rejectPDF(options); controller.abort(); return false; }], {signal:controller.signal});
  const [result] = await f.run();
  assert.equal(result.hasPDF, false); assert.match(result.error, /abort/i);
  assert.deepEqual(f.calls, ['https://example.org/direct.pdf']);
});

test('welcome waits for the UI, closes into a profile preference, and stays dismissed after restart/update', async () => {
  const prefs = new Map();
  const key = 'extensions.search4paper.welcomeShown';
  let ready, opened = 0, dialog, click;
  const Zotero = {uiReadyPromise: new Promise(resolve => { ready = resolve; }), Prefs: {
    get(name, global) { assert.equal(global, true); return prefs.get(name); },
    set(name, value, global) { assert.equal(global, true); prefs.set(name, value); }
  }};
  const parent = {closed: false, setTimeout,
    openDialog(url, name, features, args) {
      opened++;
      assert.equal(url, 'chrome://search4paper/content/welcome.xhtml');
      assert.ok(!features.includes('modal'));
      const events = {};
      dialog = {closed: false, arguments: [args], addEventListener: (name, fn) => { events[name] = fn; },
        close() { this.closed = true; events.unload(); }};
      vm.runInNewContext(readFileSync(require.resolve('../zotero-plugin/content/welcome.js'), 'utf8'), {
        window: dialog, document: {getElementById: () => ({addEventListener: (name, fn) => { click = fn; }})}
      });
      events.DOMContentLoaded();
      return dialog;
    }};
  const boot = () => {
    const scope = {Zotero};
    vm.runInNewContext(readFileSync(require.resolve('../zotero-plugin/bootstrap.js'), 'utf8'), scope);
    scope.pluginActive = true;
    return scope;
  };
  const first = boot();
  const pending = first.showWelcome(parent);
  await Promise.resolve(); assert.equal(opened, 0);
  ready(); await pending;
  assert.equal(opened, 1); assert.equal(prefs.has(key), false);
  await first.showWelcome(parent); assert.equal(opened, 1); // second main window
  dialog.close(); assert.equal(prefs.get(key), true); // window close button
  await boot().showWelcome(parent); assert.equal(opened, 1); // restart or plugin update
  prefs.clear();
  await boot().showWelcome(parent); assert.equal(opened, 2); // fresh profile
  click(); assert.equal(dialog.closed, true); assert.equal(prefs.get(key), true);
});

test('welcome does not open after shutdown or on a closed main window', async () => {
  let opened = 0;
  const scope = {Zotero: {uiReadyPromise: Promise.resolve(), Prefs: {get: () => false}}};
  vm.runInNewContext(readFileSync(require.resolve('../zotero-plugin/bootstrap.js'), 'utf8'), scope);
  const parent = {closed: false, setTimeout, openDialog() { opened++; }};
  await scope.showWelcome(parent);
  scope.pluginActive = true;
  parent.closed = true;
  await scope.showWelcome(parent);
  assert.equal(opened, 0);
});
