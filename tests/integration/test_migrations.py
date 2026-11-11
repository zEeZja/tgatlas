from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import tempfile

import psycopg2

from src import db, migrations
from src.models import Channel, Post
from tests.integration.support import PostgresCase

ROOT = Path(__file__).resolve().parents[2]


class MigrationTests(PostgresCase):
    def seed(self):
        with db.transaction(self.conn):
            channel_id = db.upsert_channel(
                self.conn,
                Channel(
                    "old_channel",
                    name="Сохранённое имя",
                    telegram_id=123456,
                    description="Описание",
                    subscribers=9876,
                ),
            )
            db.insert_posts(
                self.conn,
                [
                    Post(
                        channel_id,
                        7,
                        "https://t.me/old_channel/7",
                        text="Сохранённый текст",
                    )
                ],
            )
            db.upsert_candidate(
                self.conn,
                {
                    "username": "candidate_old",
                    "name": "Кандидат",
                    "subscribers": 1500,
                    "description": "описание",
                },
                "fixture",
                "previous-release",
                status="validated",
            )
        return channel_id

    def test_empty_install_and_repeated_migration_preserve_records(self):
        self.assertEqual(db.init_schema(self.conn), ["0001", "0002"])
        channel_id = self.seed()
        with db.transaction(self.conn):
            db.save_scrape_checkpoint(self.conn, channel_id, (99, 7))
        before = self.snapshot()
        self.assertEqual(db.init_schema(self.conn), [])
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(db.get_scrape_checkpoints(self.conn), {channel_id: (99, 7)})
        self.assertFalse(migrations.check_schema_ready(self.conn)["pending"])

    def test_previous_release_upgrade_keeps_all_data_and_identity(self):
        # An actual earlier committed schema, never produced by the new runner.
        self.query(
            (ROOT / "tests/integration/fixtures/schema_legacy_621a14e.sql").read_text(
                encoding="utf-8"
            )
        )
        self.conn.commit()
        channel_id = self.seed()
        before = self.snapshot()
        self.assertEqual(
            migrations.migration_status(self.conn)["pending"], ["0001", "0002"]
        )
        db.init_schema(self.conn)
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(
            self.query("SELECT id FROM channels WHERE telegram_id=123456"),
            [(channel_id,)],
        )
        self.assertEqual(db.get_scrape_checkpoints(self.conn), {})
        migrations.check_schema_ready(self.conn)

    def test_current_unversioned_schema_is_adopted_without_changing_data(self):
        self.query((ROOT / "db/schema.sql").read_text(encoding="utf-8"))
        self.conn.commit()
        channel_id = self.seed()
        with db.transaction(self.conn):
            db.save_scrape_checkpoint(self.conn, channel_id, (70, 7))
        before = self.snapshot()
        db.init_schema(self.conn)
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(db.get_scrape_checkpoints(self.conn), {channel_id: (70, 7)})

    def test_failed_migration_rolls_back_ddl_and_history_together(self):
        db.init_schema(self.conn)
        self.seed()
        before = self.snapshot()
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory)
            for source in migrations.MIGRATIONS_DIR.glob("*.sql"):
                (target / source.name).write_text(
                    source.read_text(encoding="utf-8"), encoding="utf-8"
                )
            (target / "0003_failure.sql").write_text(
                "CREATE TABLE must_not_survive (id INT); INSERT INTO missing_table VALUES (1);",
                encoding="utf-8",
            )
            with self.assertRaises(psycopg2.errors.UndefinedTable):
                migrations.apply_migrations(self.conn, target)
        self.assertEqual(
            self.query("SELECT to_regclass('must_not_survive')"), [(None,)]
        )
        self.assertEqual(
            self.query("SELECT version FROM schema_migrations ORDER BY version"),
            [("0001",), ("0002",)],
        )
        self.assertEqual(self.snapshot(), before)

    def test_failed_initial_batch_leaves_no_partial_schema(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            (path / "0001_good.sql").write_text(
                "CREATE TABLE temporary_install(id INT);"
            )
            (path / "0002_bad.sql").write_text("SELECT * FROM missing_table;")
            with self.assertRaises(psycopg2.errors.UndefinedTable):
                migrations.apply_migrations(self.conn, path)
        self.assertEqual(
            self.query(
                "SELECT to_regclass('schema_migrations'),to_regclass('temporary_install')"
            ),
            [(None, None)],
        )

    def test_changed_applied_sql_is_rejected(self):
        db.init_schema(self.conn)
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory)
            for source in migrations.MIGRATIONS_DIR.glob("*.sql"):
                (target / source.name).write_text(
                    source.read_text(encoding="utf-8") + "\n-- changed\n",
                    encoding="utf-8",
                )
            with self.assertRaises(migrations.MigrationError):
                migrations.apply_migrations(self.conn, target)
        self.assertFalse(migrations.migration_status(self.conn)["pending"])

    def test_newer_or_inconsistent_database_cannot_be_silently_downgraded(self):
        db.init_schema(self.conn)
        self.query(
            "INSERT INTO schema_migrations(version,name,checksum) VALUES ('9999','future.sql','unknown')"
        )
        self.conn.commit()
        with self.assertRaises(migrations.MigrationError):
            db.init_schema(self.conn)
        with self.assertRaises(migrations.MigrationError):
            migrations.check_schema_ready(self.conn)

    def test_missing_history_entry_is_rejected(self):
        db.init_schema(self.conn)
        self.query("DELETE FROM schema_migrations WHERE version='0001'")
        self.conn.commit()
        with self.assertRaises(migrations.MigrationError):
            db.init_schema(self.conn)

    def test_concurrent_initializers_are_serialized(self):
        connections = [self.connect(), self.connect()]
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(migrations.apply_migrations, connections))
        self.assertEqual(sorted(results, key=len), [[], ["0001", "0002"]])
        self.assertEqual(self.query("SELECT COUNT(*) FROM schema_migrations"), [(2,)])

    def test_snapshot_and_migrations_produce_equivalent_application_schema(self):
        db.init_schema(self.conn)
        migrated = self.query(
            "SELECT table_name,column_name,data_type,is_nullable,column_default FROM information_schema.columns WHERE table_schema=current_schema() AND table_name<>'schema_migrations' ORDER BY table_name,ordinal_position"
        )
        # Schema-specific sequence defaults differ only by the selected schema.
        other_schema = self.schema + "_snapshot"
        self.query("CREATE SCHEMA " + other_schema)
        try:
            self.query("SET search_path TO " + other_schema)
            self.query((ROOT / "db/schema.sql").read_text(encoding="utf-8"))
            snapshot = self.query(
                "SELECT table_name,column_name,data_type,is_nullable,column_default FROM information_schema.columns WHERE table_schema=current_schema() ORDER BY table_name,ordinal_position"
            )
            normalize = lambda rows: [
                tuple(
                    value.replace(other_schema, self.schema)
                    if isinstance(value, str)
                    else value
                    for value in row
                )
                for row in rows
            ]
            self.assertEqual(normalize(snapshot), migrated)
        finally:
            self.conn.rollback()
