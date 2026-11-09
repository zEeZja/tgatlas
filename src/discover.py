"""Проверка кандидатов из единого реестра по публичной странице t.me/s/."""

import logging
import time
from typing import Optional

from . import db, config
from .parser import parse_channel_meta
from .scraper import ScrapeError, fetch_channel_html

log = logging.getLogger(__name__)
DELAY_SECONDS = 2.0


def run(limit: Optional[int] = None) -> int:
    """Проверить доступность публичного канала.

    Канал подтверждается только при известном числе подписчиков не ниже
    config.MIN_CHANNEL_SUBSCRIBERS. Меньшая аудитория отклоняется, неизвестная
    оставляет кандидата pending и не позволяет импортировать его в channels.
    Ошибки 404/403 помечают кандидата отклонённым; сетевые ошибки оставляют
    его pending для повторной проверки.
    """
    conn = db.connect()
    try:
        candidates = db.list_candidates(conn, status="pending", limit=limit or 100000)
        log.info("Кандидатов к проверке: %d", len(candidates))
        validated = rejected = failed = below_floor = unknown_audience = 0

        for index, candidate in enumerate(candidates, start=1):
            username = candidate["username"]
            prefix = f"[{index}/{len(candidates)}] @{username}"
            try:
                html = fetch_channel_html(username)
                meta = parse_channel_meta(html)
                subs = meta.get("subscribers")
                if subs is None:
                    # Без числовой аудитории нельзя подтвердить соответствие
                    # обязательному порогу и нельзя импортировать канал.
                    with db.transaction(conn):
                        db.update_candidate_validation(
                            conn, username, status="pending",
                            name=meta.get("name"),
                            description=meta.get("description"),
                            subscribers=None,
                            validation_note="subscriber_count_unknown",
                        )
                    unknown_audience += 1
                    log.warning(
                        "%s — страница доступна, но число подписчиков не распознано; "
                        "оставлен pending (импорт заблокирован)", prefix,
                    )
                elif subs < config.MIN_CHANNEL_SUBSCRIBERS:
                    with db.transaction(conn):
                        db.update_candidate_validation(
                            conn, username, status="rejected",
                            name=meta.get("name"),
                            description=meta.get("description"),
                            subscribers=subs,
                        )
                    rejected += 1
                    below_floor += 1
                    log.info(
                        "%s — отклонён: %s подписчиков, минимум %s",
                        prefix, f"{subs:,}".replace(",", " "),
                        f"{config.MIN_CHANNEL_SUBSCRIBERS:,}".replace(",", " "),
                    )
                else:
                    with db.transaction(conn):
                        db.update_candidate_validation(
                            conn, username, status="validated",
                            name=meta.get("name"),
                            description=meta.get("description"),
                            subscribers=subs,
                        )
                    validated += 1
                    log.info(
                        "%s — подтверждён, подписчиков: %s",
                        prefix, f"{subs:,}".replace(",", " "),
                    )
            except ScrapeError as exc:
                if exc.permanent:
                    with db.transaction(conn):
                        db.update_candidate_validation(
                            conn, username, status="rejected",
                            validation_note=f"unavailable: {exc}",
                        )
                    rejected += 1
                    log.info("%s — недоступен: %s", prefix, exc)
                else:
                    failed += 1
                    log.warning("%s — временное ограничение/ошибка: %s; оставлен pending", prefix, exc)
            except Exception:
                failed += 1
                log.exception("%s — ошибка проверки; оставлен pending", prefix)

            if index < len(candidates):
                time.sleep(DELAY_SECONDS)

        log.info(
            "Проверка завершена: подтверждено %d, отклонено %d "
            "(ниже порога %d), без числа подписчиков %d, ошибок %d; минимум %s",
            validated, rejected, below_floor, unknown_audience, failed,
            f"{config.MIN_CHANNEL_SUBSCRIBERS:,}".replace(",", " "),
        )
        log.info("Импортировать подтверждённых: python main.py candidate-import --apply")
        return validated
    finally:
        conn.close()
