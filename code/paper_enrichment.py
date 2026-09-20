"""Enrich selected paper rows with abstracts and public PDF downloads.

The collector remains responsible for conference membership and normalized
metadata.  This module operates on selected rows from the local CSV snapshots,
so enrichment never changes the canonical accepted-paper collection.
"""

from __future__ import annotations

import difflib
import hashlib
import json
import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from . import fetch_openreview_accepted as collector


SEMANTIC_SCHOLAR_SEARCH_URL = (
    "https://api.semanticscholar.org/graph/v1/paper/search"
)
USER_AGENT = "Search4PaperEnrichment/1.0 (public scholarly metadata)"
DOWNLOAD_CHUNK_SIZE = 1024 * 256
MAX_PDF_BYTES = 100 * 1024 * 1024


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _as_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, dict) and "value" in value:
        return _as_text(value.get("value"))
    return str(value).strip()


def normalize_title(value: str) -> str:
    value = value.casefold()
    value = re.sub(r"[^\w\s]", " ", value, flags=re.UNICODE)
    return re.sub(r"\s+", " ", value).strip()


def paper_key(row: dict[str, Any]) -> str:
    """Return a stable filesystem-safe identity for one normalized paper row."""
    conference = _as_text(row.get("conference") or "paper")
    year = _as_text(row.get("year") or "unknown")
    identity = _as_text(
        row.get("openreview_id")
        or row.get("source_record_id")
        or row.get("doi")
        or row.get("title")
        or "unknown"
    )
    value = f"{conference}_{year}_{identity}"
    value = re.sub(r"[^A-Za-z0-9._-]+", "_", value).strip("._")
    return value[:180] or "paper_unknown"


def _enrichment_root(output_root: Path) -> Path:
    return output_root / "enrichment"


def _read_jsonl_map(path: Path, *, strict: bool = False) -> dict[str, dict[str, Any]]:
    if not path.is_file():
        return {}
    result: dict[str, dict[str, Any]] = {}
    try:
        with path.open("r", encoding="utf-8") as handle:
            for line in handle:
                if not line.strip():
                    continue
                value = json.loads(line)
                if isinstance(value, dict) and value.get("paper_id"):
                    result[str(value["paper_id"])] = value
                elif strict:
                    raise ValueError(f"Invalid enrichment record in {path}: missing paper_id")
    except (OSError, json.JSONDecodeError) as exc:
        if strict:
            raise ValueError(f"Cannot read enrichment cache {path}: {exc}") from exc
        return {}
    return result


def load_cached_abstracts(
    rows: Iterable[dict[str, Any]], output_root: Path
) -> list[dict[str, Any]]:
    """Fill missing abstracts from existing cache only; never fetch or write."""
    cache = _read_jsonl_map(_enrichment_root(output_root) / "abstracts.jsonl", strict=True)
    result = []
    for source in rows:
        row = dict(source)
        cached = cache.get(paper_key(row), {})
        if not _as_text(row.get("abstract")) and cached.get("status") == "success" and _as_text(cached.get("abstract")):
            row["abstract"] = cached["abstract"]
            row["abstract_status"] = "success"
            for name in ("source", "source_url", "retrieved_at", "match_confidence", "matched_title"):
                row[f"abstract_{name}"] = cached.get(name, "")
        result.append(row)
    return result


def _write_jsonl_map(path: Path, values: dict[str, dict[str, Any]]) -> None:
    collector.write_jsonl(path, [values[key] for key in sorted(values)])


def _abstract_from_openreview(payload: Any) -> str:
    notes = payload.get("notes", []) if isinstance(payload, dict) else []
    if not isinstance(notes, list) or not notes:
        return ""
    note = notes[0] if isinstance(notes[0], dict) else {}
    content = note.get("content", {})
    if not isinstance(content, dict):
        return ""
    for key in ("abstract", "Abstract"):
        value = content.get(key, "")
        text = _as_text(value)
        if text:
            return text
    return ""


def _fetch_openreview_abstract(openreview_id: str) -> tuple[str, str]:
    url = "https://api2.openreview.net/notes?" + urlencode({"id": openreview_id})
    payload, _raw, _headers = collector.request_json(url)
    abstract = _abstract_from_openreview(payload)
    return abstract, url


