"""Editable batch runner and unit tests for ``fetch_openreview_accepted``.

Run this file directly to download every currently accepted paper for the
conference/year pairs in ``DEFAULT_TARGETS``.  Run the unit tests separately:

    python -m unittest test_fetch_openreview_accepted.py
"""

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


# ---------------------------------------------------------------------------
# User-editable batch settings
# ---------------------------------------------------------------------------
# Add, remove, or edit a conference/year pair here.  Supported conference
# values: ICLR, ICML, NIPS (or NEURIPS), AAAI, ACL, CVPR, ICCV, WWW, RTSS,
# SIGKDD (or KDD), ICDE, and VLDB. OpenReview venues use a live accepted set.
# Other venues first try their official public paper source and use DBLP only
# when that source is not yet public or cannot be parsed safely.
DEFAULT_TARGETS = (
    {"conference": "ICML", "year": 2026},
    {"conference": "ICLR", "year": 2026},
    {"conference": "NIPS", "year": 2025},
    {"conference": "ICML", "year": 2025},
    {"conference": "ICLR", "year": 2025},
)

# Output is organized as OUTPUT_ROOT/<year>/<conference>/.  This matches the
# fetcher's own default so direct and batch runs share the same local cache.
OUTPUT_ROOT = Path(__file__).resolve().parent / "output"

# OpenReview currently permits up to 1,000 notes per page.
PAGE_SIZE = 1000

# Set False to skip the optional ICLR Virtual metadata enrichment.
USE_ICLR_VIRTUAL = True

# Keep False to reuse a complete local snapshot for the same conference/year.
# Set True only when you intentionally want to query OpenReview again.
REFRESH_EXISTING = False

from code import fetch_openreview_accepted as crawler


def collection_arguments(target: dict[str, object]) -> list[str]:
    """Build crawler command-line arguments for one editable target."""
    arguments = [
        "--conference",
        str(target["conference"]),
        "--year",
        str(target["year"]),
        "--output-dir",
        str(OUTPUT_ROOT),
        "--page-size",
        str(PAGE_SIZE),
    ]
    if not USE_ICLR_VIRTUAL:
        arguments.append("--no-iclr-virtual")
    if REFRESH_EXISTING:
        arguments.append("--refresh")
    return arguments


