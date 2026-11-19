"""V10: all-class semantic anchors + few-shot retrieval, train-only calibration.

No keyword winner, NLI cross-product, hidden test fitting, Telegram requests or
model execution in export/API. A missing trained class remains rankable through
its semantic definition, but cannot be auto-certified by a handful of examples.
"""

from collections import Counter, defaultdict
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import re
import time

from .classification_text import informative_description
from .classification_review import read_review
from .file_io import atomic_write_text
from .semantic_model import profile_text, review_posts
from .semantic_pipeline import cleaned_profile, geography, usable_posts
from .topic_keywords import (
    TOPICS,
    GROUPS,
    TAXONOMY,
    load_classification_rules,
    resolve_topic,
)

ROOT = Path(__file__).resolve().parent.parent
POLICY = ROOT / "config/classification_v10.json"
RESULTS = ROOT / "reports/classification-v10/catalog.json"
FORMAT = "atlas-classifier-v10"


def digest(value):
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False).encode()
    ).hexdigest()


def validate_policy(policy):
    if (
        policy.get("format") != "atlas-classification-policy-v10"
        or policy.get("version") != 1
    ):
        raise ValueError("Неизвестный формат политики V10")
    p, s, limits = policy["posts"], policy["selection"], policy["limits"]
    from .classification_encoder import validate_settings

    validate_settings(policy["encoder"])
    for section, fields in [
        (p, ["maximum", "text_limit"]),
        (
            s,
            [
                "seed",
                "folds",
                "minimum_calibration_examples",
                "minimum_class_examples",
                "minimum_profile_characters",
            ],
        ),
        (limits, ["download_bytes", "batch_channels", "post_channels", "seconds"]),
    ]:
        if any(
            isinstance(section[k], bool) or not isinstance(section[k], int)
            for k in fields
        ):
            raise ValueError("Ресурсные пределы должны быть целыми числами")
    if not isinstance(s["anchor_weights"], list) or any(
        isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v)
        for v in [p["profile_weight"], s["target_precision"], *s["anchor_weights"]]
    ):
        raise ValueError("Некорректные веса или пороги")
    if (
        not 3 <= p["maximum"] <= 12
        or not 100 <= p["text_limit"] <= 1000
        or not 0 <= p["profile_weight"] <= 1
        or not 2 <= s["folds"] <= 10
        or not s["anchor_weights"]
        or any(not 0 <= x <= 1 for x in s["anchor_weights"])
        or not 0.5 <= s["target_precision"] <= 1
        or s["minimum_calibration_examples"] < 10
        or s["minimum_class_examples"] < 2
        or not 1 <= limits["batch_channels"] <= 10000
        or not 1 <= limits["seconds"] <= 3600
        or not 0 <= limits["post_channels"] <= limits["batch_channels"]
    ):
        raise ValueError("Некорректные ограничения V10")
    return policy


def load_policy(path=POLICY):
    return validate_policy(json.loads(Path(path).read_text(encoding="utf-8")))


def label_manifest(policy):
    path = Path(policy["labels"])
    labels = json.loads(
        (path if path.is_absolute() else ROOT / path).read_text(encoding="utf-8")
    )
    classes = sorted(set(TOPICS) - {"other"})
    if set(labels) != set(classes) or any(
        not isinstance(v, str) or not 10 <= len(v) <= 1000 for v in labels.values()
    ):
        raise ValueError("Нужны положительные определения всех содержательных тем")
    return [
        dict(
            id=key,
            group=TOPICS[key]["group"],
            label=TOPICS[key]["label"],
            definition=labels[key],
        )
        for key in classes
    ]


def unit(vectors):
    import numpy as np

    a = np.asarray(vectors, dtype=np.float32)
    return a / np.maximum(np.linalg.norm(a, axis=-1, keepdims=True), 1e-12)


def input_documents(row, posts, policy):
    text = profile_text(cleaned_profile(row))
    return [text] + [
        p["text"][: policy["posts"]["text_limit"]]
        for p in posts[: policy["posts"]["maximum"]]
    ]


