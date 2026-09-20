import csv
import io
import json
import shlex
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

from code import fetch_openreview_accepted as collector
from code import query_research_topic as topic


CONFIG = {
    "search_fields": ["title", "abstract", "keywords", "tldr"],
    "context_anchors": ["agent", "agentic", "llm", "language model"],
    "query_groups": {
        "memory": {"terms": ["episodic memory", "memory retrieval"]},
        "evolving": {"direct": True, "terms": ["self-evolving agent"]},
        "tools": {"terms": ["tool creation"]},
    },
}


class ArgumentTests(unittest.TestCase):
    def test_config_and_output_directory_are_required(self):
        for argv, missing in (([], "--config"),
                              (["--output-dir", "results"], "--config"),
                              (["--config", "query.json"], "--output-dir")):
            with self.subTest(argv=argv):
                stderr = io.StringIO()
                with redirect_stderr(stderr), self.assertRaises(SystemExit) as raised:
                    topic.main(argv)
                self.assertEqual(raised.exception.code, 2)
                self.assertIn(missing, stderr.getvalue())


class MatchingTests(unittest.TestCase):
    def test_title_match(self):
        hits = topic.find_matches({"title": "Episodic Memory for LLM Agents"}, CONFIG)
        self.assertEqual(hits[0]["field"], "title")

    def test_abstract_match_when_title_does_not(self):
        row = {"title": "Persistent Assistants", "abstract": "Our language model agent performs memory retrieval."}
        self.assertEqual(topic.find_matches(row, CONFIG)[0]["field"], "abstract")

    def test_hyphen_space_and_case_variants(self):
        self.assertTrue(topic.phrase_matches("A SELF EVOLVING AGENT", "self-evolving agent"))
        self.assertTrue(topic.phrase_matches("Self-Evolving Agents", "self evolving agent"))

    def test_evidence_is_merged_across_groups(self):
        row = {"openreview_id": "one", "conference": "ICLR", "year": 2026,
               "title": "Self-Evolving Agent with Episodic Memory", "abstract": "An LLM agent."}
        candidates, _ = topic.search_papers([row], CONFIG)
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0]["matched_query_groups"], ["evolving", "memory"])

    def test_duplicate_records_are_merged(self):
        base = {"openreview_id": "same", "conference": "ICML", "year": 2026,
                "title": "Episodic Memory for Agents", "abstract": "LLM agent"}
        candidates, _ = topic.search_papers([base, dict(base)], CONFIG)
        self.assertEqual(len(candidates), 1)

    def test_case_distinct_source_ids_remain_separate(self):
        for field in ("openreview_id", "source_record_id"):
            with self.subTest(field=field):
                rows = [{field: identifier, "title": "Self-Evolving Agent"}
                        for identifier in ("PaperA", "papera")]
                candidates, _ = topic.search_papers(rows, CONFIG)
                self.assertEqual(len(candidates), 2)

    def test_memory_without_agent_context_is_not_enough(self):
        self.assertEqual(topic.find_matches({"title": "Episodic Memory in Humans"}, CONFIG), [])

    def test_evolutionary_algorithm_is_not_matched(self):
        row = {"title": "A Classical Evolutionary Algorithm", "abstract": "Population optimization"}
        self.assertEqual(topic.find_matches(row, CONFIG), [])


