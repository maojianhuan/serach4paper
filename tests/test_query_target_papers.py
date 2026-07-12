import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from code import query_target_papers as query


class TitleMatchingTests(unittest.TestCase):
    def test_matches_unordered_noncontiguous_stems(self):
        matches = query.match_title(
            "Detecting Time-Series Anomalies with Robust Representation Learning",
            "time serie anomal detect",
        )
        self.assertEqual(
            matches,
            [
                ("time", "time"),
                ("serie", "series"),
                ("anomal", "anomalies"),
                ("detect", "detecting"),
            ],
        )

    def test_does_not_match_when_one_required_token_is_absent(self):
        self.assertIsNone(
            query.match_title(
                "Time Series Forecasting with Foundation Models",
                "time serie anomal detect",
            )
        )

    def test_query_is_case_and_punctuation_insensitive(self):
        matches = query.match_title(
            "Anomaly-Detection for TIME series",
            "TIME, serie; anomal detect",
        )
        self.assertIsNotNone(matches)

    def test_boolean_query_supports_phrase_wildcard_and_not(self):
        query_text = '"time series" and (anomal* or outlier*) and not forecasting'
        self.assertIsNotNone(
            query.match_title(
                "Robust Outlier Detection for Multivariate Time-Series", query_text
            )
        )
        self.assertIsNone(
            query.match_title(
                "Time Series Anomaly Forecasting", query_text
            )
        )

    def test_boolean_query_parentheses_change_or_scope(self):
        self.assertIsNotNone(
            query.match_title(
                "Graph Anomaly Detection", "graph and (anomal* or vision)"
            )
        )
        self.assertIsNone(
            query.match_title(
                "Vision Transformer", "graph and (anomal* or vision)"
            )
        )

    def test_invalid_boolean_query_is_reported(self):
        with self.assertRaises(query.QuerySyntaxError):
            query.parse_query("time and (anomal*")


class QueryCollectionTests(unittest.TestCase):
    def test_collects_result_rows_for_each_matching_query(self):
        manifest = {
            "collection_status": "reused_local_snapshot",
            "counts": {"openreview_current_accepted": 2},
        }
        papers = [
            {
                "conference": "ICLR",
                "year": 2026,
                "openreview_id": "matching-paper",
                "submission_number": 1,
                "title": "Anomaly Detection in Multivariate Time Series",
            },
            {
                "conference": "ICLR",
                "year": 2026,
                "openreview_id": "other-paper",
                "submission_number": 2,
                "title": "Graph Representation Learning",
            },
        ]
        with tempfile.TemporaryDirectory() as temporary_directory:
            with patch.object(
                query.crawler,
                "build_conference_outputs",
                return_value=(manifest, papers, []),
            ) as collector:
                rows, summaries, failures = query.collect_query_results(
                    targets=({"conference": "ICLR", "year": 2026},),
                    queries=("time serie anomal detect",),
                    snapshot_output_root=Path(temporary_directory),
                )

        self.assertEqual(collector.call_count, 1)
        self.assertEqual(failures, {})
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["openreview_id"], "matching-paper")
        self.assertEqual(rows[0]["query"], "time serie anomal detect")
        self.assertEqual(summaries[0]["match_count"], 1)


if __name__ == "__main__":
    unittest.main()
