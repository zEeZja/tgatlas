"""Optional local TF-IDF/linear baseline. Suggestions never change the working map."""

from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

from .classification_review import (
    read_review,
    feature_text,
    automatic_prediction,
    metrics,
    load_channels,
)
from .file_io import atomic_write_text
from .topic_keywords import TOPICS, GROUPS, TAXONOMY


def _dependencies():
    try:
        import numpy as np
        from sklearn.feature_extraction.text import TfidfVectorizer
        from sklearn.linear_model import LogisticRegression
    except ImportError as exc:
        raise ImportError(
            "Установите requirements/locked-training.txt для локального обучения"
        ) from exc
    return np, TfidfVectorizer, LogisticRegression


def _deduplicate(rows, label_key="expected_topic"):
    unique = {}
    for row in rows:
        key = feature_text(row)
        if key in unique and unique[key][label_key] != row[label_key]:
            raise ValueError(
                "Одинаковые тексты имеют разные основные темы; уточните разметку"
            )
        unique.setdefault(key, row)
    return list(unique.values())


def train(review_path, output_path, threshold=0.8, *, target="topic"):
    if target not in ("topic", "group"):
        raise ValueError("Неизвестная цель обучения")
    label_key = "expected_topic" if target == "topic" else "expected_group"
    unknown = "other" if target == "topic" else "other_group"
    if not 0.5 <= threshold <= 1:
        raise ValueError("Порог должен быть от 0.5 до 1")
    if Path(review_path).resolve() == Path(output_path).resolve():
        raise ValueError("Модель не должна заменять разметку")
    rows = read_review(review_path, dimension="topic")
    train_rows = _deduplicate(
        [r for r in rows if r["split"] == "train" and r[label_key] != unknown], label_key
    )
    test_rows = _deduplicate([r for r in rows if r["split"] == "test"], label_key)
    supports = Counter(r[label_key] for r in train_rows)
    excluded = {key: count for key, count in supports.items() if count < 3}
    train_rows = [r for r in train_rows if supports[r[label_key]] >= 3]
    if (
        len(train_rows) < 40
        or len(test_rows) < 10
        or len({r[label_key] for r in train_rows}) < 2
    ):
        raise ValueError(
            "Нужно не менее 40 уникальных подтверждённых train, 10 test и двух тем минимум с 3 примерами каждая. Непроверенные предложения не обучают модель."
        )
    # Additional guard independent of editable group_id and split columns.
    if {feature_text(r) for r in train_rows} & {feature_text(r) for r in test_rows}:
        raise ValueError("Тексты обучения и проверки пересекаются")
    np, TfidfVectorizer, LogisticRegression = _dependencies()
    vectorizer = TfidfVectorizer(
        analyzer="char_wb", ngram_range=(3, 5), max_features=8000, sublinear_tf=True
    )
    features = vectorizer.fit_transform([feature_text(r) for r in train_rows])
    classifier = LogisticRegression(
        C=2, max_iter=1000, class_weight="balanced", random_state=42
    )
    classifier.fit(features, [r[label_key] for r in train_rows])
    artifact = dict(
        format="atlas-topic-linear-v1",
        taxonomy_version=TAXONOMY["version"],
        classification_version=2,
        active=False,
        target=target,
        created_at=datetime.now(timezone.utc).isoformat(),
        threshold=threshold,
        minimum_margin=0.15,
        classes=classifier.classes_.tolist(),
        vocabulary=vectorizer.vocabulary_,
        idf=vectorizer.idf_.tolist(),
        coef=classifier.coef_.tolist(),
        intercept=classifier.intercept_.tolist(),
        training_examples=len(train_rows),
        test_examples=len(test_rows),
        excluded_training_topics=excluded,
        training_fingerprint=hashlib.sha256(
            "\n".join(
                sorted(r["group_id"] + ":" + r[label_key] for r in train_rows)
            ).encode()
        ).hexdigest(),
    )
    # sklearn vocabulary indices may be numpy integers; JSON is deliberately used
    # instead of pickle/joblib, so loading an artifact cannot execute its code.
    artifact["vocabulary"] = {
        key: int(value) for key, value in artifact["vocabulary"].items()
    }
    suggestions = predict(artifact, test_rows)
    baseline = [automatic_prediction(r) for r in test_rows]
    model_predictions = [
        {
            **base,
            "topic_group": suggestion["suggested_group"] if suggestion["accepted"] else "other_group",
            "subcategory": suggestion["suggested_topic"]
            if suggestion["accepted"]
            else "other",
        }
        for base, suggestion in zip(baseline, suggestions)
    ]
    artifact["evaluation"] = dict(
        baseline=metrics(test_rows, baseline),
        model=metrics(test_rows, model_predictions),
        split="test",
        fields_trained=["primary_topic" if target == "topic" else "topic_group"],
        owner_overrides_used=False,
        warning="Стратифицированная выборка: метрики не равны точности на всём каталоге. Модель не активируется автоматически.",
    )
    atomic_write_text(
        output_path,
        json.dumps(artifact, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
    )
    return {
        key: artifact[key]
        for key in (
            "active",
            "training_examples",
            "test_examples",
            "excluded_training_topics",
            "evaluation",
        )
    }


def predict(model, rows):
    np, TfidfVectorizer, _ = _dependencies()
    if isinstance(model, (str, Path)):
        if Path(model).stat().st_size > 20_000_000:
            raise ValueError("Слишком большой файл модели")
        model = json.loads(Path(model).read_text(encoding="utf-8"))
    if (
        not isinstance(model, dict)
        or not {"classes", "vocabulary", "coef", "intercept", "idf"}.issubset(model)
        or model.get("format") != "atlas-topic-linear-v1"
    ):
        raise ValueError("Неизвестный формат модели")
    if model.get("taxonomy_version") != TAXONOMY["version"]:
        raise ValueError(
            "Версия таксономии модели устарела; переобучите и проверьте её на текущей разметке"
        )
    classes = model.get("classes", [])
    target = model.get("target", "topic")
    if target not in ("topic", "group"):
        raise ValueError("Неизвестная цель модели")
    labels = TOPICS if target == "topic" else GROUPS
    unknown = "other" if target == "topic" else "other_group"
    vocabulary = model.get("vocabulary", {})
    if (
        not isinstance(classes, list)
        or any(not isinstance(c, str) for c in classes)
        or not 2 <= len(classes) <= len(labels)
        or len(set(classes)) != len(classes)
        or any(c not in labels or c == unknown for c in classes)
    ):
        raise ValueError("Некорректные классы модели")
    if (
        not isinstance(vocabulary, dict)
        or not all(
            isinstance(v, int) and not isinstance(v, bool) for v in vocabulary.values()
        )
        or not 1 <= len(vocabulary) <= 8000
        or sorted(vocabulary.values()) != list(range(len(vocabulary)))
        or any(not isinstance(k, str) for k in vocabulary)
    ):
        raise ValueError("Некорректный словарь модели")
    coef = np.asarray(model["coef"], dtype=float)
    intercept = np.asarray(model["intercept"], dtype=float)
    idf = np.asarray(model["idf"], dtype=float)
    n_rows = 1 if len(classes) == 2 else len(classes)
    if (
        coef.shape != (n_rows, len(vocabulary))
        or intercept.shape != (n_rows,)
        or idf.shape != (len(vocabulary),)
        or not all(np.isfinite(x).all() for x in (coef, intercept, idf))
        or (idf <= 0).any()
    ):
        raise ValueError("Некорректные веса модели")
    threshold = model.get("threshold", 0.8)
    margin = model.get("minimum_margin", 0.15)
    if (
        not all(
            isinstance(x, (int, float)) and not isinstance(x, bool)
            for x in (threshold, margin)
        )
        or not 0.5 <= threshold <= 1
        or not 0 <= margin <= 1
    ):
        raise ValueError("Некорректный порог модели")
    if not rows:
        return []
    vectorizer = TfidfVectorizer(
        analyzer="char_wb", ngram_range=(3, 5), sublinear_tf=True, vocabulary=vocabulary
    )
    # Initialize sklearn's transformer with a fixed vocabulary, then restore IDF.
    # No evaluation text is fitted or used to choose features.
    vectorizer.fit([""])
    vectorizer.idf_ = idf
    scores = vectorizer.transform([feature_text(r) for r in rows]) @ coef.T + intercept
    if len(classes) == 2:
        positive = 1 / (1 + np.exp(-np.clip(scores[:, 0], -700, 700)))
        probabilities = np.column_stack((1 - positive, positive))
    else:
        probabilities = np.exp(scores - scores.max(axis=1, keepdims=True))
        probabilities /= probabilities.sum(axis=1, keepdims=True)
    result = []
    for row, values in zip(rows, probabilities):
        ranked = np.argsort(values)[::-1]
        top = int(ranked[0])
        score = float(values[top])
        gap = score - float(values[int(ranked[1])])
        result.append(
            dict(
                username=row.get("username"),
                suggested_topic=classes[top] if target == "topic" else "other",
                suggested_group=TOPICS[classes[top]]["group"] if target == "topic" else classes[top],
                granularity=target,
                model_score=score,
                margin=gap,
                accepted=score >= threshold and gap >= margin,
                reviewed=False,
            )
        )
    return result


def suggest(model_path, input_path, output_path):
    if Path(output_path).resolve() in (
        Path(model_path).resolve(),
        Path(input_path).resolve(),
    ):
        raise ValueError("Предложения не должны заменять модель или каталог")
    rows, _ = load_channels(input_path)
    predictions = predict(model_path, rows)
    atomic_write_text(
        output_path,
        json.dumps(
            dict(active=False, suggestions=predictions),
            ensure_ascii=False,
            indent=2,
            allow_nan=False,
        )
        + "\n",
    )
    return dict(
        channels=len(rows),
        above_threshold=sum(p["accepted"] for p in predictions),
        applied=0,
        output=str(output_path),
    )
