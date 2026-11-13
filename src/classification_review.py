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
    "topic_reviewed",
    "geography_reviewed",
    "formats_reviewed",
]


def _yes(value):
    return str(value or "").strip().lower() in ("yes", "да", "true", "1")


def confirmed_dimensions(row):
    legacy = _yes(row.get("reviewed")) and str(row.get("review_schema_version") or "1") != "3"
    return {
        name for name in ("topic", "geography")
        if _yes(row.get(name + "_reviewed"))
        or (not row.get(name + "_reviewed") and legacy)
    }


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
    dimensions = confirmed_dimensions(row)
    for name in ("topic", "geography"):
        row[name + "_reviewed"] = "yes" if name in dimensions else "no"
    row["formats_reviewed"] = row.get("formats_reviewed") or (
        "yes" if row["review_schema_version"] == "2" and "topic" in dimensions else "no"
    )
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


def prepare(input_path, output_path, size=200, seed=42, *, focus_groups=None, exclude_review=None):
    if size < 1:
        raise ValueError("Размер выборки должен быть больше нуля")
    if Path(input_path).resolve() == Path(output_path).resolve():
        raise ValueError("Выборка не должна заменять каталог")
    protected = {Path(input_path).resolve()}
    if exclude_review:
        protected.add(Path(exclude_review).resolve())
    if Path(output_path).suffix.lower() != ".csv" or any(
            p.resolve() in protected for p in (Path(output_path), Path(output_path).with_suffix(".html"))):
        raise ValueError("Нужен отдельный CSV: выборка и HTML не должны заменять исходные данные или разметку")
    channels, duplicates = load_channels(input_path)
    catalogue_total = len(channels)
    rules = load_classification_rules()
    predicted = {row["username"]: resolve_topic(row, **rules) for row in channels}
    focus_groups = set(focus_groups or [])
    if focus_groups - GROUPS.keys():
        raise ValueError("Неизвестная группа в целевой выборке")
    excluded = {r["username"] for r in read_review(exclude_review, dimension="topic")} if exclude_review else set()
    channels = [row for row in channels if row["username"] not in excluded
                and (not focus_groups or predicted[row["username"]]["topic_group"] in focus_groups)]
    if not channels:
        raise ValueError("Нет каналов для выбранных групп после исключения проверенных")

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
            review_schema_version="3",
            taxonomy_version=str(TAXONOMY["version"]),
            expected_group=result["topic_group"],
            expected_formats=",".join(result["publication_formats"]),
            review_reason="",
            proposed_topic="",
            topic_reviewed="no",
            geography_reviewed="no",
            formats_reviewed="no",
        )
        records.append(record)
    records.sort(key=lambda r: r["username"])
    write_review(output_path, records)
    from .review_page import render_review

    atomic_write_text(Path(output_path).with_suffix(".html"), render_review(records))
    return dict(
        total_unique=catalogue_total,
        eligible_channels=len(channels),
        case_alias_duplicates=duplicates,
        sample=len(records),
        reviewed=0,
        splits=dict(Counter(r["split"] for r in records)),
        focused_groups=sorted(focus_groups),
        excluded_reviewed=len(excluded),
    )


def _list(value, delimiter=","):
    return sorted(
        set(x.strip() for x in str(value or "").split(delimiter) if x.strip())
    )


def _review_records(path):
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
    return [upgrade_record(row) for row in rows]


def _canonical_review_regions(regions, country):
    from .places import PLACES
    from .region_aliases import REGION_ALIASES
    from .region_classifier import normalize_text
    from .title_region import FOREIGN

    result, countries = set(), set()
    for value in regions:
        key = normalize_text(value)
        candidates = {(region, "RU") for region, aliases in REGION_ALIASES.items()
                      if key in {normalize_text(region), *(normalize_text(a) for a in aliases)}}
        candidates |= {(p["region"], p["country"]) for p in PLACES
                       if key in {normalize_text(p["label"]), *(normalize_text(a) for a in p["aliases"])}}
        candidates |= {(p["label"], p["country"]) for p in FOREIGN if p["scope"] == "region"
                       and key in {normalize_text(p["label"]), *(normalize_text(a) for a in p["aliases"])}}
        if len(candidates) != 1:
            raise ValueError(f"Неизвестный или неоднозначный регион/город: {value}. Укажите канонический субъект.")
        region, resolved_country = next(iter(candidates))
        result.add(region)
        countries.add(resolved_country)
    if len(countries) > 1 or (country and countries and country not in countries):
        raise ValueError("Страна не соответствует указанным регионам")
    return sorted(result), country or next(iter(countries), "")


