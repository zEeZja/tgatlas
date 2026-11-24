"""География: справочник кандидатов + контекст и устойчивость по постам.

Не измеряет аудиторию и не принимает частоту названия за охват. Семантические
оценки — исследовательский сигнал, не калиброванные вероятности.
"""

from collections import defaultdict
from functools import lru_cache
import json
from pathlib import Path
import re

from .classification_text import informative_description
from .region_aliases import REGION_ALIASES
from .region_classifier import normalize_text

ROOT = Path(__file__).resolve().parent.parent
NOISY = {
    "аэропорт",
    "беговой",
    "сокол",
    "центр",
    "город",
    "север",
    "восток",
    "запад",
    "юг",
    "новости",
    "мир",
    "восход",
    "советск",
    "красноармейск",
    "троицк",
}
LOCAL = re.compile(
    r"(?i)новост|афиш|жител|город|област|кра[йея]|республик|местн|событи|куда пойти|жизнь|сообщество|услуги|налог|законодатель|переезд|релокац"
)
BIO = re.compile(r"(?i)родил|родом|учил|окончил|реклам|контакт|регистрац|учредител")
MULTIWORLD = re.compile(
    r"(?i)мировы[ех]|международн|со всего мира|по всему миру|world news"
)


def stem(word):
    if word in {
        "порту",
        "porto",
        "бали",
        "bali",
        "рим",
        "рима",
        "риме",
        "дели",
        "баку",
    }:
        return word
    for suffix in (
        "скими",
        "ского",
        "скому",
        "ская",
        "ский",
        "ской",
        "ское",
        "ских",
        "скую",
        "ские",
        "иями",
        "ами",
        "ями",
        "ия",
        "ии",
        "ию",
        "ья",
        "ье",
        "а",
        "я",
        "ы",
        "и",
        "е",
        "у",
        "й",
        "ь",
    ):
        if word.endswith(suffix) and len(word) - len(suffix) >= 4:
            return word[: -len(suffix)]
    return word


@lru_cache(maxsize=1)
def reference():
    path = ROOT / "config/countries_v11.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    entities = [dict(x, kind="country", region=None) for x in data["countries"]]
    for region, aliases in REGION_ALIASES.items():
        safe = [
            a
            for a in aliases
            if len(normalize_text(a)) >= 5 and normalize_text(a) not in NOISY
        ]
        entities.append(
            dict(
                id="region:" + region,
                label=region,
                region=region,
                country="RU",
                kind="region",
                aliases=[region] + safe,
            )
        )
    places = json.loads((ROOT / "config/geo_places.json").read_text(encoding="utf-8"))[
        "places"
    ]
    foreign = json.loads(
        (ROOT / "config/foreign_geography.json").read_text(encoding="utf-8")
    )
    cities = json.loads(
        (ROOT / "config/geography_city_hints_v11.json").read_text(encoding="utf-8")
    )["countries"]
    for entity in entities:
        if entity["kind"] == "region":
            entity["aliases"] += [
                a
                for p in places
                if p["region"] == entity["region"] and p["country"] == "RU"
                for a in p["aliases"]
            ]
        elif entity["kind"] == "country":
            entity["aliases"] += cities.get(entity["country"], [])
            entity["aliases"] += [
                a
                for p in foreign
                if p["country"] == entity["country"]
                for a in p["aliases"]
            ]
    patterns = []
    for entity in entities:
        for alias in set(entity["aliases"]):
            parts = normalize_text(alias).split()
            if not parts:
                continue
            patterns.append((entity["id"], parts, [stem(w) for w in parts]))
    index = defaultdict(list)
    for item in patterns:
        index[item[2][0][:4]].append(item)
    return entities, index


def mentions(text):
    _, index = reference()
    words = normalize_text(text).split()
    hits = []
    for start, word in enumerate(words):
        for identifier, parts, stems in index.get(stem(word)[:4], []):
            end = start + len(parts)
            if end > len(words):
                continue
            if all(
                part_matches(w, part, s)
                for w, part, s in zip(words[start:end], parts, stems)
            ):
                specificity = sum(
                    len(s) + (3 if w == part else 0)
                    for w, part, s in zip(words[start:end], parts, stems)
                )
                hits.append((identifier, start, end, specificity))
    # Long region/country names suppress nested city/country matches.
    kept = [
        h
        for h in hits
        if not any(
            k[1] <= h[1]
            and k[2] >= h[2]
            and (k[2] - k[1] > h[2] - h[1] or k[1:3] == h[1:3] and k[3] > h[3])
            for k in hits
        )
    ]
    return sorted({h[0] for h in kept})


