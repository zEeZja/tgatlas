"""HTTP-слой: тянет HTML страницы t.me/s/<username>.

Изменения по сравнению с предыдущей версией:
  — убран tenacity, реализован ручной retry-цикл с полным джиттером;
  — учитывается заголовок Retry-After при 429;
  — добавлена пагинация `?before=<post_id>` для сбора истории.

Никаких аккаунтов, никаких api_id/api_hash — только публичная веб-версия.
"""

import logging
import random
import time
from typing import Optional

import requests

from . import config

log = logging.getLogger(__name__)

# 4 попытки: 1 основная + 3 повтора. Для 429/5xx и сетевых ошибок.
MAX_ATTEMPTS = 4
MAX_BACKOFF = 30.0        # потолок своей задержки, сек
RETRY_AFTER_MAX = 60.0    # потолок задержки из Retry-After, сек

class ScrapeError(Exception):
    """Ошибка страницы Telegram с признаком окончательной недоступности."""

    def __init__(self, message: str, *, permanent: bool = False):
        super().__init__(message)
        self.permanent = permanent

_session = requests.Session()
_session.headers.update(
    {
        "User-Agent": config.USER_AGENT,
        "Accept-Language": "ru,en;q=0.8",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    }
)

def _backoff(attempt: int) -> float:
    """Экспоненциальная задержка с полным джиттером.

    attempt=0 → U(0, 1), attempt=1 → U(0, 2), attempt=2 → U(0, 4)…
    capped at MAX_BACKOFF. Формула из AWS Architecture Blog.
    """
    ceiling = min(MAX_BACKOFF, 2.0 ** attempt)
    return random.uniform(0, ceiling)

def fetch_channel_html(username: str, before: Optional[int] = None) -> str:
    """Скачать HTML страницы канала.

    before — id поста, старше которого тянем страницу (пагинация t.me/s/).
    Бросает ScrapeError для «безнадёжных» случаев (404/403, 429 после
    исчерпания попыток).
    """
    url = f"https://t.me/s/{username}"
    params = {"before": before} if before else None

    for attempt in range(MAX_ATTEMPTS):
        try:
            r = _session.get(url, params=params, timeout=config.REQUEST_TIMEOUT)
        except requests.RequestException as e:
            if attempt == MAX_ATTEMPTS - 1:
                raise
            delay = _backoff(attempt)
            log.warning("Сетевая ошибка (%s), повтор через %.1fс", e, delay)
            time.sleep(delay)
            continue

        # 429 — уважаем Retry-After, если он есть
        if r.status_code == 429:
            if attempt == MAX_ATTEMPTS - 1:
                raise ScrapeError(
                    f"429 Too Many Requests: {username} (попыток: {MAX_ATTEMPTS})",
                    permanent=False,
                )
            retry_after = r.headers.get("Retry-After", "").strip()
            if retry_after.isdigit():
                delay = min(RETRY_AFTER_MAX, float(retry_after))
                log.warning("429, ждём %.1fс (Retry-After)", delay)
            else:
                delay = _backoff(attempt)
                log.warning("429, ждём %.1fс (backoff)", delay)
            time.sleep(delay)
            continue

        # 5xx — ретраим
        if 500 <= r.status_code < 600:
            if attempt == MAX_ATTEMPTS - 1:
                r.raise_for_status()
            delay = _backoff(attempt)
            log.warning("HTTP %d, повтор через %.1fс", r.status_code, delay)
            time.sleep(delay)
            continue

        # «Безнадёжные» клиентские ошибки — не ретраим
        if r.status_code == 404:
            raise ScrapeError(f"Канал не найден: {username}", permanent=True)
        if r.status_code == 403:
            # 403 может быть ограничением доступа/сети, а не удалением канала.
            raise ScrapeError(f"Доступ запрещён: {username}", permanent=False)

        r.raise_for_status()
        return r.text

    raise ScrapeError(f"Не удалось получить {url} после {MAX_ATTEMPTS} попыток")
