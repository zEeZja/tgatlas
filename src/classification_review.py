"""Owner-reviewed labels, deterministic sampling and honest offline quality metrics."""

from collections import Counter, defaultdict
import csv
from datetime import datetime, timezone
import hashlib
import io
import json
from pathlib import Path
import re

from .classification_text import informative_description
from .file_io import atomic_write_text
from .topic_keywords import (
    TOPICS,
    GROUPS,
    TAXONOMY,
    load_classification_rules,
    resolve_topic,
)

ROOT = Path(__file__).resolve().parent.parent
SCOPES = {"region", "multi_region", "country", "global", "unknown", "nonlocal"}
FIELDS = [
    "username",
    "name",
    "description",
    "input_country",
    "input_region_hint",
    "group_id",
    "split",
    "suggested_topic",
    "suggested_secondary_topics",
    "suggested_scope",
    "suggested_regions",
    "suggested_country",
    "expected_topic",
    "expected_secondary_topics",
    "expected_scope",
    "expected_regions",
    "expected_country",
    "reviewed",
    "note",
]

LEGACY_FIELDS = list(FIELDS)
FIELDS += [
    "review_schema_version",
    "taxonomy_version",
    "expected_group",
    "expected_formats",
    "review_reason",
    "proposed_topic",
]


def upgrade_record(row):
    row = dict(row)
    row["review_schema_version"] = row.get("review_schema_version") or "1"
    row["taxonomy_version"] = row.get("taxonomy_version") or "5"
    row["expected_group"] = (
        row.get("expected_group")
        or TOPICS.get(row.get("expected_topic"), TOPICS["other"])["group"]
    )
    for key in ("expected_formats", "review_reason", "proposed_topic"):
        row[key] = row.get(key) or ""
    return row


def feature_text(row):
    # No username, existing topic, region label, audience or ad handles as features.
    return (
        re.sub(
            r"\s+",
            " ",
            str(row.get("name") or "")
            + " "
            + informative_description(row.get("description")),
        )
        .strip()
        .casefold()
    )


def group_id(row):
    return hashlib.sha256(feature_text(row).encode("utf-8")).hexdigest()[:20]


def load_channels(path):
    data = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    rows = data.get("channels") if isinstance(data, dict) else data
    if not isinstance(rows, list) or any(
        not isinstance(r, dict)
        or not isinstance(r.get("username"), str)
        or not r["username"].strip()
        for r in rows
    ):
        raise ValueError("Ожидается каталог с непустыми username")
    if any(
        any(
            row.get(key) is not None and not isinstance(row.get(key), str)
            for key in ("name", "description")
        )
        for row in rows
    ):
        raise ValueError("Название и описание должны быть текстом")
    unique = {}
    for row in rows:
        key = row["username"].lstrip("@").strip().lower()
        if not re.fullmatch(r"[a-z][a-z0-9_]{0,31}", key):
            raise ValueError("Некорректный username в каталоге")
        # Prefer the most informative alias snapshot; never edit the input export.
        if key not in unique or len(feature_text(row)) > len(feature_text(unique[key])):
            unique[key] = {**row, "username": key}
    return list(unique.values()), len(rows) - len(unique)


def _cell(value):
    text = str(value or "")
    return "'" + text if text.lstrip().startswith(("=", "+", "-", "@")) else text


def write_review(path, records):
    stream = io.StringIO(newline="")
    writer = csv.DictWriter(
        stream, fieldnames=FIELDS, delimiter=";", lineterminator="\n"
    )
    writer.writeheader()
    for record in records:
        writer.writerow({key: _cell(record.get(key, "")) for key in FIELDS})
    atomic_write_text(path, "\ufeff" + stream.getvalue())


