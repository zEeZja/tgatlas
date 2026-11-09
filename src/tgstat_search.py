"""Поиск публичных Telegram-каналов через документированный TGStat API.

Метод channels/search относится к TGStat API Stat (тариф S и выше).
Адаптер использует только официальный HTTPS endpoint и не обращается к
внутренним endpoint веб-интерфейса. Найденные каналы попадают в реестр
кандидатов, но не импортируются в рабочую таблицу channels автоматически.
"""

from __future__ import annotations

import logging
import time
from typing import Iterable, Optional

import requests

from . import config
from .identity import normalize_username
from .metrics import parse_count

log = logging.getLogger(__name__)
API_URL = "https://api.tgstat.ru/channels/search"
MAX_LIMIT = 100


class TGStatAPIError(RuntimeError):
    """Ошибка API TGStat с понятным для CLI сообщением."""


def _response_items(payload: dict) -> list[dict]:
    if payload.get("status") != "ok":
        message = payload.get("error") or payload.get("message") or "неизвестная ошибка API"
        raise TGStatAPIError(f"TGStat API вернул ошибку: {message}")
    response = payload.get("response")
    if not isinstance(response, dict):
        raise TGStatAPIError("TGStat API: в ответе отсутствует объект response")
    items = response.get("items")
    if not isinstance(items, list):
        raise TGStatAPIError("TGStat API: response.items отсутствует или имеет неверный формат")
    return [item for item in items if isinstance(item, dict)]


def _extract_channels(payload: dict, *, query: Optional[str], category: Optional[str]) -> list[dict]:
    """Преобразовать ответ TGStat в общий формат channel_candidates."""
    result = []
    for item in _response_items(payload):
        # Запрос уже задаёт peer_type=channel, но дополнительно проверяем ответ.
        peer_type = str(item.get("peer_type") or "channel").casefold()
        if peer_type != "channel":
            continue

        username = normalize_username(
            item.get("username") or item.get("link") or item.get("url") or ""
        )
        if not username:
            continue

        telegram_id = parse_count(item.get("tg_id"))
        if telegram_id is not None and telegram_id <= 0:
            telegram_id = None

        subscribers = parse_count(item.get("participants_count"))
        restrictions = item.get("tgstat_restrictions")
        result.append({
            "username": username,
            "telegram_id": telegram_id,
            "name": item.get("title"),
            "description": item.get("about"),
            "category": item.get("category") or category,
            "country": item.get("country") or None,
            "subscribers": subscribers,
            "tgstat_id": item.get("id"),
            "tgstat_ci_index": item.get("ci_index"),
            "tgstat_created_at": item.get("created_at"),
            "tgstat_restrictions": restrictions if isinstance(restrictions, list) else [],
            "catalog_query": query,
            "catalog_category": category,
        })
    return result


def _fetch(
    *, token: str, query: Optional[str], category: Optional[str], country: str,
    language: str, limit: int, search_by_description: bool,
) -> dict:
    params = {
        "token": token,
        "peer_type": "channel",
        "country": country,
        "language": language,
        "limit": limit,
        "search_by_description": 1 if search_by_description else 0,
    }
    if query:
        params["q"] = query
    if category:
        params["category"] = category

    try:
        response = requests.get(API_URL, params=params, timeout=config.REQUEST_TIMEOUT)
        response.raise_for_status()
    except requests.HTTPError as exc:
        status = getattr(exc.response, "status_code", None)
        if status == 401:
            raise TGStatAPIError("TGStat отклонил токен (HTTP 401). Проверьте TGSTAT_API_TOKEN.") from exc
        if status == 403:
            raise TGStatAPIError(
                "Доступ к channels/search запрещён. Проверьте тариф TGStat API Stat (S или выше)."
            ) from exc
        if status == 429:
            raise TGStatAPIError("TGStat ограничил частоту запросов (HTTP 429); остановил поиск.") from exc
        raise

    try:
        payload = response.json()
    except ValueError as exc:
        raise TGStatAPIError("TGStat вернул ответ не в формате JSON") from exc
    if not isinstance(payload, dict):
        raise TGStatAPIError("TGStat вернул JSON неожиданного формата")
    # Валидируем envelope до того, как он попадёт в БД.
    _response_items(payload)
    return payload


def run(
    *, queries: Optional[Iterable[str]] = None,
    categories: Optional[Iterable[str]] = None,
    country: str = "ru",
    language: str = "russian",
    limit: int = 20,
    search_by_description: bool = False,
    delay: float = 1.5,
) -> int:
    """Найти кандидатов TGStat и записать их в единый реестр.

    Каждый запрос/категория сохраняется как отдельный source_ref. Импорт в
    рабочую таблицу channels остаётся отдельной командой candidate-import.
    """
    token = config.TGSTAT_API_TOKEN
    if not token:
        raise TGStatAPIError(
            "Не задан TGSTAT_API_TOKEN в .env. Получите токен в личном кабинете TGStat; "
            "поиск каналов требует API Stat тарифа S или выше."
        )
    if not 1 <= limit <= MAX_LIMIT:
        raise ValueError(f"limit должен быть от 1 до {MAX_LIMIT}")
    if delay < 0:
        raise ValueError("delay не может быть отрицательным")

    query_list = list(dict.fromkeys(q.strip() for q in (queries or []) if q and q.strip()))
    category_list = list(dict.fromkeys(c.strip() for c in (categories or []) if c and c.strip()))
    if not query_list and not category_list:
        raise ValueError("Укажите хотя бы один --query или --category")
    if any(len(q) < 3 for q in query_list):
        raise ValueError("TGStat требует минимум 3 символа в параметре q")

    jobs = [(q, None) for q in query_list] + [(None, c) for c in category_list]
    from . import db

    conn = db.connect()
    seen_count = new_count = errors = 0
    try:
        for index, (query, category) in enumerate(jobs, start=1):
            label = query or f"категория: {category}"
            try:
                payload = _fetch(
                    token=token, query=query, category=category,
                    country=country, language=language, limit=limit,
                    search_by_description=search_by_description,
                )
                found = _extract_channels(payload, query=query, category=category)
                with db.transaction(conn):
                    for candidate in found:
                        source_ref = query or f"category:{category}"
                        new_count += int(db.upsert_candidate(
                            conn, candidate,
                            source_name="tgstat_api",
                            source_ref=source_ref,
                            source_url="https://tgstat.ru/channels/search",
                        ))
                        seen_count += 1
                log.info("[%d/%d] %s — найдено каналов: %d", index, len(jobs), label, len(found))
            except (requests.RequestException, TGStatAPIError, ValueError) as exc:
                errors += 1
                log.error("[%d/%d] %s — ошибка: %s", index, len(jobs), label, exc)
                # Не продолжаем после явного rate limit/API credential failure.
                error_text = str(exc).casefold()
                if isinstance(exc, TGStatAPIError) and any(
                    marker in error_text for marker in (
                        "http 429", "http 401", "доступ к channels/search запрещён"
                    )
                ):
                    break
            if index < len(jobs):
                time.sleep(delay)
    finally:
        conn.close()

    log.info(
        "TGStat завершён: получено записей %d, новых username %d, ошибок запросов %d",
        seen_count, new_count, errors,
    )
    log.info("Проверить кандидатов: python main.py candidate-stats")
    return seen_count
