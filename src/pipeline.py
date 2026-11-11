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
from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED
from typing import Iterable, List, Optional

from . import config
from .db import (
    connect,
    get_active_channels,
    get_known_usernames,
    get_post_watermarks,
    get_scrape_checkpoints,
    save_scrape_checkpoint,
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


def _fetch_pages(
    ch: Channel,
    last_seen: int = 0,
    full_history: bool = False,
    max_pages: int | None = None,
    checkpoint=None,
):
    """Poll the head, then resume any interrupted delta backfill within the page budget.

    Cursor and posts are persisted together. A large backlog cannot be skipped merely
    because the latest saved ID advanced during an earlier bounded poll.
    """
    page_posts, first_html = [], None
    before = None
    pages = max(1, config.MAX_PAGES if max_pages is None else max_pages)
    target = last_seen
    pending = checkpoint
    out = False if full_history else checkpoint
    for page_num in range(pages):
        html = fetch_channel_html(ch.username, before=before)
        if page_num == 0:
            first_html = html
        posts = parse_channel_page(html, ch.username, 0)
        if not posts:
            if not parse_channel_meta(html).get("name"):
                raise ScrapeError(
                    f"@{ch.username}: ответ не похож на публичную страницу канала; курсор сохранён"
                )
            if pending and before is None and not full_history:
                before, target = pending
                pending = None
                out = (before, target)
                continue
            out = False if full_history else None
            break
        oldest = min(p.tg_message_id for p in posts)
        if before is not None and oldest >= before:
            # Keep the cursor for the next attempt instead of silently losing a gap.
            out = False if full_history else ((before, target) if last_seen else None)
            break
        page_posts.append(
            posts if full_history else [p for p in posts if p.tg_message_id > target]
        )
        if not full_history and oldest <= target:
            if pending:
                before, target = pending
                pending = None
                out = (before, target)
                continue
            out = None
            break
        before = oldest
        out = (
            False
            if full_history
            else ((before, pending[1] if pending else target) if last_seen else None)
        )
    return first_html, page_posts, out


def _store_channel(conn, ch, prefix, first_html, page_posts, checkpoint=False):
    # Parse HTML before the transaction; only parser failures are recoverable here.
    meta = {"name": None, "description": None, "subscribers": None}
    if first_html:
        try:
            meta = parse_channel_meta(first_html)
        except Exception as exc:
            log.warning("%s — метаданные не разобраны (%s)", prefix, exc)
    with transaction(conn):
        channel_id = upsert_channel(conn, ch)
        if any(
            meta.get(key) is not None for key in ("name", "description", "subscribers")
        ):
            update_channel_meta(
                conn,
                channel_id,
                name=meta.get("name"),
                description=meta.get("description"),
                subscribers=meta.get("subscribers"),
            )

        total_found = 0
        total_new = 0
        for posts in page_posts:
            for p in posts:
                p.channel_id = channel_id
            new_count = insert_posts(conn, posts)
            total_found += len(posts)
            total_new += new_count

        save_scrape_checkpoint(conn, channel_id, checkpoint)
        mark_parsed(conn, channel_id)

    subs = meta.get("subscribers")
    subs_str = f"{subs:,}".replace(",", " ") if subs else "?"
    log.info(
        "%s — постов: %d, новых: %d, подписчиков: %s",
        prefix,
        total_found,
        total_new,
        subs_str,
    )
    return total_found, total_new


def run(
    channels_filter: Optional[Iterable[str]] = None,
    *,
    workers: int | None = None,
    full_history: bool = False,
    max_pages: int | None = None,
) -> dict:
    """Синхронизировать статический каталог с БД и обработать все активные каналы.

    Источником истины после импорта становится PostgreSQL: сюда попадают как
    исходные channels.json, так и кандидаты, подтверждённые через candidate-import.
    """
    wanted = None
    if channels_filter:
        wanted = {
            u.lstrip("@").strip().casefold() for u in channels_filter if u.strip()
        }

    conn = connect()
    try:
        configured = load_channels()
        known = get_known_usernames(conn)
        with transaction(conn):
            for ch in configured:
                if ch.username.lower() not in known and (
                    wanted is None or ch.username.lower() in wanted
                ):
                    upsert_channel(conn, ch)
                    known.add(ch.username.lower())

        rows = get_active_channels(conn)
        all_channels = [
            Channel(
                username=row["username"],
                name=row["name"],
                category=row.get("category"),
                country=row.get("country"),
                subcategory=row.get("subcategory"),
                region=row.get("region"),
                region_hint=row.get("region_hint"),
                source=row.get("source"),
                telegram_id=row.get("telegram_id"),
                id=row.get("id"),
            )
            for row in rows
        ]
        if wanted is not None:
            all_channels = [c for c in all_channels if c.username.casefold() in wanted]
            missing = wanted - {c.username.casefold() for c in all_channels}
            if missing:
                raise ValueError(
                    "Каналы не найдены в активном каталоге: "
                    + ", ".join(sorted(missing))
                )

        if not all_channels:
            log.warning("Нечего обрабатывать — список активных каналов пуст")
            return {"total": 0, "failed": 0, "found": 0, "inserted": 0}

        log.info(
            "К обработке: %d каналов из PostgreSQL, до %d страниц на канал",
            len(all_channels),
            max(1, config.MAX_PAGES if max_pages is None else max_pages),
        )

        total_found = 0
        total_new = 0
        failed = 0

        checkpoints = get_scrape_checkpoints(conn)
        watermarks = (
            {}
            if full_history
            else get_post_watermarks(conn, [ch.id for ch in all_channels])
        )
        conn.commit()  # End the read transaction before waiting on network.
        worker_count = max(
            1, min(8, workers if workers is not None else config.SCRAPE_WORKERS)
        )
        items = iter(enumerate(all_channels, start=1))

        def submit_one(pool, pending):
            item = next(items, None)
            if item is None:
                return
            i, ch = item
            log.info("[%d/%d] @%s — загрузка", i, len(all_channels), ch.username)
            future = pool.submit(
                _fetch_pages,
                ch,
                watermarks.get(ch.id, 0),
                full_history,
                max_pages,
                checkpoints.get(ch.id),
            )
            pending[future] = (i, ch)

        with ThreadPoolExecutor(max_workers=worker_count) as pool:
            pending = {}
            for _ in range(worker_count):
                submit_one(pool, pending)
            while pending:
                completed, _ = wait(pending, return_when=FIRST_COMPLETED)
                for future in completed:
                    i, ch = pending.pop(future)
                    prefix = f"[{i}/{len(all_channels)}] @{ch.username}"
                    try:
                        first_html, page_posts, checkpoint = future.result()
                        found, new = _store_channel(
                            conn, ch, prefix, first_html, page_posts, checkpoint
                        )
                        total_found += found
                        total_new += new
                    except ScrapeError as e:
                        failed += 1
                        log.warning("%s — %s", prefix, e)
                    except Exception:
                        failed += 1
                        log.exception("%s — ошибка обработки", prefix)
                    submit_one(pool, pending)

        log.info(
            "Готово. Найдено постов: %d, новых: %d, ошибок: %d",
            total_found,
            total_new,
            failed,
        )
        return {
            "total": len(all_channels),
            "failed": failed,
            "found": total_found,
            "inserted": total_new,
        }
    finally:
        conn.close()
