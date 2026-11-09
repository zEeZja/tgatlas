"""Ограниченный read-only сбор последних публикаций через MTProto.

Работает только с каналами, уже внесёнными в `channels`, и не выполняет
join/subscribe или какие-либо действия записи в Telegram. История сообщений
сохраняется в ту же таблицу `posts`, что и HTML-парсер.
"""

from __future__ import annotations

import logging
import time
from typing import Optional

from . import config
from .models import Post
from .parser import _extract_title
from .telegram_account import _connect_authorized, _input_channel

log = logging.getLogger(__name__)
MAX_CHANNELS_PER_RUN = 25
MAX_MESSAGES_PER_CHANNEL = 100
DELAY_SECONDS = 2.0


def message_to_post(message, username: str, channel_id: int) -> Optional[Post]:
    """Преобразовать Telethon Message в общую модель Post."""
    message_id = getattr(message, "id", None)
    if not message_id:
        return None

    text = getattr(message, "message", None) or getattr(message, "raw_text", None)
    text = text.strip() if isinstance(text, str) and text.strip() else None
    media = getattr(message, "media", None)
    if text is None and media is None:
        # Служебные сообщения без текста и медиа не нужны как публикации.
        return None

    return Post(
        channel_id=channel_id,
        tg_message_id=int(message_id),
        url=f"https://t.me/{username}/{int(message_id)}",
        text=text,
        title=_extract_title(text),
        posted_at=getattr(message, "date", None),
        views=getattr(message, "views", None),
    )


def _load_channels(channel_names: Optional[list[str]], limit: Optional[int]):
    from .identity import normalize_username

    from . import db

    if channel_names:
        usernames = list(dict.fromkeys(
            username for username in (normalize_username(x) for x in channel_names) if username
        ))
        if not usernames:
            raise ValueError("Не передано ни одного корректного публичного username")
        conn = db.connect()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT id, username FROM channels WHERE is_active = TRUE AND lower(username) = ANY(%s) ORDER BY username",
                    (usernames,),
                )
                rows = cur.fetchall()
        finally:
            conn.close()
        found = {row[1] for row in rows}
        missing = sorted(set(usernames) - found)
        if missing:
            log.warning("Каналы не найдены в рабочей БД: %s", ", ".join("@" + x for x in missing))
        return rows

    conn = db.connect()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, username FROM channels WHERE is_active = TRUE ORDER BY username LIMIT %s",
                (limit,),
            )
            return cur.fetchall()
    finally:
        conn.close()


def run(
    channels: Optional[list[str]] = None,
    *,
    limit: Optional[int] = None,
    messages_per_channel: int = 50,
) -> tuple[int, int]:
    """Собрать последние сообщения для заданных каналов или ограниченной партии."""
    if channels and limit is not None:
        raise ValueError("Используйте либо --channel, либо --limit, но не оба параметра")
    if not channels and limit is None:
        raise ValueError("Укажите хотя бы один --channel или ограниченный --limit")
    if limit is not None and not 1 <= limit <= MAX_CHANNELS_PER_RUN:
        raise ValueError(f"--limit должен быть от 1 до {MAX_CHANNELS_PER_RUN}")
    if not 1 <= messages_per_channel <= MAX_MESSAGES_PER_CHANNEL:
        raise ValueError(f"--messages должен быть от 1 до {MAX_MESSAGES_PER_CHANNEL}")
    if DELAY_SECONDS < 1:
        raise ValueError("Задержка между каналами должна быть не меньше 1 секунды")

    from . import db

    channel_rows = _load_channels(channels, limit)
    if not channel_rows:
        log.warning("Нет подходящих активных каналов в PostgreSQL")
        return 0, 0

    try:
        from telethon.errors import FloodWaitError
        from telethon.tl.functions.channels import GetFullChannelRequest
    except ImportError as exc:
        raise RuntimeError("Установите Telethon: python -m pip install -r requirements-mtproto.txt") from exc

    client = _connect_authorized()
    conn = None
    processed_channels = total_found = total_new = failed = 0
    stopped_by_flood = False
    try:
        conn = db.connect()
        for index, (channel_id, username) in enumerate(channel_rows, start=1):
            prefix = f"[{index}/{len(channel_rows)}] @{username}"
            try:
                input_channel = _input_channel(client, username)
                full_result = client(GetFullChannelRequest(channel=input_channel))
                full_chat = getattr(full_result, "full_chat", None)
                channel_entity = next(
                    (item for item in getattr(full_result, "chats", [])
                     if getattr(item, "broadcast", False)),
                    None,
                )
                name = getattr(channel_entity, "title", None)
                telegram_id = getattr(channel_entity, "id", None)
                subscribers = getattr(full_chat, "participants_count", None)
                description = (getattr(full_chat, "about", None) or "").strip() or None

                if channel_entity is None:
                    raise RuntimeError("Telegram не вернул сущность broadcast-канала")
                posts = []
                for message in client.iter_messages(channel_entity, limit=messages_per_channel):
                    post = message_to_post(message, username, channel_id)
                    if post is not None:
                        posts.append(post)

                with db.transaction(conn):
                    db.update_channel_meta(
                        conn, channel_id,
                        name=name,
                        description=description,
                        subscribers=subscribers,
                        telegram_id=telegram_id,
                    )
                    inserted = db.insert_posts(conn, posts)
                    db.mark_parsed(conn, channel_id)

                processed_channels += 1
                total_found += len(posts)
                total_new += inserted
                log.info(
                    "%s — получено %d сообщений, новых %d; подписчиков: %s",
                    prefix, len(posts), inserted,
                    f"{subscribers:,}".replace(",", " ") if subscribers is not None else "—",
                )
            except FloodWaitError as exc:
                seconds = getattr(exc, "seconds", None)
                log.error(
                    "%s — Telegram запросил FloodWait%s; пакет остановлен без обхода ограничения",
                    prefix, f" на {seconds} сек." if seconds is not None else "",
                )
                stopped_by_flood = True
                break
            except Exception as exc:
                failed += 1
                log.warning("%s — пропущен: %s", prefix, exc)

            if index < len(channel_rows):
                time.sleep(DELAY_SECONDS)
    finally:
        client.disconnect()
        if conn is not None:
            conn.close()

    log.info(
        "MTProto scrape завершён: каналов обработано %d, сообщений получено %d, новых %d, ошибок %d",
        processed_channels, total_found, total_new, failed,
    )
    if stopped_by_flood:
        log.warning("Часть партии не обработана из-за ограничения Telegram; повторите позже меньшей партией")
    return total_found, total_new
