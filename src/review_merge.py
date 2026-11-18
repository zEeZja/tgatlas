"""Merge local owner reviews without moving frozen splits or confirming proposals."""

import hashlib
import json
from pathlib import Path
import re

from .classification_review import (
    _review_records,
    confirmed_dimensions,
    feature_text,
    inspect_review,
    write_review,
)
from .file_io import atomic_write_text


def merge_reviews(paths, output, *, unknown_topics=(), country_corrections=()):
    paths = [Path(p) for p in paths]
    output = Path(output)
    report_path = output.with_suffix(".merge-report.json")
    if not paths or output.suffix.lower() != ".csv":
        raise ValueError("Укажите исходные CSV и отдельный выходной .csv")
    if {output.resolve(), report_path.resolve()} & {p.resolve() for p in paths}:
        raise ValueError("Выход не должен заменять исходную разметку")
    merged, changes = {}, []
    fields = {
        "topic": ("expected_topic", "expected_group", "expected_secondary_topics"),
        "geography": ("expected_scope", "expected_regions", "expected_country"),
    }
    for path in paths:
        seen = set()
        for row in _review_records(path):
            dimensions = confirmed_dimensions(row)
            if not dimensions:
                continue
            username = row["username"].strip().lstrip("@").lower()
            if not re.fullmatch(r"[a-z][a-z0-9_]{0,31}", username) or username in seen:
                raise ValueError("Неверный или повторный username: " + username)
            seen.add(username)
            row["username"] = username
            previous = merged.get(username)
            if previous:
                if previous["split"] != row["split"]:
                    changes.append(
                        dict(
                            username=username,
                            field="split",
                            old=row["split"],
                            new=previous["split"],
                        )
                    )
                row["split"] = previous["split"]
                if feature_text(previous) == feature_text(row):
                    for dimension in confirmed_dimensions(previous) - dimensions:
                        for field in fields[dimension]:
                            row[field] = previous[field]
                        dimensions.add(dimension)
                    row["post_evidence"] = row.get("post_evidence") or previous.get(
                        "post_evidence", ""
                    )
                    row["note"] = "\n".join(
                        dict.fromkeys(
                            value
                            for value in (previous.get("note"), row.get("note"))
                            if value
                        )
                    )
                else:
                    changes.append(
                        dict(
                            username=username,
                            reason="profile_changed",
                            dropped_dimensions=sorted(
                                confirmed_dimensions(previous) - dimensions
                            ),
                        )
                    )
            row["review_schema_version"] = "3"
            for dimension in fields:
                row[dimension + "_reviewed"] = (
                    "yes" if dimension in dimensions else "no"
                )
            merged[username] = row
    if not merged:
        raise ValueError("Нет подтверждённых ответов для объединения")
    for username in unknown_topics:
        username = username.lower().lstrip("@")
        if username not in merged:
            raise ValueError("Нет канала для уточнения темы: " + username)
        row = merged[username]
        old = row["expected_topic"]
        row["expected_topic"] = "other"
        row["expected_secondary_topics"] = ",".join(
            t
            for t in row["expected_secondary_topics"].split(",")
            if t not in (old, "other", "")
        )
        changes.append(
            dict(username=username, field="expected_topic", old=old, new="other")
        )
    for correction in country_corrections:
        username, separator, country = correction.partition("=")
        username, country = username.lower().lstrip("@"), country.upper()
        if (
            not separator
            or username not in merged
            or not re.fullmatch("[A-Z]{2}", country)
        ):
            raise ValueError("Уточнение страны: username=двухбуквенный_код")
        changes.append(
            dict(
                username=username,
                field="expected_country",
                old=merged[username]["expected_country"],
                new=country,
            )
        )
        merged[username]["expected_country"] = country
    # Two usernames with identical features cannot bridge train and test.
    profiles = {}
    for row in merged.values():
        key = feature_text(row)
        if row["split"] not in ("train", "test"):
            raise ValueError("Неверный split: " + row["username"])
        if key in profiles and profiles[key] != row["split"]:
            raise ValueError("Одинаковый профиль пересекает train/test")
        profiles[key] = row["split"]
    write_review(output, sorted(merged.values(), key=lambda r: r["username"]))
    report = dict(
        output=str(output),
        sources=[
            dict(path=str(p), sha256=hashlib.sha256(p.read_bytes()).hexdigest())
            for p in paths
        ],
        changes=changes,
        validation=inspect_review(output),
        applied=False,
    )
    atomic_write_text(
        report_path, json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    )
    return report
