#!/usr/bin/env python3
"""Download and reconcile the current ICLR 2026 accepted-paper list.

Primary membership source:
  OpenReview API v2 notes/search, filtered by content.venueid exactly equal to
  ICLR.cc/2026/Conference.

Enrichment / schedule source:
  ICLR Virtual's public static JSON files.  These contain conference schedule,
  topic, author-institution, and virtual-page metadata, but also contain Blog
  Track and Journal-to-Conference records, so they are never used unfiltered.

The script uses only Python's standard library.  It writes raw source snapshots,
normalized CSV/JSONL files, a source-difference report, and a provenance manifest.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import html
import json
import re
import sys
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlparse, parse_qs
from urllib.request import Request, urlopen


VENUE_ID = "ICLR.cc/2026/Conference"
VENUE_GROUP_URL = "https://openreview.net/group?id=ICLR.cc/2026/Conference"
OPENREVIEW_SEARCH_URL = "https://api2.openreview.net/notes/search"
VIRTUAL_META_URL = (
    "https://iclr.cc/static/virtual/data/iclr-2026-orals-posters.json"
)
VIRTUAL_ABSTRACTS_URL = (
    "https://iclr.cc/static/virtual/data/iclr-2026-abstracts.json"
)
VIRTUAL_MAIN_SOURCE_URL = (
    "https://openreview.net/group?id=ICLR.cc/2026/Conference"
)
USER_AGENT = (
    "ICLR2026AcceptedPapersCollector/1.0 "
    "(public scholarly metadata; contact via local operator)"
)
REFERENCE_COUNTS = {
    "openreview_current_accepted": 5351,
    "openreview_oral": 224,
    "openreview_poster": 5127,
    "virtual_all_events": 5691,
    "virtual_paper_nodes": 5468,
    "virtual_main_conference": 5353,
}


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def request_bytes(
    url: str,
    *,
    timeout: int = 90,
    retries: int = 3,
) -> tuple[bytes, dict[str, str]]:
    """Fetch a public URL with bounded retries and return bytes + key headers."""
    last_error: Exception | None = None
    for attempt in range(retries):
        req = Request(
            url,
            headers={
                "User-Agent": USER_AGENT,
                "Accept": "application/json,text/plain;q=0.9,*/*;q=0.1",
            },
        )
        try:
            with urlopen(req, timeout=timeout) as response:
                raw = response.read()
                headers = {
                    "url": response.geturl(),
                    "status": str(getattr(response, "status", 200)),
                    "content_type": response.headers.get("Content-Type", ""),
                    "content_length_header": response.headers.get(
                        "Content-Length", ""
                    ),
                    "etag": response.headers.get("ETag", ""),
                    "last_modified": response.headers.get("Last-Modified", ""),
                }
                return raw, headers
        except HTTPError as exc:
            last_error = exc
            # ChallengeRequiredError and ordinary 4xx responses should not be
            # hammered with retries.  The exception body is preserved in text.
            body = exc.read().decode("utf-8", errors="replace")
            if 400 <= exc.code < 500:
                raise RuntimeError(
                    f"HTTP {exc.code} for {url}: {body[:500]}"
                ) from exc
        except (URLError, TimeoutError) as exc:
            last_error = exc
        if attempt + 1 < retries:
            time.sleep(2**attempt)
    raise RuntimeError(f"Failed to fetch {url}: {last_error}")


def request_json(url: str) -> tuple[Any, bytes, dict[str, str]]:
    raw, headers = request_bytes(url)
    try:
        return json.loads(raw), raw, headers
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Non-JSON response from {url}: {raw[:300]!r}") from exc


def write_bytes(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def write_jsonl(path: Path, rows: Iterable[Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True))
            handle.write("\n")


def write_csv(path: Path, rows: list[dict[str, Any]], fields: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    # UTF-8 BOM keeps non-ASCII titles/names readable in desktop Excel.
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def content_value(content: dict[str, Any], key: str, default: Any = None) -> Any:
    value = content.get(key, default)
    if isinstance(value, dict) and "value" in value:
        return value.get("value", default)
    return value


def forum_id_from_url(url: str | None) -> str:
    if not url:
        return ""
    try:
        return parse_qs(urlparse(url).query).get("id", [""])[0]
    except Exception:
        match = re.search(r"[?&]id=([^&]+)", url)
        return match.group(1) if match else ""


def absolute_url(base: str, url: str | None) -> str:
    if not url:
        return ""
    if url.startswith("http://") or url.startswith("https://"):
        return url
    return base.rstrip("/") + "/" + url.lstrip("/")


def clean_normalized_text(value: str) -> str:
    """Decode HTML entities and remove invisible characters invalid in XLSX XML."""
    value = html.unescape(value)
    return re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\ufffe\uffff]", "", value)


def fetch_openreview_accepted(
    raw_dir: Path,
    *,
    page_size: int = 1000,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Fetch the exact current accepted set via OpenReview API v2 search."""
    notes: list[dict[str, Any]] = []
    page_meta: list[dict[str, Any]] = []
    offset = 0
    reported_count: int | None = None

    while reported_count is None or offset < reported_count:
        params = {
            "term": VENUE_ID,
            "content": "venueid",
            "type": "exact",
            "venueid": VENUE_ID,
            "limit": page_size,
            "offset": offset,
            "count": "true",
            "sort": "tmdate:asc",
        }
        url = OPENREVIEW_SEARCH_URL + "?" + urlencode(params)
        payload, raw, headers = request_json(url)
        batch = payload.get("notes", [])
        if reported_count is None:
            reported_count = int(payload.get("count", 0))
        page_path = raw_dir / f"openreview_search_offset_{offset:05d}.json"
        write_bytes(page_path, raw)
        page_meta.append(
            {
                **headers,
                "offset": offset,
                "returned": len(batch),
                "reported_count": reported_count,
                "sha256": sha256_bytes(raw),
                "saved_as": str(page_path),
            }
        )
        if not batch:
            break
        notes.extend(batch)
        offset += len(batch)

    # De-duplicate defensively in case the live index changed during paging.
    by_id = {note["id"]: note for note in notes if note.get("id")}
    notes = sorted(
        by_id.values(),
        key=lambda n: (n.get("number") is None, n.get("number", 0), n.get("id", "")),
    )
    if reported_count is not None and len(notes) != reported_count:
        raise RuntimeError(
            "OpenReview pagination was not stable: "
            f"reported count={reported_count}, unique notes={len(notes)}. "
            "Re-run to obtain a consistent snapshot."
        )
    bad_venueids = [
        note.get("id", "")
        for note in notes
        if content_value(note.get("content", {}), "venueid") != VENUE_ID
    ]
    if bad_venueids:
        raise RuntimeError(
            f"OpenReview exact search returned {len(bad_venueids)} non-matching venueids"
        )
    return notes, page_meta


