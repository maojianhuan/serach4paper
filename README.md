# search4paper

[中文](#zh-cn) | [English](#en)

<a id="zh-cn"></a>

## 中文说明

### 项目简介

search4paper 是一个面向文献调研的论文抓取、本地检索和论文整理工具，重点支持以下会议：

- ICML
- NeurIPS / NIPS
- ICLR
- AAAI
- KDD
- WWW / The Web Conference

项目同时保留了部分 CCF A 类其他会议和期刊的配置，但它们不是当前核心使用场景。

典型工作流：

1. 按会议、年份抓取论文元数据；
2. 使用关键词搜索论文标题；
3. 获取论文摘要，判断论文是否值得阅读；
4. 导出适合 AI 阅读的 JSONL 数据；
5. 下载公开可用的论文 PDF。

### 快速开始

#### 直接运行 Windows EXE

双击项目根目录下的：

```text
search4paper-ui.exe
```

EXE 不要求本机预先安装 Python。源码更新不会自动更新已有 EXE；
本轮新增的搜索界面功能需要在 Windows 上重新构建后才会进入 EXE。

#### 使用 Python 启动 UI

要求 Python 3.10 或更高版本：

```powershell
python .\start_ui.py
```

#### 从源码重新构建 EXE

在 Windows Python 环境中先安装 PyInstaller：

```powershell
pip install pyinstaller
python .\code\build_ui_exe.py
```

构建后的 EXE 会复制到项目根目录。`.pyinstaller/`、`dist/` 和 `*.spec` 是构建产物，已经加入 `.gitignore`，不应提交到仓库。

### 数据源和抓取策略

核心会议使用不同的数据源，结果中的 `source_manifest.json` 会记录实际来源。

- ICML、NeurIPS、ICLR：优先使用 OpenReview 精确 venue ID 查询。
- WWW：优先查询 OpenReview；如果 OpenReview 为空或不可用，则回退到 DBLP。
- AAAI：优先解析官方录用论文页面；如果官方页面不可用或无法解析，则回退到 DBLP。
- KDD：按照当前配置优先使用官方录用列表，官方数据不可用时不会静默替换成 DBLP。
- 其他会议和期刊：按照 `code/ccf_a_conferences.json` 和抓取器中的会议配置执行。

DBLP 回退结果代表已经被 DBLP 收录的出版论文，不等同于实时录用决定列表。回退结果会标记为 `dblp_fallback`，并记录 OpenReview 或官方来源的失败原因。

对于尚未公布录用结果的会议，程序会生成空结果状态，但不会把空结果永久当作有效缓存；下一次运行会重新检查数据源。

### UI 功能

UI 主要包含抓取和搜索两部分：

- 选择会议和年份，抓取论文元数据；
- 在本地快照中进行标题布尔查询或主题多字段检索；
- 直接填写主题词、上下文词，勾选搜索字段，也可加载/保存主题配置；
- 自动读取已有摘要补全数据，查看逐会议年份的覆盖范围和命中统计；
- 查看摘要与命中依据，导出候选论文及覆盖报告；
- 批量获取选中论文摘要；
- 导出适合 AI 阅读的 JSONL；
- 批量下载公开 PDF；
- 查看抓取来源、警告和失败信息。

### 命令行抓取

示例：

```powershell
python -m code.fetch_openreview_accepted --conference ICML --year 2026 --output-dir .\output
python -m code.fetch_openreview_accepted --conference AAAI --year 2026 --output-dir .\output
python -m code.fetch_openreview_accepted --conference WWW --year 2026 --output-dir .\output
```

常用参数：

```text
--conference       会议名称，例如 ICML、NIPS、ICLR、AAAI、KDD、WWW、ALL
--year             目标年份
--output-dir       输出根目录，默认是项目根目录下的 output/
--page-size        OpenReview 分页大小
--refresh          忽略已有本地快照并重新抓取
--no-iclr-virtual  不进行 ICLR Virtual 补充
```

### 本地缓存

正常运行时，程序会检查本地快照。如果以下条件都满足，就直接复用本地数据，不重复抓取：

- `source_manifest.json` 存在；
- CSV 和 JSONL 文件存在；
- 会议、年份和数据源标识匹配；
- 文件中的论文数量与 manifest 一致；
- 论文 ID 存在且不重复。

完整快照复用时，manifest 的状态为 `reused_local_snapshot`。使用 `--refresh` 可以强制重新抓取。

对于 AAAI 和 WWW 的 DBLP 回退快照，程序会再次检查主要数据源，以便后续官方或 OpenReview 数据发布后自动升级结果。

### 输出目录

默认输出目录：

```text
output/<YEAR>/<CONFERENCE>/
```

主要文件：

```text
<CONFERENCE>_<YEAR>_accepted_papers.csv
<CONFERENCE>_<YEAR>_accepted_papers.jsonl
source_manifest.json
raw/
```

摘要和 PDF enrichment 数据位于对应输出目录的 `enrichment/` 下，通常包括：

```text
enrichment/abstracts.jsonl
enrichment/pdf_manifest.jsonl
enrichment/pdf/
```

标题关键词查询结果默认写入：

```text
keyword_query_results/
```

### 标题搜索

UI 的“标题布尔查询”与本地命令行使用同一匹配器。示例：

```powershell
python -m code.paper_search --query '"time series" AND anomal* AND NOT forecasting' --conference ICLR ICML --years 2025 2026 --snapshot-output-root output --output-dir keyword_query_results/my_query
```

保留的采集加查询脚本会获取或复用快照：

```powershell
python -m code.query_target_papers
```

查询支持布尔表达式、短语、通配符和否定条件，具体以脚本当前实现为准。

### 多字段主题搜索

在“搜索论文”页选择“主题多字段”，填写以分号分隔的词组（例如 `episodic memory; memory retrieval`），
勾选标题、摘要、关键词、TLDR。上下文词留空时，任一词组命中即可入选；
填写上下文词后，还需在所选字段中匹配任一上下文词。主题匹配沿用宽松词前缀规则。
可通过界面保存配置，或加载多分组配置；加载后以配置内的会议、年份和字段为准。
标题模式支持布尔表达式；主题词组框不解析 AND/OR/NOT。

“覆盖范围”页区分已检索、未抓取、空快照和未发现公开论文，展示数据来源、采集时间、警告、缺摘要数量。
缺失范围可以与已有结果一起查看，但明确标注覆盖不完整；损坏快照会使本次搜索失败。
“命中统计”按会议、年份、主题组和字段统计；证据条数不等于论文数量，也不能跨字段相加当作总论文数。
缺失范围的零计数不代表该会议没有相关论文。
搜索只读本地文件，不隐式补抓数据；“获取选中摘要”完成后，整体结果导出会禁用，
覆盖范围和统计页会提示需要重新搜索。只有重新搜索成功后才能导出更新后的结果、命中依据和统计。
“导出本次结果与统计”生成候选 CSV/JSONL、`coverage.csv`、`query_summary.csv`、`search_report.json`。
已有摘要优先保留，仅对缺摘要论文读入 `output/enrichment/abstracts.jsonl` 中成功的补全记录。

与界面完全相同的本地主题搜索：

```powershell
python -m code.paper_search --config configs/my_topic.json --snapshot-output-root output --output-dir research_results/my_topic
```

本地 CLI 在覆盖不完整时仍导出明确标记的部分结果，并返回退出码 1；完整本地覆盖不表示远程名单已发布完整。
以下为保留的采集、检索及候选摘要补全流程配置：

主题检索要求显式指定 `--config` 和 `--output-dir`，没有内置的个人调研主题。
自行创建 `configs/my_topic.json`，例如：

```json
{
  "targets": [{"conference": "ICLR", "year": 2025}],
  "search_fields": ["title", "abstract", "keywords", "tldr"],
  "query_groups": {
    "time_series": {
      "direct": true,
      "terms": ["time series"]
    }
  }
}
```

`direct: true` 表示该分组命中任一词语即可入选，无需额外上下文词。
结果是关键词候选集，尚未经过语义相关性审核。

```powershell
python -m code.query_research_topic --config configs/my_topic.json --output-dir research_results/my_topic
```

`--snapshot-output-root` 可指定论文快照目录，默认使用 `output/`。
个人检索配置 `configs/` 和生成结果 `research_results/` 均被 Git 忽略；
这些目录及示例配置文件需要在本地创建。

### 项目结构

```text
search4paper/
├─ code/
│  ├─ build_ui_exe.py
│  ├─ fetch_openreview_accepted.py
│  ├─ fetch_iclr2026.py
│  ├─ paper_enrichment.py
│  ├─ paper_ui.py
│  ├─ paper_search.py
│  ├─ query_target_papers.py
│  ├─ query_research_topic.py
│  ├─ validate_ccf_a_2025.py
│  └─ ccf_a_conferences.json
├─ tests/
├─ start_ui.py
├─ search4paper-ui.exe
├─ output/
├─ README.md
├─ LICENSE
└─ .gitignore
```

### 说明

- 抓取结果依赖目标会议的公开发布节奏和网络可用性。
- 摘要补充和 PDF 下载只使用程序能够找到的公开来源。
- DBLP 适合做出版论文兜底，不应被理解为实时录用数据库。

<a href="#en">English version</a>

<a id="en"></a>

## English

### Overview

search4paper is a Windows-friendly tool for collecting conference paper metadata, searching local snapshots, enriching abstracts, exporting AI-readable records, and downloading public PDFs.

The primary conferences are:

- ICML
- NeurIPS / NIPS
- ICLR
- AAAI
- KDD
- WWW / The Web Conference

Additional CCF A conferences and journals remain available through the bundled catalog, but they are secondary to the six conferences above.

Typical workflow:

1. Fetch metadata by conference and year.
2. Search paper titles locally with keywords.
3. Retrieve abstracts and decide which papers are worth reading.
4. Export selected records as JSONL for AI-assisted reading.
5. Download publicly available PDFs.

### Quick start

#### Run the Windows executable

Double-click:

```text
search4paper-ui.exe
```

The executable does not require Python to be installed. Source changes do not update
the existing binary: rebuild on Windows to include the new search interface.

#### Run the UI with Python

Python 3.10 or newer is recommended:

```powershell
python .\start_ui.py
```

#### Build the executable

In a Windows Python environment, install PyInstaller and run:

```powershell
pip install pyinstaller
python .\code\build_ui_exe.py
```

The executable is copied to the repository root. `.pyinstaller/`, `dist/`, and `*.spec` are generated build artifacts and are ignored by Git.

### Data sources and fallback policy

The actual source is recorded in `source_manifest.json`.

- ICML, NeurIPS, and ICLR primarily use exact OpenReview venue-ID queries.
- WWW queries OpenReview first and falls back to DBLP when OpenReview is empty or unavailable.
- AAAI tries the official accepted-paper page first and falls back to DBLP when the official page is unavailable or cannot be parsed.
- KDD follows its configured official accepted-paper source and does not silently replace an unavailable official source with DBLP.
- Other venues follow the configuration in `code/ccf_a_conferences.json` and the collector implementation.

A DBLP fallback represents published proceedings indexed by DBLP, not a live submission-decision feed. Such results are marked as `dblp_fallback`, together with the reason why the primary source was not selected.

Empty results for an unpublished venue are not treated as permanent valid snapshots; later runs check the source again.

### UI features

The UI provides:

- Conference and year selection;
- Metadata collection;
- Boolean title search and multi-field topic search;
- Editable topic terms, context anchors and search fields, with configuration loading/saving;
- Existing abstract enrichment loaded before searching;
- Per-conference/year coverage, match statistics, abstracts and match evidence;
- Export of candidates, coverage and statistics;
- Batch abstract enrichment;
- AI-friendly JSONL export;
- Public PDF downloads;
- Source, warning, and failure reporting.

### Command-line collection

Examples:

```powershell
python -m code.fetch_openreview_accepted --conference ICML --year 2026 --output-dir .\output
python -m code.fetch_openreview_accepted --conference AAAI --year 2026 --output-dir .\output
python -m code.fetch_openreview_accepted --conference WWW --year 2026 --output-dir .\output
```

Useful options:

```text
--conference       ICML, NIPS, ICLR, AAAI, KDD, WWW, or ALL
--year             Target year
--output-dir       Output root; defaults to output/ in the repository root
--page-size        OpenReview page size
--refresh          Ignore local snapshots and fetch again
--no-iclr-virtual  Disable ICLR Virtual enrichment
```

### Local cache

With the default settings, a complete local snapshot is reused when:

- `source_manifest.json` exists;
- the CSV and JSONL outputs exist;
- the conference, year, and source identifiers match;
- the stored paper count matches the manifest;
- paper identifiers are present and unique.

A reused snapshot is reported as `reused_local_snapshot`. Use `--refresh` to force a new collection.

For AAAI and WWW DBLP fallback snapshots, the primary source is checked again so a later official or OpenReview release can replace the fallback data.

### Output layout

The default layout is:

```text
output/<YEAR>/<CONFERENCE>/
```

Main files:

```text
<CONFERENCE>_<YEAR>_accepted_papers.csv
<CONFERENCE>_<YEAR>_accepted_papers.jsonl
source_manifest.json
raw/
```

Abstract and PDF enrichment data are normally stored under:

```text
enrichment/abstracts.jsonl
enrichment/pdf_manifest.jsonl
enrichment/pdf/
```

Title-query results are written to:

```text
keyword_query_results/
```

### Title search

The UI Boolean title mode and local CLI share the same matcher:

```powershell
python -m code.paper_search --query '"time series" AND anomal* AND NOT forecasting' --conference ICLR ICML --years 2025 2026 --snapshot-output-root output --output-dir keyword_query_results/my_query
```

The existing collection-plus-query script fetches or reuses snapshots:

```powershell
python -m code.query_target_papers
```

The query implementation supports Boolean expressions, phrases, wildcards, and negation.

### Multi-field topic search

Select topic mode on the search page, enter semicolon-separated phrases and optional context anchors,
and select title, abstract, keywords or TLDR. Without context anchors, any phrase can qualify;
otherwise a context anchor must also match a selected field. Topic matching uses permissive word prefixes.
The phrase box does not parse Boolean operators. Save the configuration in the UI or load a multi-group
configuration; loaded targets and fields take precedence over manual controls.

Coverage distinguishes searched, missing, empty and unpublished snapshots and displays source, collection time,
warnings and missing abstracts. Missing targets produce explicitly incomplete results; corrupt snapshots fail the search.
Statistics separate conference, year, group and field: evidence counts are not paper counts and must not be summed
across fields as unique papers. Zero counts for missing targets do not establish absence of relevant papers.
Search only reads local files. After fetching selected abstracts, full-result export is disabled and
coverage/statistics are marked as requiring a new search. A successful search restores export with
updated abstracts, match evidence and statistics.
Export writes candidate CSV/JSONL, `coverage.csv`, `query_summary.csv` and `search_report.json`.
Existing snapshot abstracts take precedence; missing abstracts are filled from successful entries in
`output/enrichment/abstracts.jsonl` before matching.

The identical local topic search is available from the CLI:

```powershell
python -m code.paper_search --config configs/my_topic.json --snapshot-output-root output --output-dir research_results/my_topic
```

Incomplete local coverage exports clearly marked partial results and returns exit code 1.
Complete local coverage does not prove remote publication is complete.
The existing collection, search and candidate-abstract-enrichment pipeline remains available below:

Topic retrieval requires explicit `--config` and `--output-dir` arguments;
there is no built-in personal research topic. Create `configs/my_topic.json`, for example:

```json
{
  "targets": [{"conference": "ICLR", "year": 2025}],
  "search_fields": ["title", "abstract", "keywords", "tldr"],
  "query_groups": {
    "time_series": {
      "direct": true,
      "terms": ["time series"]
    }
  }
}
```

`direct: true` accepts a match on any term in the group without additional context anchors.
The output is a keyword candidate set, without semantic relevance review.

```powershell
python -m code.query_research_topic --config configs/my_topic.json --output-dir research_results/my_topic
```

Use `--snapshot-output-root` to select a snapshot directory (default: `output/`).
Personal configurations in `configs/` and generated results in `research_results/`
are ignored by Git; create these directories and the example configuration locally.

### Repository layout

```text
search4paper/
├─ code/
│  ├─ build_ui_exe.py
│  ├─ fetch_openreview_accepted.py
│  ├─ fetch_iclr2026.py
│  ├─ paper_enrichment.py
│  ├─ paper_ui.py
│  ├─ paper_search.py
│  ├─ query_target_papers.py
│  ├─ query_research_topic.py
│  ├─ validate_ccf_a_2025.py
│  └─ ccf_a_conferences.json
├─ tests/
├─ start_ui.py
├─ search4paper-ui.exe
├─ output/
├─ README.md
├─ LICENSE
└─ .gitignore
```

### Notes

- Collection depends on the venue's public release schedule and network availability.
- Abstract enrichment and PDF downloads use public sources available to the program.
- DBLP is a proceedings fallback and should not be interpreted as a real-time acceptance database.

<a href="#zh-cn">中文版本</a>

## License

See [LICENSE](LICENSE).