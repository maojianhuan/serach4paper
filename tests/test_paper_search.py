import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from code import fetch_openreview_accepted as collector
from code import paper_enrichment as enrichment
from code import paper_search as search
from code import query_research_topic as topic
from code import query_target_papers as titles


def make_snapshot(root, year=2025, conference="ICLR", source_kind="openreview", rows=None):
    spec = collector.CONFERENCE_SPECS[conference]
    if rows is None:
        rows = [{"conference": conference, "year": year, "openreview_id": f"Paper{year}",
                 "source_record_id": f"record-{year}", "title": "An Autonomous Assistant",
                 "abstract": "", "venueid": spec.venue_id(year) if source_kind == "openreview" else ""}]
    directory = root / str(year) / conference
    csv_path = directory / f"{conference}_{year}_accepted_papers.csv"
    collector.write_csv(csv_path, rows, collector.CSV_FIELDS)
    manifest = dict(conference=conference, year=year, source_kind=source_kind,
                    counts={"accepted_paper_count": len(rows)}, fetched_at_utc="2026-01-01T00:00:00Z",
                    collection_status="fetched_from_sources", warnings=[])
    collector.write_json(directory / "source_manifest.json", manifest)
    return rows


def topic_config(years=(2025,)):
    return dict(targets=[dict(conference="ICLR", year=year) for year in years],
                search_fields=["title", "abstract"], context_anchors=["agent"],
                query_groups={"memory": {"terms": ["episodic memory", "memory retrieval"]}})


