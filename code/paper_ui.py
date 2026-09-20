#!/usr/bin/env python3
"""Simple GUI for fetching conference papers and searching locally saved results.

This tool provides two pages:
- Fetch page: run conference/year/output-dir fetch jobs.
- Search page: Boolean title and multi-field topic search with coverage reports.
"""

from __future__ import annotations

import csv
import json
import os
import sys
import subprocess
import webbrowser
from urllib.parse import urlsplit
import threading
from pathlib import Path
from tkinter import BOTH, END, LEFT, BooleanVar, StringVar, Tk, Toplevel, filedialog, messagebox, simpledialog
from tkinter import ttk
from tkinter.scrolledtext import ScrolledText

from . import fetch_openreview_accepted as collector
from . import paper_enrichment as enrichment
from . import paper_search as search
from . import query_research_topic as topic


def filter_catalog(rank="全部", venue_type="全部", field="全部", query="") -> list[dict]:
    query = query.strip().casefold()
    return [entry for entry in collector.CCF_CATALOG["venues"]
            if (rank == "全部" or entry["category"] == rank)
            and (venue_type == "全部" or entry["type"] == venue_type)
            and (field == "全部" or field in entry["professional_fields"])
            and (not query or query in (entry["key"] + " " + entry["abbreviation"] + " " + entry["full_name"]).casefold())]


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
CONFERENCES = UI_FETCHABLE_CONFERENCES + ("ALL", "目录多选")
YEAR_CHOICES = [""] + [str(y) for y in range(2000, 2031)]
CURRENT_YEAR = "2026"
DEFAULT_YEAR = _env("SEARCH4PAPER_UI_YEAR", CURRENT_YEAR)
DEFAULT_CONFERENCE = _env("SEARCH4PAPER_UI_CONFERENCE", "ICLR")
DEFAULT_OUTPUT_ROOT = Path(_env("SEARCH4PAPER_UI_OUTPUT_DIR", str(DEFAULT_OUTPUT_ROOT)))
DEFAULT_SEARCH_CONFERENCE = _env("SEARCH4PAPER_UI_SEARCH_CONFERENCE", DEFAULT_CONFERENCE)


