#!/usr/bin/env python3
"""Query collected conference papers with paperCrawler-style Boolean title search.

Edit ``DEFAULT_TARGETS`` and ``DEFAULT_QUERIES`` near the top, then run:

    python query_target_papers.py

For every conference/year pair, the program reuses the complete local snapshot
created by ``fetch_openreview_accepted.py`` when it is valid. Set
``REFRESH_EXISTING`` to ``True`` only when a fresh source download is needed.

Query syntax
------------
Adjacent terms imply AND.  ``and``, ``or``, ``not`` and parentheses are
case-insensitive; quoted phrases require adjacent title words; ``*`` is a
word-level wildcard.  For example:

    "time series" and (anomal* or outlier*) and not forecasting

Bare words retain this project's useful stem matching, so the editable default
``time serie anomal detect`` also matches ``Time-Series Anomaly Detection``.
"""

from __future__ import annotations

import csv
import json
import re
import unicodedata
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    from . import fetch_openreview_accepted as crawler
except ImportError:  # pragma: no cover
    import fetch_openreview_accepted as crawler


# ---------------------------------------------------------------------------
# User-editable settings
# ---------------------------------------------------------------------------
# Supported values include ICLR, ICML, NIPS/NEURIPS, AAAI, ACL, CVPR, ICCV,
# WWW, RTSS, SIGKDD/KDD, ICDE, and VLDB.
DEFAULT_TARGETS = (
    {"conference": "ICML", "year": 2026},
    {"conference": "ICLR", "year": 2026},
    {"conference": "NIPS", "year": 2025},
    {"conference": "ICML", "year": 2025},
    {"conference": "ICLR", "year": 2025},
    {"conference": "AAAI", "year": 2026},
    {"conference": "KDD", "year": 2026},
    {"conference": "WWW", "year": 2026},
)

# One query per item. Words do not need to be consecutive or in the same order
# in a paper title. Partial word stems are supported: this default query matches
# titles such as "Time Series Anomaly Detection".
DEFAULT_QUERIES = (
    "time serie anomal detect",
)

# Reuse the same root as the accepted-paper collector.
PROJECT_ROOT = Path(__file__).resolve().parent.parent
SNAPSHOT_OUTPUT_ROOT = PROJECT_ROOT / "output"

# Keyword-query CSVs and manifests are written here.
QUERY_OUTPUT_DIR = PROJECT_ROOT / "keyword_query_results"

# OpenReview limits this endpoint to 1,000 notes per page.
PAGE_SIZE = 1000

# Set True only when you intentionally want a fresh network snapshot.
REFRESH_EXISTING = False

# Set False to skip optional ICLR Virtual metadata enrichment on a fresh fetch.
USE_ICLR_VIRTUAL = True


RESULT_FIELDS = [
    "query",
    "query_tokens",
    "matched_title_tokens",
    *crawler.CSV_FIELDS,
]

SUMMARY_FIELDS = [
    "conference",
    "year",
    "query",
    "match_count",
    "collection_status",
    "accepted_paper_count",
    "error",
]


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def tokenize(text: str) -> list[str]:
    """Normalize text into Unicode-insensitive alphanumeric word tokens."""
    normalized = unicodedata.normalize("NFKD", text.casefold())
    ascii_like = "".join(
        character
        for character in normalized
        if not unicodedata.combining(character)
    )
    return re.findall(r"[a-z0-9]+", ascii_like)


def common_prefix_length(left: str, right: str) -> int:
    """Return the number of leading characters shared by two normalized words."""
    length = 0
    for left_character, right_character in zip(left, right):
        if left_character != right_character:
            break
        length += 1
    return length


def token_matches(query_token: str, title_token: str) -> bool:
    """Match exact words, prefixes, and simple inflectional variants.

    A query stem such as ``anomal`` matches ``anomaly``/``anomalies``; a full
    word such as ``anomaly`` also matches the plural because the first five
    characters are shared.  Very short tokens remain exact/prefix-only to avoid
    broad accidental matches.
    """
    if title_token.startswith(query_token):
        return True
    # Do not let a short title word such as "for" satisfy "forecasting".
    if len(title_token) >= 5 and query_token.startswith(title_token):
        return True
    minimum_shared_prefix = min(5, len(query_token), len(title_token))
    return (
        minimum_shared_prefix >= 5
        and common_prefix_length(query_token, title_token) >= minimum_shared_prefix
    )


