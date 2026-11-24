"""Основной пакетный V11: профиль/посты, новая сетка, отдельная география."""

from datetime import datetime, timezone
import json
from pathlib import Path
import re
import time

from .catalog_collection import suitable_posts
from .classification_runtime import (
    fingerprint,
    manual_overrides,
    policy,
    profile,
    taxonomy,
)
from .geography_v11 import GeographyResolver
from .topic_keywords import load_classification_rules


def classify(rows, directory, encoder, *, seconds=3600):
    import numpy as np

    if (
        not 1 <= len(rows) <= 3000
        or len({r["username"] for r in rows}) != len(rows)
        or any(
            not isinstance(r["username"], str)
            or not re.fullmatch(r"[a-z][a-z0-9_]{0,31}", r["username"])
            for r in rows
        )
    ):
        raise ValueError("Нужен снимок до 3000 уникальных публичных username")

    tx, settings = taxonomy(), policy()
    topics = sorted(tx["topics"], key=lambda t: t["id"])
    groups = {g["id"]: g for g in tx["groups"]}
    anchors = encoder.encode([t["label"] + ". " + t["definition"] for t in topics])
    geocoder = GeographyResolver(encoder)
    overrides = manual_overrides()
    old_rules = load_classification_rules()
    output = {}
    started = time.perf_counter()
    for start in range(0, len(rows), 32):
        if time.perf_counter() - started >= seconds:
            break
        batch = rows[start : start + 32]
        docs, evidence, spans = [], [], []
        for row in batch:
            username = row["username"]
            path = Path(directory) / "posts" / (username + ".json")
            posts, sample = [], {}
            if path.exists():
                sample = json.loads(path.read_text(encoding="utf-8"))
                if (
                    sample.get("username") != username
                    or sample.get("purpose") != "classification_only"
                ):
                    raise ValueError("Кеш постов не соответствует каналу")
                posts = suitable_posts(sample, settings["posts"])
            spans.append((len(docs), len(posts)))
            cleaned = profile(row)
            docs.append(cleaned["name"] + ". " + cleaned["description"])
            docs.extend(p["text"][: settings["post_characters"]] for p in posts)
            evidence.append((posts, sample))
        vectors = encoder.encode(docs)
        for row, (offset, count), (posts, sample) in zip(batch, spans, evidence):
            username = row["username"]
            profile_vector = vectors[offset]
            post_vectors = vectors[offset + 1 : offset + 1 + count]
            combined = profile_vector
            if count >= settings["minimum_posts"]:
                content = post_vectors.mean(axis=0)
                content /= max(float(np.linalg.norm(content)), 1e-12)
                combined = (1 - settings["content_weight"]) * profile_vector + settings[
                    "content_weight"
                ] * content
                combined /= max(float(np.linalg.norm(combined)), 1e-12)
            values = anchors @ combined
            order = np.argsort(-values, kind="stable")[:5]
            candidates = [
                dict(id=topics[int(i)]["id"], score=float(values[i])) for i in order
            ]
            selected = topics[int(order[0])]
            reasons = ["not_independently_calibrated"]
            if count < settings["minimum_posts"]:
                reasons.append("insufficient_posts")
            if posts and len({p["date"][:10] for p in posts}) < 2:
                reasons.append("narrow_post_time_window")
            manual = overrides.get(username, {})
            if manual.get("topic"):
                selected = next((t for t in topics if t["id"] == manual["topic"]), None)
                if selected is None:
                    raise ValueError("Неизвестная ручная тема V11")
                reasons = []
            geographic_override = manual.get("geography") or old_rules[
                "geography_overrides"
            ].get(username)
            if geographic_override and geographic_override.get("country") not in (
                None,
                "",
                "RU",
            ):
                geographic_override = dict(
                    geographic_override,
                    regions=[],
                    scope="country"
                    if geographic_override["scope"] in ("region", "multi_region")
                    else geographic_override["scope"],
                )
            geo = geocoder.resolve(
                row, posts, profile_vector, post_vectors, geographic_override
            )
            group = groups[selected["group"]]
            output[username] = dict(
                classification_engine="semantic-v11",
                classification_taxonomy_sha256=tx["fingerprint"],
                classification_version=11,
                classification_manual_dimensions=list(manual),
                classification_profile=profile(row),
                classification_created_at=datetime.now(timezone.utc).isoformat(),
                classification_model=dict(
                    encoder=encoder.settings["name"],
                    revision=encoder.settings["revision"],
                    precision=encoder.settings["precision"],
                    scoring="semantic_prototypes",
                    calibrated=False,
                ),
                subcategory=selected["id"],
                primary_category=selected["id"],
                topic_group=selected["group"],
                topic_group_label=group["label"],
                topic_category="v11",
                topic_path=[selected["group"], selected["id"]],
                topic_granularity="topic",
                topic_source="manual"
                if manual.get("topic")
                else "semantic-v11-proposal",
                topic_score=None if manual.get("topic") else candidates[0]["score"],
                topic_score_kind="cosine_similarity_not_probability",
                topic_margin=candidates[0]["score"] - candidates[1]["score"],
                topic_candidates=candidates,
                topic_review_required=bool(reasons),
                classification_review_reasons=reasons,
                topic_assignment_status="manual" if manual.get("topic") else "proposal",
                topic_tags=[selected["id"]],
                secondary_topics=[],
                broader_topics=[],
                topic_evidence=[dict(source="profile", text=docs[offset])]
                + [
                    dict(
                        source="post",
                        text=p["text"][:1000],
                        message_id=p.get("message_id"),
                        date=p.get("date"),
                        url=p.get("url"),
                    )
                    for p in posts
                ],
                post_routing=dict(
                    status="usable" if count >= 3 else "insufficient_posts",
                    requested=True,
                    usable=count,
                    fetched_at=sample.get("fetched_at"),
                    source=sample.get("source", "cache") if sample else None,
                ),
                **geo,
            )
        print(f"classified {len(output)}/{len(rows)}", flush=True)
    return dict(
        format="atlas-primary-results-v11",
        taxonomy_sha256=tx["fingerprint"],
        input_sha256=fingerprint(rows),
        created_at=datetime.now(timezone.utc).isoformat(),
        channels=output,
        calibration="not_independently_validated",
        remaining=len(rows) - len(output),
        benchmark=dict(
            seconds=time.perf_counter() - started, encoder=dict(encoder.stats)
        ),
    )
