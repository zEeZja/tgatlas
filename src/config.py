"""Чтение настроек из .env и константы проекта."""

import os
import math
import logging
from pathlib import Path
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")


def _number(name, default, cast=int, *, optional=False):
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        value = cast(raw)
        if not math.isfinite(value):
            raise ValueError
        return value
    except (ValueError, OverflowError):
        if optional:
            logging.getLogger(__name__).warning(
                "%s: некорректное числовое значение; используется значение по умолчанию",
                name,
            )
            return default
        raise ValueError(f"{name}: ожидается конечное число") from None


CHANNELS_FILE = ROOT / "config" / "channels.json"

PG = {
    "host": os.getenv("PG_HOST", "localhost"),
    "port": _number("PG_PORT", 5432),
    "user": os.getenv("PG_USER", "postgres"),
    "password": os.getenv("PG_PASSWORD", ""),
    "dbname": os.getenv("PG_DATABASE", "tg_parser"),
}

REQUEST_TIMEOUT = _number("REQUEST_TIMEOUT", 20)
# Жёсткий нижний предел аудитории для попадания кандидата в рабочую базу.
MIN_CHANNEL_SUBSCRIBERS = max(0, _number("MIN_CHANNEL_SUBSCRIBERS", 1000))
DELAY_MIN = _number("DELAY_MIN", 1.5, float)
DELAY_MAX = _number("DELAY_MAX", 3.5, float)

# ─── Пагинация t.me/s/ ─────────────────────────────────────────────────
# Сколько страниц (по ~20 постов) тянуть с одного канала за один scrape.
# 5 страниц ≈ 100 последних постов. 0 — только первая страница.
MAX_PAGES = _number("MAX_PAGES", 5)

USER_AGENT = os.getenv(
    "USER_AGENT",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
)

# ─── Семантическая карта ───────────────────────────────────────────────
# Доля притяжения точки к центру своего региона (0 — выкл, 1 — в центр).
SEMANTIC_ATTRACTION = _number("SEMANTIC_ATTRACTION", 0.25, float, optional=True)
UMAP_N_NEIGHBORS = _number("SEMANTIC_UMAP_N_NEIGHBORS", 10, int, optional=True)
UMAP_MIN_DIST = _number("SEMANTIC_UMAP_MIN_DIST", 0.08, float, optional=True)

# ─── MTProto (Telethon) ────────────────────────────────────────────────
# Получить на https://my.telegram.org → API development tools.
TGSTAT_API_TOKEN = os.getenv("TGSTAT_API_TOKEN", "").strip()

TG_API_ID = _number("TG_API_ID", 0, int, optional=True)
TG_API_HASH = os.getenv("TG_API_HASH", "").strip()
TG_SESSION_NAME = (
    os.getenv("TG_SESSION_NAME", "atlas_backup_account").strip()
    or "atlas_backup_account"
)
TG_EXPECTED_USER_ID = _number("TG_EXPECTED_USER_ID", 0, int, optional=True)
TG_RECOMMENDATION_DELAY = _number("TG_RECOMMENDATION_DELAY", 2.0, float, optional=True)
TG_MAX_RECOMMENDATION_SEEDS = _number(
    "TG_MAX_RECOMMENDATION_SEEDS", 10, int, optional=True
)

# Пустое значение означает "без прокси" (аккаунт за системным VPN).
# Для HTTP- или SOCKS-прокси задайте TG_PROXY_HOST/PORT/TYPE.
TG_PROXY_HOST = os.getenv("TG_PROXY_HOST", "").strip() or None
TG_PROXY_PORT = _number("TG_PROXY_PORT", 0, int, optional=True) or None
TG_PROXY_TYPE = os.getenv("TG_PROXY_TYPE", "socks5").strip().lower()
TG_PROXY_USER = os.getenv("TG_PROXY_USER", "").strip() or None
TG_PROXY_PASS = os.getenv("TG_PROXY_PASS", "").strip() or None

# Bounded public-preview polling. One global request gate is shared by all workers.
SCRAPE_WORKERS = max(1, min(8, _number("SCRAPE_WORKERS", 4)))
SCRAPE_REQUEST_INTERVAL = max(0.1, _number("SCRAPE_REQUEST_INTERVAL", 1.0, float))

if not 1 <= PG["port"] <= 65535:
    raise ValueError("PG_PORT должен быть от 1 до 65535")
if REQUEST_TIMEOUT <= 0:
    raise ValueError("REQUEST_TIMEOUT должен быть больше нуля")
if DELAY_MIN < 0 or DELAY_MAX < DELAY_MIN:
    raise ValueError("Ожидается 0 <= DELAY_MIN <= DELAY_MAX")
