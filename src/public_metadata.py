"""Refresh public channel profiles without Telegram credentials or post parsing."""

import logging
from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED
from . import config, db
from .parser import parse_channel_meta
from .scraper import fetch_channel_html, ScrapeError

log = logging.getLogger(__name__)


def fetch_metadata(username):
    meta = parse_channel_meta(fetch_channel_html(username, profile=True))
    if not meta.get("name") or meta.get("subscribers") is None:
        raise ScrapeError(
            f"@{username}: публичная страница не содержит метаданных канала"
        )
    return meta


def run(channels=None, workers=None, from_config=False):
    wanted = {x.lstrip("@").strip().lower() for x in channels or []}
    conn = db.connect()
    updated = failed = 0
    try:
        if from_config:
            from .pipeline import load_channels

            known = db.get_known_usernames(conn)
            with db.transaction(conn):
                for ch in load_channels():
                    if ch.username.lower() not in known and (
                        not wanted or ch.username.lower() in wanted
                    ):
                        db.upsert_channel(conn, ch)
                        known.add(ch.username.lower())
        rows = [
            r
            for r in db.get_active_channels(conn)
            if not wanted or r["username"].lower() in wanted
        ]
        conn.commit()  # No open read transaction during HTTP requests.
        missing = wanted - {r["username"].lower() for r in rows}
        if missing:
            raise ValueError(
                "Каналы не найдены в активном каталоге: " + ", ".join(sorted(missing))
            )
        if not rows:
            log.warning(
                "Нет активных каналов. Для первичного импорта config/channels.json используйте --from-config."
            )
            return {"updated": 0, "failed": 0, "total": 0}
        items = iter(rows)
        count = max(
            1, min(8, workers if workers is not None else config.SCRAPE_WORKERS)
        )
        with ThreadPoolExecutor(max_workers=count) as pool:
            pending = {}

            def submit():
                row = next(items, None)
                if row is not None:
                    pending[pool.submit(fetch_metadata, row["username"])] = row

            for _ in range(count):
                submit()
            while pending:
                completed, _ = wait(pending, return_when=FIRST_COMPLETED)
                for future in completed:
                    row = pending.pop(future)
                    try:
                        meta = future.result()
                        with db.transaction(conn):
                            db.update_channel_meta(
                                conn,
                                row["id"],
                                name=meta["name"],
                                description=meta["description"],
                                subscribers=meta["subscribers"],
                            )
                        updated += 1
                        log.info(
                            "[%d/%d] @%s: подписчиков %s, описание %d симв.",
                            updated + failed,
                            len(rows),
                            row["username"],
                            meta["subscribers"],
                            len(meta["description"] or ""),
                        )
                    except Exception as exc:
                        failed += 1
                        log.warning(
                            "@%s: метаданные не обновлены — %s", row["username"], exc
                        )
                    submit()
        log.info(
            "Метаданные: обновлено %d, ошибок %d. Публикации не запрашивались и не изменялись.",
            updated,
            failed,
        )
        return {"updated": updated, "failed": failed, "total": len(rows)}
    finally:
        conn.close()
