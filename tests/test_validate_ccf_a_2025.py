"""Unit tests for the CCF A catalog registration and sample validator."""

from __future__ import annotations

import json
import tempfile
import unittest
from http.client import RemoteDisconnected
from pathlib import Path
from unittest.mock import patch

from code import fetch_openreview_accepted as crawler
from code import validate_ccf_a_2025 as validator


class CCFACatalogTests(unittest.TestCase):
    def test_catalog_has_all_58_a_conferences_and_registered_specs(self):
        entries = crawler.CCF_A_CATALOG["conferences"]
        self.assertEqual(len(entries), 58)
        self.assertEqual(len(crawler.CCF_A_BY_KEY), 58)
        self.assertTrue(all(entry["category"] == "A" for entry in entries))
        self.assertTrue(all(entry["type"] == "会议" for entry in entries))
        self.assertTrue(set(crawler.CCF_A_BY_KEY).issubset(crawler.CONFERENCE_SPECS))
        self.assertEqual(
            crawler.normalize_conference_argument("ACM SIGOPS ATC"), "ATC"
        )
        self.assertEqual(crawler.normalize_conference_argument("IEEE VIS"), "IEEE_VIS")

    def test_special_source_keeps_ccf_metadata(self):
        spec = crawler.CONFERENCE_SPECS["IEEE_VIS"]
        self.assertEqual(spec.official_adapter, "ieee_vis_json")
        self.assertEqual(spec.ccf_abbreviation, "IEEE VIS")
        self.assertEqual(spec.ccf_category, "A")
        self.assertEqual(spec.ccf_type, "会议")


class IEEEVisAdapterTests(unittest.TestCase):
    def test_only_full_main_program_papers_are_collected(self):
        raw = json.dumps(
            [
                {
                    "id": "full-1",
                    "title": "A Full Paper",
                    "authors": [{"name": "Alice Example"}],
                    "paper_type": "full",
                    "event_id": "v-full",
                    "doi": "10.1109/example",
                    "keywords": ["visualization"],
                },
                {
                    "id": "short-1",
                    "title": "A Short Paper",
                    "authors": [{"name": "Bob Example"}],
                    "paper_type": "short",
                    "event_id": "v-short",
                },
                {
                    "id": "invited-1",
                    "title": "An Invited Paper",
                    "authors": [{"name": "Carol Example"}],
                    "paper_type": "invited",
                    "event_id": "v-full",
                },
            ]
        ).encode("utf-8")
        with tempfile.TemporaryDirectory() as temporary_directory:
            with patch.object(
                crawler,
                "request_bytes",
                return_value=(raw, {"status": "200", "url": "official-json"}),
            ):
                rows, rich_rows, sources = crawler.fetch_official_conference(
                    Path(temporary_directory),
                    spec=crawler.CONFERENCE_SPECS["IEEE_VIS"],
                    year=2025,
                )

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["title"], "A Full Paper")
        self.assertEqual(rows[0]["source_type"], "official_ieee_vis_program")
        self.assertEqual(rows[0]["ccf_abbreviation"], "IEEE VIS")
        self.assertEqual(rich_rows[0]["official_ieee_vis_record"]["id"], "full-1")
        self.assertEqual(len(sources), 1)


