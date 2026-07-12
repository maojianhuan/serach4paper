#!/usr/bin/env python3
"""Validate one real paper for every bundled CCF A conference.

The bundled ``ccf_a_conferences.json`` is the CCF 2026 (seventh-edition)
catalog.  This program deliberately fetches only one target-year paper per
venue, using the same source policy as the full collector.  It is therefore a
bounded source-health check, not a replacement for full proceedings collection.

Examples
--------
    python validate_ccf_a_2025.py
    python validate_ccf_a_2025.py --year 2025 --conference CVPR --conference ICLR

The default output records both successful samples and honest failures. If the
bundled catalog documents that a conference was not held in the requested year,
the validator retrieves the documented most-recent main-conference edition and
marks that row ``verified_fallback_year`` rather than mislabeling it as a 2025
paper.
"""

from __future__ import annotations

import argparse
import csv
import json
import tempfile
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    from . import fetch_openreview_accepted as crawler
except ImportError:  # pragma: no cover
    import fetch_openreview_accepted as crawler


DEFAULT_YEAR = 2025
DEFAULT_OUTPUT_DIR = Path(__file__).resolve().parent / "ccf_a_validation"
DEFAULT_CONFERENCES = tuple(crawler.CCF_A_BY_KEY)
# The validator may make two public requests per venue. A small pause avoids
# turning a 58-conference audit into a burst against a shared scholarly index.
DEFAULT_REQUEST_PAUSE_SECONDS = 1.0

VALIDATION_FIELDS = [
    "conference",
    "ccf_abbreviation",
    "ccf_category",
    "ccf_type",
    "ccf_professional_field",
    "year",
    "fetched_year",
    "year_fallback",
    "year_fallback_reason",
    "status",
    "selected_source",
    "fallback_used",
    "record_year",
    "sample_title",
    "sample_authors",
    "sample_source_url",
    "sample_record_id",
    "source_urls",
    "error",
]


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def selected_conferences(values: list[str] | None) -> list[str]:
    if not values:
        return list(DEFAULT_CONFERENCES)
    keys: list[str] = []
    for value in values:
        key = crawler.normalize_conference_argument(value)
        if key == "ALL":
            keys.extend(DEFAULT_CONFERENCES)
        else:
            keys.append(key)
    return list(dict.fromkeys(keys))


def source_urls(sources: list[dict[str, Any]]) -> str:
    return "; ".join(
        sorted({str(source.get("url", "")) for source in sources if source.get("url")})
    )


def validate_one(spec: crawler.ConferenceSpec, year: int) -> dict[str, Any]:
    """Return one audit row without retaining downloaded raw material."""
    catalog_entry = crawler.CCF_A_BY_KEY.get(spec.key, {})
    try:
        fetched_year = int(catalog_entry.get("validation_fallback_year", year))
    except (TypeError, ValueError):
        fetched_year = year
    uses_year_fallback = fetched_year != year
    fallback_reason = (
        str(catalog_entry.get("validation_note", "")) if uses_year_fallback else ""
    )
    base = {
        "conference": spec.key,
        "ccf_abbreviation": spec.ccf_abbreviation,
        "ccf_category": spec.ccf_category,
        "ccf_type": spec.ccf_type,
        "ccf_professional_field": spec.ccf_professional_field,
        "year": year,
        "fetched_year": fetched_year,
        "year_fallback": str(uses_year_fallback).lower(),
        "year_fallback_reason": fallback_reason,
        "status": "not_verified",
        "selected_source": "",
        "fallback_used": "",
        "record_year": "",
        "sample_title": "",
        "sample_authors": "",
        "sample_source_url": "",
        "sample_record_id": "",
        "source_urls": "",
        "error": "",
    }
    with tempfile.TemporaryDirectory(
        prefix=f"ccf_{spec.key}_{fetched_year}_"
    ) as temporary_dir:
        try:
            row, rich, sources, decision = crawler.fetch_conference_sample(
                Path(temporary_dir), spec=spec, year=fetched_year
            )
        except RuntimeError as exc:
            base["error"] = str(exc)
            return base

    record_year = crawler.rich_record_year(row, rich, fetched_year)
    record_matches = record_year == str(fetched_year)
    base.update(
        {
            "status": (
                "verified_fallback_year"
                if record_matches and uses_year_fallback
                else "verified"
                if record_matches
                else "not_verified"
            ),
            "selected_source": str(decision.get("selected_source", "")),
            "fallback_used": str(bool(decision.get("fallback_used", False))).lower(),
            "record_year": record_year,
            "sample_title": row.get("title", ""),
            "sample_authors": row.get("authors", ""),
            "sample_source_url": row.get("source_url", ""),
            "sample_record_id": row.get("source_record_id", "") or row.get("openreview_id", ""),
            "source_urls": source_urls(sources),
        }
    )
    if decision.get("official_error"):
        base["error"] = f"Official source unavailable; DBLP fallback used: {decision['official_error']}"
    if not record_matches:
        base["error"] = (
            f"source returned record year {record_year!r}, not {fetched_year}"
        )
    return base