def prepare(input_path, output_path, size=200, seed=42):
    if size < 1:
        raise ValueError("Размер выборки должен быть больше нуля")
    if Path(input_path).resolve() == Path(output_path).resolve():
        raise ValueError("Выборка не должна заменять каталог")
    channels, duplicates = load_channels(input_path)
    rules = load_classification_rules()
    predicted = {row["username"]: resolve_topic(row, **rules) for row in channels}

    def order(row):
        return hashlib.sha256(f"{seed}:{row['username']}".encode()).hexdigest()

    ordered = sorted(channels, key=order)
    chosen = {}
    difficult = [
        row
        for row in ordered
        if predicted[row["username"]]["subcategory"] == "other"
        or predicted[row["username"]]["geography_scope"] == "unknown"
    ]
    for row in difficult[: min(size // 4, len(difficult))]:
        chosen[row["username"]] = row
    buckets = defaultdict(list)
    for row in ordered:
        if row["username"] not in chosen:
            buckets[predicted[row["username"]]["subcategory"]].append(row)
    while len(chosen) < min(len(channels), size * 4 // 5) and any(buckets.values()):
        for key in sorted(buckets):
            if buckets[key] and len(chosen) < min(len(channels), size * 4 // 5):
                row = buckets[key].pop(0)
                chosen[row["username"]] = row
    for row in ordered:
        if len(chosen) >= size:
            break
        chosen.setdefault(row["username"], row)
    records = []
    for row in chosen.values():
        result = predicted[row["username"]]
        gid = group_id(row)
        record = dict(
            input_country=row.get("country") or "",
            input_region_hint=row.get("region_hint") or "",
            username=row["username"],
            name=row.get("name") or row["username"],
            description=informative_description(row.get("description")),
            group_id=gid,
            split="test" if int(gid[:8], 16) % 4 == 0 else "train",
            suggested_topic=result["subcategory"],
            suggested_secondary_topics=",".join(result["secondary_topics"]),
            suggested_scope=result["geography_scope"],
            suggested_regions="|".join(result["geography_regions"]),
            suggested_country=result["geography_country"],
            reviewed="no",
            note="",
        )
        for key in ("topic", "secondary_topics", "scope", "regions", "country"):
            record["expected_" + key] = record["suggested_" + key]
        record.update(
            review_schema_version="2",
            taxonomy_version=str(TAXONOMY["version"]),
            expected_group=result["topic_group"],
            expected_formats=",".join(result["publication_formats"]),
            review_reason="",
            proposed_topic="",
        )
        records.append(record)
    records.sort(key=lambda r: r["username"])
    write_review(output_path, records)
    from .review_page import render_review

    atomic_write_text(Path(output_path).with_suffix(".html"), render_review(records))
    return dict(
        total_unique=len(channels),
        case_alias_duplicates=duplicates,
        sample=len(records),
        reviewed=0,
        splits=dict(Counter(r["split"] for r in records)),
    )


def _list(value, delimiter=","):
    return sorted(
        set(x.strip() for x in str(value or "").split(delimiter) if x.strip())
    )


def read_review(path, *, confirmed_only=True):
    with Path(path).open(encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream, delimiter=";")
        if not set(LEGACY_FIELDS).issubset(reader.fieldnames or []):
            raise ValueError("Файл разметки не соответствует шаблону prepare")
        rows = list(reader)
    if any(
        None in row or any(row.get(key) is None for key in LEGACY_FIELDS)
        for row in rows
    ):
        raise ValueError("Повреждена строка CSV; используйте экспорт из формы проверки")
    confirmed = []
    seen = set()
    groups = {}
    for raw_row in rows:
        row = upgrade_record(raw_row)
        if row.get("reviewed", "").strip().lower() not in ("yes", "да", "true", "1"):
            if confirmed_only:
                continue
            confirmed.append(row)
            continue
        username = row["username"].strip().lstrip("@").lower()
        if not re.fullmatch(r"[a-z][a-z0-9_]{0,31}", username) or username in seen:
            raise ValueError(
                "Некорректный или повторный username в подтверждённой разметке"
            )
        seen.add(username)
        row["username"] = username
        for key in ("name", "description", "note", "input_region_hint"):
            # Undo the spreadsheet display guard, not arbitrary apostrophes.
            value = row[key]
            if value.startswith("'") and value[1:].lstrip().startswith(
                ("=", "+", "-", "@")
            ):
                row[key] = value[1:]
        if (
            row["expected_topic"] not in TOPICS
            or row["expected_scope"] not in SCOPES
            or row["split"] not in ("train", "test")
        ):
            raise ValueError(f"Недопустимая метка или split: {username}")
        if row["expected_group"] not in GROUPS:
            raise ValueError(f"Неизвестная крупная группа: {username}")
        if (
            row["expected_topic"] != "other"
            and row["expected_group"] != TOPICS[row["expected_topic"]]["group"]
        ):
            raise ValueError(f"Группа не соответствует подтеме: {username}")
        if any(
            f not in {x["id"] for x in TAXONOMY["formats"]}
            for f in _list(row["expected_formats"])
        ):
            raise ValueError(f"Неизвестный формат: {username}")
        if (
            int(row["review_schema_version"]) not in (1, 2)
            or not 1 <= int(row["taxonomy_version"]) <= TAXONOMY["version"]
        ):
            raise ValueError("Не поддерживается версия формы или таксономии")
        tags = _list(row["expected_secondary_topics"])
        regions = _list(row["expected_regions"], "|")
        if any(
            tag not in TOPICS or tag in ("other", row["expected_topic"]) for tag in tags
        ):
            raise ValueError(f"Недопустимые дополнительные темы: {username}")
        scope = row["expected_scope"]
        country = row["expected_country"].strip().upper()
        if country and not re.fullmatch("[A-Z]{2}", country):
            raise ValueError(f"Нужен двухбуквенный код страны: {username}")
        if (
            (scope == "region" and len(regions) != 1)
            or (scope == "multi_region" and len(regions) < 2)
            or (scope in ("country", "global", "unknown", "nonlocal") and regions)
            or (scope == "country" and not country)
        ):
            raise ValueError(f"Охват не соответствует регионам/стране: {username}")
        gid = group_id(row)
        if gid in groups and groups[gid] != row["split"]:
            raise ValueError("Одинаковые тексты попали в train и test; исправьте split")
        groups[gid] = row["split"]
        row["group_id"] = gid
        row["expected_country"] = "" if scope == "nonlocal" else country
        row["expected_regions"] = "|".join(regions)
        row["expected_secondary_topics"] = ",".join(tags)
        confirmed.append(row)
    return confirmed


def metrics(rows, predictions):
    if not rows:
        raise ValueError("Нет подтверждённых примеров для оценки")
    if len(rows) != len(predictions):
        raise ValueError("Число прогнозов не совпадает с числом примеров")
    group_correct = 0
    topic_correct = topic_accepted = topic_accepted_correct = geo_correct = (
        geo_accepted
    ) = geo_accepted_correct = 0
    confusions = Counter()
    counts = Counter()
    tag_tp = tag_fp = tag_fn = 0
    for row, pred in zip(rows, predictions):
        expected_group = (
            row.get("expected_group") or TOPICS[row["expected_topic"]]["group"]
        )
        actual_group = pred.get("topic_group") or TOPICS[pred["subcategory"]]["group"]
        group_correct += expected_group == actual_group
        truth = row["expected_topic"]
        guess = pred["subcategory"]
        counts[truth] += 1
        confusions[truth, guess] += 1
        correct = truth == guess
        topic_correct += correct
        if guess != "other":
            topic_accepted += 1
            topic_accepted_correct += correct
        expected = (
            row["expected_scope"],
            _list(row["expected_regions"], "|"),
            row["expected_country"],
        )
        actual = (
            pred["geography_scope"],
            sorted(pred["geography_regions"]),
            pred["geography_country"],
        )
        correct_geo = expected == actual
        geo_correct += correct_geo
        if pred["geography_scope"] != "unknown":
            geo_accepted += 1
            geo_accepted_correct += correct_geo
        true_tags = set(_list(row["expected_secondary_topics"]))
        guessed_tags = set(pred.get("secondary_topics", []))
        tag_tp += len(true_tags & guessed_tags)
        tag_fp += len(guessed_tags - true_tags)
        tag_fn += len(true_tags - guessed_tags)
    per_class = {}
    for topic in sorted(set(counts) | {p["subcategory"] for p in predictions}):
        tp = confusions[topic, topic]
        fp = sum(n for (a, b), n in confusions.items() if b == topic and a != topic)
        fn = sum(n for (a, b), n in confusions.items() if a == topic and b != topic)
        per_class[topic] = dict(
            support=counts[topic],
            precision=tp / (tp + fp) if tp + fp else None,
            recall=tp / (tp + fn) if tp + fn else None,
            f1=2 * tp / (2 * tp + fp + fn) if 2 * tp + fp + fn else 0,
        )
    n = len(rows)
    return dict(
        examples=n,
        topic_group_accuracy=group_correct / n,
        topic_accuracy=topic_correct / n,
        topic_coverage=topic_accepted / n,
        topic_selective_precision=topic_accepted_correct / topic_accepted
        if topic_accepted
        else None,
        topic_macro_f1=sum(v["f1"] for v in per_class.values()) / len(per_class),
        geography_exact_accuracy=geo_correct / n,
        geography_coverage=geo_accepted / n,
        geography_selective_precision=geo_accepted_correct / geo_accepted
        if geo_accepted
        else None,
        secondary_tags_precision=tag_tp / (tag_tp + tag_fp)
        if tag_tp + tag_fp
        else None,
        secondary_tags_recall=tag_tp / (tag_tp + tag_fn) if tag_tp + tag_fn else None,
        per_class=per_class,
        confusions=[
            dict(expected=a, predicted=b, count=n)
            for (a, b), n in sorted(confusions.items())
            if a != b
        ],
    )


def automatic_prediction(row):
    channel = {key: row.get(key, "") for key in ("username", "name", "description")}
    channel.update(
        country=row.get("input_country", ""),
        region_hint=row.get("input_region_hint", ""),
    )
    return resolve_topic(channel, {}, geography_overrides={}, tag_overrides={})


def evaluate(review_path, split="test"):
    rows = [
        r for r in read_review(review_path) if split == "all" or r["split"] == split
    ]
    # Evaluate automatic evidence, not owner overrides memorizing the same labels.
    predictions = [automatic_prediction(r) for r in rows]
    return dict(split=split, overrides_used=False, **metrics(rows, predictions))


def apply_review(review_path, apply=False, labels_path=None):
    rows = read_review(review_path)
    if not rows:
        raise ValueError(
            "Нет строк reviewed=yes; неподтверждённые предложения не применяются"
        )
    path = (
        Path(labels_path) if labels_path else ROOT / "config/classification_labels.json"
    )
    if path.resolve() == Path(review_path).resolve():
        raise ValueError("Разметка и конфигурация должны быть разными файлами")
    labels = (
        json.loads(path.read_text(encoding="utf-8"))
        if path.exists()
        else {"version": 1, "channels": {}}
    )
    entries = dict(labels["channels"])
    for row in rows:
        entries[row["username"]] = dict(
            topic=row["expected_topic"],
            group=row["expected_group"],
            review_schema_version=int(row["review_schema_version"]),
            taxonomy_version=int(row["taxonomy_version"]),
            secondary_topics=_list(row["expected_secondary_topics"]),
            scope=row["expected_scope"],
            regions=_list(row["expected_regions"], "|"),
            country=row["expected_country"],
            reviewed=True,
            review_reason=row["review_reason"],
            proposed_topic=row["proposed_topic"],
            note=row["note"],
            reviewed_profile=dict(
                name=row["name"],
                description=informative_description(row["description"]),
            ),
            reviewed_at=datetime.now(timezone.utc).isoformat(),
        )
        if int(row["review_schema_version"]) >= 2:
            entries[row["username"]]["formats"] = _list(row["expected_formats"])
    if apply:
        atomic_write_text(
            path,
            json.dumps(
                dict(
                    version=2,
                    taxonomy_version=TAXONOMY["version"],
                    channels=entries,
                    updated_at=datetime.now(timezone.utc).isoformat(),
                ),
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
        )
    return dict(confirmed=len(rows), applied=apply, path=str(path))