def fetch_virtual_sources(
    raw_dir: Path,
) -> tuple[dict[str, Any], dict[str, str], list[dict[str, Any]]]:
    meta, meta_raw, meta_headers = request_json(VIRTUAL_META_URL)
    abstracts, abstracts_raw, abstracts_headers = request_json(VIRTUAL_ABSTRACTS_URL)
    meta_path = raw_dir / "iclr-2026-orals-posters.json"
    abstracts_path = raw_dir / "iclr-2026-abstracts.json"
    write_bytes(meta_path, meta_raw)
    write_bytes(abstracts_path, abstracts_raw)
    source_meta = [
        {
            **meta_headers,
            "sha256": sha256_bytes(meta_raw),
            "saved_as": str(meta_path),
        },
        {
            **abstracts_headers,
            "sha256": sha256_bytes(abstracts_raw),
            "saved_as": str(abstracts_path),
        },
    ]
    return meta, abstracts, source_meta


def normalize_virtual(
    payload: dict[str, Any], abstracts: dict[str, str]
) -> tuple[dict[str, dict[str, Any]], dict[str, Any]]:
    results = payload.get("results", [])
    if payload.get("count") != len(results):
        raise RuntimeError(
            f"ICLR Virtual count mismatch: count={payload.get('count')}, "
            f"results={len(results)}"
        )

    paper_nodes = [r for r in results if r.get("eventtype") == "Poster"]
    main_nodes = [
        r
        for r in paper_nodes
        if r.get("event_type") == "Poster"
        and r.get("sourceurl") == VIRTUAL_MAIN_SOURCE_URL
    ]
    oral_events = {
        r.get("id"): r for r in results if r.get("eventtype") == "Oral"
    }

    by_forum: dict[str, dict[str, Any]] = {}
    for record in main_nodes:
        forum_id = forum_id_from_url(record.get("paper_url"))
        if not forum_id:
            raise RuntimeError(f"Virtual main record {record.get('id')} lacks forum id")
        if forum_id in by_forum:
            raise RuntimeError(f"Duplicate Virtual forum id: {forum_id}")
        related_orals = [
            oral_events[event_id]
            for event_id in record.get("related_events_ids", []) or []
            if event_id in oral_events
        ]
        enriched = dict(record)
        enriched["abstract"] = abstracts.get(str(record.get("id")), "")
        enriched["openreview_id"] = forum_id
        enriched["oral_events"] = related_orals
        by_forum[forum_id] = enriched

    track_counts = Counter(
        (r.get("sourceurl") or "<blank>") for r in paper_nodes
    )
    stats = {
        "payload_count": payload.get("count"),
        "result_count": len(results),
        "eventtype_counts": dict(Counter(r.get("eventtype") for r in results)),
        "paper_node_count": len(paper_nodes),
        "paper_node_source_counts": dict(track_counts),
        "main_conference_count": len(main_nodes),
        "main_decision_counts": dict(
            Counter(r.get("decision") for r in main_nodes)
        ),
        "main_unique_numeric_ids": len({r.get("id") for r in main_nodes}),
        "main_unique_forum_ids": len(by_forum),
        "abstract_keys": len(abstracts),
        "main_abstracts_nonempty": sum(
            bool(abstracts.get(str(r.get("id")), "")) for r in main_nodes
        ),
    }
    return by_forum, stats