def validate_catalog(
    conferences: list[str], year: int, *, pause_seconds: float = DEFAULT_REQUEST_PAUSE_SECONDS
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for index, key in enumerate(conferences):
        rows.append(validate_one(crawler.CONFERENCE_SPECS[key], year))
        if pause_seconds > 0 and index + 1 < len(conferences):
            time.sleep(pause_seconds)
    return rows


def write_outputs(rows: list[dict[str, Any]], output_dir: Path, year: int) -> dict[str, str]:
    output_dir.mkdir(parents=True, exist_ok=True)
    csv_path = output_dir / f"ccf_a_{year}_sample_validation.csv"
    json_path = output_dir / f"ccf_a_{year}_sample_validation.json"
    crawler.write_csv(csv_path, rows, VALIDATION_FIELDS)
    status_counts = dict(Counter(row["status"] for row in rows))
    crawler.write_json(
        json_path,
        {
            "created_at_utc": utc_now(),
            "year": year,
            "catalog": {
                "name": crawler.CCF_A_CATALOG.get("catalog_name", ""),
                "version": crawler.CCF_A_CATALOG.get("catalog_version", ""),
                "source_url": crawler.CCF_A_CATALOG.get("catalog_source_url", ""),
                "conference_count": len(crawler.CCF_A_BY_KEY),
            },
            "status_counts": status_counts,
            "rows": rows,
        },
    )
    return {"csv": str(csv_path), "json": str(json_path)}


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Validate one target-year paper for each CCF A conference."
    )
    parser.add_argument("--year", type=crawler.valid_year, default=DEFAULT_YEAR)
    parser.add_argument(
        "--conference",
        action="append",
        help="Optional conference alias; repeatable. Defaults to every CCF A conference.",
    )
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument(
        "--pause-seconds",
        type=float,
        default=DEFAULT_REQUEST_PAUSE_SECONDS,
        help="Polite delay between venues during public-source validation (default: 1.0).",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.pause_seconds < 0:
        raise ValueError("--pause-seconds must be non-negative")
    conferences = selected_conferences(args.conference)
    rows = validate_catalog(
        conferences, args.year, pause_seconds=args.pause_seconds
    )
    outputs = write_outputs(rows, args.output_dir.resolve(), args.year)
    verified = sum(row["status"].startswith("verified") for row in rows)
    verified_fallback_year = sum(
        row["status"] == "verified_fallback_year" for row in rows
    )
    print(
        json.dumps(
            {
                "year": args.year,
                "requested": len(rows),
                "verified": verified,
                "not_verified": len(rows) - verified,
                "verified_fallback_year": verified_fallback_year,
                "outputs": outputs,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0 if verified == len(rows) else 1


if __name__ == "__main__":
    raise SystemExit(main())
