#!/usr/bin/env python3
"""Simple GUI for fetching conference papers and searching locally saved results.

This tool provides two pages:
- Fetch page: run conference/year/output-dir fetch jobs.
- Search page: query local CSVs by title in a year range.
"""

from __future__ import annotations

import csv
import os
import sys
import threading
from pathlib import Path
from tkinter import BOTH, END, LEFT, BooleanVar, StringVar, Tk, filedialog, messagebox
from tkinter import ttk

from . import fetch_openreview_accepted as collector
from . import paper_enrichment as enrichment


def _env(key: str, default: str) -> str:
    value = os.environ.get(key, "").strip()
    return value if value else default

def application_root() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


BASE_DIR = Path(__file__).resolve().parent
DEFAULT_OUTPUT_ROOT = application_root() / "output"
UI_FETCHABLE_CONFERENCES = ("ICML", "NIPS", "ICLR", "AAAI", "KDD", "WWW")
UI_FETCHABLE_KEYS = tuple(
    collector.normalize_conference_argument(value)
    for value in UI_FETCHABLE_CONFERENCES
)
CONFERENCES = UI_FETCHABLE_CONFERENCES + ("ALL",)
YEAR_CHOICES = [""] + [str(y) for y in range(2000, 2031)]
CURRENT_YEAR = "2026"
DEFAULT_YEAR = _env("SEARCH4PAPER_UI_YEAR", CURRENT_YEAR)
DEFAULT_CONFERENCE = _env("SEARCH4PAPER_UI_CONFERENCE", "ICLR")
DEFAULT_OUTPUT_ROOT = Path(_env("SEARCH4PAPER_UI_OUTPUT_DIR", str(DEFAULT_OUTPUT_ROOT)))
DEFAULT_SEARCH_CONFERENCE = _env("SEARCH4PAPER_UI_SEARCH_CONFERENCE", DEFAULT_CONFERENCE)