def _author_tokens(value: str) -> set[str]:
    tokens: set[str] = set()
    for author in value.split(";"):
        words = re.findall(r"[a-z0-9]+", author.casefold())
        if words:
            tokens.add(words[-1])
    return tokens


def _semantic_scholar_abstract(
    row: dict[str, Any],
) -> tuple[str, str, float, str] | None:
    title = _as_text(row.get("title"))
    if not title:
        return None
    params = urlencode(
        {
            "query": title,
            "limit": "5",
            "fields": "title,abstract,authors,year,externalIds,url",
        }
    )
    url = f"{SEMANTIC_SCHOLAR_SEARCH_URL}?{params}"
    payload, _raw, _headers = collector.request_json(url)
    candidates = payload.get("data", []) if isinstance(payload, dict) else []
    if not isinstance(candidates, list):
        return None

    wanted_title = normalize_title(title)
    wanted_year = _as_text(row.get("year"))
    wanted_authors = _author_tokens(_as_text(row.get("authors")))
    ranked: list[tuple[float, dict[str, Any]]] = []
    for candidate in candidates:
        if not isinstance(candidate, dict) or not _as_text(candidate.get("abstract")):
            continue
        candidate_title = _as_text(candidate.get("title"))
        normalized_candidate = normalize_title(candidate_title)
        if not normalized_candidate:
            continue
        title_score = difflib.SequenceMatcher(
            None, wanted_title, normalized_candidate
        ).ratio()
        token_left = set(wanted_title.split())
        token_right = set(normalized_candidate.split())
        union = token_left | token_right
        token_score = len(token_left & token_right) / len(union) if union else 0.0
        score = max(title_score, token_score)
        if normalized_candidate == wanted_title:
            score = 1.0
        candidate_year = _as_text(candidate.get("year"))
        if wanted_year and candidate_year and wanted_year != candidate_year:
            score -= 0.08
        candidate_authors = candidate.get("authors", [])
        candidate_author_text = ";".join(
            _as_text(author.get("name"))
            for author in candidate_authors
            if isinstance(author, dict)
        )
        if wanted_authors and _author_tokens(candidate_author_text) & wanted_authors:
            score += 0.03
        ranked.append((score, candidate))

    if not ranked:
        return None
    score, candidate = max(ranked, key=lambda item: item[0])
    if score < 0.92:
        return None
    return (
        _as_text(candidate.get("abstract")),
        url,
        round(min(score, 1.0), 4),
        _as_text(candidate.get("title")),
    )


def enrich_abstracts(
    rows: Iterable[dict[str, Any]],
    output_root: Path,
    *,
    refresh: bool = False,
    use_external_source: bool = True,
) -> tuple[list[dict[str, Any]], dict[str, str]]:
    """Fill missing abstracts for selected rows and persist a reusable cache."""
    cache_path = _enrichment_root(output_root) / "abstracts.jsonl"
    cache = _read_jsonl_map(cache_path)
    enriched_rows: list[dict[str, Any]] = []
    failures: dict[str, str] = {}

    for source_row in rows:
        row = dict(source_row)
        key = paper_key(row)
        cached = cache.get(key)
        if cached and cached.get("status") == "success" and not refresh:
            result = cached
        else:
            abstract = _as_text(row.get("abstract"))
            result: dict[str, Any] = {
                "paper_id": key,
                "status": "success" if abstract else "not_found",
                "abstract": abstract,
                "source": "local_dataset" if abstract else "",
                "source_url": _as_text(row.get("source_url")),
                "retrieved_at": utc_now(),
                "match_confidence": 1.0 if abstract else 0.0,
                "matched_title": _as_text(row.get("title")) if abstract else "",
                "error": "",
            }
            if not abstract and _as_text(row.get("openreview_id")):
                try:
                    abstract, source_url = _fetch_openreview_abstract(
                        _as_text(row["openreview_id"])
                    )
                    if abstract:
                        result.update(
                            status="success",
                            abstract=abstract,
                            source="openreview",
                            source_url=source_url,
                            match_confidence=1.0,
                            matched_title=_as_text(row.get("title")),
                        )
                except RuntimeError as exc:
                    result["error"] = str(exc)
            if not abstract and use_external_source:
                try:
                    match = _semantic_scholar_abstract(row)
                    if match:
                        abstract, source_url, confidence, matched_title = match
                        result.update(
                            status="success",
                            abstract=abstract,
                            source="semantic_scholar",
                            source_url=source_url,
                            match_confidence=confidence,
                            matched_title=matched_title,
                            error="",
                        )
                except RuntimeError as exc:
                    result["error"] = str(exc)
            if not result["abstract"]:
                result["status"] = "not_found"
                failures[key] = result.get("error") or "No abstract source matched"
            cache[key] = result

        row["paper_id"] = key
        row["abstract"] = result.get("abstract", "")
        row["abstract_status"] = result.get("status", "not_found")
        row["abstract_source"] = result.get("source", "")
        row["abstract_source_url"] = result.get("source_url", "")
        row["abstract_retrieved_at"] = result.get("retrieved_at", "")
        row["abstract_match_confidence"] = result.get("match_confidence", 0.0)
        row["abstract_matched_title"] = result.get("matched_title", "")
        row["abstract_error"] = result.get("error", "")
        enriched_rows.append(row)

    _write_jsonl_map(cache_path, cache)
    return enriched_rows, failures


