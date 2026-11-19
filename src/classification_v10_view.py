"""Автономный просмотр готовых прогнозов V10; без загрузки моделей и публикации."""

import hashlib
import json
import math
import re
from pathlib import Path

from .classification_text import informative_description
from .topic_keywords import TOPICS

ROOT = Path(__file__).resolve().parent.parent


def build_view(snapshot, results):
    if results.get("format") != "atlas-semantic-results-v10":
        raise ValueError("Нужен готовый отчёт V10")
    predictions = results.get("channels")
    if not isinstance(snapshot, list) or not isinstance(predictions, dict):
        raise ValueError("Некорректный снимок или прогнозы")
    rows, seen = [], set()
    for source in snapshot:
        username = str(source.get("username") or "").lower().lstrip("@")
        if not re.fullmatch(r"[a-z][a-z0-9_]{0,31}", username) or username in seen:
            raise ValueError("Пустой или повторный username снимка")
        seen.add(username)
        prediction = predictions.get(username)
        if prediction is None:
            continue
        profile = dict(
            name=source.get("name") or "",
            description=informative_description(source.get("description")),
        )
        if prediction.get("classification_profile") != profile:
            raise ValueError("Профиль отличается от снимка прогноза: " + username)
        if prediction.get("classification_engine") != "semantic-v10":
            raise ValueError("Неизвестный движок: " + username)
        topic = prediction.get("subcategory")
        if topic not in TOPICS or source.get("subcategory") not in TOPICS:
            raise ValueError("Неизвестная тема: " + username)
        candidates = prediction.get("topic_candidates", [])[:5]
        if any(
            c.get("id") not in TOPICS
            or isinstance(c.get("score"), bool)
            or not isinstance(c.get("score"), (int, float))
            or not math.isfinite(c["score"])
            for c in candidates
        ):
            raise ValueError("Некорректные оценки: " + username)
        reasons = prediction.get("classification_review_reasons", [])
        evidence = prediction.get("topic_evidence", [])
        if not isinstance(reasons, list) or any(
            not isinstance(r, str) for r in reasons
        ):
            raise ValueError("Некорректные причины проверки: " + username)
        if not isinstance(evidence, list) or any(
            not isinstance(e, dict) or not isinstance(e.get("text"), str)
            for e in evidence
        ):
            raise ValueError("Некорректные свидетельства: " + username)
        rows.append(
            dict(
                username=username,
                name=source.get("name") or username,
                description=source.get("description") or "",
                current=source["subcategory"],
                current_source=source.get("topic_source") or "unknown",
                predicted=topic,
                candidates=candidates,
                reasons=prediction.get("classification_review_reasons", []),
                review_required=bool(prediction.get("topic_review_required")),
                evidence=prediction.get("topic_evidence", []),
                routing=prediction.get("post_routing", {}),
            )
        )
    if set(predictions) - seen or not rows:
        raise ValueError("Прогнозы не соответствуют снимку или пусты")
    fingerprint = hashlib.sha256(
        json.dumps(
            [snapshot, results], sort_keys=True, ensure_ascii=False, allow_nan=False
        ).encode()
    ).hexdigest()
    payload = dict(
        format="atlas-v10-view-v1",
        fingerprint=fingerprint,
        model_sha256=results.get("model_sha256"),
        rows=rows,
        snapshot_channels=len(snapshot),
        topics=[dict(id=key, label=value["label"]) for key, value in TOPICS.items()],
    )
    data = (
        json.dumps(payload, ensure_ascii=False, allow_nan=False)
        .replace("<", "\\u003c")
        .replace("\u2028", "\\u2028")
        .replace("\u2029", "\\u2029")
    )
    template = (ROOT / "web/classification-v10-template.html").read_text(
        encoding="utf-8"
    )
    script = (ROOT / "web/classification-v10-view.js").read_text(encoding="utf-8")
    return template.replace("/*__DATA__*/", data).replace(
        "/*__SCRIPT__*/", script.replace("</script", "<\\/script")
    )
