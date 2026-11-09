"""Простые dataclass-модели. Без ORM — намеренно."""

from dataclasses import dataclass
from datetime import datetime
from typing import Optional

@dataclass
class Channel:
    username: str
    name: Optional[str] = None
    category: Optional[str] = None
    country: Optional[str] = None
    subcategory: Optional[str] = None
    region: Optional[str] = None  # canonical subject assigned by classifier
    region_hint: Optional[str] = None  # raw city/region hint from discovery catalogue
    source: Optional[str] = "manual"
    channel_type: Optional[str] = None  # 'federal' | 'regional' | 'unknown'
    telegram_id: Optional[int] = None
    description: Optional[str] = None
    subscribers: Optional[int] = None
    id: Optional[int] = None

@dataclass
class Post:
    channel_id: int
    tg_message_id: int
    url: str
    text: Optional[str] = None
    # Эти поля извлекаются парсером и сохраняются в columns posts.*.
    title: Optional[str] = None
    posted_at: Optional[datetime] = None
    views: Optional[int] = None
