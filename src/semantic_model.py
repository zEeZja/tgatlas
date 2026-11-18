"""Versioned semantic encoders and small supervised heads, never train on test."""

from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

from .classification_text import informative_description
from .classification_review import read_review, feature_text
from .file_io import atomic_write_text
from .topic_keywords import TAXONOMY, TOPICS, GROUPS

ENCODER = "intfloat/multilingual-e5-small"


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


def documents(row, posts=None):
    docs = [profile_text(row)]
    docs.extend(str(p["text"])[:4000] for p in (posts or [])[:12])
    return docs


def combine(encoder, row, posts=None):
    import numpy as np

    vectors = encoder.encode(documents(row, posts))
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

    rows = [cleaned_profile(r) for r in read_review(review, dimension="topic")]
    # Identical channel profiles are one observation. Preserve the frozen split.
    unique = {}
    for r in rows:
        key = feature_text(r)
        if key in unique:
            if (
                unique[key]["split"] != r["split"]
                or unique[key]["expected_topic"] != r["expected_topic"]
            ):
                raise ValueError(
                    "Дубликат текста пересекает split или имеет разные ответы"
                )
        else:
            unique[key] = r
    records = list(unique.values())
    train = [r for r in records if r["split"] == "train"]
    test = [r for r in records if r["split"] == "test"]
    if len(train) < 10 or len(test) < 3:
        raise ValueError(
            "Для пилота нужны минимум 10 подтверждённых train и 3 test; split не меняется"
        )
    prepared = {}
    routing = {}
    for r in sorted(
        records, key=lambda r: ("пост" not in r.get("note", "").lower(), r["username"])
    ):
        posts, detail = (
            post_provider(r) if post_provider else ([], dict(status="not_requested"))
        )
        prepared[r["username"]] = posts
        routing[r["username"]] = detail
    # Shared/reposted content across train/test must not bridge the frozen split.
    hash_text = lambda p: hashlib.sha256(
        " ".join(p["text"].casefold().split()).encode()
    ).hexdigest()
    train_hashes = {hash_text(p) for r in train for p in prepared[r["username"]]}
    dropped = 0
    for r in test:
        old = prepared[r["username"]]
        prepared[r["username"]] = [p for p in old if hash_text(p) not in train_hashes]
        dropped += len(old) - len(prepared[r["username"]])
    X = np.asarray([combine(encoder, r, prepared[r["username"]]) for r in train])
    Y = np.asarray([combine(encoder, r, prepared[r["username"]]) for r in test])
    from .classification_review import (
        _review_records,
        _validate_record,
        confirmed_dimensions,
    )

    geo_valid = {}
    geo_errors = []
    for raw in _review_records(review):
        if "geography" not in confirmed_dimensions(raw):
            continue
        try:
            valid = _validate_record(raw, {"geography"})
            geo_valid[valid["username"]] = valid
        except ValueError as exc:
            geo_errors.append(dict(username=raw["username"], error=str(exc)))
    heads = {}
    evaluation = {}
    baseline = {}
    from .classification_review import automatic_prediction

    for kind, key, unknown in [
        ("topic", "expected_topic", "other"),
        ("group", "expected_group", "other_group"),
        ("scope", "expected_scope", None),
    ]:
        ids = [
            i
            for i, r in enumerate(train)
            if r[key] != unknown and (kind != "scope" or r["username"] in geo_valid)
        ]
        test_ids = [
            i
            for i, r in enumerate(test)
            if kind != "scope" or r["username"] in geo_valid
        ]
        control = [test[i] for i in test_ids]
        heads[kind] = fit_head(X[ids], [train[i][key] for i in ids]) if ids else None
        if not heads[kind] or not control:
            continue
        predictions = head_predict(heads[kind], Y[test_ids])
        expected = [r[key] for r in control]
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
                    expected=r[key],
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
        baseline[kind] = sum(v[field] == r[key] for v, r in zip(b, control)) / len(
            control
        )
    if heads.get("topic") is None:
        raise ValueError("Нужны минимум две подтверждённые темы в train")
    model = dict(
        format="atlas-semantic-v9",
        version=1,
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
        confirmed_rows=len(rows),
        duplicate_profiles=len(rows) - len(records),
        input_policy="profile35_posts65_mean_query_e5_v1",
        review_sha256=hashlib.sha256(Path(review).read_bytes()).hexdigest(),
        training_fingerprint=hashlib.sha256(
            json.dumps(
                [(feature_text(r), r["expected_topic"], r["split"]) for r in records],
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
                    text_sha256=hashlib.sha256(p["text"].encode()).hexdigest(),
                )
                for p in posts
            ]
            for username, posts in prepared.items()
            if posts
        },
        geography_validation_errors=geo_errors,
        shared_test_posts_removed=dropped,
        warning="50 примеров — исследовательский пилот, не подтверждение качества 64 классов. Все выводы требуют проверки. Порог не калиброван; контрольные метки не обучают модель.",
    )
    atomic_write_text(
        output, json.dumps(model, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
    )
    return model
