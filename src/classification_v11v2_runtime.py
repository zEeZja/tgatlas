"""Лёгкий контракт публикации 11v2: без numpy, torch и запуска моделей."""

from datetime import datetime, timezone
import json
import math
from pathlib import Path
import re

from .classification_runtime import ROOT, fingerprint, profile, taxonomy
from .file_io import atomic_write_text
from .region_aliases import REGION_ALIASES
from .topic_keywords import GROUPS, TOPICS

CACHE = "reports/classification-v11v2/catalog.json"


def timestamp(value):
    if not isinstance(value, str):
        raise ValueError("Нет времени классификации 11v2")
    result = datetime.fromisoformat(value)
    if result.tzinfo is None:
        raise ValueError("Время классификации должно содержать часовой пояс")
    return result


def validate_results(data):
    tx = taxonomy()
    if (
        not isinstance(data, dict)
        or data.get("format") != "atlas-results-v11v2"
        or data.get("taxonomy_sha256") != tx["fingerprint"]
        or not isinstance(data.get("channels"), dict)
        or not data["channels"]
        or type(data.get("remaining")) is not int
        or data["remaining"] < 0
    ):
        raise ValueError("Несовместимый или неполный формат результата 11v2")
    timestamp(data.get("created_at"))
    topics = {t["id"]: t for t in tx["topics"]}
    groups = {g["id"]: g for g in tx["groups"]}
    countries = {
        c["country"]
        for c in json.loads(
            (ROOT / "config/countries_v11.json").read_text(encoding="utf-8")
        )["countries"]
    }
    for username, row in data["channels"].items():
        if (
            not isinstance(username, str)
            or not re.fullmatch(r"[a-z][a-z0-9_]{0,31}", username)
            or not isinstance(row, dict)
        ):
            raise ValueError("Повреждён username или результат 11v2")
        kind, topic, group = (
            row.get("topic_granularity"),
            row.get("subcategory"),
            row.get("topic_group"),
        )
        manual = row.get("topic_source") in ("manual", "local")
        if (
            row.get("classification_engine") != "semantic-v11v2"
            or row.get("classification_version") != "11v2"
        ):
            raise ValueError("Неизвестный движок 11v2: " + username)
        if kind == "topic":
            expected = topics.get(topic) or (TOPICS.get(topic) if manual else None)
            if not expected or group != expected["group"]:
                raise ValueError("Некорректная тема 11v2: " + username)
        elif kind == "group":
            if topic is not None or group not in groups:
                raise ValueError("Некорректный раздел 11v2: " + username)
        elif kind == "abstain":
            if topic is not None or group is not None:
                raise ValueError(
                    "Отказ не должен содержать назначенную тему: " + username
                )
        else:
            raise ValueError("Неизвестная детализация 11v2: " + username)
        snapshot = row.get("classification_profile")
        if not isinstance(snapshot, dict) or any(
            not isinstance(snapshot.get(k), str) for k in ("name", "description")
        ):
            raise ValueError("Нет профиля классификации: " + username)
        timestamp(row.get("classification_created_at"))
        if not isinstance(row.get("topic_review_required"), bool) or not isinstance(
            row.get("topic_candidates"), list
        ):
            raise ValueError("Нет статуса проверки 11v2: " + username)
        for candidate in row["topic_candidates"]:
            if (
                not isinstance(candidate, dict)
                or candidate.get("id") not in topics
                or type(candidate.get("score")) not in (int, float)
                or not math.isfinite(candidate["score"])
            ):
                raise ValueError("Некорректный кандидат темы: " + username)
        routing = row.get("post_routing")
        if (
            not isinstance(routing, dict)
            or type(routing.get("usable")) is not int
            or not 0 <= routing["usable"] <= 8
        ):
            raise ValueError("Некорректный счётчик постов: " + username)
        if routing.get("fetched_at") is not None:
            timestamp(routing["fetched_at"])
        if row.get("suggested_topic") not in topics and not (
            manual and row.get("suggested_topic") in TOPICS
        ):
            raise ValueError("Неизвестный кандидат 11v2: " + username)
        scope, country, regions = (
            row.get("geography_scope"),
            row.get("geography_country"),
            row.get("geography_regions"),
        )
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
            or country not in ("", *countries)
            or not isinstance(regions, list)
            or any(not isinstance(r, str) or r not in REGION_ALIASES for r in regions)
            or len(regions) != len(set(regions))
            or scope == "region"
            and (country != "RU" or len(regions) != 1)
            or scope == "multi_region"
            and (country != "RU" or len(regions) < 2)
            or scope == "country"
            and (not country or regions)
            or scope in ("global", "nonlocal", "unknown")
            and (regions or country and row.get("geography_source") != "manual")
        ):
            raise ValueError("Некорректная география 11v2: " + username)
    fingerprint(data)  # Reject NaN/Infinity anywhere before writing.
    return data["channels"]


