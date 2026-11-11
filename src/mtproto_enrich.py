"""Точечное обогащение каналов через авторизованный резервный аккаунт.

Запрашивает только название/описание/число подписчиков и Telegram ID.
Не скачивает историю публикаций. Для безопасности требует указать конкретный
username или небольшой лимит до 25 каналов.
"""

import logging
import time
from typing import Optional

from . import db
from .telegram_account import _connect_authorized, _input_channel

log = logging.getLogger(__name__)
MAX_BATCH = 25
DELAY_SECONDS = 1.5


def run(limit: Optional[int] = None, channel: Optional[str] = None) -> int:
    if channel and limit is not None:
        raise ValueError(
            "Используйте либо --channel, либо --limit, но не оба параметра"
        )
    if not channel and not limit:
        raise ValueError("Укажите --channel USERNAME либо ограниченный --limit N")
    if limit is not None and (limit < 1 or limit > MAX_BATCH):
        raise ValueError(f"--limit должен быть от 1 до {MAX_BATCH}")

    conn = db.connect()
    try:
        with conn.cursor() as cur:
            if channel:
                cur.execute(
                    "SELECT id, username FROM channels WHERE lower(username) = lower(%s) AND is_active = TRUE",
                    (db.normalize_username(channel),),
                )
            else:
                cur.execute(
                    "SELECT id, username FROM channels WHERE is_active = TRUE ORDER BY username LIMIT %s",
                    (limit,),
                )
            channels = cur.fetchall()
    finally:
        conn.close()

    if not channels:
        log.warning("Подходящих каналов не найдено")
        return 0

    try:
        from telethon.tl.functions.channels import GetFullChannelRequest
        from telethon.errors import FloodWaitError
    except ImportError as exc:
        raise RuntimeError("Установите requirements-mtproto.txt") from exc

    client = _connect_authorized()
    updated = failed = 0
    conn = db.connect()
    try:
        for index, (channel_id, username) in enumerate(channels, start=1):
            prefix = f"[{index}/{len(channels)}] @{username}"
            try:
                input_channel = _input_channel(client, username)
                result = client(GetFullChannelRequest(channel=input_channel))
                full = result.full_chat
                channel_entity = next(
                    (
                        item
                        for item in result.chats
                        if getattr(item, "broadcast", False)
                    ),
                    None,
                )
                about = (getattr(full, "about", None) or "").strip() or None
                subscribers = getattr(full, "participants_count", None)
                name = getattr(channel_entity, "title", None)
                telegram_id = getattr(channel_entity, "id", None)
                with db.transaction(conn):
                    db.update_channel_meta(
                        conn,
                        channel_id,
                        name=name,
                        description=about,
                        subscribers=subscribers,
                        telegram_id=telegram_id,
                    )
                updated += 1
                log.info(
                    "%s — подписчиков: %s; описание: %s",
                    prefix,
                    f"{subscribers:,}".replace(",", " ")
                    if subscribers is not None
                    else "—",
                    f"{len(about)} симв." if about else "—",
                )
            except FloodWaitError as exc:
                log.error(
                    "%s — Telegram запросил FloodWait на %s сек.; останавливаю пакет",
                    prefix,
                    getattr(exc, "seconds", "неизвестно"),
                )
                break
            except Exception as exc:
                failed += 1
                log.warning("%s — %s", prefix, exc)

            if index < len(channels):
                time.sleep(DELAY_SECONDS)
    finally:
        client.disconnect()
        conn.close()

    log.info("Готово: обновлено %d, ошибок %d", updated, failed)
    log.info("После обновления можно выполнить: python main.py backfill-region --force")
    return updated
