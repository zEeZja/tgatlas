"""Regression checks for failures uncovered by the stability audit."""

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from src import db, exporter, file_io, pipeline, public_metadata, scraper
from src.models import Channel


class StabilityTests(unittest.TestCase):
    def test_failed_atomic_replace_preserves_previous_export(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "channels.json"
            target.write_text("previous", encoding="utf-8")
            with patch.object(file_io.os, "replace", side_effect=OSError("disk error")):
                with self.assertRaises(OSError):
                    file_io.atomic_write_text(target, "new")
            self.assertEqual(target.read_text(), "previous")
            self.assertEqual(list(Path(directory).iterdir()), [target])

    def test_atomic_replace_preserves_permissions_and_utf8(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "catalogue"
            target.write_text("old")
            target.chmod(0o640)
            file_io.atomic_write_text(target, "Мурманск")
            self.assertEqual(target.read_text(encoding="utf-8"), "Мурманск")
            if os.name != "nt":
                self.assertEqual(target.stat().st_mode & 0o777, 0o640)

    def test_batch_classification_reads_three_rule_files_once(self):
        row = dict(
            username="test",
            name="Новости науки",
            category="news",
            country="RU",
            description="",
            subscribers=10,
        )
        original = Path.read_text
        reads = []

        def read(path, *args, **kwargs):
            reads.append(path.name)
            return original(path, *args, **kwargs)

        with patch.object(Path, "read_text", read):
            rows = exporter.channel_rows([row.copy() for _ in range(100)])
        self.assertEqual(len(rows), 100)
        self.assertEqual(
            sorted(reads),
            sorted(
                [
                    "topic_overrides.json",
                    "geography_overrides.json",
                    "federal_channels.json",
                ]
            ),
        )
        self.assertEqual(rows[0]["subcategory"], "science")

    def test_invalid_channel_and_cursor_never_make_network_request(self):
        with patch.object(scraper, "_get_session") as session:
            for username, kwargs in [
                ("bad/path", {}),
                ("test", {"before": -1}),
                ("test", {"before": True}),
                ("test", {"before": 1, "profile": True}),
            ]:
                with self.assertRaises(ValueError):
                    scraper.fetch_channel_html(username, **kwargs)
            session.assert_not_called()

    def test_malformed_preview_does_not_clear_backfill_checkpoint(self):
        with patch.object(
            pipeline, "fetch_channel_html", return_value="<html>unavailable</html>"
        ):
            with self.assertRaises(scraper.ScrapeError):
                pipeline._fetch_pages(
                    Channel("test"), last_seen=100, checkpoint=(90, 20)
                )

    def test_metadata_sql_failure_rolls_back_whole_channel(self):
        conn = MagicMock()
        with (
            patch.object(
                pipeline,
                "parse_channel_meta",
                return_value={"name": "Test", "description": "", "subscribers": 1000},
            ),
            patch.object(pipeline, "upsert_channel", return_value=1),
            patch.object(
                pipeline, "update_channel_meta", side_effect=RuntimeError("SQL failed")
            ),
            patch.object(pipeline, "mark_parsed") as mark,
        ):
            with self.assertRaises(RuntimeError):
                pipeline._store_channel(conn, Channel("test"), "test", "html", [], None)
            conn.rollback.assert_called_once()
            conn.commit.assert_not_called()
            mark.assert_not_called()

    def test_empty_watermark_selection_avoids_full_posts_scan(self):
        with patch.object(db, "_cursor") as cursor:
            self.assertEqual(db.get_post_watermarks(None, []), {})
            cursor.assert_not_called()

    def test_unknown_metadata_selector_is_an_error(self):
        with (
            patch.object(public_metadata.db, "connect", return_value=MagicMock()),
            patch.object(public_metadata.db, "get_active_channels", return_value=[]),
        ):
            with self.assertRaises(ValueError):
                public_metadata.run(channels=["missing"])

    def test_optional_unused_credentials_do_not_break_base_import(self):
        env = dict(
            os.environ,
            TG_API_ID="not-configured",
            TG_PROXY_PORT="",
            PG_PORT="5432",
            REQUEST_TIMEOUT="20",
            DELAY_MIN="1",
            DELAY_MAX="2",
        )
        result = subprocess.run(
            [
                sys.executable,
                "-c",
                "from src import config; assert config.TG_API_ID == 0",
            ],
            env=env,
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_api_closes_connection_on_query_failure(self):
        try:
            import api
            from fastapi.testclient import TestClient
            import httpx  # noqa: F401
        except ImportError:
            self.skipTest("Install requirements-dev.txt for optional API checks")
        conn = MagicMock()
        with (
            patch.object(api.db, "connect", return_value=conn),
            patch.object(
                api.db,
                "get_channels_with_meta",
                side_effect=RuntimeError("database unavailable"),
            ),
        ):
            client = TestClient(api.app, raise_server_exceptions=False)
            self.assertEqual(client.get("/api/channels").status_code, 500)
            conn.close.assert_called_once()
            self.assertEqual(client.get("/api/health").json(), {"status": "ok"})

    def test_existing_metadata_is_not_reseeded_from_stale_config(self):
        with (
            patch.object(public_metadata.db, "connect", return_value=MagicMock()),
            patch.object(
                public_metadata.db, "get_known_usernames", return_value={"test"}
            ),
            patch.object(
                pipeline,
                "load_channels",
                return_value=[Channel("test", name="Old name")],
            ),
            patch.object(
                public_metadata.db,
                "get_active_channels",
                return_value=[{"id": 1, "username": "test"}],
            ),
            patch.object(
                public_metadata,
                "fetch_metadata",
                side_effect=RuntimeError("unavailable"),
            ),
            patch.object(public_metadata.db, "upsert_channel") as upsert,
        ):
            result = public_metadata.run(from_config=True, workers=1)
            self.assertEqual(result["failed"], 1)
            upsert.assert_not_called()

    def test_scrape_command_reports_partial_failure_to_automation(self):
        import main

        args = MagicMock()
        with patch.object(pipeline, "run", return_value={"total": 2, "failed": 1}):
            self.assertEqual(main.cmd_scrape(args), 1)
        with patch.object(pipeline, "run", return_value={"total": 2, "failed": 0}):
            self.assertEqual(main.cmd_scrape(args), 0)

    def test_tsne_perplexity_remains_valid_for_small_samples(self):
        from types import ModuleType
        from src import build_semantic_map

        manifold = ModuleType("sklearn.manifold")
        manifold.TSNE = MagicMock()
        with patch.dict(
            sys.modules,
            {"sklearn": ModuleType("sklearn"), "sklearn.manifold": manifold},
        ):
            for size in (2, 3, 5, 30):
                build_semantic_map._reduce_tsne([[1, 0, 1]] * size)
                value = manifold.TSNE.call_args.kwargs["perplexity"]
                self.assertGreater(value, 0)
                self.assertLess(value, size)