def resolve_pdf_url(row: dict[str, Any]) -> str:
    for field in ("pdf_url", "paper_url"):
        value = _as_text(row.get(field))
        if value.casefold().startswith(("http://", "https://")):
            if field == "pdf_url" or value.casefold().split("?", 1)[0].endswith(".pdf"):
                return value
    openreview_id = _as_text(row.get("openreview_id"))
    if openreview_id:
        return "https://openreview.net/pdf?id=" + openreview_id
    return ""


def _download_one_pdf(url: str, target: Path) -> dict[str, Any]:
    last_error: Exception | None = None
    for attempt in range(3):
        temp = target.with_name(target.name + ".part")
        digest = hashlib.sha256()
        total = 0
        try:
            request = Request(
                url,
                headers={"User-Agent": USER_AGENT, "Accept": "application/pdf,*/*;q=0.5"},
            )
            with urlopen(request, timeout=120) as response:
                final_url = response.geturl()
                content_type = response.headers.get("Content-Type", "")
                with temp.open("wb") as handle:
                    while True:
                        chunk = response.read(DOWNLOAD_CHUNK_SIZE)
                        if not chunk:
                            break
                        total += len(chunk)
                        if total > MAX_PDF_BYTES:
                            raise RuntimeError("PDF exceeds 100 MB safety limit")
                        digest.update(chunk)
                        handle.write(chunk)
            if total < 5:
                raise RuntimeError("Downloaded file is empty")
            with temp.open("rb") as handle:
                signature = handle.read(5)
            if signature != b"%PDF-" and "pdf" not in content_type.casefold():
                raise RuntimeError("Downloaded response is not a PDF")
            target.parent.mkdir(parents=True, exist_ok=True)
            os.replace(temp, target)
            return {
                "status": "downloaded",
                "requested_url": url,
                "final_url": final_url,
                "content_type": content_type,
                "file_size": total,
                "sha256": digest.hexdigest(),
                "local_path": str(target),
                "downloaded_at": utc_now(),
                "error": "",
            }
        except HTTPError as exc:
            last_error = exc
            if 400 <= exc.code < 500:
                break
        except (URLError, TimeoutError, OSError, RuntimeError) as exc:
            last_error = exc
        finally:
            if temp.exists():
                try:
                    temp.unlink()
                except OSError:
                    pass
        if attempt < 2:
            time.sleep(2**attempt)
    return {
        "status": "failed",
        "requested_url": url,
        "final_url": "",
        "content_type": "",
        "file_size": 0,
        "sha256": "",
        "local_path": "",
        "downloaded_at": utc_now(),
        "error": str(last_error) if last_error else "Unknown download error",
    }


