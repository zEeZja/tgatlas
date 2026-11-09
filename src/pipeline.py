"""Оркестрация: конфиг → каналы в БД → метаданные + посты в БД.

Изменения по сравнению с предыдущей версией:
  — пагинация t.me/s/ через ?before=<post_id> (до MAX_PAGES страниц);
  — каждый канал пишется одной транзакцией (атомарно);
  — HTTP-запросы делаются ДО открытия транзакции, чтобы паузы
    между страницами не держали блокировки БД.

Защита от каскадных падений:
  — ошибка на одном канале не роняет остальные;
  — transaction() откатывает незавершённые изменения.
"""

import json
import logging
import random
import time
from typing import Iterable, List, Optional

from . import config
from .db import (
    connect,
    get_active_channels,
    insert_posts,
    mark_parsed,
    transaction,
    update_channel_meta,
    upsert_channel,
)
from .models import Channel
from .parser import parse_channel_meta, parse_channel_page
from .scraper import ScrapeError, fetch_channel_html

log = logging.getLogger(__name__)

def load_channels() -> List[Channel]:
    """Прочитать config/channels.json."""
    with open(config.CHANNELS_FILE, "r", encoding="utf-8") as f:
        data = json.load(f)

    raw = data.get("channels", [])
    result: List[Channel] = []
    for c in raw:
        username = str(c.get("username", "")).lstrip("@").strip()
        if not username:
            log.warning("Пропущена запись без username: %r", c)
            continue
        result.append(
            Channel(
                username=username,
                name=c.get("name"),
                category=c.get("category"),
                country=c.get("country"),
                subcategory=c.get("subcategory"),
                region_hint=c.get("region_hint") or c.get("region"),
                source=c.get("source", "manual"),
                telegram_id=c.get("telegram_id"),
            )
        )
    return result

def _fetch_pages(ch: Channel) -> tuple[Optional[str], List[list]]:
    """Скачать до MAX_PAGES страниц канала.

    Возвращает (first_html, page_posts) где page_posts — список списков Post
    (channel_id пока 0; будет проставлен после upsert_channel).
    """
    page_posts: List[list] = []
    first_html: Optional[str] = None
    before: Optional[int] = None
    pages = max(1, config.MAX_PAGES)

    for page_num in range(pages):
        html = fetch_channel_html(ch.username, before=before)
        if page_num == 0:
            first_html = html

        posts = parse_channel_page(html, ch.username, 0)
        if not posts:
            break
        page_posts.append(posts)

        before = min(p.tg_message_id for p in posts)

        if page_num < pages - 1:
            delay = random.uniform(config.DELAY_MIN, config.DELAY_MAX)
            time.sleep(delay)

    return first_html, page_posts

def _scrape_channel(conn, ch: Channel, prefix: str) -> tuple[int, int]:
    """Скачать все страницы канала и записать их одной транзакцией."""
    log.info("%s — запрашиваю t.me/s/...", prefix)
    first_html, page_posts = _fetch_pages(ch)

    # ─── Write phase: одна транзакция на канал ────────────────────────
    with transaction(conn):
        channel_id = upsert_channel(conn, ch)

        meta = {"name": None, "description": None, "subscribers": None}
        if first_html:
            try:
                meta = parse_channel_meta(first_html)
                update_channel_meta(
                    conn, channel_id,
                    name=meta.get("name"),
                    description=meta["description"],
                    subscribers=meta["subscribers"],
                )
            except Exception as e:
                # не роняем канал из-за метаданных
                log.warning("%s — метаданные не обновлены (%s)", prefix, e)

        total_found = 0
        total_new = 0
        for posts in page_posts:
            for p in posts:
                p.channel_id = channel_id
            new_count = insert_posts(conn, posts)
            total_found += len(posts)
            total_new += new_count

        mark_parsed(conn, channel_id)

    subs = meta.get("subscribers")
    subs_str = f"{subs:,}".replace(",", " ") if subs else "?"
    log.info(
        "%s — постов: %d, новых: %d, подписчиков: %s",
        prefix, total_found, total_new, subs_str,
    )
    return total_found, total_new

def run(channels_filter: Optional[Iterable[str]] = None) -> None:
    """Синхронизировать статический каталог с БД и обработать все активные каналы.

    Источником истины после импорта становится PostgreSQL: сюда попадают как
    исходные channels.json, так и кандидаты, подтверждённые через candidate-import.
    """
    wanted = None
    if channels_filter:
        wanted = {u.lstrip("@").strip().casefold() for u in channels_filter if u.strip()}

    conn = connect()
    try:
        configured = load_channels()
        with transaction(conn):
            for ch in configured:
                upsert_channel(conn, ch)

        rows = get_active_channels(conn)
        all_channels = [
            Channel(
                username=row["username"], name=row["name"],
                category=row.get("category"), country=row.get("country"),
                subcategory=row.get("subcategory"), region=row.get("region"),
                region_hint=row.get("region_hint"), source=row.get("source"),
                telegram_id=row.get("telegram_id"), id=row.get("id"),
            )
            for row in rows
        ]
        if wanted is not None:
            all_channels = [c for c in all_channels if c.username.casefold() in wanted]

        if not all_channels:
            log.warning("Нечего обрабатывать — список активных каналов пуст")
            return

        log.info(
            "К обработке: %d каналов из PostgreSQL, до %d страниц на канал",
            len(all_channels), max(1, config.MAX_PAGES),
        )

        total_found = 0
        total_new = 0
        failed = 0

        for i, ch in enumerate(all_channels, start=1):
            prefix = f"[{i}/{len(all_channels)}] @{ch.username}"
            try:
                found, new = _scrape_channel(conn, ch, prefix)
                total_found += found
                total_new += new
            except ScrapeError as e:
                failed += 1
                log.warning("%s — %s", prefix, e)
            except Exception:
                failed += 1
                log.exception("%s — неожиданная ошибка, пропускаю", prefix)

            if i < len(all_channels):
                delay = random.uniform(config.DELAY_MIN, config.DELAY_MAX)
                time.sleep(delay)

        log.info(
            "Готово. Найдено постов: %d, новых: %d, ошибок: %d",
            total_found, total_new, failed,
        )
    finally:
        conn.close()
