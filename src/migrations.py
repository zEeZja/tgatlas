"""Ordered, checksummed PostgreSQL migrations; init-db owns this transaction."""

from dataclasses import dataclass
import hashlib
from pathlib import Path
import re

from psycopg2 import sql

MIGRATIONS_DIR = Path(__file__).resolve().parent.parent / "db" / "migrations"
# Transaction-scoped, shared by concurrent initializers in this database.
ADVISORY_LOCK_ID = 0x544741544C4153


class MigrationError(RuntimeError):
    """The schema history and the checked-out application are incompatible."""


@dataclass(frozen=True)
class Migration:
    version: str
    name: str
    checksum: str
    script: str


def load_migrations(directory=MIGRATIONS_DIR):
    migrations = []
    versions = set()
    for path in sorted(Path(directory).glob("*.sql")):
        match = re.fullmatch(r"([0-9]{4})_[a-z0-9_]+\.sql", path.name)
        if not match or match[1] in versions:
            raise MigrationError(
                f"Некорректная или повторная версия миграции: {path.name}"
            )
        # Universal newlines keep checksums identical after Windows checkout.
        script = path.read_text(encoding="utf-8")
        if not script.strip():
            raise MigrationError(f"Пустая миграция: {path.name}")
        versions.add(match[1])
        migrations.append(
            Migration(
                match[1],
                path.name,
                hashlib.sha256(script.encode("utf-8")).hexdigest(),
                script,
            )
        )
    if not migrations:
        raise MigrationError("SQL-миграции не найдены")
    return migrations


def _history(cursor, migrations):
    cursor.execute(
        "SELECT version, name, checksum FROM schema_migrations ORDER BY version"
    )
    applied = cursor.fetchall()
    # Only an exact prefix is valid. Never silently downgrade a newer database.
    if len(applied) > len(migrations):
        raise MigrationError(
            "База новее приложения; используйте соответствующую версию кода"
        )
    for row, migration in zip(applied, migrations):
        if tuple(row) != (migration.version, migration.name, migration.checksum):
            raise MigrationError(
                "История миграций не совпадает с кодом; применённые SQL-файлы изменять нельзя"
            )
    return len(applied)


def apply_migrations(conn, directory=MIGRATIONS_DIR):
    """Apply all pending migrations and history atomically, retaining legacy data.

    The caller must use a fresh, non-autocommit connection. On failure even DDL
    and history inserts roll back. No application table is dropped or truncated.
    """
    migrations = load_migrations(directory)
    with conn:
        with conn.cursor() as cursor:
            cursor.execute("SELECT set_config('lock_timeout', '10s', true)")
            cursor.execute("SELECT pg_advisory_xact_lock(%s)", (ADVISORY_LOCK_ID,))
            cursor.execute("""CREATE TABLE IF NOT EXISTS schema_migrations (
                version TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                checksum TEXT NOT NULL,
                applied_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )""")
            count = _history(cursor, migrations)
            for migration in migrations[count:]:
                cursor.execute(migration.script)
                cursor.execute(
                    "INSERT INTO schema_migrations (version, name, checksum) VALUES (%s, %s, %s)",
                    (migration.version, migration.name, migration.checksum),
                )
    return [migration.version for migration in migrations[count:]]


def migration_status(conn, directory=MIGRATIONS_DIR):
    """Read only: no automatic migration during API startup or health requests."""
    migrations = load_migrations(directory)
    with conn.cursor() as cursor:
        cursor.execute("SELECT to_regclass('schema_migrations')")
        count = _history(cursor, migrations) if cursor.fetchone()[0] else 0
    return {
        "current": migrations[count - 1].version if count else None,
        "latest": migrations[-1].version,
        "pending": [migration.version for migration in migrations[count:]],
    }


# Probe the columns used by the main read/write paths, without loading rows.
REQUIRED_COLUMNS = {
    "channels": "id username name category country description subscribers is_active last_parsed_at last_meta_at created_at updated_at subcategory region region_hint source channel_type region_confidence region_source region_reason region_candidates region_review_required telegram_id last_meta_attempt_at last_meta_error",
    "channel_merge_archive": "id canonical_username kept_channel_id archived_at records",
    "posts": "id channel_id tg_message_id url text title posted_at views fetched_at",
    "channel_candidates": "username telegram_id name category subcategory country region_hint description subscribers validation_note status first_seen_at last_seen_at validated_at imported_at imported_channel_id raw_data updated_at",
    "channel_candidate_sources": "username source_name source_ref source_url first_seen_at last_seen_at raw_data",
    "channel_sources": "id channel_id source_name source_ref source_url first_seen_at last_seen_at raw_data",
    "scrape_checkpoints": "channel_id before_id target_id",
}


def check_schema_ready(conn):
    status = migration_status(conn)
    if status["pending"]:
        raise MigrationError("Требуется python main.py init-db")
    with conn.cursor() as cursor:
        for table, columns in REQUIRED_COLUMNS.items():
            cursor.execute(
                sql.SQL("SELECT {} FROM {} LIMIT 0").format(
                    sql.SQL(", ").join(map(sql.Identifier, columns.split())),
                    sql.Identifier(table),
                )
            )
    return status