def part_matches(word, part, root):
    if word == part:
        return True
    if not re.fullmatch(r"[а-я]+", part) or len(root) < 4 or not word.startswith(root):
        return False
    ending = word[len(root) :]
    if part.endswith("ия"):
        return ending in {"ия", "ии", "ию", "ией", "иею", "иям", "иях", "иями"}
    if part.endswith("й"):
        return ending in {
            "й",
            "я",
            "ю",
            "е",
            "ем",
            "и",
            "йский",
            "йская",
            "йской",
            "йского",
            "йские",
            "йских",
        }
    return ending in {
        "",
        "а",
        "я",
        "ы",
        "и",
        "е",
        "у",
        "ю",
        "ой",
        "ом",
        "ам",
        "ям",
        "ах",
        "ях",
        "ами",
        "ями",
        "ий",
        "ая",
        "ое",
        "ую",
        "ого",
        "ому",
        "ые",
        "ых",
        "ыми",
        "ская",
        "ский",
        "ской",
        "ское",
        "скую",
        "ского",
        "скому",
        "ские",
        "ских",
        "скими",
        "ь",
        "ью",
        "ья",
        "ье",
    }


class GeographyResolver:
    def __init__(self, encoder):
        self.encoder = encoder
        self.entities, _ = reference()
        self.by_id = {e["id"]: e for e in self.entities}
        self.focus = None

    def prepare(self):
        if self.focus is None:
            self.focus = self.encoder.encode(
                [
                    f"{e['label']}. Местные новости и события, жизнь жителей, афиша, услуги и сообщество этой территории."
                    for e in self.entities
                ]
            )
            self.background = self.encoder.encode(
                [
                    "Мировые новости и международные события из разных стран, не местный канал.",
                    "Профессиональные знания, технологии, искусство и увлечения без территориальной направленности.",
                    "Упоминание места в биографии автора, рекламе, регистрации организации или отдельном сюжете.",
                ]
            )

    def resolve(self, row, posts, profile_vector, post_vectors, override=None):
        import numpy as np

        if override is not None:
            return manual_geography(override)
        title = row.get("name") or ""
        desc = informative_description(row.get("description"))
        title_ids = set(mentions(title))
        desc_ids = set(
            mentions(
                "\n".join(line for line in desc.splitlines() if not BIO.search(line))
            )
        )
        post_ids = [set(mentions(p["text"][:1000])) for p in posts]
        diverse_dates = len({str(p.get("date") or "")[:10] for p in posts}) >= 2
        ids = (
            title_ids | desc_ids | set().union(*post_ids)
            if post_ids
            else title_ids | desc_ids
        )
        counts = {key: sum(key in found for found in post_ids) for key in ids}
        candidates = sorted(
            ids,
            key=lambda k: (k in title_ids, k in desc_ids, counts[k], k),
            reverse=True,
        )[:8]
        if not candidates:
            return empty_geography("no_geographic_evidence")
        self.prepare()
        documents = np.asarray(post_vectors if len(post_vectors) else [profile_vector])
        ranked = []
        for identifier in candidates:
            entity = self.by_id[identifier]
            column = self.entities.index(entity)
            scores = documents @ self.focus[column]
            margins = scores - (documents @ self.background.T).max(axis=1)
            support = counts[identifier]
            # A title/profile is stronger than an incidental post mention, but
            # a repeated, matching content signal is needed for Russian regions.
            intent = bool(LOCAL.search(title + " " + desc)) and not bool(
                MULTIWORLD.search(title + " " + desc)
            )
            semantic = float(np.mean(margins))
            rank_score = (
                3 * (identifier in title_ids)
                + 1.5 * (identifier in desc_ids)
                + min(2, support / 2)
                + semantic
            )
            eligible = intent and (
                (
                    identifier in title_ids
                    and (not posts or support >= 1 or len(ids) == 1)
                )
                or (identifier in desc_ids and support >= 2)
            )
            if (
                support >= 3
                and posts
                and diverse_dates
                and support / len(posts) >= 0.6
                and semantic > 0
                and not MULTIWORLD.search(title + " " + desc)
            ):
                eligible = True
            ranked.append(
                dict(
                    id=identifier,
                    label=entity["label"],
                    country=entity["country"],
                    region=entity["region"],
                    score=rank_score,
                    semantic_margin=semantic,
                    post_support=support,
                    title_match=identifier in title_ids,
                    description_match=identifier in desc_ids,
                    eligible=eligible,
                )
            )
        ranked.sort(key=lambda x: (x["eligible"], x["score"]), reverse=True)
        accepted = [c for c in ranked if c["eligible"]]
        if not accepted:
            return dict(
                empty_geography("mentions_do_not_establish_coverage"),
                geography_candidates=ranked[:5],
            )
        # Prefer a supported region over its containing country, never over a
        # different region/country merely because the labels are similar.
        regional = [c for c in accepted if c["region"]]
        selected = regional if regional else accepted
        countries = {c["country"] for c in accepted}
        if len(countries) > 1:
            return dict(
                empty_geography("conflicting_countries"),
                geography_candidates=ranked[:5],
            )
        regions = sorted({c["region"] for c in regional})
        normalized_profile = normalize_text(title + " " + desc)
        explicit_regions = all(
            normalize_text(c["region"]) in normalized_profile for c in regional
        )
        if len(regions) > 1 and not explicit_regions:
            return dict(
                empty_geography("conflicting_regions"), geography_candidates=ranked[:5]
            )
        scope = (
            "multi_region" if len(regions) > 1 else "region" if regions else "country"
        )
        evidence = [dict(source="profile", text=title + ". " + desc)]
        evidence += [
            dict(source="post", message_id=p.get("message_id"), text=p["text"][:1000])
            for p, found in zip(posts, post_ids)
            if any(c["id"] in found for c in selected)
        ][:5]
        return dict(
            geography_scope=scope,
            geography_country=next(iter(countries)),
            geography_regions=regions,
            geography_source="semantic-v11-proposal",
            geography_relevance="territorial",
            geography_candidates=ranked[:5],
            geography_review_required=True,
            geography_evidence=evidence,
            geography_explanation="Предложение по контексту профиля и повторяющимся местам в постах; не география подписчиков и не подтверждённая оценка.",
            geography_review_reasons=["geography_not_independently_validated"],
            geography_places=[],
        )


