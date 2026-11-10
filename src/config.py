"""Чтение настроек из .env и константы проекта."""

import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

ROOT = Path(__file__).resolve().parent.parent
CHANNELS_FILE = ROOT / "config" / "channels.json"

PG = {
    "host":     os.getenv("PG_HOST", "localhost"),
    "port":     int(os.getenv("PG_PORT", "5432")),
    "user":     os.getenv("PG_USER", "postgres"),
    "password": os.getenv("PG_PASSWORD", ""),
    "dbname":   os.getenv("PG_DATABASE", "tg_parser"),
}

REQUEST_TIMEOUT = int(os.getenv("REQUEST_TIMEOUT", "20"))
# Жёсткий нижний предел аудитории для попадания кандидата в рабочую базу.
MIN_CHANNEL_SUBSCRIBERS = max(0, int(os.getenv("MIN_CHANNEL_SUBSCRIBERS", "1000")))
DELAY_MIN = float(os.getenv("DELAY_MIN", "1.5"))
DELAY_MAX = float(os.getenv("DELAY_MAX", "3.5"))

# ─── Пагинация t.me/s/ ─────────────────────────────────────────────────
# Сколько страниц (по ~20 постов) тянуть с одного канала за один scrape.
# 5 страниц ≈ 100 последних постов. 0 — только первая страница.
MAX_PAGES = int(os.getenv("MAX_PAGES", "5"))

USER_AGENT = os.getenv(
    "USER_AGENT",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
)

# ─── Семантическая карта ───────────────────────────────────────────────
# Доля притяжения точки к центру своего региона (0 — выкл, 1 — в центр).
SEMANTIC_ATTRACTION = float(os.getenv("SEMANTIC_ATTRACTION", "0.25"))
UMAP_N_NEIGHBORS = int(os.getenv("SEMANTIC_UMAP_N_NEIGHBORS", "10"))
UMAP_MIN_DIST = float(os.getenv("SEMANTIC_UMAP_MIN_DIST", "0.08"))

# ─── MTProto (Telethon) ────────────────────────────────────────────────
# Получить на https://my.telegram.org → API development tools.
TGSTAT_API_TOKEN = os.getenv("TGSTAT_API_TOKEN", "").strip()

TG_API_ID = int(os.getenv("TG_API_ID", "0") or "0")
TG_API_HASH = os.getenv("TG_API_HASH", "").strip()
TG_SESSION_NAME = os.getenv("TG_SESSION_NAME", "atlas_backup_account").strip() or "atlas_backup_account"
TG_EXPECTED_USER_ID = int(os.getenv("TG_EXPECTED_USER_ID", "0") or "0")
TG_RECOMMENDATION_DELAY = float(os.getenv("TG_RECOMMENDATION_DELAY", "2.0"))
TG_MAX_RECOMMENDATION_SEEDS = int(os.getenv("TG_MAX_RECOMMENDATION_SEEDS", "10"))

# Пустое значение означает "без прокси" (аккаунт за системным VPN).
# Для HTTP- или SOCKS-прокси задайте TG_PROXY_HOST/PORT/TYPE.
TG_PROXY_HOST = os.getenv("TG_PROXY_HOST", "").strip() or None
TG_PROXY_PORT = int(os.getenv("TG_PROXY_PORT", "0") or "0") or None
TG_PROXY_TYPE = os.getenv("TG_PROXY_TYPE", "socks5").strip().lower()
TG_PROXY_USER = os.getenv("TG_PROXY_USER", "").strip() or None
TG_PROXY_PASS = os.getenv("TG_PROXY_PASS", "").strip() or None

# Bounded public-preview polling. One global request gate is shared by all workers.
SCRAPE_WORKERS = max(1, min(8, int(os.getenv("SCRAPE_WORKERS", "4"))))
SCRAPE_REQUEST_INTERVAL = max(0.1, float(os.getenv("SCRAPE_REQUEST_INTERVAL", "1.0")))