class OfficialListAndJournalFilterTests(unittest.TestCase):
    def test_sigir_parser_keeps_only_the_full_paper_section(self):
        raw = b"""
        <h2 id="full-papers">Full Papers</h2>
        <ul><li class="accepted-paper-item">
          <span class="accepted-paper-title">Full Paper</span>
          <span class="accepted-paper-author">Alice Example, Bob Example</span>
        </li></ul>
        <h2 id="short-papers">Short Papers</h2>
        <ul><li class="accepted-paper-item">
          <span class="accepted-paper-title">Short Paper</span>
          <span class="accepted-paper-author">Carol Example</span>
        </li></ul>
        """
        with tempfile.TemporaryDirectory() as temporary_directory:
            with patch.object(
                crawler,
                "request_bytes",
                return_value=(raw, {"status": "200", "url": "sigir-official"}),
            ):
                rows, _, _ = crawler.fetch_official_conference(
                    Path(temporary_directory),
                    spec=crawler.CONFERENCE_SPECS["SIGIR"],
                    year=2025,
                )
        self.assertEqual([row["title"] for row in rows], ["Full Paper"])
        self.assertEqual(rows[0]["track"], "Full Papers")

    def test_numbered_icde_official_parser(self):
        raw = b"""
        <h1>Research Papers</h1>
        <p>42 | A Research Paper</p>
        <p>Alice Example (Example University); Bob Example (Example Lab)</p>
        """
        with tempfile.TemporaryDirectory() as temporary_directory:
            with patch.object(
                crawler,
                "request_bytes",
                return_value=(raw, {"status": "200", "url": "icde-official"}),
            ):
                rows, _, _ = crawler.fetch_official_conference(
                    Path(temporary_directory),
                    spec=crawler.CONFERENCE_SPECS["ICDE"],
                    year=2025,
                )
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["title"], "A Research Paper")
        self.assertEqual(rows[0]["authors"], "Alice Example; Bob Example")

    def test_journal_issue_filter_skips_editorial_and_wrong_issue(self):
        spec = crawler.ConferenceSpec(
            key="TEST",
            display_name="Test",
            source_kind="dblp",
            dblp_collection="journals/test",
            dblp_volume_prefix="test",
            dblp_journal_issues=(
                crawler.DblpJournalIssue(
                    "https://example.test/test{journal_volume}.xml",
                    journal_volume_year_offset=-2023,
                    issue_numbers=("FSE",),
                ),
            ),
        )
        editorial = crawler.ET.fromstring(
            "<article key='journals/test/editorial'><author>Editor</author>"
            "<title>Test V2: Editorial.</title><number>FSE</number><year>2025</year>"
            "</article>"
        )
        paper = crawler.ET.fromstring(
            "<article key='journals/test/paper'><author>Alice</author>"
            "<title>Research Paper.</title><number>FSE</number><year>2025</year>"
            "</article>"
        )
        other_issue = crawler.ET.fromstring(
            "<article key='journals/test/other'><author>Alice</author>"
            "<title>Other Issue Paper.</title><number>ISSTA</number><year>2025</year>"
            "</article>"
        )
        volume_url = "https://example.test/test2.xml"
        self.assertIsNone(
            crawler.normalize_dblp_record(
                editorial, spec=spec, year=2025, volume_url=volume_url
            )
        )
        self.assertIsNotNone(
            crawler.normalize_dblp_record(
                paper, spec=spec, year=2025, volume_url=volume_url
            )
        )
        self.assertIsNone(
            crawler.normalize_dblp_record(
                other_issue, spec=spec, year=2025, volume_url=volume_url
            )
        )

    def test_minimum_page_filter_excludes_short_non_paper_records(self):
        spec = crawler.ConferenceSpec(
            key="TEST",
            display_name="Test",
            source_kind="dblp",
            dblp_collection="conf/test",
            dblp_volume_prefix="test",
            dblp_min_page_count=6,
        )
        short_record = crawler.ET.fromstring(
            "<inproceedings key='conf/test/short'><author>Alice</author>"
            "<title>Demo: Short Record.</title><pages>1-2</pages><year>2025</year>"
            "</inproceedings>"
        )
        long_record = crawler.ET.fromstring(
            "<inproceedings key='conf/test/long'><author>Alice</author>"
            "<title>Long Research Record.</title><pages>6-20</pages><year>2025</year>"
            "</inproceedings>"
        )
        self.assertIsNone(
            crawler.normalize_dblp_record(
                short_record, spec=spec, year=2025, volume_url="https://example.test/test.xml"
            )
        )
        self.assertIsNotNone(
            crawler.normalize_dblp_record(
                long_record, spec=spec, year=2025, volume_url="https://example.test/test.xml"
            )
        )


class ValidationFallbackTests(unittest.TestCase):
    def test_fm_uses_documented_2024_fallback_year(self):
        sample_row = {
            "title": "Formal Methods Sample",
            "authors": "Alice Example",
            "source_url": "https://dblp.org/rec/conf/fm/example",
            "source_record_id": "conf/fm/example",
            "year": 2024,
        }
        sample_rich = {"dblp_record": {"year": "2024"}}
        sample_sources = [{"url": "https://dblp.org/db/conf/fm/fm2024.xml"}]
        with patch.object(
            crawler,
            "fetch_conference_sample",
            return_value=(
                sample_row,
                sample_rich,
                sample_sources,
                {"selected_source": "dblp_fallback", "fallback_used": True},
            ),
        ) as sample_fetch:
            result = validator.validate_one(crawler.CONFERENCE_SPECS["FM"], 2025)

        self.assertEqual(sample_fetch.call_args.kwargs["year"], 2024)
        self.assertEqual(result["status"], "verified_fallback_year")
        self.assertEqual(result["fetched_year"], 2024)
        self.assertEqual(result["year_fallback"], "true")

    def test_transport_disconnect_is_reported_as_a_runtime_error(self):
        with patch.object(
            crawler, "urlopen", side_effect=RemoteDisconnected("closed")
        ):
            with self.assertRaisesRegex(RuntimeError, "Failed to fetch"):
                crawler.request_bytes("https://example.invalid/", retries=1)


if __name__ == "__main__":
    unittest.main()
