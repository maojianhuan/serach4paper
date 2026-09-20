#!/usr/bin/env python3
"""Configuration-driven, auditable multi-field research-topic retrieval."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import shlex
import subprocess
import sys
import unicodedata
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from . import fetch_openreview_accepted as collector
from . import paper_enrichment
from .query_target_papers import token_matches, tokenize


PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_SNAPSHOT_ROOT = PROJECT_ROOT / "output"

SEARCH_FIELDS = ("title", "abstract", "keywords", "tldr")
CANDIDATE_FIELDS = [
    "conference", "year", "title", "authors", "abstract", "keywords", "tldr",
    "primary_area", "secondary_area", "topic", "openreview_id",
    "source_record_id", "source_url", "openreview_url", "paper_url", "pdf_url",
    "matched_query_groups", "matched_terms", "matched_fields", "matched_snippets",
    "abstract_source", "abstract_source_url", "abstract_retrieved_at", "abstract_match_confidence",
]
SUMMARY_FIELDS = [
    "conference", "year", "query_group", "matched_field", "raw_match_count",
    "deduplicated_match_count",
]


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def normalize_text(value: Any) -> str:
    """Normalize case, accents, punctuation, and hyphen/space variants."""
    if isinstance(value, (list, tuple, set)):
        value = "; ".join(str(item) for item in value)
    normalized = unicodedata.normalize("NFKD", str(value or "").casefold())
    normalized = "".join(c for c in normalized if not unicodedata.combining(c))
    return " ".join(re.findall(r"[a-z0-9]+", normalized))


def phrase_matches(text: Any, term: str) -> bool:
    """Match phrases with simple inflection support on every word."""
    text_tokens = tokenize(str(text or ""))
    term_tokens = tokenize(term)
    if not term_tokens or len(term_tokens) > len(text_tokens):
        return False
    width = len(term_tokens)
    return any(
        all(token_matches(wanted, actual) for wanted, actual in zip(term_tokens, window))
        for window in (text_tokens[index:index + width] for index in range(len(text_tokens) - width + 1))
    )


def matched_snippet(text: Any, term: str, *, limit: int = 260) -> str:
    """Return a short source excerpt near a matched term without inventing text."""
    source = re.sub(r"\s+", " ", str(text or "")).strip()
    if len(source) <= limit:
        return source
    wanted = tokenize(term)
    tokens = [(token, match.start()) for match in re.finditer(r"[^\W_]+", source)
              for token in tokenize(match.group())]
    center = next((tokens[index][1] for index in range(len(tokens) - len(wanted) + 1)
                   if wanted and all(token_matches(term_token, tokens[index + offset][0])
                                     for offset, term_token in enumerate(wanted))), 0)
    start = max(0, center - limit // 3)
    end = min(len(source), start + limit)
    prefix = "..." if start else ""
    suffix = "..." if end < len(source) else ""
    return prefix + source[start:end].strip() + suffix


def paper_identity(row: dict[str, Any]) -> str:
    """Apply the documented stable deduplication priority."""
    for field in ("openreview_id", "source_record_id", "doi"):
        value = str(row.get(field, "")).strip()
        if value:
            if field == "source_record_id":
                return f"{field}:{row.get('conference', '')}:{row.get('year', '')}:{value}"
            return f"{field}:{value}"
    return f"title:{normalize_text(row.get('title'))}:{row.get('year', '')}"


def _has_any(text: str, terms: Iterable[str]) -> bool:
    return any(phrase_matches(text, term) for term in terms)


def find_matches(
    row: dict[str, Any], config: dict[str, Any]
) -> list[dict[str, str]]:
    """Return term/field evidence that passes each group's context rule."""
    fields = tuple(config.get("search_fields") or SEARCH_FIELDS)
    combined = " ".join(str(row.get(field, "") or "") for field in fields)
    has_context = _has_any(combined, config.get("context_anchors", []))
    evidence: list[dict[str, str]] = []
    for group_name, group in config["query_groups"].items():
        terms = group.get("terms", [])
        standalone = set(group.get("standalone_terms", []))
        direct = bool(group.get("direct", False))
        group_hits: list[dict[str, str]] = []
        for field in fields:
            value = row.get(field, "")
            for term in terms:
                if phrase_matches(value, term):
                    group_hits.append({
                        "group": group_name,
                        "term": term,
                        "field": field,
                        "snippet": matched_snippet(value, term),
                    })
        if group_hits and (direct or has_context or any(hit["term"] in standalone for hit in group_hits)):
            evidence.extend(group_hits)
    unique: dict[tuple[str, str, str, str], dict[str, str]] = {}
    for item in evidence:
        unique[(item["group"], item["term"], item["field"], item["snippet"])] = item
    return list(unique.values())


