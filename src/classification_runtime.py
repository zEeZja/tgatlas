"""Лёгкий контракт основной V11: экспорт/API никогда не импортируют ML."""

from datetime import datetime, timezone
from functools import lru_cache
import hashlib
import json
from pathlib import Path
import re

from .classification_text import informative_description
from .file_io import atomic_write_text
from .topic_keywords import TOPICS as LEGACY_TOPICS, GROUPS as LEGACY_GROUPS

ROOT = Path(__file__).resolve().parent.parent
POLICY_PATH = ROOT / "config/classification_v11.json"
PALETTE = [
    "#8aafa3",
    "#df9cba",
    "#b2b77c",
    "#d2b17d",
    "#ae91d4",
    "#d79d78",
    "#79aeca",
    "#b5a58a",
    "#8dbab3",
    "#bc9da9",
    "#98b974",
    "#b399c8",
    "#d5a5be",
    "#c9ab91",
    "#9fb27e",
    "#c29eb5",
    "#80b5ba",
    "#96a9c8",
]


def fingerprint(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False).encode()
    ).hexdigest()


@lru_cache(maxsize=1)
def policy():
    return json.loads(POLICY_PATH.read_text(encoding="utf-8"))


@lru_cache(maxsize=1)
def taxonomy():
    source = json.loads((ROOT / policy()["taxonomy"]).read_text(encoding="utf-8"))
    groups, topics, migration = [], [], {}
    for i, group in enumerate(source["groups"]):
        gid = "v11_" + group["id"]
        groups.append(
            dict(
                id=gid,
                label=group["label"],
                color=PALETTE[i % len(PALETTE)],
                section="v11",
            )
        )
        for topic in group["topics"]:
            tid = "v11_" + topic["id"]
            topics.append(
                dict(
                    topic,
                    id=tid,
                    group=gid,
                    section="v11",
                    keywords=[],
                    color=LEGACY_TOPICS["emergencies"]["color"]
                    if topic["id"] == "incidents"
                    else PALETTE[i % len(PALETTE)],
                )
            )
            for old in topic["legacy_hints"]:
                migration.setdefault(old, []).append(tid)
    # Broad old labels must not silently become narrower confirmed answers.
    for old in (
        "business",
        "general",
        "culture",
        "government",
        "design_fashion",
        "other",
        "tech",
    ):
        migration.pop(old, None)
    migration = {old: ids[0] for old, ids in migration.items() if len(ids) == 1}
    return dict(
        version=11,
        source_version=source["version"],
        fingerprint=fingerprint(source),
        groups=groups,
        topics=topics,
        legacy_migration=migration,
        broad_migration={
            "culture": "v11_culture",
            "tech": "v11_technology",
            "general": "v11_news_society",
        },
    )


def profile(row):
    return dict(
        name=row.get("name") or "",
        description=informative_description(row.get("description")),
    )


def manual_overrides():
    data = json.loads(
        (ROOT / "config/classification_v11_overrides.json").read_text(encoding="utf-8")
    )
    if data.get("format") != "atlas-manual-classification-v11":
        raise ValueError("Некорректные ручные ответы V11")
    return data["channels"]


def validate_results(data):
    tx = taxonomy()
    if (
        data.get("format") != "atlas-primary-results-v11"
        or data.get("taxonomy_sha256") != tx["fingerprint"]
    ):
        raise ValueError("Кеш V11 не соответствует действующей тематической сетке")
    topics = {t["id"]: t for t in tx["topics"]}
    from .region_aliases import REGION_ALIASES

    country_codes = {
        c["country"]
        for c in json.loads(
            (ROOT / "config/countries_v11.json").read_text(encoding="utf-8")
        )["countries"]
    }
    for username, row in data["channels"].items():
        if (
            not isinstance(username, str)
            or not re.fullmatch(r"[a-z][a-z0-9_]{0,31}", username)
            or row.get("classification_engine") != "semantic-v11"
            or row.get("subcategory") not in topics
            or row.get("topic_group") != topics[row["subcategory"]]["group"]
            or not isinstance(row.get("topic_review_required"), bool)
            or not isinstance(row.get("classification_profile"), dict)
        ):
            raise ValueError("Повреждён результат V11: " + username)
        scope = row.get("geography_scope")
        country = row.get("geography_country", "")
        regions = row.get("geography_regions", [])
        if (
            scope
            not in (
                "region",
                "multi_region",
                "country",
                "global",
                "nonlocal",
                "unknown",
            )
            or country
            and country not in country_codes
            or not isinstance(regions, list)
            or any(r not in REGION_ALIASES for r in regions)
            or regions
            and country != "RU"
            or scope == "region"
            and len(regions) != 1
            or scope == "multi_region"
            and len(regions) < 2
            or scope == "country"
            and not country
        ):
            raise ValueError("Некорректная география V11: " + username)
    fingerprint(data)
    return data["channels"]


def load_results(path=None):
    if path is None:
        # The new cache is independent: never overwrite the historical V11.
        primary = load_results(ROOT / "reports/classification-v11/catalog.json")
        if policy()["enabled"]:
            from .classification_v11v2_runtime import load_results as load_v11v2

            primary.update(load_v11v2())
        return primary
    path = Path(path or ROOT / "reports/classification-v11/catalog.json")
    if not path.exists():
        return {}
    if not policy()["enabled"]:
        return {}
    return validate_results(json.loads(path.read_text(encoding="utf-8")))


