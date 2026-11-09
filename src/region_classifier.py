"""Консервативная классификация Telegram-канала по субъекту РФ.

Классификатор не пытается угадать регион любой ценой. Он собирает признаки
из названия, username, описания и подсказки каталога, а при конфликте оставляет
канал без региона и передаёт его на ручную проверку.

Confidence — эвристическая оценка силы совпадения, а не статистическая
вероятность правильной классификации.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Iterable

from .region_aliases import REGION_ALIASES, USERNAME_HINTS

MIN_CONFIDENCE = 0.78
AMBIGUITY_MARGIN = 0.07


def normalize_text(value: str | None) -> str:
    """Нормализует текст, сохраняя кириллицу и латиницу."""
    text = unicodedata.normalize("NFKC", str(value or "")).casefold().replace("ё", "е")
    text = re.sub(r"[\W_]+", " ", text, flags=re.UNICODE)
    return re.sub(r"\s+", " ", text).strip()


def _tokens(value: str | None) -> list[str]:
    normalized = normalize_text(value)
    return normalized.split() if normalized else []


def _part_matches(token: str, alias_part: str) -> bool:
    """Точное совпадение либо префикс достаточно длинного стема."""
    if token == alias_part:
        return True
    # Не даём коротким словам давать ложные срабатывания:
    # «луга» не должно совпадать с «луганск», «чита» — с «читает».
    return len(alias_part) >= 5 and token.startswith(alias_part)


def _alias_match(tokens: list[str], alias: str) -> tuple[bool, int, int, int, int]:
    parts = _tokens(alias)
    if not parts or len(parts) > len(tokens):
        return False, 0, 0, -1, -1
    for start in range(len(tokens) - len(parts) + 1):
        window = tokens[start:start + len(parts)]
        if all(_part_matches(token, part) for token, part in zip(window, parts)):
            return True, sum(map(len, parts)), len(parts), start, start + len(parts)
    return False, 0, 0, -1, -1


_GEO_CONTEXT = {
    "новости", "новостной", "канал", "жителей", "жители", "житель",
    "город", "города", "городе", "район", "района", "районе",
    "области", "края", "республики", "администрации", "события",
    "местные", "местного", "регион", "регионе", "присылайте",
    "прислать", "подписывайтесь", "весь", "главный", "наш", "нашего",
    "место", "месте", "происшествия", "афиша", "жизнь", "онлайн",
}


def _field_matches(value: str | None, field: str) -> dict[str, tuple[float, str]]:
    tokens = _tokens(value)
    if not tokens:
        return {}

    # Подсказка каталога весома, но уступает явному географическому
    # упоминанию в названии. Описание используется только с контекстом.
    weights = {
        "name": (0.77, 0.97),
        "username": (0.70, 0.93),
        "hint": (0.86, 0.94),
        "description": (0.54, 0.84),
    }
    base, cap = weights[field]
    normalized = " ".join(tokens)
    hits: list[tuple[str, float, str, int, int]] = []

    for region, aliases in REGION_ALIASES.items():
        for alias in aliases:
            matched, length, words, start, end = _alias_match(tokens, alias)
            if not matched:
                continue
            score = min(cap, base + min(length, 20) * 0.009 + (0.035 if words > 1 else 0))
            evidence = f"{field}:{alias}"

            if field == "description":
                nearby = tokens[max(0, start - 3):min(len(tokens), end + 3)]
                if any(token in _GEO_CONTEXT for token in nearby):
                    score = min(cap, score + 0.22)
                    evidence += " (географический контекст)"

            # Совпадение полного официального названия субъекта — сильный признак.
            canonical = _tokens(region)
            if canonical and len(canonical) > 1:
                full, _, _, _, _ = _alias_match(tokens, region)
                if full:
                    score = min(cap, max(score, base + 0.17))
                    evidence = f"{field}:полное название региона"

            hits.append((region, score, evidence, start, end))

    # Длинное составное совпадение подавляет вложенное короткое совпадение
    # другого субъекта: «Нижний Новгород» не должен давать «Новгородскую область».
    filtered = []
    for hit in hits:
        region, score, evidence, start, end = hit
        contained = any(
            other[0] != region
            and (other[4] - other[3]) > (end - start)
            and other[3] <= start and other[4] >= end
            for other in hits
        )
        if not contained:
            filtered.append(hit)

    by_region: dict[str, list[tuple[float, str]]] = {}
    for region, score, evidence, *_ in filtered:
        by_region.setdefault(region, []).append((score, evidence))

    found: dict[str, tuple[float, str]] = {}
    for region, matches in by_region.items():
        matches.sort(key=lambda item: item[0], reverse=True)
        # Два независимых географических признака одного субъекта повышают оценку.
        bonus = min(0.08, max(0, len(matches) - 1) * 0.04)
        score, evidence = matches[0]
        found[region] = (min(cap, score + bonus), evidence)

    # Коды вроде msk/spb применяются только как отдельный токен username.
    if field == "username":
        for code, region in USERNAME_HINTS.items():
            if code in tokens:
                score = 0.91 if len(code) >= 5 else 0.88
                old = found.get(region)
                if old is None or score > old[0]:
                    found[region] = (score, f"username-код:{code}")

    return found


@dataclass(frozen=True)
class RegionResult:
    region: str | None
    channel_type: str
    confidence: float
    source: str
    reason: str
    candidates: tuple[tuple[str, float], ...] = ()

    @property
    def needs_review(self) -> bool:
        return self.channel_type == "unknown"


def _load_federal_set(federal_channels: Iterable[str] | None) -> set[str]:
    return {
        normalize_text(username).replace(" ", "").lstrip("@")
        for username in (federal_channels or ()) if username
    }


def classify_channel(
    username: str,
    name: str | None = None,
    description: str | None = None,
    region_hint: str | None = None,
    federal_channels: Iterable[str] | None = None,
) -> RegionResult:
    """Классифицировать канал с сохранением альтернативных кандидатов."""
    clean_username = normalize_text(username).replace(" ", "").lstrip("@")
    if clean_username in _load_federal_set(federal_channels):
        return RegionResult(None, "federal", 1.0, "whitelist", "Канал находится в федеральном whitelist")

    scores: dict[str, tuple[float, str]] = {}
    for field, value in (
        ("hint", region_hint),
        ("name", name),
        ("username", username),
        ("description", description),
    ):
        for region, (score, evidence) in _field_matches(value, field).items():
            current = scores.get(region)
            if current is None or score > current[0]:
                scores[region] = (score, evidence)
            elif score == current[0] and evidence not in current[1]:
                scores[region] = (score, current[1] + ";" + evidence)

    if not scores:
        return RegionResult(None, "unknown", 0.0, "none", "Не найдено региональных признаков")

    # Если в самом названии одновременно фигурируют два разных региона
    # с близкими оценками, подсказка каталога не должна «перебить» конфликт.
    name_scores = _field_matches(name, "name")
    name_ranked = sorted(name_scores.items(), key=lambda item: (-item[1][0], item[0]))
    if len(name_ranked) > 1 and name_ranked[0][1][0] - name_ranked[1][1][0] < AMBIGUITY_MARGIN:
        candidates = tuple((region, round(score, 3)) for region, (score, _) in name_ranked[:5])
        top_region, (top_score, top_evidence) = name_ranked[0]
        return RegionResult(
            None, "unknown", round(top_score, 3), top_evidence,
            "В названии канала явно упомянуты разные регионы", candidates,
        )

    ranked = sorted(scores.items(), key=lambda item: (-item[1][0], item[0]))
    best_region, (best_score, evidence) = ranked[0]
    second_score = ranked[1][1][0] if len(ranked) > 1 else 0.0
    candidates = tuple((region, round(score, 3)) for region, (score, _) in ranked[:5])

    if best_score < MIN_CONFIDENCE:
        return RegionResult(
            None, "unknown", round(best_score, 3), evidence,
            "Слабое совпадение; нужна ручная проверка", candidates,
        )
    if second_score and best_score - second_score < AMBIGUITY_MARGIN:
        return RegionResult(
            None, "unknown", round(best_score, 3), evidence,
            "Обнаружено несколько близких региональных кандидатов", candidates,
        )

    return RegionResult(
        best_region, "regional", round(best_score, 3), evidence,
        "Определён по региональным признакам", candidates,
    )


def classify_many(
    channels: Iterable[dict], federal_channels: Iterable[str] | None = None,
) -> list[dict]:
    """Классифицировать список словарей (удобно для тестов и отчётов)."""
    result = []
    for channel in channels:
        classification = classify_channel(
            username=channel.get("username", ""),
            name=channel.get("name"),
            description=channel.get("description"),
            region_hint=channel.get("region_hint", channel.get("region")),
            federal_channels=federal_channels,
        )
        result.append({
            **channel,
            "region": classification.region,
            "channel_type": classification.channel_type,
            "region_confidence": classification.confidence,
            "region_source": classification.source,
            "region_reason": classification.reason,
            "region_candidates": [
                {"region": region, "confidence": confidence}
                for region, confidence in classification.candidates
            ],
        })
    return result
