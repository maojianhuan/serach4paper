import json
import tempfile
import unittest
from collections import Counter
from pathlib import Path
from unittest.mock import patch
from code import fetch_openreview_accepted as c
from code import fetch_venue_metadata as metadata
from code import paper_enrichment as enrichment
from code import paper_search
from code.paper_ui import filter_catalog


class CatalogueTests(unittest.TestCase):
    def test_full_catalogue_preserves_old_conference_protocols(self):
        entries = c.CCF_CATALOG['venues']
        self.assertEqual(len(entries), 677)
        self.assertEqual(sum(len(e['professional_fields']) for e in entries), 681)
        self.assertEqual(Counter(e['type'] for e in entries), {'会议': 386, '期刊': 291})
        self.assertEqual(len(filter_catalog('A', '会议')), 58)
        self.assertEqual(len(filter_catalog('B', '会议')), 132)
        self.assertEqual(len(filter_catalog('C', '会议')), 196)
        self.assertEqual(c.CONFERENCE_SPECS['ICLR'].source_kind, 'openreview')
        self.assertEqual(c.normalize_conference_argument('FSE'), 'FSE')
        self.assertNotEqual(c.CONFERENCE_SPECS['FSE'].dblp_collection, c.CONFERENCE_SPECS['FSE_CRYPTO'].dblp_collection)
        self.assertEqual(filter_catalog('A', '期刊', query='TODS')[0]['key'], 'J_TODS')
        for entry in entries:
            self.assertEqual(c.normalize_conference_argument(entry['key']), entry['key'])


class MetadataTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def hit(self, n, **overrides):
        info = dict(key=f'journals/tods/Test{n}', title=f'Paper {n}', year='2025',
                    type='Journal Articles', venue='ACM Trans. Database Syst.',
                    authors={'author': {'text': 'Example Author'}}, doi=f'10.1/{n}')
        info.update(overrides)
        return {'info': info}

    def page(self, hits, total=2, stream='journals/tods'):
        return {'result': {'query': f':facetid:stream:"streams/{stream}"',
                           'hits': {'@total': str(total), 'hit': hits}}}

    def test_pagination_and_local_journal_search(self):
        with patch.object(metadata, 'fetch_json', side_effect=[self.page([self.hit(1)]), self.page([self.hit(2)])]) as fetch:
            manifest, rows, _ = metadata.build_outputs(self.root, spec=c.CONFERENCE_SPECS['J_TODS'], year=2025)
        self.assertEqual(fetch.call_count, 2)
        self.assertIn('f=1', fetch.call_args.args[0])
        self.assertEqual(manifest['counts']['published_paper_count'], 2)
        result = paper_search.search_local(self.root, [{'conference': 'J_TODS', 'year': 2025}], query='paper')
        self.assertEqual(len(result['candidates']), 2)
        self.assertEqual(result['coverage'][0]['missing_abstract_count'], 2)
        self.assertEqual(result['coverage'][0]['ccf_type'], '期刊')
        self.assertIsNotNone(c.load_cached_conference_outputs(self.root, spec=c.CONFERENCE_SPECS['J_TODS'], year=2025, include_rows=True))

    def test_rejects_wrong_venue_year_duplicates_and_truncation(self):
        for pages in ([self.page([self.hit(1, year='2024')])],
                      [self.page([self.hit(1)], stream='journals/other')],
                      [self.page([self.hit(1)]), self.page([self.hit(1)])],
                      [self.page([self.hit(1)]), self.page([])]):
            with self.subTest(pages=pages), patch.object(metadata, 'fetch_json', side_effect=pages):
                with self.assertRaises(RuntimeError):
                    metadata.build_outputs(self.root, spec=c.CONFERENCE_SPECS['J_TODS'], year=2025)
            self.assertFalse((self.root / '2025/J_TODS/source_manifest.json').exists())

    def test_conference_accepts_journal_papers_in_confirmed_stream(self):
        hits = [self.hit(1, key='journals/cscw/LeeCR17', year='2017'),
                self.hit(2, key='conf/ecscw/example17', year='2017', type='Conference and Workshop Papers'),
                self.hit(3, key='conf/ecscw/2017', year='2017', type='Editorship')]
        with patch.object(metadata, 'fetch_json', return_value=self.page(hits, total=3, stream='conf/ecscw')):
            manifest, rows, _ = metadata.build_outputs(self.root, spec=c.CONFERENCE_SPECS['ECSCW'], year=2017)
        self.assertEqual({row['source_record_id'] for row in rows}, {'journals/cscw/LeeCR17', 'conf/ecscw/example17'})
        self.assertEqual(manifest['counts']['excluded_record_count'], 1)
        result = paper_search.search_local(self.root, [{'conference': 'ECSCW', 'year': 2017}], query='paper')
        self.assertEqual(len(result['candidates']), 2)

    def test_missing_stream_confirmation_is_not_accepted(self):
        page = self.page([self.hit(1)], total=1)
        page['result']['query'] = 'unscoped search'
        with patch.object(metadata, 'fetch_json', return_value=page):
            with self.assertRaisesRegex(RuntimeError, 'requested venue stream'):
                metadata.dblp_rows(c.CONFERENCE_SPECS['J_TODS'], 2025, self.root)

    def test_combined_manifest_preserves_requested_scope_including_failures(self):
        manifest = c.build_combined_outputs(
            self.root, year=2025, requested_keys=['J_TODS', 'J_JATS'],
            manifests={'J_TODS': {'counts': {'accepted_paper_count': 0}}},
            csv_rows=[], jsonl_rows=[], failures={'J_JATS': 'source unavailable'})
        self.assertEqual(manifest['conferences_requested'], ['J_TODS', 'J_JATS'])
        self.assertEqual(manifest['conferences_completed'], ['J_TODS'])
        saved = json.loads((self.root / '2025/ALL/source_manifest.json').read_text())
        self.assertEqual(saved['conferences_requested'], ['J_TODS', 'J_JATS'])

    def test_cli_reports_new_journal_snapshot(self):
        from contextlib import redirect_stdout
        import io
        with patch.object(metadata, 'fetch_json', return_value=self.page([self.hit(1)], total=1)):
            with redirect_stdout(io.StringIO()) as output:
                status = c.main(['--conference', 'J_TODS', '--year', '2025', '--output-dir', str(self.root)])
        self.assertEqual(status, 0)
        self.assertEqual(json.loads(output.getvalue())['completed']['J_TODS']['accepted'], 1)

    def test_crossref_earliest_publication_year_and_issn(self):
        article = {'DOI':'10.123/ABC', 'type':'journal-article', 'title':['A <i>study</i>'],
                   'ISSN':['2833-0528'], 'published':{'date-parts':[[2024,12]]},
                   'published-print':{'date-parts':[[2025]]}, 'abstract':'<p>Abstract</p>'}
        with patch.object(metadata, 'fetch_json', return_value={'message':{'items':[article], 'total-results':1}}):
            manifest, rows, _ = metadata.build_outputs(self.root, spec=c.CONFERENCE_SPECS['J_JATS'], year=2024)
            self.assertEqual(rows[0]['title'], 'A study')
            self.assertEqual(rows[0]['abstract'], 'Abstract')
            self.assertEqual(rows[0]['doi'], '10.123/abc')
            self.assertIn('最早', manifest['publication_year_policy'])
            article['ISSN']=['0000-0000']
            with self.assertRaisesRegex(RuntimeError, 'ISSN'):
                metadata.build_outputs(self.root, spec=c.CONFERENCE_SPECS['J_JATS'], year=2024)

    def test_access_challenge_is_not_empty_success(self):
        with patch.object(c, 'request_bytes', return_value=(b'<html>verify access</html>', {})):
            with self.assertRaisesRegex(RuntimeError, 'JSON'):
                metadata.fetch_json('https://example.test', self.root, 0)

    def test_local_pdf_association_and_missing_file(self):
        row = dict(conference='J_TODS', year=2025, source_record_id='journals/tods/test', title='Example')
        pdf = self.root/'user.pdf'
        pdf.write_bytes(b'%PDF-1.7\nexample')
        linked = enrichment.link_local_pdf(row, pdf, self.root)
        self.assertEqual(linked['pdf_status'], 'linked')
        self.assertEqual(enrichment.load_cached_pdfs([row], self.root)[0]['pdf_local_path'], str(pdf))
        with patch.object(enrichment, '_download_one_pdf') as download:
            restored, failures = enrichment.download_pdfs([row], self.root)
            download.assert_not_called()
            self.assertFalse(failures)
            self.assertEqual(restored[0]['pdf_status'], 'linked')
        pdf.unlink()
        self.assertNotIn('pdf_local_path', enrichment.load_cached_pdfs([row], self.root)[0])

    def test_oa_links_are_distinct_from_pdf_access(self):
        import io
        payload = {'doi':'10.123/test', 'best_oa_location':{'url_for_landing_page':'https://example.test/article','version':'acceptedVersion'}}
        with patch.object(enrichment, 'urlopen', return_value=io.BytesIO(json.dumps(payload).encode())):
            row = enrichment.lookup_open_access({'doi':'10.123/test'}, 'reader@example.test')
        self.assertEqual(row['oa_status'], 'found')
        self.assertEqual(enrichment.resolve_pdf_url(row), '')
        self.assertEqual(row['oa_version'], 'acceptedVersion')
        self.assertEqual(enrichment.lookup_open_access({}, 'reader@example.test')['oa_status'], 'no_doi')
