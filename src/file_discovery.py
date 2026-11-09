"""Импорт пользовательских списков и экспортов в единый реестр кандидатов.

Поддерживаются:
- TXT: один @username или публичная ссылка t.me на строку;
- CSV/TSV/JSON: поля username/link/url/title/name/description/category и
  русскоязычные аналоги, совместимые с импортёром CloudSeeding.

По умолчанию выполняется только preview. Флаг --apply записывает записи в
channel_candidates, но НЕ импортирует их в рабочую таблицу channels.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

from .identity import normalize_username

log = logging.getLogger(__name__)


def _records_from_file(path: Path) -> list[dict]:
    suffix = path.suffix.casefold()
    if suffix == ".txt":
        rows = []
        for line_number, line in enumerate(path.read_text(encoding="utf-8-sig").splitlines(), start=1):
            value = line.strip()
            if not value or value.startswith(("#", ";")):
                continue
            rows.append({"username": value, "source_line": line_number})
        return rows

    if suffix in {".csv", ".tsv", ".json"}:
        from .cloudseeding import _load_records
        return _load_records(path)

    raise ValueError("Поддерживаются TXT, CSV, TSV и JSON")


def _to_candidate(row: dict) -> Optional[dict]:
    from .cloudseeding import _candidate_from_row

    candidate = _candidate_from_row(row)
    if not candidate:
        return None
    username = normalize_username(candidate.get("username") or "")
    if not username:
        return None
    candidate["username"] = username
    return candidate


def run(
    *,
    file_path: str,
    source_name: str = "manual_file_import",
    source_url: Optional[str] = None,
    apply: bool = False,
) -> int:
    path = Path(file_path).expanduser().resolve()
    if not path.is_file():
        raise FileNotFoundError(f"Файл со списком каналов не найден: {path}")
    if not source_name.strip():
        raise ValueError("source_name не может быть пустым")

    records = _records_from_file(path)
    prepared = []
    seen = set()
    skipped = 0
    for index, row in enumerate(records, start=1):
        candidate = _to_candidate(row)
        if not candidate:
            skipped += 1
            continue
        username = candidate["username"]
        if username in seen:
            skipped += 1
            continue
        seen.add(username)
        prepared.append((index, candidate))

    log.info("Файл: %s", path.name)
    log.info("Строк/записей: %d; уникальных публичных каналов: %d; пропущено: %d",
             len(records), len(prepared), skipped)
    for _, candidate in prepared[:30]:
        title = candidate.get("name") or "без названия"
        log.info("  @%s — %s", candidate["username"], title)
    if len(prepared) > 30:
        log.info("  ... ещё %d записей", len(prepared) - 30)

    if not apply:
        log.info("Это preview. Для добавления в channel_candidates повторите команду с --apply.")
        return len(prepared)

    from . import db

    conn = db.connect()
    accepted = new_count = 0
    try:
        with db.transaction(conn):
            for index, candidate in prepared:
                source_ref = str(candidate.get("source_row", {}).get("id") or index)
                is_new = db.upsert_candidate(
                    conn,
                    candidate,
                    source_name=source_name,
                    source_ref=source_ref,
                    source_url=source_url,
                )
                accepted += 1
                new_count += int(is_new)
    finally:
        conn.close()

    log.info("Импорт завершён: записей %d, новых username %d", accepted, new_count)
    log.info("Следующий шаг: python main.py candidate-stats")
    return accepted
