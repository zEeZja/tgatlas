from datetime import datetime, timedelta, timezone
import unittest
from unittest.mock import MagicMock, patch

from src import exporter, public_metadata


class CatalogueIntegrityTests(unittest.TestCase):
    def test_export_rejects_case_only_duplicates_before_loading_rules(self):
        with patch.object(exporter, "load_classification_rules") as rules:
            with self.assertRaisesRegex(ValueError, "username"):
                exporter.channel_rows(
                    [{"username": "Example"}, {"username": "example"}]
                )
            rules.assert_not_called()

    def test_export_preserves_success_and_attempt_dates(self):
        now = datetime.now(timezone.utc)
        row = dict(
            username="example",
            name="Example",
            category="news",
            country="RU",
            description="",
            subscribers=1234,
            last_meta_at=now,
            last_meta_attempt_at=now + timedelta(hours=1),
            last_meta_error="timeout",
        )
        exported = exporter._channel_to_row(row)
        self.assertEqual(exported["last_meta_at"], now.isoformat())
        self.assertEqual(
            exported["last_meta_attempt_at"], row["last_meta_attempt_at"].isoformat()
        )
        self.assertEqual(exported["last_meta_error"], "timeout")
        self.assertIsNone(exported["last_parsed_at"])


class MetadataSchedulingTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 11, 12, tzinfo=timezone.utc)
        self.rows = [
            dict(id=1, username="fresh", last_meta_at=self.now - timedelta(hours=2)),
            dict(id=2, username="stale", last_meta_at=self.now - timedelta(hours=25)),
            dict(id=3, username="unknown", last_meta_at=None),
            dict(
                id=4,
                username="retry",
                last_meta_at=None,
                last_meta_attempt_at=self.now - timedelta(minutes=10),
                last_meta_error="timeout",
            ),
        ]

    def test_oldest_first_skips_fresh_and_recent_failures(self):
        due = public_metadata.select_due(self.rows, now=self.now)
        self.assertEqual([r["id"] for r in due], [3, 2])

    def test_limit_and_force(self):
        self.assertEqual(
            public_metadata.select_due(self.rows, now=self.now, limit=1)[0]["id"], 3
        )
        self.assertEqual(
            len(public_metadata.select_due(self.rows, now=self.now, force=True)), 4
        )

    def test_retry_after_cooldown_and_exact_freshness_boundary(self):
        due = public_metadata.select_due(self.rows, now=self.now + timedelta(hours=22))
        self.assertEqual({r["id"] for r in due}, {1, 2, 3, 4})

    def test_invalid_intervals(self):
        for value in (-1, float("nan"), float("inf")):
            with self.assertRaises(ValueError):
                public_metadata.select_due([], max_age_hours=value)

    def test_invalid_limits_and_retry_intervals(self):
        for value in (-1, float("nan"), float("inf")):
            with self.assertRaises(ValueError):
                public_metadata.select_due([], retry_after_hours=value)
        for value in (-1, 0, 1.5, True):
            with self.assertRaises(ValueError):
                public_metadata.select_due([], limit=value)

    def test_invalid_or_future_success_time_is_not_treated_as_fresh(self):
        rows = [
            dict(username="invalid", last_meta_at="bad"),
            dict(username="future", last_meta_at=self.now + timedelta(days=2)),
        ]
        self.assertEqual(len(public_metadata.select_due(rows, now=self.now)), 2)

    def test_cli_exposes_metadata_scheduling_options(self):
        from main import build_parser

        args = build_parser().parse_args(
            [
                "refresh-metadata",
                "--force",
                "--limit",
                "10",
                "--max-age-hours",
                "12",
                "--retry-after-hours",
                "2",
            ]
        )
        self.assertTrue(args.force)
        self.assertEqual(
            (args.limit, args.max_age_hours, args.retry_after_hours), (10, 12, 2)
        )

    def test_no_due_rows_is_success_and_does_not_request_telegram(self):
        with (
            patch.object(public_metadata.db, "connect", return_value=MagicMock()),
            patch.object(
                public_metadata.db,
                "get_active_channels",
                return_value=[
                    dict(
                        id=1, username="fresh", last_meta_at=datetime.now(timezone.utc)
                    )
                ],
            ),
            patch.object(public_metadata, "fetch_metadata") as fetch,
        ):
            result = public_metadata.run(workers=1)
        self.assertEqual(
            result, dict(total=1, updated=0, failed=0, attempted=0, skipped=1)
        )
        fetch.assert_not_called()

    def test_failure_state_is_stored_without_exposing_exception_details(self):
        conn = MagicMock()
        with (
            patch.object(public_metadata.db, "connect", return_value=conn),
            patch.object(
                public_metadata.db,
                "get_active_channels",
                return_value=[dict(id=1, username="example")],
            ),
            patch.object(
                public_metadata,
                "fetch_metadata",
                side_effect=RuntimeError("private details"),
            ),
            patch.object(public_metadata.db, "mark_metadata_failure") as failure,
            patch.object(public_metadata.db, "update_channel_meta") as update,
        ):
            result = public_metadata.run(workers=1)
        failure.assert_called_once_with(conn, 1, "update_failed")
        update.assert_not_called()
        self.assertEqual(result["failed"], 1)
