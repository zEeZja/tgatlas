"""Черновик новой тематической сетки и исследование постов, не рабочая модель."""

from collections import defaultdict
import hashlib
import json
from pathlib import Path
import re

from .topic_keywords import TOPICS

ROOT = Path(__file__).resolve().parent.parent
DRAFT = ROOT / "config/taxonomy_v11_draft.json"


def load_taxonomy(path=DRAFT):
    taxonomy = json.loads(Path(path).read_text(encoding="utf-8"))
    if (
        taxonomy.get("format") != "atlas-topic-taxonomy-draft"
        or taxonomy.get("active") is not False
        or taxonomy.get("status") != "owner_review_required"
    ):
        raise ValueError("Нужен неактивный черновик сетки")
    groups, topics = set(), set()
    for group in taxonomy["groups"]:
        if (
            group["id"] in groups
            or not group["topics"]
            or any(
                not isinstance(group.get(k), str) or not group[k].strip()
                for k in ("id", "label", "definition")
            )
            or any(h not in TOPICS for h in group.get("legacy_hints", []))
        ):
            raise ValueError("Повторный или пустой раздел")
        groups.add(group["id"])
        for topic in group["topics"]:
            if topic["id"] in topics or any(
                not isinstance(topic.get(k), str) or not topic[k].strip()
                for k in ("id", "label", "definition", "boundary")
            ):
                raise ValueError("Повторная или неопределённая подтема")
            topics.add(topic["id"])
            if any(h not in TOPICS for h in topic["legacy_hints"]):
                raise ValueError("Неизвестная старая тема")
    return taxonomy


def legacy_mapping(taxonomy):
    choices = defaultdict(list)
    notes = {r["id"]: r["note"] for r in taxonomy.get("retired_legacy_labels", [])}
    for group in taxonomy["groups"]:
        for old in group.get("legacy_hints", []):
            choices[old].append(
                dict(group=group["id"], topic=None, label=group["label"])
            )
        for topic in group["topics"]:
            for old in topic["legacy_hints"]:
                choices[old].append(
                    dict(group=group["id"], topic=topic["id"], label=topic["label"])
                )
    return [
        dict(
            id=key,
            label=value["label"],
            candidates=choices[key],
            automatic=False,
            note=notes.get(key, ""),
        )
        for key, value in TOPICS.items()
    ]


def render_review(taxonomy, analysis=None):
    payload = dict(
        taxonomy=taxonomy,
        analysis=analysis,
        legacy_mapping=legacy_mapping(taxonomy),
        old_labels={k: v["label"] for k, v in TOPICS.items()},
    )
    data = (
        json.dumps(payload, ensure_ascii=False, allow_nan=False)
        .replace("<", "\\u003c")
        .replace("\u2028", "\\u2028")
        .replace("\u2029", "\\u2029")
    )
    template = (ROOT / "web/taxonomy-review-template.html").read_text(encoding="utf-8")
    script = (
        (ROOT / "web/taxonomy-review.js")
        .read_text(encoding="utf-8")
        .replace("</script", "<\\/script")
    )
    return template.replace("/*__DATA__*/", data).replace("/*__SCRIPT__*/", script)