def publish_results(data, path=None):
    validate_results(data)
    path = Path(path or ROOT / policy()["results"])
    # Preserve every previous publication as a separately named archive.
    if path.exists():
        backup = path.with_name(
            "catalog-previous-"
            + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%f")
            + ".json"
        )
        atomic_write_text(backup, path.read_text(encoding="utf-8"))
    atomic_write_text(
        path, json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
    )


def merge_result(row, results):
    result = results.get(str(row.get("username") or "").lower().lstrip("@"))
    if not result or result["classification_profile"] != profile(row):
        return row
    if result.get("classification_engine") == "semantic-v11v2":
        try:
            age = (
                datetime.now(timezone.utc)
                - datetime.fromisoformat(result["classification_created_at"])
            ).total_seconds()
            if not 0 <= age <= policy()["evidence_ttl_hours"] * 3600:
                return {**row, "semantic_result_stale": True}
        except (ValueError, TypeError, KeyError):
            return row
    evidence_at = result.get("post_routing", {}).get("fetched_at")
    if evidence_at:
        try:
            age = (
                datetime.now(timezone.utc) - datetime.fromisoformat(evidence_at)
            ).total_seconds()
            if not 0 <= age <= policy()["evidence_ttl_hours"] * 3600:
                return {**row, "semantic_result_stale": True}
        except (ValueError, TypeError):
            return row
    merged = {
        **row,
        **result,
        "legacy_classification": row.get("legacy_classification")
        or {
            k: row.get(k)
            for k in (
                "subcategory",
                "topic_group",
                "topic_source",
                "geography_scope",
                "geography_regions",
                "geography_country",
                "geography_source",
            )
        },
    }
    tx = taxonomy()
    merged.pop("semantic_proposal", None)
    if row.get("topic_source") in ("manual", "local") and "topic" not in result.get(
        "classification_manual_dimensions", []
    ):
        old = row["subcategory"]
        target = tx["legacy_migration"].get(old)
        if target:
            topic = next(t for t in tx["topics"] if t["id"] == target)
            merged.update(
                subcategory=target,
                primary_category=target,
                topic_group=topic["group"],
                topic_group_label=next(
                    g["label"] for g in tx["groups"] if g["id"] == topic["group"]
                ),
                topic_source="manual",
                topic_review_required=False,
                topic_path=[topic["group"], target],
                topic_tags=[target],
                secondary_topics=[],
                topic_granularity="topic",
                topic_score=None,
            )
            merged["topic_assignment_status"] = "manual_migrated"
        else:
            for key, value in row.items():
                if (
                    key.startswith(("topic_", "primary_", "secondary_", "broader_"))
                    or key == "subcategory"
                ):
                    merged[key] = value
            group = (
                row.get("topic_group")
                or next((t["group"] for t in tx["topics"] if t["id"] == old), None)
                or LEGACY_TOPICS.get(old, {}).get("group", "other_group")
            )
            granularity = row.get("topic_granularity") or "topic"
            merged.update(
                primary_category=row.get("primary_category", old),
                topic_group=group,
                topic_group_label=row.get("topic_group_label")
                or next((g["label"] for g in tx["groups"] if g["id"] == group), None)
                or LEGACY_GROUPS.get(group, {}).get("label", group),
                topic_granularity=granularity,
                topic_score=row.get("topic_score"),
                topic_tags=row.get(
                    "topic_tags",
                    [old] if old != "other" and granularity == "topic" else [],
                ),
                topic_path=row.get(
                    "topic_path", [group, old] if granularity == "topic" else [group]
                ),
                secondary_topics=row.get("secondary_topics", []),
                broader_topics=row.get("broader_topics", []),
            )
            merged["semantic_proposal"] = {
                k: result.get(k) for k in ("subcategory", "topic_group", "topic_score")
            }
    if row.get("geography_source") == "manual" and "geography" not in result.get(
        "classification_manual_dimensions", []
    ):
        merged.update({k: v for k, v in row.items() if k.startswith("geography_")})
        for key, default in (
            ("geography_candidates", []),
            ("geography_evidence", []),
            ("geography_review_reasons", []),
            ("geography_review_required", False),
            (
                "geography_explanation",
                "Подтверждено владельцем; имеет приоритет над моделью.",
            ),
        ):
            merged[key] = row.get(key, default)
    if merged.get("geography_country") not in (None, "", "RU") and merged.get(
        "geography_scope"
    ) in ("region", "multi_region"):
        merged.update(geography_scope="country", geography_regions=[])
    # The public map carries examples, never the entire local text corpus.
    evidence = merged.get("topic_evidence", [])
    compact, post_count = [], 0
    for item in evidence:
        if isinstance(item, dict) and item.get("source") == "post":
            if post_count >= 2:
                continue
            post_count += 1
        if len(compact) >= 4:
            break
        compact.append(
            {**item, "text": str(item.get("text", ""))[:350]}
            if isinstance(item, dict)
            else str(item)[:350]
        )
    if evidence:
        merged["topic_evidence"] = compact
    if merged.get("geography_evidence"):
        merged["geography_evidence"] = [
            {**item, "text": str(item.get("text", ""))[:350]}
            if isinstance(item, dict)
            else str(item)[:350]
            for item in merged["geography_evidence"][:3]
        ]
    if merged.get("post_routing"):
        merged["post_routing"] = {
            **merged["post_routing"],
            "displayed_posts": post_count,
        }
    return merged