def search_papers(
    papers: Iterable[dict[str, Any]], config: dict[str, Any]
) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    """Search, deduplicate, and merge every evidence item for each paper."""
    merged: dict[str, dict[str, Any]] = {}
    raw_evidence: list[dict[str, str]] = []
    for source in papers:
        row = dict(source)
        evidence = find_matches(row, config)
        if not evidence:
            continue
        identity = paper_identity(row)
        record = merged.setdefault(identity, {**row, "_evidence": []})
        known = {
            (item["group"], item["term"], item["field"], item["snippet"])
            for item in record["_evidence"]
        }
        for item in evidence:
            keyed = {"conference": str(row.get("conference", "")), "year": str(row.get("year", "")), "paper_key": identity, **item}
            raw_evidence.append(keyed)
            key = (item["group"], item["term"], item["field"], item["snippet"])
            if key not in known:
                record["_evidence"].append(item)
                known.add(key)

    candidates: list[dict[str, Any]] = []
    for record in merged.values():
        evidence = record.pop("_evidence")
        record["matched_query_groups"] = sorted({item["group"] for item in evidence})
        record["matched_terms"] = sorted({item["term"] for item in evidence})
        record["matched_fields"] = sorted({item["field"] for item in evidence})
        record["matched_snippets"] = [
            {"query_group": item["group"], "term": item["term"],
             "field": item["field"], "text": item["snippet"]}
            for item in evidence
        ]
        candidates.append(record)
    candidates.sort(key=lambda row: (
        str(row.get("conference", "")), normalize_text(row.get("title", "")),
        paper_identity(row),
    ))
    return candidates, raw_evidence


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _accepted_count(manifest: dict[str, Any]) -> int:
    counts = manifest.get("counts", {})
    return int(counts.get("accepted_paper_count", counts.get("openreview_current_accepted", 0)))


def collect_snapshots(
    config: dict[str, Any], snapshot_root: Path
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, str], list[str]]:
    """Reuse valid accepted-paper snapshots and independently audit their rows."""
    papers: list[dict[str, Any]] = []
    snapshots: list[dict[str, Any]] = []
    errors: dict[str, str] = {}
    warnings: list[str] = []
    for target in config["targets"]:
        conference = collector.normalize_conference_argument(str(target["conference"]))
        year = collector.valid_year(str(target["year"]))
        key = f"{conference}-{year}"
        spec = collector.CONFERENCE_SPECS[conference]
        try:
            manifest, rows, _ = collector.build_conference_outputs(
                snapshot_root.resolve(), spec=spec, year=year, page_size=1000,
                use_iclr_virtual=True, refresh=False, include_rows=True,
            )
            expected = _accepted_count(manifest)
            identities = [paper_identity(row) for row in rows]
            exact_venue = spec.venue_id(year)
            integrity = {
                "count_matches_manifest": len(rows) == expected,
                "paper_ids_present": all(
                    str(row.get("openreview_id") or row.get("source_record_id") or "").strip()
                    for row in rows
                ),
                "paper_ids_unique": len(identities) == len(set(identities)),
                "exact_venue_id_matches": (manifest.get("source_kind", spec.source_kind) != "openreview"
                                           or all(str(row.get("venueid", "")) == exact_venue for row in rows)),
            }
            if not all(integrity.values()):
                raise RuntimeError(f"Snapshot integrity check failed: {integrity}")
            manifest_path = Path(manifest["outputs"].get("manifest") or snapshot_root / str(year) / conference / "source_manifest.json")
            csv_path = Path(manifest["outputs"]["csv"])
            jsonl_path = Path(manifest["outputs"]["jsonl"])
            snapshots.append({
                "conference": conference, "year": year,
                "venue_id": manifest.get("venue_id", ""), "source_kind": manifest.get("source_kind", ""),
                "collection_status": manifest.get("collection_status", ""),
                "accepted_paper_count": len(rows), "fetched_at_utc": manifest.get("fetched_at_utc", ""),
                "source_manifest_path": str(manifest_path),
                "source_manifest_sha256": _sha256_file(manifest_path),
                "snapshot_csv_path": str(csv_path), "snapshot_csv_sha256": _sha256_file(csv_path),
                "snapshot_jsonl_path": str(jsonl_path), "snapshot_jsonl_sha256": _sha256_file(jsonl_path),
                "integrity": integrity,
            })
            papers.extend(rows)
            warnings.extend(f"{key}: {warning}" for warning in manifest.get("warnings", []))
        except (RuntimeError, OSError, KeyError, ValueError) as exc:
            raise RuntimeError(f"{key}: collection failed: {exc}") from exc
    return papers, snapshots, errors, warnings


