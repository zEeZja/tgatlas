"""Нормализация числовых метрик из внешних каталогов."""

from __future__ import annotations

import re
from typing import Optional


def parse_count(value) -> Optional[int]:
    """Преобразовать число/строку вида 12 345, 12.5K, 4,2 тыс., 1.3 млн.

    Для строки без суффикса запятая с тремя цифрами справа считается
    разделителем тысяч (12,345); с другим количеством цифр — десятичной.
    """
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)

    raw = str(value).strip().casefold().replace("\u00a0", " ")
    if not raw:
        return None

    match = re.search(
        r"([+-]?[\d\s.,]+)\s*(тыс(?:\.|яч(?:а|и)?)?|млн(?:\.)?|млрд(?:\.)?|[kкmмbб])?",
        raw,
        flags=re.IGNORECASE,
    )
    if not match:
        return None

    number_text = re.sub(r"\s+", "", match.group(1))
    suffix = (match.group(2) or "").casefold().rstrip(".")
    if not number_text or number_text in {"+", "-"}:
        return None

    # Убираем разделители разрядов с учётом европейской и американской записи.
    if "." in number_text and "," in number_text:
        decimal_sep = "." if number_text.rfind(".") > number_text.rfind(",") else ","
        thousands_sep = "," if decimal_sep == "." else "."
        normalized = number_text.replace(thousands_sep, "").replace(decimal_sep, ".")
    elif "," in number_text:
        left, right = number_text.rsplit(",", 1)
        if suffix or len(right) != 3:
            normalized = number_text.replace(",", ".")
        else:
            normalized = number_text.replace(",", "")
    elif number_text.count(".") > 1:
        parts = number_text.split(".")
        normalized = "".join(parts[:-1]) + "." + parts[-1]
    else:
        normalized = number_text

    try:
        number = float(normalized)
    except ValueError:
        return None

    multipliers = {
        "": 1,
        "k": 1_000, "к": 1_000, "тыс": 1_000, "тысяч": 1_000,
        "m": 1_000_000, "м": 1_000_000, "млн": 1_000_000,
        "b": 1_000_000_000, "б": 1_000_000_000, "млрд": 1_000_000_000,
    }
    multiplier = multipliers.get(suffix)
    if multiplier is None:
        return None
    return int(number * multiplier)
