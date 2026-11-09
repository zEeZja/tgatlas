"""Пересчёт регионов и типа Telegram-каналов.

Перед массовой записью рекомендуется запускать --dry-run --report.
Неоднозначные каналы остаются с region=NULL и попадают в отчёт проверки.
"""

from __future__ import annotations

import csv
import json
import logging
from pathlib import Path
from typing import Any

from . import config
from .region_classifier import classify_channel

log = logging.getLogger(__name__)


CHANNELS_FILE = config.CHANNELS_FILE
FEDERAL_FILE = CHANNELS_FILE.parent / "federal_channels.json"
OVERRIDES_FILE = CHANNELS_FILE.parent / "region_overrides.json"


def _load_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else default
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"Не удалось прочитать {path}: {exc}") from exc


def _load_federal_channels() -> set[str]:
    data = _load_json(FEDERAL_FILE, {"channels": []})
    values = data.get("channels", []) if isinstance(data, dict) else []
    return {str(value).strip().lstrip("@").casefold() for value in values if value}


def _load_hints() -> dict[str, str]:
    """Взять только подсказки из исходного каталога, не текущий результат БД."""
    data = _load_json(CHANNELS_FILE, {"channels": []})
    channels = data.get("channels", []) if isinstance(data, dict) else []
    hints = {}
    for item in channels:
        username = str(item.get("username") or "").strip().lstrip("@").casefold()
        hint = item.get("region_hint") or item.get("region")
        if username and hint:
            hints[username] = str(hint)
    return hints


def _load_overrides() -> dict[str, dict]:
    data = _load_json(OVERRIDES_FILE, {"channels": {}})
    entries = data.get("channels", {}) if isinstance(data, dict) else {}
    if not isinstance(entries, dict):
        raise RuntimeError(f"Ожидался объект channels в {OVERRIDES_FILE}")
    return {str(k).strip().lstrip("@").casefold(): v for k, v in entries.items() if isinstance(v, dict)}


def _build_report_row(row: dict, result: dict) -> dict:
    return {
        "username": row["username"],
        "name": row.get("name"),
        "old_region": row.get("region"),
        "region_hint": row.get("region_hint"),
        "new_region": result["region"],
        "channel_type": result["channel_type"],
        "confidence": result["region_confidence"],
        "source": result["region_source"],
        "reason": result["region_reason"],
        "candidates": result["region_candidates"],
    }


def _write_report(path: str | Path, report_rows: list[dict]) -> Path:
    target = Path(path)
    if not target.is_absolute():
        target = config.ROOT / target
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.suffix.lower() == ".csv":
        with target.open("w", encoding="utf-8-sig", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=list(report_rows[0]) if report_rows else [
                "username", "name", "old_region", "region_hint", "new_region", "channel_type",
                "confidence", "source", "reason", "candidates",
            ])
            writer.writeheader()
            for item in report_rows:
                row = dict(item)
                row["candidates"] = json.dumps(row["candidates"], ensure_ascii=False)
                writer.writerow(row)
    else:
        target.write_text(json.dumps(report_rows, ensure_ascii=False, indent=2), encoding="utf-8")
    return target


def run(
    force: bool = False,
    debug: bool = False,
    username: str | None = None,
    dry_run: bool = False,
    report_path: str | None = None,
) -> dict[str, int]:
    federal = _load_federal_channels()
    hints = _load_hints()
    overrides = _load_overrides()
    log.info("Федеральный whitelist: %d; ручных исключений: %d", len(federal), len(overrides))

    # Импорт подключения отложен: модуль и классификатор можно тестировать
    # без установленного psycopg2 и без доступной базы данных.
    from .db import connect

    conn = connect()
    report_rows: list[dict] = []
    stats = {"processed": 0, "regional": 0, "federal": 0, "unknown": 0, "overridden": 0}
    try:
        # psycopg2 импортируется только в момент работы с БД: модуль
        # классификатора и его тесты доступны без установленного драйвера.
        from psycopg2.extras import RealDictCursor

        where = "WHERE is_active = TRUE"
        params: list[Any] = []
        if not force:
            where += " AND (region IS NULL OR channel_type IS NULL OR region_review_required = TRUE)"
        if username:
            where += " AND lower(username) = lower(%s)"
            params.append(username.lstrip("@"))

        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                f"""SELECT id, username, name, description, region, region_hint, channel_type
                      FROM channels {where} ORDER BY username""",
                params,
            )
            rows = list(cur.fetchall())

        log.info("Каналов к обработке: %d%s", len(rows), " (dry-run)" if dry_run else "")
        updates = []
        for row in rows:
            key = str(row["username"]).strip().lstrip("@").casefold()
            override = overrides.get(key)
            if override:
                region = override.get("region")
                channel_type = override.get("channel_type", "regional" if region else "unknown")
                if channel_type not in {"regional", "federal", "unknown"}:
                    raise ValueError(f"Некорректный channel_type для @{key}: {channel_type}")
                if channel_type == "regional" and not region:
                    raise ValueError(f"Для регионального override @{key} требуется region")
                result = {
                    "region": region if channel_type == "regional" else None,
                    "channel_type": channel_type,
                    "region_confidence": 1.0,
                    "region_source": "manual_override",
                    "region_reason": str(override.get("reason") or "Ручное правило из config/region_overrides.json"),
                    "region_candidates": [],
                    "region_review_required": channel_type == "unknown",
                }
                stats["overridden"] += 1
            else:
                classified = classify_channel(
                    username=row["username"],
                    name=row.get("name"),
                    description=row.get("description"),
                    region_hint=row.get("region_hint") or hints.get(key),
                    federal_channels=federal,
                )
                result = {
                    "region": classified.region,
                    "channel_type": classified.channel_type,
                    "region_confidence": classified.confidence,
                    "region_source": classified.source,
                    "region_reason": classified.reason,
                    "region_candidates": [
                        {"region": name, "confidence": confidence}
                        for name, confidence in classified.candidates
                    ],
                    "region_review_required": classified.needs_review,
                }

            stats["processed"] += 1
            stats[result["channel_type"]] += 1
            report_rows.append(_build_report_row(row, result))
            updates.append((
                result["region"], result["channel_type"], result["region_confidence"],
                result["region_source"], result["region_reason"],
                json.dumps(result["region_candidates"], ensure_ascii=False),
                result["region_review_required"], row["id"],
            ))
            if debug:
                log.info(
                    "@%s → %s / %s (%.3f): %s",
                    key, result["region"] or "—", result["channel_type"],
                    result["region_confidence"], result["region_reason"],
                )

        if not dry_run and updates:
            with conn.cursor() as cur:
                cur.executemany(
                    """UPDATE channels
                          SET region = %s,
                              channel_type = %s,
                              region_confidence = %s,
                              region_source = %s,
                              region_reason = %s,
                              region_candidates = %s::jsonb,
                              region_review_required = %s,
                              updated_at = NOW()
                        WHERE id = %s""",
                    updates,
                )
            conn.commit()
        elif dry_run:
            conn.rollback()

        if report_path:
            target = _write_report(report_path, report_rows)
            log.info("Отчёт сохранён: %s", target)

        log.info(
            "Обработано: %(processed)d; региональных: %(regional)d; федеральных: %(federal)d; "
            "на проверку: %(unknown)d; ручных правил: %(overridden)d",
            stats,
        )
        if dry_run:
            log.info("Режим dry-run: изменения в БД не записаны")
        return stats
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