def analyze_pilot(directory, taxonomy, encoder, settings):
    import numpy as np
    from sklearn.cluster import HDBSCAN

    directory = Path(directory)
    plan = json.loads((directory / "plan.json").read_text(encoding="utf-8"))
    state = json.loads((directory / "collection.json").read_text(encoding="utf-8"))
    if len(state["channels"]) != len(plan) and not state.get("stopped"):
        raise ValueError("Дождитесь завершения пилотного сбора")
    topics = [
        dict(topic, group=g["id"], group_label=g["label"])
        for g in taxonomy["groups"]
        for topic in g["topics"]
    ]
    texts = [t["label"] + ". " + t["definition"] for t in topics]
    anchors = encoder.encode(texts)
    samples, occurrences = {}, defaultdict(set)
    for row in plan:
        username = row["username"]
        if not re.fullmatch(r"[a-z][a-z0-9_]{0,31}", username):
            raise ValueError("Некорректный username")
        path = directory / "posts" / (username + ".json")
        if (
            state["channels"].get(username, {}).get("status") != "ok"
            or not path.exists()
        ):
            continue
        sample = json.loads(path.read_text(encoding="utf-8"))
        if (
            sample.get("username") != username
            or sample.get("purpose") != "classification_only"
        ):
            raise ValueError("Снимок постов относится к другому каналу")
        samples[username] = sample
        for post in sample["posts"]:
            fingerprint = hashlib.sha256(
                " ".join(post["text"].casefold().split()).encode()
            ).hexdigest()
            occurrences[fingerprint].add(username)
    rows, docs, spans, removed = [], [], [], 0
    for source in plan:
        username = source["username"]
        sample = samples.get(username, {})
        posts = []
        for post in sample.get("posts", [])[:20]:
            fingerprint = hashlib.sha256(
                " ".join(post["text"].casefold().split()).encode()
            ).hexdigest()
            if len(occurrences[fingerprint]) > 1:
                removed += 1
            else:
                posts.append(post)
        row = dict(
            username=username,
            name=source.get("name") or username,
            description=source.get("description") or "",
            profile_metadata_at=source.get("last_meta_at"),
            current=source["subcategory"],
            v10=source["v10_proposal"],
            posts=posts,
            low_diversity=sample.get("low_diversity", True),
            fetched_at=sample.get("fetched_at"),
            status="posts_available" if len(posts) >= 3 else "insufficient_posts",
        )
        start = len(docs)
        docs.append(row["name"] + ". " + row["description"])
        docs.extend(p["text"][:1000] for p in posts)
        spans.append((start, len(posts)))
        rows.append(row)
    embeddings = encoder.encode(docs)
    content_vectors, eligible = [], []

    def normalized(vector):
        return vector / max(float(np.linalg.norm(vector)), 1e-12)

    def ranking(vector):
        similarities = anchors @ vector
        return [
            dict(
                id=topics[int(i)]["id"],
                group=topics[int(i)]["group"],
                score=float(similarities[i]),
            )
            for i in np.argsort(-similarities, kind="stable")[:5]
        ]

    for index, (row, (start, count)) in enumerate(zip(rows, spans)):
        row["profile_candidates"] = ranking(embeddings[start])
        row["post_candidates"] = []
        row["combined_candidates"] = []
        if count < 3:
            continue
        content = normalized(embeddings[start + 1 : start + 1 + count].mean(axis=0))
        combined = normalized(0.25 * embeddings[start] + 0.75 * content)
        row["post_candidates"] = ranking(content)
        row["combined_candidates"] = ranking(combined)
        content_vectors.append(content)
        eligible.append(index)
    clusters = []
    if len(eligible) >= 5:
        cluster_labels = HDBSCAN(
            min_cluster_size=3, min_samples=2, metric="euclidean"
        ).fit_predict(content_vectors)
        for label in sorted(set(cluster_labels) - {-1}):
            members = [
                i for i, cluster in enumerate(cluster_labels) if cluster == label
            ]
            centroid = normalized(np.asarray(content_vectors)[members].mean(axis=0))
            nearest = sorted(
                members, key=lambda i: -float(content_vectors[i] @ centroid)
            )
            clusters.append(
                dict(
                    id=int(label),
                    channels=[rows[eligible[i]]["username"] for i in nearest],
                    nearest_draft_topics=ranking(centroid),
                    status="unnamed_owner_review_required",
                )
            )
        noise = int((cluster_labels == -1).sum())
    else:
        noise = len(eligible)
    return dict(
        format="atlas-taxonomy-experiment-v1",
        taxonomy_version=taxonomy["version"],
        taxonomy_sha256=hashlib.sha256(
            json.dumps(taxonomy, sort_keys=True, ensure_ascii=False).encode()
        ).hexdigest(),
        encoder=settings,
        profile_weight=0.25,
        content_weight=0.75,
        weighting_status="provisional_not_tuned_or_calibrated",
        trained_classifier=False,
        channels=rows,
        clusters=clusters,
        noise_channels=noise,
        shared_post_occurrences_removed=removed,
        source_collection=state,
        summary=dict(
            channels=len(rows),
            with_posts=len(eligible),
            without_sufficient_posts=len(rows) - len(eligible),
            post_occurrences=sum(len(r["posts"]) for r in rows),
            profile_post_disagreements=sum(
                r["profile_candidates"][0]["id"] != r["post_candidates"][0]["id"]
                for r in rows
                if r["post_candidates"]
            ),
        ),
        warning="Исследовательский поиск по новой сетке, не дообученный V10. Нет контрольной оценки точности. Группы HDBSCAN — кандидаты для обсуждения, а не утверждённые темы.",
    )
