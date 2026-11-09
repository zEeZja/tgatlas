"""Лёгкая эвристическая классификация тематики Telegram-канала.

Модуль самостоятельный и пока не включён в основной пайплайн: основной
источник рубрики — каталог/ручная настройка. Его можно использовать как
подсказку при будущей модерации и автотегировании.
"""

from __future__ import annotations

import re
import unicodedata
from collections import defaultdict

TOPIC_LABELS = {
    "news": "Новости",
    "business": "Бизнес и экономика",
    "tech": "Технологии",
    "crypto": "Криптовалюты",
    "science": "Наука",
    "sport": "Спорт",
    "military": "Военная сводка",
    "politics": "Политика",
    "culture": "Культура и развлечения",
    "auto": "Авто",
    "health": "Здоровье и медицина",
    "other": "Другое",
}

TOPIC_EMOJI = {
    "news": "📰", "business": "💼", "tech": "💻", "crypto": "🪙",
    "science": "🔬", "sport": "⚽", "military": "🎖", "politics": "🏛",
    "culture": "🎭", "auto": "🚗", "health": "🏥", "other": "📌",
}

TOPIC_KEYWORDS = {
    "news": [
        "новост", "news", "срочно", "breaking", "чп", "происшеств",
        "инцидент", "молния", "экстренн", "сводк", "хроник", "репортаж",
        "репортер", "обзор", "дайджест", "лента", "информ", "сми", "медиа",
    ],
    "business": [
        "бизнес", "business", "экономик", "economy", "финанс", "finance",
        "инвест", "invest", "акци", "stock", "рынок", "market", "банк", "bank",
        "деньг", "money", "валют", "currency", "кредит", "налог", "бюджет",
        "компани", "корпорац", "предпринимат", "стартап", "startup", "венчур",
        "капитал", "торги", "биржа", "дивиденд", "облигац", "недвиж", "аренд",
        "ипотек", "ритейл", "retail", "маркетинг", "менеджмент",
    ],
    "tech": [
        "технолог", "technology", "tech", "гаджет", "gadget", "девайс", "смартфон",
        "компьютер", "ноутбук", "программ", "software", "код", "code", "разработк",
        "программист", "developer", "инженер", "прилож", "сервис", "платформ",
        "интернет", "сервер", "облак", "данн", "искусственн", "нейросет", "robot",
        "робот", "автоматизац", "кибербезопасн", "хакер", "openai", "microsoft",
    ],
    "crypto": [
        "крипт", "crypto", "биткоин", "bitcoin", "btc", "эфириум", "ethereum", "eth",
        "блокчейн", "blockchain", "токен", "token", "кошел", "wallet", "майнинг",
        "mining", "defi", "nft", "web3", "binance", "coinbase", "альткоин",
        "стейблкоин", "smartcontract", "dapp",
    ],
    "science": [
        "наук", "science", "исследован", "research", "космос", "space", "космонавт",
        "физик", "physics", "химик", "chemistry", "биолог", "biology", "медицин",
        "medicine", "генетик", "днк", "археолог", "истори", "history", "открыти",
        "эксперимент", "теори", "гипотез", "астроном", "телескоп", "климат", "эколог",
        "математ", "quantum",
    ],
    "sport": [
        "спорт", "sport", "футбол", "football", "хокке", "hockey", "баскетбол",
        "basketball", "волейбол", "теннис", "бокс", "boxing", "mma", "ufc",
        "олимпиад", "чемпионат", "матч", "турнир", "команд", "игрок", "тренер",
        "стадион", "гол", "победа", "поражен", "фитнес", "тренировк",
    ],
    "military": [
        "военн", "military", "арми", "army", "войн", "war", "вооруж", "всу",
        "мобилизац", "оружи", "weapon", "ракет", "missile", "танк", "авиаци",
        "флот", "дрон", "бпла", "uav", "фронт", "наступлен", "оборон", "defense",
        "генштаб", "военкор", "военкорр",
    ],
    "politics": [
        "полит", "politic", "выбор", "election", "депутат", "госдум", "парламент",
        "правительств", "президент", "министр", "губернатор", "мэр", "законопроект",
        "парт", "санкц", "дипломат", "государств",
    ],
    "culture": [
        "культур", "culture", "кино", "фильм", "movie", "театр", "theatre", "музык",
        "music", "концерт", "афиш", "выставк", "литератур", "книг", "писател",
        "искусств", "art", "дизайн", "мода", "fashion", "звезд", "шоу", "сериал",
    ],
    "auto": [
        "авто", "автомобил", "машин", "car", "cars", "motor", "мото", "водител",
        "дтп", "дорог", "заправк", "транспорт", "автоспорт", "тюнинг", "шины",
    ],
    "health": [
        "здоров", "health", "медицин", "medicine", "врач", "doctor", "клиник", "больниц",
        "лекарств", "фарма", "психолог", "психотерап", "питани", "диет", "фитнес",
    ],
}


def _normalize(value: str | None) -> str:
    text = unicodedata.normalize("NFKC", str(value or "")).casefold().replace("ё", "е")
    return re.sub(r"[\W_]+", " ", text, flags=re.UNICODE).strip()


def classify_topic(name: str = "", username: str = "", description: str = "", threshold: float = 1.0) -> dict:
    """Вернуть лучшую тему, оценку и баллы; при слабом сигнале — other.

    Счёт — эвристика для сортировки кандидатов, не probability.
    """
    fields = {
        "name": (_normalize(name), 3.0),
        "username": (_normalize(username), 2.0),
        "description": (_normalize(description), 1.0),
    }
    scores = defaultdict(float)
    evidence = defaultdict(list)
    for topic, keywords in TOPIC_KEYWORDS.items():
        for keyword in keywords:
            pattern = _normalize(keyword)
            if not pattern:
                continue
            for field, (text, weight) in fields.items():
                if re.search(rf"(?<!\w){re.escape(pattern)}\w*", text):
                    scores[topic] += weight
                    evidence[topic].append(f"{field}:{keyword}")
                    break
    if not scores:
        return {"subcategory": "other", "score": 0.0, "evidence": []}
    topic, score = max(scores.items(), key=lambda item: (item[1], item[0]))
    if score < threshold:
        topic = "other"
    return {
        "subcategory": topic,
        "score": round(score, 3),
        "evidence": evidence.get(topic, []),
    }
