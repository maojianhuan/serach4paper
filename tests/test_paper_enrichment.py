import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from code import paper_enrichment as enrichment


class PaperEnrichmentTests(unittest.TestCase):
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