def encode_channels(rows, posts, encoder, policy):
    """One global embedding pass, not N separate model calls per channel."""
    import numpy as np

    docs, spans = [], []
    for row, sample in zip(rows, posts):
        batch = input_documents(row, sample, policy)
        spans.append((len(docs), len(batch)))
        docs.extend(batch)
    embeddings = encoder.encode(docs)
    weight = policy["posts"]["profile_weight"]
    combined = []
    for start, size in spans:
        combined.append(
            embeddings[start]
            if size == 1
            else weight * embeddings[start]
            + (1 - weight) * embeddings[start + 1 : start + size].mean(axis=0)
        )
    return (
        unit(np.asarray(combined).reshape(len(rows), encoder.dimension)),
        embeddings,
        spans,
    )


def anchors(encoder, manifest):
    texts = []
    for label in manifest:
        texts.extend([label["definition"], label["label"] + ". " + label["definition"]])
    vectors = encoder.encode(texts)
    return unit(vectors.reshape(len(manifest), 2, encoder.dimension).mean(axis=1))


def scores(
    vectors, anchor_vectors, examples, example_labels, classes, anchor_weight, head=None
):
    import numpy as np

    if head is not None:
        return np.asarray(vectors) @ np.asarray(head["coef"]).T + np.asarray(
            head["intercept"]
        )
    prior = np.asarray(vectors) @ np.asarray(anchor_vectors).T
    prior = (prior - prior.mean(axis=1, keepdims=True)) / np.maximum(
        prior.std(axis=1, keepdims=True), 1e-6
    )
    if anchor_weight == 1 or not len(examples):
        return prior
    similarities = np.asarray(vectors) @ np.asarray(examples).T
    nearest = np.zeros_like(prior)
    supported = []
    for column, label in enumerate(classes):
        ids = [i for i, value in enumerate(example_labels) if value == label]
        if ids:
            supported.append(column)
            k = min(2, len(ids))
            nearest[:, column] = np.sort(similarities[:, ids], axis=1)[:, -k:].mean(
                axis=1
            )
    if supported:
        observed = nearest[:, supported]
        nearest[:, supported] = (
            observed - observed.mean(axis=1, keepdims=True)
        ) / np.maximum(observed.std(axis=1, keepdims=True), 1e-6)
    # Missing example classes have neutral evidence, not a different score scale.
    return anchor_weight * prior + (1 - anchor_weight) * nearest


def fit_linear(
    vectors,
    labels,
    anchor_vectors,
    classes,
    regularization,
    anchor_example_weight,
    balanced=False,
):
    import numpy as np
    import warnings
    from sklearn.linear_model import LogisticRegression

    x = np.vstack([vectors, anchor_vectors])
    classifier = LogisticRegression(
        C=regularization,
        max_iter=1000,
        random_state=42,
        class_weight="balanced" if balanced else None,
    )
    with warnings.catch_warnings():
        warnings.filterwarnings(
            "ignore",
            message="The number of unique classes is greater than 50%",
            category=UserWarning,
        )
        classifier.fit(
            x,
            list(labels) + classes,
            sample_weight=np.asarray(
                [1.0] * len(labels) + [anchor_example_weight] * len(classes)
            ),
        )
    if classifier.classes_.tolist() != classes:
        raise ValueError("Linear head must cover the complete sorted taxonomy")
    return dict(
        coef=classifier.coef_.tolist(), intercept=classifier.intercept_.tolist()
    )


def rank(matrix, classes):
    import numpy as np

    output = []
    for values in matrix:
        order = np.argsort(-values, kind="stable")
        output.append([dict(id=classes[int(i)], score=float(values[i])) for i in order])
    return output


