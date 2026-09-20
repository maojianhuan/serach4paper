#!/usr/bin/env python3
"""Fetch conference papers from OpenReview or published proceedings.

Sources and membership rules
----------------------------
ICLR, ICML, NeurIPS and The Web Conference use OpenReview API v2
``/notes/search`` filtered by ``content.venueid`` exactly equal to the
conference venue ID.  Other venues first try a public official paper source
(for example ACL Anthology or CVF Open Access), then fall back to DBLP only
when that source is unavailable. DBLP output is bibliographic, not a live
decision list.

Supported conference arguments
------------------------------
All CCF seventh-edition A/B/C conferences and journals in ``ccf_venues.json``
are accepted, including historical aliases ``NEURIPS`` and ``KDD``. CLI ``ALL``
selects the entire catalogue; the GUI retains its six-core-venue shortcut.

Examples
--------
    python fetch_openreview_accepted.py --year 2026 --conference ICLR
    python fetch_openreview_accepted.py --year 2026 --conference NIPS
    python fetch_openreview_accepted.py --year 2026 --conference ALL

The script uses only Python's standard library. For every selected conference it
writes raw API snapshots, normalized CSV/JSONL files, and a provenance manifest.
When ``ALL`` is selected, it also writes a combined CSV/JSONL dataset.

ICLR virtual-program enrichment is best-effort. If the corresponding static JSON
files are unavailable for a year, OpenReview output is still generated.
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
import threading
from contextlib import contextmanager
from html.parser import HTMLParser
from http.cookiejar import CookieJar
import xml.etree.ElementTree as ET
from collections import Counter
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlencode, urljoin, urlparse
from urllib.request import Request, urlopen, build_opener, HTTPCookieProcessor


OPENREVIEW_SEARCH_URL = "https://api2.openreview.net/notes/search"


def application_root() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def bundled_resource_path(name: str) -> Path:
    """Locate a bundled resource in source and PyInstaller layouts."""
    module_dir = Path(__file__).resolve().parent
    bundle_root = Path(getattr(sys, "_MEIPASS", module_dir))
    candidates = (
        module_dir / name,
        module_dir / "code" / name,
        bundle_root / "code" / name,
        bundle_root / name,
    )
    return next((candidate for candidate in candidates if candidate.is_file()), candidates[0])


CCF_A_CATALOG_PATH = bundled_resource_path("ccf_a_conferences.json")
USER_AGENT = (
    "OpenReviewAcceptedPapersCollector/2.0 "
    "(public scholarly metadata; contact via local operator)"
)
NON_PAPER_DBLP_TITLE = re.compile(
    r"^(?:front matter|(?:a )?message from the chairs|preface|"
    r"table of contents|proceedings)\.?$",
    flags=re.IGNORECASE,
)


@dataclass(frozen=True)
class DblpJournalIssue:
    """One DBLP journal XML volume and the issues belonging to a conference."""

    url_template: str
    journal_volume_year_offset: int = 0
    issue_numbers: tuple[str, ...] = ()
    publication_years: tuple[int, ...] = ()
    publication_year_offsets: tuple[int, ...] = ()

    def url(self, year: int) -> str:
        return self.url_template.format(
            year=year,
            journal_volume=year + self.journal_volume_year_offset,
        )

    def allowed_publication_years(self, year: int) -> set[str]:
        if self.publication_years:
            return {str(value) for value in self.publication_years}
        return {str(year + offset) for offset in self.publication_year_offsets}


@dataclass(frozen=True)
class ConferenceSpec:
    """Configuration for one supported conference series."""

    key: str
    display_name: str
    venue_prefix: str = ""
    supports_iclr_virtual: bool = False
    source_kind: str = "openreview"
    requires_official_source: bool = False
    allow_dblp_fallback: bool = False
    dblp_collection: str = ""
    dblp_volume_prefix: str = ""
    dblp_volume_year_offset: int = 0
    dblp_volume_suffixes: tuple[int, ...] = ()
    dblp_journal_issues: tuple[DblpJournalIssue, ...] = ()
    dblp_excluded_title_patterns: tuple[str, ...] = ()
    dblp_min_page_count: int = 0
    official_adapter: str = ""
    official_url_templates: tuple[str, ...] = ()
    ccf_abbreviation: str = ""
    ccf_category: str = ""
    ccf_type: str = ""
    ccf_professional_field: str = ""
    issns: tuple[str, ...] = ()
    venue_filter: str = ""
    mapping_note: str = ""

    def venue_id(self, year: int) -> str:
        return f"{self.venue_prefix}/{year}/Conference"

    def group_url(self, year: int) -> str:
        return f"https://openreview.net/group?id={self.venue_id(year)}"

    def dblp_source_id(self, year: int) -> str:
        """Stable identifier for the configured DBLP proceedings volume(s)."""
        volume_number = year + self.dblp_volume_year_offset
        suffix = (
            "+".join(str(value) for value in self.dblp_volume_suffixes)
            if self.dblp_volume_suffixes
            else "all"
        )
        return (
            f"DBLP:{self.dblp_collection}/{self.dblp_volume_prefix}"
            f"{volume_number}:{suffix}"
        )

    def source_id(self, year: int) -> str:
        """Stable identifier used to validate a local conference snapshot."""
        if self.source_kind == "openreview":
            return self.venue_id(year)
        return self.dblp_source_id(year)

    def collection_id(self, year: int) -> str:
        """Identifier for the collection policy, independent of its fallback."""
        if self.source_kind == "openreview":
            return self.venue_id(year)
        return f"{self.key}:{year}:{self.source_kind}:v1"


CONFERENCE_SPECS: dict[str, ConferenceSpec] = {
    "ICLR": ConferenceSpec(
        key="ICLR",
        display_name="ICLR",
        venue_prefix="ICLR.cc",
        supports_iclr_virtual=True,
    ),
    "ICML": ConferenceSpec(
        key="ICML",
        display_name="ICML",
        venue_prefix="ICML.cc",
    ),
    "NIPS": ConferenceSpec(
        key="NIPS",
        display_name="NeurIPS",
        venue_prefix="NeurIPS.cc",
    ),
    # These conferences prefer their own public paper sources. DBLP is retained
    # only as a documented fallback when an official source is not available.
    "AAAI": ConferenceSpec(
        key="AAAI", display_name="AAAI", source_kind="official_then_dblp",
        # Prefer the official accepted-paper list, but allow DBLP after the
        # proceedings have been published or when the official page changes.
        requires_official_source=False,
        dblp_collection="conf/aaai", dblp_volume_prefix="aaai",
        official_adapter="accepted_list_html",
        official_url_templates=(
            "https://aaai.org/conference/aaai/aaai-{year_short}/accepted-papers/",
        ),
    ),
    "ACL": ConferenceSpec(
        key="ACL", display_name="ACL", source_kind="official_then_dblp",
        dblp_collection="conf/acl", dblp_volume_prefix="acl",
        # ACL's main conference has distinct long- and short-paper volumes.
        dblp_volume_suffixes=(1, 2),
        official_adapter="acl_anthology",
    ),
    "CVPR": ConferenceSpec(
        key="CVPR", display_name="CVPR", source_kind="official_then_dblp",
        dblp_collection="conf/cvpr", dblp_volume_prefix="cvpr",
        official_adapter="cvf_openaccess",
    ),
    "ICCV": ConferenceSpec(
        key="ICCV", display_name="ICCV", source_kind="official_then_dblp",
        dblp_collection="conf/iccv", dblp_volume_prefix="iccv",
        official_adapter="cvf_openaccess",
    ),
    "WWW": ConferenceSpec(
        key="WWW", display_name="The Web Conference (WWW)",
        venue_prefix="ACM.org/TheWebConf",
        allow_dblp_fallback=True,
        dblp_collection="conf/www", dblp_volume_prefix="www",
    ),
    "RTSS": ConferenceSpec(
        key="RTSS", display_name="RTSS", source_kind="official_then_dblp",
        dblp_collection="conf/rtss", dblp_volume_prefix="rtss",
        official_adapter="accepted_list_html",
        official_url_templates=("https://{year}.rtss.org/program/",),
    ),
    "SIGKDD": ConferenceSpec(
        key="SIGKDD", display_name="ACM SIGKDD", source_kind="official_then_dblp",
        requires_official_source=True,
        dblp_collection="conf/kdd", dblp_volume_prefix="kdd",
        official_adapter="accepted_list_html",
        official_url_templates=("https://kdd{year}.kdd.org/accepted-papers/",),
    ),
    "ICDE": ConferenceSpec(
        key="ICDE", display_name="ICDE", source_kind="official_then_dblp",
        dblp_collection="conf/icde", dblp_volume_prefix="icde",
        official_adapter="icde_research_papers_html",
        official_url_templates=("https://ieee-icde.org/{year}/research-papers/",),
    ),
    "VLDB": ConferenceSpec(
        key="VLDB", display_name="VLDB / PVLDB", source_kind="dblp",
        # PVLDB volume N corresponds to calendar year N + 2007.
        dblp_collection="journals/pvldb", dblp_volume_prefix="pvldb",
        dblp_volume_year_offset=-2007,
        # PVLDB volume 18 issue 12 is front matter and accompanying events,
        # not the VLDB research track. The first eleven issues form VLDB 2025.
        dblp_journal_issues=(
            DblpJournalIssue(
                "https://dblp.org/db/journals/pvldb/pvldb{journal_volume}.xml",
                journal_volume_year_offset=-2007,
                issue_numbers=("1", "2", "3", "4", "5", "6", "7", "8", "9", "10", "11"),
                publication_year_offsets=(0,),
            ),
        ),
    ),
    "SIGMOD": ConferenceSpec(
        key="SIGMOD", display_name="ACM SIGMOD Conference",
        source_kind="official_then_dblp",
        dblp_collection="conf/sigmod", dblp_volume_prefix="sigmod",
        official_adapter="sigmod_accepted_html",
        official_url_templates=(
            "https://sigmodconf.hosting.acm.org/{year}/sigmod_papers.shtml",
        ),
        # SIGMOD 2025 is published across PACMMOD V3 N1/N3 and V2 N6.
        # These issue filters are only a transparent fallback when the
        # conference's own accepted-paper page cannot be retrieved.
        dblp_journal_issues=(
            DblpJournalIssue(
                "https://dblp.org/db/journals/pacmmod/pacmmod{journal_volume}.xml",
                journal_volume_year_offset=-2022,
                issue_numbers=("1", "3"),
            ),
            DblpJournalIssue(
                "https://dblp.org/db/journals/pacmmod/pacmmod{journal_volume}.xml",
                journal_volume_year_offset=-2023,
                issue_numbers=("6",),
            ),
        ),
    ),
    "FSE": ConferenceSpec(
        key="FSE", display_name="ACM International Conference on the Foundations of Software Engineering",
        source_kind="official_then_dblp",
        dblp_collection="journals/pacmse", dblp_volume_prefix="pacmse",
        official_adapter="researchr_accepted_html",
        official_url_templates=(
            "https://conf.researchr.org/track/fse-{year}/fse-{year}-research-papers",
        ),
        # PACMSE V2's FSE issue is the proceedings record for FSE 2025.
        dblp_journal_issues=(
            DblpJournalIssue(
                "https://dblp.org/db/journals/pacmse/pacmse{journal_volume}.xml",
                journal_volume_year_offset=-2023,
                issue_numbers=("FSE",),
            ),
        ),
    ),
    "PLDI": ConferenceSpec(
        key="PLDI", display_name="ACM SIGPLAN Conference on Programming Language Design and Implementation",
        source_kind="dblp",
        dblp_collection="journals/pacmpl", dblp_volume_prefix="pacmpl",
        dblp_journal_issues=(
            DblpJournalIssue(
                "https://dblp.org/db/journals/pacmpl/pacmpl{journal_volume}.xml",
                journal_volume_year_offset=-2016,
                issue_numbers=("PLDI",),
            ),
        ),
    ),
    "POPL": ConferenceSpec(
        key="POPL", display_name="ACM SIGPLAN-SIGACT Symposium on Principles of Programming Languages",
        source_kind="dblp",
        dblp_collection="journals/pacmpl", dblp_volume_prefix="pacmpl",
        dblp_journal_issues=(
            DblpJournalIssue(
                "https://dblp.org/db/journals/pacmpl/pacmpl{journal_volume}.xml",
                journal_volume_year_offset=-2016,
                issue_numbers=("POPL",),
            ),
        ),
    ),
    "ISSTA": ConferenceSpec(
        key="ISSTA", display_name="International Symposium on Software Testing and Analysis",
        source_kind="dblp",
        dblp_collection="journals/pacmse", dblp_volume_prefix="pacmse",
        dblp_journal_issues=(
            DblpJournalIssue(
                "https://dblp.org/db/journals/pacmse/pacmse{journal_volume}.xml",
                journal_volume_year_offset=-2023,
                issue_numbers=("ISSTA",),
            ),
        ),
    ),
    "OOPSLA": ConferenceSpec(
        key="OOPSLA", display_name="Conference on Object-Oriented Programming Systems, Languages, and Applications",
        source_kind="dblp",
        dblp_collection="journals/pacmpl", dblp_volume_prefix="pacmpl",
        dblp_journal_issues=(
            DblpJournalIssue(
                "https://dblp.org/db/journals/pacmpl/pacmpl{journal_volume}.xml",
                journal_volume_year_offset=-2016,
                issue_numbers=("OOPSLA1", "OOPSLA2"),
            ),
        ),
    ),
    "CSCW": ConferenceSpec(
        key="CSCW", display_name="ACM Conference on Computer Supported Cooperative Work and Social Computing",
        source_kind="official_then_dblp",
        dblp_collection="journals/pacmhci", dblp_volume_prefix="pacmhci",
        official_adapter="sigchi_program_json",
        dblp_journal_issues=(
            DblpJournalIssue(
                "https://dblp.org/db/journals/pacmhci/pacmhci{journal_volume}.xml",
                journal_volume_year_offset=-2016,
                issue_numbers=("2", "7"),
            ),
        ),
    ),
    "UBICOMP": ConferenceSpec(
        key="UBICOMP", display_name="ACM International Joint Conference on Pervasive and Ubiquitous Computing",
        source_kind="dblp",
        dblp_collection="journals/imwut", dblp_volume_prefix="imwut",
        # The annual UbiComp technical programme comprises IMWUT V(n-1) N4
        # plus Vn N1--N3.  Order the current volume first so a sampled record
        # retains the requested calendar publication year when available.
        dblp_journal_issues=(
            DblpJournalIssue(
                "https://dblp.org/db/journals/imwut/imwut{journal_volume}.xml",
                journal_volume_year_offset=-2016,
                issue_numbers=("1", "2", "3"),
            ),
            DblpJournalIssue(
                "https://dblp.org/db/journals/imwut/imwut{journal_volume}.xml",
                journal_volume_year_offset=-2017,
                issue_numbers=("4",),
            ),
        ),
    ),
    "SIGIR": ConferenceSpec(
        key="SIGIR", display_name="International ACM SIGIR Conference on Research and Development in Information Retrieval",
        source_kind="official_then_dblp",
        dblp_collection="conf/sigir", dblp_volume_prefix="sigir",
        official_adapter="sigir_accepted_html",
        official_url_templates=(
            "https://sigir{year}.dei.unipd.it/accepted-papers.html",
        ),
        # The proceedings include the conference keynote as a bibliographic
        # record. It is not a submitted SIGIR research paper.
        dblp_excluded_title_patterns=(
            r"^digital health\.?$",
            r"^please meet ai, our dear new colleague\. in other words: can scientists and machines truly cooperate\?$",
            r"^bm25 and all that - a look back\.?$",
        ),
    ),
    "ACM_MM": ConferenceSpec(
        key="ACM_MM", display_name="ACM International Conference on Multimedia",
        source_kind="official_then_dblp",
        dblp_collection="conf/mm", dblp_volume_prefix="mm",
        official_adapter="acmmm_regular_papers_html",
        official_url_templates=(
            "https://acmmm{year}.org/accepted-regular-papers/",
        ),
        # ACM MM's programme keynote is distributed in the same proceedings
        # feed; retain only paper records for collection and validation.
        dblp_excluded_title_patterns=(
            r"^ai-mediated human interaction\.?$",
            r"^next phase of research on multimodal foundation models: from alignments to content generation and quality assessment\.?$",
            r"^sensecam and isotyping: the challenges and benefits of working with new hardware\.?$",
        ),
    ),
    "CCS": ConferenceSpec(
        key="CCS", display_name="ACM Conference on Computer and Communications Security",
        source_kind="dblp",
        dblp_collection="conf/ccs", dblp_volume_prefix="ccs",
        dblp_excluded_title_patterns=(
            r"^autonomous vulnerability analysis, triaging, and repair: a historical perspective\.?$",
            r"^mechanizing privacy by design\.?$",
            r"^(?:demo|poster|dissertation research description|acm ccs young scholars)",
            r"workshop",
        ),
        # CCS research papers are long-form; this excludes the published
        # demo/poster/doctoral descriptions that share the proceedings volume.
        dblp_min_page_count=6,
    ),
    "IEEE_VIS": ConferenceSpec(
        key="IEEE_VIS", display_name="IEEE Visualization Conference",
        source_kind="official_then_dblp",
        dblp_collection="conf/visualization", dblp_volume_prefix="vis",
        # The public program exposes a static JSON export.  Restricting its
        # contents to ``paper_type=full`` preserves the catalog's main-paper
        # scope rather than mixing in invited, short, or associated papers.
        official_adapter="ieee_vis_json",
        official_url_templates=(
            "https://ieeexplore.ieeevis.org/year/{year}/program/papers.json",
        ),
    ),
}

CONFERENCE_ALIASES = {
    "ICLR": "ICLR",
    "ICML": "ICML",
    "NIPS": "NIPS",
    "NEURIPS": "NIPS",
    "AAAI": "AAAI",
    "ACL": "ACL",
    "CVPR": "CVPR",
    "ICCV": "ICCV",
    "WWW": "WWW",
    "RTSS": "RTSS",
    "SIGKDD": "SIGKDD",
    "KDD": "SIGKDD",
    "ICDE": "ICDE",
    "VLDB": "VLDB",
    "ALL": "ALL",
}


def load_ccf_a_catalog(path: Path = CCF_A_CATALOG_PATH) -> dict[str, Any]:
    """Load and validate the versioned CCF A-conference catalog bundled here."""
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"Cannot load CCF A conference catalog {path}: {exc}") from exc
    entries = payload.get("conferences") if isinstance(payload, dict) else None
    if not isinstance(entries, list) or not entries:
        raise RuntimeError("CCF A conference catalog has no conference entries")
    required = {
        "key",
        "abbreviation",
        "full_name",
        "category",
        "type",
        "professional_field",
        "dblp_collection",
        "dblp_volume_prefix",
    }
    keys: set[str] = set()
    for entry in entries:
        if not isinstance(entry, dict) or not required.issubset(entry):
            raise RuntimeError("CCF A conference catalog has an invalid entry")
        key = str(entry["key"])
        if key in keys:
            raise RuntimeError(f"CCF A conference catalog repeats key {key!r}")
        if entry["category"] != "A" or entry["type"] != "会议":
            raise RuntimeError(f"CCF catalog entry {key!r} is not an A conference")
        keys.add(key)
    return payload


CCF_A_CATALOG = load_ccf_a_catalog()
CCF_A_BY_KEY: dict[str, dict[str, Any]] = {
    str(entry["key"]): entry for entry in CCF_A_CATALOG["conferences"]
}


def register_ccf_a_conferences() -> None:
    """Enrich special sources and add DBLP-fallback specs for every CCF A venue."""
    for key, entry in CCF_A_BY_KEY.items():
        ccf_values = {
            "ccf_abbreviation": str(entry["abbreviation"]),
            "ccf_category": str(entry["category"]),
            "ccf_type": str(entry["type"]),
            "ccf_professional_field": str(entry["professional_field"]),
        }
        if key in CONFERENCE_SPECS:
            # Preserve the explicitly implemented official/OpenReview source
            # strategy, but attach the CCF metadata to its output and manifest.
            CONFERENCE_SPECS[key] = replace(CONFERENCE_SPECS[key], **ccf_values)
        else:
            # A catalog-driven venue has no venue-specific official adapter yet.
            # The collector records this fact and then uses DBLP as a bounded,
            # reproducible fallback rather than pretending it is a live decision feed.
            CONFERENCE_SPECS[key] = ConferenceSpec(
                key=key,
                display_name=str(entry["full_name"]),
                source_kind="official_then_dblp",
                dblp_collection=str(entry["dblp_collection"]),
                dblp_volume_prefix=str(entry["dblp_volume_prefix"]),
                dblp_volume_year_offset=int(entry.get("dblp_volume_year_offset", 0)),
                official_adapter="",
                **ccf_values,
            )
        for alias in [key, str(entry["abbreviation"]), *entry.get("aliases", [])]:
            normalized_alias = str(alias).strip().upper()
            if normalized_alias:
                CONFERENCE_ALIASES.setdefault(normalized_alias, key)


register_ccf_a_conferences()


def load_ccf_catalog() -> dict[str, Any]:
    payload = json.loads(bundled_resource_path("ccf_venues.json").read_text(encoding="utf-8"))
    entries = payload["venues"]
    keys = [entry["key"] for entry in entries]
    if len(keys) != len(set(keys)) or len(keys) != payload["unique_venue_count"]:
        raise RuntimeError("Invalid CCF catalogue: duplicate keys or incorrect count")
    for entry in entries:
        if entry["category"] not in {"A", "B", "C"} or entry["type"] not in {"会议", "期刊"}:
            raise RuntimeError(f"Invalid CCF classification: {entry['key']}")
        if not (entry["dblp_collection"] or entry["issns"] or entry["venue_filter"]):
            raise RuntimeError(f"Missing metadata source: {entry['key']}")
    return payload


CCF_CATALOG = load_ccf_catalog()
for _entry in CCF_CATALOG["venues"]:
    _key = _entry["key"]
    if _key not in CONFERENCE_SPECS:
        CONFERENCE_SPECS[_key] = ConferenceSpec(
            key=_key, display_name=_entry["full_name"], source_kind=_entry["source_kind"],
            dblp_collection=_entry["dblp_collection"], issns=tuple(_entry["issns"]),
            venue_filter=_entry["venue_filter"], mapping_note=_entry["mapping_note"],
            ccf_abbreviation=_entry["abbreviation"], ccf_category=_entry["category"],
            ccf_type=_entry["type"], ccf_professional_field=_entry["professional_field"],
        )
    CONFERENCE_ALIASES[_key.upper()] = _key
# Only unambiguous new abbreviations become aliases. Existing names stay stable.
for _entry in CCF_CATALOG["venues"]:
    _alias = _entry["abbreviation"].strip().upper()
    if _alias and sum(e["abbreviation"].strip().upper() == _alias for e in CCF_CATALOG["venues"]) == 1:
        CONFERENCE_ALIASES.setdefault(_alias, _entry["key"])

# Reference counts are diagnostic only. They never determine membership.
REFERENCE_COUNTS: dict[tuple[str, int], dict[str, int]] = {
    ("ICLR", 2026): {
        "openreview_current_accepted": 5351,
        "openreview_oral": 224,
        "openreview_poster": 5127,
        "virtual_main_conference": 5353,
    }
}


CSV_FIELDS = [
    "conference",
    "conference_display_name",
    "ccf_abbreviation",
    "ccf_category",
    "ccf_type",
    "ccf_professional_field",
    "year",
    "source_type",
    "source_id",
    "source_record_id",
    "source_url",
    "doi",
    "paper_url",
    "booktitle",
    "volume",
    "issue",
    "pages",
    "venueid",
    "openreview_id",
    "submission_number",
    "title",
    "presentation_type",
    "venue",
    "track",
    "authors",
    "author_ids",
    "author_institutions",
    "author_virtual_urls",
    "primary_area",
    "secondary_area",
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
    "openreview_cdate_ms",
    "openreview_mdate_ms",
    "openreview_tmdate_ms",
    "openreview_cdate_utc",
    "openreview_mdate_utc",
    "openreview_tmdate_utc",
    "virtual_event_id",
    "virtual_source_id",
]


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def ccf_metadata(spec: ConferenceSpec) -> dict[str, str]:
    """Return empty values for non-CCF venues and catalog values otherwise."""
    return {
        "abbreviation": spec.ccf_abbreviation,
        "category": spec.ccf_category,
        "type": spec.ccf_type,
        "professional_field": spec.ccf_professional_field,
        "catalog_version": str(CCF_A_CATALOG.get("catalog_version", "")),
        "catalog_source_url": str(CCF_A_CATALOG.get("catalog_source_url", "")),
    }


def milliseconds_to_iso(value: Any) -> str:
    try:
        milliseconds = int(value)
    except (TypeError, ValueError):
        return ""
    try:
        return datetime.fromtimestamp(
            milliseconds / 1000, tz=timezone.utc
        ).replace(microsecond=0).isoformat()
    except (OverflowError, OSError, ValueError):
        return ""


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


_dblp_access = threading.local()
DBLP_HOSTS = {"dblp.org", "dblp.uni-trier.de", "dblp1.uni-trier.de", "dblp.dagstuhl.de"}


@contextmanager
def dblp_access_session(progress=None):
    """Keep verification cookies in memory and progress local to this fetch thread."""
    previous = getattr(_dblp_access, "session", None)
    _dblp_access.session = (build_opener(HTTPCookieProcessor(CookieJar())), progress)
    try:
        yield
    finally:
        _dblp_access.session = previous


class _RefreshParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.refresh = None

    def handle_starttag(self, tag, attrs):
        values = dict(attrs)
        if tag == "meta" and (values.get("http-equiv") or "").casefold() == "refresh":
            self.refresh = values.get("content")


def _dblp_challenge_refresh(raw: bytes, response_url: str):
    if b"anubis_challenge" not in raw:
        return None
    parser = _RefreshParser()
    parser.feed(raw.decode("utf-8", errors="replace"))
    match = re.fullmatch(r"\s*(\d+)\s*;\s*url\s*=\s*(.+?)\s*", parser.refresh or "", re.I)
    if not match:
        raise RuntimeError("DBLP 返回了暂不支持的访问验证；未取得论文数据。请在浏览器检查访问状态。")
    delay = int(match[1])
    target = urljoin(response_url, match[2].strip("\"'"))
    source, destination = urlparse(response_url), urlparse(target)
    if (delay > 10 or source.hostname not in DBLP_HOSTS
            or (destination.scheme, destination.netloc) != (source.scheme, source.netloc)
            or destination.path != "/.within.website/x/cmd/anubis/api/pass-challenge"):
        raise RuntimeError("DBLP 验证等待时间或跳转地址不符合已支持的验证流程；已停止采集。")
    return delay, target


def request_bytes(
    url: str,
    *,
    timeout: int = 90,
    retries: int = 4,
) -> tuple[bytes, dict[str, str]]:
    """Fetch a public URL with bounded retries and return bytes plus headers."""
    last_error: Exception | None = None
    is_dblp = urlparse(url).hostname in DBLP_HOSTS
    if is_dblp:
        if getattr(_dblp_access, "session", None) is None:
            _dblp_access.session = (build_opener(HTTPCookieProcessor(CookieJar())), None)
        opener, progress = _dblp_access.session
    for attempt in range(retries):
        try:
            current_url = url
            for hop in range(3):
                req = Request(current_url, headers={
                    "User-Agent": USER_AGENT,
                    "Accept": "application/json,text/plain;q=0.9,*/*;q=0.1",
                })
                open_request = opener.open if is_dblp else urlopen
                with open_request(req, timeout=timeout) as response:
                    raw = response.read()
                    headers = {
                        "url": response.geturl(),
                        "status": str(getattr(response, "status", 200)),
                        "content_type": response.headers.get("Content-Type", ""),
                        "content_length_header": response.headers.get("Content-Length", ""),
                        "etag": response.headers.get("ETag", ""),
                        "last_modified": response.headers.get("Last-Modified", ""),
                    }
                refresh = _dblp_challenge_refresh(raw, headers["url"]) if is_dblp else None
                if refresh is None:
                    return raw, headers
                if hop == 2:
                    raise RuntimeError("DBLP 访问验证反复出现；此次采集未完成，请稍后重试。")
                delay, current_url = refresh
                if progress:
                    progress(f"正在等待 DBLP 访问验证（{delay} 秒）…")
                time.sleep(delay)
                if progress:
                    progress("正在完成 DBLP 访问验证并继续采集…")
        except HTTPError as exc:
            last_error = exc
            body = exc.read().decode("utf-8", errors="replace")
            if 400 <= exc.code < 500:
                challenge_hint = ""
                if "ChallengeRequiredError" in body:
                    challenge_hint = (
                        " OpenReview requested an anti-bot challenge. Try another "
                        "network, disable a problematic VPN/proxy, or complete the "
                        "browser verification before rerunning."
                    )
                raise RuntimeError(
                    f"HTTP {exc.code} for {url}: {body[:500]}{challenge_hint}"
                ) from exc
        # Some public mirrors terminate a keep-alive connection before a
        # response is sent (for example ``http.client.RemoteDisconnected``).
        # It is an ``OSError`` rather than a ``URLError`` on CPython, so treat
        # it as a retryable transport failure instead of aborting a whole
        # multi-conference validation run.
        except (URLError, TimeoutError, OSError) as exc:
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
    # UTF-8 BOM keeps non-ASCII titles and names readable in desktop Excel.
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def read_csv_rows(path: Path) -> list[dict[str, Any]]:
    """Load a previously generated CSV, accepting the UTF-8 BOM used by writer."""
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def read_jsonl_rows(path: Path) -> list[dict[str, Any]]:
    """Load a previous rich-record export for a combined ``ALL`` request."""
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            try:
                value = json.loads(line)
            except json.JSONDecodeError as exc:
                raise RuntimeError(
                    f"Invalid JSONL at {path}, line {line_number}"
                ) from exc
            if not isinstance(value, dict):
                raise RuntimeError(
                    f"Invalid JSONL object at {path}, line {line_number}"
                )
            rows.append(value)
    return rows


def content_value(content: dict[str, Any], key: str, default: Any = None) -> Any:
    value = content.get(key, default)
    if isinstance(value, dict) and "value" in value:
        return value.get("value", default)
    return value


def first_content_value(
    content: dict[str, Any], keys: Iterable[str], default: Any = ""
) -> Any:
    for key in keys:
        value = content_value(content, key, None)
        if value not in (None, "", []):
            return value
    return default


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
    """Decode HTML entities and remove XML-invalid invisible characters."""
    value = html.unescape(value)
    return re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\ufffe\uffff]", "", value)


def normalize_sequence(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    return [value]


def presentation_type_from_venue(venue: str) -> str:
    """Map venue labels from different conferences to a stable category."""
    normalized = venue.strip().lower()
    label_patterns = (
        ("oral", "Oral"),
        ("spotlight", "Spotlight"),
        ("poster", "Poster"),
        ("notable", "Notable"),
    )
    for needle, label in label_patterns:
        if needle in normalized:
            return label
    return "Accepted"


def submission_sort_key(value: Any) -> tuple[int, int | str]:
    """Sort numeric submission numbers before missing or nonnumeric values."""
    if value in (None, ""):
        return (2, "")
    try:
        return (0, int(value))
    except (TypeError, ValueError):
        return (1, str(value))


def fetch_openreview_accepted(
    raw_dir: Path,
    *,
    venue_id: str,
    page_size: int = 1000,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Fetch an exact accepted-paper set from OpenReview API v2 search."""
    notes: list[dict[str, Any]] = []
    page_meta: list[dict[str, Any]] = []
    offset = 0
    reported_count: int | None = None

    while reported_count is None or offset < reported_count:
        params = {
            "term": venue_id,
            "content": "venueid",
            "type": "exact",
            "venueid": venue_id,
            "limit": page_size,
            "offset": offset,
            "count": "true",
            "sort": "tmdate:asc",
        }
        url = OPENREVIEW_SEARCH_URL + "?" + urlencode(params)
        payload, raw, headers = request_json(url)
        if not isinstance(payload, dict):
            raise RuntimeError(f"Unexpected OpenReview response type for {venue_id}")
        batch = payload.get("notes", [])
        if not isinstance(batch, list):
            raise RuntimeError(f"OpenReview response has invalid notes for {venue_id}")
        if reported_count is None:
            reported_count = int(payload.get("count", 0))

        page_path = raw_dir / f"openreview_search_offset_{offset:05d}.json"
        write_bytes(page_path, raw)
        page_meta.append(
            {
                **headers,
                "venue_id": venue_id,
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
        key=lambda note: (
            submission_sort_key(note.get("number")),
            note.get("id", ""),
        ),
    )

    if reported_count is not None and len(notes) != reported_count:
        raise RuntimeError(
            f"OpenReview pagination was not stable for {venue_id}: "
            f"reported count={reported_count}, unique notes={len(notes)}. "
            "Rerun to obtain a consistent snapshot."
        )

    bad_venueids = [
        note.get("id", "")
        for note in notes
        if content_value(note.get("content", {}), "venueid") != venue_id
    ]
    if bad_venueids:
        raise RuntimeError(
            f"OpenReview exact search returned {len(bad_venueids)} "
            f"non-matching venue IDs for {venue_id}"
        )
    return notes, page_meta


def iclr_virtual_urls(year: int) -> tuple[str, str]:
    return (
        f"https://iclr.cc/static/virtual/data/iclr-{year}-orals-posters.json",
        f"https://iclr.cc/static/virtual/data/iclr-{year}-abstracts.json",
    )


def fetch_iclr_virtual_sources(
    raw_dir: Path, year: int
) -> tuple[dict[str, Any], dict[str, str], list[dict[str, Any]]]:
    meta_url, abstracts_url = iclr_virtual_urls(year)
    meta, meta_raw, meta_headers = request_json(meta_url)
    abstracts, abstracts_raw, abstracts_headers = request_json(abstracts_url)
    if not isinstance(meta, dict) or not isinstance(abstracts, dict):
        raise RuntimeError("ICLR Virtual endpoints returned unexpected JSON")

    meta_path = raw_dir / f"iclr-{year}-orals-posters.json"
    abstracts_path = raw_dir / f"iclr-{year}-abstracts.json"
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


def normalize_iclr_virtual(
    payload: dict[str, Any],
    abstracts: dict[str, str],
    *,
    venue_group_url: str,
) -> tuple[dict[str, dict[str, Any]], dict[str, Any]]:
    results = payload.get("results", [])
    if not isinstance(results, list):
        raise RuntimeError("ICLR Virtual payload has no valid results list")
    if payload.get("count") is not None and payload.get("count") != len(results):
        raise RuntimeError(
            f"ICLR Virtual count mismatch: count={payload.get('count')}, "
            f"results={len(results)}"
        )

    paper_nodes = [record for record in results if record.get("eventtype") == "Poster"]
    main_nodes = [
        record
        for record in paper_nodes
        if record.get("event_type") == "Poster"
        and record.get("sourceurl") == venue_group_url
    ]
    oral_events = {
        record.get("id"): record
        for record in results
        if record.get("eventtype") == "Oral"
    }

    by_forum: dict[str, dict[str, Any]] = {}
    for record in main_nodes:
        forum_id = forum_id_from_url(record.get("paper_url"))
        if not forum_id:
            raise RuntimeError(
                f"ICLR Virtual main record {record.get('id')} lacks forum ID"
            )
        if forum_id in by_forum:
            raise RuntimeError(f"Duplicate ICLR Virtual forum ID: {forum_id}")
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

    stats = {
        "payload_count": payload.get("count"),
        "result_count": len(results),
        "eventtype_counts": dict(Counter(r.get("eventtype") for r in results)),
        "paper_node_count": len(paper_nodes),
        "paper_node_source_counts": dict(
            Counter((r.get("sourceurl") or "<blank>") for r in paper_nodes)
        ),
        "main_conference_count": len(main_nodes),
        "main_decision_counts": dict(Counter(r.get("decision") for r in main_nodes)),
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
    note: dict[str, Any],
    *,
    spec: ConferenceSpec,
    year: int,
    virtual: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    content = note.get("content", {})
    forum_id = note.get("id") or note.get("forum") or ""
    venue = str(content_value(content, "venue", "") or "")
    presentation = presentation_type_from_venue(venue)

    authors = normalize_sequence(content_value(content, "authors", []))
    authorids = normalize_sequence(content_value(content, "authorids", []))
    keywords = normalize_sequence(content_value(content, "keywords", []))

    pdf_path = str(content_value(content, "pdf", "") or "")
    related_orals = (virtual or {}).get("oral_events", []) or []
    virtual_authors = (virtual or {}).get("authors", []) or []
    virtual_author_urls = [
        absolute_url("https://iclr.cc", author.get("url", ""))
        for author in virtual_authors
        if isinstance(author, dict)
    ]
    institutions = author_institutions_from_virtual(virtual)
    oral_sessions = [record.get("session", "") for record in related_orals]
    oral_times = [record.get("starttime", "") for record in related_orals]

    track = first_content_value(
        content,
        ("track", "paper_type", "submission_type", "venue_type"),
        "",
    )
    secondary_area = first_content_value(
        content,
        ("secondary_area", "secondary_areas", "secondary_subject_area"),
        "",
    )
    secondary_area_values = normalize_sequence(secondary_area)

    csv_row: dict[str, Any] = {
        "conference": spec.key,
        "conference_display_name": spec.display_name,
        "ccf_abbreviation": spec.ccf_abbreviation,
        "ccf_category": spec.ccf_category,
        "ccf_type": spec.ccf_type,
        "ccf_professional_field": spec.ccf_professional_field,
        "year": year,
        "source_type": "openreview",
        "source_id": spec.source_id(year),
        "source_record_id": forum_id,
        "source_url": f"https://openreview.net/forum?id={forum_id}",
        "doi": "",
        "paper_url": absolute_url("https://openreview.net", pdf_path),
        "booktitle": "",
        "venueid": content_value(content, "venueid", "") or "",
        "openreview_id": forum_id,
        "submission_number": note.get("number", ""),
        "title": content_value(content, "title", "") or "",
        "presentation_type": presentation,
        "venue": venue,
        "track": track,
        "authors": "; ".join(str(value) for value in authors),
        "author_ids": "; ".join(str(value) for value in authorids),
        "author_institutions": "; ".join(institutions),
        "author_virtual_urls": "; ".join(virtual_author_urls),
        "primary_area": first_content_value(
            content,
            ("primary_area", "primary_subject_area", "subject_area"),
            "",
        ),
        "secondary_area": "; ".join(str(value) for value in secondary_area_values),
        "topic": (virtual or {}).get("topic", "") or "",
        "keywords": "; ".join(str(value) for value in keywords),
        "tldr": first_content_value(content, ("TLDR", "TL;DR", "tldr"), ""),
        "abstract": content_value(content, "abstract", "")
        or (virtual or {}).get("abstract", "")
        or "",
        "poster_session": (virtual or {}).get("session", "") or "",
        "poster_start": (virtual or {}).get("starttime", "") or "",
        "poster_end": (virtual or {}).get("endtime", "") or "",
        "oral_session": "; ".join(str(value) for value in oral_sessions if value),
        "oral_start": "; ".join(str(value) for value in oral_times if value),
        "poster_position": (virtual or {}).get("poster_position", "") or "",
        "openreview_url": f"https://openreview.net/forum?id={forum_id}",
        "pdf_url": absolute_url("https://openreview.net", pdf_path),
        "virtual_url": absolute_url(
            "https://iclr.cc", (virtual or {}).get("virtualsite_url", "")
        ),
        "bibtex": content_value(content, "_bibtex", "") or "",
        "license": note.get("license", "") or "",
        "openreview_cdate_ms": note.get("cdate", ""),
        "openreview_mdate_ms": note.get("mdate", ""),
        "openreview_tmdate_ms": note.get("tmdate", ""),
        "openreview_cdate_utc": milliseconds_to_iso(note.get("cdate")),
        "openreview_mdate_utc": milliseconds_to_iso(note.get("mdate")),
        "openreview_tmdate_utc": milliseconds_to_iso(note.get("tmdate")),
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


def consume_braced_text(value: str, start: int) -> tuple[str, int]:
    """Return the content and end offset of a balanced BibTeX-style value."""
    if start >= len(value) or value[start] != "{":
        raise ValueError("balanced value must start with an opening brace")
    depth = 0
    index = start
    while index < len(value):
        character = value[index]
        if character == "\\":
            index += 2
            continue
        if character == "{":
            depth += 1
        elif character == "}":
            depth -= 1
            if depth == 0:
                return value[start + 1 : index], index + 1
        index += 1
    raise ValueError("unclosed braced value")


def consume_quoted_text(value: str, start: int) -> tuple[str, int]:
    if start >= len(value) or value[start] != '"':
        raise ValueError("quoted value must start with a quote")
    index = start + 1
    pieces: list[str] = []
    while index < len(value):
        character = value[index]
        if character == "\\" and index + 1 < len(value):
            pieces.append(value[index : index + 2])
            index += 2
            continue
        if character == '"':
            return "".join(pieces), index + 1
        pieces.append(character)
        index += 1
    raise ValueError("unclosed quoted value")


def parse_bibtex_entries(raw: bytes) -> list[dict[str, str]]:
    """Parse enough BibTeX for public proceedings exports without dependencies."""
    text = raw.decode("utf-8", errors="replace")
    entries: list[dict[str, str]] = []
    start_pattern = re.compile(r"@(\w+)\s*\{")
    for match in start_pattern.finditer(text):
        entry_start = match.start()
        try:
            body, end = consume_braced_text(text, match.end() - 1)
        except ValueError:
            continue
        if "," not in body:
            continue
        citation_key, fields_text = body.split(",", 1)
        fields: dict[str, str] = {}
        index = 0
        while index < len(fields_text):
            while index < len(fields_text) and fields_text[index] in "\t\r\n ,":
                index += 1
            field_match = re.match(r"([A-Za-z][A-Za-z0-9_-]*)\s*=\s*", fields_text[index:])
            if not field_match:
                break
            field_name = field_match.group(1).casefold()
            index += field_match.end()
            try:
                if index < len(fields_text) and fields_text[index] == "{":
                    field_value, index = consume_braced_text(fields_text, index)
                elif index < len(fields_text) and fields_text[index] == '"':
                    field_value, index = consume_quoted_text(fields_text, index)
                else:
                    end_value = fields_text.find(",", index)
                    if end_value < 0:
                        end_value = len(fields_text)
                    field_value = fields_text[index:end_value]
                    index = end_value
            except ValueError:
                break
            fields[field_name] = field_value.strip()
        if fields:
            fields["_entry_type"] = match.group(1).casefold()
            fields["_citation_key"] = citation_key.strip()
            fields["_raw_bibtex"] = text[entry_start:end]
            entries.append(fields)
    return entries


def clean_bibtex_text(value: str) -> str:
    value = value.replace("\\&", "&").replace("\\_", "_")
    value = re.sub(r"\\url\{([^{}]*)\}", r"\1", value)
    value = value.replace("{", "").replace("}", "")
    return clean_normalized_text(value)


def bibliographic_csv_row(
    *,
    spec: ConferenceSpec,
    year: int,
    source_type: str,
    source_id: str,
    source_record_id: str,
    source_url: str,
    title: str,
    authors: list[str],
    booktitle: str = "",
    doi: str = "",
    paper_url: str = "",
    bibtex: str = "",
    track: str = "",
) -> dict[str, Any]:
    """Create one source-neutral bibliographic row in the public CSV schema."""
    return {
        "conference": spec.key,
        "conference_display_name": spec.display_name,
        "ccf_abbreviation": spec.ccf_abbreviation,
        "ccf_category": spec.ccf_category,
        "ccf_type": spec.ccf_type,
        "ccf_professional_field": spec.ccf_professional_field,
        "year": year,
        "source_type": source_type,
        "source_id": source_id,
        "source_record_id": source_record_id,
        "source_url": source_url,
        "doi": doi,
        "paper_url": paper_url,
        "booktitle": booktitle,
        "venueid": "",
        "openreview_id": "",
        "submission_number": "",
        "title": title,
        "presentation_type": "Official published/accepted paper list",
        "venue": booktitle or spec.display_name,
        "track": track,
        "authors": "; ".join(author for author in authors if author),
        "author_ids": "",
        "author_institutions": "",
        "author_virtual_urls": "",
        "primary_area": "",
        "secondary_area": "",
        "topic": "",
        "keywords": "",
        "tldr": "",
        "abstract": "",
        "poster_session": "",
        "poster_start": "",
        "poster_end": "",
        "oral_session": "",
        "oral_start": "",
        "poster_position": "",
        "openreview_url": "",
        "pdf_url": paper_url if paper_url.casefold().endswith(".pdf") else "",
        "virtual_url": "",
        "bibtex": bibtex,
        "license": "",
        "openreview_cdate_ms": "",
        "openreview_mdate_ms": "",
        "openreview_tmdate_ms": "",
        "openreview_cdate_utc": "",
        "openreview_mdate_utc": "",
        "openreview_tmdate_utc": "",
        "virtual_event_id": "",
        "virtual_source_id": "",
    }


def normalize_bibtex_record(
    entry: dict[str, str],
    *,
    spec: ConferenceSpec,
    year: int,
    source_type: str,
    source_id: str,
    collection_url: str,
    track: str = "",
) -> tuple[dict[str, Any], dict[str, Any]] | None:
    if entry.get("_entry_type", "") not in {"inproceedings", "article"}:
        return None
    title = clean_bibtex_text(entry.get("title", ""))
    citation_key = entry.get("_citation_key", "")
    if not title or not citation_key:
        return None
    authors = [
        clean_bibtex_text(author)
        for author in re.split(r"\s+and\s+", entry.get("author", ""))
        if author.strip()
    ]
    if not authors:
        return None
    doi = clean_bibtex_text(entry.get("doi", ""))
    paper_url = clean_bibtex_text(entry.get("url", ""))
    if not paper_url and doi:
        paper_url = f"https://doi.org/{doi}"
    # CVF's displayed BibTeX key is based on the first author and year, so it
    # is not globally unique (many different papers can be ``Wang_2026_CVPR``).
    # Keep the readable key but add a content identity before de-duplication.
    record_fingerprint = hashlib.sha256(
        "\0".join((title, ";".join(authors), doi, paper_url)).encode("utf-8")
    ).hexdigest()[:16]
    row = bibliographic_csv_row(
        spec=spec,
        year=year,
        source_type=source_type,
        source_id=source_id,
        source_record_id=f"{source_type}:{citation_key}:{record_fingerprint}",
        source_url=paper_url or collection_url,
        title=title,
        authors=authors,
        booktitle=clean_bibtex_text(entry.get("booktitle", "")),
        doi=doi,
        paper_url=paper_url,
        bibtex=entry.get("_raw_bibtex", ""),
        track=track,
    )
    return row, {"normalized": row, "official_bibtex": entry}


def official_raw_snapshot(
    raw_dir: Path,
    *,
    filename: str,
    url: str,
) -> tuple[bytes, dict[str, Any]]:
    # A fallback candidate must not hold up the durable source for minutes.
    raw, headers = request_bytes(url, timeout=30, retries=2)
    path = raw_dir / filename
    write_bytes(path, raw)
    return raw, {
        **headers,
        "source": "official public paper source",
        "sha256": sha256_bytes(raw),
        "saved_as": str(path),
    }


def deduplicate_bibliographic_rows(
    rows: list[dict[str, Any]], jsonl_rows: list[dict[str, Any]]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    by_id = {row["source_record_id"]: row for row in rows}
    rich_by_id = {row["normalized"]["source_record_id"]: row for row in jsonl_rows}
    normalized_rows = sorted(
        by_id.values(), key=lambda row: (row["title"], row["source_record_id"])
    )
    return normalized_rows, [rich_by_id[row["source_record_id"]] for row in normalized_rows]


def fetch_acl_anthology(
    raw_dir: Path, *, spec: ConferenceSpec, year: int
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    """Read ACL main-conference long and short papers from ACL Anthology."""
    source_id = f"ACL_ANTHOLOGY:{year}:main_long_short"
    rows: list[dict[str, Any]] = []
    rich_rows: list[dict[str, Any]] = []
    sources: list[dict[str, Any]] = []
    for track in ("acl-long", "acl-short"):
        url = f"https://aclanthology.org/volumes/{year}.{track}.bib"
        raw, source = official_raw_snapshot(
            raw_dir, filename=f"acl_anthology_{track}.bib", url=url
        )
        sources.append(source)
        for entry in parse_bibtex_entries(raw):
            normalized = normalize_bibtex_record(
                entry,
                spec=spec,
                year=year,
                source_type="official_acl_anthology",
                source_id=source_id,
                collection_url=url,
                track="Long Papers" if track.endswith("long") else "Short Papers",
            )
            if normalized is not None:
                row, rich = normalized
                rows.append(row)
                rich_rows.append(rich)
    rows, rich_rows = deduplicate_bibliographic_rows(rows, rich_rows)
    if not rows:
        raise RuntimeError(
            f"ACL Anthology has no public main-conference BibTeX volumes for ACL {year}."
        )
    return rows, rich_rows, sources


def fetch_cvf_openaccess(
    raw_dir: Path, *, spec: ConferenceSpec, year: int
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    """Read CVPR/ICCV papers from CVF's official Open Access repository."""
    conference_code = f"{spec.key}{year}"
    base_url = f"https://openaccess.thecvf.com/{conference_code}"
    base_raw, base_source = official_raw_snapshot(
        raw_dir, filename=f"{conference_code}_index.html", url=base_url
    )
    sources = [base_source]
    base_text = base_raw.decode("utf-8", errors="replace")
    page_urls = {
        urljoin(base_url, match.group(1))
        for match in re.finditer(
            r"href\s*=\s*[\"']([^\"']*\?day=[^\"']+)[\"']",
            base_text,
            flags=re.IGNORECASE,
        )
        if "day=all" not in match.group(1).casefold()
    }
    if not page_urls:
        page_urls = {base_url}

    source_id = f"CVF_OPENACCESS:{conference_code}"
    rows: list[dict[str, Any]] = []
    rich_rows: list[dict[str, Any]] = []
    for page_index, page_url in enumerate(sorted(page_urls), start=1):
        if page_url == base_url:
            raw = base_raw
        else:
            raw, source = official_raw_snapshot(
                raw_dir,
                filename=f"{conference_code}_day_{page_index:02d}.html",
                url=page_url,
            )
            sources.append(source)
        for entry in parse_bibtex_entries(raw):
            normalized = normalize_bibtex_record(
                entry,
                spec=spec,
                year=year,
                source_type="official_cvf_openaccess",
                source_id=source_id,
                collection_url=page_url,
            )
            if normalized is not None:
                row, rich = normalized
                rows.append(row)
                rich_rows.append(rich)
    rows, rich_rows = deduplicate_bibliographic_rows(rows, rich_rows)
    if not rows:
        raise RuntimeError(
            f"CVF Open Access has no public paper records for {spec.key} {year}."
        )
    return rows, rich_rows, sources


def fetch_ieee_vis_json(
    raw_dir: Path, *, spec: ConferenceSpec, year: int
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    """Read IEEE VIS main papers from its public program JSON export.

    IEEE VIS publishes its program client-side.  The page itself declares
    ``papers.json`` as its public paper data endpoint, which is more timely
    than waiting for a DBLP proceedings volume.  Only regular/full papers are
    included: the same JSON also contains invited, short, and associated work.
    """
    urls = official_template_urls(spec, year)
    if len(urls) != 1:
        raise RuntimeError("IEEE VIS JSON adapter requires exactly one URL template")
    url = urls[0]
    raw, source = official_raw_snapshot(
        raw_dir, filename=f"ieee_vis_{year}_papers.json", url=url
    )
    try:
        records = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"IEEE VIS returned invalid paper JSON for {year}: {exc}") from exc
    if not isinstance(records, list):
        raise RuntimeError(f"IEEE VIS paper JSON was not a list for {year}")

    rows: list[dict[str, Any]] = []
    rich_rows: list[dict[str, Any]] = []
    source_id = f"IEEE_VIS_PROGRAM:{year}:full_papers"
    for record in records:
        if not isinstance(record, dict):
            continue
        # ``paper_type`` separates regular submissions from the program's
        # invited material. ``event_id`` is an additional guard against a
        # future full-paper category outside the VIS main-paper programme.
        if str(record.get("paper_type", "")).casefold() != "full":
            continue
        if str(record.get("event_id", "")).casefold() != "v-full":
            continue
        title = clean_normalized_text(str(record.get("title", "")))
        record_id = clean_normalized_text(
            str(record.get("id") or record.get("UID") or "")
        )
        authors_value = record.get("authors", [])
        if not isinstance(authors_value, list):
            authors_value = []
        authors = [
            clean_normalized_text(
                str(author.get("name", "")) if isinstance(author, dict) else str(author)
            )
            for author in authors_value
        ]
        authors = [author for author in authors if author]
        if not title or not record_id or not authors:
            continue
        detail_url = (
            f"https://ieeexplore.ieeevis.org/year/{year}/program/"
            f"paper_{record_id}.html"
        )
        external_link = clean_normalized_text(str(record.get("external_paper_link") or ""))
        pdf_link = clean_normalized_text(str(record.get("pdf_url") or ""))
        paper_url = external_link or pdf_link or detail_url
        doi = clean_normalized_text(str(record.get("doi") or ""))
        row = bibliographic_csv_row(
            spec=spec,
            year=year,
            source_type="official_ieee_vis_program",
            source_id=source_id,
            source_record_id=f"ieee_vis:{year}:{record_id}",
            source_url=detail_url,
            title=title,
            authors=authors,
            booktitle=f"IEEE VIS {year}",
            doi=doi,
            paper_url=paper_url,
            track="VIS Full Papers",
        )
        row["abstract"] = clean_normalized_text(str(record.get("abstract") or ""))
        keywords = record.get("keywords", [])
        if isinstance(keywords, list):
            row["keywords"] = "; ".join(
                clean_normalized_text(str(keyword))
                for keyword in keywords
                if clean_normalized_text(str(keyword))
            )
        rows.append(row)
        rich_rows.append({"normalized": row, "official_ieee_vis_record": record})

    rows, rich_rows = deduplicate_bibliographic_rows(rows, rich_rows)
    if not rows:
        raise RuntimeError(f"IEEE VIS has no public full-paper records for {year}.")
    return rows, rich_rows, [source]


def fetch_sigmod_accepted_html(
    raw_dir: Path, *, spec: ConferenceSpec, year: int
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    """Read the SIGMOD research-paper list published by the conference itself."""
    urls = official_template_urls(spec, year)
    if len(urls) != 1:
        raise RuntimeError("SIGMOD accepted-list adapter requires exactly one URL template")
    url = urls[0]
    raw, source = official_raw_snapshot(
        raw_dir, filename=f"sigmod_{year}_accepted_papers.html", url=url
    )
    text = raw.decode("utf-8", errors="replace")
    heading_pattern = re.compile(
        r"<h3\b[^>]*\bid\s*=\s*['\"]round\d+['\"][^>]*>.*?</h3>",
        flags=re.IGNORECASE | re.DOTALL,
    )
    headings = list(heading_pattern.finditer(text))
    if not headings:
        raise RuntimeError(f"SIGMOD accepted-list page has no round sections for {year}")

    rows: list[dict[str, Any]] = []
    rich_rows: list[dict[str, Any]] = []
    source_id = f"SIGMOD_ACCEPTED_LIST:{year}:research_papers"
    paper_pattern = re.compile(
        r"<li\b[^>]*>\s*<b\b[^>]*>(?P<title>.*?)</b>\s*"
        r"<br\s*/?\s*>(?P<authors>.*?)</li>",
        flags=re.IGNORECASE | re.DOTALL,
    )
    for index, heading in enumerate(headings):
        section_end = headings[index + 1].start() if index + 1 < len(headings) else len(text)
        section = text[heading.end() : section_end]
        round_name = strip_html_fragment(heading.group(0))
        for match in paper_pattern.finditer(section):
            title = strip_html_fragment(match.group("title"))
            authors_text = strip_html_fragment(match.group("authors"))
            authors = [
                clean_normalized_text(author)
                for author in re.split(r"\s*(?:,|\band\b)\s*", authors_text)
                if clean_normalized_text(author)
            ]
            if not title or not authors:
                continue
            record_id = hashlib.sha256(
                f"{title}\0{';'.join(authors)}".encode("utf-8")
            ).hexdigest()[:16]
            row = bibliographic_csv_row(
                spec=spec,
                year=year,
                source_type="official_sigmod_accepted_list",
                source_id=source_id,
                source_record_id=f"sigmod:{year}:{record_id}",
                source_url=url,
                title=title,
                authors=authors,
                booktitle=f"SIGMOD {year}",
                track="SIGMOD Research Papers",
            )
            rows.append(row)
            rich_rows.append(
                {
                    "normalized": row,
                    "official_sigmod_round": round_name,
                    "official_sigmod_html": {
                        "title": title,
                        "authors": authors,
                    },
                }
            )
    rows, rich_rows = deduplicate_bibliographic_rows(rows, rich_rows)
    if not rows:
        raise RuntimeError(f"SIGMOD accepted-list page has no parsed research papers for {year}")
    return rows, rich_rows, [source]


def fetch_researchr_accepted_html(
    raw_dir: Path, *, spec: ConferenceSpec, year: int
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    """Parse Researchr's static Accepted Papers table for an ACM venue."""
    urls = official_template_urls(spec, year)
    if len(urls) != 1:
        raise RuntimeError("Researchr accepted-list adapter requires exactly one URL template")
    url = urls[0]
    raw, source = official_raw_snapshot(
        raw_dir, filename=f"researchr_{spec.key.lower()}_{year}_accepted.html", url=url
    )
    text = raw.decode("utf-8", errors="replace")
    overview_match = re.search(
        r"<div\b[^>]*\bid\s*=\s*['\"]event-overview['\"][^>]*>",
        text,
        flags=re.IGNORECASE,
    )
    if overview_match is None:
        raise RuntimeError(f"Researchr page has no Accepted Papers overview for {spec.key} {year}")
    table_start = text.find("<table", overview_match.end())
    table_end = text.find("</table>", table_start)
    if table_start < 0 or table_end < 0:
        raise RuntimeError(f"Researchr Accepted Papers table is missing for {spec.key} {year}")
    table = text[table_start : table_end + len("</table>")]
    rows: list[dict[str, Any]] = []
    rich_rows: list[dict[str, Any]] = []
    source_id = f"RESEARCHR_ACCEPTED_LIST:{spec.key}:{year}"
    for row_match in re.finditer(
        r"<tr\b[^>]*>(?P<body>.*?)</tr>", table, flags=re.IGNORECASE | re.DOTALL
    ):
        body = row_match.group("body")
        event_match = re.search(
            r"\bdata-event-modal\s*=\s*['\"](?P<id>[^'\"]+)['\"]",
            body,
            flags=re.IGNORECASE,
        )
        title_match = re.search(
            r"<a\b[^>]*\bdata-event-modal\s*=\s*['\"][^'\"]+['\"][^>]*>"
            r"(?P<title>.*?)</a>",
            body,
            flags=re.IGNORECASE | re.DOTALL,
        )
        performers_match = re.search(
            r"<div\b[^>]*\bclass\s*=\s*['\"][^'\"]*\bperformers\b[^'\"]*['\"][^>]*>"
            r"(?P<authors>.*?)</div>",
            body,
            flags=re.IGNORECASE | re.DOTALL,
        )
        if event_match is None or title_match is None or performers_match is None:
            continue
        title = strip_html_fragment(title_match.group("title"))
        authors = [
            strip_html_fragment(match.group("author"))
            for match in re.finditer(
                r"<a\b[^>]*>(?P<author>.*?)</a>",
                performers_match.group("authors"),
                flags=re.IGNORECASE | re.DOTALL,
            )
        ]
        authors = [author for author in authors if author]
        if not title or not authors:
            continue
        doi_match = re.search(
            r"\bhref\s*=\s*['\"](?P<doi>https?://(?:dx\.)?doi\.org/[^'\"]+)['\"]",
            body,
            flags=re.IGNORECASE,
        )
        doi_url = clean_normalized_text(doi_match.group("doi")) if doi_match else ""
        doi = doi_url.rsplit("doi.org/", 1)[-1] if doi_url else ""
        record_id = clean_normalized_text(event_match.group("id"))
        row = bibliographic_csv_row(
            spec=spec,
            year=year,
            source_type="official_researchr_accepted_list",
            source_id=source_id,
            source_record_id=f"researchr:{spec.key}:{year}:{record_id}",
            source_url=url,
            title=title,
            authors=authors,
            booktitle=f"{spec.display_name} {year}",
            doi=doi,
            paper_url=doi_url,
            track="Research Papers",
        )
        rows.append(row)
        rich_rows.append({"normalized": row, "official_researchr_html": url})
    rows, rich_rows = deduplicate_bibliographic_rows(rows, rich_rows)
    if not rows:
        raise RuntimeError(f"Researchr Accepted Papers table has no parsed records for {spec.key} {year}")
    return rows, rich_rows, [source]


def html_text_lines(text: str) -> list[str]:
    """Turn simple public conference list markup into non-empty display lines."""
    text = re.sub(r"<br\b[^>]*>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(
        r"</(?:p|li|div|h[1-6]|tr|td|section|article)>",
        "\n",
        text,
        flags=re.IGNORECASE,
    )
    text = re.sub(r"<[^>]+>", "", text)
    lines: list[str] = []
    for line in text.splitlines():
        normalized = clean_normalized_text(html.unescape(line)).strip()
        if normalized:
            lines.append(normalized)
    return lines


def fetch_sigir_accepted_html(
    raw_dir: Path, *, spec: ConferenceSpec, year: int
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    """Read only SIGIR's explicitly labelled full-paper section."""
    urls = official_template_urls(spec, year)
    if len(urls) != 1:
        raise RuntimeError("SIGIR accepted-list adapter requires exactly one URL template")
    url = urls[0]
    raw, source = official_raw_snapshot(
        raw_dir, filename=f"sigir_{year}_accepted_papers.html", url=url
    )
    text = raw.decode("utf-8", errors="replace")
    full_heading = re.search(
        r"<h2\b[^>]*\bid\s*=\s*['\"]full-papers['\"][^>]*>.*?</h2>",
        text,
        flags=re.IGNORECASE | re.DOTALL,
    )
    short_heading = re.search(
        r"<h2\b[^>]*\bid\s*=\s*['\"]short-papers['\"][^>]*>.*?</h2>",
        text,
        flags=re.IGNORECASE | re.DOTALL,
    )
    if full_heading is None or short_heading is None or short_heading.start() <= full_heading.end():
        raise RuntimeError(f"SIGIR page has no bounded full-paper section for {year}")
    section = text[full_heading.end() : short_heading.start()]
    item_pattern = re.compile(
        r"<li\b[^>]*\bclass\s*=\s*['\"][^'\"]*\baccepted-paper-item\b[^'\"]*['\"][^>]*>"
        r".*?<span\b[^>]*\bclass\s*=\s*['\"][^'\"]*\baccepted-paper-title\b[^'\"]*['\"][^>]*>"
        r"(?P<title>.*?)</span>"
        r".*?<span\b[^>]*\bclass\s*=\s*['\"][^'\"]*\baccepted-paper-author\b[^'\"]*['\"][^>]*>"
        r"(?P<authors>.*?)</span>",
        flags=re.IGNORECASE | re.DOTALL,
    )
    rows: list[dict[str, Any]] = []
    rich_rows: list[dict[str, Any]] = []
    source_id = f"SIGIR_ACCEPTED_LIST:{year}:full_papers"
    for match in item_pattern.finditer(section):
        title = strip_html_fragment(match.group("title"))
        authors = [
            clean_normalized_text(author)
            for author in strip_html_fragment(match.group("authors")).split(",")
            if clean_normalized_text(author)
        ]
        if not title or not authors:
            continue
        record_id = hashlib.sha256(
            f"{title}\0{';'.join(authors)}".encode("utf-8")
        ).hexdigest()[:16]
        row = bibliographic_csv_row(
            spec=spec,
            year=year,
            source_type="official_sigir_accepted_list",
            source_id=source_id,
            source_record_id=f"sigir:{year}:{record_id}",
            source_url=url,
            title=title,
            authors=authors,
            booktitle=f"SIGIR {year}",
            track="Full Papers",
        )
        rows.append(row)
        rich_rows.append({"normalized": row, "official_sigir_html": url})
    rows, rich_rows = deduplicate_bibliographic_rows(rows, rich_rows)
    if not rows:
        raise RuntimeError(f"SIGIR full-paper section has no parsed records for {year}")
    return rows, rich_rows, [source]


def fetch_numbered_official_papers_html(
    raw_dir: Path,
    *,
    spec: ConferenceSpec,
    year: int,
    source_type: str,
    heading: str,
    title_pattern: str,
    track: str,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    """Parse a public accepted-paper page made of numbered title/author lines."""
    urls = official_template_urls(spec, year)
    if len(urls) != 1:
        raise RuntimeError(f"{spec.key} numbered-list adapter requires one URL template")
    url = urls[0]
    raw, source = official_raw_snapshot(
        raw_dir, filename=f"{spec.key.lower()}_{year}_official_papers.html", url=url
    )
    lines = html_text_lines(raw.decode("utf-8", errors="replace"))
    start = next(
        (
            index + 1
            for index, line in enumerate(lines)
            if line.casefold() == heading.casefold()
        ),
        0,
    )
    title_re = re.compile(title_pattern)
    rows: list[dict[str, Any]] = []
    rich_rows: list[dict[str, Any]] = []
    source_id = f"OFFICIAL_NUMBERED_LIST:{spec.key}:{year}"
    for index in range(start, len(lines) - 1):
        title_match = title_re.fullmatch(lines[index])
        if title_match is None:
            continue
        record_id = title_match.group("id")
        title = clean_normalized_text(title_match.group("title"))
        author_line = lines[index + 1]
        if not title or title_re.fullmatch(author_line):
            continue
        # ICDE uses semicolons between authors; ACM MM uses commas. Keep the
        # exact public names while stripping displayed affiliation brackets.
        raw_authors = re.split(r"\s*;\s*" if ";" in author_line else r"\s*,\s*", author_line)
        authors = [
            clean_normalized_text(re.sub(r"\s*\([^()]*\)", "", author))
            for author in raw_authors
            if clean_normalized_text(re.sub(r"\s*\([^()]*\)", "", author))
        ]
        if not authors:
            continue
        row = bibliographic_csv_row(
            spec=spec,
            year=year,
            source_type=source_type,
            source_id=source_id,
            source_record_id=f"official:{spec.key}:{year}:{record_id}",
            source_url=url,
            title=title,
            authors=authors,
            booktitle=f"{spec.display_name} {year}",
            track=track,
        )
        rows.append(row)
        rich_rows.append({"normalized": row, "official_numbered_list": url})
    rows, rich_rows = deduplicate_bibliographic_rows(rows, rich_rows)
    if not rows:
        raise RuntimeError(f"{spec.key} official paper list has no parsed records for {year}")
    return rows, rich_rows, [source]


def fetch_icde_research_papers_html(
    raw_dir: Path, *, spec: ConferenceSpec, year: int
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    return fetch_numbered_official_papers_html(
        raw_dir,
        spec=spec,
        year=year,
        source_type="official_icde_research_papers",
        heading="Research Papers",
        title_pattern=r"(?P<id>\d+)\s*\|\s*(?P<title>.+)",
        track="Research Papers",
    )


def fetch_acmmm_regular_papers_html(
    raw_dir: Path, *, spec: ConferenceSpec, year: int
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    return fetch_numbered_official_papers_html(
        raw_dir,
        spec=spec,
        year=year,
        source_type="official_acmmm_regular_papers",
        heading="Accepted Papers",
        title_pattern=r"(?P<id>\d+)\s+(?P<title>.+)",
        track="Regular Papers",
    )


SIGCHI_PROGRAM_LIST_URL = "https://files.sigchi.org/conference/cache/program-list"


def sigchi_person_name(person: Any) -> str:
    """Render the public SIGCHI program's structured person object."""
    if not isinstance(person, dict):
        return ""
    return clean_normalized_text(
        " ".join(
            str(person.get(field, "")).strip()
            for field in ("firstName", "middleInitial", "lastName")
            if str(person.get(field, "")).strip()
        )
    )


def fetch_sigchi_program_json(
    raw_dir: Path, *, spec: ConferenceSpec, year: int
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    """Read a SIGCHI conference's explicit Paper program records."""
    raw_index, index_source = official_raw_snapshot(
        raw_dir,
        filename=f"sigchi_{spec.key.lower()}_{year}_program_list.json",
        url=SIGCHI_PROGRAM_LIST_URL,
    )
    try:
        index_entries = json.loads(raw_index)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"SIGCHI program index is not valid JSON: {exc}") from exc
    if not isinstance(index_entries, list):
        raise RuntimeError("SIGCHI program index is not a list")
    matching_entries = [
        entry
        for entry in index_entries
        if isinstance(entry, dict)
        and isinstance(entry.get("conference"), dict)
        and str(entry["conference"].get("shortName", "")).casefold()
        == spec.key.casefold()
        and str(entry["conference"].get("year", "")) == str(year)
        and isinstance(entry.get("publicationInfo"), dict)
    ]
    if not matching_entries:
        raise RuntimeError(f"SIGCHI program index has no {spec.key} {year} entry")
    entry = max(
        matching_entries,
        key=lambda candidate: int(candidate["publicationInfo"].get("version", -1)),
    )
    conference = entry["conference"]
    publication = entry["publicationInfo"]
    conference_id = conference.get("id")
    version = publication.get("version")
    if conference_id is None or version is None:
        raise RuntimeError(f"SIGCHI program entry lacks id/version for {spec.key} {year}")
    program_url = (
        f"https://files.sigchi.org/conference/cache/{conference_id}/{version}/program"
    )
    raw_program, program_source = official_raw_snapshot(
        raw_dir,
        filename=f"sigchi_{spec.key.lower()}_{year}_program.json",
        url=program_url,
    )
    try:
        program = json.loads(raw_program)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"SIGCHI program data is not valid JSON: {exc}") from exc
    if not isinstance(program, dict):
        raise RuntimeError(f"SIGCHI program data is not an object for {spec.key} {year}")
    content_types = program.get("contentTypes", [])
    paper_type_ids = {
        item.get("id")
        for item in content_types
        if isinstance(item, dict)
        and str(item.get("name", "")).casefold() == "paper"
        and item.get("id") is not None
    }
    if not paper_type_ids:
        raise RuntimeError(f"SIGCHI program has no explicit Paper type for {spec.key} {year}")
    people_by_id = {
        person.get("id"): person
        for person in program.get("people", [])
        if isinstance(person, dict) and person.get("id") is not None
    }
    program_page_url = f"https://programs.sigchi.org/{spec.key.lower()}/{year}/program"
    rows: list[dict[str, Any]] = []
    rich_rows: list[dict[str, Any]] = []
    source_id = f"SIGCHI_PROGRAM:{spec.key}:{year}:{conference_id}:{version}"
    for content in program.get("contents", []):
        if not isinstance(content, dict):
            continue
        if content.get("typeId") not in paper_type_ids or content.get("isBreak"):
            continue
        record_id = content.get("id")
        title = clean_normalized_text(str(content.get("title", "")))
        if record_id is None or not title:
            continue
        authors = [
            sigchi_person_name(people_by_id.get(author.get("personId")))
            for author in content.get("authors", [])
            if isinstance(author, dict)
        ]
        authors = [author for author in authors if author]
        if not authors:
            continue
        affiliations = [
            clean_normalized_text(str(affiliation.get("institution", "")))
            for author in content.get("authors", [])
            if isinstance(author, dict)
            for affiliation in author.get("affiliations", [])
            if isinstance(affiliation, dict)
            and clean_normalized_text(str(affiliation.get("institution", "")))
        ]
        row = bibliographic_csv_row(
            spec=spec,
            year=year,
            source_type="official_sigchi_program",
            source_id=source_id,
            source_record_id=f"sigchi:{spec.key}:{year}:{record_id}",
            source_url=program_page_url,
            title=title,
            authors=authors,
            booktitle=f"{spec.display_name} {year}",
            track="Papers",
        )
        row["abstract"] = clean_normalized_text(str(content.get("abstract") or ""))
        row["author_institutions"] = "; ".join(dict.fromkeys(affiliations))
        rows.append(row)
        rich_rows.append({"normalized": row, "official_sigchi_content": content})
    rows, rich_rows = deduplicate_bibliographic_rows(rows, rich_rows)
    if not rows:
        raise RuntimeError(f"SIGCHI program has no parsed paper records for {spec.key} {year}")
    return rows, rich_rows, [index_source, program_source]


def official_template_urls(spec: ConferenceSpec, year: int) -> list[str]:
    replacements = {
        "year": str(year),
        "year_short": f"{year % 100:02d}",
        "vldb_volume": str(year + spec.dblp_volume_year_offset),
    }
    return [template.format(**replacements) for template in spec.official_url_templates]


def strip_html_fragment(value: str) -> str:
    text = re.sub(r"<[^>]+>", " ", value)
    return clean_normalized_text(html.unescape(re.sub(r"\s+", " ", text)).strip())


def extract_named_html_papers(
    text: str,
    *,
    spec: ConferenceSpec,
    year: int,
    source_url: str,
) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    """Extract the explicit paper-id/title/author list used by several venues."""
    pattern = re.compile(
        r"<li\b[^>]*>\s*"
        r"(?:<span\b[^>]*class=[\"'][^\"']*paper-id[^\"']*[\"'][^>]*>"
        r"(?P<paper_id>.*?)</span>\s*)?"
        r"(?P<title>.*?)\s*(?:—|&mdash;|&#8212;)\s*"
        r"<span\b[^>]*class=[\"'][^\"']*paper-authors[^\"']*[\"'][^>]*>"
        r"(?P<authors>.*?)</span>\s*</li>",
        flags=re.IGNORECASE | re.DOTALL,
    )
    rows: list[tuple[dict[str, Any], dict[str, Any]]] = []
    source_id = f"OFFICIAL_HTML:{spec.key}:{year}"
    for match in pattern.finditer(text):
        title = strip_html_fragment(match.group("title"))
        authors = [
            clean_normalized_text(author.strip())
            for author in re.split(r"\s+(?:and|,|·)\s+", strip_html_fragment(match.group("authors")))
            if author.strip()
        ]
        paper_id = strip_html_fragment(match.group("paper_id"))
        if not title or not authors:
            continue
        record_id = paper_id or hashlib.sha256(
            f"{title}\0{';'.join(authors)}".encode("utf-8")
        ).hexdigest()[:16]
        row = bibliographic_csv_row(
            spec=spec,
            year=year,
            source_type="official_accepted_list_html",
            source_id=source_id,
            source_record_id=f"official_html:{record_id}",
            source_url=source_url,
            title=title,
            authors=authors,
        )
        rows.append((row, {"normalized": row, "official_html_source": source_url}))
    return rows


def fetch_accepted_list_html(
    raw_dir: Path, *, spec: ConferenceSpec, year: int
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    """Try an official accepted-list page with a strict, non-guessing parser."""
    urls = official_template_urls(spec, year)
    if not urls:
        raise RuntimeError("no official accepted-list URL template is configured")
    errors: list[str] = []
    for index, url in enumerate(urls, start=1):
        try:
            raw, source = official_raw_snapshot(
                raw_dir, filename=f"official_accepted_list_{index:02d}.html", url=url
            )
        except RuntimeError as exc:
            errors.append(str(exc))
            continue
        pairs = extract_named_html_papers(
            raw.decode("utf-8", errors="replace"),
            spec=spec,
            year=year,
            source_url=url,
        )
        if pairs:
            rows, rich_rows = zip(*pairs)
            normalized_rows, normalized_rich = deduplicate_bibliographic_rows(
                list(rows), list(rich_rows)
            )
            return normalized_rows, normalized_rich, [source]
        errors.append(f"official page had no explicit paper-id/title/author records: {url}")
    raise RuntimeError("; ".join(errors))


def fetch_official_conference(
    raw_dir: Path, *, spec: ConferenceSpec, year: int
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    if spec.official_adapter == "acl_anthology":
        return fetch_acl_anthology(raw_dir, spec=spec, year=year)
    if spec.official_adapter == "cvf_openaccess":
        return fetch_cvf_openaccess(raw_dir, spec=spec, year=year)
    if spec.official_adapter == "ieee_vis_json":
        return fetch_ieee_vis_json(raw_dir, spec=spec, year=year)
    if spec.official_adapter == "sigmod_accepted_html":
        return fetch_sigmod_accepted_html(raw_dir, spec=spec, year=year)
    if spec.official_adapter == "researchr_accepted_html":
        return fetch_researchr_accepted_html(raw_dir, spec=spec, year=year)
    if spec.official_adapter == "sigir_accepted_html":
        return fetch_sigir_accepted_html(raw_dir, spec=spec, year=year)
    if spec.official_adapter == "icde_research_papers_html":
        return fetch_icde_research_papers_html(raw_dir, spec=spec, year=year)
    if spec.official_adapter == "acmmm_regular_papers_html":
        return fetch_acmmm_regular_papers_html(raw_dir, spec=spec, year=year)
    if spec.official_adapter == "sigchi_program_json":
        return fetch_sigchi_program_json(raw_dir, spec=spec, year=year)
    if spec.official_adapter == "accepted_list_html":
        return fetch_accepted_list_html(raw_dir, spec=spec, year=year)
    raise RuntimeError(f"unsupported official source adapter: {spec.official_adapter!r}")


def build_official_conference_outputs(
    output_root: Path,
    *,
    spec: ConferenceSpec,
    year: int,
) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
    conference_dir = output_root / str(year) / spec.key
    conference_dir.mkdir(parents=True, exist_ok=True)
    rows, rich_rows, sources = fetch_official_conference(
        conference_dir / "raw", spec=spec, year=year
    )
    file_stem = f"{spec.key}_{year}_accepted_papers"
    csv_path = conference_dir / f"{file_stem}.csv"
    jsonl_path = conference_dir / f"{file_stem}.jsonl"
    write_csv(csv_path, rows, CSV_FIELDS)
    write_jsonl(jsonl_path, rich_rows)
    selected_source = rows[0].get("source_type", "official_public_source") if rows else ""
    manifest = {
        "dataset": f"{spec.display_name} {year} official public paper list",
        "conference": spec.key,
        "conference_display_name": spec.display_name,
        "ccf": ccf_metadata(spec),
        "year": year,
        "collection_id": spec.collection_id(year),
        "source_kind": selected_source,
        "source_id": rows[0].get("source_id", "") if rows else "",
        "venue_id": "",
        "venue_group_url": "",
        "fetched_at_utc": utc_now(),
        "canonical_membership_rule": "Records in the selected official public paper source",
        "canonical_membership_endpoint": "; ".join(
            str(source.get("url", "")) for source in sources
        ),
        "counts": {"accepted_paper_count": len(rows)},
        "missing_field_counts": {
            field: sum(not row.get(field) for row in rows)
            for field in ("title", "authors", "abstract", "doi", "paper_url")
        },
        "virtual_stats": {},
        "warnings": [
            "Official source selected before DBLP. Availability depends on the venue's public release schedule."
        ],
        "sources": sources,
        "collection_status": "fetched_from_sources",
        "outputs": {"csv": str(csv_path), "jsonl": str(jsonl_path)},
    }
    manifest_path = conference_dir / "source_manifest.json"
    write_json(manifest_path, manifest)
    manifest["outputs"]["manifest"] = str(manifest_path)
    return manifest, rows, rich_rows


def dblp_collection_url(spec: ConferenceSpec) -> str:
    return f"https://dblp.org/db/{spec.dblp_collection}/"


def dblp_journal_issue_for_url(
    spec: ConferenceSpec, *, year: int, volume_url: str
) -> DblpJournalIssue | None:
    """Return the configured journal-issue filter for a direct DBLP XML URL."""
    for issue in spec.dblp_journal_issues:
        if issue.url(year) == volume_url:
            return issue
    return None


def matches_dblp_journal_issue(
    record: ET.Element, *, spec: ConferenceSpec, year: int, volume_url: str
) -> bool:
    """Limit a journal volume to the issue(s) that comprise one conference."""
    issue = dblp_journal_issue_for_url(spec, year=year, volume_url=volume_url)
    if issue is None:
        return True
    if issue.issue_numbers:
        record_number = xml_child_text(record, "number").casefold()
        allowed_numbers = {number.casefold() for number in issue.issue_numbers}
        if record_number not in allowed_numbers:
            return False
    allowed_publication_years = issue.allowed_publication_years(year)
    if allowed_publication_years:
        record_year = xml_child_text(record, "year")
        if record_year not in allowed_publication_years:
            return False
    return True


def dblp_volume_urls(
    raw_dir: Path, *, spec: ConferenceSpec, year: int
) -> tuple[list[str], list[dict[str, Any]]]:
    """Discover the DBLP volumes belonging to one conference year.

    Most venues use one predictable XML file.  ACL and SIGKDD have multiple
    proceedings volumes, so the conference index is parsed rather than relying
    on a hard-coded list of volume numbers.
    """
    if spec.dblp_journal_issues:
        volume_urls = [issue.url(year) for issue in spec.dblp_journal_issues]
        return volume_urls, [
            {
                "url": volume_url,
                "source": "DBLP configured journal issue",
                "issue_numbers": list(issue.issue_numbers),
                "publication_years": sorted(issue.allowed_publication_years(year)),
            }
            for volume_url, issue in zip(volume_urls, spec.dblp_journal_issues)
        ]

    base_url = dblp_collection_url(spec)
    index_url = base_url + "index.html"
    raw, headers = request_bytes(index_url)
    index_path = raw_dir / "dblp_collection_index.html"
    write_bytes(index_path, raw)
    source_meta = [{
        **headers,
        "source": "DBLP collection index",
        "sha256": sha256_bytes(raw),
        "saved_as": str(index_path),
    }]

    volume_number = year + spec.dblp_volume_year_offset
    escaped_collection = re.escape(spec.dblp_collection)
    escaped_prefix = re.escape(spec.dblp_volume_prefix)
    pattern = re.compile(
        rf"https?://dblp\.org/db/{escaped_collection}/"
        rf"{escaped_prefix}{volume_number}(?:-\d+)?\.html"
    )
    volume_urls = sorted({match.group(0)[:-5] + ".xml" for match in pattern.finditer(raw.decode("utf-8", errors="replace"))})
    if spec.dblp_volume_suffixes:
        allowed = {
            f"{base_url}{spec.dblp_volume_prefix}{volume_number}-{suffix}.xml"
            for suffix in spec.dblp_volume_suffixes
        }
        volume_urls = [url for url in volume_urls if url in allowed]
        # The index may lag while its direct volume XML is already public.
        if not volume_urls:
            volume_urls = sorted(allowed)
    if not volume_urls:
        volume_urls = [
            f"{base_url}{spec.dblp_volume_prefix}{volume_number}.xml"
        ]
    return volume_urls, source_meta


def xml_child_text(record: ET.Element, name: str) -> str:
    element = record.find(name)
    if element is None:
        return ""
    return clean_normalized_text("".join(element.itertext()).strip())


def is_bibliographic_paper_title(title: str, spec: ConferenceSpec) -> bool:
    """Reject obvious proceedings front matter and venue-specific non-papers."""
    normalized_title = clean_normalized_text(title).strip()
    if not normalized_title:
        return False
    if NON_PAPER_DBLP_TITLE.fullmatch(normalized_title):
        return False
    # Journal issue exports frequently add an editorial record whose title
    # includes the issue name rather than being simply ``Editorial``.
    if re.search(r"(?::\s*)?editorial\.?$", normalized_title, flags=re.IGNORECASE):
        return False
    return not any(
        re.search(pattern, normalized_title, flags=re.IGNORECASE)
        for pattern in spec.dblp_excluded_title_patterns
    )


def dblp_page_count(pages: str) -> int | None:
    """Return the inclusive length of a conventional DBLP page range."""
    match = re.search(r"(?P<first>\d+)\s*[-–]\s*(?P<last>\d+)", pages)
    if match is None:
        return None
    first = int(match.group("first"))
    last = int(match.group("last"))
    return last - first + 1 if last >= first else None


def normalize_dblp_record(
    record: ET.Element,
    *,
    spec: ConferenceSpec,
    year: int,
    volume_url: str,
) -> tuple[dict[str, Any], dict[str, Any]] | None:
    """Normalize a DBLP proceedings item into the same CSV schema as OpenReview."""
    record_type = record.tag.rsplit("}", 1)[-1]
    if record_type not in {"inproceedings", "article"}:
        return None
    if record_type == "article" and not (
        spec.dblp_journal_issues or spec.dblp_collection.startswith("journals/")
    ):
        return None
    if not matches_dblp_journal_issue(
        record, spec=spec, year=year, volume_url=volume_url
    ):
        return None
    record_key = record.get("key", "")
    title = xml_child_text(record, "title")
    if not record_key or not is_bibliographic_paper_title(title, spec):
        return None
    authors = [
        clean_normalized_text("".join(author.itertext()).strip())
        for author in record.findall("author")
    ]
    # Proceedings tables of contents also contain front matter and similar
    # non-paper records. Published research papers always have at least one
    # listed author, so omit metadata-only entries from the paper dataset.
    if not any(authors):
        return None
    electronic_editions = [
        clean_normalized_text("".join(item.itertext()).strip())
        for item in record.findall("ee")
    ]
    doi = xml_child_text(record, "doi")
    if not doi:
        doi_link = next((item for item in electronic_editions if "doi.org/" in item), "")
        doi = doi_link.rsplit("doi.org/", 1)[-1] if doi_link else ""
    paper_url = next((item for item in electronic_editions if item), "")
    source_url = f"https://dblp.org/rec/{record_key}"
    booktitle = xml_child_text(record, "booktitle") or xml_child_text(record, "journal")
    pages = xml_child_text(record, "pages")
    if spec.dblp_min_page_count:
        count = dblp_page_count(pages)
        if count is None or count < spec.dblp_min_page_count:
            return None
    csv_row: dict[str, Any] = {
        "conference": spec.key,
        "conference_display_name": spec.display_name,
        "ccf_abbreviation": spec.ccf_abbreviation,
        "ccf_category": spec.ccf_category,
        "ccf_type": spec.ccf_type,
        "ccf_professional_field": spec.ccf_professional_field,
        "year": year,
        "source_type": "dblp_proceedings",
        "source_id": spec.dblp_source_id(year),
        "source_record_id": record_key,
        "source_url": source_url,
        "doi": doi,
        "paper_url": paper_url,
        "booktitle": booktitle,
        "venueid": "",
        "openreview_id": "",
        "submission_number": "",
        "title": title,
        "presentation_type": "Published proceedings",
        "venue": booktitle,
        "track": "",
        "authors": "; ".join(author for author in authors if author),
        "author_ids": "",
        "author_institutions": "",
        "author_virtual_urls": "",
        "primary_area": "",
        "secondary_area": "",
        "topic": "",
        "keywords": "",
        "tldr": "",
        "abstract": "",
        "poster_session": "",
        "poster_start": "",
        "poster_end": "",
        "oral_session": "",
        "oral_start": "",
        "poster_position": "",
        "openreview_url": "",
        "pdf_url": paper_url if paper_url.lower().endswith(".pdf") else "",
        "virtual_url": "",
        "bibtex": "",
        "license": "",
        "openreview_cdate_ms": "",
        "openreview_mdate_ms": "",
        "openreview_tmdate_ms": "",
        "openreview_cdate_utc": "",
        "openreview_mdate_utc": "",
        "openreview_tmdate_utc": "",
        "virtual_event_id": "",
        "virtual_source_id": "",
    }
    raw_record = {
        "key": record_key,
        "type": record_type,
        "title": title,
        "authors": authors,
        "year": xml_child_text(record, "year"),
        "booktitle": booktitle,
        "pages": pages,
        "doi": doi,
        "electronic_editions": electronic_editions,
        "volume_url": volume_url,
    }
    return csv_row, {"normalized": csv_row, "dblp_record": raw_record}


def fetch_dblp_conference(
    raw_dir: Path, *, spec: ConferenceSpec, year: int
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    """Fetch published conference-volume records from DBLP's XML exports."""
    volume_urls, source_meta = dblp_volume_urls(raw_dir, spec=spec, year=year)
    csv_rows: list[dict[str, Any]] = []
    jsonl_rows: list[dict[str, Any]] = []
    for volume_url in volume_urls:
        raw, headers = request_bytes(volume_url)
        raw_path = raw_dir / volume_url.rsplit("/", 1)[-1]
        write_bytes(raw_path, raw)
        source_meta.append({
            **headers,
            "source": "DBLP volume XML",
            "volume_url": volume_url,
            "sha256": sha256_bytes(raw),
            "saved_as": str(raw_path),
        })
        try:
            root = ET.fromstring(raw)
        except ET.ParseError as exc:
            raise RuntimeError(f"DBLP returned invalid XML for {volume_url}: {exc}") from exc
        # DBLP's ``.xml`` table-of-contents export wraps records in ``<r>``
        # elements beneath ``<dblpcites>``; records are therefore descendants,
        # not direct children of the document root.
        for record in root.iter():
            normalized = normalize_dblp_record(
                record, spec=spec, year=year, volume_url=volume_url
            )
            if normalized is not None:
                row, rich = normalized
                csv_rows.append(row)
                jsonl_rows.append(rich)

    by_id = {row["source_record_id"]: row for row in csv_rows}
    rich_by_id = {
        row["normalized"]["source_record_id"]: row for row in jsonl_rows
    }
    csv_rows = sorted(by_id.values(), key=lambda row: (row["title"], row["source_record_id"]))
    jsonl_rows = [rich_by_id[row["source_record_id"]] for row in csv_rows]
    if not csv_rows:
        raise RuntimeError(
            f"DBLP returned no proceedings records for {spec.key} {year}. "
            "The proceedings may not be indexed yet or may use a different volume."
        )
    return csv_rows, jsonl_rows, source_meta


def fetch_dblp_conference_sample(
    raw_dir: Path, *, spec: ConferenceSpec, year: int
) -> tuple[dict[str, Any], dict[str, Any], list[dict[str, Any]]]:
    """Fetch just one real target-year record for bounded catalog validation."""
    volume_urls, source_meta = dblp_volume_urls(raw_dir, spec=spec, year=year)
    for volume_url in volume_urls:
        raw, headers = request_bytes(volume_url, timeout=45, retries=4)
        raw_path = raw_dir / volume_url.rsplit("/", 1)[-1]
        write_bytes(raw_path, raw)
        source_meta.append({
            **headers,
            "source": "DBLP volume XML",
            "volume_url": volume_url,
            "sha256": sha256_bytes(raw),
            "saved_as": str(raw_path),
        })
        try:
            root = ET.fromstring(raw)
        except ET.ParseError as exc:
            raise RuntimeError(f"DBLP returned invalid XML for {volume_url}: {exc}") from exc
        for record in root.iter():
            normalized = normalize_dblp_record(
                record, spec=spec, year=year, volume_url=volume_url
            )
            if normalized is None:
                continue
            row, rich = normalized
            record_year = str(rich.get("dblp_record", {}).get("year", ""))
            if record_year == str(year):
                return row, rich, source_meta
    raise RuntimeError(
        f"DBLP has no author-attributed {year} proceedings record for {spec.key}."
    )


def fetch_openreview_accepted_sample(
    raw_dir: Path,
    *,
    venue_id: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Fetch one exact accepted note without downloading the full venue set."""
    params = {
        "term": venue_id,
        "content": "venueid",
        "type": "exact",
        "venueid": venue_id,
        "limit": 1,
        "offset": 0,
        "count": "true",
        "sort": "tmdate:asc",
    }
    url = OPENREVIEW_SEARCH_URL + "?" + urlencode(params)
    payload, raw, headers = request_json(url)
    if not isinstance(payload, dict) or not isinstance(payload.get("notes"), list):
        raise RuntimeError(f"Unexpected OpenReview response for {venue_id}")
    notes = payload["notes"]
    if not notes:
        raise RuntimeError(f"OpenReview returned no accepted notes for {venue_id}")
    note = notes[0]
    if content_value(note.get("content", {}), "venueid") != venue_id:
        raise RuntimeError(f"OpenReview sample does not match exact venue ID {venue_id}")
    path = raw_dir / "openreview_sample.json"
    write_bytes(path, raw)
    return note, {
        **headers,
        "venue_id": venue_id,
        "reported_count": payload.get("count", ""),
        "sha256": sha256_bytes(raw),
        "saved_as": str(path),
    }


def rich_record_year(row: dict[str, Any], rich: dict[str, Any], default: int) -> str:
    """Return the source-declared publication year when one is available."""
    raw_dblp = rich.get("dblp_record") if isinstance(rich, dict) else None
    if isinstance(raw_dblp, dict) and raw_dblp.get("year"):
        return str(raw_dblp["year"])
    raw_bibtex = rich.get("official_bibtex") if isinstance(rich, dict) else None
    if isinstance(raw_bibtex, dict) and raw_bibtex.get("year"):
        return clean_bibtex_text(str(raw_bibtex["year"]))
    return str(row.get("year", default))


def fetch_conference_sample(
    raw_dir: Path,
    *,
    spec: ConferenceSpec,
    year: int,
) -> tuple[dict[str, Any], dict[str, Any], list[dict[str, Any]], dict[str, Any]]:
    """Retrieve one verifiable target-year paper using the normal source policy.

    This is intentionally separate from the full collection workflow. It is
    used by the CCF catalog validator to test all venues without persisting a
    full proceedings snapshot for every conference.
    """
    if spec.source_kind == "openreview":
        try:
            note, source = fetch_openreview_accepted_sample(
                raw_dir, venue_id=spec.venue_id(year)
            )
            row, rich = normalize_openreview_note(note, spec=spec, year=year)
            return row, rich, [source], {"selected_source": "openreview"}
        except RuntimeError as openreview_error:
            if not spec.allow_dblp_fallback:
                raise
            row, rich, sources = fetch_dblp_conference_sample(
                raw_dir, spec=spec, year=year
            )
            return row, rich, sources, {
                "selected_source": "dblp_fallback",
                "fallback_used": True,
                "openreview_error": str(openreview_error),
            }

    if spec.source_kind == "official_then_dblp":
        try:
            rows, rich_rows, sources = fetch_official_conference(
                raw_dir, spec=spec, year=year
            )
            for row, rich in zip(rows, rich_rows):
                if rich_record_year(row, rich, year) == str(year):
                    return row, rich, sources, {
                        "selected_source": row.get("source_type", "official"),
                        "fallback_used": False,
                    }
            raise RuntimeError("official source returned no target-year paper")
        except RuntimeError as official_error:
            row, rich, sources = fetch_dblp_conference_sample(
                raw_dir, spec=spec, year=year
            )
            return row, rich, sources, {
                "selected_source": "dblp_fallback",
                "fallback_used": True,
                "official_error": str(official_error),
            }

    if spec.source_kind == "dblp":
        row, rich, sources = fetch_dblp_conference_sample(
            raw_dir, spec=spec, year=year
        )
        return row, rich, sources, {"selected_source": "dblp_proceedings"}
    raise RuntimeError(f"Unsupported source kind for sample validation: {spec.source_kind}")


def build_dblp_conference_outputs(
    output_root: Path,
    *,
    spec: ConferenceSpec,
    year: int,
) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
    conference_dir = output_root / str(year) / spec.key
    conference_dir.mkdir(parents=True, exist_ok=True)
    csv_rows, jsonl_rows, source_meta = fetch_dblp_conference(
        conference_dir / "raw", spec=spec, year=year
    )
    file_stem = f"{spec.key}_{year}_accepted_papers"
    csv_path = conference_dir / f"{file_stem}.csv"
    jsonl_path = conference_dir / f"{file_stem}.jsonl"
    write_csv(csv_path, csv_rows, CSV_FIELDS)
    write_jsonl(jsonl_path, jsonl_rows)
    missing = {
        field: sum(not row.get(field) for row in csv_rows)
        for field in ("title", "authors", "abstract", "doi", "paper_url")
    }
    manifest = {
        "dataset": f"{spec.display_name} {year} published proceedings papers",
        "conference": spec.key,
        "conference_display_name": spec.display_name,
        "ccf": ccf_metadata(spec),
        "year": year,
        "collection_id": spec.collection_id(year),
        "source_kind": (
            "dblp_fallback"
            if spec.source_kind == "official_then_dblp" or spec.allow_dblp_fallback
            else "dblp_proceedings"
        ),
        "source_id": spec.dblp_source_id(year),
        "venue_id": "",
        "venue_group_url": dblp_collection_url(spec),
        "fetched_at_utc": utc_now(),
        "canonical_membership_rule": (
            "DBLP XML records in the configured conference proceedings volume(s)"
        ),
        "canonical_membership_endpoint": dblp_collection_url(spec),
        "counts": {
            "accepted_paper_count": len(csv_rows),
            "dblp_published_proceedings_records": len(csv_rows),
        },
        "missing_field_counts": missing,
        "virtual_stats": {},
        "warnings": [
            "This source is a published-proceedings bibliography, not a live "
            "submission decision feed. It can appear only after DBLP indexes "
            "the relevant volume.",
        ],
        "sources": source_meta,
        "collection_status": "fetched_from_sources",
        "outputs": {"csv": str(csv_path), "jsonl": str(jsonl_path)},
    }
    manifest_path = conference_dir / "source_manifest.json"
    write_json(manifest_path, manifest)
    manifest["outputs"]["manifest"] = str(manifest_path)
    return manifest, csv_rows, jsonl_rows


def build_official_then_dblp_outputs(
    output_root: Path,
    *,
    spec: ConferenceSpec,
    year: int,
) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
    """Use an official source first and make an explicit DBLP fallback audit trail."""
    try:
        return build_official_conference_outputs(output_root, spec=spec, year=year)
    except RuntimeError as official_error:
        if spec.requires_official_source:
            raise RuntimeError(
                f"No usable official accepted-paper list is available for "
                f"{spec.key} {year}; refusing to substitute delayed DBLP "
                f"proceedings data. Official-source error: {official_error}"
            ) from official_error
        manifest, rows, rich_rows = build_dblp_conference_outputs(
            output_root, spec=spec, year=year
        )
        manifest["source_attempts"] = [
            {
                "source": f"official:{spec.official_adapter or 'unconfigured'}",
                "status": "unavailable_or_unparseable",
                "error": str(official_error),
            },
            {"source": "dblp", "status": "selected_fallback"},
        ]
        manifest["warnings"].insert(
            0,
            "No usable official public paper list was available at collection time; DBLP was used as fallback.",
        )
        manifest_path = Path(str(manifest["outputs"]["manifest"]))
        write_json(manifest_path, manifest)
        return manifest, rows, rich_rows


def load_cached_conference_outputs(
    output_root: Path,
    *,
    spec: ConferenceSpec,
    year: int,
    include_rows: bool,
) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]] | None:
    """Return a complete local collection, or ``None`` when a refresh is needed.

    A cache is reusable only when its manifest identifies the same venue and its
    primary CSV/JSONL outputs exist.  The CSV is checked against the manifest
    count, which prevents a partial interrupted download from being treated as
    complete.
    """
    conference_dir = output_root / str(year) / spec.key
    manifest_path = conference_dir / "source_manifest.json"
    if not manifest_path.is_file():
        return None

    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(manifest, dict):
        return None
    if manifest.get("collection_status") == "no_public_accepted_papers":
        # A venue may publish decisions after an earlier empty check. Never
        # treat an empty snapshot as a complete cache.
        return None
    if manifest.get("conference") != spec.key or manifest.get("year") != year:
        return None
    manifest_collection_id = manifest.get("collection_id")
    manifest_source_id = manifest.get("source_id") or manifest.get("venue_id")
    if manifest_collection_id:
        if manifest_collection_id != spec.collection_id(year):
            return None
    elif spec.source_kind == "official_then_dblp":
        # A legacy DBLP-only cache must be reconsidered once, so that a newly
        # public official list can replace it.
        return None
    elif manifest_source_id != spec.source_id(year):
        return None

    outputs = manifest.get("outputs", {})
    counts = manifest.get("counts", {})
    if not isinstance(outputs, dict) or not isinstance(counts, dict):
        return None
    csv_path = Path(str(outputs.get("csv", "")))
    jsonl_path = Path(str(outputs.get("jsonl", "")))
    if not csv_path.is_file() or not jsonl_path.is_file():
        return None
    try:
        csv_rows = read_csv_rows(csv_path)
        expected_count = int(
            counts.get("accepted_paper_count", counts.get("openreview_current_accepted"))
        )
    except (OSError, RuntimeError, TypeError, ValueError):
        return None
    if len(csv_rows) != expected_count:
        return None
    manifest_source_kind = manifest.get("source_kind", spec.source_kind)
    record_id_field = (
        "openreview_id"
        if manifest_source_kind == "openreview"
        else "source_record_id"
    )
    if any(not row.get(record_id_field) for row in csv_rows):
        return None
    if len({row[record_id_field] for row in csv_rows}) != len(csv_rows):
        return None

    jsonl_rows: list[dict[str, Any]] = []
    if include_rows:
        try:
            jsonl_rows = read_jsonl_rows(jsonl_path)
        except (OSError, RuntimeError):
            return None
        if len(jsonl_rows) != expected_count:
            return None

    manifest.setdefault("outputs", {})["manifest"] = str(manifest_path)
    manifest["collection_status"] = "reused_local_snapshot"
    return manifest, csv_rows if include_rows else [], jsonl_rows


def build_conference_outputs(
    output_root: Path,
    *,
    spec: ConferenceSpec,
    year: int,
    page_size: int,
    use_iclr_virtual: bool,
    refresh: bool,
    include_rows: bool,
) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
    if not refresh:
        cached = load_cached_conference_outputs(
            output_root,
            spec=spec,
            year=year,
            include_rows=include_rows,
        )
        if cached is not None:
            cached_manifest = cached[0]
            if (
                spec.requires_official_source
                and cached_manifest.get("source_kind") == "dblp_fallback"
            ):
                # Core conferences must never silently reuse delayed DBLP data
                # as if it were a current accepted-paper list.
                cached = None
            elif (
                spec.allow_dblp_fallback
                and cached_manifest.get("source_kind") == "dblp_fallback"
            ):
                # Recheck OpenReview on subsequent runs so a later public
                # accepted list can replace an earlier DBLP fallback.
                cached = None
            elif (
                spec.source_kind == "official_then_dblp"
                and cached_manifest.get("source_kind") == "dblp_fallback"
            ):
                # Do not make a DBLP snapshot permanently authoritative. A
                # later run upgrades it automatically when the venue publishes
                # an official machine-readable list.
                try:
                    return build_official_conference_outputs(
                        output_root, spec=spec, year=year
                    )
                except RuntimeError:
                    return cached
            else:
                return cached

    if spec.source_kind == "official_then_dblp":
        return build_official_then_dblp_outputs(output_root, spec=spec, year=year)
    if spec.source_kind == "dblp":
        return build_dblp_conference_outputs(output_root, spec=spec, year=year)
    if spec.source_kind in {"dblp_stream", "crossref_journal"}:
        if not __package__:
            sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
        from code.fetch_venue_metadata import build_outputs
        return build_outputs(output_root, spec=spec, year=year)

    conference_dir = output_root / str(year) / spec.key
    conference_dir.mkdir(parents=True, exist_ok=True)
    raw_dir = conference_dir / "raw"
    fetched_at = utc_now()
    venue_id = spec.venue_id(year)
    venue_group_url = spec.group_url(year)

    openreview_error: str | None = None
    dblp_fallback_error: str | None = None
    try:
        openreview_notes, openreview_sources = fetch_openreview_accepted(
            raw_dir,
            venue_id=venue_id,
            page_size=page_size,
        )
    except RuntimeError as exc:
        if not spec.allow_dblp_fallback:
            raise
        openreview_notes, openreview_sources = [], []
        openreview_error = str(exc)

    if spec.allow_dblp_fallback and not openreview_notes:
        try:
            fallback_manifest, fallback_rows, fallback_rich_rows = (
                build_dblp_conference_outputs(
                    output_root, spec=spec, year=year
                )
            )
        except RuntimeError as exc:
            dblp_fallback_error = str(exc)
        else:
            fallback_manifest["source_attempts"] = [
                {
                    "source": "openreview",
                    "status": "unavailable_or_empty",
                    "error": openreview_error or "no accepted papers returned",
                },
                {"source": "dblp", "status": "selected_fallback"},
            ]
            fallback_manifest["warnings"].insert(
                0,
                "OpenReview was unavailable or empty; DBLP published proceedings were used as fallback.",
            )
            fallback_manifest_path = Path(str(fallback_manifest["outputs"]["manifest"]))
            write_json(fallback_manifest_path, fallback_manifest)
            return fallback_manifest, fallback_rows, fallback_rich_rows

    virtual_by_forum: dict[str, dict[str, Any]] = {}
    virtual_stats: dict[str, Any] = {}
    virtual_sources: list[dict[str, Any]] = []
    virtual_warning = ""

    if spec.supports_iclr_virtual and use_iclr_virtual:
        try:
            payload, abstracts, virtual_sources = fetch_iclr_virtual_sources(
                raw_dir, year
            )
            virtual_by_forum, virtual_stats = normalize_iclr_virtual(
                payload,
                abstracts,
                venue_group_url=venue_group_url,
            )
        except RuntimeError as exc:
            virtual_warning = (
                "ICLR Virtual enrichment was unavailable; OpenReview output is "
                f"still complete. Details: {exc}"
            )

    openreview_ids = {note.get("id") for note in openreview_notes if note.get("id")}
    virtual_ids = set(virtual_by_forum)
    only_openreview = sorted(openreview_ids - virtual_ids) if virtual_by_forum else []
    only_virtual = sorted(virtual_ids - openreview_ids) if virtual_by_forum else []
    overlap = openreview_ids & virtual_ids

    csv_rows: list[dict[str, Any]] = []
    jsonl_rows: list[dict[str, Any]] = []
    for note in openreview_notes:
        row, rich = normalize_openreview_note(
            note,
            spec=spec,
            year=year,
            virtual=virtual_by_forum.get(note.get("id", "")),
        )
        csv_rows.append(row)
        jsonl_rows.append(rich)

    file_stem = f"{spec.key}_{year}_accepted_papers"
    accepted_csv = conference_dir / f"{file_stem}.csv"
    accepted_jsonl = conference_dir / f"{file_stem}.jsonl"
    write_csv(accepted_csv, csv_rows, CSV_FIELDS)
    write_jsonl(accepted_jsonl, jsonl_rows)

    diff_csv: Path | None = None
    if virtual_by_forum:
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
                        "Present in ICLR Virtual main-conference snapshot but "
                        "not in the current exact OpenReview accepted set"
                    ),
                }
            )
        diff_csv = conference_dir / f"{spec.key}_{year}_source_differences.csv"
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
    venue_counts = Counter(row["venue"] for row in csv_rows)
    missing = {
        "title": sum(not row["title"] for row in csv_rows),
        "authors": sum(not row["authors"] for row in csv_rows),
        "abstract": sum(not row["abstract"] for row in csv_rows),
        "keywords": sum(not row["keywords"] for row in csv_rows),
        "pdf_url": sum(not row["pdf_url"] for row in csv_rows),
        "virtual_match": (
            sum(not row["virtual_event_id"] for row in csv_rows)
            if virtual_by_forum
            else None
        ),
    }
    counts: dict[str, Any] = {
        "accepted_paper_count": len(openreview_notes),
        "openreview_current_accepted": len(openreview_notes),
        "openreview_presentation_counts": dict(type_counts),
        "openreview_venue_counts": dict(venue_counts),
    }
    if virtual_by_forum:
        counts.update(
            {
                "virtual_main_conference": virtual_stats.get(
                    "main_conference_count", 0
                ),
                "source_overlap": len(overlap),
                "only_openreview": len(only_openreview),
                "only_virtual": len(only_virtual),
            }
        )

    warnings: list[str] = []
    if openreview_error:
        warnings.append(f"OpenReview collection failed; details: {openreview_error}")
    if dblp_fallback_error:
        warnings.append(f"DBLP fallback was unavailable; details: {dblp_fallback_error}")
    if not openreview_notes:
        warnings.append(
            "No accepted papers were returned. This may mean decisions are not "
            "public yet, the selected year predates this venue-ID convention, "
            "or the conference used a different OpenReview configuration."
        )
    if virtual_warning:
        warnings.append(virtual_warning)
    if only_openreview:
        warnings.append(
            f"{len(only_openreview)} current OpenReview papers lack ICLR Virtual "
            "metadata."
        )
    if only_virtual:
        warnings.append(
            f"{len(only_virtual)} ICLR Virtual papers are not in the current exact "
            "OpenReview accepted set; see the source-differences CSV."
        )

    reference = REFERENCE_COUNTS.get((spec.key, year))
    if reference:
        expected = reference.get("openreview_current_accepted")
        if expected is not None and len(openreview_notes) != expected:
            warnings.append(
                f"Accepted count differs from the stored reference snapshot "
                f"({expected}); the live OpenReview data may have changed."
            )
        expected_virtual = reference.get("virtual_main_conference")
        if (
            expected_virtual is not None
            and virtual_stats
            and virtual_stats.get("main_conference_count") != expected_virtual
        ):
            warnings.append(
                "ICLR Virtual count differs from the stored reference snapshot "
                f"({expected_virtual})."
            )

    outputs: dict[str, str] = {
        "csv": str(accepted_csv),
        "jsonl": str(accepted_jsonl),
    }
    if diff_csv is not None:
        outputs["source_differences_csv"] = str(diff_csv)

    manifest = {
        "dataset": f"{spec.display_name} {year} current accepted papers",
        "conference": spec.key,
        "conference_display_name": spec.display_name,
        "ccf": ccf_metadata(spec),
        "year": year,
        "collection_id": spec.collection_id(year),
        "source_kind": spec.source_kind,
        "source_id": spec.source_id(year),
        "venue_id": venue_id,
        "venue_group_url": venue_group_url,
        "fetched_at_utc": fetched_at,
        "canonical_membership_rule": (
            f"OpenReview Note content.venueid.value == {venue_id}"
        ),
        "canonical_membership_endpoint": OPENREVIEW_SEARCH_URL,
        "counts": counts,
        "missing_field_counts": missing,
        "virtual_stats": virtual_stats,
        "warnings": warnings,
        "source_differences": {
            "only_openreview_ids": only_openreview,
            "only_virtual_ids": only_virtual,
        },
        "sources": openreview_sources + virtual_sources,
        "collection_status": (
            "no_public_accepted_papers"
            if not openreview_notes
            else "fetched_from_sources"
        ),
        "outputs": outputs,
    }
    manifest_path = conference_dir / "source_manifest.json"
    write_json(manifest_path, manifest)
    manifest["outputs"]["manifest"] = str(manifest_path)
    return manifest, csv_rows, jsonl_rows


def build_combined_outputs(
    output_root: Path,
    *,
    year: int,
    requested_keys: list[str],
    manifests: dict[str, dict[str, Any]],
    csv_rows: list[dict[str, Any]],
    jsonl_rows: list[dict[str, Any]],
    failures: dict[str, str],
) -> dict[str, Any]:
    combined_dir = output_root / str(year) / "ALL"
    combined_dir.mkdir(parents=True, exist_ok=True)

    csv_rows = sorted(
        csv_rows,
        key=lambda row: (
            row.get("conference", ""),
            submission_sort_key(row.get("submission_number")),
            row.get("openreview_id", ""),
        ),
    )
    jsonl_rows = sorted(
        jsonl_rows,
        key=lambda row: (
            row.get("normalized", {}).get("conference", ""),
            submission_sort_key(
                row.get("normalized", {}).get("submission_number")
            ),
            row.get("normalized", {}).get("openreview_id", ""),
        ),
    )

    csv_path = combined_dir / f"ALL_{year}_accepted_papers.csv"
    jsonl_path = combined_dir / f"ALL_{year}_accepted_papers.jsonl"
    write_csv(csv_path, csv_rows, CSV_FIELDS)
    write_jsonl(jsonl_path, jsonl_rows)

    conference_counts = {
        key: manifest.get("counts", {}).get(
            "accepted_paper_count",
            manifest.get("counts", {}).get("openreview_current_accepted", 0),
        )
        for key, manifest in manifests.items()
    }
    manifest = {
        "dataset": f"Combined collected conference papers for {year}",
        "year": year,
        "fetched_at_utc": utc_now(),
        "conferences_requested": list(requested_keys),
        "conferences_completed": sorted(manifests),
        "conference_counts": conference_counts,
        "total_accepted": len(csv_rows),
        "failures": failures,
        "outputs": {
            "csv": str(csv_path),
            "jsonl": str(jsonl_path),
        },
        "conference_manifests": {
            key: manifest.get("outputs", {}).get("manifest", "")
            for key, manifest in manifests.items()
        },
    }
    manifest_path = combined_dir / "source_manifest.json"
    write_json(manifest_path, manifest)
    manifest["outputs"]["manifest"] = str(manifest_path)
    return manifest


def normalize_conference_argument(value: str) -> str:
    normalized = value.strip().upper()
    if normalized not in CONFERENCE_ALIASES:
        allowed = ", ".join(CONFERENCE_ALIASES)
        raise argparse.ArgumentTypeError(
            f"unsupported conference {value!r}; choose one of: {allowed}"
        )
    return CONFERENCE_ALIASES[normalized]


def valid_year(value: str) -> int:
    try:
        year = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("year must be an integer") from exc
    if year < 2000 or year > 2100:
        raise argparse.ArgumentTypeError("year must be between 2000 and 2100")
    return year


def valid_page_size(value: str) -> int:
    try:
        page_size = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("page size must be an integer") from exc
    if page_size < 1 or page_size > 1000:
        raise argparse.ArgumentTypeError("page size must be between 1 and 1000")
    return page_size


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Fetch OpenReview accepted papers or DBLP published proceedings "
            "for a supported conference."
        )
    )
    parser.add_argument(
        "--year",
        type=valid_year,
        required=True,
        help="Conference year, for example 2026.",
    )
    parser.add_argument(
        "--conference",
        type=normalize_conference_argument,
        required=True,
        metavar="{ICLR,ICML,NIPS,AAAI,ACL,CVPR,ICCV,WWW,RTSS,SIGKDD,ICDE,VLDB,ALL}",
        help="Conference to fetch. NEURIPS and KDD are accepted as aliases.",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=application_root() / "output",
        help="Output root. Results are written under YEAR/CONFERENCE.",
    )
    parser.add_argument(
        "--page-size",
        type=valid_page_size,
        default=1000,
        help="OpenReview page size (1-1000; ignored for DBLP; default: 1000).",
    )
    parser.add_argument(
        "--no-iclr-virtual",
        action="store_true",
        help="Skip best-effort ICLR virtual-program enrichment.",
    )
    parser.add_argument(
        "--refresh",
        action="store_true",
        help=(
            "Ignore a complete local collection for the same conference/year "
            "and download a fresh source snapshot."
        ),
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    output_root = args.output_dir.resolve()

    selected_keys = (
        list(CONFERENCE_SPECS)
        if args.conference == "ALL"
        else [args.conference]
    )

    manifests: dict[str, dict[str, Any]] = {}
    combined_csv_rows: list[dict[str, Any]] = []
    combined_jsonl_rows: list[dict[str, Any]] = []
    failures: dict[str, str] = {}

    for key in selected_keys:
        spec = CONFERENCE_SPECS[key]
        try:
            manifest, csv_rows, jsonl_rows = build_conference_outputs(
                output_root,
                spec=spec,
                year=args.year,
                page_size=args.page_size,
                use_iclr_virtual=not args.no_iclr_virtual,
                refresh=args.refresh,
                include_rows=args.conference == "ALL",
            )
            manifests[key] = manifest
            combined_csv_rows.extend(csv_rows)
            combined_jsonl_rows.extend(jsonl_rows)
        except RuntimeError as exc:
            failures[key] = str(exc)
            if args.conference != "ALL":
                print(
                    json.dumps(
                        {
                            "conference": key,
                            "year": args.year,
                            "error": str(exc),
                        },
                        ensure_ascii=False,
                        indent=2,
                    ),
                    file=sys.stderr,
                )
                return 1

    combined_manifest: dict[str, Any] | None = None
    if args.conference == "ALL":
        combined_manifest = build_combined_outputs(
            output_root,
            year=args.year,
            requested_keys=selected_keys,
            manifests=manifests,
            csv_rows=combined_csv_rows,
            jsonl_rows=combined_jsonl_rows,
            failures=failures,
        )

    summary = {
        "output_root": str(output_root),
        "year": args.year,
        "conference_argument": args.conference,
        "completed": {
            key: {
                "venue_id": manifest.get("venue_id", ""),
                "accepted": manifest["counts"].get(
                    "accepted_paper_count",
                    manifest["counts"].get("openreview_current_accepted", 0),
                ),
                "presentation_counts": manifest["counts"].get(
                    "openreview_presentation_counts", {}
                ),
                "collection_status": manifest.get("collection_status", ""),
                "warnings": manifest["warnings"],
                "outputs": manifest["outputs"],
            }
            for key, manifest in manifests.items()
        },
        "failures": failures,
        "combined": combined_manifest,
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
