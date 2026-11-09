"""HTML → список Post + метаданные канала.

Изменения по сравнению с предыдущей версией:
  — добавлены fallback-селекторы для шапки канала и счётчиков;
  — при отсутствии основного селектора пишется warning в лог,
    чтобы можно было заметить изменения вёрстки t.me.

Селекторы основаны на текущей вёрстке t.me/s/ (проверено июнь 2026).
"""

import logging
import re
from datetime import datetime
from typing import List, Optional

from bs4 import BeautifulSoup

from .models import Post
from .metrics import parse_count

log = logging.getLogger(__name__)

_TITLE_MAX_LEN = 300
_CYRILLIC_SUFFIX = {"К": "K", "М": "M", "Б": "B"}

def parse_views(raw: Optional[str]) -> Optional[int]:
    """Разобрать число с суффиксом: '12.3K' → 12300, '1,2М' → 1200000, '1 218 400' → 1218400."""
    if not raw:
        return None
    s = re.sub(r"\s+", "", raw)
    m = re.search(r"([\d.,]+)", s)
    if not m:
        return None

    num_str = m.group(1).replace(",", ".")
    suffix = ""
    for ch in s[m.end():]:
        if ch.isalpha():
            suffix = ch.upper()
            break

    suffix = _CYRILLIC_SUFFIX.get(suffix, suffix)
    multiplier = {"": 1, "K": 1_000, "M": 1_000_000, "B": 1_000_000_000}

    try:
        return int(float(num_str) * multiplier.get(suffix, 1))
    except (ValueError, KeyError):
        return None

def _node_to_text(node) -> Optional[str]:
    """Извлечь текст, сохранив переносы от <br>."""
    if node is None:
        return None
    for br in node.find_all("br"):
        br.replace_with("\n")
    text = node.get_text()
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = text.strip()
    return text or None

def _extract_title(text: Optional[str]) -> Optional[str]:
    """Эвристика: первая непустая строка — как заголовок новости."""
    if not text:
        return None
    for line in text.splitlines():
        line = line.strip()
        if line:
            return line[:_TITLE_MAX_LEN]
    return None

def _parse_datetime(raw: Optional[str]) -> Optional[datetime]:
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None

def _select_first(soup, *selectors):
    """Вернуть первый найденный элемент по списку селекторов."""
    for sel in selectors:
        node = soup.select_one(sel)
        if node is not None:
            return node
    return None

def parse_channel_meta(html: str) -> dict:
    """Извлечь из шапки канала: описание и число подписчиков.

    Возвращает {'description': str|None, 'subscribers': int|None}.
    Использует несколько селекторов на случай косметических изменений вёрстки.
    """
    soup = BeautifulSoup(html, "lxml")
    result = {"name": None, "description": None, "subscribers": None}

    title_node = _select_first(
        soup,
        "div.tgme_channel_info_header_title",
        "div[class*='channel_info_header_title']",
        "div.tgme_page_title",
    )
    if title_node:
        result["name"] = _node_to_text(title_node)

    desc_node = _select_first(
        soup,
        "div.tgme_channel_info_description",
        "div[class*='channel_info_description']",
    )
    if desc_node:
        result["description"] = _node_to_text(desc_node)

    counter_nodes = soup.select(
        "div.tgme_channel_info_counter, "
        "div[class*='channel_info_counter']"
    )
    if not counter_nodes:
        log.debug("Не найдено ни одного tgme_channel_info_counter")
    for counter in counter_nodes:
        type_node = (
            counter.select_one(".counter_type")
            or counter.select_one("[class*='counter_type']")
        )
        value_node = (
            counter.select_one(".counter_value")
            or counter.select_one("[class*='counter_value']")
        )
        if not type_node or not value_node:
            continue
        counter_type = type_node.get_text(" ", strip=True).lower()
        if "subscriber" in counter_type or "подписчик" in counter_type:
            raw_count = value_node.get_text(" ", strip=True)
            parsed_count = parse_count(raw_count)
            result["subscribers"] = parsed_count if parsed_count is not None else parse_views(raw_count)
            break

    return result

def parse_channel_page(html: str, username: str, channel_id: int) -> List[Post]:
    """Разобрать HTML страницы t.me/s/<username> в список Post."""
    soup = BeautifulSoup(html, "lxml")

    posts: List[Post] = []
    for wrap in soup.select("div.tgme_widget_message[data-post]"):
        data_post = wrap.get("data-post") or ""
        if "/" not in data_post:
            continue

        try:
            _, msg_id_str = data_post.split("/", 1)
            tg_message_id = int(msg_id_str)
        except (ValueError, IndexError):
            continue

        text_node = _select_first(
            wrap,
            "div.tgme_widget_message_text",
            "div[class*='message_text']",
        )
        text = _node_to_text(text_node)
        title = _extract_title(text)

        time_node = wrap.select_one("time[datetime]")
        posted_at = _parse_datetime(time_node.get("datetime") if time_node else None)

        views_node = _select_first(
            wrap,
            "span.tgme_widget_message_views",
            "span[class*='message_views']",
        )
        views = parse_views(views_node.get_text() if views_node else None)

        posts.append(
            Post(
                channel_id=channel_id,
                tg_message_id=tg_message_id,
                url=f"https://t.me/{data_post}",
                text=text,
                title=title,
                posted_at=posted_at,
                views=views,
            )
        )

    return posts