def author_institutions_from_virtual(record: dict[str, Any] | None) -> list[str]:
    if not record:
        return []
    values: list[str] = []
    for author in record.get("authors", []) or []:
        institution = author.get("institution") if isinstance(author, dict) else None
        if isinstance(institution, dict):
            institution = institution.get("name") or institution.get("fullname")
        values.append(clean_normalized_text(str(institution or "")))
    return values


def normalize_openreview_note(
    note: dict[str, Any], virtual: dict[str, Any] | None
) -> tuple[dict[str, Any], dict[str, Any]]:
    content = note.get("content", {})
    forum_id = note.get("id") or note.get("forum") or ""
    venue = content_value(content, "venue", "") or ""
    if venue == "ICLR 2026 Oral":
        presentation = "Oral"
    elif venue == "ICLR 2026 Poster":
        presentation = "Poster"
    else:
        presentation = venue

    authors = content_value(content, "authors", []) or []
    authorids = content_value(content, "authorids", []) or []
    keywords = content_value(content, "keywords", []) or []
    if isinstance(authors, str):
        authors = [authors]
    if isinstance(authorids, str):
        authorids = [authorids]
    if isinstance(keywords, str):
        keywords = [keywords]

    pdf_path = content_value(content, "pdf", "") or ""
    related_orals = (virtual or {}).get("oral_events", []) or []
    virtual_authors = (virtual or {}).get("authors", []) or []
    virtual_author_urls = [
        absolute_url("https://iclr.cc", a.get("url", ""))
        for a in virtual_authors
        if isinstance(a, dict)
    ]
    institutions = author_institutions_from_virtual(virtual)
    oral_sessions = [r.get("session", "") for r in related_orals]
    oral_times = [r.get("starttime", "") for r in related_orals]

    csv_row = {
        "openreview_id": forum_id,
        "submission_number": note.get("number", ""),
        "title": content_value(content, "title", "") or "",
        "presentation_type": presentation,
        "venue": venue,
        "authors": "; ".join(str(v) for v in authors),
        "author_ids": "; ".join(str(v) for v in authorids),
        "author_institutions": "; ".join(institutions),
        "author_virtual_urls": "; ".join(virtual_author_urls),
        "primary_area": content_value(content, "primary_area", "") or "",
        "topic": (virtual or {}).get("topic", "") or "",
        "keywords": "; ".join(str(v) for v in keywords),
        "tldr": content_value(content, "TLDR", "")
        or content_value(content, "TL;DR", "")
        or "",
        "abstract": content_value(content, "abstract", "")
        or (virtual or {}).get("abstract", "")
        or "",
        "poster_session": (virtual or {}).get("session", "") or "",
        "poster_start": (virtual or {}).get("starttime", "") or "",
        "poster_end": (virtual or {}).get("endtime", "") or "",
        "oral_session": "; ".join(str(v) for v in oral_sessions if v),
        "oral_start": "; ".join(str(v) for v in oral_times if v),
        "poster_position": (virtual or {}).get("poster_position", "") or "",
        "openreview_url": f"https://openreview.net/forum?id={forum_id}",
        "pdf_url": absolute_url("https://openreview.net", pdf_path),
        "virtual_url": absolute_url(
            "https://iclr.cc", (virtual or {}).get("virtualsite_url", "")
        ),
        "bibtex": content_value(content, "_bibtex", "") or "",
        "license": note.get("license", "") or "",
        "venueid": content_value(content, "venueid", "") or "",
        "openreview_cdate_ms": note.get("cdate", ""),
        "openreview_mdate_ms": note.get("mdate", ""),
        "openreview_tmdate_ms": note.get("tmdate", ""),
        "virtual_event_id": (virtual or {}).get("id", ""),
        "virtual_source_id": (virtual or {}).get("sourceid", ""),
    }
    csv_row = {
        key: clean_normalized_text(value) if isinstance(value, str) else value
        for key, value in csv_row.items()
    }
    jsonl_row = {
        "normalized": csv_row,
        "openreview_note": note,
        "virtual_record": virtual,
    }
    return csv_row, jsonl_row


