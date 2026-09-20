const { test } = require('node:test');
const assert = require('node:assert/strict');
const core = require('../zotero-plugin/content/core.js');

const query = { terms: ['anomaly detection'], context: ['time series'], fields: core.FIELDS };
function note(id, changes = {}) {
  return { id, number: Number(id), content: {
    venueid: { value: 'ICML.cc/2026/Conference' }, title: { value: 'Anomaly Detection for Time-Series' },
    authors: { value: ['Family Given', 'Research Consortium'] }, abstract: { value: 'An abstract.' },
    pdf: { value: '/pdf?id=' + id }, ...changes
  } };
}
const fetchWith = request => core.fetchAccepted(2026, { request });

test('only the three requested conferences map to the selected year', async () => {
  assert.deepEqual(core.CONFERENCES, ['ICML', 'NeurIPS', 'ICLR']);
  for (const conference of core.CONFERENCES) {
    for (const year of [2025, 2026]) assert.equal(core.venueID(conference, year), `${conference}.cc/${year}/Conference`);
  }
  let requests = 0;
  for (const conference of ['ACL', 'NIPS', '', 'constructor', '../ICML']) {
    await assert.rejects(core.fetchAccepted(2026, { conference, request: async () => { requests++; } }), /请选择/);
  }
  assert.equal(requests, 0);
});

test('every page and normalized paper retain the selected conference and year', async () => {
  for (const conference of core.CONFERENCES) for (const year of [2025, 2026]) {
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
  for (const conference of core.CONFERENCES) {
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

test('research context can occur in another field; unrelated anomaly papers are excluded', () => {
  assert.equal(core.matchPaper({ title: 'Anomaly Detection for Images' }, query), null);
  const result = core.matchPaper({ title: 'Anomaly Detection', abstract: 'We study multivariate time series.' }, query);
  assert.deepEqual(result.evidence.map(hit => hit.field), ['title']);
});

test('matching retains existing stems, inflections, accents and punctuation semantics', () => {
  for (const [text, term] of [['Time-Series Anomalies Detection', 'time serie anomaly detect'],
    ['SELF-EVOLVING AGENTS', 'self evolving agent'], ['Détection', 'detection']]) {
    assert.ok(core.phraseMatches(text, term), `${term}: ${text}`);
  }
  assert.equal(core.phraseMatches('for', 'forecasting'), false);
  assert.equal(core.phraseMatches('time and series', 'time series'), false);
});

test('terms use OR, context uses OR, and selected fields bound both', () => {
  const paper = { title: 'Outlier Detection', abstract: 'A temporal model' };
  const q = { terms: ['anomaly detection', 'outlier detection'], context: ['time series', 'temporal'], fields: core.FIELDS };
  assert.ok(core.matchPaper(paper, q));
  assert.equal(core.matchPaper(paper, { ...q, fields: ['title'] }), null);
  assert.ok(core.matchPaper(paper, { ...q, fields: ['title'], context: [] }));
});

test('invalid query never silently searches everything', () => {
  assert.throws(() => core.validateQuery({ ...query, terms: [] }));
  assert.throws(() => core.validateQuery({ ...query, terms: ['???'] }));
  assert.throws(() => core.validateQuery({ ...query, fields: [] }));
  assert.throws(() => core.validateQuery({ ...query, fields: ['unknown'] }));
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
    saved.venueID = `${conference}.cc/2026/Conference`;
    saved.papers[0] = core.normalizeNote(note('1', { venueid: { value: saved.venueID } }), 2026, conference);
    assert.equal(core.validateMetadata(saved, 2026, conference), saved);
    assert.throws(() => core.validateMetadata(saved, 2025, conference), /年份不一致/);
    for (const other of core.CONFERENCES.filter(c => c !== conference)) {
      assert.throws(() => core.validateMetadata(saved, 2026, other), /会议或年份不一致/);
      const wrongPaper = { ...saved, papers: [{ ...saved.papers[0], venueID: `${other}.cc/2026/Conference` }] };
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

test('evidence note cannot interpret source text as HTML', () => {
  const html = core.evidenceNote({ ...core.normalizeNote(note('1'), 2026),
    evidence: [{ field: 'abstract', term: '<script>', text: '<img src=x onerror=alert(1)>' }], bibtex: '@x{a&b}' }, query);
  assert.ok(html.includes('&lt;img'));
  assert.ok(html.includes('a&amp;b'));
  assert.ok(html.includes('Context: time series'));
  assert.equal(html.includes('<script>'), false);
});
