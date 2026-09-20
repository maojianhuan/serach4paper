import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from tkinter import Tk
from unittest.mock import patch

from code import paper_ui
from code import paper_search as search
from code import paper_enrichment as enrichment
from tests.test_paper_search import make_snapshot, topic_config


@unittest.skipUnless(os.name == "nt" or os.environ.get("DISPLAY"), "Requires a desktop display or Xvfb")
class DesktopSearchTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.root = Tk()
        self.addCleanup(self.root.destroy)
        self.ui = paper_ui.PaperUI(self.root)
        self.ui.output_root.set(str(self.directory))
        self.ui.search_conference.set("ICLR")
        self.ui.search_year_start.set("2025")
        self.ui.search_year_end.set("2025")
        self.ui._show_page("search")
        self.errors = []
        error_patch = patch.object(paper_ui.messagebox, "showerror", side_effect=lambda *args: self.errors.append(args))
        error_patch.start()
        self.addCleanup(error_patch.stop)

    def run_search(self):
        self.ui.search_button.invoke()
        self.wait_for(lambda: not self.ui.search_busy)

    def wait_for(self, finished):
        def poll():
            if not finished():
                self.root.after(10, poll)
            else:
                self.root.quit()
        self.root.after(10, poll)
        timeout = self.root.after(5000, self.root.quit)
        self.root.mainloop()
        self.root.after_cancel(timeout)
        self.assertTrue(finished(), "UI operation did not finish")

    def search_and_enrich(self):
        rows = make_snapshot(self.directory)
        rows[0]["title"] = "Episodic Memory for Agents"
        make_snapshot(self.directory, rows=rows)
        self.ui.search_mode.set("主题多字段")
        self.ui._search_mode_changed()
        self.ui.topic_terms.set("episodic memory")
        self.run_search()
        item = next(iter(self.ui.result_rows))
        self.ui.results.selection_set(item)
        with patch.object(enrichment, "_fetch_openreview_abstract",
                          return_value=("An agent uses episodic memory.", "https://example.test/abstract")):
            self.ui.abstract_button.invoke()
            self.wait_for(lambda: self.ui.status.get().startswith("摘要处理完成"))
        self.assertFalse(self.errors)
        self.assertEqual(self.ui.result_rows[item]["abstract"], "An agent uses episodic memory.")

    def test_enrichment_blocks_report_until_search_refreshes_abstracts_and_statistics(self):
        self.search_and_enrich()
        self.assertTrue(self.ui.search_stale)
        self.assertTrue(self.ui.report_button.instate(["disabled"]))
        self.assertIn("重新搜索", self.ui.search_coverage_hint.get())
        self.assertIn("重新搜索", self.ui.summary_view.get("1.0", "end"))
        with patch.object(paper_ui.filedialog, "askdirectory") as choose, \
             patch.object(paper_ui.messagebox, "showwarning") as warning:
            self.ui.report_button.invoke()
            self.ui._export_search_report()
            choose.assert_not_called()
            warning.assert_called_once()
        # Completion of another action must not re-enable the stale report.
        self.ui._set_action_buttons("normal")
        self.assertTrue(self.ui.report_button.instate(["disabled"]))
        self.run_search()
        self.assertFalse(self.ui.search_stale)
        self.assertTrue(self.ui.report_button.instate(["!disabled"]))
        output = self.directory / "refreshed"
        with patch.object(paper_ui.filedialog, "askdirectory", return_value=str(output)):
            self.ui.report_button.invoke()
        exported = json.loads((output / "candidate_papers.jsonl").read_text())
        self.assertEqual(exported["abstract"], "An agent uses episodic memory.")
        self.assertIn("abstract", exported["matched_fields"])
        report = json.loads((output / "search_report.json").read_text())
        self.assertEqual(report["coverage"][0]["missing_abstract_count"], 0)
        abstract_count = next(item for item in report["summary"] if item["matched_field"] == "abstract")
        self.assertEqual(abstract_count["deduplicated_match_count"], 1)

    def test_failed_or_invalid_refresh_does_not_restore_report_export(self):
        self.search_and_enrich()
        self.ui.topic_terms.set("")
        self.ui.search_button.invoke()
        self.assertTrue(self.errors)
        self.assertTrue(self.ui.report_button.instate(["disabled"]))
        self.ui.topic_terms.set("episodic memory")
        (self.directory / "2025" / "ICLR" / "source_manifest.json").write_text("{broken")
        self.run_search()
        self.assertIsNone(self.ui.last_search_result)
        self.assertTrue(self.ui.report_button.instate(["disabled"]))

    def test_title_search_button_agrees_with_cli(self):
        rows = make_snapshot(self.directory)
        rows[0]["title"] = "Time-Series Anomaly Detection"
        make_snapshot(self.directory, rows=rows)
        query = '"time series" AND anomal* AND NOT forecasting'
        self.ui.search_text.set(query)
        self.run_search()
        self.assertFalse(self.errors)
        ui_ids = [row["openreview_id"] for row in self.ui.result_rows.values()]
        output = self.directory / "cli"
        with redirect_stdout(io.StringIO()):
            code = search.main(["--query", query, "--conference", "ICLR", "--years", "2025",
                                "--snapshot-output-root", str(self.directory), "--output-dir", str(output)])
        self.assertEqual(code, 0)
        exported = [json.loads(line) for line in (output / "candidate_papers.jsonl").read_text().splitlines()]
        self.assertEqual(ui_ids, [row["openreview_id"] for row in exported])

    def test_manual_topic_search_cached_abstract_details_save_and_export(self):
        rows = make_snapshot(self.directory)
        enrichment.enrich_abstracts([{**rows[0], "abstract": "An agent uses episodic memory."}], self.directory)
        self.ui.search_mode.set("主题多字段")
        self.ui._search_mode_changed()
        self.ui.topic_terms.set("episodic memory")
        self.ui.topic_context.set("agent")
        self.run_search()
        self.assertFalse(self.errors)
        self.assertEqual(len(self.ui.result_rows), 1)
        item = next(iter(self.ui.result_rows))
        self.ui.results.selection_set(item)
        self.ui._show_paper_details()
        self.assertIn("episodic memory", self.ui.detail_view.get("1.0", "end"))
        self.assertIn("abstract", self.ui.summary_view.get("1.0", "end"))
        config_path = self.directory / "saved.json"
        with patch.object(paper_ui.filedialog, "asksaveasfilename", return_value=str(config_path)):
            self.ui._save_topic_config()
        config = json.loads(config_path.read_text())
        self.assertEqual(config["query_groups"]["topic"]["terms"], ["episodic memory"])
        output = self.directory / "export"
        with patch.object(paper_ui.filedialog, "askdirectory", return_value=str(output)):
            self.ui.report_button.invoke()
        self.assertTrue((output / "coverage.csv").is_file())
        self.assertTrue((output / "query_summary.csv").is_file())
        report = json.loads((output / "search_report.json").read_text())
        self.assertTrue(report["complete"])
        self.assertEqual(report["config"], config)

    def test_loaded_configuration_uses_its_targets_and_shows_missing_coverage(self):
        make_snapshot(self.directory)
        config_path = self.directory / "query.json"
        config = topic_config((2025, 2026))
        config["query_groups"] = {"assistants": {"direct": True, "terms": ["assistant"]}}
        with config_path.open("w") as handle:
            json.dump(config, handle)
        with patch.object(paper_ui.filedialog, "askopenfilename", return_value=str(config_path)):
            self.ui._load_topic_config()
        self.run_search()
        self.assertFalse(self.errors)
        self.assertEqual(len(self.ui.result_rows), 1)
        self.assertFalse(self.ui.last_search_result["complete"])
        self.assertIn("覆盖范围不完整", self.ui.search_coverage_hint.get())
        self.assertIn("ICLR 2026：未抓取", self.ui.coverage_view.get("1.0", "end"))
        self.assertEqual(self.ui.last_search_request["config"], config)
        self.ui._clear_topic_config()
        self.assertIsNone(self.ui.topic_config)

    def test_invalid_query_is_shown_and_no_worker_starts(self):
        self.ui.search_text.set("memory AND (")
        self.ui.search_button.invoke()
        self.assertTrue(self.errors)
        self.assertFalse(self.ui.search_busy)
        self.assertIsNone(self.ui.last_search_result)

    def test_corrupt_snapshot_surfaces_worker_error(self):
        make_snapshot(self.directory)
        (self.directory / "2025" / "ICLR" / "source_manifest.json").write_text("{broken")
        self.ui.search_text.set("assistant")
        self.run_search()
        self.assertTrue(self.errors)
        self.assertIn("搜索失败", self.ui.search_coverage_hint.get())
        self.assertIsNone(self.ui.last_search_result)


if __name__ == "__main__":
    unittest.main()