CSV_FIELDS = [
    "openreview_id",
    "submission_number",
    "title",
    "presentation_type",
    "venue",
    "authors",
    "author_ids",
    "author_institutions",
    "author_virtual_urls",
    "primary_area",
    "topic",
    "keywords",
    "tldr",
    "abstract",
    "poster_session",
    "poster_start",
    "poster_end",
    "oral_session",
    "oral_start",
    "poster_position",
    "openreview_url",
    "pdf_url",
    "virtual_url",
    "bibtex",
    "license",
    "venueid",
    "openreview_cdate_ms",
    "openreview_mdate_ms",
    "openreview_tmdate_ms",
    "virtual_event_id",
    "virtual_source_id",
]


def build_outputs(output_dir: Path) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    raw_dir = output_dir / "raw"
    fetched_at = utc_now()

    virtual_payload, virtual_abstracts, virtual_sources = fetch_virtual_sources(raw_dir)
    virtual_by_forum, virtual_stats = normalize_virtual(
        virtual_payload, virtual_abstracts
    )
    openreview_notes, openreview_sources = fetch_openreview_accepted(raw_dir)

    openreview_ids = {n.get("id") for n in openreview_notes if n.get("id")}
    virtual_ids = set(virtual_by_forum)
    only_openreview = sorted(openreview_ids - virtual_ids)
    only_virtual = sorted(virtual_ids - openreview_ids)
    overlap = openreview_ids & virtual_ids

    csv_rows: list[dict[str, Any]] = []
    jsonl_rows: list[dict[str, Any]] = []
    for note in openreview_notes:
        row, rich = normalize_openreview_note(
            note, virtual_by_forum.get(note.get("id", ""))
        )
        csv_rows.append(row)
        jsonl_rows.append(rich)

    accepted_csv = output_dir / "ICLR2026_accepted_papers.csv"
    accepted_jsonl = output_dir / "ICLR2026_accepted_papers.jsonl"
    write_csv(accepted_csv, csv_rows, CSV_FIELDS)
    write_jsonl(accepted_jsonl, jsonl_rows)

    virtual_only_rows = []
    for forum_id in only_virtual:
        record = virtual_by_forum[forum_id]
        virtual_only_rows.append(
            {
                "openreview_id": forum_id,
                "title": record.get("name", ""),
                "virtual_decision": record.get("decision", ""),
                "paper_url": record.get("paper_url", ""),
                "virtual_url": absolute_url(
                    "https://iclr.cc", record.get("virtualsite_url", "")
                ),
                "note": (
                    "Present in ICLR Virtual main-conference snapshot but no longer "
                    "has accepted venueid in current OpenReview search"
                ),
            }
        )
    diff_csv = output_dir / "ICLR2026_source_differences.csv"
    write_csv(
        diff_csv,
        virtual_only_rows,
        [
            "openreview_id",
            "title",
            "virtual_decision",
            "paper_url",
            "virtual_url",
            "note",
        ],
    )

    type_counts = Counter(row["presentation_type"] for row in csv_rows)
    missing = {
        "title": sum(not row["title"] for row in csv_rows),
        "authors": sum(not row["authors"] for row in csv_rows),
        "abstract": sum(not row["abstract"] for row in csv_rows),
        "keywords": sum(not row["keywords"] for row in csv_rows),
        "pdf_url": sum(not row["pdf_url"] for row in csv_rows),
        "virtual_match": sum(not row["virtual_event_id"] for row in csv_rows),
    }
    current_counts = {
        "openreview_current_accepted": len(openreview_notes),
        "openreview_presentation_counts": dict(type_counts),
        "virtual_main_conference": virtual_stats["main_conference_count"],
        "source_overlap": len(overlap),
        "only_openreview": len(only_openreview),
        "only_virtual": len(only_virtual),
    }
    warnings = []
    if len(openreview_notes) != REFERENCE_COUNTS["openreview_current_accepted"]:
        warnings.append(
            "OpenReview accepted count has changed since the 2026-07-10 reference "
            f"snapshot ({REFERENCE_COUNTS['openreview_current_accepted']})."
        )
    if virtual_stats["main_conference_count"] != REFERENCE_COUNTS[
        "virtual_main_conference"
    ]:
        warnings.append(
            "ICLR Virtual main-conference count has changed since the 2026-07-10 "
            f"reference snapshot ({REFERENCE_COUNTS['virtual_main_conference']})."
        )
    if only_openreview:
        warnings.append(
            f"{len(only_openreview)} current OpenReview papers lack Virtual metadata."
        )
    if only_virtual:
        warnings.append(
            f"{len(only_virtual)} Virtual-program papers are not in the current "
            "OpenReview accepted set; see ICLR2026_source_differences.csv."
        )

    manifest = {
        "dataset": "ICLR 2026 current accepted papers",
        "fetched_at_utc": fetched_at,
        "canonical_membership_rule": (
            "OpenReview Note content.venueid.value == " + VENUE_ID
        ),
        "canonical_membership_endpoint": OPENREVIEW_SEARCH_URL,
        "virtual_enrichment_filter": {
            "eventtype": "Poster",
            "event_type": "Poster",
            "sourceurl": VIRTUAL_MAIN_SOURCE_URL,
        },
        "counts": current_counts,
        "virtual_stats": virtual_stats,
        "missing_field_counts": missing,
        "reference_counts_as_of_2026_07_10": REFERENCE_COUNTS,
        "warnings": warnings,
        "source_differences": {
            "only_openreview_ids": only_openreview,
            "only_virtual_ids": only_virtual,
        },
        "sources": virtual_sources + openreview_sources,
        "outputs": {
            "csv": str(accepted_csv),
            "jsonl": str(accepted_jsonl),
            "source_differences_csv": str(diff_csv),
        },
    }
    manifest_path = output_dir / "source_manifest.json"
    write_json(manifest_path, manifest)
    return manifest


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Fetch and reconcile current ICLR 2026 accepted papers."
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path(__file__).resolve().parent.parent / "output",
        help="Directory for raw snapshots and normalized outputs.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    manifest = build_outputs(args.output_dir.resolve())
    print(json.dumps({
        "output_dir": str(args.output_dir.resolve()),
        "counts": manifest["counts"],
        "missing_field_counts": manifest["missing_field_counts"],
        "warnings": manifest["warnings"],
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