class PaperUI:
    def __init__(self, root: Tk) -> None:
        self.root = root
        self.root.title("Paper Collector")
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
        self.search_mode = StringVar(value="标题布尔查询")
        self.topic_terms = StringVar()
        self.topic_context = StringVar()
        self.topic_fields = {field: BooleanVar(value=True) for field in topic.SEARCH_FIELDS}
        self.topic_config = None
        self.topic_config_hint = StringVar(value="手动主题：词组用分号分隔；上下文词可留空。")
        self.last_search_result = None
        self.last_search_request = None
        self.search_stale = False
        self.only_missing_abstract = BooleanVar(value=False)
        self.only_local_pdf = BooleanVar(value=False)
        self.result_sort = StringVar(value="年份降序")
        self.result_count = StringVar(value="显示 0 / 0 篇")
        self.search_busy = False
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

        ttk.Label(top, text="Paper Collector", style="Title.TLabel").pack(
            anchor="w", padx=12, pady=(10, 2)
        )
        ttk.Label(top, text="抓取并检索会议与期刊论文元数据", style="Hint.TLabel").pack(
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

        self.fetch_frame = ttk.Frame(self.content, padding=12)
        self.search_frame = ttk.Frame(self.content, padding=12)
        self._build_fetch_page(self.fetch_frame)
        self._build_search_page(self.search_frame)

        self._show_page("fetch")
        ttk.Label(
            self.root,
            textvariable=self.status,
            style="Status.TLabel",
        ).pack(side="bottom", fill="x", padx=12, pady=(0, 10))
        self.content.pack(fill=BOTH, expand=True, padx=12, pady=6)

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

        ttk.Label(panel, text="会议/期刊", style="Section.TLabel").grid(row=0, column=0, sticky="w", pady=6)
        ttk.Combobox(
            panel,
            textvariable=self.fetch_conference,
            values=CONFERENCES,
            state="readonly",
            width=24,
        ).grid(row=0, column=1, sticky="w", pady=6)

        ttk.Button(panel, text="选择 CCF 目录…", command=lambda: self._choose_venues("fetch")).grid(row=0, column=2, padx=10)

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
        panel = ttk.LabelFrame(parent, text="搜索条件（只读取本地数据）", padding=8)
        panel.pack(fill="x")
        panel.columnconfigure(1, weight=1)
        ttk.Label(panel, text="论文目录").grid(row=0, column=0, sticky="w")
        ttk.Entry(panel, textvariable=self.output_root).grid(row=0, column=1, sticky="ew")
        ttk.Button(panel, text="浏览", command=self._browse_output).grid(row=0, column=2)
        filters = ttk.Frame(panel)
        filters.grid(row=1, column=0, columnspan=3, sticky="w", pady=5)
        for label, variable, choices in (("会议", self.search_conference, CONFERENCES),
                                        ("起始年", self.search_year_start, YEAR_CHOICES),
                                        ("结束年", self.search_year_end, YEAR_CHOICES),
                                        ("模式", self.search_mode, ("标题布尔查询", "主题多字段"))):
            ttk.Label(filters, text=label).pack(side=LEFT, padx=(0, 4))
            box = ttk.Combobox(filters, textvariable=variable, values=choices, width=14,
                               state="readonly" if label in ("会议", "模式") else "normal")
            box.pack(side=LEFT, padx=(0, 10))
            if label == "模式":
                box.bind("<<ComboboxSelected>>", lambda _event: self._search_mode_changed())
        ttk.Button(filters, text="CCF 目录…", command=lambda: self._choose_venues("search")).pack(side=LEFT)
        self.title_controls = ttk.Frame(panel)
        self.title_controls.grid(row=2, column=0, columnspan=3, sticky="ew")
        self.title_controls.columnconfigure(1, weight=1)
        ttk.Label(self.title_controls, text="标题表达式").grid(row=0, column=0)
        entry = ttk.Entry(self.title_controls, textvariable=self.search_text)
        entry.grid(row=0, column=1, sticky="ew", padx=8)
        entry.bind("<Return>", lambda _event: self._search())
        ttk.Label(self.title_controls, text='支持 AND / OR / NOT、括号、"精确短语"、通配符 *；空格表示 AND。').grid(row=1, column=1, sticky="w")
        self.topic_controls = ttk.Frame(panel)
        self.topic_controls.grid(row=3, column=0, columnspan=3, sticky="ew")
        self.topic_controls.columnconfigure(1, weight=1)
        ttk.Label(self.topic_controls, text="主题词组").grid(row=0, column=0)
        self.topic_terms_entry = ttk.Entry(self.topic_controls, textvariable=self.topic_terms)
        self.topic_terms_entry.grid(row=0, column=1, sticky="ew", padx=8)
        ttk.Label(self.topic_controls, text="上下文词").grid(row=1, column=0)
        self.topic_context_entry = ttk.Entry(self.topic_controls, textvariable=self.topic_context)
        self.topic_context_entry.grid(row=1, column=1, sticky="ew", padx=8)
        field_bar = ttk.Frame(self.topic_controls)
        field_bar.grid(row=2, column=0, columnspan=2, sticky="w")
        self.field_buttons = []
        for field, label in (("title", "标题"), ("abstract", "摘要"), ("keywords", "关键词"), ("tldr", "TLDR")):
            button = ttk.Checkbutton(field_bar, text=label, variable=self.topic_fields[field])
            button.pack(side=LEFT)
            self.field_buttons.append(button)
        ttk.Button(field_bar, text="加载主题配置", command=self._load_topic_config).pack(side=LEFT, padx=8)
        ttk.Button(field_bar, text="使用手动条件", command=self._clear_topic_config).pack(side=LEFT)
        ttk.Button(field_bar, text="保存主题配置", command=self._save_topic_config).pack(side=LEFT, padx=8)
        ttk.Label(self.topic_controls, textvariable=self.topic_config_hint, wraplength=950).grid(row=3, column=0, columnspan=2, sticky="w")
        actions = ttk.Frame(panel)
        actions.grid(row=4, column=0, columnspan=3, sticky="w", pady=5)
        self.search_button = ttk.Button(actions, text="搜索", command=self._search)
        self.abstract_button = ttk.Button(actions, text="获取选中摘要", command=self._start_abstract_enrichment)
        self.bibtex_enrich_button = ttk.Button(actions, text="补全选中 BibTeX", command=self._start_bibtex_enrichment)
        self.export_button = ttk.Button(actions, text="导出选中 AI JSONL", command=self._export_ai_jsonl)
        self.pdf_button = ttk.Button(actions, text="下载选中 PDF", command=self._start_pdf_download)
        self.report_button = ttk.Button(actions, text="导出本次结果与统计", command=self._export_search_report, state="disabled")
        for button in (self.search_button, self.abstract_button, self.bibtex_enrich_button,
                       self.export_button, self.pdf_button, self.report_button):
            button.pack(side=LEFT, padx=(0, 8))
        self.search_coverage_hint = StringVar(value="已有摘要补全数据会自动参与搜索；主题词采用词前缀匹配，候选仍需人工审核。")
        ttk.Label(parent, textvariable=self.search_coverage_hint, wraplength=1000).pack(fill="x", pady=5)
        notebook = ttk.Notebook(parent)
        notebook.pack(fill=BOTH, expand=True)
        self.result_panel = ttk.Frame(notebook)
        notebook.add(self.result_panel, text="论文结果")
        view_bar = ttk.Frame(self.result_panel)
        view_bar.grid(row=0, column=0, columnspan=2, sticky="ew", pady=4)
        ttk.Checkbutton(view_bar, text="仅缺摘要", variable=self.only_missing_abstract, command=self._apply_result_view).pack(side=LEFT)
        ttk.Checkbutton(view_bar, text="仅已有本地 PDF", variable=self.only_local_pdf, command=self._apply_result_view).pack(side=LEFT)
        self.sort_box = ttk.Combobox(view_bar, textvariable=self.result_sort, state="readonly", width=12,
                                    values=("年份降序", "年份升序", "标题 A–Z", "标题 Z–A"))
        self.sort_box.pack(side=LEFT, padx=8)
        self.sort_box.bind("<<ComboboxSelected>>", lambda _event: self._apply_result_view())
        ttk.Label(view_bar, textvariable=self.result_count).pack(side=LEFT)
        reading_bar = ttk.Frame(self.result_panel)
        reading_bar.grid(row=1, column=0, columnspan=2, sticky="w", pady=4)
        self.open_web_button = ttk.Button(reading_bar, text="打开论文网页", command=lambda: self._open_selected_paper("web"))
        self.open_pdf_button = ttk.Button(reading_bar, text="打开本地 PDF", command=lambda: self._open_selected_paper("pdf"))
        self.markdown_button = ttk.Button(reading_bar, text="导出选中阅读清单", command=self._export_reading_list)
        self.bibtex_button = ttk.Button(reading_bar, text="导出选中 BibTeX", command=self._export_bibtex)
        self.oa_button = ttk.Button(reading_bar, text="查找开放全文", command=self._start_oa_lookup)
        self.link_pdf_button = ttk.Button(reading_bar, text="关联本地 PDF", command=self._link_local_pdf)
        for button in (self.open_web_button, self.open_pdf_button, self.markdown_button, self.bibtex_button,
                       self.oa_button, self.link_pdf_button):
            button.pack(side=LEFT, padx=(0, 8))
        columns = ("conference", "year", "title", "authors", "abstract_status", "pdf_status", "source_url")
        self.results = ttk.Treeview(self.result_panel, columns=columns, show="headings", selectmode="extended")
        for col, title, width in (("conference", "会议/期刊", 110), ("year", "年份", 70), ("title", "标题", 410),
                                  ("authors", "作者", 200), ("abstract_status", "摘要", 70), ("pdf_status", "PDF", 90), ("source_url", "来源", 220)):
            self.results.heading(col, text=title)
            self.results.column(col, width=width, anchor="w")
        yscroll = ttk.Scrollbar(self.result_panel, orient="vertical", command=self.results.yview)
        xscroll = ttk.Scrollbar(self.result_panel, orient="horizontal", command=self.results.xview)
        self.results.configure(yscrollcommand=yscroll.set, xscrollcommand=xscroll.set)
        self.results.grid(row=2, column=0, sticky="nsew")
        yscroll.grid(row=2, column=1, sticky="ns")
        xscroll.grid(row=3, column=0, sticky="ew")
        self.result_panel.rowconfigure(2, weight=1)
        self.result_panel.columnconfigure(0, weight=1)
        self.results.bind("<<TreeviewSelect>>", self._show_paper_details)
        self.coverage_view = ScrolledText(notebook, wrap="word", state="disabled")
        self.summary_view = ScrolledText(notebook, wrap="word", state="disabled")
        self.detail_view = ScrolledText(notebook, wrap="word", state="disabled")
        self.detail_view.tag_configure("match", background="#ffe082", foreground="#111111")
        notebook.add(self.coverage_view, text="覆盖范围")
        notebook.add(self.summary_view, text="命中统计")
        notebook.add(self.detail_view, text="摘要与命中依据")
        self._search_mode_changed()

    @staticmethod
    def _set_text(widget, text: str) -> None:
        widget.configure(state="normal")
        widget.delete("1.0", END)
        widget.insert("1.0", text)
        widget.configure(state="disabled")

    def _search_mode_changed(self) -> None:
        if self.search_mode.get() == "主题多字段":
            self.title_controls.grid_remove()
            self.topic_controls.grid()
        else:
            self.topic_controls.grid_remove()
            self.title_controls.grid()

    def _load_topic_config(self) -> None:
        path = filedialog.askopenfilename(title="加载主题配置", filetypes=[("JSON", "*.json")])
        if not path:
            return
        try:
            config = json.loads(Path(path).read_text(encoding="utf-8"))
            search.validate_config(config)
        except (OSError, ValueError, KeyError, TypeError, collector.argparse.ArgumentTypeError) as exc:
            messagebox.showerror("配置无效", str(exc))
            return
        self.topic_config = config
        self.search_mode.set("主题多字段")
        self._search_mode_changed()
        targets = ", ".join(f"{t['conference']} {t['year']}" for t in config["targets"])
        self.topic_config_hint.set(f"使用配置 {Path(path).name}：{targets}；分组 {', '.join(config['query_groups'])}；字段 {', '.join(config.get('search_fields', topic.SEARCH_FIELDS))}。上方手动会议/年份不生效。")
        for widget in (self.topic_terms_entry, self.topic_context_entry, *self.field_buttons):
            widget.configure(state="disabled")

    def _clear_topic_config(self) -> None:
        self.topic_config = None
        self.topic_config_hint.set("手动主题：词组用分号分隔；上下文词可留空。")
        for widget in (self.topic_terms_entry, self.topic_context_entry, *self.field_buttons):
            widget.configure(state="normal")

    def _manual_targets(self, root: Path) -> list[dict]:
        years = self._iter_years(root, self._parse_year(self.search_year_start.get()), self._parse_year(self.search_year_end.get()))
        if not years:
            raise ValueError("未找到年份目录，请指定年份并先抓取论文。")
        return [dict(conference=key, year=year) for year in years
                for key in self._target_search_conferences(self.search_conference.get())]

    def _topic_request(self, root: Path) -> dict:
        if self.topic_config is not None:
            return self.topic_config
        terms = [term.strip() for term in self.topic_terms.get().replace("；", ";").split(";") if term.strip()]
        context = [term.strip() for term in self.topic_context.get().replace("；", ";").split(";") if term.strip()]
        config = dict(targets=self._manual_targets(root), context_anchors=context,
                      search_fields=[field for field, variable in self.topic_fields.items() if variable.get()],
                      query_groups={"topic": dict(terms=terms, direct=not bool(context))})
        search.validate_config(config)
        return config

    def _save_topic_config(self) -> None:
        try:
            config = self._topic_request(Path(self.output_root.get()).expanduser().resolve())
            path = filedialog.asksaveasfilename(title="保存主题配置", defaultextension=".json", filetypes=[("JSON", "*.json")])
            if path:
                collector.write_json(Path(path), config)
        except (OSError, ValueError, KeyError, TypeError, collector.argparse.ArgumentTypeError) as exc:
            messagebox.showerror("保存失败", str(exc))

    def _export_search_report(self) -> None:
        if self.search_stale:
            messagebox.showwarning("导出", "摘要已更新，请重新搜索后再导出本次结果与统计。")
            return
        if self.last_search_result is None:
            messagebox.showwarning("导出", "请先完成搜索")
            return
        directory = filedialog.askdirectory(title="选择本次结果输出目录（同名结果文件会覆盖）")
        if directory:
            try:
                search.write_search_outputs(Path(directory), self.last_search_result, self.last_search_request)
                self.status.set(f"已导出结果、覆盖范围与命中统计：{directory}")
            except (OSError, ValueError) as exc:
                messagebox.showerror("导出失败", str(exc))

    @staticmethod
    def _has_local_pdf(row: dict) -> bool:
        path = str(row.get("pdf_local_path", "")).strip()
        return bool(path) and Path(path).is_file()

    def _apply_result_view(self) -> None:
        selected = set(self.results.selection())
        items = list(self.result_rows)
        order = self.result_sort.get()
        if order.startswith("年份"):
            items.sort(key=lambda item: (int(self.result_rows[item].get("year") or 0),
                                         str(self.result_rows[item].get("title", "")).casefold()),
                       reverse=order == "年份降序")
        else:
            items.sort(key=lambda item: str(self.result_rows[item].get("title", "")).casefold(),
                       reverse=order == "标题 Z–A")
        visible = []
        for item in items:
            row = self.result_rows[item]
            if ((self.only_missing_abstract.get() and str(row.get("abstract", "")).strip())
                    or (self.only_local_pdf.get() and not self._has_local_pdf(row))):
                self.results.detach(item)
            else:
                self.results.move(item, "", "end")
                visible.append(item)
        self.results.selection_set([item for item in visible if item in selected])
        self.result_count.set(f"显示 {len(visible)} / {len(items)} 篇（整体导出包含全部搜索结果）")
        self._show_paper_details()

    def _open_selected_paper(self, kind: str) -> None:
        selected = self._selected_result_rows()
        if len(selected) != 1:
            messagebox.showwarning("打开论文", "请选择一篇论文")
            return
        row = selected[0][1]
        try:
            if kind == "web":
                url = str(row.get("oa_landing_url") or row.get("source_url") or row.get("paper_url") or row.get("openreview_url") or "").strip()
                if urlsplit(url).scheme not in ("http", "https") or not urlsplit(url).netloc:
                    raise ValueError("该论文没有有效的网页链接")
                if not webbrowser.open(url):
                    raise RuntimeError("无法启动默认浏览器")
            else:
                if not self._has_local_pdf(row):
                    raise ValueError("未找到本地 PDF，请先下载该论文")
                path = str(Path(row["pdf_local_path"]).resolve())
                if sys.platform == "win32":
                    os.startfile(path)
                else:
                    subprocess.run(["open" if sys.platform == "darwin" else "xdg-open", path], check=True)
        except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
            messagebox.showerror("打开失败", str(exc))

    def _export_reading_list(self) -> None:
        selected = self._selected_result_rows()
        if not selected:
            messagebox.showwarning("导出", "请先选择论文；只导出当前可见且选中的论文。")
            return
        path = filedialog.asksaveasfilename(title="导出选中阅读清单", initialfile="reading_list.md",
                                          defaultextension=".md", filetypes=[("Markdown", "*.md")])
        if path:
            try:
                enrichment.export_reading_list((row for _, row in selected), Path(path))
                self.status.set(f"已导出 {len(selected)} 篇论文阅读清单：{path}")
            except (OSError, ValueError) as exc:
                messagebox.showerror("导出失败", str(exc))

    def _export_bibtex(self) -> None:
        selected = self._selected_result_rows()
        if not selected:
            messagebox.showwarning("导出", "请先选择论文；只导出当前可见且选中的论文。")
            return
        path = filedialog.asksaveasfilename(title="导出选中 BibTeX", initialfile="selected_papers.bib",
                                          defaultextension=".bib", filetypes=[("BibTeX", "*.bib")])
        if path:
            try:
                enrichment.export_bibtex((row for _, row in selected), Path(path))
                self.status.set(f"已导出 {len(selected)} 篇论文 BibTeX：{path}")
            except (OSError, ValueError) as exc:
                messagebox.showerror("导出失败", str(exc))

    def _show_paper_details(self, _event=None) -> None:
        selected = self._selected_result_rows()
        self.detail_view.tag_remove("match", "1.0", END)
        if not selected:
            self._set_text(self.detail_view, "请选择论文查看摘要与命中依据。")
            return
        row = selected[0][1]
        title = str(row.get("title", ""))
        abstract = str(row.get("abstract") or "缺少摘要")
        prefix = f"{title}\n\n摘要来源：{row.get('abstract_source') or '原始快照/未提供'}\n{row.get('abstract_source_url', '')}\n\n"
        evidence = "\n".join(f"{hit.get('query_group', '')} | {hit.get('field', '')} | {hit.get('term', '')}\n{hit.get('text', '')}" for hit in row.get("matched_snippets", []))
        if self.search_stale:
            evidence = "摘要已更新，请重新搜索后查看最新命中依据和高亮。"
        bibtex_status = ("已有" if row.get("bibtex") else
                         {"no_doi": "缺少 DOI", "invalid_doi": "DOI 格式无效", "failed": "补全失败"}
                         .get(row.get("bibtex_status"), "未提供"))
        bibtex_source = ("DOI 引用服务" if row.get("bibtex_source") == "doi_content_negotiation"
                         else "原始快照" if row.get("bibtex") else "未提供")
        self._set_text(self.detail_view, prefix + abstract + "\n\n命中依据：\n" + evidence + "\n\n全文状态："
                       + str(row.get("oa_status", "未查询")) + "；版本：" + str(row.get("oa_version", "未知"))
                       + "\n开放版本：" + str(row.get("oa_landing_url") or row.get("oa_pdf_url") or "未发现/未查询")
                       + "\n本地 PDF：" + str(row.get("pdf_local_path") or "未关联")
                       + "\n未发现开放版或下载失败不代表一定需要订阅；可打开论文网页，通过机构账号访问。"
                       + f"\n\nBibTeX：{bibtex_status}；来源：{bibtex_source}"
                       + "\n引用来源链接：" + str(row.get("bibtex_source_url") or "未记录")
                       + "\n引用获取时间：" + str(row.get("bibtex_retrieved_at") or "未记录")
                       + "\n补全提示：" + str(row.get("bibtex_error") or "无"))
        if self.search_stale:
            return
        exact = (self.last_search_request or {}).get("config") is None
        for hit in row.get("matched_snippets", []):
            field, term = hit.get("field"), hit.get("term", "")
            if field not in ("title", "abstract"):
                continue
            text, offset = (title, 0) if field == "title" else (abstract, len(prefix))
            for start, end in topic.phrase_match_spans(text, term, exact=exact):
                self.detail_view.tag_add("match", f"1.0+{offset + start}c", f"1.0+{offset + end}c")

    def _choose_venues(self, purpose: str) -> None:
        dialog = Toplevel(self.root)
        dialog.title("CCF 第七版：A/B/C 会议与期刊")
        dialog.geometry("1080x650")
        bar = ttk.Frame(dialog, padding=8)
        bar.pack(fill="x")
        variables = [StringVar(dialog, value="全部") for _ in range(3)]
        term = StringVar(dialog)
        fields = sorted({field for entry in collector.CCF_CATALOG["venues"] for field in entry["professional_fields"]})
        for label, variable, values, width in zip(("级别", "类型", "学科"), variables,
                (("全部", "A", "B", "C"), ("全部", "会议", "期刊"), ["全部"] + fields), (6, 7, 36)):
            ttk.Label(bar, text=label).pack(side=LEFT)
            ttk.Combobox(bar, textvariable=variable, values=values, state="readonly", width=width).pack(side=LEFT, padx=5)
        ttk.Entry(bar, textvariable=term, width=25).pack(side=LEFT, padx=5)
        ttk.Label(dialog, text="输入简称或全名筛选；Ctrl/Shift 多选。筛选后仍保留已选项。ALL 快捷项仅代表原有六个核心会议。").pack(anchor="w", padx=8)
        tree = ttk.Treeview(dialog, columns=("rank", "type", "name", "field"), show="tree headings", selectmode="extended")
        tree.heading("#0", text="标识"); tree.column("#0", width=150)
        for key, title, width in (("rank", "级别", 45), ("type", "类型", 50), ("name", "名称", 440), ("field", "学科", 280)):
            tree.heading(key, text=title); tree.column(key, width=width)
        scroll = ttk.Scrollbar(dialog, orient="vertical", command=tree.yview)
        scroll.pack(side="right", fill="y")
        tree.configure(yscrollcommand=scroll.set)
        bottom = ttk.Frame(dialog, padding=8)
        bottom.pack(side="bottom", fill="x")
        tree.pack(fill=BOTH, expand=True)
        selected = set(getattr(self, purpose + "_venues", []))
        count = StringVar(dialog)
        visible = set()
        def remember():
            selected.difference_update(visible)
            selected.update(tree.selection())
        def refresh(*_):
            remember()
            tree.delete(*tree.get_children())
            visible.clear()
            for entry in filter_catalog(variables[0].get(), variables[1].get(), variables[2].get(), term.get()):
                key = entry["key"]
                visible.add(key)
                tree.insert("", END, iid=key, text=key, values=(entry["category"], entry["type"], entry["full_name"], entry["professional_field"]))
            tree.selection_set(sorted(selected & visible))
            count.set(f"显示 {len(visible)} / 677；已选 {len(selected)}")
        def selection_changed(_event=None):
            remember()
            count.set(f"显示 {len(visible)} / 677；已选 {len(selected)}")
        def apply():
            remember()
            if not selected:
                messagebox.showwarning("选择目录", "请至少选择一个会议或期刊", parent=dialog)
                return
            setattr(self, purpose + "_venues", sorted(selected))
            getattr(self, purpose + "_conference").set("目录多选")
            self.status.set("已选目录：" + ", ".join(sorted(selected)))
            if purpose == "fetch":
                self._update_local_snapshot_summary()
            dialog.destroy()
        for variable in variables + [term]:
            variable.trace_add("write", refresh)
        tree.bind("<<TreeviewSelect>>", selection_changed)
        ttk.Label(bottom, textvariable=count).pack(side=LEFT)
        ttk.Button(bottom, text="全选当前筛选", command=lambda: tree.selection_set(tree.get_children())).pack(side=LEFT, padx=8)
        ttk.Button(bottom, text="清空全部", command=lambda: (selected.clear(), tree.selection_remove(tree.selection()), selection_changed())).pack(side=LEFT)
        ttk.Button(bottom, text="使用所选目录", command=apply).pack(side="right")
        refresh()

    def _update_access_result(self, item, updated) -> None:
        row = self.result_rows[item]
        delta = int(self._has_local_pdf(updated)) - int(self._has_local_pdf(row))
        row.update(updated)
        self._update_result_item(item, row)
        if self.last_search_result and not self.search_stale:
            for coverage in self.last_search_result["coverage"]:
                if coverage["conference"] == row["conference"] and str(coverage["year"]) == str(row["year"]):
                    coverage["local_pdf_count"] += delta
            self._set_text(self.coverage_view, search.coverage_text(self.last_search_result))
        self._show_paper_details()

    def _link_local_pdf(self) -> None:
        selected = self._selected_result_rows()
        if len(selected) != 1:
            messagebox.showwarning("关联 PDF", "请选择一篇论文，再关联你下载的 PDF。")
            return
        path = filedialog.askopenfilename(title="选择该论文的 PDF", filetypes=[("PDF", "*.pdf")])
        if not path:
            return
        try:
            item, row = selected[0]
            updated = enrichment.link_local_pdf(row, Path(path), Path(self.output_root.get()).expanduser().resolve())
            self._update_access_result(item, updated)
            self._apply_result_view()
            self.status.set("本地 PDF 已关联；可使用“打开本地 PDF”阅读。")
        except (ValueError, OSError) as exc:
            messagebox.showerror("关联 PDF 失败", str(exc))

    def _start_oa_lookup(self) -> None:
        selected = self._selected_result_rows()
        if not selected:
            messagebox.showwarning("开放全文", "请先选择论文。")
            return
        email = simpledialog.askstring("开放全文", "Unpaywall 要求联系邮箱（仅随此次查询发送，不保存）：", parent=self.root)
        if not email:
            return
        if "@" not in email or any(c.isspace() for c in email):
            messagebox.showerror("开放全文", "请输入有效邮箱。")
            return
        self._set_action_buttons("disabled")
        self.status.set("正在按 DOI 查询开放全文…")
        def worker():
            try:
                rows = [enrichment.lookup_open_access(row, email) for _, row in selected]
                self.root.after(0, self._oa_done, selected, rows)
            except Exception as exc:
                self.root.after(0, self._action_error, "开放全文查询失败", str(exc))
        threading.Thread(target=worker, daemon=True).start()

    def _oa_done(self, selected, rows) -> None:
        for (item, _), row in zip(selected, rows):
            self._update_access_result(item, row)
        self._set_action_buttons("normal")
        found = sum(row.get("oa_status") == "found" for row in rows)
        self.status.set(f"找到 {found}/{len(rows)} 篇开放版本。无结果不代表必须订阅；可打开论文网页通过机构访问。")

    def _selected_fetch_conferences(self, conference: str) -> list[str]:
        if conference == "目录多选":
            selected = list(getattr(self, "fetch_venues", []))
            if not selected:
                raise ValueError("请先从 CCF 目录选择会议或期刊")
            return selected
        if conference == "ALL":
            return list(UI_FETCHABLE_KEYS)
        return [collector.normalize_conference_argument(conference)]

    def _target_search_conferences(self, conference: str) -> list[str]:
        if conference == "目录多选":
            selected = list(getattr(self, "search_venues", []))
            if not selected:
                raise ValueError("请先从 CCF 目录选择会议或期刊")
            return selected
        if conference == "ALL":
            return list(UI_FETCHABLE_KEYS)
        return [collector.normalize_conference_argument(conference)]

    def _selected_result_rows(self) -> list[tuple[str, dict[str, object]]]:
        selected = set(self.results.selection())
        return [
            (item_id, self.result_rows[item_id])
            for item_id in self.results.get_children()
            if item_id in selected and item_id in self.result_rows
        ]

    def _row_abstract_status(self, row: dict[str, object]) -> str:
        if row.get("abstract_status") == "success" or str(row.get("abstract", "")).strip():
            return "已有"
        return "未获取"

    def _row_pdf_status(self, row: dict[str, object]) -> str:
        status = str(row.get("pdf_status", "")).strip()
        if self._has_local_pdf(row):
            return "本地已关联" if status == "linked" else "已下载"
        if row.get("oa_status") == "found":
            return "有开放链接"
        if row.get("oa_status") == "not_found":
            return "未发现开放版"
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
        for button in (self.abstract_button, self.export_button, self.pdf_button, self.search_button, self.report_button,
                       self.open_web_button, self.open_pdf_button, self.markdown_button, self.bibtex_button, self.bibtex_enrich_button,
                       self.oa_button, self.link_pdf_button):
            button.config(state=state)
        if self.search_stale or self.last_search_result is None:
            self.report_button.config(state="disabled")

    def _start_bibtex_enrichment(self) -> None:
        selected = self._selected_result_rows()
        if not selected:
            messagebox.showwarning("BibTeX", "请先在搜索结果中选择论文")
            return
        output_root = Path(self.output_root.get().strip()).expanduser().resolve()
        self._set_action_buttons("disabled")
        self.status.set(f"正在补全 {len(selected)} 篇选中论文的 BibTeX…")
        def worker():
            try:
                rows, failures = enrichment.enrich_bibtex((row for _, row in selected), output_root)
                self.root.after(0, self._bibtex_done, selected, rows, failures)
            except Exception as exc:
                self.root.after(0, self._action_error, "BibTeX 补全失败", str(exc))
        threading.Thread(target=worker, daemon=True).start()

    def _bibtex_done(self, selected, rows, failures) -> None:
        for (item, _), row in zip(selected, rows):
            self.result_rows[item].update(row)
        self._set_action_buttons("normal")
        self._show_paper_details()
        self.status.set(f"BibTeX 补全完成：已有或补全 {len(rows) - len(failures)}，失败 {len(failures)}")
        if failures:
            details = [f"{row.get('title') or enrichment.paper_key(row)}：{failures[enrichment.paper_key(row)]}"
                       for row in rows if enrichment.paper_key(row) in failures]
            messagebox.showwarning("BibTeX 补全完成", "以下论文未能补全：\n" + "\n".join(details))

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
            self.root.after(0, self._action_error, "摘要获取失败", str(exc))

    def _abstract_done(
        self,
        selected: list[tuple[str, dict[str, object]]],
        rows: list[dict[str, object]],
        failures: dict[str, str],
    ) -> None:
        for (item_id, _old_row), row in zip(selected, rows):
            self.result_rows[item_id] = row
            self._update_result_item(item_id, row)
        self.search_stale = True
        stale_notice = "摘要已更新：请重新搜索以更新候选、命中依据和统计；整体结果导出暂不可用。"
        self.search_coverage_hint.set(stale_notice)
        self._set_text(self.coverage_view, stale_notice)
        self._set_text(self.summary_view, stale_notice)
        self._set_action_buttons("normal")
        success_count = len(rows) - len(failures)
        self.status.set(f"摘要处理完成：成功 {success_count}，失败 {len(failures)}；重新搜索可更新候选与统计")
        self._apply_result_view()
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
            self.root.after(0, self._action_error, "PDF 下载失败", str(exc))

    def _pdf_done(
        self,
        selected: list[tuple[str, dict[str, object]]],
        rows: list[dict[str, object]],
        failures: dict[str, str],
    ) -> None:
        for (item_id, _old_row), row in zip(selected, rows):
            self._update_access_result(item_id, row)
        self._set_action_buttons("normal")
        success_count = len(rows) - len(failures)
        self.status.set(f"PDF 处理完成：成功 {success_count}，失败 {len(failures)}")
        self._apply_result_view()
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

        for conference in (getattr(self, "fetch_venues", []) if self.fetch_conference.get() == "目录多选" else UI_FETCHABLE_KEYS):
            display_name = conference
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
            selected = self._selected_fetch_conferences(conference)
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
            args=(conference, year, output_root, refresh, selected),
            daemon=True,
        ).start()

    def _fetch_worker(
        self, conference: str, year: int, output_root: Path, refresh: bool = True, selected: list[str] | None = None
    ) -> None:
        try:
            selected = selected if selected is not None else self._selected_fetch_conferences(conference)
            manifests: dict[str, dict] = {}
            rows: list[dict] = []
            jsonl_rows: list[dict] = []
            failures: dict[str, str] = {}

            with collector.dblp_access_session(
                    lambda message: self.root.after(0, self.status.set, message)):
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
                        if len(selected) == 1:
                            raise

            if len(selected) > 1:
                collector.build_combined_outputs(
                    output_root,
                    year=year,
                    requested_keys=selected,
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
                "以下会议/期刊抓取失败：\n" + "\n".join(f"{k}: {v}" for k, v in failures.items()),
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
            if not root.is_dir():
                return []
            for item in sorted(root.iterdir(), key=lambda p: p.name):
                if item.is_dir() and item.name.isdigit():
                    years.append(int(item.name))
            return years

        if start is not None and end is not None and start > end:
            raise ValueError("起始年份不能大于结束年份")

        if start is None:
            start = end
        if end is None:
            end = start
        return list(range(int(start), int(end) + 1))

    def _search(self) -> None:
        if self.search_busy:
            return
        try:
            if not self.output_root.get().strip():
                raise ValueError("论文目录不能为空")
            root = Path(self.output_root.get()).expanduser().resolve()
            if self.search_mode.get() == "主题多字段":
                config = self._topic_request(root)
                query, targets = None, config["targets"]
            else:
                query = self.search_text.get().strip()
                search.titles.parse_query(query)
                targets, config = self._manual_targets(root), None
        except (OSError, ValueError, KeyError, TypeError, collector.argparse.ArgumentTypeError) as exc:
            messagebox.showerror("搜索条件无效", str(exc))
            return
        self.last_search_result = None
        self.last_search_request = None
        for item in self.result_rows:
            self.results.delete(item)
        self.result_rows.clear()
        self.result_count.set("显示 0 / 0 篇")
        for view in (self.coverage_view, self.summary_view, self.detail_view):
            self._set_text(view, "")
        self.search_coverage_hint.set("正在读取本地论文和已有摘要…")
        self.search_busy = True
        self._set_action_buttons("disabled")
        self.status.set("搜索中…")
        request = dict(query=query, config=config, targets=targets, snapshot_output_root=str(root))
        threading.Thread(target=self._search_worker, args=(root, request), daemon=True).start()

    def _search_worker(self, root: Path, request: dict) -> None:
        try:
            result = search.search_local(root, request["targets"], query=request["query"], config=request["config"])
            self.root.after(0, self._search_done, result, request)
        except Exception as exc:
            self.root.after(0, self._search_failed, str(exc))

    def _search_failed(self, detail: str) -> None:
        self.search_busy = False
        self.search_coverage_hint.set("搜索失败，未生成本次结果。")
        self._action_error("搜索失败", detail)

    def _search_done(self, result: dict, request: dict) -> None:
        self.search_busy = False
        self.search_stale = False
        self.last_search_result, self.last_search_request = result, request
        self._set_action_buttons("normal")
        self.search_query = request["query"] or json.dumps(request["config"], ensure_ascii=False)
        for row in result["candidates"]:
            item_id = self.results.insert("", END)
            self.result_rows[item_id] = row
            self._update_result_item(item_id, row)
        self._apply_result_view()
        coverage = search.coverage_text(result)
        self._set_text(self.coverage_view, coverage)
        self.search_coverage_hint.set("\n".join(coverage.splitlines()[:2]))
        lines = ["原始命中数 = 词语/字段命中证据条数；去重数 = 该会议年份、分组、字段中的论文数。"]
        for item in result["summary"]:
            lines.append(f"{item['conference']} {item['year']} | {item['query_group']} | {item['matched_field']}：证据 {item['raw_match_count']}，论文 {item['deduplicated_match_count']}")
        self._set_text(self.summary_view, "\n".join(lines) if result["summary"] else "标题查询的逐会议、逐年份论文数量见覆盖范围页。")
        self.status.set(f"搜索完成：{len(result['candidates'])} 篇；" + ("本地覆盖完整" if result["complete"] else "覆盖不完整，请查看覆盖范围"))



def main() -> None:
    root = Tk()
    PaperUI(root)
    root.mainloop()


if __name__ == "__main__":
    main()
