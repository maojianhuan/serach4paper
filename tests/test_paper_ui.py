import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from tkinter import Tk
from unittest.mock import patch
from urllib.error import URLError

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

    def test_sort_filters_and_reading_list_use_only_visible_selection(self):
        rows = make_snapshot(self.directory)
        rows[0].update(title="Alpha Assistant", abstract="Available abstract")
        rows.append({**rows[0], "openreview_id": "second", "source_record_id": "second",
                     "title": "Beta Assistant", "abstract": ""})
        make_snapshot(self.directory, rows=rows)
        pdf = self.directory / "enrichment" / "pdf" / f"{enrichment.paper_key(rows[1])}.pdf"
        pdf.parent.mkdir(parents=True)
        pdf.write_bytes(b"%PDF-1.4 test")
        enrichment.collector.write_jsonl(self.directory / "enrichment" / "pdf_manifest.jsonl",
            [{"paper_id": enrichment.paper_key(rows[1]), "status": "downloaded", "local_path": str(pdf)}])
        self.ui.search_text.set("assistant")
        self.run_search()
        self.ui.result_sort.set("标题 Z–A")
        self.ui.sort_box.event_generate("<<ComboboxSelected>>")
        self.root.update()
        items = self.ui.results.get_children()
        self.assertEqual([self.ui.result_rows[item]["title"] for item in items], ["Beta Assistant", "Alpha Assistant"])
        self.ui.results.selection_set(items)
        self.ui.only_missing_abstract.set(True)
        self.ui.only_local_pdf.set(True)
        self.ui._apply_result_view()
        visible = self.ui.results.get_children()
        self.assertEqual(len(visible), 1)
        self.assertEqual(self.ui.result_rows[visible[0]]["title"], "Beta Assistant")
        self.assertEqual(len(self.ui.last_search_result["candidates"]), 2)
        output = self.directory / "reading.md"
        with patch.object(paper_ui.filedialog, "asksaveasfilename", return_value=str(output)):
            self.ui.markdown_button.invoke()
        text = output.read_text()
        self.assertIn("Beta Assistant", text)
        self.assertNotIn("Alpha Assistant", text)
        self.assertIn("未提供摘要", text)
        self.assertIn(pdf.resolve().as_uri(), text)
        self.ui.only_missing_abstract.set(False)
        self.ui.only_local_pdf.set(False)
        self.ui._apply_result_view()
        self.assertEqual(len(self.ui.results.get_children()), 2)
        self.assertEqual(len(self.ui.results.selection()), 1)

    def test_year_sort_and_new_search_remove_detached_items(self):
        make_snapshot(self.directory, 2024)
        make_snapshot(self.directory, 2025)
        self.ui.search_year_start.set("2024")
        self.ui.search_text.set("assistant")
        self.run_search()
        self.assertEqual([self.ui.result_rows[item]["year"] for item in self.ui.results.get_children()], ["2025", "2024"])
        self.ui.result_sort.set("年份升序")
        self.ui._apply_result_view()
        self.assertEqual([self.ui.result_rows[item]["year"] for item in self.ui.results.get_children()], ["2024", "2025"])
        old_items = list(self.ui.result_rows)
        self.ui.only_local_pdf.set(True)
        self.ui._apply_result_view()
        self.assertEqual(len(self.ui.results.get_children()), 0)
        self.run_search()
        self.assertTrue(all(not self.ui.results.exists(item) for item in old_items))
        self.ui.only_local_pdf.set(False)
        self.ui._apply_result_view()
        self.assertEqual(len(self.ui.results.get_children()), 2)

    def test_bibtex_export_uses_visible_selection_and_sort_order_in_both_search_modes(self):
        base = make_snapshot(self.directory)[0]
        rows = [{**base, "openreview_id": name, "source_record_id": name,
                 "title": f"{name} Assistant", "abstract": "Available" if name == "Gamma" else "",
                 "bibtex": f"@inproceedings{{{name}, title={{{name} Assistant}}, year={{2025}}}}"}
                for name in ("Alpha", "Beta", "Gamma", "Delta")]
        make_snapshot(self.directory, rows=rows)
        for mode in ("标题布尔查询", "主题多字段"):
            with self.subTest(mode=mode):
                self.ui.search_mode.set(mode)
                self.ui._search_mode_changed()
                self.ui.search_text.set("assistant")
                self.ui.topic_terms.set("assistant")
                self.ui.only_missing_abstract.set(False)
                self.run_search()
                self.ui.result_sort.set("标题 Z–A")
                self.ui._apply_result_view()
                self.ui.results.selection_set([item for item, row in self.ui.result_rows.items()
                                               if row["openreview_id"] != "Delta"])
                self.ui.only_missing_abstract.set(True)
                self.ui._apply_result_view()
                output = self.directory / "selected.bib"
                with patch.object(paper_ui.filedialog, "asksaveasfilename", return_value=str(output)):
                    self.ui.bibtex_button.invoke()
                self.assertFalse(self.errors)
                self.assertEqual(output.read_text(encoding="utf-8"), rows[1]["bibtex"] + "\n\n" + rows[0]["bibtex"] + "\n")
                self.assertIn("已导出 2 篇论文 BibTeX", self.ui.status.get())

    def test_bibtex_export_empty_selection_cancel_and_busy_button_do_not_write(self):
        with patch.object(paper_ui.filedialog, "asksaveasfilename", return_value="") as choose, \
             patch.object(paper_ui.messagebox, "showwarning") as warning, \
             patch.object(enrichment, "export_bibtex") as export:
            self.ui.bibtex_button.invoke()
            warning.assert_called_once()
            choose.assert_not_called()
            make_snapshot(self.directory)
            self.ui.search_text.set("assistant")
            self.run_search()
            self.ui.results.selection_set(self.ui.results.get_children())
            self.ui._set_action_buttons("disabled")
            self.ui.bibtex_button.invoke()
            choose.assert_not_called()
            self.ui._set_action_buttons("normal")
            self.ui.bibtex_button.invoke()
            choose.assert_called_once()
            self.assertEqual(choose.call_args.kwargs["defaultextension"], ".bib")
            export.assert_not_called()

    def test_bibtex_export_missing_source_is_reported_without_overwriting_file(self):
        make_snapshot(self.directory)
        self.ui.search_text.set("assistant")
        self.run_search()
        self.ui.results.selection_set(self.ui.results.get_children())
        output = self.directory / "selected.bib"
        output.write_text("Keep existing bibliography", encoding="utf-8")
        with patch.object(paper_ui.filedialog, "asksaveasfilename", return_value=str(output)):
            self.ui.bibtex_button.invoke()
        self.assertEqual(len(self.errors), 1)
        self.assertEqual(self.errors[0][0], "导出失败")
        self.assertIn("缺少来源 BibTeX", self.errors[0][1])
        self.assertIn("An Autonomous Assistant", self.errors[0][1])
        self.assertEqual(output.read_text(), "Keep existing bibliography")

    def test_bibtex_enrichment_button_exports_immediately_and_restores_after_search(self):
        rows = make_snapshot(self.directory)
        rows[0]["doi"] = "10.1234/assistant"
        make_snapshot(self.directory, rows=rows)
        snapshot = self.directory / "2025" / "ICLR" / "ICLR_2025_accepted_papers.csv"
        original = snapshot.read_bytes()
        self.ui.search_text.set("assistant")
        self.run_search()
        self.ui.results.selection_set(self.ui.results.get_children())
        bibtex = "@article{assistant, title={An Autonomous Assistant}, doi={10.1234/assistant}}"
        with patch.object(enrichment, "urlopen") as request:
            response = request.return_value.__enter__.return_value
            response.read.return_value = bibtex.encode("utf-8")
            response.geturl.return_value = "https://api.crossref.org/assistant/transform"
            self.ui.bibtex_enrich_button.invoke()
            self.assertTrue(self.ui.bibtex_button.instate(["disabled"]))
            self.wait_for(lambda: self.ui.status.get().startswith("BibTeX 补全完成"))
        self.assertFalse(self.errors)
        self.assertFalse(self.ui.search_stale)
        self.assertTrue(self.ui.report_button.instate(["!disabled"]))
        self.assertEqual(self.ui.last_search_result["candidates"][0]["bibtex"], bibtex)
        self.assertIn("DOI 引用服务", self.ui.detail_view.get("1.0", "end"))
        self.assertIn("https://api.crossref.org/assistant/transform", self.ui.detail_view.get("1.0", "end"))
        output = self.directory / "selected.bib"
        with patch.object(paper_ui.filedialog, "asksaveasfilename", return_value=str(output)):
            self.ui.bibtex_button.invoke()
        self.assertEqual(output.read_text(encoding="utf-8"), bibtex + "\n")
        with patch.object(enrichment, "urlopen", side_effect=AssertionError("network forbidden")):
            self.run_search()
            self.assertEqual(next(iter(self.ui.result_rows.values()))["bibtex"], bibtex)
        self.assertEqual(snapshot.read_bytes(), original)

    def test_bibtex_enrichment_shows_per_paper_errors_and_reenables_actions(self):
        base = make_snapshot(self.directory)[0]
        rows = [{**base, "title": "Missing DOI Assistant"},
                {**base, "openreview_id": "network", "source_record_id": "network", "title": "Offline Assistant", "doi": "10.1234/offline"}]
        make_snapshot(self.directory, rows=rows)
        self.ui.search_text.set("assistant")
        self.run_search()
        self.ui.results.selection_set(self.ui.results.get_children())
        with patch.object(enrichment, "urlopen", side_effect=URLError("offline")), \
             patch.object(paper_ui.messagebox, "showwarning") as warning:
            self.ui.bibtex_enrich_button.invoke()
            self.wait_for(lambda: self.ui.status.get().startswith("BibTeX 补全完成"))
        self.assertFalse(self.errors)
        self.assertIn("失败 2", self.ui.status.get())
        self.assertTrue(self.ui.bibtex_enrich_button.instate(["!disabled"]))
        warning.assert_called_once()
        detail = warning.call_args.args[1]
        for text in ("Missing DOI Assistant", "缺少 DOI", "Offline Assistant", "offline"):
            self.assertIn(text, detail)
        for item, row in self.ui.result_rows.items():
            self.ui.results.selection_set(item)
            self.ui._show_paper_details()
            self.assertIn(row["bibtex_error"], self.ui.detail_view.get("1.0", "end"))

    def test_bibtex_enrichment_storage_failure_is_reported_and_actions_reenabled(self):
        rows = make_snapshot(self.directory)
        rows[0]["doi"] = "10.1234/assistant"
        make_snapshot(self.directory, rows=rows)
        self.ui.search_text.set("assistant")
        self.run_search()
        self.ui.results.selection_set(self.ui.results.get_children())
        with patch.object(enrichment, "_fetch_doi_bibtex", return_value=("@article{key, title={Paper}}", "https://doi.org/10.1234/assistant")), \
             patch.object(enrichment, "_write_jsonl_map", side_effect=OSError("Read-only directory")):
            self.ui.bibtex_enrich_button.invoke()
            self.wait_for(lambda: bool(self.errors))
        self.assertEqual(self.errors[0][0], "BibTeX 补全失败")
        self.assertIn("Read-only directory", self.errors[0][1])
        self.assertTrue(self.ui.bibtex_enrich_button.instate(["!disabled"]))
        self.assertFalse(next(iter(self.ui.result_rows.values()))["bibtex"])

    def test_bibtex_enrichment_requires_visible_selection_and_respects_busy_state(self):
        with patch.object(paper_ui.messagebox, "showwarning") as warning, \
             patch.object(enrichment, "enrich_bibtex") as enrich:
            self.ui._set_action_buttons("disabled")
            self.ui.bibtex_enrich_button.invoke()
            warning.assert_not_called()
            self.ui._set_action_buttons("normal")
            self.ui.bibtex_enrich_button.invoke()
            warning.assert_called_once()
            enrich.assert_not_called()

    def test_topic_highlights_real_phrase_variants_and_clears_on_deselection(self):
        rows = make_snapshot(self.directory)
        rows[0]["abstract"] = "An agent uses EPISODIC-MEMORIES and episodic memory."
        make_snapshot(self.directory, rows=rows)
        self.ui.search_mode.set("主题多字段")
        self.ui.topic_terms.set("episodic memory")
        self.run_search()
        self.ui.results.selection_set(self.ui.results.get_children())
        self.ui._show_paper_details()
        ranges = self.ui.detail_view.tag_ranges("match")
        highlighted = [self.ui.detail_view.get(ranges[i], ranges[i+1]) for i in range(0, len(ranges), 2)]
        self.assertEqual(highlighted, ["EPISODIC-MEMORIES", "episodic memory"])
        self.ui.results.selection_remove(self.ui.results.selection())
        self.ui._show_paper_details()
        self.assertEqual(self.ui.detail_view.tag_ranges("match"), ())
        self.assertNotIn(rows[0]["abstract"], self.ui.detail_view.get("1.0", "end"))

    def test_title_highlight_uses_matched_tokens_not_negative_terms(self):
        rows = make_snapshot(self.directory)
        rows[0]["title"] = "Time-Series Anomaly Detection"
        make_snapshot(self.directory, rows=rows)
        self.ui.search_text.set('"time series" AND anomal* AND NOT forecasting')
        self.run_search()
        self.ui.results.selection_set(self.ui.results.get_children())
        self.ui._show_paper_details()
        ranges = self.ui.detail_view.tag_ranges("match")
        highlighted = [self.ui.detail_view.get(ranges[i], ranges[i+1]) for i in range(0, len(ranges), 2)]
        self.assertEqual(highlighted, ["Time-Series", "Anomaly"])

    def test_open_buttons_dispatch_selected_web_and_existing_pdf(self):
        rows = make_snapshot(self.directory)
        rows[0]["source_url"] = "https://example.test/paper"
        make_snapshot(self.directory, rows=rows)
        self.ui.search_text.set("assistant")
        self.run_search()
        item = next(iter(self.ui.result_rows))
        self.ui.results.selection_set(item)
        with patch.object(paper_ui.webbrowser, "open", return_value=True) as browser:
            self.ui.open_web_button.invoke()
            browser.assert_called_once_with("https://example.test/paper")
        pdf = self.directory / "paper with spaces.pdf"
        pdf.write_bytes(b"%PDF-1.4 test")
        self.ui.result_rows[item]["pdf_local_path"] = str(pdf)
        with patch.object(paper_ui.sys, "platform", "linux"), patch.object(paper_ui.subprocess, "run") as launch:
            self.ui.open_pdf_button.invoke()
            launch.assert_called_once_with(["xdg-open", str(pdf.resolve())], check=True)
        pdf.unlink()
        with patch.object(paper_ui.subprocess, "run") as launch:
            self.ui.open_pdf_button.invoke()
            launch.assert_not_called()
        self.assertIn("未找到本地 PDF", self.errors[-1][1])

    def test_catalogue_picker_applies_journal_selection(self):
        from tkinter import Toplevel, ttk
        self.ui._choose_venues('search')
        dialog = next(child for child in self.root.winfo_children() if isinstance(child, Toplevel))
        def descendants(widget):
            for child in widget.winfo_children():
                yield child
                yield from descendants(child)
        widgets = list(descendants(dialog))
        tree = next(w for w in widgets if isinstance(w, ttk.Treeview))
        self.assertEqual(len(tree.get_children()), 677)
        tree.selection_set(('J_TODS', 'J_JATS'))
        next(w for w in widgets if isinstance(w, ttk.Button) and w.cget('text') == '使用所选目录').invoke()
        self.assertEqual(self.ui._target_search_conferences(self.ui.search_conference.get()), ['J_JATS', 'J_TODS'])
        self.assertFalse(self.errors)

    def test_link_pdf_button_updates_report_and_restores_after_search(self):
        make_snapshot(self.directory)
        self.ui.search_text.set('assistant')
        self.run_search()
        item = next(iter(self.ui.result_rows))
        self.ui.results.selection_set(item)
        pdf = self.directory / 'downloaded-by-user.pdf'
        pdf.write_bytes(b'%PDF-1.7\nexample')
        with patch.object(paper_ui.filedialog, 'askopenfilename', return_value=str(pdf)):
            self.ui.link_pdf_button.invoke()
        self.assertEqual(self.ui.last_search_result['coverage'][0]['local_pdf_count'], 1)
        self.assertEqual(self.ui.last_search_result['candidates'][0]['pdf_local_path'], str(pdf))
        self.run_search()
        self.assertEqual(self.ui.last_search_result['coverage'][0]['local_pdf_count'], 1)
        self.assertFalse(self.errors)

    def test_oa_button_runs_from_visual_interface(self):
        make_snapshot(self.directory)
        self.ui.search_text.set('assistant')
        self.run_search()
        item = next(iter(self.ui.result_rows))
        self.ui.results.selection_set(item)
        def lookup(row, email):
            return dict(row, oa_status='found', oa_pdf_url='https://example.test/paper.pdf')
        with patch.object(paper_ui.simpledialog, 'askstring', return_value='reader@example.test'), \
             patch.object(enrichment, 'lookup_open_access', side_effect=lookup):
            self.ui.oa_button.invoke()
            self.wait_for(lambda: self.ui.status.get().startswith('找到'))
        self.assertEqual(self.ui.last_search_result['candidates'][0]['oa_status'], 'found')
        self.assertFalse(self.errors)

    def test_fetch_displays_dblp_verification_progress(self):
        from tests.test_dblp_access import CHALLENGE, URL, response
        from unittest.mock import Mock
        collector = paper_ui.collector
        opener = Mock()
        opener.open.side_effect = [response(CHALLENGE), response(b'{"ok":true}')]
        statuses = []
        self.ui.status.trace_add('write', lambda *_: statuses.append(self.ui.status.get()))
        self.ui.fetch_venues = ['J_TODS']
        self.ui.fetch_conference.set('目录多选')
        self.ui.fetch_year.set('2025')
        def collect(*args, **kwargs):
            collector.request_bytes(URL)
            return {}, [], []
        with patch.object(collector, 'build_opener', return_value=opener), \
             patch.object(collector.time, 'sleep'), \
             patch.object(collector, 'build_conference_outputs', side_effect=collect), \
             patch.object(paper_ui.messagebox, 'showinfo'):
            self.ui.fetch_button.invoke()
            self.wait_for(lambda: self.ui.fetch_button.instate(['!disabled']))
        self.assertTrue(any('等待 DBLP 访问验证' in value for value in statuses))
        self.assertTrue(self.ui.status.get().startswith('抓取完成'))
        self.assertFalse(self.errors)

    def test_multiselect_fetch_exports_actual_scope_with_failed_source(self):
        collector = paper_ui.collector
        self.ui.fetch_venues = ['J_TODS', 'J_JATS']
        self.ui.fetch_conference.set('目录多选')
        self.ui.fetch_year.set('2025')
        def collect(*args, **kwargs):
            if kwargs['spec'].key == 'J_JATS':
                raise RuntimeError('source unavailable')
            return {'counts': {'accepted_paper_count': 0}}, [], []
        with patch.object(collector, 'build_conference_outputs', side_effect=collect), \
             patch.object(paper_ui.messagebox, 'showwarning'):
            self.ui.fetch_button.invoke()
            self.wait_for(lambda: self.ui.fetch_button.instate(['!disabled']))
        manifest = json.loads((self.directory / '2025/ALL/source_manifest.json').read_text())
        self.assertEqual(manifest['conferences_requested'], ['J_TODS', 'J_JATS'])
        self.assertEqual(manifest['conferences_completed'], ['J_TODS'])
        self.assertIn('J_JATS', manifest['failures'])
        self.assertFalse(self.errors)


if __name__ == "__main__":
    unittest.main()
