import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from code import paper_enrichment as enrichment


class PaperEnrichmentTests(unittest.TestCase):
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