class OutputAndSnapshotTests(unittest.TestCase):
    def test_collection_failure_stops_before_writing_partial_results(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.json"
            source.write_text("{}", encoding="utf-8")
            config_path = root / "config.json"
            config_path.write_text(json.dumps({**CONFIG, "targets": [
                {"conference": "ICLR", "year": 2026},
                {"conference": "ICML", "year": 2026},
                {"conference": "ICLR", "year": 2025},
            ]}), encoding="utf-8")
            row = {"openreview_id": "PaperA", "title": "Self-Evolving Agent",
                   "venueid": collector.CONFERENCE_SPECS["ICLR"].venue_id(2026)}
            manifest = {"counts": {"accepted_paper_count": 1},
                        "outputs": {key: str(source) for key in ("manifest", "csv", "jsonl")}}
            output = root / "results"
            stdout = io.StringIO()
            with patch.object(topic.collector, "build_conference_outputs", side_effect=[
                (manifest, [row], []), RuntimeError("source unavailable"),
            ]) as build, patch.object(topic, "enrich_missing_candidate_abstracts") as enrich:
                with redirect_stdout(stdout):
                    status = topic.main(["--config", str(config_path), "--output-dir", str(output),
                                         "--snapshot-output-root", str(root / "snapshots")])
            self.assertEqual(status, 1)
            self.assertEqual(build.call_count, 2)
            enrich.assert_not_called()
            self.assertFalse(output.exists())
            failure = json.loads(stdout.getvalue())
            self.assertEqual(failure["status"], "failed")
            self.assertIn("ICML-2026", failure["error"])
            self.assertIn("source unavailable", failure["error"])

    def test_custom_configuration_is_reflected_in_outputs_and_command(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config_path = root / "custom config.json"
            config = {"targets": [{"conference": "ICLR", "year": 2025}],
                      "search_fields": ["title"], "query_groups": {
                          "forecasting": {"direct": True, "terms": ["forecasting"]}}}
            config_path.write_text(json.dumps(config), encoding="utf-8")
            output = root / "custom results"
            snapshots = root / "custom snapshots"
            row = {"openreview_id": "PaperA", "conference": "ICLR", "year": 2025,
                   "title": "Forecasting", "abstract": "An existing abstract."}
            with patch.object(topic, "collect_snapshots", return_value=([row], [], {}, [])):
                manifest = topic.run_pipeline(config_path, output, snapshots)
            readme = (output / "README.md").read_text(encoding="utf-8")
            self.assertIn("ICLR 2025", readme)
            self.assertIn("## Search fields\n\n`title`.", readme)
            self.assertIn("`forecasting`", readme)
            self.assertNotIn("ICML 2026", readme)
            self.assertNotIn("Agent Memory", readme)
            command = shlex.split(manifest["command"])
            args = topic.parse_args(command[3:])
            self.assertEqual(args.config, config_path)
            self.assertEqual(args.output_dir, output)
            self.assertEqual(args.snapshot_output_root, snapshots)
            self.assertIn(manifest["command"], readme)
            saved = json.loads((output / "run_manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(saved["command"], manifest["command"])
            self.assertEqual(saved["deduplicated_candidate_count"], 1)

    def test_csv_and_jsonl_have_complete_identical_fields(self):
        row = {field: "value" for field in topic.CANDIDATE_FIELDS}
        row["matched_query_groups"] = ["memory"]
        row["matched_terms"] = ["episodic memory"]
        row["matched_fields"] = ["title"]
        row["matched_snippets"] = [{"field": "title", "text": "Episodic Memory"}]
        with tempfile.TemporaryDirectory() as directory:
            csv_path, jsonl_path = topic.write_candidate_files(Path(directory), [row])
            with csv_path.open(encoding="utf-8-sig", newline="") as handle:
                csv_fields = set(next(csv.DictReader(handle)).keys())
            json_fields = set(json.loads(jsonl_path.read_text(encoding="utf-8").strip()).keys())
        self.assertEqual(csv_fields, set(topic.CANDIDATE_FIELDS))
        self.assertEqual(json_fields, set(topic.CANDIDATE_FIELDS))

    def test_valid_snapshot_is_requested_with_cache_reuse(self):
        spec = collector.CONFERENCE_SPECS["ICLR"]
        row = {"conference": "ICLR", "year": 2026, "openreview_id": "paper-1",
               "source_record_id": "paper-1", "venueid": spec.venue_id(2026), "title": "Paper"}
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            conference_dir = root / "2026" / "ICLR"
            csv_path = conference_dir / "papers.csv"
            jsonl_path = conference_dir / "papers.jsonl"
            manifest_path = conference_dir / "source_manifest.json"
            collector.write_csv(csv_path, [row], collector.CSV_FIELDS)
            collector.write_jsonl(jsonl_path, [{"normalized": row}])
            manifest = {"counts": {"accepted_paper_count": 1}, "source_kind": "openreview",
                        "collection_status": "reused_local_snapshot",
                        "outputs": {"csv": str(csv_path), "jsonl": str(jsonl_path), "manifest": str(manifest_path)}}
            collector.write_json(manifest_path, manifest)
            config = {"targets": [{"conference": "ICLR", "year": 2026}]}
            with patch.object(topic.collector, "build_conference_outputs", return_value=(manifest, [row], [])) as build:
                papers, snapshots, errors, _ = topic.collect_snapshots(config, root)
        self.assertFalse(build.call_args.kwargs["refresh"])
        self.assertEqual(len(papers), 1)
        self.assertEqual(snapshots[0]["accepted_paper_count"], 1)
        self.assertEqual(errors, {})


if __name__ == "__main__":
    unittest.main()