def enrich_missing_candidate_abstracts(
    candidates: list[dict[str, Any]], snapshot_root: Path
) -> tuple[list[dict[str, Any]], dict[str, str]]:
    """Use existing enrichment only for already-recalled candidates lacking abstracts."""
    missing = [row for row in candidates if not str(row.get("abstract", "")).strip()]
    if not missing:
        return candidates, {}
    enriched, failures = paper_enrichment.enrich_abstracts(missing, snapshot_root, refresh=False)
    by_key = {paper_identity(row): row for row in enriched}
    return [by_key.get(paper_identity(row), row) for row in candidates], failures


def build_query_summary(raw_evidence: list[dict[str, str]], config: dict | None = None) -> list[dict[str, Any]]:
    raw_counts: dict[tuple[str, str, str, str], int] = defaultdict(int)
    papers: dict[tuple[str, str, str, str], set[str]] = defaultdict(set)
    if config:
        for target in config["targets"]:
            for group in config["query_groups"]:
                for field in config.get("search_fields", SEARCH_FIELDS):
                    key = (collector.normalize_conference_argument(target["conference"]), str(target["year"]), group, field)
                    raw_counts[key] = 0
    for item in raw_evidence:
        key = (item["conference"], item["year"], item["group"], item["field"])
        raw_counts[key] += 1
        papers[key].add(item["paper_key"])
    return [
        {"conference": key[0], "year": key[1], "query_group": key[2], "matched_field": key[3],
         "raw_match_count": raw_counts[key], "deduplicated_match_count": len(papers[key])}
        for key in sorted(raw_counts)
    ]


def _csv_value(value: Any) -> Any:
    return json.dumps(value, ensure_ascii=False, sort_keys=True) if isinstance(value, (list, dict)) else value


