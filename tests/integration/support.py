import os
import unittest
import uuid

import psycopg2
from psycopg2 import sql

TEST_DSN = os.getenv("ATLAS_TEST_DSN")


@unittest.skipUnless(TEST_DSN, "Set ATLAS_TEST_DSN to run real PostgreSQL checks")
class PostgresCase(unittest.TestCase):
    def setUp(self):
        # Never fall back to the application's PG_* values or .env.
        self.schema = "atlas_test_" + uuid.uuid4().hex
        self.connections = []
        self.admin = psycopg2.connect(TEST_DSN, connect_timeout=5)
        self.admin.autocommit = True
        self.addCleanup(self.cleanup_schema)
        with self.admin.cursor() as cursor:
            cursor.execute(
                sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(self.schema))
            )
        self.conn = self.connect()

    def connect(self):
        conn = psycopg2.connect(
            TEST_DSN, connect_timeout=5, options=f"-csearch_path={self.schema}"
        )
        self.connections.append(conn)
        return conn

    def cleanup_schema(self):
        for conn in self.connections:
            conn.close()
        with self.admin.cursor() as cursor:
            cursor.execute(
                sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(
                    sql.Identifier(self.schema)
                )
            )
        self.admin.close()

    def query(self, statement, params=None):
        with self.conn.cursor() as cursor:
            cursor.execute(statement, params)
            return cursor.fetchall() if cursor.description else None

    def snapshot(self, columns=None):
        tables = [
            "channels",
            "posts",
            "channel_candidates",
            "channel_candidate_sources",
            "channel_sources",
        ]
        return {
            table: self.query(
                sql.SQL("SELECT {} FROM {} ORDER BY 1").format(
                    sql.SQL(", ").join(map(sql.Identifier, columns[table]))
                    if columns
                    else sql.SQL("*"),
                    sql.Identifier(table),
                )
            )
            for table in tables
        }

    def snapshot_columns(self):
        """Capture old columns so additive migrations cannot hide data changes."""
        rows = self.query("""SELECT table_name,column_name FROM information_schema.columns
            WHERE table_schema=current_schema() ORDER BY table_name,ordinal_position""")
        result = {}
        for table, column in rows:
            result.setdefault(table, []).append(column)
        return result
