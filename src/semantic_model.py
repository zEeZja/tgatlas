"""Versioned semantic encoders and small supervised heads, never train on test."""

from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re

from .classification_text import informative_description
from .classification_review import read_review, feature_text
from .file_io import atomic_write_text
from .topic_keywords import TAXONOMY, TOPICS, GROUPS

ENCODER = "intfloat/multilingual-e5-small"
POST_TEXT_LIMIT = 1000


def review_posts(row):
    """Frozen owner-review excerpts are training data, never a live post cache.

    Older CSVs omitted dates. Preserve that limitation instead of inventing dates
    or claiming the live provider's two-day diversity check has passed.
    """
    from .classification_samples import AD

    evidence = str(row.get("post_evidence") or "")
    header = re.compile(
        r"^#([1-9]\d*)[ \t]+(https://t\.me/([a-zA-Z][a-zA-Z0-9_]{0,31})/([1-9]\d*))"
        r"(?:[ \t]+\[date=([^\]\r\n]+)\])?[ \t]*$",
        re.MULTILINE,
    )
    matches = list(header.finditer(evidence))
    posts, seen = [], set()
    username = row["username"].lower().lstrip("@")
    for i, match in enumerate(matches):
        message_id, url, channel, url_id, date = match.groups()
        if message_id != url_id or channel.lower() != username:
            continue
        end = matches[i + 1].start() if i + 1 < len(matches) else len(evidence)
        text = evidence[match.end() : end].strip()[:POST_TEXT_LIMIT]
        key = " ".join(text.casefold().split())
        if len(text) < 40 or key in seen or AD.search(text):
            continue
        if date:
            try:
                parsed = datetime.fromisoformat(date.replace("Z", "+00:00"))
                date = parsed.isoformat() if parsed.tzinfo else None
            except ValueError:
                date = None
        seen.add(key)
        posts.append(dict(message_id=int(message_id), url=url, date=date, text=text))
    usable = posts[:12] if len(posts) >= 3 else []
    return usable, dict(
        requested=True,
        source="owner_review_snapshot",
        status="usable" if usable else "insufficient_review_evidence",
        usable=len(usable),
        dates_unknown=any(not p["date"] for p in usable),
        snapshot_sha256=hashlib.sha256(evidence.encode()).hexdigest(),
    )


def profile_text(row):
    return (
        (row.get("name") or "") + ". " + informative_description(row.get("description"))
    ).strip(" .")


def profile_hash(row):
    return hashlib.sha256(profile_text(row).encode()).hexdigest()


class Encoder:
    """One pinned multilingual encoder; masks padding and uses the E5 query prefix."""

    def __init__(
        self,
        name=ENCODER,
        revision=None,
        cache_dir="reports/semantic-v9/embeddings",
        device="cpu",
    ):
        import torch
        from transformers import AutoModel, AutoTokenizer

        torch.set_num_threads(min(torch.get_num_threads(), 4))
        self.torch = torch
        self.tokenizer = AutoTokenizer.from_pretrained(
            name, revision=revision, trust_remote_code=False
        )
        self.model = (
            AutoModel.from_pretrained(name, revision=revision, trust_remote_code=False)
            .float()
            .to(device)
            .eval()
        )
        self.name = name
        self.revision = getattr(self.model.config, "_commit_hash", None) or revision
        self.cache_dir = Path(cache_dir)
        self.device = device
        self.dimension = self.model.config.hidden_size

    def encode(self, texts, batch_size=8):
        import numpy as np

        result = [None] * len(texts)
        pending = []
        for i, text in enumerate(texts):
            text = str(text)[:6000]
            key = hashlib.sha256(
                json.dumps(
                    [self.name, self.revision, "query", text], ensure_ascii=False
                ).encode()
            ).hexdigest()
            path = self.cache_dir / (key + ".json")
            if path.exists():
                vector = json.loads(path.read_text())
                if len(vector) == self.dimension and np.isfinite(vector).all():
                    result[i] = vector
                    continue
            pending.append((i, text, path))
        for start in range(0, len(pending), batch_size):
            batch = pending[start : start + batch_size]
            tokens = self.tokenizer(
                ["query: " + t for _, t, _ in batch],
                padding=True,
                truncation=True,
                max_length=512,
                return_tensors="pt",
            ).to(self.device)
            with self.torch.inference_mode():
                hidden = self.model(**tokens).last_hidden_state
                mask = tokens["attention_mask"].unsqueeze(-1)
                vectors = (hidden * mask).sum(1) / mask.sum(1).clamp(min=1)
                vectors = (
                    self.torch.nn.functional.normalize(vectors, p=2, dim=1)
                    .cpu()
                    .tolist()
                )
            for (i, _, path), vector in zip(batch, vectors):
                result[i] = vector
                atomic_write_text(path, json.dumps(vector, allow_nan=False))
        return np.asarray(result, dtype=float).reshape(len(texts), self.dimension)


