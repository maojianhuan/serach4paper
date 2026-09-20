import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch
from urllib.error import HTTPError, URLError

from code import paper_enrichment as enrichment


class PaperEnrichmentTests(unittest.TestCase):
    def test_doi_bibtex_enrichment_checks_identity_and_preserves_source_and_provenance(self):
        row = dict(conference="TODS", year=2025, source_record_id="paper-one", title="A paper",
                   doi="HTTPS://doi.org/10.1234/EXAMPLE%23v1", bibtex="")
        bibtex = '@article{Example, title={{A} paper}, DOI={10.1234/Example#v1}, year={2025}}'
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with patch.object(enrichment, "urlopen") as request, \
                 patch.object(enrichment, "utc_now", return_value="2026-09-20T12:00:00+00:00"):
                response = request.return_value.__enter__.return_value
                response.read.return_value = bibtex.encode("utf-8")
                response.geturl.return_value = "https://api.crossref.org/example/transform"
                rows, failures = enrichment.enrich_bibtex([row], root)
            request.assert_called_once()
            sent = request.call_args.args[0]
            self.assertEqual(sent.full_url, "https://doi.org/10.1234/example%23v1")
            self.assertEqual(sent.get_header("Accept"), "application/x-bibtex")
            self.assertEqual(failures, {})
            self.assertEqual(row["bibtex"], "")
            self.assertEqual(rows[0]["doi"], row["doi"])
            self.assertEqual(rows[0]["bibtex"], bibtex)
            self.assertEqual(rows[0]["bibtex_source"], "doi_content_negotiation")
            self.assertEqual(rows[0]["bibtex_source_url"], "https://api.crossref.org/example/transform")
            self.assertEqual(rows[0]["bibtex_retrieved_at"], "2026-09-20T12:00:00+00:00")
            path = root / "enrichment" / "bibtex.jsonl"
            before = path.read_bytes()
            record = json.loads(before)
            self.assertEqual(record["doi"], "10.1234/example#v1")
            self.assertEqual(record["status"], "success")
            with patch.object(enrichment, "urlopen", side_effect=AssertionError("network forbidden")):
                self.assertEqual(enrichment.load_cached_bibtex([row], root), rows)
                reused, errors = enrichment.enrich_bibtex([row], root)
                self.assertEqual((reused, errors), (rows, {}))
                authoritative = dict(row, bibtex="@article{source, title={Original citation}}")
                self.assertEqual(enrichment.load_cached_bibtex([authoritative], root), [authoritative])
                self.assertEqual(enrichment.enrich_bibtex([authoritative], root), ([authoritative], {}))
            self.assertEqual(path.read_bytes(), before)

    def test_doi_bibtex_enrichment_reports_each_failure_and_keeps_successes(self):
        rows = [dict(conference="TODS", year=2025, source_record_id=name, title=name, doi=doi)
                for name, doi in (("Existing", ""), ("Missing DOI", ""), ("Invalid DOI", "not-a-doi"),
                                  ("Wrong identity", "10.1234/wrong"), ("Network failure", "10.1234/network"),
                                  ("Matched paper", "10.1234/matched"))]
        rows[0]["bibtex"] = "@article{original, title={Original}}"
        wrong, matched = MagicMock(), MagicMock()
        wrong.__enter__.return_value.read.return_value = b"@article{wrong, title={Wrong paper}, doi={10.1234/other}}"
        matched.__enter__.return_value.read.return_value = b"@article{matched, title={Matched paper}, doi={10.1234/matched}}"
        matched.__enter__.return_value.geturl.return_value = "https://api.crossref.org/matched/transform"
        with tempfile.TemporaryDirectory() as directory, \
             patch.object(enrichment, "urlopen", side_effect=[wrong, URLError("offline"), matched]) as request:
            result, failures = enrichment.enrich_bibtex(rows, Path(directory))
            self.assertEqual(request.call_count, 3)
            self.assertEqual(len(failures), 4)
            self.assertEqual(result[0], rows[0])
            self.assertEqual([row["bibtex_status"] for row in result[1:]],
                             ["no_doi", "invalid_doi", "failed", "failed", "success"])
            for row, message in zip(result[1:5], ("缺少 DOI", "DOI 格式无效", "DOI 不匹配", "offline")):
                self.assertIn(message, row["bibtex_error"])
                self.assertEqual(row["bibtex"], "")
                self.assertIn(enrichment.paper_key(row), failures)
            restored = enrichment.load_cached_bibtex(rows, Path(directory))
            self.assertEqual(restored, result)

    def test_doi_bibtex_rejects_unverified_responses_without_retry(self):
        bodies = [b"<html>Access denied</html>",
                  b"@article{key, title={Missing DOI}}",
                  b"@article{key, title={Wrong DOI}, doi={10.1234/other}}",
                  b"@article{a, title={A}, doi={10.1234/test}} @article{b, title={B}, doi={10.1234/test}}",
                  b"\xff"]
        for body in bodies:
            with self.subTest(body=body), patch.object(enrichment, "urlopen") as request:
                request.return_value.__enter__.return_value.read.return_value = body
                with self.assertRaises(ValueError):
                    enrichment._fetch_doi_bibtex("10.1234/test")
                request.assert_called_once()
        error = HTTPError("https://doi.org/10.1234/test", 404, "Not Found", {}, None)
        with patch.object(enrichment, "urlopen", side_effect=error) as request:
            with self.assertRaisesRegex(RuntimeError, "HTTP 404"):
                enrichment._fetch_doi_bibtex("10.1234/test")
            request.assert_called_once()

    def test_doi_bibtex_existing_citation_needs_no_doi_request_or_cache_write(self):
        row = dict(title="Original", bibtex="@article{source, title={Original}}")
        with tempfile.TemporaryDirectory() as directory, \
             patch.object(enrichment, "urlopen", side_effect=AssertionError("network forbidden")):
            self.assertEqual(enrichment.enrich_bibtex([row], Path(directory)), ([row], {}))
            self.assertEqual(list(Path(directory).iterdir()), [])

    def test_doi_bibtex_failed_lookup_is_retried_only_by_explicit_enrichment(self):
        row = dict(source_record_id="one", doi="doi:10.1234/test", title="A paper")
        bibtex = "@article{key, title={A paper}, doi={10.1234/test}}"
        with tempfile.TemporaryDirectory() as directory, \
             patch.object(enrichment, "_fetch_doi_bibtex", side_effect=[RuntimeError("offline"),
                                                                      (bibtex, "https://doi.org/10.1234/test")]) as fetch:
            root = Path(directory)
            failed, failures = enrichment.enrich_bibtex([row], root)
            self.assertEqual(len(failures), 1)
            restored = enrichment.load_cached_bibtex([row], root)
            self.assertEqual(restored, failed)
            fetch.assert_called_once_with("10.1234/test")
            retried, failures = enrichment.enrich_bibtex(restored, root)
            self.assertEqual(fetch.call_count, 2)
            self.assertEqual(failures, {})
            self.assertEqual(retried[0]["bibtex"], bibtex)
            self.assertEqual(retried[0]["bibtex_error"], "")
            self.assertEqual(enrichment.load_cached_bibtex([row], root), retried)

    def test_doi_bibtex_corrupt_or_mismatched_saved_record_fails_explicitly(self):
        row = dict(source_record_id="one", title="A paper", doi="10.1234/current")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "enrichment" / "bibtex.jsonl"
            path.parent.mkdir()
            path.write_text("{broken", encoding="utf-8")
            for action in (enrichment.load_cached_bibtex, enrichment.enrich_bibtex):
                with self.subTest(action=action.__name__), self.assertRaisesRegex(ValueError, "Cannot read enrichment cache"):
                    action([row], root)
            for record, message in ((dict(doi="10.1234/old", bibtex="@article{key, title={Old}}"), "DOI 不匹配"),
                                    (dict(doi="10.1234/current", bibtex=""), "缺少引用内容")):
                enrichment.collector.write_jsonl(path, [dict(record, paper_id=enrichment.paper_key(row), status="success")])
                for action in (enrichment.load_cached_bibtex, enrichment.enrich_bibtex):
                    with self.subTest(record=record, action=action.__name__), self.assertRaisesRegex(ValueError, message):
                        action([row], root)

    def test_bibtex_preserves_source_text_and_selection_order(self):
        first = '@inproceedings{z2025,\n  title = {{LLM} 与记忆},\n  author = {M{\\\"u}ller and 王明},\n  year = {2025}\n}'
        second = '@article{a2024, title={Second paper}, journal={Research \\& Science}, year={2024}}'
        rows = [{"title": "LLM 与记忆", "bibtex": first}, {"title": "Second paper", "bibtex": second}]
        with tempfile.TemporaryDirectory() as directory:
            output = enrichment.export_bibtex(iter(rows), Path(directory) / "papers.bib")
            self.assertEqual(output.read_text(encoding="utf-8"), first + "\n\n" + second + "\n")

    def test_bibtex_missing_records_are_listed_without_creating_or_overwriting_file(self):
        rows = [{"title": "Available", "bibtex": "@article{one, title={Available}}"},
                {"title": "Missing one"}, {"title": "Missing two", "bibtex": "  \n"}]
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "papers.bib"
            for existing in (False, True):
                with self.subTest(existing=existing):
                    if existing:
                        output.write_text("Keep existing bibliography", encoding="utf-8")
                    with self.assertRaises(ValueError) as error:
                        enrichment.export_bibtex(iter(rows), output)
                    self.assertIn("2 篇论文缺少来源 BibTeX", str(error.exception))
                    self.assertIn("Missing one", str(error.exception))
                    self.assertIn("Missing two", str(error.exception))
                    if existing:
                        self.assertEqual(output.read_text(), "Keep existing bibliography")
                    else:
                        self.assertFalse(output.exists())

    def test_bibtex_duplicate_citation_keys_report_both_papers_before_writing(self):
        rows = [{"title": title, "bibtex": f"@inproceedings{{Wang_2026_CVPR, title={{{title}}}}}"}
                for title in ("First paper", "Second paper")]
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "papers.bib"
            output.write_text("Keep existing bibliography", encoding="utf-8")
            with self.assertRaises(ValueError) as error:
                enrichment.export_bibtex(rows, output)
            for text in ("引用键重复", "Wang_2026_CVPR", "First paper", "Second paper"):
                self.assertIn(text, str(error.exception))
            self.assertEqual(output.read_text(), "Keep existing bibliography")

    def test_bibtex_empty_selection_or_unrecognized_record_leaves_file_unchanged(self):
        selections = [[], [{"title": "Broken", "bibtex": "not BibTeX"}],
                      [{"title": "Truncated", "bibtex": "@article{key, title={Paper}"}],
                      [{"title": "No key", "bibtex": "@article{, title={Paper}}"}],
                      [{"title": "Two entries", "bibtex": "@article{a, title={A}}\n@article{b, title={B}}"}]]
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "papers.bib"
            output.write_text("Keep existing bibliography", encoding="utf-8")
            for rows in selections:
                with self.subTest(rows=rows), self.assertRaises(ValueError):
                    enrichment.export_bibtex(rows, output)
                self.assertEqual(output.read_text(), "Keep existing bibliography")

    def test_reading_list_preserves_selection_order_and_escapes_markdown(self):
        rows = [{"title": "A [test] *paper*", "abstract": "Use <memory> and [tools].",
                 "source_url": "https://example.test/a paper(x)", "conference": "ICLR", "year": 2025},
                {"title": "Second paper"}]
        with tempfile.TemporaryDirectory() as directory:
            output = enrichment.export_reading_list(rows, Path(directory) / "reading.md")
            text = output.read_text(encoding="utf-8")
        self.assertIn(r"A \[test\] \*paper\*", text)
        self.assertIn(r"Use \<memory\> and \[tools\].", text)
        self.assertIn("https://example.test/a%20paper%28x%29", text)
        self.assertLess(text.index("## 1."), text.index("## 2."))
        self.assertIn("未提供摘要", text)

    def test_reading_list_keeps_publisher_and_open_access_links(self):
        row = dict(title='Journal paper', source_url='https://doi.org/10.1/example',
                   oa_landing_url='https://repository.example/article',
                   oa_pdf_url='https://repository.example/paper.pdf', oa_version='acceptedVersion')
        with tempfile.TemporaryDirectory() as directory:
            output = enrichment.export_reading_list([row], Path(directory) / 'reading.md').read_text()
        for field in ('source_url', 'oa_landing_url', 'oa_pdf_url', 'oa_version'):
            self.assertIn(row[field], output)
        self.assertNotIn('None', output)

    def test_cached_pdf_requires_existing_file_and_does_not_write(self):
        row = {"conference": "ICLR", "year": 2025, "openreview_id": "one"}
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            key = enrichment.paper_key(row)
            manifest = root / "enrichment" / "pdf_manifest.jsonl"
            enrichment.collector.write_jsonl(manifest, [{"paper_id": key, "status": "downloaded"}])
            self.assertNotIn("pdf_local_path", enrichment.load_cached_pdfs([row], root)[0])
            pdf = root / "enrichment" / "pdf" / f"{key}.pdf"
            pdf.parent.mkdir()
            pdf.write_bytes(b"%PDF-1.4 test")
            before = manifest.read_bytes()
            restored = enrichment.load_cached_pdfs([row], root)[0]
            self.assertEqual(restored["pdf_local_path"], str(pdf.resolve()))
            self.assertEqual(restored["pdf_status"], "downloaded")
            self.assertEqual(manifest.read_bytes(), before)

    def test_local_abstract_is_normalized_and_cached(self):
        row = {
            "conference": "ICLR",
            "year": 2026,
            "source_record_id": "forum-1",
            "title": "A Paper",
            "authors": "Alice Example",
            "abstract": "An existing abstract.",
        }
        with tempfile.TemporaryDirectory() as directory:
            enriched, failures = enrichment.enrich_abstracts((row,), Path(directory))
            self.assertEqual(failures, {})
            self.assertEqual(enriched[0]["abstract_status"], "success")
            self.assertEqual(enriched[0]["abstract_source"], "local_dataset")
            cache = Path(directory) / "enrichment" / "abstracts.jsonl"
            self.assertTrue(cache.is_file())

    def test_semantic_scholar_requires_a_strong_title_match(self):
        row = {
            "conference": "AAAI",
            "year": 2026,
            "source_record_id": "paper-1",
            "title": "Reliable Graph Learning",
            "authors": "Alice Example",
        }
        payload = {
            "data": [
                {
                    "title": "Reliable Graph Learning",
                    "abstract": "A matched abstract.",
                    "authors": [{"name": "Alice Example"}],
                    "year": 2026,
                }
            ]
        }
        with patch.object(enrichment.collector, "request_json", return_value=(payload, b"", {})):
            match = enrichment._semantic_scholar_abstract(row)
        self.assertIsNotNone(match)
        assert match is not None
        self.assertEqual(match[0], "A matched abstract.")
        self.assertEqual(match[2], 1.0)

    def test_export_contains_ai_friendly_structured_record(self):
        row = {
            "conference": "ICML",
            "year": 2026,
            "source_record_id": "paper-1",
            "title": "A Paper",
            "authors": "Alice Example; Bob Example",
            "abstract": "An abstract.",
            "abstract_status": "success",
            "abstract_source": "openreview",
            "keywords": "graph; learning",
            "source_url": "https://example.test/source",
            "pdf_local_path": "C:/papers/paper.pdf",
        }
        with tempfile.TemporaryDirectory() as directory:
            output = enrichment.export_ai_jsonl((row,), Path(directory) / "papers.jsonl")
            records = [json.loads(line) for line in output.read_text(encoding="utf-8").splitlines()]
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["authors"], ["Alice Example", "Bob Example"])
        self.assertEqual(records[0]["abstract"], "An abstract.")
        self.assertEqual(records[0]["local_files"]["pdf_path"], "C:/papers/paper.pdf")

    def test_pdf_without_public_url_is_reported_without_network_access(self):
        row = {
            "conference": "AAAI",
            "year": 2026,
            "source_record_id": "paper-1",
            "title": "A Paper",
        }
        with tempfile.TemporaryDirectory() as directory:
            rows, failures = enrichment.download_pdfs((row,), Path(directory))
        self.assertEqual(rows[0]["pdf_status"], "no_pdf_url")
        self.assertIn(rows[0]["paper_id"], failures)


if __name__ == "__main__":
    unittest.main()