def write_candidate_files(output_dir: Path, candidates: list[dict[str, Any]]) -> tuple[Path, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    csv_path = output_dir / "candidate_papers.csv"
    jsonl_path = output_dir / "candidate_papers.jsonl"
    csv_rows = [{field: _csv_value(row.get(field, "")) for field in CANDIDATE_FIELDS} for row in candidates]
    collector.write_csv(csv_path, csv_rows, CANDIDATE_FIELDS)
    collector.write_jsonl(jsonl_path, [{field: row.get(field, "") for field in CANDIDATE_FIELDS} for row in candidates])
    return csv_path, jsonl_path


def _git_commit() -> str:
    try:
        return subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=PROJECT_ROOT, check=True,
            capture_output=True, text=True, timeout=10,
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return "unknown"


def write_readme(output_dir: Path, config: dict[str, Any], command: str) -> Path:
    groups = "\n".join(f"- `{name}`" for name in config["query_groups"])
    targets = ", ".join(f"{target['conference']} {target['year']}" for target in config["targets"])
    fields = ", ".join(f"`{field}`" for field in (config.get("search_fields") or SEARCH_FIELDS))
    path = output_dir / "README.md"
    path.write_text(
        "# Research-topic keyword candidates\n\n"
        "This dataset is an automatically retrieved keyword candidate set. "
        "It has **not** undergone semantic relevance review, method classification, or RCA analysis.\n\n"
        f"## Data source\n\n{targets} accepted papers, "
        "using the repository's validated local snapshot/cache logic.\n\n"
        f"## Search fields\n\n{fields}.\n\n"
        f"## Query groups\n\n{groups}\n\n"
        f"## Run command\n\n```text\n{command}\n```\n\n"
        "## Outputs\n\n- `candidate_papers.csv`\n- `candidate_papers.jsonl`\n"
        "- `query_summary.csv`\n- `run_manifest.json`\n",
        encoding="utf-8",
    )
    return path


def run_pipeline(config_path: Path, output_dir: Path, snapshot_root: Path) -> dict[str, Any]:
    started = utc_now()
    config = json.loads(config_path.read_text(encoding="utf-8"))
    from .paper_search import validate_config
    validate_config(config)
    papers, snapshots, collection_errors, warnings = collect_snapshots(config, snapshot_root)
    papers = paper_enrichment.load_cached_abstracts(papers, snapshot_root)
    initial_candidates, initial_evidence = search_papers(papers, config)
    candidates, abstract_errors = enrich_missing_candidate_abstracts(initial_candidates, snapshot_root)
    # Re-run matching so newly enriched abstracts contribute auditable evidence.
    candidates, raw_evidence = search_papers(candidates, config)
    summary = build_query_summary(raw_evidence, config)
    csv_path, jsonl_path = write_candidate_files(output_dir, candidates)
    summary_path = output_dir / "query_summary.csv"
    collector.write_csv(summary_path, summary, SUMMARY_FIELDS)
    command = shlex.join([sys.executable, "-m", "code.query_research_topic",
                          "--config", str(config_path.resolve()),
                          "--output-dir", str(output_dir.resolve()),
                          "--snapshot-output-root", str(snapshot_root.resolve())])
    readme_path = write_readme(output_dir, config, command)
    manifest = {
        "started_at_utc": started, "completed_at_utc": utc_now(), "git_commit": _git_commit(),
        "config_path": str(config_path.resolve()), "config_sha256": _sha256_file(config_path),
        "input_conferences": config["targets"], "snapshots": snapshots,
        "accepted_paper_counts": {f"{item['conference']}-{item['year']}": item["accepted_paper_count"] for item in snapshots},
        "initial_raw_match_count": len(initial_evidence),
        "initial_candidate_count": len(initial_candidates),
        "deduplicated_candidate_count": len(candidates),
        "missing_abstract_count": sum(not str(row.get("abstract", "")).strip() for row in candidates),
        "collection_errors": collection_errors, "abstract_enrichment_errors": abstract_errors,
        "warnings": warnings, "command": command,
        "outputs": {"csv": str(csv_path), "jsonl": str(jsonl_path),
                    "query_summary": str(summary_path), "readme": str(readme_path)},
    }
    manifest_path = output_dir / "run_manifest.json"
    collector.write_json(manifest_path, manifest)
    manifest["outputs"]["manifest"] = str(manifest_path)
    return manifest


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="High-recall multi-field research-topic retrieval")
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--snapshot-output-root", type=Path, default=DEFAULT_SNAPSHOT_ROOT)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    try:
        manifest = run_pipeline(args.config.resolve(), args.output_dir.resolve(), args.snapshot_output_root.resolve())
    except (OSError, ValueError, RuntimeError) as exc:
        print(json.dumps({"status": "failed", "error": str(exc)}, ensure_ascii=False, indent=2))
        return 1
    print(json.dumps(manifest, ensure_ascii=False, indent=2))
    return 1 if manifest["collection_errors"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