def fold_ids(rows, count, seed):
    """Stratify without moving the frozen test or putting identical profiles apart."""
    groups = defaultdict(list)
    for i, row in enumerate(rows):
        groups[row["expected_topic"]].append(i)
    assignments = [0] * len(rows)
    for label, ids in sorted(groups.items()):
        ids.sort(key=lambda i: digest(profile_text(cleaned_profile(rows[i]))))
        offset = int(digest([seed, label])[:8], 16) % count
        for j, i in enumerate(ids):
            assignments[i] = (offset + j) % count
    return assignments


def calibration(predictions, expected, selection):
    """Training OOF operating point. Never a probability or test-set guarantee."""
    import numpy as np

    tops = np.asarray([p[0]["score"] for p in predictions])
    gaps = np.asarray([p[0]["score"] - p[1]["score"] for p in predictions])
    correct = np.asarray(
        [p[0]["id"] == label for p, label in zip(predictions, expected)]
    )
    best = None
    for floor in np.unique(np.quantile(tops, np.linspace(0, 1, 16))):
        for margin in np.unique(np.quantile(gaps, np.linspace(0, 1, 16))):
            accepted = (tops >= floor) & (gaps >= margin)
            n = int(accepted.sum())
            if n < selection["minimum_calibration_examples"]:
                continue
            precision = float(correct[accepted].mean())
            if precision < selection["target_precision"]:
                continue
            candidate = dict(
                minimum_score=float(floor),
                minimum_margin=float(margin),
                accepted=n,
                precision=precision,
            )
            if best is None or (n, precision, margin, floor) > (
                best["accepted"],
                best["precision"],
                best["minimum_margin"],
                best["minimum_score"],
            ):
                best = candidate
    return dict(
        status="estimated_on_training_oof" if best else "insufficient_calibration",
        examples=len(expected),
        **(best or {}),
        calibrated_probability=False,
        independent_quality_guarantee=False,
    )


def prepare_review(path, policy, post_cache=None):
    rows = read_review(path, dimension="topic")
    unique = {}
    for row in rows:
        text = profile_text(cleaned_profile(row))
        if not text:
            raise ValueError("Пустой подтверждённый профиль")
        if text in unique:
            old = unique[text]
            if (
                old["split"] != row["split"]
                or old["expected_topic"] != row["expected_topic"]
            ):
                raise ValueError(
                    "Дубликат профиля пересекает split или имеет разные ответы"
                )
            if not old.get("post_evidence") and row.get("post_evidence"):
                old["post_evidence"] = row["post_evidence"]
        else:
            unique[text] = dict(row)
    rows = sorted(unique.values(), key=lambda r: (r["split"], r["username"]))
    posts = [review_posts(r)[0][: policy["posts"]["maximum"]] for r in rows]
    if post_cache is not None:
        for i, row in enumerate(rows):
            if posts[i]:
                continue
            username = row["username"].lower().lstrip("@")
            if not re.fullmatch(r"[a-z][a-z0-9_]{0,31}", username):
                raise ValueError("Некорректный username обучающего снимка")
            cached = Path(post_cache) / (username + ".json")
            if not cached.exists():
                continue
            sample = json.loads(cached.read_text(encoding="utf-8"))
            if (
                sample.get("username") != username
                or sample.get("purpose") != "classification_only"
            ):
                raise ValueError("Обучающий снимок относится к другому каналу")
            # Historical training evidence is frozen, NOT advertised as a fresh
            # classification cache. Prediction still enforces the live cache TTL.
            posts[i] = usable_posts(sample)[0][: policy["posts"]["maximum"]]
    # Also remove cross-channel reposts between internal folds, not only train/test.
    occurrences = defaultdict(set)
    for i, sample in enumerate(posts):
        for p in sample:
            occurrences[digest(" ".join(p["text"].casefold().split()))].add(i)
    removed = 0
    for i, sample in enumerate(posts):
        clean = [
            p
            for p in sample
            if len(occurrences[digest(" ".join(p["text"].casefold().split()))]) == 1
        ]
        removed += len(sample) - len(clean)
        posts[i] = clean if len(clean) >= 3 else []
    train = [i for i, row in enumerate(rows) if row["split"] == "train"]
    test = [i for i, row in enumerate(rows) if row["split"] == "test"]
    if len(train) < 20 or len(test) < 3:
        raise ValueError("Нужны не менее 20 подтверждённых train и 3 замороженных test")
    return rows, posts, train, test, removed


