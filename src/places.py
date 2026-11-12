"""Versioned place identities, exact aliases, and explicit ambiguity resolution."""

import json
from pathlib import Path
from functools import lru_cache
from .region_classifier import normalize_text

CATALOG = json.loads(
    (Path(__file__).resolve().parent.parent / "config/geo_places.json").read_text(
        encoding="utf-8"
    )
)
PLACES = CATALOG["places"]
PLACE_RULES = [
    (place, normalize_text(alias).split())
    for place in PLACES
    for alias in place["aliases"]
]
CONTEXT = {
    "новости",
    "новостей",
    "жизни",
    "жизнь",
    "город",
    "города",
    "городе",
    "жителей",
    "жители",
    "медиа",
    "события",
    "сообщество",
    "канал",
    "афиша",
}
BIO = {
    "родился",
    "родилась",
    "родом",
    "учился",
    "училась",
    "окончил",
    "окончила",
    "живу",
    "переехал",
    "переехала",
    "проживаю",
}


@lru_cache(maxsize=4096)
def place_candidates(text, contextual=False):
    words = normalize_text(text).split()
    hits = []
    for place, parts in PLACE_RULES:
        for start in range(len(words) - len(parts) + 1):
            end = start + len(parts)
            if words[start:end] != parts:
                continue
            nearby = words[max(0, start - 4) : min(len(words), end + 4)]
            if contextual and (
                not CONTEXT.intersection(nearby) or BIO.intersection(nearby)
            ):
                continue
            hits.append((place, start, end))
    kept = [
        h
        for h in hits
        if not any(
            k[2] - k[1] > h[2] - h[1] and k[1] <= h[1] and k[2] >= h[2] for k in hits
        )
    ]
    return tuple(sorted({p["id"] for p, _, _ in kept}))


def resolve_places(text, hint="", contextual=False):
    ids = place_candidates(text, contextual)
    hits = [p for p in PLACES if p["id"] in ids]
    if not hits:
        return [], False
    regions = {p["region"] for p in hits}
    if len(regions) > 1:
        # An explicit region name can disambiguate a homonymous city. The city
        # alias itself is not counted again as a region hint.
        context = normalize_text(str(text) + " " + str(hint))
        selected = [p for p in hits if normalize_text(p["region"]) in context]
        if len({p["region"] for p in selected}) == 1:
            hits = selected
        else:
            return hits, True
    return hits, False