def documents(row, posts=None, post_text_limit=4000):
    docs = [profile_text(row)]
    docs.extend(str(p["text"])[:post_text_limit] for p in (posts or [])[:12])
    return docs


def combine(encoder, row, posts=None, post_text_limit=4000):
    import numpy as np

    vectors = encoder.encode(documents(row, posts, post_text_limit))
    vector = (
        vectors[0]
        if len(vectors) == 1
        else 0.35 * vectors[0] + 0.65 * vectors[1:].mean(axis=0)
    )
    return vector / max(np.linalg.norm(vector), 1e-12)


def head_predict(head, vectors):
    import numpy as np

    scores = np.asarray(vectors) @ np.asarray(head["coef"]).T + np.asarray(
        head["intercept"]
    )
    if len(head["classes"]) == 2:
        p = 1 / (1 + np.exp(-np.clip(scores[:, 0], -700, 700)))
        scores = np.column_stack([1 - p, p])
    else:
        scores = np.exp(scores - scores.max(axis=1, keepdims=True))
        scores /= scores.sum(axis=1, keepdims=True)
    result = []
    for values in scores:
        ids = np.argsort(-values, kind="stable")
        result.append(
            [dict(id=head["classes"][int(i)], score=float(values[i])) for i in ids]
        )
    return result


def fit_head(vectors, labels):
    from sklearn.linear_model import LogisticRegression

    if len(set(labels)) < 2:
        return None
    clf = LogisticRegression(
        C=2, class_weight="balanced", max_iter=2000, random_state=42
    ).fit(vectors, labels)
    return dict(
        classes=clf.classes_.tolist(),
        coef=clf.coef_.tolist(),
        intercept=clf.intercept_.tolist(),
        support=dict(Counter(labels)),
    )