def evaluate(expected, predictions, policy, supports, operating_point):
    from sklearn.metrics import accuracy_score, f1_score

    guesses = [p[0]["id"] for p in predictions]
    accepted = [
        not decision_reasons(p, supports, operating_point, policy) for p in predictions
    ]
    hits = sum(g == y for y, g, yes in zip(expected, guesses, accepted) if yes)
    n = sum(accepted)
    return dict(
        examples=len(expected),
        top1_accuracy=float(accuracy_score(expected, guesses)),
        macro_f1=float(f1_score(expected, guesses, average="macro", zero_division=0)),
        assigned=n,
        coverage=n / len(expected),
        precision_among_assigned=hits / n if n else None,
        raw_correct=sum(g == y for y, g in zip(expected, guesses)),
    )


def decision_reasons(prediction, support, operating_point, policy):
    reasons = []
    top, second = prediction[:2]
    if operating_point.get("status") != "estimated_on_training_oof":
        reasons.append("insufficient_calibration")
    elif (
        top["score"] < operating_point["minimum_score"]
        or top["score"] - second["score"] < operating_point["minimum_margin"]
    ):
        reasons.append("uncertain_semantic_match")
    if support.get(top["id"], 0) < policy["selection"]["minimum_class_examples"]:
        reasons.append("definition_only_or_rare_class")
    return reasons