def run_default_collection() -> int:
    """Download every paper for each conference/year pair at the top of file."""
    statuses: list[dict[str, object]] = []
    for target in DEFAULT_TARGETS:
        exit_code = crawler.main(collection_arguments(target))
        statuses.append({**target, "exit_code": exit_code})

    print(
        json.dumps(
            {
                "output_root": str(OUTPUT_ROOT.resolve()),
                "targets": statuses,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 1 if any(status["exit_code"] for status in statuses) else 0


class ConferenceConfigurationTests(unittest.TestCase):
    def test_default_targets(self):
        self.assertGreater(len(DEFAULT_TARGETS), 0)
        self.assertIn(
            {"conference": "ICLR", "year": 2026},
            DEFAULT_TARGETS,
        )
        self.assertIn(
            {"conference": "NIPS", "year": 2025},
            DEFAULT_TARGETS,
        )
        for target in DEFAULT_TARGETS:
            self.assertNotEqual(
                crawler.normalize_conference_argument(str(target["conference"])),
                "ALL",
            )
            self.assertIsInstance(crawler.valid_year(str(target["year"])), int)

    def test_collection_arguments(self):
        arguments = collection_arguments({"conference": "ICLR", "year": 2026})
        self.assertIn("ICLR", arguments)
        self.assertIn("2026", arguments)
        self.assertIn(str(OUTPUT_ROOT), arguments)
        self.assertNotIn("--refresh", arguments)

    def test_refresh_argument(self):
        with patch(__name__ + ".REFRESH_EXISTING", True):
            arguments = collection_arguments(
                {"conference": "ICLR", "year": 2026}
            )
        self.assertIn("--refresh", arguments)

    def test_venue_ids(self):
        self.assertEqual(
            crawler.CONFERENCE_SPECS["ICLR"].venue_id(2026),
            "ICLR.cc/2026/Conference",
        )
        self.assertEqual(
            crawler.CONFERENCE_SPECS["ICML"].venue_id(2025),
            "ICML.cc/2025/Conference",
        )
        self.assertEqual(
            crawler.CONFERENCE_SPECS["NIPS"].venue_id(2024),
            "NeurIPS.cc/2024/Conference",
        )

    def test_aliases(self):
        self.assertEqual(crawler.normalize_conference_argument("nips"), "NIPS")
        self.assertEqual(crawler.normalize_conference_argument("NeurIPS"), "NIPS")
        self.assertEqual(crawler.normalize_conference_argument("all"), "ALL")
        self.assertEqual(crawler.normalize_conference_argument("kdd"), "SIGKDD")

    def test_added_proceedings_conferences_are_configured(self):
        for conference in (
            "AAAI", "ACL", "CVPR", "ICCV", "RTSS", "SIGKDD", "ICDE"
        ):
            self.assertEqual(
                crawler.CONFERENCE_SPECS[conference].source_kind,
                "official_then_dblp",
            )
        self.assertEqual(crawler.CONFERENCE_SPECS["WWW"].source_kind, "openreview")
        self.assertEqual(crawler.CONFERENCE_SPECS["VLDB"].source_kind, "dblp")

    def test_presentation_classification(self):
        self.assertEqual(
            crawler.presentation_type_from_venue("NeurIPS 2025 Spotlight"),
            "Spotlight",
        )
        self.assertEqual(
            crawler.presentation_type_from_venue("ICLR 2026 Poster"),
            "Poster",
        )
        self.assertEqual(
            crawler.presentation_type_from_venue("ICML 2026"),
            "Accepted",
        )


class FetchTests(unittest.TestCase):
    def test_exact_pagination_and_normalization(self):
        venue_id = "ICML.cc/2026/Conference"
        notes = [
            {
                "id": "paper-a",
                "number": 1,
                "content": {
                    "venueid": {"value": venue_id},
                    "venue": {"value": "ICML 2026 Oral"},
                    "title": {"value": "Paper A"},
                    "authors": {"value": ["Alice"]},
                    "abstract": {"value": "Abstract A"},
                    "pdf": {"value": "/pdf?id=paper-a"},
                },
            },
            {
                "id": "paper-b",
                "number": 2,
                "content": {
                    "venueid": {"value": venue_id},
                    "venue": {"value": "ICML 2026 Poster"},
                    "title": {"value": "Paper B"},
                    "authors": {"value": ["Bob"]},
                },
            },
        ]
        responses = [
            (
                {"count": 2, "notes": [notes[0]]},
                json.dumps({"count": 2, "notes": [notes[0]]}).encode(),
                {"status": "200", "url": "page-1"},
            ),
            (
                {"count": 2, "notes": [notes[1]]},
                json.dumps({"count": 2, "notes": [notes[1]]}).encode(),
                {"status": "200", "url": "page-2"},
            ),
        ]
        with tempfile.TemporaryDirectory() as temporary_directory:
            with patch.object(crawler, "request_json", side_effect=responses):
                fetched, page_meta = crawler.fetch_openreview_accepted(
                    Path(temporary_directory), venue_id=venue_id, page_size=1
                )

        self.assertEqual([note["id"] for note in fetched], ["paper-a", "paper-b"])
        self.assertEqual(len(page_meta), 2)

        row, _ = crawler.normalize_openreview_note(
            fetched[0],
            spec=crawler.CONFERENCE_SPECS["ICML"],
            year=2026,
        )
        self.assertEqual(row["conference"], "ICML")
        self.assertEqual(row["presentation_type"], "Oral")
        self.assertEqual(row["pdf_url"], "https://openreview.net/pdf?id=paper-a")

    def test_dblp_proceedings_are_normalized_without_openreview(self):
        spec = crawler.CONFERENCE_SPECS["AAAI"]
        index_html = (
            b'<a href="https://dblp.org/db/conf/aaai/aaai2025.html">AAAI 2025</a>'
        )
        volume_xml = b"""<?xml version='1.0' encoding='UTF-8'?>
        <dblp><inproceedings key='conf/aaai/Test2025'>
        <author>Alice Example</author><author>Bob Example</author>
        <title>Time Series Anomaly Detection.</title><pages>1-10</pages>
        <year>2025</year><booktitle>AAAI 2025</booktitle>
        <doi>10.1234/example</doi><ee>https://doi.org/10.1234/example</ee>
        </inproceedings></dblp>"""
        responses = [
            (index_html, {"status": "200", "url": "index"}),
            (volume_xml, {"status": "200", "url": "volume"}),
        ]
        with tempfile.TemporaryDirectory() as temporary_directory:
            with patch.object(crawler, "request_bytes", side_effect=responses):
                manifest, rows, rich_rows = crawler.build_dblp_conference_outputs(
                    Path(temporary_directory),
                    spec=spec,
                    year=2025,
                )

        self.assertEqual(manifest["counts"]["accepted_paper_count"], 1)
        self.assertEqual(rows[0]["source_record_id"], "conf/aaai/Test2025")
        self.assertEqual(rows[0]["doi"], "10.1234/example")
        self.assertEqual(rows[0]["openreview_id"], "")
        self.assertEqual(rich_rows[0]["dblp_record"]["pages"], "1-10")

    def test_acl_uses_official_anthology_before_dblp(self):
        spec = crawler.CONFERENCE_SPECS["ACL"]
        long_bib = b"""@inproceedings{long-paper,
          title = {Long Paper Title},
          author = {Example, Alice and Example, Bob},
          booktitle = {ACL 2025},
          year = {2025},
          url = {https://aclanthology.org/2025.acl-long.1/}
        }"""
        short_bib = b"""@inproceedings{short-paper,
          title = {Short Paper Title},
          author = {Example, Carol},
          booktitle = {ACL 2025},
          year = {2025}
        }"""
        responses = [
            (long_bib, {"status": "200", "url": "long"}),
            (short_bib, {"status": "200", "url": "short"}),
        ]
        with tempfile.TemporaryDirectory() as temporary_directory:
            with patch.object(crawler, "request_bytes", side_effect=responses):
                manifest, rows, _ = crawler.build_conference_outputs(
                    Path(temporary_directory),
                    spec=spec,
                    year=2025,
                    page_size=1000,
                    use_iclr_virtual=False,
                    refresh=True,
                    include_rows=True,
                )

        self.assertEqual(manifest["source_kind"], "official_acl_anthology")
        self.assertEqual(manifest["counts"]["accepted_paper_count"], 2)
        self.assertTrue(all(row["source_type"] == "official_acl_anthology" for row in rows))

    def test_cvpr_uses_official_cvf_open_access_before_dblp(self):
        spec = crawler.CONFERENCE_SPECS["CVPR"]
        index_html = b'<a href="/CVPR2026?day=2026-06-05">Day 1</a>'
        day_html = b"""@InProceedings{Example_2026_CVPR,
          author = {Example, Alice and Example, Bob},
          title = {Official CVF Paper},
          booktitle = {Proceedings of CVPR},
          year = {2026}
        }
        @InProceedings{Example_2026_CVPR,
          author = {Example, Carol},
          title = {Another Official CVF Paper},
          booktitle = {Proceedings of CVPR},
          year = {2026}
        }"""
        responses = [
            (index_html, {"status": "200", "url": "index"}),
            (day_html, {"status": "200", "url": "day"}),
        ]
        with tempfile.TemporaryDirectory() as temporary_directory:
            with patch.object(crawler, "request_bytes", side_effect=responses):
                manifest, rows, _ = crawler.build_conference_outputs(
                    Path(temporary_directory),
                    spec=spec,
                    year=2026,
                    page_size=1000,
                    use_iclr_virtual=False,
                    refresh=True,
                    include_rows=True,
                )

        self.assertEqual(manifest["source_kind"], "official_cvf_openaccess")
        self.assertEqual(len(rows), 2)
        self.assertEqual(
            {row["title"] for row in rows},
            {"Official CVF Paper", "Another Official CVF Paper"},
        )
        self.assertTrue(all(row["source_type"] == "official_cvf_openaccess" for row in rows))


class BatchRunnerTests(unittest.TestCase):
    def test_default_runner_invokes_each_configured_target(self):
        with patch.object(crawler, "main", return_value=0) as mocked_main:
            exit_code = run_default_collection()

        self.assertEqual(exit_code, 0)
        self.assertEqual(mocked_main.call_count, len(DEFAULT_TARGETS))
        for index, target in enumerate(DEFAULT_TARGETS):
            self.assertEqual(
                mocked_main.call_args_list[index].args[0],
                collection_arguments(target),
            )


class LocalSnapshotTests(unittest.TestCase):
    def test_complete_local_snapshot_is_reused_without_fetching(self):
        spec = crawler.CONFERENCE_SPECS["ICLR"]
        year = 2026
        with tempfile.TemporaryDirectory() as temporary_directory:
            output_root = Path(temporary_directory)
            conference_dir = output_root / str(year) / spec.key
            csv_path = conference_dir / "ICLR_2026_accepted_papers.csv"
            jsonl_path = conference_dir / "ICLR_2026_accepted_papers.jsonl"
            crawler.write_csv(
                csv_path,
                [{"openreview_id": "cached-paper", "title": "Cached paper"}],
                crawler.CSV_FIELDS,
            )
            crawler.write_jsonl(
                jsonl_path,
                [{"normalized": {"openreview_id": "cached-paper"}}],
            )
            crawler.write_json(
                conference_dir / "source_manifest.json",
                {
                    "conference": spec.key,
                    "year": year,
                    "venue_id": spec.venue_id(year),
                    "counts": {"openreview_current_accepted": 1},
                    "outputs": {"csv": str(csv_path), "jsonl": str(jsonl_path)},
                    "warnings": [],
                },
            )
            with patch.object(crawler, "fetch_openreview_accepted") as fetch:
                manifest, csv_rows, jsonl_rows = crawler.build_conference_outputs(
                    output_root,
                    spec=spec,
                    year=year,
                    page_size=1000,
                    use_iclr_virtual=True,
                    refresh=False,
                    include_rows=True,
                )

        fetch.assert_not_called()
        self.assertEqual(manifest["collection_status"], "reused_local_snapshot")
        self.assertEqual(csv_rows[0]["openreview_id"], "cached-paper")
        self.assertEqual(jsonl_rows[0]["normalized"]["openreview_id"], "cached-paper")

    def test_official_source_upgrades_a_cached_dblp_fallback(self):
        spec = crawler.CONFERENCE_SPECS["ACL"]
        year = 2025
        with tempfile.TemporaryDirectory() as temporary_directory:
            output_root = Path(temporary_directory)
            conference_dir = output_root / str(year) / spec.key
            csv_path = conference_dir / "ACL_2025_accepted_papers.csv"
            jsonl_path = conference_dir / "ACL_2025_accepted_papers.jsonl"
            fallback_row = {
                "source_record_id": "dblp:old-paper",
                "title": "Cached DBLP paper",
            }
            crawler.write_csv(csv_path, [fallback_row], crawler.CSV_FIELDS)
            crawler.write_jsonl(jsonl_path, [{"normalized": fallback_row}])
            crawler.write_json(
                conference_dir / "source_manifest.json",
                {
                    "conference": spec.key,
                    "year": year,
                    "collection_id": spec.collection_id(year),
                    "source_kind": "dblp_fallback",
                    "source_id": spec.source_id(year),
                    "counts": {"accepted_paper_count": 1},
                    "outputs": {"csv": str(csv_path), "jsonl": str(jsonl_path)},
                    "warnings": [],
                },
            )
            official_manifest = {
                "source_kind": "official_acl_anthology",
                "counts": {"accepted_paper_count": 1},
            }
            official_rows = [{"source_record_id": "acl:new-paper", "title": "Official paper"}]
            with patch.object(
                crawler,
                "build_official_conference_outputs",
                return_value=(official_manifest, official_rows, []),
            ) as official_builder:
                manifest, rows, _ = crawler.build_conference_outputs(
                    output_root,
                    spec=spec,
                    year=year,
                    page_size=1000,
                    use_iclr_virtual=False,
                    refresh=False,
                    include_rows=True,
                )

        official_builder.assert_called_once()
        self.assertEqual(manifest["source_kind"], "official_acl_anthology")
        self.assertEqual(rows[0]["source_record_id"], "acl:new-paper")


if __name__ == "__main__":
    raise SystemExit(run_default_collection())