def public_result(row, data):
    """Keep the public legacy schema without fabricating a narrow topic."""
    result = dict(row)
    tx = taxonomy()
    groups = {**GROUPS, **{g["id"]: g for g in tx["groups"]}}
    kind = row["topic_granularity"]
    topic = row["subcategory"] if kind == "topic" else "other"
    group = row["topic_group"] if kind != "abstain" else "other_group"
    model = data.get("model", {})
    encoder = model.get("encoder", {})
    routing = dict(row.get("post_routing") or {})
    routing.update(
        status="usable" if routing.get("usable", 0) >= 3 else "insufficient_posts",
        requested=bool(routing.get("fetched_at")),
    )
    if not routing.get("usable", 0):
        routing["sample_fetched_at"] = routing.get("fetched_at")
        routing["fetched_at"] = None
    result.update(
        classification_policy_sha256=data.get("policy_sha256"),
        classification_implementation_sha256=data.get("implementation_sha256"),
        classification_geography_policy_sha256=data.get("geography_policy_sha256"),
        classification_taxonomy_sha256=data["taxonomy_sha256"],
        classification_model=dict(
            encoder=encoder.get("name"),
            revision=encoder.get("revision"),
            precision=encoder.get("precision"),
            scoring=model.get("scoring"),
            calibrated=False,
        ),
        subcategory=topic,
        primary_category=topic if kind == "topic" else None,
        topic_group=group,
        topic_group_label=groups[group]["label"],
        topic_category="v11",
        topic_path=[group, topic]
        if kind == "topic"
        else [group]
        if kind == "group"
        else [],
        topic_tags=[topic] if kind == "topic" else [],
        secondary_topics=[],
        broader_topics=[],
        topic_suggested=row.get("suggested_topic"),
        topic_group_candidates=(row.get("topic_diagnostics") or {}).get(
            "group_candidates", []
        ),
        post_routing=routing,
    )
    return result


def load_results(path=None):
    path = Path(path or ROOT / CACHE)
    if not path.exists():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    rows = validate_results(data)
    return {username: public_result(row, data) for username, row in rows.items()}


def publish_run(directory, *, apply=False, allow_partial=False, current_path=None):
    """Validate full input/snapshot coverage before touching any public cache."""
    directory = Path(directory)
    data = json.loads((directory / "results.json").read_text(encoding="utf-8"))
    predictions = validate_results(data)
    snapshot = json.loads((directory / "snapshot.json").read_text(encoding="utf-8"))
    if not isinstance(snapshot, list) or fingerprint(snapshot) != data.get(
        "input_sha256"
    ):
        raise ValueError("Результат 11v2 получен по другому снимку")
    source = {r["username"]: r for r in snapshot}
    if len(source) != len(snapshot) or not set(predictions) <= source.keys():
        raise ValueError("Повторные или посторонние каналы в снимке 11v2")
    if data["remaining"] != len(source) - len(predictions):
        raise ValueError("Неверный счётчик оставшихся каналов 11v2")
    policy = json.loads(
        (ROOT / "config/classification_v11v2.json").read_text(encoding="utf-8")
    )
    geo_policy = json.loads(
        (ROOT / "config/geography_v11v2.json").read_text(encoding="utf-8")
    )
    if data.get("policy_sha256") != fingerprint(policy) or data.get(
        "geography_policy_sha256"
    ) != fingerprint(geo_policy):
        raise ValueError("Настройки 11v2 изменились: пересчитайте классификацию")
    age = (datetime.now(timezone.utc) - timestamp(data["created_at"])).total_seconds()
    if not 0 <= age <= 168 * 3600:
        raise ValueError("Результат 11v2 устарел")
    current = json.loads(
        Path(current_path or ROOT / "web/data/channels.json").read_text(
            encoding="utf-8"
        )
    )
    rows = current["channels"] if isinstance(current, dict) else current
    catalog = {r["username"]: r for r in rows}
    if len(catalog) != len(rows):
        raise ValueError("Повторные каналы текущего каталога")
    if not allow_partial and (data["remaining"] or set(predictions) != set(catalog)):
        raise ValueError(
            "Для карты нужен полный каталог; пилот/частичный результат не публикуется без --allow-partial"
        )
    for username, result in predictions.items():
        row_age = (
            datetime.now(timezone.utc) - timestamp(result["classification_created_at"])
        ).total_seconds()
        if not 0 <= row_age <= 168 * 3600:
            raise ValueError("Предложение 11v2 устарело: " + username)
        if (
            result["classification_profile"] != profile(source[username])
            or username in catalog
            and result["classification_profile"] != profile(catalog[username])
        ):
            raise ValueError("Профиль изменился после классификации: " + username)
    text = json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
    if apply:
        target = ROOT / CACHE
        if target.exists():
            stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%f")
            atomic_write_text(
                target.with_name("catalog-previous-" + stamp + ".json"),
                target.read_text(encoding="utf-8"),
            )
        atomic_write_text(target, text)
    return dict(
        channels=len(predictions),
        catalog_channels=len(catalog),
        applied=apply,
        partial=bool(data["remaining"] or set(predictions) != set(catalog)),
    )