class QuerySyntaxError(ValueError):
    """A user-editable Boolean query could not be parsed."""


def lex_query(query: str) -> list[tuple[str, str]]:
    """Tokenize a Boolean title query without requiring third-party packages."""
    tokens: list[tuple[str, str]] = []
    index = 0
    while index < len(query):
        character = query[index]
        if character.isspace():
            index += 1
            continue
        if character in "()":
            tokens.append((character, character))
            index += 1
            continue
        if character in "\"'":
            quote = character
            end = query.find(quote, index + 1)
            if end < 0:
                raise QuerySyntaxError("unclosed quoted phrase")
            phrase = query[index + 1 : end]
            if not tokenize(phrase):
                raise QuerySyntaxError("quoted phrase must contain a word")
            tokens.append(("PHRASE", phrase))
            index = end + 1
            continue
        end = index
        while end < len(query) and not query[end].isspace() and query[end] not in "()\"'":
            end += 1
        word = query[index:end]
        normalized = word.casefold()
        if normalized in {"and", "or", "not"}:
            tokens.append((normalized.upper(), normalized))
        elif tokenize(word.replace("*", "")):
            tokens.append(("TERM", word))
        else:
            raise QuerySyntaxError(f"invalid query token: {word!r}")
        index = end
    return tokens


class BooleanTitleQuery:
    """Recursive-descent parser for the small Boolean grammar used by paperCrawler."""

    def __init__(self, query: str):
        self.query = query
        self.tokens = lex_query(query)
        self.position = 0
        if not self.tokens:
            raise QuerySyntaxError("query must contain at least one search term")
        self.tree = self.parse_or()
        if self.position != len(self.tokens):
            token = self.tokens[self.position][1]
            raise QuerySyntaxError(f"unexpected token: {token!r}")

    def peek(self) -> str | None:
        return self.tokens[self.position][0] if self.position < len(self.tokens) else None

    def accept(self, kind: str) -> bool:
        if self.peek() != kind:
            return False
        self.position += 1
        return True

    def parse_or(self) -> tuple[Any, ...]:
        node = self.parse_and()
        while self.accept("OR"):
            node = ("or", node, self.parse_and())
        return node

    def parse_and(self) -> tuple[Any, ...]:
        node = self.parse_not()
        while True:
            if self.accept("AND"):
                node = ("and", node, self.parse_not())
            elif self.peek() in {"TERM", "PHRASE", "NOT", "("}:
                # Adjacent terms/phrases/groups imply AND, as in paperCrawler.
                node = ("and", node, self.parse_not())
            else:
                return node

    def parse_not(self) -> tuple[Any, ...]:
        if self.accept("NOT"):
            return ("not", self.parse_not())
        return self.parse_primary()

    def parse_primary(self) -> tuple[Any, ...]:
        if self.accept("("):
            node = self.parse_or()
            if not self.accept(")"):
                raise QuerySyntaxError("missing closing parenthesis")
            return node
        if self.peek() in {"TERM", "PHRASE"}:
            kind, value = self.tokens[self.position]
            self.position += 1
            return ("phrase" if kind == "PHRASE" else "term", value)
        token = self.tokens[self.position][1] if self.position < len(self.tokens) else "end of query"
        raise QuerySyntaxError(f"expected a term, phrase, or parenthesized group; got {token!r}")

    def terms(self) -> list[str]:
        def visit(node: tuple[Any, ...]) -> list[str]:
            if node[0] in {"term", "phrase"}:
                return [str(node[1])]
            if node[0] == "not":
                return ["NOT", *visit(node[1])]
            return [*visit(node[1]), node[0].upper(), *visit(node[2])]

        return visit(self.tree)


