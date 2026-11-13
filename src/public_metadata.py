"""Refresh public channel profiles without Telegram credentials or post parsing."""

import logging
import math
from datetime import datetime, timedelta, timezone
from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED
import requests
from . import config, db
from .parser import parse_channel_meta
from .scraper import fetch_channel_html, ScrapeError

log = logging.getLogger(__name__)


def fetch_metadata(username):
    meta = parse_channel_meta(fetch_channel_html(username, profile=True))
    if (
        not meta.get("name")
        or meta.get("subscribers") is None
        or meta.get("description") is None
    ):
        raise ScrapeError(
            f"@{username}: публичная страница не содержит метаданных канала"
        )
    return meta


def _timestamp(value):
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    if not isinstance(value, datetime):
        return None
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value


def select_due(
    rows, *, max_age_hours=24, retry_after_hours=1, limit=None, force=False, now=None
):
    """Сначала непроверенные/старые; свежие и недавние ошибки не запрашиваются."""
    for value in (max_age_hours, retry_after_hours):
        if not math.isfinite(value) or value < 0:
            raise ValueError("Интервал должен быть конечным неотрицательным числом")
    if limit is not None and (
        isinstance(limit, bool) or not isinstance(limit, int) or limit < 1
    ):
        raise ValueError("limit должен быть положительным целым числом")
    now = _timestamp(now) if now is not None else datetime.now(timezone.utc)
    if now is None:
        raise ValueError("Некорректное время планирования")
    due = []
    for row in rows:
        checked = _timestamp(row.get("last_meta_at"))
        if checked and checked > now + timedelta(minutes=5):
            checked = None
        attempted = _timestamp(row.get("last_meta_attempt_at"))
        if not force:
            if (
                row.get("last_meta_error")
                and attempted
                and (now - attempted).total_seconds() / 3600 < retry_after_hours
            ):
                continue
            if checked and (now - checked).total_seconds() / 3600 < max_age_hours:
                continue
        due.append(
            (
                checked or datetime.min.replace(tzinfo=timezone.utc),
                str(row.get("username") or ""),
                row,
            )
        )
    due.sort(key=lambda item: (item[0], item[1]))
    return [item[2] for item in due[:limit]]


def _error_code(exc):
    if isinstance(exc, requests.Timeout):
        return "timeout"
    if isinstance(exc, requests.RequestException):
        return "network_error"
    if isinstance(exc, ScrapeError):
        return "unavailable" if exc.permanent else "invalid_or_limited_response"
    return "update_failed"


def run(
    channels=None,
    workers=None,
    from_config=False,
    *,
    max_age_hours=24,
    retry_after_hours=1,
    limit=None,
    force=False,
):
    # Validate before opening a connection or seeding a configuration.
    select_due(
        [],
        max_age_hours=max_age_hours,
        retry_after_hours=retry_after_hours,
        limit=limit,
    )
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
            return {"updated": 0, "failed": 0, "total": 0, "attempted": 0, "skipped": 0}
        due = select_due(
            rows,
            max_age_hours=max_age_hours,
            retry_after_hours=retry_after_hours,
            limit=limit,
            force=force,
        )
        items = iter(due)
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
                        code = _error_code(exc)
                        with db.transaction(conn):
                            db.mark_metadata_failure(conn, row["id"], code)
                        log.warning(
                            "@%s: метаданные не обновлены — %s", row["username"], code
                        )
                    submit()
        log.info(
            "Метаданные: обновлено %d, ошибок %d. Публикации не запрашивались и не изменялись.",
            updated,
            failed,
        )
        return {
            "updated": updated,
            "failed": failed,
            "total": len(rows),
            "attempted": len(due),
            "skipped": len(rows) - len(due),
        }
    finally:
        conn.close()