def _validate_record(row, dimensions):
    row = dict(row)
    username = row["username"].strip().lstrip("@").lower()
    if not re.fullmatch(r"[a-z][a-z0-9_]{0,31}", username):
        raise ValueError("Некорректный username в подтверждённой разметке")
    row["username"] = username
    for key in ("name", "description", "note", "input_region_hint"):
        # Undo the spreadsheet display guard, not arbitrary apostrophes.
        value = row[key]
        if value.startswith("'") and value[1:].lstrip().startswith(("=", "+", "-", "@")):
            row[key] = value[1:]
    if row["split"] not in ("train", "test"):
        raise ValueError(f"Недопустимый split: {username}")
    if (int(row["review_schema_version"]) not in (1, 2, 3)
            or not 1 <= int(row["taxonomy_version"]) <= TAXONOMY["version"]):
        raise ValueError("Не поддерживается версия формы или таксономии")
    if "topic" in dimensions:
        if (
            row["expected_topic"] not in TOPICS
        ):
            raise ValueError(f"Недопустимая метка или split: {username}")
        if row["expected_group"] not in GROUPS:
            raise ValueError(f"Неизвестная крупная группа: {username}")
        if (
            row["expected_topic"] != "other"
            and row["expected_group"] != TOPICS[row["expected_topic"]]["group"]
        ):
            raise ValueError(f"Группа не соответствует подтеме: {username}")
        if _yes(row.get("formats_reviewed")) and any(
            f not in {x["id"] for x in TAXONOMY["formats"]}
            for f in _list(row["expected_formats"])
        ):
            raise ValueError(f"Неизвестный формат: {username}")
        tags = _list(row["expected_secondary_topics"])
        if any(
            tag not in TOPICS or tag in ("other", row["expected_topic"]) for tag in tags
        ):
            raise ValueError(f"Недопустимые дополнительные темы: {username}")
        row["expected_secondary_topics"] = ",".join(tags)
    if "geography" in dimensions:
        regions = _list(row["expected_regions"], "|")
        scope = row["expected_scope"]
        if scope not in SCOPES:
            raise ValueError(f"Недопустимая география: {username}")
        country = row["expected_country"].strip().upper()
        if country and not re.fullmatch("[A-Z]{2}", country):
            raise ValueError(f"Нужен двухбуквенный код страны: {username}")
        if scope in ("region", "multi_region"):
            regions, country = _canonical_review_regions(regions, country)
        if (
            (scope == "region" and len(regions) != 1)
            or (scope == "multi_region" and len(regions) < 2)
            or (scope in ("country", "global", "unknown", "nonlocal") and regions)
            or (scope == "country" and not country)
        ):
            raise ValueError(f"Охват не соответствует регионам/стране: {username}")
        row["expected_country"] = "" if scope == "nonlocal" else country
        row["expected_regions"] = "|".join(regions)
    row["group_id"] = group_id(row)
    for name in ("topic", "geography"):
        row[name + "_reviewed"] = "yes" if name in dimensions else "no"
    return row


def read_review(path, *, confirmed_only=True, dimension="all"):
    if dimension not in ("all", "topic", "geography"):
        raise ValueError("Неизвестное измерение проверки")
    confirmed, seen, groups = [], set(), {}
    for row in _review_records(path):
        dimensions = confirmed_dimensions(row)
        if dimension != "all":
            dimensions &= {dimension}
        if not dimensions:
            if not confirmed_only:
                confirmed.append(row)
            continue
        row = _validate_record(row, dimensions)
        if row["username"] in seen:
            raise ValueError("Повторный username в подтверждённой разметке")
        seen.add(row["username"])
        gid = group_id(row)
        if gid in groups and groups[gid] != row["split"]:
            raise ValueError("Одинаковые тексты попали в train и test; исправьте split")
        groups[gid] = row["split"]
        row["group_id"] = gid
        confirmed.append(row)
    return confirmed


def inspect_review(path):
    """Collect every validation error without modifying or guessing owner labels."""
    rows = _review_records(path)
    valid = {"topic": [], "geography": []}
    errors, seen, groups = [], set(), {}
    for row in rows:
        dimensions = confirmed_dimensions(row)
        if not dimensions:
            continue
        username = row["username"].lstrip("@").strip().lower()
        duplicate = username in seen
        seen.add(username)
        gid = group_id(row)
        cross_split = gid in groups and groups[gid] != row["split"]
        groups[gid] = row["split"]
        for name in sorted(dimensions):
            try:
                normalized = _validate_record(row, {name})
                if duplicate or cross_split:
                    raise ValueError("Повторный username или пересечение train/test")
                valid[name].append(normalized)
            except (ValueError, TypeError) as exc:
                errors.append(dict(username=username, dimension=name, error=str(exc)))
    topic_rows = valid["topic"]
    supports = {split: dict(Counter(r["expected_topic"] for r in topic_rows if r["split"] == split))
                for split in ("train", "test")}
    return dict(total=len(rows), confirmed_any=sum(bool(confirmed_dimensions(r)) for r in rows),
                valid={name: len(items) for name, items in valid.items()}, errors=errors,
                topic_support=supports,
                group_support=dict(Counter(r["expected_group"] for r in topic_rows)),
                missing_topic_proposals=[dict(username=r["username"], proposal=r["proposed_topic"])
                                         for r in rows if r.get("proposed_topic")],
                applied=False,
                warning="Разметка, по которой меняется таксономия, является разработочной. Для итоговой оценки нужен независимый контрольный набор.")


