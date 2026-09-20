"""Shared, read-only local search used by the desktop UI and CLI."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from . import fetch_openreview_accepted as collector
from . import paper_enrichment as enrichment
from . import query_research_topic as topic
from . import query_target_papers as titles

COVERAGE_FIELDS = ["conference", "year", "status", "paper_count", "abstract_count",
                   "missing_abstract_count", "match_count", "source_kind", "ccf_category", "ccf_type", "collection_scope", "local_pdf_count", "fetched_at_utc", "warnings"]


def validate_config(config: dict[str, Any]) -> None:
    if not isinstance(config, dict) or not isinstance(config.get("targets"), list) or not config["targets"]:
        raise ValueError("targets must be a non-empty list of conference/year pairs")
    targets = [titles.normalize_target(target) for target in config["targets"]]
    if len(targets) != len(set(targets)):
        raise ValueError("Duplicate conference/year targets")
    fields = config.get("search_fields", list(topic.SEARCH_FIELDS))
    if not isinstance(fields, list) or not fields or any(field not in topic.SEARCH_FIELDS for field in fields):
        raise ValueError("search_fields must select title, abstract, keywords or tldr")
    groups = config.get("query_groups")
    if not isinstance(groups, dict) or not groups:
        raise ValueError("query_groups must contain at least one group")
    for name, group in groups.items():
        if not isinstance(group, dict) or not isinstance(group.get("terms"), list) or not group["terms"]:
            raise ValueError(f"{name}: terms must be a non-empty list")
        for key in ("terms", "standalone_terms"):
            if not isinstance(group.get(key, []), list) or any(not isinstance(term, str) or not term.strip() for term in group.get(key, [])):
                raise ValueError(f"{name}: {key} must contain non-empty strings")
        if "direct" in group and not isinstance(group["direct"], bool):
            raise ValueError(f"{name}: direct must be true or false")
    if not isinstance(config.get("context_anchors", []), list) or any(not isinstance(term, str) or not term.strip() for term in config.get("context_anchors", [])):
        raise ValueError("context_anchors must contain non-empty strings")


def load_local_papers(root: Path, targets: list[dict[str, Any]]) -> tuple[list[dict], list[dict]]:
    papers, coverage = [], []
    seen = set()
    for target in targets:
        conference, year = titles.normalize_target(target)
        if (conference, year) in seen:
            raise ValueError(f"Duplicate target: {conference} {year}")
        seen.add((conference, year))
        directory = root / str(year) / conference
        csv_path = directory / f"{conference}_{year}_accepted_papers.csv"
        manifest_path = directory / "source_manifest.json"
        item = dict(conference=conference, year=year, status="missing", paper_count=0,
                    abstract_count=0, missing_abstract_count=0, match_count=0,
                    source_kind="", fetched_at_utc="", warnings=[],
                    ccf_category=collector.CONFERENCE_SPECS[conference].ccf_category,
                    ccf_type=collector.CONFERENCE_SPECS[conference].ccf_type,
                    collection_scope="", local_pdf_count=0)
        coverage.append(item)
        if not csv_path.is_file() and not manifest_path.is_file():
            continue
        if not csv_path.is_file() or not manifest_path.is_file():
            raise ValueError(f"{conference} {year}: incomplete local snapshot (CSV/manifest missing)")
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if manifest.get("conference") != conference or manifest.get("year") != year:
            raise ValueError(f"{manifest_path}: conference/year mismatch")
        rows = collector.read_csv_rows(csv_path)
        counts = manifest.get("counts", {})
        expected = counts.get("accepted_paper_count", counts.get("openreview_current_accepted"))
        if expected is None or len(rows) != int(expected):
            raise ValueError(f"{csv_path}: paper count does not match manifest")
        source_kind = manifest.get("source_kind", "")
        id_field = "openreview_id" if source_kind == "openreview" else "source_record_id"
        ids = [row.get(id_field, "").strip() for row in rows]
        if any(not value for value in ids) or len(ids) != len(set(ids)):
            raise ValueError(f"{csv_path}: missing or duplicate paper IDs")
        for row in rows:
            if row.get("conference") != conference or str(row.get("year")) != str(year):
                raise ValueError(f"{csv_path}: row conference/year mismatch")
            if source_kind == "openreview" and row.get("venueid") != collector.CONFERENCE_SPECS[conference].venue_id(year):
                raise ValueError(f"{csv_path}: incorrect OpenReview venue ID")
        item.update(status="searched" if rows else "empty", paper_count=len(rows),
                    collection_scope=manifest.get("collection_scope", source_kind),
                    source_kind=source_kind, fetched_at_utc=manifest.get("fetched_at_utc", ""),
                    warnings=manifest.get("warnings", []))
        if manifest.get("collection_status") == "no_public_accepted_papers":
            if rows:
                raise ValueError(f"{csv_path}: unpublished snapshot contains papers")
            item["status"] = "unavailable"
        papers.extend(rows)
    papers = enrichment.load_cached_abstracts(papers, root)
    papers = enrichment.load_cached_pdfs(papers, root)
    for item in coverage:
        rows = [row for row in papers if row["conference"] == item["conference"] and str(row["year"]) == str(item["year"])]
        item["abstract_count"] = sum(bool(str(row.get("abstract", "")).strip()) for row in rows)
        item["local_pdf_count"] = sum(bool(row.get("pdf_local_path")) for row in rows)
        item["missing_abstract_count"] = len(rows) - item["abstract_count"]
    return papers, coverage


def search_local(root: Path, targets: list[dict], *, query: str | None = None,
                 config: dict | None = None) -> dict:
    if (query is None) == (config is None):
        raise ValueError("Select exactly one of a title query or topic configuration")
    if config is not None:
        validate_config(config)
        targets = config["targets"]
    parsed = titles.parse_query(query) if query is not None else None
    papers, coverage = load_local_papers(root, targets)
    summary = []
    if config is not None:
        candidates, evidence = topic.search_papers(papers, config)
        summary = topic.build_query_summary(evidence, config)
    else:
        candidates = []
        for paper in papers:
            hits = titles.match_title(str(paper.get("title", "")), parsed)
            if hits is not None:
                row = titles.result_row(paper, query=query, parsed_query=parsed, matches=hits)
                row.update(matched_fields=["title"], matched_terms=sorted({term for term, _ in hits}),
                           matched_snippets=[{"field": "title", "term": actual,
                                              "query_term": term, "text": paper["title"]}
                                             for term, actual in hits])
                candidates.append(row)
    for item in coverage:
        item["match_count"] = sum(row.get("conference") == item["conference"] and str(row.get("year")) == str(item["year"]) for row in candidates)
    return dict(candidates=candidates, coverage=coverage, summary=summary,
                complete=all(item["status"] == "searched" for item in coverage),
                searched_paper_count=len(papers))


def coverage_text(result: dict) -> str:
    coverage = result["coverage"]
    searched = sum(item["status"] == "searched" for item in coverage)
    missing = sum(item["missing_abstract_count"] for item in coverage)
    labels = {"searched": "已检索", "missing": "未抓取", "empty": "空快照", "unavailable": "未发现公开论文"}
    lines = [f"已检索 {searched}/{len(coverage)} 个会议/期刊年份，{result['searched_paper_count']} 篇论文；"
             f"匹配 {len(result['candidates'])} 篇，缺摘要 {missing} 篇。",
             "覆盖范围完整。" if result["complete"] else "覆盖范围不完整：结果仅代表已有数据，请先抓取缺失范围。"]
    for item in coverage:
        lines.append(f"{item['conference']} {item['year']}：{labels[item['status']]}；"
                     f"{item['ccf_type']} {item['ccf_category']}；论文 {item['paper_count']}，匹配 {item['match_count']}，缺摘要 {item['missing_abstract_count']}；"
                     f"本地全文 {item['local_pdf_count']}；来源 {item['source_kind'] or '未知'}；采集时间 {item['fetched_at_utc'] or '未知'}")
        lines.extend(f"  警告：{warning}" for warning in item["warnings"])
    lines.append("本地覆盖不代表远程名单已完整发布；缺摘要可能导致主题检索漏检。")
    return "\n".join(lines)


def write_search_outputs(output_dir: Path, result: dict, request: dict) -> None:
    topic.write_candidate_files(output_dir, result["candidates"])
    collector.write_csv(output_dir / "coverage.csv", result["coverage"], COVERAGE_FIELDS)
    collector.write_csv(output_dir / "query_summary.csv", result["summary"], topic.SUMMARY_FIELDS)
    collector.write_json(output_dir / "search_report.json", {**request, **{key: value for key, value in result.items() if key != "candidates"}})


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Read-only local search, shared with the desktop UI")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--query")
    mode.add_argument("--config", type=Path)
    parser.add_argument("--conference", nargs="+")
    parser.add_argument("--years", nargs="+", type=int)
    parser.add_argument("--snapshot-output-root", type=Path, default=topic.DEFAULT_SNAPSHOT_ROOT)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args(argv)
    if args.config and (args.conference or args.years):
        parser.error("--config supplies its own targets; do not pass --conference/--years")
    if args.query is not None and (not args.conference or not args.years):
        parser.error("--query requires --conference and --years")
    try:
        config = json.loads(args.config.read_text(encoding="utf-8")) if args.config else None
        targets = config["targets"] if config else [dict(conference=c, year=y) for c in args.conference for y in args.years]
        result = search_local(args.snapshot_output_root, targets, query=args.query, config=config)
        write_search_outputs(args.output_dir, result, dict(query=args.query, config=config, targets=targets))
        print(coverage_text(result))
        return 0 if result["complete"] else 1
    except (OSError, ValueError, KeyError, TypeError, RuntimeError, argparse.ArgumentTypeError) as exc:
        print(f"Search failed: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
