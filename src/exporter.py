"""Экспорт каналов из БД в JSON и JS для статического фронтенда."""

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

from .file_io import atomic_write_text
from .topic_keywords import resolve_topic, load_classification_rules

log = logging.getLogger(__name__)


def _channel_to_row(ch: dict, **rules) -> dict:
    username = ch["username"]
    last_parsed = ch.get("last_parsed_at")
    row = {
        "id": username,
        "telegram_id": ch.get("telegram_id"),
        "username": username,
        "name": ch["name"] or username,
        "category": ch["category"] or "other",
        "subcategory": ch.get("subcategory"),
        "region": ch.get("region"),
        "region_hint": ch.get("region_hint"),
        "country": ch["country"],
        "source": ch.get("source"),
        "sources": ch.get("sources") or [],
        "channel_type": ch.get("channel_type") or "unknown",
        "region_confidence": float(ch.get("region_confidence") or 0),
        "region_source": ch.get("region_source"),
        "region_reason": ch.get("region_reason"),
        "region_candidates": ch.get("region_candidates") or [],
        "region_review_required": bool(ch.get("region_review_required")),
        "description": ch["description"],
        "subscribers": ch["subscribers"],
        "url": f"https://t.me/{username}",
        "last_parsed_at": last_parsed.isoformat() if last_parsed else None,
    }

    row["original_subcategory"] = ch.get("subcategory")
    for key in ("regions", "geographic_scope"):
        if key in ch:
            row[key] = ch[key]
    row.update(resolve_topic(row, **rules))
    return row


def channel_rows(channels):
    rules = load_classification_rules()
    return [_channel_to_row(ch, **rules) for ch in channels]


def export_channels(conn, db_module) -> int:
    channels = db_module.get_channels_with_meta(conn)
    rows = channel_rows(channels)

    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "_sample": False,
        "channels": rows,
    }

    web_data = Path(__file__).resolve().parent.parent / "web" / "data"
    web_data.mkdir(parents=True, exist_ok=True)

    payload_json = json.dumps(payload, ensure_ascii=False, indent=2)
    # Both snapshots are complete files; either is independently consumable.
    atomic_write_text(web_data / "channels.json", payload_json)
    atomic_write_text(
        web_data / "channels.js", "window.TG_DATA = " + payload_json + ";\n"
    )

    log.info("Экспортировано каналов: %d", len(rows))
    return len(rows)