class LocalSearchTests(unittest.TestCase):
    def test_cached_abstract_recalls_previously_unmatched_paper_without_network_or_writes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            rows = make_snapshot(root)
            cache_path = root / "enrichment" / "abstracts.jsonl"
            collector.write_jsonl(cache_path, [dict(paper_id=enrichment.paper_key(rows[0]), status="success",
                                                   abstract="An agent uses episodic memory and memory retrieval.",
                                                   source="openreview", source_url="https://example.test/paper")])
            before = {str(path): path.read_bytes() for path in root.rglob('*') if path.is_file()}
            with patch.object(enrichment, "urlopen", side_effect=AssertionError("network forbidden")):
                result = search.search_local(root, [], config=topic_config())
            after = {str(path): path.read_bytes() for path in root.rglob('*') if path.is_file()}
            self.assertEqual(before, after)
            self.assertEqual(len(result["candidates"]), 1)
            self.assertEqual(result["candidates"][0]["matched_fields"], ["abstract"])
            self.assertEqual(result["candidates"][0]["abstract_source"], "openreview")
            self.assertEqual(result["coverage"][0]["missing_abstract_count"], 0)
            abstract_stats = next(item for item in result["summary"] if item["matched_field"] == "abstract")
            self.assertEqual(abstract_stats["raw_match_count"], 2)
            self.assertEqual(abstract_stats["deduplicated_match_count"], 1)

    def test_snapshot_abstract_is_not_overwritten_by_cached_text(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            row = make_snapshot(root)[0]
            row["abstract"] = "Authoritative abstract"
            collector.write_jsonl(root / "enrichment" / "abstracts.jsonl", [dict(
                paper_id=enrichment.paper_key(row), status="success", abstract="Other text")])
            self.assertEqual(enrichment.load_cached_abstracts([row], root)[0]["abstract"], row["abstract"])

    def test_years_zero_matches_and_missing_targets_are_distinct(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            make_snapshot(root, 2024)
            rows = make_snapshot(root, 2025)
            rows[0]["abstract"] = "Agent with episodic memory"
            make_snapshot(root, 2025, rows=rows)
            result = search.search_local(root, [], config=topic_config((2024, 2025, 2026)))
            self.assertEqual([item["status"] for item in result["coverage"]], ["searched", "searched", "missing"])
            self.assertEqual([item["match_count"] for item in result["coverage"]], [0, 1, 0])
            self.assertFalse(result["complete"])
            self.assertEqual({item["year"] for item in result["summary"]}, {"2024", "2025", "2026"})
            self.assertIn("覆盖范围不完整", search.coverage_text(result))

    def test_official_source_does_not_require_openreview_venue(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            make_snapshot(root, conference="AAAI", source_kind="official")
            result = search.search_local(root, [dict(conference="AAAI", year=2025)], query="assistant")
            self.assertTrue(result["complete"])
            self.assertEqual(len(result["candidates"]), 1)

    def test_invalid_snapshot_and_cache_fail_explicitly(self):
        for corruption in ("count", "identity", "cache"):
            with self.subTest(corruption=corruption), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                rows = make_snapshot(root)
                if corruption == "count":
                    path = root / "2025" / "ICLR" / "source_manifest.json"
                    manifest = json.loads(path.read_text())
                    manifest["counts"]["accepted_paper_count"] = 2
                    collector.write_json(path, manifest)
                elif corruption == "identity":
                    rows[0]["conference"] = "ICML"
                    make_snapshot(root, rows=rows)
                else:
                    path = root / "enrichment" / "abstracts.jsonl"
                    path.parent.mkdir()
                    path.write_text("{broken")
                with self.assertRaises(ValueError):
                    search.search_local(root, [], config=topic_config())

    def test_local_cli_and_matching_functions_agree(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            rows = make_snapshot(root)
            rows[0]["title"] = "Time Series Anomaly Detection"
            make_snapshot(root, rows=rows)
            query = '"time series" AND anomal* AND NOT forecasting'
            result = search.search_local(root, [dict(conference="ICLR", year=2025)], query=query)
            expected = [row["openreview_id"] for row in rows if titles.match_title(row["title"], query) is not None]
            self.assertEqual([row["openreview_id"] for row in result["candidates"]], expected)
            output = root / "results"
            with redirect_stdout(io.StringIO()):
                status = search.main(["--query", query, "--conference", "ICLR", "--years", "2025",
                                      "--snapshot-output-root", str(root), "--output-dir", str(output)])
            self.assertEqual(status, 0)
            exported = [json.loads(line) for line in (output / "candidate_papers.jsonl").read_text().splitlines()]
            self.assertEqual([row["openreview_id"] for row in exported], expected)
            self.assertTrue(json.loads((output / "search_report.json").read_text())["complete"])

    def test_topic_cli_matches_local_result(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            rows = make_snapshot(root)
            rows[0]["abstract"] = "Agent episodic memory"
            make_snapshot(root, rows=rows)
            config = topic_config()
            config_path = root / "query.json"
            collector.write_json(config_path, config)
            result = search.search_local(root, [], config=config)
            output = root / "results"
            with redirect_stdout(io.StringIO()):
                status = search.main(["--config", str(config_path), "--snapshot-output-root", str(root),
                                      "--output-dir", str(output)])
            self.assertEqual(status, 0)
            report = json.loads((output / "search_report.json").read_text())
            self.assertEqual(report["summary"], result["summary"])
            self.assertEqual(report["coverage"], result["coverage"])

    def test_phrase_matches_complete_tokens(self):
        self.assertIsNone(titles.match_title("Agent Memorybank", '"agent memory"'))
        self.assertIsNotNone(titles.match_title("Agent Memory", '"agent memory"'))

    def test_invalid_topic_fields_are_rejected(self):
        config = topic_config()
        config["search_fields"] = []
        with self.assertRaises(ValueError):
            search.validate_config(config)

    def test_source_record_ids_are_scoped_to_conference_and_year(self):
        rows = [dict(conference=conference, year=year, source_record_id="1",
                     title="Episodic memory for agents")
                for conference, year in (("ICLR", 2025), ("ICLR", 2026), ("AAAI", 2025))]
        candidates, _ = topic.search_papers(rows, topic_config())
        self.assertEqual(len(candidates), 3)

    def test_snippet_locates_complete_matching_phrase(self):
        source = "Memory allocation. " + "unrelated words " * 35 + "An agent uses memory retrieval effectively."
        snippet = topic.matched_snippet(source, "memory retrieval")
        self.assertIn("memory retrieval", snippet)

    def test_empty_and_unpublished_snapshots_are_not_complete_coverage(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            make_snapshot(root, 2025, rows=[])
            make_snapshot(root, 2026, rows=[])
            path = root / "2026" / "ICLR" / "source_manifest.json"
            manifest = json.loads(path.read_text())
            manifest["collection_status"] = "no_public_accepted_papers"
            collector.write_json(path, manifest)
            result = search.search_local(root, [], config=topic_config((2025, 2026)))
            self.assertEqual([item["status"] for item in result["coverage"]], ["empty", "unavailable"])
            self.assertFalse(result["complete"])


if __name__ == "__main__":
    unittest.main()
