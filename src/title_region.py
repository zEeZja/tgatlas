"""Conservative place evidence from titles and contextual profile descriptions."""

from functools import lru_cache
import json
import re
from pathlib import Path

from .classification_text import informative_description
from .places import resolve_places
from .region_aliases import REGION_ALIASES
from .region_classifier import normalize_text

RULES = [
    (region, tuple(normalize_text(alias).split()))
    for region, aliases in REGION_ALIASES.items()
    for alias in aliases
]
FOREIGN = json.loads(
    (
        Path(__file__).resolve().parent.parent / "config/foreign_geography.json"
    ).read_text(encoding="utf-8")
)
BIOGRAPHY = {
    "родился",
    "родилась",
    "родом",
    "учился",
    "училась",
    "окончил",
    "окончила",
    "переехал",
    "переехала",
    "живу",
    "проживаю",
}
GEO_CONTEXT = {
    "новости",
    "новостей",
    "новостях",
    "жизнь",
    "жизни",
    "события",
    "событий",
    "города",
    "городе",
    "жители",
    "жителей",
    "жителям",
    "медиа",
    "мультимедиа",
    "афиша",
    "афиши",
    "канал",
    "телеграм",
    "сообщество",
    "сообщества",
    "журналист",
    "журналиста",
    "газета",
    "газеты",
    "портал",
    "портала",
}


@lru_cache(maxsize=4096)
def find_title_regions(name, contextual=False):
    tokens = normalize_text(name).split()
    hits = []
    for region, parts in RULES:
        for start in range(len(tokens) - len(parts) + 1):
            end = start + len(parts)
            if all(
                a == b or (len(b) >= 5 and a.startswith(b))
                for a, b in zip(tokens[start:], parts)
            ):
                if contextual and BIOGRAPHY.intersection(
                    tokens[max(0, start - 4) : min(len(tokens), end + 2)]
                ):
                    continue
                if contextual and not GEO_CONTEXT.intersection(
                    tokens[max(0, start - 6) : min(len(tokens), end + 6)]
                ):
                    continue
                hits.append((region, start, end, sum(map(len, parts))))
    kept = [
        h
        for h in hits
        if not any(k[3] > h[3] and k[1] <= h[1] and k[2] >= h[2] for k in hits)
    ]
    return tuple(sorted({h[0] for h in kept}))


def infer_title_region(name):
    regions = find_title_regions(name)
    return regions[0] if len(regions) == 1 else None


def infer_foreign_title(name):
    text = normalize_text(name).split()
    matches = []
    for place in FOREIGN:
        if any(
            any(t == alias or (len(alias) >= 5 and t.startswith(alias)) for t in text)
            for alias in place["aliases"]
        ):
            matches.append(place)
    if len({p["country"] for p in matches}) != 1:
        return None
    cities = [p for p in matches if p["scope"] == "region"]
    return (
        cities[0]
        if len(cities) == 1
        else matches[0]
        if not cities and len(matches) == 1
        else None
    )


def profile_geography(channel):
    """Return a region only with unambiguous evidence, never from ad contacts."""
    name = channel.get("name") or ""
    places, ambiguous = resolve_places(name, channel.get("region_hint") or "")
    if ambiguous:
        return [], None, "conflict"
    if places:
        f = infer_foreign_title(name)
        if f and f["country"] != "RU":
            return [], None, "conflict"
        return sorted({p["region"] for p in places}), None, "name"
    if any(
        w in normalize_text(name).split()
        for w in ("проспект", "улица", "шоссе", "переулок")
    ):
        return [], None, "conflict"
    title = find_title_regions(name)
    foreign = infer_foreign_title(name)
    if title or foreign:
        if len(title) == 1 and (
            not foreign
            or (foreign["scope"] == "country" and foreign["country"] == "RU")
        ):
            return list(title), None, "name"
        if foreign and not title:
            return (
                [foreign["label"]] if foreign["scope"] == "region" else [],
                foreign,
                "name",
            )
        return [], None, "conflict"
    desc = informative_description(channel.get("description"))
    places, ambiguous = resolve_places(desc, channel.get("region_hint") or "", True)
    if ambiguous:
        return [], None, "conflict"
    if places:
        return sorted({p["region"] for p in places}), None, "description"
    description = find_title_regions(desc, True)
    hint = find_title_regions(channel.get("region_hint") or "")
    if len(description) > 1 or (description and hint and set(description) != set(hint)):
        return [], None, "conflict"
    if len(description) == 1:
        return list(description), None, "description"
    if len(hint) == 1:
        return list(hint), None, "hint"
    return [], None, "none"


def local_news_evidence(name, description):
    normalized = normalize_text(name)
    tokens = normalized.split()
    region = infer_title_region(name)
    foreign = infer_foreign_title(name)
    if not region and not (foreign and foreign["scope"] == "region"):
        desc = informative_description(description)
        tokens_desc = normalize_text(desc).split()
        if len(find_title_regions(desc, True)) == 1 and any(
            t in {"медиа", "мультимедиа"} or t.startswith("новост") for t in tokens_desc
        ):
            return "description:городское медиа (требует проверки темы)"
        return None
    markers = {
        "сейчас",
        "сегодня",
        "онлайн",
        "online",
        "live",
        "life",
        "лайф",
        "подслушано",
        "типичный",
        "типичная",
        "главный",
        "события",
        "жесть",
        "чпн",
    }
    if (
        markers.intersection(tokens)
        or "в курсе" in normalized
        or re.search(r"№\s*1\b|\.(?:ru|рф|txt)\b", name, re.I)
    ):
        return "name:городской формат (город + сейчас/онлайн/подслушано)"
    desc = normalize_text(informative_description(description))
    if any(
        x in desc
        for x in [
            "поделиться новост",
            "прислать новост",
            "присылайте новост",
            "предложить новост",
            "новости города",
            "новости нашего города",
            "события города",
            "народных новост",
        ]
    ):
        return "description:приём городских новостей"
    if any(t.startswith("новост") or t.startswith("событи") for t in desc.split()):
        return "description:город и новостное описание (требует проверки темы)"
    aliases = REGION_ALIASES[region] if region else foreign["aliases"]
    if normalized in {normalize_text(a) for a in aliases}:
        return "name:название города (требует проверки темы)"
    return None