class PaperUI:
    def __init__(self, root: Tk) -> None:
        self.root = root
        self.root.title("Conference Paper Collector")
        self.root.geometry("1180x760")
        self.root.minsize(980, 620)

        self._configure_style()

        self.output_root = StringVar(value=str(DEFAULT_OUTPUT_ROOT))
        self.fetch_conference = StringVar(value=DEFAULT_CONFERENCE)
        self.fetch_year = StringVar(value=DEFAULT_YEAR)
        self.refresh_existing = BooleanVar(value=True)
        self.search_conference = StringVar(value=DEFAULT_SEARCH_CONFERENCE)
        self.search_year_start = StringVar(value="")
        self.search_year_end = StringVar(value="")
        self.search_text = StringVar()
        self.status = StringVar(value="Ready")
        self.result_rows: dict[str, dict[str, object]] = {}
        self.search_query = ""

        self._active_page = StringVar(value="fetch")

        self._build_layout()

        self.fetch_year.trace_add("write", lambda *_args: self._update_local_snapshot_summary())
        self.output_root.trace_add("write", lambda *_args: self._update_local_snapshot_summary())

    def _configure_style(self) -> None:
        self.root.configure(bg="#f4f7fb")
        style = ttk.Style()
        try:
            style.theme_use("clam")
        except Exception:
            pass

        style.configure(
            "Primary.TButton",
            font=("Microsoft YaHei", 10, "bold"),
            padding=(14, 8),
        )
        style.configure(
            "Accent.TButton",
            font=("Microsoft YaHei", 10, "bold"),
            background="#1d4ed8",
            foreground="white",
        )
        style.configure(
            "Title.TLabel",
            font=("Microsoft YaHei", 16, "bold"),
            foreground="#0f172a",
            background="#f4f7fb",
        )
        style.configure(
            "Section.TLabel",
            font=("Microsoft YaHei", 11, "bold"),
            foreground="#0f172a",
            background="#f4f7fb",
        )
        style.configure("Hint.TLabel", font=("Microsoft YaHei", 9), foreground="#475569", background="#f4f7fb")
        style.configure(
            "Card.TLabelframe",
            background="#ffffff",
            relief="solid",
            borderwidth=1,
        )
        style.configure(
            "Card.TLabelframe.Label",
            font=("Microsoft YaHei", 11, "bold"),
            foreground="#1d4ed8",
            background="#ffffff",
        )
        style.configure("Action.TEntry", padding=8)
        style.configure("Status.TLabel", foreground="#111827", background="#eef2ff", padding=10)

    def _build_layout(self) -> None:
        top = ttk.Frame(self.root, style="Card.TLabelframe")
        top.pack(fill="x", padx=12, pady=(12, 6))

        ttk.Label(top, text="Conference Paper Collector", style="Title.TLabel").pack(
            anchor="w", padx=12, pady=(10, 2)
        )
        ttk.Label(top, text="抓取并检索本地会议论文元数据", style="Hint.TLabel").pack(
            anchor="w", padx=12, pady=(0, 10)
        )

        button_bar = ttk.Frame(top)
        button_bar.pack(fill="x", padx=12, pady=(0, 12))

        self.btn_fetch_page = ttk.Button(
            button_bar,
            text="抓取论文",
            style="Primary.TButton",
            command=lambda: self._show_page("fetch"),
        )
        self.btn_fetch_page.pack(side=LEFT, padx=(0, 10))

        self.btn_search_page = ttk.Button(
            button_bar,
            text="搜索论文",
            style="Primary.TButton",
            command=lambda: self._show_page("search"),
        )
        self.btn_search_page.pack(side=LEFT)

        self.content = ttk.Frame(self.root)
        self.content.pack(fill=BOTH, expand=True, padx=12, pady=6)

        self.fetch_frame = ttk.Frame(self.content, padding=12)
        self.search_frame = ttk.Frame(self.content, padding=12)
        self._build_fetch_page(self.fetch_frame)
        self._build_search_page(self.search_frame)

        self._show_page("fetch")
        ttk.Label(
            self.root,
            textvariable=self.status,
            style="Status.TLabel",
        ).pack(fill="x", padx=12, pady=(0, 10))

        self._update_local_snapshot_summary()

    def _show_page(self, page: str) -> None:
        if page == "fetch":
            self.search_frame.pack_forget()
            self.fetch_frame.pack(fill=BOTH, expand=True)
            self._active_page.set("fetch")
            self.btn_fetch_page.state(["pressed"])  # type: ignore[attr-defined]
            self.btn_search_page.state(["!pressed"])  # type: ignore[attr-defined]
        else:
            self.fetch_frame.pack_forget()
            self.search_frame.pack(fill=BOTH, expand=True)
            self._active_page.set("search")
            self.btn_search_page.state(["pressed"])  # type: ignore[attr-defined]
            self.btn_fetch_page.state(["!pressed"])  # type: ignore[attr-defined]

    def _build_fetch_page(self, parent: ttk.Frame) -> None:
        panel = ttk.LabelFrame(parent, text="抓取参数", style="Card.TLabelframe", padding=14)
        panel.pack(fill="x")
        panel.columnconfigure(1, weight=1)

        ttk.Label(panel, text="会议", style="Section.TLabel").grid(row=0, column=0, sticky="w", pady=6)
        ttk.Combobox(
            panel,
            textvariable=self.fetch_conference,
            values=CONFERENCES,
            state="readonly",
            width=24,
        ).grid(row=0, column=1, sticky="w", pady=6)

        ttk.Label(panel, text="年份（留空即默认 2026）", style="Section.TLabel").grid(
            row=1, column=0, sticky="w", pady=6
        )
        ttk.Combobox(
            panel,
            textvariable=self.fetch_year,
            values=YEAR_CHOICES,
            width=24,
        ).grid(row=1, column=1, sticky="w", pady=6)

        ttk.Label(panel, text="输出目录", style="Section.TLabel").grid(row=2, column=0, sticky="w", pady=6)
        ttk.Entry(panel, textvariable=self.output_root, style="Action.TEntry").grid(
            row=2, column=1, sticky="ew", pady=6
        )
        ttk.Button(panel, text="浏览", style="Primary.TButton", command=self._browse_output).grid(
            row=2, column=2, padx=(10, 0), pady=6
        )

        self.fetch_button = ttk.Button(
            panel,
            text="开始抓取",
            style="Accent.TButton",
            command=self._start_fetch,
        )
        self.fetch_button.grid(row=3, column=1, sticky="w", pady=(16, 0))
        ttk.Checkbutton(
            panel,
            text="抓取前刷新远程数据（推荐）",
            variable=self.refresh_existing,
        ).grid(row=3, column=2, sticky="w", padx=(10, 0), pady=(16, 0))

        hint = "抓取结果写入：<输出目录>/<年份>/<会议>/..."
        ttk.Label(panel, text=hint, style="Hint.TLabel", wraplength=700).grid(
            row=4,
            column=1,
            sticky="w",
            pady=(12, 2),
        )

        status_panel = ttk.LabelFrame(
            parent,
            text="本地抓取数据",
            style="Card.TLabelframe",
            padding=(10, 8, 10, 10),
        )
        status_panel.pack(fill=BOTH, expand=True, padx=(0, 0), pady=(12, 0))
        status_panel.columnconfigure(0, weight=1)

        self.local_status_hint = StringVar(
            value=f"按年份 {self.fetch_year.get() or CURRENT_YEAR} 扫描本地数据"
        )
        ttk.Label(
            status_panel,
            textvariable=self.local_status_hint,
            style="Hint.TLabel",
        ).grid(row=0, column=0, sticky="w", pady=(0, 8))

        self.refresh_status_button = ttk.Button(
            status_panel,
            text="刷新本地统计",
            style="Primary.TButton",
            command=self._update_local_snapshot_summary,
        )
        self.refresh_status_button.grid(row=0, column=1, sticky="e")

        status_columns = ("conference", "count", "source")
        self.local_status = ttk.Treeview(
            status_panel,
            columns=status_columns,
            show="headings",
            height=6,
        )
        for col, title, width in (
            ("conference", "会议", 120),
            ("count", "已抓取论文数", 120),
            ("source", "文件路径", 700),
        ):
            self.local_status.heading(col, text=title)
            self.local_status.column(col, width=width, anchor="w")

        v = ttk.Scrollbar(status_panel, orient="vertical", command=self.local_status.yview)
        h = ttk.Scrollbar(status_panel, orient="horizontal", command=self.local_status.xview)
        self.local_status.configure(yscrollcommand=v.set, xscrollcommand=h.set)
        self.local_status.grid(row=1, column=0, columnspan=2, sticky="nsew")
        v.grid(row=1, column=2, sticky="ns")
        h.grid(row=2, column=0, columnspan=2, sticky="ew")
        status_panel.rowconfigure(1, weight=1)

    def _build_search_page(self, parent: ttk.Frame) -> None:
        panel = ttk.LabelFrame(parent, text="搜索条件", style="Card.TLabelframe", padding=14)
        panel.pack(fill="x")

        panel.columnconfigure(1, weight=1)
        panel.rowconfigure(6, weight=1)

        ttk.Label(panel, text="会议", style="Section.TLabel").grid(row=0, column=0, sticky="w", pady=6)
        ttk.Combobox(
            panel,
            textvariable=self.search_conference,
            values=CONFERENCES,
            state="readonly",
            width=24,
        ).grid(row=0, column=1, sticky="w", pady=6)

        ttk.Label(panel, text="起始年份（可空）", style="Section.TLabel").grid(row=1, column=0, sticky="w", pady=6)
        ttk.Combobox(
            panel,
            textvariable=self.search_year_start,
            values=YEAR_CHOICES,
            width=24,
        ).grid(row=1, column=1, sticky="w", pady=6)

        ttk.Label(panel, text="结束年份（可空）", style="Section.TLabel").grid(row=2, column=0, sticky="w", pady=6)
        ttk.Combobox(
            panel,
            textvariable=self.search_year_end,
            values=YEAR_CHOICES,
            width=24,
        ).grid(row=2, column=1, sticky="w", pady=6)

        ttk.Label(panel, text="标题关键词", style="Section.TLabel").grid(row=3, column=0, sticky="w", pady=6)
        entry = ttk.Entry(panel, textvariable=self.search_text)
        entry.grid(row=3, column=1, sticky="ew", pady=6)
        entry.bind("<Return>", lambda _event: self._search())

        self.search_button = ttk.Button(
            panel,
            text="搜索",
            style="Accent.TButton",
            command=self._search,
        )
        self.search_button.grid(row=3, column=2, padx=(10, 0), pady=6)

        action_bar = ttk.Frame(panel)
        action_bar.grid(row=4, column=0, columnspan=3, sticky="w", pady=(8, 0))
        self.abstract_button = ttk.Button(
            action_bar,
            text="获取选中摘要",
            command=self._start_abstract_enrichment,
        )
        self.abstract_button.pack(side=LEFT, padx=(0, 8))
        self.export_button = ttk.Button(
            action_bar,
            text="导出 AI JSONL",
            command=self._export_ai_jsonl,
        )
        self.export_button.pack(side=LEFT, padx=(0, 8))
        self.pdf_button = ttk.Button(
            action_bar,
            text="下载选中 PDF",
            command=self._start_pdf_download,
        )
        self.pdf_button.pack(side=LEFT)

        self.result_panel = ttk.LabelFrame(
            parent,
            text="搜索结果",
            style="Card.TLabelframe",
            padding=(10, 8, 10, 10),
        )
        self.result_panel.pack(fill=BOTH, expand=True, pady=(12, 0))

        columns = (
            "conference",
            "year",
            "title",
            "authors",
            "abstract_status",
            "pdf_status",
            "source_url",
        )
        self.results = ttk.Treeview(
            self.result_panel,
            columns=columns,
            show="headings",
            selectmode="extended",
        )
        for col, title, width in (
            ("conference", "会议", 80),
            ("year", "年份", 70),
            ("title", "标题", 410),
            ("authors", "作者", 240),
            ("abstract_status", "摘要", 90),
            ("pdf_status", "PDF", 90),
            ("source_url", "来源", 240),
        ):
            self.results.heading(col, text=title)
            self.results.column(col, width=width, anchor="w")

        yscroll = ttk.Scrollbar(self.result_panel, orient="vertical", command=self.results.yview)
        xscroll = ttk.Scrollbar(self.result_panel, orient="horizontal", command=self.results.xview)
        self.results.configure(yscrollcommand=yscroll.set, xscrollcommand=xscroll.set)

        self.results.grid(row=0, column=0, sticky="nsew")
        yscroll.grid(row=0, column=1, sticky="ns")
        xscroll.grid(row=1, column=0, sticky="ew")
        self.result_panel.rowconfigure(0, weight=1)
        self.result_panel.columnconfigure(0, weight=1)

        ttk.Label(
            self.result_panel,
            text="说明：仅按标题进行模糊匹配。可搜索多个年份。",
            style="Hint.TLabel",
        ).grid(row=2, column=0, columnspan=2, sticky="w", pady=(8, 0))

    def _selected_fetch_conferences(self, conference: str) -> list[str]:
        if conference == "ALL":
            return list(UI_FETCHABLE_KEYS)
        return [collector.normalize_conference_argument(conference)]

    def _target_search_conferences(self, conference: str) -> list[str]:
        if conference == "ALL":
            return list(UI_FETCHABLE_KEYS)
        return [collector.normalize_conference_argument(conference)]

    def _selected_result_rows(self) -> list[tuple[str, dict[str, object]]]:
        return [
            (item_id, self.result_rows[item_id])
            for item_id in self.results.selection()
            if item_id in self.result_rows
        ]

    def _row_abstract_status(self, row: dict[str, object]) -> str:
        if row.get("abstract_status") == "success" or str(row.get("abstract", "")).strip():
            return "已有"
        return "未获取"

    def _row_pdf_status(self, row: dict[str, object]) -> str:
        status = str(row.get("pdf_status", "")).strip()
        if status == "downloaded":
            return "已下载"
        if status in {"failed", "no_pdf_url"}:
            return "失败/无链接"
        if enrichment.resolve_pdf_url(row):
            return "可下载"
        return "无链接"

    def _update_result_item(self, item_id: str, row: dict[str, object]) -> None:
        key = str(row.get("conference", ""))
        self.results.item(
            item_id,
            values=(
                "KDD" if key == "SIGKDD" else key,
                row.get("year", ""),
                row.get("title", ""),
                row.get("authors", ""),
                self._row_abstract_status(row),
                self._row_pdf_status(row),
                row.get("source_url", ""),
            ),
        )

    def _set_action_buttons(self, state: str) -> None:
        for button in (self.abstract_button, self.export_button, self.pdf_button, self.search_button):
            button.config(state=state)

    def _start_abstract_enrichment(self) -> None:
        selected = self._selected_result_rows()
        if not selected:
            messagebox.showwarning("摘要", "请先在搜索结果中选择论文")
            return
        output_root = Path(self.output_root.get().strip()).expanduser().resolve()
        self._set_action_buttons("disabled")
        self.status.set(f"正在获取 {len(selected)} 篇论文摘要…")
        threading.Thread(
            target=self._abstract_worker,
            args=(selected, output_root),
            daemon=True,
        ).start()

    def _abstract_worker(
        self,
        selected: list[tuple[str, dict[str, object]]],
        output_root: Path,
    ) -> None:
        try:
            rows, failures = enrichment.enrich_abstracts(
                (row for _item_id, row in selected),
                output_root,
            )
            self.root.after(0, self._abstract_done, selected, rows, failures)
        except Exception as exc:
            self.root.after(0, lambda: self._action_error("摘要获取失败", str(exc)))

    def _abstract_done(
        self,
        selected: list[tuple[str, dict[str, object]]],
        rows: list[dict[str, object]],
        failures: dict[str, str],
    ) -> None:
        for (item_id, _old_row), row in zip(selected, rows):
            self.result_rows[item_id] = row
            self._update_result_item(item_id, row)
        self._set_action_buttons("normal")
        success_count = len(rows) - len(failures)
        self.status.set(f"摘要处理完成：成功 {success_count}，失败 {len(failures)}")
        if failures:
            messagebox.showwarning(
                "摘要处理完成",
                "部分论文未找到摘要：\n" + "\n".join(failures.values()),
            )

    def _start_pdf_download(self) -> None:
        selected = self._selected_result_rows()
        if not selected:
            messagebox.showwarning("PDF", "请先在搜索结果中选择论文")
            return
        output_root = Path(self.output_root.get().strip()).expanduser().resolve()
        self._set_action_buttons("disabled")
        self.status.set(f"正在下载 {len(selected)} 篇论文 PDF…")
        threading.Thread(
            target=self._pdf_worker,
            args=(selected, output_root),
            daemon=True,
        ).start()

    def _pdf_worker(
        self,
        selected: list[tuple[str, dict[str, object]]],
        output_root: Path,
    ) -> None:
        try:
            rows, failures = enrichment.download_pdfs(
                (row for _item_id, row in selected),
                output_root,
            )
            self.root.after(0, self._pdf_done, selected, rows, failures)
        except Exception as exc:
            self.root.after(0, lambda: self._action_error("PDF 下载失败", str(exc)))

    def _pdf_done(
        self,
        selected: list[tuple[str, dict[str, object]]],
        rows: list[dict[str, object]],
        failures: dict[str, str],
    ) -> None:
        for (item_id, _old_row), row in zip(selected, rows):
            self.result_rows[item_id] = row
            self._update_result_item(item_id, row)
        self._set_action_buttons("normal")
        success_count = len(rows) - len(failures)
        self.status.set(f"PDF 处理完成：成功 {success_count}，失败 {len(failures)}")
        if failures:
            messagebox.showwarning(
                "PDF 处理完成",
                "部分论文下载失败：\n" + "\n".join(failures.values()),
            )

    def _action_error(self, title: str, detail: str) -> None:
        self._set_action_buttons("normal")
        self.status.set(title)
        messagebox.showerror(title, detail)

    def _export_ai_jsonl(self) -> None:
        selected = self._selected_result_rows()
        if not selected:
            messagebox.showwarning("导出", "请先在搜索结果中选择论文")
            return
        output_root = Path(self.output_root.get().strip()).expanduser().resolve()
        default_dir = output_root / "enrichment"
        default_dir.mkdir(parents=True, exist_ok=True)
        selected_path = filedialog.asksaveasfilename(
            title="导出 AI JSONL",
            initialdir=str(default_dir),
            initialfile="selected_papers.jsonl",
            defaultextension=".jsonl",
            filetypes=[("JSON Lines", "*.jsonl"), ("All files", "*.*")],
        )
        if not selected_path:
            return
        try:
            path = enrichment.export_ai_jsonl(
                (row for _item_id, row in selected),
                Path(selected_path),
                query=self.search_query,
            )
        except Exception as exc:
            messagebox.showerror("导出失败", str(exc))
            return
        self.status.set(f"已导出 {len(selected)} 篇论文：{path}")
        messagebox.showinfo("导出完成", f"已生成 AI JSONL：\n{path}")

    def _browse_output(self) -> None:
        selected = filedialog.askdirectory(initialdir=self.output_root.get() or str(BASE_DIR))
        if selected:
            self.output_root.set(selected)

    def _safe_fetch_year(self) -> int:
        try:
            return collector.valid_year(self.fetch_year.get().strip()) if self.fetch_year.get().strip() else 2026
        except Exception:
            return 2026

    def _read_csv_count(self, csv_path: Path) -> int | None:
        try:
            with csv_path.open("r", encoding="utf-8-sig", newline="") as handle:
                return sum(1 for _ in csv.DictReader(handle))
        except Exception:
            return None

    def _update_local_snapshot_summary(self) -> None:
        year = self._safe_fetch_year()
        self.local_status_hint.set(f"{year} 年本地抓取数据")
        for item in self.local_status.get_children():
            self.local_status.delete(item)

        output_root = Path(self.output_root.get().strip()).expanduser().resolve()
        if not output_root.exists():
            self.local_status.insert("", END, values=("-", "-", "输出目录不存在"))
            return

        for display_name, conference in zip(UI_FETCHABLE_CONFERENCES, UI_FETCHABLE_KEYS):
            csv_path = output_root / str(year) / conference / f"{conference}_{year}_accepted_papers.csv"
            if csv_path.exists():
                count = self._read_csv_count(csv_path)
                if count is None:
                    status = "文件不可读"
                    count_text = "-"
                elif count == 0:
                    status = "文件为空"
                    count_text = "0"
                else:
                    status = str(csv_path)
                    count_text = str(count)
            else:
                status = "未抓取（未找到文件）"
                count_text = "0"
            self.local_status.insert(
                "",
                END,
                values=(display_name, count_text, status),
            )

    def _start_fetch(self) -> None:
        conference = self.fetch_conference.get().strip().upper()
        year_text = self.fetch_year.get().strip()

        try:
            year = collector.valid_year(year_text) if year_text else 2026
            output_root = Path(self.output_root.get().strip()).expanduser().resolve()
            if not str(output_root):
                raise ValueError("输出目录不能为空")
        except (ValueError, OSError, collector.argparse.ArgumentTypeError) as exc:
            messagebox.showerror("输入无效", str(exc))
            return

        if conference not in CONFERENCES:
            messagebox.showerror("输入无效", f"不支持的会议: {conference}")
            return

        self.fetch_button.config(state="disabled")
        self.status.set(f"抓取中：{conference} {year} ...")
        refresh = self.refresh_existing.get()
        threading.Thread(
            target=self._fetch_worker,
            args=(conference, year, output_root, refresh),
            daemon=True,
        ).start()

    def _fetch_worker(
        self, conference: str, year: int, output_root: Path, refresh: bool = True
    ) -> None:
        try:
            selected = self._selected_fetch_conferences(conference)
            manifests: dict[str, dict] = {}
            rows: list[dict] = []
            jsonl_rows: list[dict] = []
            failures: dict[str, str] = {}

            for key in selected:
                try:
                    manifest, csv_rows, rich_rows = collector.build_conference_outputs(
                        output_root,
                        spec=collector.CONFERENCE_SPECS[key],
                        year=year,
                        page_size=1000,
                        use_iclr_virtual=True,
                        refresh=refresh,
                        include_rows=True,
                    )
                    manifests[key] = manifest
                    if manifest.get("collection_status") == "no_public_accepted_papers":
                        failures[key] = (
                            f"{key} {year} 尚未发现公开录用论文；可能尚未公布，"
                            "下次抓取会重新检查。"
                        )
                    rows.extend(csv_rows)
                    jsonl_rows.extend(rich_rows)
                except RuntimeError as exc:
                    failures[key] = str(exc)
                    if conference != "ALL":
                        raise

            if conference == "ALL":
                collector.build_combined_outputs(
                    output_root,
                    year=year,
                    manifests=manifests,
                    csv_rows=rows,
                    jsonl_rows=jsonl_rows,
                    failures=failures,
                )

            self.root.after(0, self._fetch_done, conference, year, failures)
        except Exception as exc:
            detail = str(exc)
            self.root.after(0, lambda: self._fetch_error(detail))

    def _fetch_done(self, conference: str, year: int, failures: dict[str, str]) -> None:
        self.fetch_button.config(state="normal")
        self._update_local_snapshot_summary()

        if failures:
            keys = ", ".join(failures)
            self.status.set(f"抓取完成但有失败：{keys}")
            messagebox.showwarning(
                "抓取完成",
                "以下会议抓取失败：\n" + "\n".join(f"{k}: {v}" for k, v in failures.items()),
            )
        else:
            self.status.set(f"抓取完成：{conference} {year}")
            messagebox.showinfo("抓取完成", f"{conference} {year} 抓取已完成并写入本地。")

    def _fetch_error(self, detail: str) -> None:
        self.fetch_button.config(state="normal")
        self._update_local_snapshot_summary()
        self.status.set("抓取失败")
        messagebox.showerror("抓取失败", detail)

    def _parse_year(self, value: str) -> int | None:
        value = value.strip()
        if not value:
            return None
        return collector.valid_year(value)

    def _iter_years(self, root: Path, start: int | None, end: int | None) -> list[int]:
        years: list[int] = []
        if start is None and end is None:
            for item in sorted(root.iterdir(), key=lambda p: p.name):
                if item.is_dir() and item.name.isdigit():
                    years.append(int(item.name))
            return years

        if start is not None and end is not None and start > end:
            start, end = end, start

        if start is None:
            start = end
        if end is None:
            end = start
        return list(range(int(start), int(end) + 1))

    def _search(self) -> None:
        conference = self.search_conference.get().strip().upper()
        query = self.search_text.get().strip().casefold()
        if not query:
            messagebox.showwarning("搜索", "请输入标题关键词")
            return

        try:
            root = Path(self.output_root.get().strip()).expanduser().resolve()
            start_year = self._parse_year(self.search_year_start.get())
            end_year = self._parse_year(self.search_year_end.get())
        except (ValueError, OSError, collector.argparse.ArgumentTypeError) as exc:
            messagebox.showerror("输入无效", str(exc))
            return

        years = self._iter_years(root, start_year, end_year)
        if not years:
            messagebox.showinfo("搜索", "未找到可搜索年份目录")
            return

        for item in self.results.get_children():
            self.results.delete(item)
        self.result_rows.clear()
        self.search_query = query

        keys = self._target_search_conferences(conference)

        matches = 0
        for year in years:
            for key in keys:
                path = root / str(year) / key / f"{key}_{year}_accepted_papers.csv"
                if not path.exists():
                    continue
                with path.open("r", encoding="utf-8-sig", newline="") as handle:
                    for row in csv.DictReader(handle):
                        if query in str(row.get("title", "")).casefold():
                            result_row = dict(row)
                            result_row["year"] = row.get("year") or year
                            item_id = self.results.insert(
                                "",
                                END,
                                values=(
                                    "KDD" if key == "SIGKDD" else row.get("conference", key),
                                    year,
                                    row.get("title", ""),
                                    row.get("authors", ""),
                                    self._row_abstract_status(result_row),
                                    self._row_pdf_status(result_row),
                                    row.get("source_url", ""),
                                ),
                            )
                            self.result_rows[item_id] = result_row
                            matches += 1

        self.status.set(f"找到 {matches} 条匹配论文")
        if matches == 0:
            messagebox.showinfo("搜索", "未匹配到论文；请确认年份和输出目录是否正确")


def main() -> None:
    root = Tk()
    PaperUI(root)
    root.mainloop()


if __name__ == "__main__":
    main()
