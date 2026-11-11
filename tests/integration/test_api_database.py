from unittest.mock import patch

import importlib.util
import unittest
import psycopg2

API_AVAILABLE = all(
    importlib.util.find_spec(name) is not None for name in ("fastapi", "httpx")
)
if API_AVAILABLE:
    from fastapi.testclient import TestClient
    import api
from src import db, migrations
from src.models import Channel
from tests.integration.support import PostgresCase


@unittest.skipUnless(API_AVAILABLE, "Install requirements/locked-dev.txt for API tests")
class ApiDatabaseTests(PostgresCase):
    def client(self):
        # Only the connection factory is redirected; all queries use PostgreSQL.
        factory = patch.object(api.db, "connect", side_effect=self.connect)
        factory.start()
        self.addCleanup(factory.stop)
        return TestClient(api.app)

    def test_ready_is_503_before_migration_and_200_after(self):
        client = self.client()
        self.assertEqual(client.get("/api/health").status_code, 200)
        self.assertEqual(client.get("/api/ready").status_code, 503)
        self.assertEqual(
            self.query("SELECT to_regclass('schema_migrations')"), [(None,)]
        )
        self.conn.rollback()
        db.init_schema(self.conn)
        response = client.get("/api/ready")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(),
            {"status": "ready", "database": "ok", "schema_version": "0002"},
        )
        self.assertTrue(all(conn.closed for conn in self.connections[1:]))

    def test_missing_application_table_is_not_ready_despite_current_history(self):
        db.init_schema(self.conn)
        self.query("DROP TABLE posts")
        self.conn.commit()
        response = self.client().get("/api/ready")
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json(), {"status": "not_ready"})

    def test_changed_history_is_not_ready(self):
        db.init_schema(self.conn)
        self.query(
            "UPDATE schema_migrations SET checksum='changed' WHERE version='0001'"
        )
        self.conn.commit()
        self.assertEqual(self.client().get("/api/ready").status_code, 503)

    def test_database_connection_failure_keeps_liveness_up(self):
        client = TestClient(api.app)

        # Real libpq connection failure, not a mocked exception.
        def offline():
            return psycopg2.connect(
                host="127.0.0.1", port=1, dbname="unavailable", connect_timeout=1
            )

        with patch.object(api.db, "connect", side_effect=offline):
            self.assertEqual(client.get("/api/health").json(), {"status": "ok"})
            response = client.get("/api/ready")
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json(), {"status": "not_ready"})

    def test_api_reads_and_classifies_real_database_metadata(self):
        db.init_schema(self.conn)
        with db.transaction(self.conn):
            channel_id = db.upsert_channel(
                self.conn,
                Channel(
                    "science_test",
                    name="Новости науки",
                    category="news",
                    country="RU",
                    subcategory="general",
                ),
            )
            db.update_channel_meta(
                self.conn,
                channel_id,
                name="Новости науки",
                description="Научные открытия",
                subscribers=12345,
            )
        response = self.client().get("/api/channels")
        self.assertEqual(response.status_code, 200)
        rows = response.json()["channels"]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["subscribers"], 12345)
        self.assertEqual(rows[0]["subcategory"], "science")
        self.assertEqual(self.query("SELECT subcategory FROM channels"), [("general",)])
        self.assertFalse(migrations.migration_status(self.conn)["pending"])