def empty_geography(reason):
    return dict(
        geography_scope="unknown",
        geography_country="",
        geography_regions=[],
        geography_source="semantic-v11-proposal",
        geography_relevance="undetermined",
        geography_candidates=[],
        geography_review_required=True,
        geography_evidence=[],
        geography_places=[],
        geography_review_reasons=[reason],
        geography_explanation="Недостаточно свидетельств территориальной направленности; упоминания мест не приравниваются к охвату.",
    )


def manual_geography(value):
    scope = value["scope"]
    country = value.get("country", "")
    regions = value.get("regions", [])
    countries = {e["country"] for e in reference()[0] if e["kind"] == "country"}
    if (
        scope
        not in ("region", "multi_region", "country", "global", "nonlocal", "unknown")
        or country
        and country not in countries
        or any(r not in REGION_ALIASES for r in regions)
        or regions
        and country != "RU"
        or scope in ("region", "multi_region", "country")
        and not country
        or scope == "region"
        and len(regions) != 1
        or scope == "multi_region"
        and len(regions) < 2
        or scope not in ("region", "multi_region")
        and regions
    ):
        raise ValueError("Некорректный ручной географический ответ V11")
    return dict(
        geography_scope=scope,
        geography_country=country,
        geography_regions=regions,
        geography_source="manual",
        geography_relevance="nonterritorial" if scope == "nonlocal" else "territorial",
        geography_candidates=[],
        geography_review_required=False,
        geography_evidence=[],
        geography_places=[],
        geography_review_reasons=[],
        geography_explanation="Подтверждено владельцем; имеет приоритет над моделью.",
    )
