"""Импорт пользовательского CSV/JSON-экспорта CloudSeeding.

Dashboard может быть доступен только по подписке/учётной записи. Модуль
работает с файлом, который пользователь вправе экспортировать, и не обходит
авторизацию, paywall или внутренние endpoint сервиса.
"""

import csv
import json
import logging
import re
from pathlib import Path
from typing import Optional

from .metrics import parse_count


log = logging.getLogger(__name__)


def _norm_key(value: str) -> str:
    return "".join(ch for ch in str(value or "").casefold() if ch.isalnum())


def _get(row: dict, *keys):
    normalized = {_norm_key(k): v for k, v in row.items()}
    for key in keys:
        value = normalized.get(_norm_key(key))
        if value not in (None, ""):
            return value
    return None


def _parse_count(value) -> Optional[int]:
    """Совместимый локальный alias общего парсера числовых метрик."""
    return parse_count(value)


def _load_records(path: Path) -> list[dict]:
    suffix = path.suffix.casefold()
    if suffix == ".json":
        raw = json.loads(path.read_text(encoding="utf-8-sig"))
        # Поддерживаем несколько распространённых форм экспорта.
        while isinstance(raw, dict):
            next_value = None
            for key in ("channels", "items", "results", "data"):
                if isinstance(raw.get(key), (dict, list)):
                    next_value = raw[key]
                    break
            if next_value is None:
                raise ValueError("JSON не содержит массива channels/items/results/data")
            raw = next_value
        if not isinstance(raw, list):
            raise ValueError("JSON должен содержать массив каналов")
        return [x for x in raw if isinstance(x, dict)]

    if suffix in {".csv", ".tsv"}:
        with path.open("r", encoding="utf-8-sig", newline="") as stream:
            sample = stream.read(8192)
            stream.seek(0)
            if suffix == ".tsv":
                delimiter = "\t"
            else:
                try:
                    delimiter = csv.Sniffer().sniff(sample, delimiters=",;\t").delimiter
                except csv.Error:
                    delimiter = ","
            return list(csv.DictReader(stream, delimiter=delimiter))

    raise ValueError("Поддерживаются только CSV, TSV и JSON")


def _candidate_from_row(row: dict) -> Optional[dict]:
    username = _get(
        row, "username", "channel_username", "handle", "канал",
        "ссылка", "telegram", "tg_link", "link", "url",
    )
    if not username:
        return None

    raw_country = _get(row, "country", "country_code", "страна")
    country_text = str(raw_country).strip().casefold() if raw_country else ""
    country_map = {"россия": "RU", "russia": "RU", "ru": "RU",
                   "казахстан": "KZ", "kazakhstan": "KZ", "kz": "KZ",
                   "беларусь": "BY", "belarus": "BY", "by": "BY",
                   "украина": "UA", "ukraine": "UA", "ua": "UA"}
    country = country_map.get(country_text, country_text.upper()[:3] or None)

    telegram_id = _get(row, "telegram_id", "tg_id", "channel_id")
    try:
        telegram_id = int(telegram_id) if telegram_id not in (None, "") else None
    except (TypeError, ValueError):
        telegram_id = None

    return {
        "username": username,
        "telegram_id": telegram_id,
        "name": _get(row, "name", "название", "title", "channel_name"),
        "category": _get(row, "category", "категория", "тематика", "topic"),
        "subcategory": _get(row, "subcategory", "подкатегория", "microcategory"),
        "region_hint": _get(row, "region", "регион", "город", "location", "география"),
        "country": country,
        "description": _get(row, "description", "описание", "about"),
        "subscribers": _parse_count(_get(row, "subscribers", "подписчики", "аудитория", "members")),
        "avg_reach_24h": _parse_count(_get(row, "avg_reach_24h", "reach_24h", "охват 24ч", "средний охват 24")),
        "avg_reach_48h": _parse_count(_get(row, "avg_reach_48h", "reach_48h", "охват 48ч", "средний охват 48")),
        "engagement_rate": _get(row, "er", "engagement_rate", "вовлеченность", "вовлечённость"),
        "cpm": _get(row, "cpm", "цена за тысячу", "стоимость размещения"),
        "source_row": row,
    }


def run(file_path: str) -> int:
    path = Path(file_path).expanduser().resolve()
    if not path.is_file():
        raise FileNotFoundError(f"Файл экспорта не найден: {path}")
    records = _load_records(path)
    log.info("Записей в экспорте: %d", len(records))

    from . import db

    conn = db.connect()
    accepted = skipped = new_count = 0
    try:
        with db.transaction(conn):
            for index, row in enumerate(records, start=1):
                candidate = _candidate_from_row(row)
                if not candidate:
                    skipped += 1
                    continue
                is_new = db.upsert_candidate(
                    conn, candidate,
                    source_name="cloudseeding_export",
                    source_ref=str(_get(row, "id", "export_id") or index),
                    source_url="https://cloudseeding.ru/dashboard",
                )
                accepted += 1
                new_count += int(is_new)
    finally:
        conn.close()

    log.info(
        "CloudSeeding import завершён: обработано %d, новых %d, пропущено %d",
        accepted, new_count, skipped,
    )
    log.info("Проверить список: python main.py candidate-stats")
    return accepted
