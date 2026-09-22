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
  const controls = new Map(['fetch', 'refresh-papers', 'conference', 'cancel', 'filter', 'import', 'select-page', 'clear',
    'previous', 'next', 'source', 'tab-search', 'tab-fulltext', 'fulltext-cancel'].map(id => [id, {}]));
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

test('the twenty-six supported conferences include WWW with its exact OpenReview venue ID', async () => {
  assert.deepEqual(core.CONFERENCES, ['ICML', 'NeurIPS', 'ICLR', 'AAAI', 'ACL', 'CVPR', 'ICCV', 'EMNLP', 'ECCV', 'CRYPTO', 'EUROCRYPT', 'ASIACRYPT', 'ICDE', 'SIGMOD', 'KDD', 'SIGIR', 'VLDB', 'FAST', 'NSDI', 'OSDI', 'USENIX Security', 'CCS', 'NDSS', 'SIGCOMM', 'FSE', 'WWW']);
  for (const conference of ['ICML', 'NeurIPS', 'ICLR']) {
    for (const year of [2025, 2026]) assert.equal(core.venueID(conference, year), `${conference}.cc/${year}/Conference`);
  }
  for (const year of [2025, 2026]) assert.equal(core.venueID('WWW', year), `ACM.org/TheWebConf/${year}/Conference`);
  let requests = 0;
  for (const conference of ['NIPS', '', 'constructor', '../ICML']) {
    await assert.rejects(core.fetchAccepted(2026, { conference, request: async () => { requests++; } }), /请选择/);
  }
  assert.equal(requests, 0);
});

