"""Экспорт каналов из БД в JSON и JS для статического фронтенда."""

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

from .topic_keywords import resolve_topic

log = logging.getLogger(__name__)

def _channel_to_row(ch: dict) -> dict:
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
    row.update(resolve_topic(row))
    return row

def export_channels(conn, db_module) -> int:
    channels = db_module.get_channels_with_meta(conn)
    rows = [_channel_to_row(ch) for ch in channels]

    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "_sample": False,
        "channels": rows,
    }

    web_data = Path(__file__).resolve().parent.parent / "web" / "data"
    web_data.mkdir(parents=True, exist_ok=True)

    json_path = web_data / "channels.json"
    json_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    js_path = web_data / "channels.js"
    js_path.write_text(
        "window.TG_DATA = " + json.dumps(payload, ensure_ascii=False) + ";\n",
        encoding="utf-8",
    )

    log.info("Экспортировано каналов: %d", len(rows))
    return len(rows)