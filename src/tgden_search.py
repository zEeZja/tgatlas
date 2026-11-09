"""Обнаружение публичных Telegram-каналов через прежний suggest endpoint tgden.

В ранней версии Atlas использовался GET /api/suggest?q=... и группа
``groups[key=channels]``. В ветке 0.7.x этот механизм был заменён на
неподтверждённый /api/catalog, который в ноябре 2026 г. вернул статический
набор demo-* для разных запросов. Здесь восстановлен прежний маршрут.

/api/suggest — endpoint подсказок, а не bulk-выгрузка всего каталога.
Подписчики обычно не возвращаются этим endpoint; неизвестная аудитория
проверяется отдельной командой discover-validate через публичную страницу t.me.
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Iterable, Optional

import requests

from . import config
from .identity import normalize_username
from .metrics import parse_count

log = logging.getLogger(__name__)
SUGGEST_URL = "https://tgden.com/api/suggest"
DEFAULT_QUERY_FILE = config.ROOT / "config" / "discovery_queries.json"
DEFAULT_RESULT_LIMIT = 20
MAX_RESULT_LIMIT = 100
DELAY_BETWEEN_QUERIES = 1.5
CITY_QUERY_HINTS = {
    "москва", "санкт-петербург", "новосибирск", "екатеринбург", "казань",
    "нижний новгород", "челябинск", "самара", "омск", "ростов-на-дону",
    "уфа", "красноярск", "воронеж", "пермь", "волгоград", "краснодар",
    "иркутск", "тюмень", "хабаровск", "владивосток", "калининград",
    "саратов", "ярославль", "томск", "барнаул", "мурманск", "архангельск",
    "ставрополь", "сочи", "белгород", "курск", "тула", "рязань", "ижевск",
    "оренбург", "пенза", "липецк", "тверь", "вологда", "чита", "якутск",
    "махачкала", "грозный", "симферополь",
}


def load_default_queries(path: Path = DEFAULT_QUERY_FILE) -> list[str]:
    """Прочитать и дедуплицировать поисковые запросы из JSON."""
    if not path.is_file():
        raise FileNotFoundError(f"Файл запросов не найден: {path}")
    payload = json.loads(path.read_text(encoding="utf-8"))
    queries = payload.get("queries") if isinstance(payload, dict) else payload
    if not isinstance(queries, list):
        raise ValueError("discovery_queries.json должен содержать массив queries")
    result, seen = [], set()
    for query in queries:
        if not isinstance(query, str) or not query.strip():
            continue
        clean = query.strip()
        key = clean.casefold()
        if key not in seen:
            seen.add(key)
            result.append(clean)
    return result


def _payload_items(payload: dict) -> list[dict]:
    """Достать записи из форматов suggest, встречавшихся в ответах tgden."""
    groups = payload.get("groups")
    if isinstance(groups, list):
        rows = []
        for group in groups:
            if isinstance(group, dict) and group.get("key") == "channels":
                rows.extend(x for x in group.get("items", []) if isinstance(x, dict))
        return rows
    for key in ("items", "results", "channels"):
        if isinstance(payload.get(key), list):
            return [x for x in payload[key] if isinstance(x, dict)]
    data = payload.get("data")
    if isinstance(data, list):
        return [x for x in data if isinstance(x, dict)]
    if isinstance(data, dict):
        for key in ("items", "results", "channels"):
            if isinstance(data.get(key), list):
                return [x for x in data[key] if isinstance(x, dict)]
    return []


def _is_demo_payload(payload: dict) -> bool:
    """Блокировать тестовые записи вида demo-000001, если они встретятся."""
    items = _payload_items(payload)
    ids = [str(item.get("id") or item.get("uuid") or "").strip().casefold()
           for item in items]
    ids = [value for value in ids if value]
    return bool(ids) and all(value.startswith("demo-") for value in ids)


def _fetch_suggest(query: str) -> dict:
    """Выполнить UTF-8 поисковый запрос к прежнему tgden suggest endpoint."""
    response = requests.get(
        SUGGEST_URL,
        params={"q": query},
        headers={
            "User-Agent": config.USER_AGENT,
            "Accept": "application/json",
        },
        timeout=config.REQUEST_TIMEOUT,
    )
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, dict):
        raise ValueError("tgden /api/suggest вернул JSON неожиданного формата")
    if _is_demo_payload(payload):
        raise ValueError("tgden /api/suggest вернул демонстрационные demo-* записи")
    if not isinstance(payload.get("groups"), list) and not _payload_items(payload):
        raise ValueError("tgden /api/suggest не вернул ожидаемую группу channels")
    return payload


def _first_value(item: dict, *keys):
    for key in keys:
        value = item.get(key)
        if value not in (None, "", [], {}):
            return value
    return None


def _extract_channels(payload: dict, query: str) -> list[dict]:
    """Извлечь только публичные broadcast-каналы из ответа tgden."""
    items = _payload_items(payload)
    result = []
    for item in items:
        kind = str(_first_value(item, "kind", "type", "entity_type") or "channel").casefold()
        if kind not in {"channel", "broadcast", ""}:
            continue
        if item.get("is_private") is True or item.get("private") is True:
            continue

        raw_username = _first_value(item, "username", "channel_username", "handle")
        # В старом ответе tgden поле sub иногда дублировало @username.
        if not raw_username:
            sub = item.get("sub")
            if isinstance(sub, str) and sub.strip().startswith("@"):
                raw_username = sub.strip()
        if not raw_username:
            href = _first_value(item, "href", "path")
            if isinstance(href, str):
                if "/channel/" in href:
                    raw_username = href.rsplit("/channel/", 1)[-1].split("?", 1)[0].strip("/")
                elif "t.me/" in href:
                    raw_username = href.split("t.me/", 1)[-1].split("?", 1)[0].strip("/")
        username = normalize_username(raw_username or "")
        if not username:
            continue

        categories = _first_value(item, "categories", "category", "topic")
        if isinstance(categories, list):
            categories = ", ".join(str(x) for x in categories if x)

        subscribers = parse_count(_first_value(
            item, "subscribers", "participants_count", "members_count", "members"
        ))
        result.append({
            "username": username,
            "telegram_id": parse_count(_first_value(item, "telegram_id", "channel_id", "tg_id")),
            "name": _first_value(item, "label", "title", "name") or username,
            "description": _first_value(item, "description", "about"),
            "category": categories,
            "subcategory": _first_value(item, "subcategory", "sub_category"),
            "country": _first_value(item, "country", "country_code"),
            "region_hint": (
                _first_value(item, "region", "city", "location")
                or (query if query.casefold() in CITY_QUERY_HINTS else None)
            ),
            "subscribers": subscribers,
            "catalog_query": query,
            "catalog_id": _first_value(item, "id", "uuid"),
        })
    # Autocomplete can return the same username more than once.
    unique = {}
    for row in result:
        unique[row["username"]] = row
    return list(unique.values())


def run(
    queries: Optional[Iterable[str]] = None,
    per_query_limit: int = DEFAULT_RESULT_LIMIT,
    delay: float = DELAY_BETWEEN_QUERIES,
) -> int:
    """Найти каналы через suggest и добавить их в общий реестр кандидатов."""
    target_queries = list(queries) if queries else load_default_queries()
    target_queries = list(dict.fromkeys(q.strip() for q in target_queries if q and q.strip()))
    if not target_queries:
        log.warning("Список запросов пуст — ничего не ищу")
        return 0
    if not 1 <= per_query_limit <= MAX_RESULT_LIMIT:
        raise ValueError(f"per_query_limit должен быть от 1 до {MAX_RESULT_LIMIT}")
    if delay < 1.0:
        raise ValueError("Задержка между запросами не должна быть меньше 1 секунды")

    log.info("Запросов tgden: %d; максимум каналов на запрос: %d", len(target_queries), per_query_limit)
    from . import db

    conn = db.connect()
    new_count = seen_count = failed_queries = 0
    try:
        for index, query in enumerate(target_queries, start=1):
            log.info("[%d/%d] tgden suggest: %s", index, len(target_queries), query)
            try:
                payload = _fetch_suggest(query)
                found = _extract_channels(payload, query)[:per_query_limit]
                with db.transaction(conn):
                    for candidate in found:
                        is_new = db.upsert_candidate(
                            conn,
                            candidate,
                            source_name="tgden_api_suggest",
                            source_ref=query,
                            source_url="https://tgden.com/ru",
                        )
                        new_count += int(is_new)
                        seen_count += 1
                log.info("  каналов-кандидатов: %d", len(found))
            except (requests.RequestException, ValueError) as exc:
                failed_queries += 1
                log.warning("  запрос не выполнен: %s", exc)
            if index < len(target_queries):
                time.sleep(delay)
    finally:
        conn.close()

    log.info("tgden завершён: обработано %d записей, новых username %d, ошибок %d",
             seen_count, new_count, failed_queries)
    log.info("Следующий шаг: python main.py discover-validate --limit 100")
    return seen_count