def train(review, output, encoder, policy, post_cache=None):
    validate_policy(policy)
    if Path(review).resolve() == Path(output).resolve():
        raise ValueError("Артефакт не должен заменять проверенную разметку")
    import numpy as np
    from sklearn.metrics import f1_score

    started = time.perf_counter()
    manifest = label_manifest(policy)
    classes = [label["id"] for label in manifest]
    rows, posts, train_ids, test_ids, removed = prepare_review(
        review, policy, post_cache
    )
    anchor_vectors = anchors(encoder, manifest)
    all_vectors, _, _ = encode_channels(rows, posts, encoder, policy)
    train_rows = [rows[i] for i in train_ids]
    X = all_vectors[train_ids]
    y = [row["expected_topic"] for row in train_rows]
    folds = fold_ids(
        train_rows, policy["selection"]["folds"], policy["selection"]["seed"]
    )
    candidates = []
    settings = [
        dict(kind="relative_cosine", anchor_weight=w)
        for w in policy["selection"]["anchor_weights"]
    ]
    settings += [
        dict(
            kind="linear",
            regularization=c,
            anchor_example_weight=w,
            anchor_weight=1.0,
            balanced=b,
        )
        for c in (10.0, 100.0, 1000.0)
        for w in (0.25, 1.0)
        for b in (False, True)
    ]
    for parameters in settings:
        weight = parameters["anchor_weight"]
        oof = np.zeros((len(train_ids), len(classes)), dtype=np.float32)
        for fold in sorted(set(folds)):
            fitting = [i for i, f in enumerate(folds) if f != fold and y[i] != "other"]
            validation = [i for i, f in enumerate(folds) if f == fold]
            fitting_labels = [y[i] for i in fitting]
            head = (
                fit_linear(
                    X[fitting],
                    fitting_labels,
                    anchor_vectors,
                    classes,
                    parameters["regularization"],
                    parameters["anchor_example_weight"],
                    parameters["balanced"],
                )
                if parameters["kind"] == "linear"
                else None
            )
            oof[validation] = scores(
                X[validation],
                anchor_vectors,
                X[fitting],
                fitting_labels,
                classes,
                weight,
                head,
            )
        predictions = rank(oof, classes)
        quality = float(
            f1_score(
                y, [p[0]["id"] for p in predictions], average="macro", zero_division=0
            )
        )
        candidates.append((quality, parameters, predictions))
    quality, parameters, oof = max(
        candidates, key=lambda entry: (entry[0], entry[1]["kind"] == "relative_cosine")
    )
    weight = parameters["anchor_weight"]
    operating_point = calibration(oof, y, policy["selection"])
    fitting = [i for i, label in enumerate(y) if label != "other"]
    support = dict(Counter(y[i] for i in fitting))
    model = dict(
        format=FORMAT,
        version=1,
        active=False,
        taxonomy_version=TAXONOMY["version"],
        encoder=dict(encoder.settings),
        encoder_identity=encoder.identity,
        dimension=encoder.dimension,
        classes=classes,
        labels=manifest,
        labels_sha256=digest(manifest),
        policy=policy,
        policy_sha256=digest(policy),
        anchor_vectors=anchor_vectors.tolist(),
        example_vectors=X[fitting].tolist(),
        example_labels=[y[i] for i in fitting],
        support=support,
        anchor_weight=weight,
        scoring=parameters,
        head=fit_linear(
            X[fitting],
            [y[i] for i in fitting],
            anchor_vectors,
            classes,
            parameters["regularization"],
            parameters["anchor_example_weight"],
            parameters["balanced"],
        )
        if parameters["kind"] == "linear"
        else None,
        calibration=operating_point,
        training_examples=len(train_ids),
        test_examples=len(test_ids),
        training_fingerprint=digest(
            [
                (
                    profile_text(cleaned_profile(rows[i])),
                    rows[i]["expected_topic"],
                    input_documents(rows[i], posts[i], policy),
                )
                for i in train_ids
            ]
        ),
        review_sha256=hashlib.sha256(Path(review).read_bytes()).hexdigest(),
        created_at=datetime.now(timezone.utc).isoformat(),
        input_policy="profile_and_frozen_posts_v10.1",
        shared_posts_removed=removed,
        evidence_manifest={
            rows[i]["username"]: dict(
                source="owner_review_snapshot"
                if rows[i].get("post_evidence")
                else "frozen_local_cache",
                posts=[
                    dict(
                        message_id=p.get("message_id"),
                        url=p.get("url"),
                        date=p.get("date"),
                        sha256=digest(p["text"][: policy["posts"]["text_limit"]]),
                    )
                    for p in sample
                ],
            )
            for i, sample in enumerate(posts)
            if sample
        },
        selection=dict(
            source="training_oof_only",
            folds=len(set(folds)),
            macro_f1=quality,
            candidates=[dict(**p, macro_f1=q) for q, p, _ in candidates],
        ),
    )
    control = rank(
        scores(
            all_vectors[test_ids],
            anchor_vectors,
            X[fitting],
            model["example_labels"],
            classes,
            weight,
            model["head"],
        ),
        classes,
    )
    expected = [rows[i]["expected_topic"] for i in test_ids]
    model["evaluation"] = evaluate(expected, control, policy, support, operating_point)
    model["evaluation"].update(
        split="frozen_test",
        independent_of_historical_tuning=False,
        missing_training_classes=sorted(set(expected) - set(support) - {"other"}),
        predictions=[
            dict(
                username=rows[i]["username"],
                expected=label,
                predicted=p[0]["id"],
                score=p[0]["score"],
                margin=p[0]["score"] - p[1]["score"],
            )
            for i, label, p in zip(test_ids, expected, control)
        ],
    )
    from .classification_review import automatic_prediction

    baseline = [automatic_prediction(rows[i])["subcategory"] for i in test_ids]
    model["evaluation"]["legacy_rules_accuracy"] = sum(
        a == b for a, b in zip(expected, baseline)
    ) / len(expected)
    model["benchmark"] = dict(
        seconds=time.perf_counter() - started, encoder=dict(encoder.stats)
    )
    model["warning"] = (
        "Scores are similarities, not calibrated probabilities. Historical frozen test is diagnostic, not a new independent accuracy certificate."
    )
    atomic_write_text(
        output, json.dumps(model, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
    )
    summary = {
        k: v for k, v in model.items() if k not in ("anchor_vectors", "example_vectors")
    }
    atomic_write_text(
        Path(output).with_suffix(".report.json"),
        json.dumps(summary, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
    )
    return model


def load_model(path):
    import numpy as np

    path = Path(path)
    if path.stat().st_size > 20_000_000:
        raise ValueError("Слишком большой артефакт V10")
    model = json.loads(path.read_text(encoding="utf-8"))
    if (
        model.get("format") != FORMAT
        or model.get("version") != 1
        or model.get("taxonomy_version") != TAXONOMY["version"]
    ):
        raise ValueError("Несовместимый артефакт V10")
    classes, dimension = model["classes"], model["dimension"]
    if (
        classes != sorted(set(TOPICS) - {"other"})
        or not isinstance(dimension, int)
        or not 1 <= dimension <= 4096
    ):
        raise ValueError("Артефакт должен покрывать все темы текущей таксономии")
    if (
        digest(model["labels"]) != model["labels_sha256"]
        or digest(model["policy"]) != model["policy_sha256"]
    ):
        raise ValueError("Повреждены определения или политика модели")
    validate_policy(model["policy"])
    if model["labels"] != label_manifest(model["policy"]):
        raise ValueError("Определения тем изменились; модель нужно переобучить")
    if (
        model["encoder"] != model["policy"]["encoder"]
        or model["encoder"]["dimension"] != dimension
    ):
        raise ValueError("Encoder не совпадает с политикой артефакта")
    kind = model.get("scoring", {}).get("kind")
    if kind not in ("linear", "relative_cosine") or (kind == "linear") != (
        model.get("head") is not None
    ):
        raise ValueError("Некорректный алгоритм V10")
    if model["support"] != dict(Counter(model["example_labels"])):
        raise ValueError("Некорректная поддержка классов")
    operating = model["calibration"]
    if operating.get("status") not in (
        "estimated_on_training_oof",
        "insufficient_calibration",
    ):
        raise ValueError("Некорректная калибровка V10")
    if operating["status"] == "estimated_on_training_oof":
        if any(
            isinstance(operating.get(k), bool)
            or not isinstance(operating.get(k), (int, float))
            or not math.isfinite(operating[k])
            for k in ("minimum_score", "minimum_margin", "precision", "accepted")
        ):
            raise ValueError("Некорректные пороги V10")
        if (
            operating["precision"] < model["policy"]["selection"]["target_precision"]
            or operating["precision"] > 1
            or operating["minimum_margin"] < 0
            or operating["accepted"]
            < model["policy"]["selection"]["minimum_calibration_examples"]
        ):
            raise ValueError("Недостаточная калибровка V10")
    for key, count in [
        ("anchor_vectors", len(classes)),
        ("example_vectors", len(model["example_labels"])),
    ]:
        array = np.asarray(model[key], dtype=np.float32)
        if (
            array.shape != (count, dimension)
            or not np.isfinite(array).all()
            or not np.allclose(np.linalg.norm(array, axis=1), 1, atol=0.01)
        ):
            raise ValueError("Некорректные векторы модели")
    if (
        any(c not in classes for c in model["example_labels"])
        or not 0 <= model["anchor_weight"] <= 1
    ):
        raise ValueError("Некорректная обучающая выборка V10")
    if model.get("head") is not None:
        coef, intercept = (
            np.asarray(model["head"]["coef"]),
            np.asarray(model["head"]["intercept"]),
        )
        if (
            coef.shape != (len(classes), dimension)
            or intercept.shape != (len(classes),)
            or not np.isfinite(coef).all()
            or not np.isfinite(intercept).all()
        ):
            raise ValueError("Некорректная линейная голова V10")
    model["artifact_sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
    return model


def classify(rows, model, encoder, provider=None, rules=None, *, seconds=None):
    """Bounded batches, optional local evidence only; no network provider by default."""
    started = time.perf_counter()
    policy = model["policy"]
    if encoder.identity != model["encoder_identity"]:
        raise ValueError("Encoder/precision/token policy differs from trained model")
    if len(rows) > policy["limits"]["batch_channels"]:
        raise ValueError("Превышен бюджет каналов")
    rules = rules or load_classification_rules()
    output, post_used = {}, 0
    total_rows = len(rows)
    pending, seen = [], set()
    for row in rows:
        username = str(row.get("username") or "").lower().lstrip("@")
        if not re.fullmatch(r"[a-z][a-z0-9_]{0,31}", username) or username in seen:
            raise ValueError("Некорректный или повторный username V10")
        seen.add(username)
        if username not in rules["overrides"]:
            pending.append(row)
            continue
        result = resolve_topic(row, **rules)
        result.update(
            geography(cleaned_profile(row), overrides=rules["geography_overrides"])
        )
        result.update(
            classification_engine="semantic-v10",
            classification_version=10,
            classification_profile=dict(
                name=row.get("name") or "",
                description=informative_description(row.get("description")),
            ),
            classification_model=dict(encoder="not_loaded_manual_priority"),
            classification_review_reasons=["reviewed_profile_changed"]
            if result.get("classification_review_stale")
            else [],
            post_routing=dict(requested=False, status="manual_priority"),
        )
        output[username] = result
    rows = pending
    maximum_seconds = seconds or policy["limits"]["seconds"]
    for start in range(0, len(rows), 64):
        if time.perf_counter() - started >= maximum_seconds:
            break
        batch = rows[start : start + 64]
        initial, _, _ = encode_channels(batch, [[] for _ in batch], encoder, policy)
        initial_ranks = rank(
            scores(
                initial,
                model["anchor_vectors"],
                model["example_vectors"],
                model["example_labels"],
                model["classes"],
                model["anchor_weight"],
                model.get("head"),
            ),
            model["classes"],
        )
        posts, details = [], []
        for row, prediction in zip(batch, initial_ranks):
            reasons = decision_reasons(
                prediction, model["support"], model["calibration"], policy
            )
            sparse = (
                len(profile_text(cleaned_profile(row)))
                < policy["selection"]["minimum_profile_characters"]
            )
            requested = bool(reasons or sparse)
            sample, detail = (
                [],
                dict(
                    requested=requested,
                    status="not_needed" if not requested else "not_available_offline",
                ),
            )
            if requested and provider and post_used < policy["limits"]["post_channels"]:
                sample, detail = provider(row)
                post_used += 1
                sample = sample[: policy["posts"]["maximum"]]
            posts.append(sample)
            details.append(dict(detail, requested=requested))
        vectors, all_docs, spans = encode_channels(batch, posts, encoder, policy)
        predictions = rank(
            scores(
                vectors,
                model["anchor_vectors"],
                model["example_vectors"],
                model["example_labels"],
                model["classes"],
                model["anchor_weight"],
                model.get("head"),
            ),
            model["classes"],
        )
        for row, sample, detail, prediction, (offset, size) in zip(
            batch, posts, details, predictions, spans
        ):
            username = str(row["username"]).lower().lstrip("@")
            topic = prediction[0]["id"]
            reasons = decision_reasons(
                prediction, model["support"], model["calibration"], policy
            )
            if (
                len(profile_text(cleaned_profile(row)))
                < policy["selection"]["minimum_profile_characters"]
                and not sample
            ):
                reasons.append("insufficient_content")
            if sample:
                post_guesses = rank(
                    scores(
                        all_docs[offset + 1 : offset + size],
                        model["anchor_vectors"],
                        model["example_vectors"],
                        model["example_labels"],
                        model["classes"],
                        model["anchor_weight"],
                        model.get("head"),
                    ),
                    model["classes"],
                )
                votes = Counter(p[0]["id"] for p in post_guesses)
                if votes.get(topic, 0) / len(sample) < 0.6:
                    reasons.append("mixed_post_topics")
            else:
                votes = Counter()
            manual = rules["overrides"].get(username)
            if manual is not None:
                topic = manual
                reasons = []
            group = (
                rules.get("group_overrides", {}).get(username, TOPICS[topic]["group"])
                if manual is not None
                else TOPICS[topic]["group"]
            )
            geo = geography(cleaned_profile(row), sample, rules["geography_overrides"])
            output[username] = dict(
                classification_engine="semantic-v10",
                classification_version=10,
                classification_model=dict(
                    encoder=model["encoder"]["name"],
                    revision=model["encoder"]["revision"],
                    precision=model["encoder"]["precision"],
                    artifact_sha256=model.get("artifact_sha256"),
                ),
                classification_profile=dict(
                    name=row.get("name") or "",
                    description=informative_description(row.get("description")),
                ),
                subcategory=topic,
                primary_category=topic,
                topic_group=group,
                topic_group_label=GROUPS[group]["label"],
                topic_category=TOPICS[topic]["section"],
                topic_path=[group, topic] if topic != "other" else [group],
                topic_granularity="topic"
                if topic != "other"
                else "group"
                if group != "other_group"
                else "unknown",
                broader_topics=TOPICS[topic].get("parent_topics", []),
                topic_source="manual" if manual is not None else "semantic-v10",
                topic_score=None if manual is not None else prediction[0]["score"],
                topic_score_kind="relative_semantic_score_not_probability",
                topic_margin=prediction[0]["score"] - prediction[1]["score"],
                topic_candidates=prediction[:5],
                topic_tags=[topic] + rules["tag_overrides"].get(username, []),
                secondary_topics=rules["tag_overrides"].get(username, []),
                topic_review_required=bool(reasons),
                classification_review_reasons=reasons,
                topic_evidence=[
                    dict(
                        source="profile", text=profile_text(cleaned_profile(row))[:2000]
                    )
                ]
                + [
                    dict(
                        source="post",
                        text=p["text"][:1000],
                        message_id=p.get("message_id"),
                        date=p.get("date"),
                        url=p.get("url"),
                    )
                    for p in sample
                ],
                post_routing=detail,
                post_topic_votes=dict(votes),
                **geo,
            )
    return output, dict(
        seconds=time.perf_counter() - started,
        channels=len(output),
        remaining=total_rows - len(output),
        post_channels=post_used,
        encoder=dict(encoder.stats),
        applied=False,
    )


def validate_results(rows):
    if not isinstance(rows, dict):
        raise ValueError("Ожидается словарь результатов V10")
    for username, row in rows.items():
        if (
            not isinstance(username, str)
            or not re.fullmatch(r"[a-z][a-z0-9_]{0,31}", username)
            or not isinstance(row, dict)
            or row.get("classification_engine") != "semantic-v10"
            or row.get("subcategory") not in TOPICS
            or row.get("topic_group") not in GROUPS
            or (
                row["subcategory"] != "other"
                and TOPICS[row["subcategory"]]["group"] != row["topic_group"]
            )
        ):
            raise ValueError("Некорректный результат V10")
        digest(row)  # Reject NaN/Infinity before any file is replaced.


def load_results(path=RESULTS):
    path = Path(path)
    if not path.exists():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("format") != "atlas-semantic-results-v10" or not isinstance(
        data.get("channels"), dict
    ):
        raise ValueError("Некорректный кеш результатов V10")
    if data.get("taxonomy_version") != TAXONOMY["version"]:
        raise ValueError("Кеш V10 относится к другой таксономии")
    validate_results(data["channels"])
    return data["channels"]


def publish_results(rows, path=RESULTS):
    validate_results(rows)
    current = load_results(path)
    current.update(rows)
    atomic_write_text(
        path,
        json.dumps(
            dict(
                format="atlas-semantic-results-v10",
                taxonomy_version=TAXONOMY["version"],
                updated_at=datetime.now(timezone.utc).isoformat(),
                channels=current,
            ),
            ensure_ascii=False,
            indent=2,
            allow_nan=False,
        )
        + "\n",
    )