def metrics(rows, predictions):
    if not rows:
        raise ValueError("Нет подтверждённых примеров для оценки")
    if len(rows) != len(predictions):
        raise ValueError("Число прогнозов не совпадает с числом примеров")
    group_correct = 0
    topic_n = geo_n = group_n = 0
    topic_correct = topic_accepted = topic_accepted_correct = geo_correct = (
        geo_accepted
    ) = geo_accepted_correct = 0
    confusions = Counter()
    counts = Counter()
    tag_tp = tag_fp = tag_fn = 0
    for row, pred in zip(rows, predictions):
        dimensions = confirmed_dimensions(row)
        expected_group = (
            row.get("expected_group") or TOPICS[row["expected_topic"]]["group"]
        )
        actual_group = pred.get("topic_group") or TOPICS[pred["subcategory"]]["group"]
        if "topic" in dimensions:
            group_n += 1
            group_correct += expected_group == actual_group
            # A reviewed broad group is not a ground-truth unknown leaf.
            if row["expected_topic"] != "other" or expected_group == "other_group":
                topic_n += 1
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
        if "geography" in dimensions:
            geo_n += 1
            correct_geo = expected == actual
            geo_correct += correct_geo
            if pred["geography_scope"] != "unknown":
                geo_accepted += 1
                geo_accepted_correct += correct_geo
        if "topic" in dimensions:
            true_tags = set(_list(row["expected_secondary_topics"]))
            guessed_tags = set(pred.get("secondary_topics", []))
            tag_tp += len(true_tags & guessed_tags)
            tag_fp += len(guessed_tags - true_tags)
            tag_fn += len(true_tags - guessed_tags)
    per_class = {}
    for topic in sorted(set(counts) | {b for a, b in confusions}):
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
        topic_examples=topic_n,
        topic_group_examples=group_n,
        geography_examples=geo_n,
        topic_group_accuracy=group_correct / group_n if group_n else None,
        topic_accuracy=topic_correct / topic_n if topic_n else None,
        topic_coverage=topic_accepted / topic_n if topic_n else None,
        topic_selective_precision=topic_accepted_correct / topic_accepted
        if topic_accepted
        else None,
        topic_macro_f1=sum(v["f1"] for v in per_class.values()) / len(per_class) if per_class else None,
        geography_exact_accuracy=geo_correct / geo_n if geo_n else None,
        geography_coverage=geo_accepted / geo_n if geo_n else None,
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


def evaluate(review_path, split="test", dimension="all"):
    rows = [
        r for r in read_review(review_path, dimension=dimension) if split == "all" or r["split"] == split
    ]
    # Evaluate automatic evidence, not owner overrides memorizing the same labels.
    predictions = [automatic_prediction(r) for r in rows]
    return dict(split=split, overrides_used=False, **metrics(rows, predictions))


def apply_review(review_path, apply=False, labels_path=None, *, dimension="all"):
    rows = read_review(review_path, dimension=dimension)
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
        entry = dict(entries.get(row["username"], {}))
        entry.update(
            review_schema_version=int(row["review_schema_version"]),
            taxonomy_version=int(row["taxonomy_version"]),
            reviewed=True,
            review_reason=row["review_reason"],
            proposed_topic=row["proposed_topic"],
            note=row["note"],
            reviewed_at=datetime.now(timezone.utc).isoformat(),
        )
        profile = dict(name=row["name"], description=informative_description(row["description"]))
        dimensions = confirmed_dimensions(row)
        if "topic" in dimensions:
            entry.update(topic=row["expected_topic"], group=row["expected_group"],
                         secondary_topics=_list(row["expected_secondary_topics"]), reviewed_profile=profile)
            if _yes(row.get("formats_reviewed")):
                entry["formats"] = _list(row["expected_formats"])
        if "geography" in dimensions:
            entry.update(scope=row["expected_scope"], regions=_list(row["expected_regions"], "|"),
                         country=row["expected_country"], geography_reviewed_profile=profile)
        entries[row["username"]] = entry
    if apply:
        atomic_write_text(
            path,
            json.dumps(
                dict(
                     version=3,
                    taxonomy_version=TAXONOMY["version"],
                    channels=entries,
                    updated_at=datetime.now(timezone.utc).isoformat(),
                ),
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
        )
    return dict(confirmed=len(rows), dimension=dimension, applied=apply, path=str(path))