def wildcard_matches(query_term: str, title_token: str) -> bool:
    normalized = "".join(tokenize(query_term.replace("*", "")))
    if "*" not in query_term:
        return token_matches(normalized, title_token)
    pattern = re.escape(query_term.casefold()).replace(r"\*", ".*")
    return bool(re.fullmatch(pattern, title_token))


def evaluate_query_tree(
    node: tuple[Any, ...], *, title_tokens: list[str], normalized_title: str
) -> list[tuple[str, str]] | None:
    kind = node[0]
    if kind == "term":
        query_term = str(node[1])
        matched = next(
            (token for token in title_tokens if wildcard_matches(query_term, token)),
            None,
        )
        return [(query_term, matched)] if matched is not None else None
    if kind == "phrase":
        phrase_tokens = tokenize(str(node[1]))
        phrase = " ".join(phrase_tokens)
        if phrase_tokens and any(title_tokens[index:index + len(phrase_tokens)] == phrase_tokens
                                 for index in range(len(title_tokens) - len(phrase_tokens) + 1)):
            return [(str(node[1]), phrase)]
        return None
    if kind == "not":
        return [] if evaluate_query_tree(node[1], title_tokens=title_tokens, normalized_title=normalized_title) is None else None
    left = evaluate_query_tree(node[1], title_tokens=title_tokens, normalized_title=normalized_title)
    right = evaluate_query_tree(node[2], title_tokens=title_tokens, normalized_title=normalized_title)
    if kind == "and":
        return None if left is None or right is None else [*left, *right]
    if kind == "or":
        return left if left is not None else right
    raise RuntimeError(f"unknown Boolean query node: {kind}")


def parse_query(query: str) -> BooleanTitleQuery:
    """Parse once per query; raises a readable error for invalid expressions."""
    return BooleanTitleQuery(query)


def match_title(title: str, query: str | BooleanTitleQuery) -> list[tuple[str, str]] | None:
    """Evaluate a Boolean title query and return positive-match diagnostics."""
    parsed = parse_query(query) if isinstance(query, str) else query
    title_tokens = tokenize(title)
    if not title_tokens:
        return None
    return evaluate_query_tree(
        parsed.tree,
        title_tokens=title_tokens,
        normalized_title=" ".join(title_tokens),
    )


def normalize_target(target: dict[str, object]) -> tuple[str, int]:
    """Validate one user-editable target using the collector's canonical rules."""
    conference = crawler.normalize_conference_argument(str(target["conference"]))
    year = crawler.valid_year(str(target["year"]))
    if conference == "ALL":
        raise ValueError("DEFAULT_TARGETS must use one concrete conference per row")
    return conference, year


def result_row(
    paper: dict[str, Any],
    *,
    query: str,
    parsed_query: BooleanTitleQuery,
    matches: list[tuple[str, str]],
) -> dict[str, Any]:
    """Add query diagnostics to one normalized accepted-paper row."""
    return {
        "query": query,
        "query_tokens": "; ".join(parsed_query.terms()),
        "matched_title_tokens": "; ".join(
            f"{query_token}->{title_token}"
            for query_token, title_token in matches
        ),
        **paper,
    }