def download_pdfs(
    rows: Iterable[dict[str, Any]],
    output_root: Path,
    *,
    refresh: bool = False,
) -> tuple[list[dict[str, Any]], dict[str, str]]:
    """Download selected public PDFs and persist a resumable manifest."""
    manifest_path = _enrichment_root(output_root) / "pdf_manifest.jsonl"
    manifest = _read_jsonl_map(manifest_path)
    results: list[dict[str, Any]] = []
    failures: dict[str, str] = {}
    pdf_dir = _enrichment_root(output_root) / "pdf"

    for source_row in rows:
        row = dict(source_row)
        key = paper_key(row)
        cached = manifest.get(key)
        target = pdf_dir / f"{key}.pdf"
        if cached and cached.get("status") == "downloaded" and target.is_file() and not refresh:
            result = cached
        else:
            url = resolve_pdf_url(row)
            if not url:
                result = {
                    "status": "no_pdf_url",
                    "requested_url": "",
                    "final_url": "",
                    "content_type": "",
                    "file_size": 0,
                    "sha256": "",
                    "local_path": "",
                    "downloaded_at": utc_now(),
                    "error": "No public PDF URL was found in the paper metadata",
                }
            else:
                result = _download_one_pdf(url, target)
            result["paper_id"] = key
            result["title"] = _as_text(row.get("title"))
            manifest[key] = result

        row["paper_id"] = key
        row["pdf_status"] = result.get("status", "")
        row["pdf_url_resolved"] = result.get("final_url") or result.get("requested_url", "")
        row["pdf_local_path"] = result.get("local_path", "")
        row["pdf_sha256"] = result.get("sha256", "")
        row["pdf_error"] = result.get("error", "")
        results.append(row)
        if result.get("status") != "downloaded":
            failures[key] = result.get("error") or result.get("status", "failed")

    _write_jsonl_map(manifest_path, manifest)
    return results, failures


def export_ai_jsonl(
    rows: Iterable[dict[str, Any]],
    output_path: Path,
    *,
    query: str = "",
) -> Path:
    """Export selected rows as compact, one-paper-per-line AI input records."""
    records: list[dict[str, Any]] = []
    for source_row in rows:
        row = dict(source_row)
        authors = [value.strip() for value in _as_text(row.get("authors")).split(";") if value.strip()]
        keywords = [value.strip() for value in _as_text(row.get("keywords")).split(";") if value.strip()]
        records.append(
            {
                "paper_id": paper_key(row),
                "conference": row.get("conference", ""),
                "conference_display_name": row.get("conference_display_name", ""),
                "year": row.get("year", ""),
                "title": row.get("title", ""),
                "authors": authors,
                "abstract": row.get("abstract", ""),
                "abstract_status": row.get("abstract_status", "available" if row.get("abstract") else "not_found"),
                "abstract_source": row.get("abstract_source", ""),
                "abstract_match_confidence": row.get("abstract_match_confidence", 0.0),
                "keywords": keywords,
                "tldr": row.get("tldr", ""),
                "areas": {
                    "primary": row.get("primary_area", ""),
                    "secondary": row.get("secondary_area", ""),
                    "topic": row.get("topic", ""),
                },
                "links": {
                    "source": row.get("source_url", ""),
                    "paper": row.get("paper_url", ""),
                    "pdf": row.get("pdf_url_resolved") or row.get("pdf_url", ""),
                    "openreview": row.get("openreview_url", ""),
                    "virtual": row.get("virtual_url", ""),
                },
                "local_files": {
                    "pdf_path": row.get("pdf_local_path", ""),
                    "pdf_sha256": row.get("pdf_sha256", ""),
                },
                "search": {"query": query,
                           "matched_query_groups": row.get("matched_query_groups", []),
                           "matched_terms": row.get("matched_terms", []),
                           "matched_fields": row.get("matched_fields", []),
                           "matched_snippets": row.get("matched_snippets", [])},
                "provenance": {
                    "source_type": row.get("source_type", ""),
                    "source_id": row.get("source_id", ""),
                    "source_record_id": row.get("source_record_id", ""),
                },
            }
        )
    collector.write_jsonl(output_path, records)
    return output_path
