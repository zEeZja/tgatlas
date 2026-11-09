"""Нормализация публичной идентичности Telegram-канала без зависимости от БД."""

import re
from urllib.parse import urlparse


def normalize_username(value: str) -> str:
    """Вернуть публичный username Telegram или пустую строку.

    Принимает @username, username, https://t.me/username и t.me/s/username.
    Не принимает ссылки на отдельные сообщения, приватные invite-links и
    внутренние ссылки формата t.me/c/<id>/<message_id>.
    """
    raw = str(value or "").strip()
    if not raw:
        return ""

    if raw.startswith(("https://", "http://")) or raw.lower().startswith(
        ("t.me/", "telegram.me/", "www.t.me/", "www.telegram.me/")
    ):
        parsed = urlparse(raw if "://" in raw else "https://" + raw)
        host = (parsed.hostname or "").lower()
        if host not in {"t.me", "www.t.me", "telegram.me", "www.telegram.me"}:
            return ""
        parts = [part for part in parsed.path.split("/") if part]
        if len(parts) == 2 and parts[0].lower() == "s":
            parts = parts[1:]
        if len(parts) != 1:
            return ""
        raw = parts[0]

    raw = raw.lstrip("@").strip().lower()
    if not re.fullmatch(r"[a-z][a-z0-9_]{4,31}", raw):
        return ""
    return raw