def load_model(path):
    import numpy as np

    path = Path(path)
    if path.stat().st_size > 20_000_000:
        raise ValueError("Слишком большой файл модели")
    model = json.loads(path.read_text(encoding="utf-8"))
    if (
        model.get("format") != "atlas-semantic-v9"
        or model.get("taxonomy_version") != TAXONOMY["version"]
    ):
        raise ValueError("Формат или таксономия модели не совпадают с текущими")
    dim = model.get("dimension")
    if not isinstance(dim, int) or not 1 <= dim <= 4096:
        raise ValueError("Некорректная размерность модели")
    if model.get("post_text_limit", 4000) not in (1000, 4000):
        raise ValueError("Неизвестная политика длины публикаций")
    for kind, allowed in [
        ("topic", TOPICS),
        ("group", GROUPS),
        (
            "scope",
            {
                k: None
                for k in (
                    "unknown",
                    "region",
                    "multi_region",
                    "country",
                    "global",
                    "nonlocal",
                )
            },
        ),
    ]:
        h = model["heads"].get(kind)
        if h is None:
            continue
        classes = h["classes"]
        coef = np.asarray(h["coef"])
        intercept = np.asarray(h["intercept"])
        rows = 1 if len(classes) == 2 else len(classes)
        if (
            len(classes) < 2
            or len(classes) != len(set(classes))
            or any(c not in allowed for c in classes)
            or coef.shape != (rows, dim)
            or intercept.shape != (rows,)
            or not np.isfinite(coef).all()
            or not np.isfinite(intercept).all()
        ):
            raise ValueError("Некорректные классы или веса модели")
    model["artifact_sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
    return model


def train_pilot(review, output, encoder, post_provider=None):
    import numpy as np
    from .semantic_pipeline import cleaned_profile
    from .classification_review import (
        _review_records,
        _validate_record,
        confirmed_dimensions,
    )

    rows = [cleaned_profile(r) for r in read_review(review, dimension="topic")]
    topic_valid = {r["username"]: r for r in rows}
    geo_valid, geo_errors = {}, []
    for raw in _review_records(review):
        if "geography" not in confirmed_dimensions(raw):
            continue
        try:
            valid = cleaned_profile(_validate_record(raw, {"geography"}))
            if valid["username"] in geo_valid:
                raise ValueError("Повторный username в географической разметке")
            geo_valid[valid["username"]] = valid
        except ValueError as exc:
            geo_errors.append(dict(username=raw["username"], error=str(exc)))
    # Each head receives only its own confirmed answers, including geo-only rows.
    by_username = {**geo_valid, **topic_valid}
    for username, row in by_username.items():
        row["_labels"] = {}
        if username in topic_valid:
            row["_labels"].update(
                topic=row["expected_topic"], group=row["expected_group"]
            )
        if username in geo_valid:
            row["_labels"]["scope"] = geo_valid[username]["expected_scope"]
    # Identical channel profiles are one observation. Preserve the frozen split.
    unique = {}
    for r in by_username.values():
        key = feature_text(r)
        if key in unique:
            previous = unique[key]
            if previous["split"] != r["split"] or any(
                previous["_labels"][kind] != value
                for kind, value in r["_labels"].items()
                if kind in previous["_labels"]
            ):
                raise ValueError(
                    "Дубликат текста пересекает split или имеет разные ответы"
                )
            previous["_labels"].update(r["_labels"])
            if not previous.get("post_evidence"):
                previous["post_evidence"] = r.get("post_evidence", "")
        else:
            unique[key] = r
    records = list(unique.values())
    train = [r for r in records if r["split"] == "train"]
    test = [r for r in records if r["split"] == "test"]
    if (
        sum("topic" in r["_labels"] for r in train) < 10
        or sum("topic" in r["_labels"] for r in test) < 3
    ):
        raise ValueError(
            "Для пилота нужны минимум 10 подтверждённых train и 3 test; split не меняется"
        )
    prepared = {}
    routing = {}
    for r in sorted(
        records, key=lambda r: ("пост" not in r.get("note", "").lower(), r["username"])
    ):
        posts, detail = review_posts(r)
        if not posts:
            snapshot_detail = detail if r.get("post_evidence") else None
            posts, detail = (
                post_provider(r)
                if post_provider
                else ([], dict(status="not_requested"))
            )
            if snapshot_detail:
                detail["review_snapshot"] = snapshot_detail
        else:
            # Notes affect routing only, never embedding features or labels.
            from .semantic_pipeline import needs_posts

            detail["reasons"] = needs_posts(r)
        prepared[r["username"]] = posts
        routing[r["username"]] = detail
    # Shared/reposted content across train/test must not bridge the frozen split.
    hash_text = lambda p: hashlib.sha256(
        " ".join(p["text"][:POST_TEXT_LIMIT].casefold().split()).encode()
    ).hexdigest()
    train_hashes = {hash_text(p) for r in train for p in prepared[r["username"]]}
    dropped = 0
    for r in test:
        old = prepared[r["username"]]
        filtered = [p for p in old if hash_text(p) not in train_hashes]
        dropped += len(old) - len(filtered)
        prepared[r["username"]] = filtered if len(filtered) >= 3 else []
        if old and not prepared[r["username"]]:
            routing[r["username"]].update(
                status="insufficient_after_split_dedup", usable=0
            )
    X = np.asarray(
        [combine(encoder, r, prepared[r["username"]], POST_TEXT_LIMIT) for r in train]
    )
    Y = np.asarray(
        [combine(encoder, r, prepared[r["username"]], POST_TEXT_LIMIT) for r in test]
    )
    heads = {}
    evaluation = {}
    baseline = {}
    from .classification_review import automatic_prediction

    for kind, unknown in [
        ("topic", "other"),
        ("group", "other_group"),
        ("scope", "unknown"),
    ]:
        ids = [
            i
            for i, r in enumerate(train)
            if kind in r["_labels"] and r["_labels"][kind] != unknown
        ]
        test_ids = [i for i, r in enumerate(test) if kind in r["_labels"]]
        control = [test[i] for i in test_ids]
        heads[kind] = (
            fit_head(X[ids], [train[i]["_labels"][kind] for i in ids]) if ids else None
        )
        if not heads[kind] or not control:
            continue
        predictions = head_predict(heads[kind], Y[test_ids])
        expected = [r["_labels"][kind] for r in control]
        guesses = [p[0]["id"] for p in predictions]
        evaluation[kind] = dict(
            examples=len(control),
            top1_accuracy=sum(a == b for a, b in zip(expected, guesses)) / len(control),
            unseen_test_classes=sorted(set(expected) - set(heads[kind]["classes"])),
            single_example_training_classes=[
                c for c, n in heads[kind]["support"].items() if n == 1
            ],
            predictions=[
                dict(
                    username=r["username"],
                    expected=r["_labels"][kind],
                    predicted=p[0]["id"],
                    score=p[0]["score"],
                    needs_review=True,
                )
                for r, p in zip(control, predictions)
            ],
        )
        b = [automatic_prediction(r) for r in control]
        field = (
            "subcategory"
            if kind == "topic"
            else "geography_scope"
            if kind == "scope"
            else "topic_group"
        )
        baseline[kind] = sum(
            v[field] == r["_labels"][kind] for v, r in zip(b, control)
        ) / len(control)
    if heads.get("topic") is None:
        raise ValueError("Нужны минимум две подтверждённые темы в train")
    model = dict(
        format="atlas-semantic-v9",
        version=2,
        active=False,
        pilot_only=True,
        taxonomy_version=TAXONOMY["version"],
        encoder=encoder.name,
        revision=encoder.revision,
        dimension=X.shape[1],
        created_at=datetime.now(timezone.utc).isoformat(),
        heads=heads,
        training_examples=len(train),
        test_examples=len(test),
        confirmed_rows=len(by_username),
        confirmed_topic_rows=len(rows),
        duplicate_profiles=len(by_username) - len(records),
        input_policy="profile35_posts65_mean_query_e5_excerpt_v2",
        post_text_limit=POST_TEXT_LIMIT,
        review_sha256=hashlib.sha256(Path(review).read_bytes()).hexdigest(),
        training_fingerprint=hashlib.sha256(
            json.dumps(
                [(feature_text(r), r["_labels"], r["split"]) for r in records],
                sort_keys=True,
            ).encode()
        ).hexdigest(),
        evaluation=evaluation,
        legacy_baseline=baseline,
        post_routing=routing,
        post_manifest={
            username: [
                dict(
                    message_id=p.get("message_id"),
                    date=p.get("date"),
                    url=p.get("url"),
                    text_sha256=hashlib.sha256(
                        p["text"][:POST_TEXT_LIMIT].encode()
                    ).hexdigest(),
                )
                for p in posts
            ]
            for username, posts in prepared.items()
            if posts
        },
        geography_validation_errors=geo_errors,
        shared_test_posts_removed=dropped,
        warning="Малая выборка — исследовательский пилот, не подтверждение качества всего каталога. Все выводы требуют проверки. Порог не калиброван; контрольные метки не обучают модель.",
    )
    atomic_write_text(
        output, json.dumps(model, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
    )
    return model
