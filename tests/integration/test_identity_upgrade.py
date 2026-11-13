from pathlib import Path
import tempfile
from unittest.mock import patch

import psycopg2
import requests

from src import db, migrations, public_metadata
from src.models import Channel
from tests.integration.support import PostgresCase


class IdentityUpgradeTests(PostgresCase):
    def legacy(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory)
            for source in sorted(migrations.MIGRATIONS_DIR.glob("*.sql"))[:2]:
                (target / source.name).write_text(source.read_text(encoding="utf-8"), encoding="utf-8")
            migrations.apply_migrations(self.conn, target)

    def test_case_duplicates_merge_preserves_evidence_relations_and_checkpoint(self):
        self.legacy()
        first = self.query("INSERT INTO channels(username,name,description,subscribers,last_meta_at) VALUES ('Example','Old','old description',1000,'2026-11-01') RETURNING id")[0][0]
        second = self.query("INSERT INTO channels(username,name,description,subscribers,last_meta_at,telegram_id) VALUES ('example','New','',2000,'2026-11-10',123) RETURNING id")[0][0]
        self.query("INSERT INTO posts(channel_id,tg_message_id,url,text) VALUES (%s,1,'url','one'),(%s,1,'url','duplicate'),(%s,2,'url','two')", (first, second, second))
        original_second_post = self.query("SELECT id FROM posts WHERE channel_id=%s AND tg_message_id=2", (second,))[0][0]
        self.query("INSERT INTO channel_sources(channel_id,source_name,source_ref) VALUES (%s,'first','ref'),(%s,'second','ref')", (first, second))
        self.query("INSERT INTO channel_candidates(username,status,imported_channel_id) VALUES ('candidate','imported',%s)", (second,))
        self.query("INSERT INTO scrape_checkpoints VALUES (%s,80,20),(%s,100,40)", (first, second))
        self.conn.commit()
        self.assertEqual(db.init_schema(self.conn), ["0003", "0004"])
        self.assertEqual(self.query("SELECT id,username,name,description,subscribers,telegram_id FROM channels"), [(first, "example", "New", "", 2000, 123)])
        self.assertEqual(self.query("SELECT tg_message_id FROM posts ORDER BY tg_message_id"), [(1,), (2,)])
        self.assertEqual(self.query("SELECT id FROM posts WHERE tg_message_id=2"), [(original_second_post,)])
        self.assertEqual(self.query("SELECT DISTINCT channel_id FROM posts"), [(first,)])
        self.assertEqual(self.query("SELECT count(*) FROM channel_sources WHERE channel_id=%s", (first,)), [(2,)])
        self.assertEqual(self.query("SELECT imported_channel_id FROM channel_candidates"), [(first,)])
        self.assertEqual(db.get_scrape_checkpoints(self.conn), {first: (100, 20)})
        archive = self.query("SELECT records FROM channel_merge_archive")[0][0]
        self.assertEqual(len(archive["channels"]), 2)
        self.assertEqual(len(archive["posts"]), 3)
        self.assertEqual(len(archive["checkpoints"]), 2)
        self.assertEqual(db.init_schema(self.conn), [])

    def test_conflicting_telegram_ids_abort_without_changes(self):
        self.legacy()
        self.query("INSERT INTO channels(username,telegram_id) VALUES ('Example',1),('example',2)")
        self.conn.commit()
        with self.assertRaises(psycopg2.errors.RaiseException):
            db.init_schema(self.conn)
        self.assertEqual(self.query("SELECT username,telegram_id FROM channels ORDER BY id"), [("Example", 1), ("example", 2)])
        self.assertEqual(migrations.migration_status(self.conn)["current"], "0002")
        self.assertEqual(self.query("SELECT to_regclass('channel_merge_archive')"), [(None,)])

    def test_database_rejects_mixed_case_even_for_direct_sql(self):
        db.init_schema(self.conn)
        with db.transaction(self.conn):
            first = db.upsert_channel(self.conn, Channel("Example"))
            self.assertEqual(db.upsert_channel(self.conn, Channel("EXAMPLE")), first)
        with self.assertRaises(psycopg2.errors.CheckViolation):
            with db.transaction(self.conn):
                self.query("INSERT INTO channels(username) VALUES ('eXample')")
        self.assertEqual(self.query("SELECT count(*) FROM channels"), [(1,)])

    def test_failure_preserves_last_success_and_success_clears_error(self):
        db.init_schema(self.conn)
        with db.transaction(self.conn):
            channel_id = db.upsert_channel(self.conn, Channel("example"))
            db.update_channel_meta(self.conn, channel_id, name="Old", description="", subscribers=1234)
        checked = self.query("SELECT last_meta_at FROM channels")[0][0]
        with db.transaction(self.conn):
            db.mark_metadata_failure(self.conn, channel_id, "timeout")
        row = db.get_channels_with_meta(self.conn)[0]
        self.assertEqual(row["last_meta_at"], checked)
        self.assertEqual(row["subscribers"], 1234)
        self.assertEqual(row["last_meta_error"], "timeout")
        self.assertEqual(public_metadata.select_due([row], max_age_hours=0), [])
        with db.transaction(self.conn):
            db.update_channel_meta(self.conn, channel_id, name="Partial")
        self.assertEqual(self.query("SELECT last_meta_at,last_meta_error,subscribers FROM channels"), [(checked, "incomplete_metadata", 1234)])
        with db.transaction(self.conn):
            db.update_channel_meta(self.conn, channel_id, name="Full", description="", subscribers=2345)
        self.assertEqual(self.query("SELECT subscribers,last_meta_error FROM channels"), [(2345, None)])

    def test_refresh_restart_skips_recent_failure_and_force_retries(self):
        db.init_schema(self.conn)
        with db.transaction(self.conn):
            channel_id = db.upsert_channel(self.conn, Channel("example"))
            db.update_channel_meta(self.conn, channel_id, name="Old", description="", subscribers=1234)
            self.query("UPDATE channels SET last_meta_at=NOW()-INTERVAL '48 hours'")
        with (
            patch.object(public_metadata.db, "connect", side_effect=self.connect),
            patch.object(public_metadata, "fetch_metadata", side_effect=requests.Timeout("private URL")) as fetch,
        ):
            self.assertEqual(public_metadata.run(workers=1)["failed"], 1)
            self.assertEqual(public_metadata.run(workers=1)["skipped"], 1)
            self.assertEqual(fetch.call_count, 1)
        self.assertEqual(self.query("SELECT subscribers,last_meta_error FROM channels"), [(1234, "timeout")])
        self.conn.commit()
        with (
            patch.object(public_metadata.db, "connect", side_effect=self.connect),
            patch.object(public_metadata, "fetch_metadata", return_value=dict(name="New", description="New description", subscribers=2345)),
        ):
            self.assertEqual(public_metadata.run(workers=1, force=True)["updated"], 1)
        self.assertEqual(self.query("SELECT subscribers,last_meta_error FROM channels"), [(2345, None)])