def collect_query_results(
    *,
    targets: tuple[dict[str, object], ...] = DEFAULT_TARGETS,
    queries: tuple[str, ...] = DEFAULT_QUERIES,
    snapshot_output_root: Path = SNAPSHOT_OUTPUT_ROOT,
    refresh_existing: bool = REFRESH_EXISTING,
    use_iclr_virtual: bool = USE_ICLR_VIRTUAL,
    page_size: int = PAGE_SIZE,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, str]]:
    """Fetch/reuse targets and return matching rows, summaries, and failures."""
    parsed_queries: list[tuple[str, BooleanTitleQuery]] = []
    for query in queries:
        if not query.strip():
            continue
        try:
            parsed_queries.append((query, parse_query(query)))
        except QuerySyntaxError as exc:
            raise ValueError(f"Invalid title query {query!r}: {exc}") from exc
    if not parsed_queries:
        raise ValueError("DEFAULT_QUERIES must contain at least one non-empty query")

    rows: list[dict[str, Any]] = []
    summaries: list[dict[str, Any]] = []
    failures: dict[str, str] = {}

    for target in targets:
        conference, year = normalize_target(target)
        target_key = f"{conference}-{year}"
        spec = crawler.CONFERENCE_SPECS[conference]
        try:
            manifest, csv_rows, _ = crawler.build_conference_outputs(
                snapshot_output_root.resolve(),
                spec=spec,
                year=year,
                page_size=page_size,
                use_iclr_virtual=use_iclr_virtual,
                refresh=refresh_existing,
                include_rows=True,
            )
        except RuntimeError as exc:
            failures[target_key] = str(exc)
            for query, _ in parsed_queries:
                summaries.append(
                    {
                        "conference": conference,
                        "year": year,
                        "query": query,
                        "match_count": 0,
                        "collection_status": "failed",
                        "accepted_paper_count": 0,
                        "error": str(exc),
                    }
                )
            continue

        for query, parsed_query in parsed_queries:
            query_matches = 0
            for paper in csv_rows:
                matches = match_title(str(paper.get("title", "")), parsed_query)
                if matches is None:
                    continue
                rows.append(
                    result_row(
                        paper,
                        query=query,
                        parsed_query=parsed_query,
                        matches=matches,
                    )
                )
                query_matches += 1
            summaries.append(
                {
                    "conference": conference,
                    "year": year,
                    "query": query,
                    "match_count": query_matches,
                    "collection_status": manifest.get("collection_status", ""),
                    "accepted_paper_count": manifest.get("counts", {}).get(
                        "accepted_paper_count",
                        manifest.get("counts", {}).get(
                            "openreview_current_accepted", 0
                        ),
                    ),
                    "error": "",
                }
            )

    rows.sort(
        key=lambda row: (
            row.get("query", ""),
            row.get("conference", ""),
            row.get("year", 0),
            crawler.submission_sort_key(row.get("submission_number")),
            row.get("openreview_id", "") or row.get("source_record_id", ""),
        )
    )
    return rows, summaries, failures


def write_query_outputs(
    *,
    output_dir: Path,
    rows: list[dict[str, Any]],
    summaries: list[dict[str, Any]],
    failures: dict[str, str],
) -> dict[str, str]:
    """Write the row-level matches, per-target counts, and audit manifest."""
    output_dir.mkdir(parents=True, exist_ok=True)
    results_path = output_dir / "title_keyword_matches.csv"
    summary_path = output_dir / "title_keyword_summary.csv"
    manifest_path = output_dir / "title_keyword_manifest.json"
    crawler.write_csv(results_path, rows, RESULT_FIELDS)
    crawler.write_csv(summary_path, summaries, SUMMARY_FIELDS)
    crawler.write_json(
        manifest_path,
        {
            "created_at_utc": utc_now(),
            "targets": DEFAULT_TARGETS,
            "queries": DEFAULT_QUERIES,
            "title_matching": (
                "Boolean title expressions support implicit AND, AND/OR/NOT, "
                "parentheses, quoted phrases, and * wildcards. Bare words use "
                "case-insensitive stem/inflection matching."
            ),
            "match_count": len(rows),
            "failures": failures,
            "outputs": {
                "matches_csv": str(results_path),
                "summary_csv": str(summary_path),
            },
        },
    )
    return {
        "matches_csv": str(results_path),
        "summary_csv": str(summary_path),
        "manifest": str(manifest_path),
    }


def main() -> int:
    rows, summaries, failures = collect_query_results()
    outputs = write_query_outputs(
        output_dir=QUERY_OUTPUT_DIR.resolve(),
        rows=rows,
        summaries=summaries,
        failures=failures,
    )
    print(
        json.dumps(
            {
                "targets": DEFAULT_TARGETS,
                "queries": DEFAULT_QUERIES,
                "match_count": len(rows),
                "summary": summaries,
                "failures": failures,
                "outputs": outputs,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