test('every page and normalized paper retain the selected conference and year', async () => {
  for (const conference of core.OPENREVIEW_CONFERENCES) for (const year of [2025, 2026]) {
    const venueID = core.venueID(conference, year), offsets = [];
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
    const selected = core.venueID(conference, 2026);
    for (const venue of [core.venueID(other, 2026), core.venueID(conference, 2025),
      `${selected}/Rejected`, `${selected}/Workshop`]) {
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
  for (const conference of core.CONFERENCES.filter(c => !core.DATABASE_SOURCES[c] && !core.SYSTEMS_SOURCES[c])) {
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
  if (conference === 'ICDE') return {id: `${year}:1`,source:'icde',year,venueID:core.venueID(conference,year),number:null,
    url:`https://ieee-icde.org/${year}/research-papers/`,sourceURL:`https://ieee-icde.org/${year}/research-papers/`,detailURL:'',ieeeURL:'',
    title:'Time Series Anomaly Detection',authors:['Example Author'],abstract:'',doi:'',keywords:'',tldr:'',pdfURL:'',bibtex:''};
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

function pdfFixture(attempts, { direct = true, existing = false, signal, url = "", extra = "" } = {}) {
  const scope = {URL};
  vm.runInNewContext(readFileSync(require.resolve('../zotero-plugin/content/import.js'), 'utf8'), scope);
  const calls = [], item = { id: 123, getField: field => ({url,extra}[field] || ""), isRegularItem: () => true, getAttachments: () => existing ? [456] : [] };
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
  const result=(await f.run())[0];assert.equal(result.hasPDF, true);assert.equal(result.source,'直接下载');
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
  const nativeResult=(await native.run())[0];assert.equal(nativeResult.hasPDF, true);assert.equal(nativeResult.source,'Zotero 原生全文查找');
  assert.deepEqual(native.calls, ['resolve','https://example.org/native.pdf']);
  const existing = pdfFixture([], {existing:true});
  const existingResult=(await existing.run())[0];assert.equal(existingResult.hasPDF, true);assert.equal(existingResult.source,'已有本地 PDF'); assert.deepEqual(existing.calls, []);
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

function workflowUI(Zotero = {}, importer = {}, ieee = {status: '尚未验证', context: null}, arxiv = {resolve: async () => null},
    publication = {isWWW:()=>false,resolveWWWDOI:async()=>''}, openreview = {URLs:async()=>[]}) {
  const ieeeScope = {URL};
  vm.runInNewContext(readFileSync(require.resolve('../zotero-plugin/content/ieee.js'),'utf8'),ieeeScope);
  for (const method of ['pdfURL','hasPaperURL','canResolve']) ieee[method] = ieeeScope.Search4PaperIEEE.prototype[method];
  const elements = new Map();
  const element = id => {
    if (!elements.has(id)) elements.set(id, {value: '', textContent: '', disabled: false,
      setAttribute(name, value) { this[name] = value; }, classList: {toggle() {}}});
    return elements.get(id);
  };
  const scope = {URL, AbortController, Option: function(text,value){this.text=text;this.value=value;}, Search4PaperCore: core, Search4PaperImport: importer,
    Search4PaperArxiv: arxiv, Search4PaperPublication: publication, Search4PaperOpenReview: openreview,
    window: {arguments: [{Zotero, ieeeSession: ieee, acmSession: {canHandle:()=>false,status:'',verificationURL:''}}], addEventListener() {}},
    document: {getElementById: element, querySelectorAll: () => [...elements.values()]}};
  vm.runInNewContext(readFileSync(require.resolve('../zotero-plugin/content/search.js'), 'utf8'), scope);
  return scope.Search4PaperUI;
}

test('tab switches preserve search state and keep in-flight status/cancellation on the owning page', async () => {
  const ui = workflowUI();
  const paper = {id: 'saved'};
  ui.papers = ui.candidates = [paper]; ui.active = paper; ui.selected.add(paper.id); ui.page = 2;
  ui.$('results').textContent = 'Existing rendered rows'; ui.$('abstract').textContent = 'Existing abstract';
  let finish;
  const task = ui.operation(async signal => {
    ui.switchPage('fulltext');
    ui.status('search still running');
    assert.equal(ui.$('status').textContent, 'search still running');
    assert.equal(ui.$('fulltext-status').textContent, '');
    assert.equal(ui.$('cancel').disabled, false);
    assert.equal(ui.$('fulltext-cancel').disabled, true);
    assert.equal(ui.$('tab-search').disabled, false);
    await new Promise(resolve => {finish = resolve;});
    assert.equal(signal.aborted, false);
  });
  finish(); await task;
  ui.status('PDF page message'); ui.switchPage('search');
  assert.equal(ui.$('fulltext-status').textContent, 'PDF page message');
  assert.equal(ui.candidates[0], paper); assert.equal(ui.active, paper);
  assert.equal(ui.selected.has(paper.id), true); assert.equal(ui.page, 2);
  assert.equal(ui.$('results').textContent, 'Existing rendered rows');
  assert.equal(ui.$('abstract').textContent, 'Existing abstract');
  assert.equal(ui.$('page-fulltext').hidden, true);
});

test('full-text action snapshots current Zotero selection, routes existing mechanisms in order, and isolates failures', async () => {
  const calls = [];
  const item = (id, url) => ({id, libraryID: 1, isRegularItem: () => true, getField: f => f === 'title' ? 'Paper '+id : url});
  const items = [item(1, 'https://example.org/paper'), item(2, 'https://ieeexplore.ieee.org/document/2'), item(3, 'https://example.org/3')];
  const Zotero = {getActiveZoteroPane: () => ({getSelectedItems: () => items}), Libraries: {get: () => ({filesEditable: true})}};
  const importer = {async findPDFs(z, results) {
    const {item, paper} = results[0]; calls.push(['generic', item.id, paper.pdfURL]);
    if (item.id === 3) throw new Error('No full text');
    return [{itemID: item.id, title: paper.title, hasPDF: true}];
  }};
  const ieee = {context: {id: 42}, status: '尚未验证', async download(selected) {
    calls.push(['ieee', selected[0].id]);
    return [{itemID: selected[0].id, title: 'Paper 2', hasPDF: true, startedAt: '2026-09-21T01:00:00.000Z'}];
  }};
  const ui = workflowUI(Zotero, importer, ieee);
  ui.lastImport = {results: [{item: items[0], paper: {title: 'Paper 1', pdfURL: 'https://example.org/paper.pdf'}}]};
  ui.switchPage('fulltext'); await ui.operation(signal => ui.getFullText(signal));
  assert.deepEqual(calls, [['generic',1,'https://example.org/paper.pdf'],['ieee',2],['generic',3,undefined]]);
  assert.equal(ui.lastPDFs.length, 3); assert.match(ui.lastPDFs[2].error, /No full text/);
  assert.match(ui.$('fulltext-status').textContent, /成功 2，失败 1/);
  assert.match(ui.$('fulltext-status').textContent, /请求开始/);
  assert.equal(ui.$('status').textContent, '');
});

test('full-text retrieval supports arbitrary existing items without a login or plugin import and respects cancellation', async () => {
  const selected = [1,2].map(id => ({id, libraryID: 1, isRegularItem: () => true, getField: () => 'Existing item'}));
  const controller = new AbortController(); let calls = 0;
  const ui = workflowUI({getActiveZoteroPane: () => ({getSelectedItems: () => selected}), Libraries: {get: () => ({filesEditable:true})}}, {
    async findPDFs(z, results) { calls++; controller.abort(); return [{title: 'Existing item', hasPDF: true}]; }
  });
  ui.switchPage('fulltext'); await ui.getFullText(controller.signal);
  assert.equal(calls, 1); assert.match(ui.$('fulltext-status').textContent, /未处理 1/);
  selected.length = 0;
  await assert.rejects(() => ui.getFullText(new AbortController().signal), /主窗口选择/);
});

test('search import creates bibliographic items without invoking PDF retrieval', async () => {
  let imported = false;
  const ui = workflowUI({}, {async papers(z, papers) {imported = true; return {results:[{paper: papers[0], item: {id:1}, status:'created'}],unprocessed:0};},
    findPDFs() {throw new Error('Search must not retrieve PDFs');}});
  const paper = {id: 'one', title: 'Test'};
  ui.candidates = [paper]; ui.selected.add(paper.id); ui.filtersDirty = false; ui.refreshCollections = () => {};
  await ui.importPapers(new AbortController().signal);
  assert.equal(imported, true); assert.match(ui.$('status').textContent, /新增 1/);
});

test('ICDE metadata uses source/year identities, preserves explicit links and leaves absent fields empty', () => {
  const paper = {id:'2026:1',source:'icde',year:2026,venueID:'ICDE/2026/Conference',title:'FaScalSQL: A Fast SQL Engine',authors:['Ada Example'],
    abstract:'',keywords:'',tldr:'',doi:'',pdfURL:'',bibtex:'',sourceURL:'https://icde2026.github.io/accepted-papers.html',detailURL:'',ieeeURL:'',url:'https://icde2026.github.io/accepted-papers.html'};
  const metadata = {schemaVersion:1,year:2026,venueID:paper.venueID,fetchedAt:new Date().toISOString(),paperCount:1,papers:[paper]};
  assert.equal(core.validateMetadata(metadata,2026,'ICDE'),metadata);
  assert.ok(core.matchPaper(paper,{terms:['FaScalSQL'],operator:'AND',fields:core.FIELDS}));
  assert.equal(core.matchPaper(paper,{terms:['FaScalSQL'],operator:'AND',fields:['abstract']}),null);
  assert.throws(()=>core.validateMetadata(metadata,2025,'ICDE'),/年份/);
  assert.throws(()=>core.validateMetadata(metadata,2026,'ICML'),/会议/);
  assert.ok(core.identities(paper).has('source:icde:2026:1'));
  assert.ok(core.identities({extra:'Source ID: icde:2026:1'}).has('source:icde:2026:1'));
  const linked = {...paper,doi:'10.1109/test',ieeeURL:'https://ieeexplore.ieee.org/document/123',url:'https://ieeexplore.ieee.org/document/123'};
  core.validateMetadata({...metadata,papers:[linked]},2026,'ICDE');
  assert.match(core.evidenceNote(linked,query),/Official source: https:\/\/icde2026.github.io/);
  for (const change of [{id:'2025:1'},{sourceURL:'https://example.org/2026/'},{detailURL:'javascript:alert(1)'},{ieeeURL:'https://example.org/document/123'}]) {
    assert.throws(()=>core.validateMetadata({...metadata,papers:[{...paper,...change}]},2026,'ICDE'),/ICDE/);
  }
});

test('ICDE uses an explicitly started IEEE session before the final Zotero fallback', async () => {
  for (const hasPDF of [true,false]) {
    const calls = [];
    const item = {id:1,libraryID:1,isRegularItem:()=>true,getField:f=>f==='extra'?'Source ID: icde:2026:1':f==='url'?'https://ieeexplore.ieee.org/document/123':'ICDE paper'};
    const ui = workflowUI({getActiveZoteroPane:()=>({getSelectedItems:()=>[item]}),Libraries:{get:()=>({filesEditable:true})}},
      {async findPDFs(){calls.push('generic');return [{hasPDF,title:'ICDE paper'}];}},
      {context:{id:1},status:'test',async download(){calls.push('institutional');return [{hasPDF:true,title:'ICDE paper'}];}});
    ui.switchPage('fulltext');await ui.getFullText(new AbortController().signal);
    assert.deepEqual(calls,['institutional']);
  }
});

test('failed IEEE address lookup preserves generic failure and does not attempt institutional download', async () => {
  let institutionalCalls = 0;
  const item = {id:1,libraryID:1,isRegularItem:()=>true,getField:f=>f==='extra'?'Source ID: icde:2025:1':f==='url'?'https://ieee-icde.org/2025/research-papers/':'ICDE paper'};
  const ui = workflowUI({getActiveZoteroPane:()=>({getSelectedItems:()=>[item]}),Libraries:{get:()=>({filesEditable:true})}},
    {async findPDFs(){return [{hasPDF:false,title:'ICDE paper',error:'Zotero 原生全文查找：未找到可用全文'}];}},
    {context:{id:1},status:'test',async resolvePaperURL(){throw new Error('IEEE 地址补全：未找到匹配记录');},async download(){institutionalCalls++;}});
  ui.switchPage('fulltext');await ui.getFullText(new AbortController().signal);
  assert.equal(institutionalCalls,0);
  assert.match(ui.lastPDFs[0].error,/IEEE 地址补全：未找到匹配记录/);
  assert.match(ui.lastPDFs[0].error,/Zotero 原生全文查找/);
});

test('full-text flow resolves a missing IEEE URL then continues with login or native retrieval',async()=>{
  for (const loggedIn of [true,false]) {
    const calls=[],fields={title:'ICDE paper',extra:'Source ID: icde:2025:1',url:'https://ieee-icde.org/2025/research-papers/'};
    const item={id:1,libraryID:1,isRegularItem:()=>true,getField:f=>fields[f]||''};
    const ui=workflowUI({getActiveZoteroPane:()=>({getSelectedItems:()=>[item]}),Libraries:{get:()=>({filesEditable:true})}},
      {async findPDFs(){calls.push('generic');return [{title:fields.title,hasPDF:calls.length>1}];}},
      {context:loggedIn?{id:1}:null,status:'test',async resolvePaperURL(){calls.push('resolve');fields.url='https://ieeexplore.ieee.org/document/11113091/';},
        async download(){calls.push('institutional');return [{title:fields.title,hasPDF:true}];}});
    await ui.getFullText(new AbortController().signal);
    assert.deepEqual(calls,['resolve',loggedIn?'institutional':'generic']);assert.equal(ui.lastPDFs[0].hasPDF,true);
  }
});

test('CCF filters use the existing 2026 catalogue and intersect without adding sources',()=>{
  const catalog=JSON.parse(readFileSync(require.resolve('../code/ccf_venues.json'),'utf8')).venues;
  assert.deepEqual(Object.keys(core.CONFERENCE_CATEGORIES),core.CONFERENCES);
  for(const name of core.CONFERENCES){
    const entry=catalog.find(v=>v.abbreviation===(name==='KDD'?'SIGKDD':name)&&v.type==='会议'&&(name!=='FSE'||v.category==='A'));
    assert.deepEqual(core.CONFERENCE_CATEGORIES[name],{field:entry.professional_field,type:entry.type,rank:entry.category});
  }
  assert.deepEqual(core.filterConferences(),core.CONFERENCES);
  assert.deepEqual(core.filterConferences({field:'人工智能',type:'会议',rank:'B'}),['EMNLP','ECCV']);
  assert.deepEqual(core.filterConferences({field:'网络与信息安全',rank:'A'}),['CRYPTO','EUROCRYPT','USENIX Security','CCS','NDSS']);
  assert.deepEqual(core.filterConferences({field:'数据库/数据挖掘/内容检索'}),['ICDE','SIGMOD','KDD','SIGIR','VLDB']);
  assert.deepEqual(core.filterConferences({field:'交叉/综合/新兴'}),['WWW']);
  assert.deepEqual(core.filterConferences({type:'期刊'}),[]);
  assert.deepEqual(core.filterConferences({rank:'C'}),[]);
});

test('conference filters retain compatible selection and results, and disable searches for empty combinations',()=>{
  const ui=workflowUI(),select=ui.$('conference');
  select.options=[];select.add=o=>select.options.push(o);select.replaceChildren=()=>{select.options=[];};
  select.value='ICML';ui.loadedConference='ICML';ui.filtersDirty=false;
  const paper={id:'existing'};ui.papers=ui.candidates=[paper];ui.active=paper;ui.selected.add(paper.id);
  ui.$('venue-field').value='人工智能';ui.updateConferenceOptions();
  assert.equal(select.value,'ICML');assert.equal(ui.filtersDirty,false);
  ui.$('venue-rank').value='B';ui.updateConferenceOptions();
  assert.deepEqual(select.options.map(o=>o.value),['EMNLP','ECCV']);
  assert.equal(select.value,'EMNLP');assert.equal(ui.filtersDirty,true);
  assert.equal(ui.active,paper);assert.equal(ui.candidates[0],paper);assert.equal(ui.selected.has(paper.id),true);
  ui.$('venue-type').value='期刊';ui.updateConferenceOptions();
  assert.equal(select.value,'');assert.equal(select.disabled,true);
  assert.equal(ui.$('fetch').disabled,true);assert.equal(ui.$('refresh-papers').disabled,true);
  assert.equal(ui.$('import').disabled,true);assert.match(ui.$('venue-summary').textContent,/暂无已支持来源/);
  ui.$('venue-type').value='会议';ui.updateConferenceOptions();
  assert.equal(select.value,'EMNLP');assert.equal(select.disabled,false);assert.equal(ui.$('fetch').disabled,false);
});

test('saved OpenReview items recover direct PDFs without any recent import or cached paper metadata',async()=>{
  for (const fields of [{url:'https://openreview.net/forum?id=Note_123-abc'},
    {url:'https://openreview.net/pdf?id=Note_123-abc'},
    {extra:'Other metadata\nOpenReview ID: Note_123-abc\n'}]) {
    // Each fixture loads a fresh import module, modelling reopening the plugin.
    const f=pdfFixture([()=>true],{direct:false,...fields});
    const result=await f.run();
    assert.equal(result[0].hasPDF,true);
    assert.deepEqual(f.calls,['https://openreview.net/pdf?id=Note_123-abc']);
  }
});
test('recovered OpenReview direct failure is reported before native failure; existing attachment is reused',async()=>{
  const url='https://openreview.net/forum?id=Note123';
  const f=pdfFixture([rejectPDF,()=>false],{direct:false,url});
  const result=await f.run();
  assert.deepEqual(f.calls,['https://openreview.net/pdf?id=Note123','resolve','https://example.org/native.pdf']);
  assert.match(result[0].error,/直接下载：HTTP 403.*Zotero 原生全文查找/);
  const existing=pdfFixture([],{direct:false,url,existing:true});
  assert.equal((await existing.run())[0].hasPDF,true);assert.deepEqual(existing.calls,[]);
});
test('OpenReview recovery rejects unrelated hosts and malformed IDs; saved identity takes precedence over transient PDF URLs',async()=>{
  for(const url of ['https://openreview.net.example.org/forum?id=123','https://other.org/forum?id=123',
    'https://user@openreview.net/forum?id=123','https://openreview.net/forum?id=bad%26id','https://openreview.net/group?id=123']) {
    const f=pdfFixture([()=>true],{direct:false,url});await f.run();
    assert.deepEqual(f.calls,['resolve','https://example.org/native.pdf']);
  }
  const f=pdfFixture([()=>true],{url:'https://openreview.net/forum?id=StableID'});await f.run();
  assert.deepEqual(f.calls,['https://openreview.net/pdf?id=StableID']);
});

test('fulltext uses ACM public download first and exposes verification only after 403; other failures keep native fallback',async()=>{
 const item={id:1,libraryID:1,isRegularItem:()=>true,getField:f=>({title:'TSINR',DOI:'10.1145/1.2',url:'https://dl.acm.org/doi/10.1145/1.2'})[f]||''};
 for(const outcome of [{hasPDF:true},{hasPDF:false,verificationRequired:true,error:'ACM HTTP 403'},{hasPDF:false,error:'ACM HTTP 404'}]){
  let native=0,acm=0;const ui=workflowUI({getActiveZoteroPane:()=>({getSelectedItems:()=>[item]}),Libraries:{get:()=>({filesEditable:true})}},
   {findPDFs:async()=>{native++;return [{title:'TSINR',hasPDF:false,error:'native unavailable'}];}});
  ui.acm={canHandle:()=>true,status:'ACM',verificationURL:outcome.verificationRequired?'https://dl.acm.org/doi/10.1145/1.2':'',
   download:async()=>{acm++;return {title:'TSINR',...outcome};}};
  ui.switchPage('fulltext');await ui.getFullText(new AbortController().signal);
  assert.equal(acm,1);assert.equal(native,outcome.hasPDF?0:1);
  assert.equal(ui.$('acm-access').hidden,!outcome.verificationRequired);
  if(native)assert.equal(ui.lastPDFs[0].error,`${outcome.error}；native unavailable`);
 }
});

test('known FSE/OA PDF is tried before ACM access and a successful download stops further requests',async()=>{
 const item={id:1,libraryID:1,isRegularItem:()=>true,getField:f=>({title:'FSE paper',DOI:'10.1145/3715733',url:'https://doi.org/10.1145/3715733'})[f]||''};
 for(const directSuccess of [true,false]){
  const calls=[];
  const ui=workflowUI({getActiveZoteroPane:()=>({getSelectedItems:()=>[item]}),Libraries:{get:()=>({filesEditable:true})}},
   {findPDFs:async(Z,results,options)=>{calls.push([options.direct===false?'native':'direct',options.direct]);
     return [{title:'FSE paper',hasPDF:options.direct!==false&&directSuccess,error:options.direct!==false&&directSuccess?'':'download failed'}];}},
   undefined,{resolve:async()=>({pdfURL:'https://arxiv.org/pdf/2502.01937',arxivID:'2502.01937'})});
  ui.acm={canHandle:()=>true,status:'ACM',verificationURL:'',download:async()=>{calls.push(['acm']);return {title:'FSE paper',hasPDF:false,verificationRequired:true,error:'ACM 403'};}};
  ui.lastImport={results:[{item,paper:{title:'FSE paper',pdfURL:'https://arxiv.org/pdf/2502.01937'}}]};
  ui.switchPage('fulltext');await ui.getFullText(new AbortController().signal);
  assert.deepEqual(calls,directSuccess?[['direct','https://arxiv.org/pdf/2502.01937']]
    :[['direct','https://arxiv.org/pdf/2502.01937'],['acm'],['native',false]]);
  assert.equal(ui.lastPDFs[0].hasPDF,directSuccess);
  if(!directSuccess)assert.match(ui.lastPDFs[0].error,/download failed；ACM 403/);
 }
});
