"""Read-only zero-shot benchmark on real channel profiles; no Telegram requests."""

import argparse
from collections import Counter, defaultdict
import csv
from datetime import datetime, timezone
import hashlib
import io
from importlib.metadata import PackageNotFoundError, version
import json
from pathlib import Path
import random
import sys

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from src.classification_review import load_channels  # noqa: E402
from src.classification_text import informative_description  # noqa: E402
from src.file_io import atomic_write_text  # noqa: E402
from src.topic_keywords import TAXONOMY, load_classification_rules, resolve_topic  # noqa: E402

MODEL = "MoritzLaurer/mDeBERTa-v3-base-mnli-xnli"


def label_map(path=None):
    topics = [t for t in TAXONOMY["topics"] if t["id"] != "other"]
    source = Path(path) if path else ROOT / "config/zero_shot_labels.json"
    result = json.loads(source.read_text(encoding="utf-8-sig"))
    if not isinstance(result, dict) or set(result) != {t["id"] for t in topics}:
        raise ValueError("Файл меток должен содержать все ID текущих тем, кроме other")
    if any(not isinstance(v, str) or not v.strip() for v in result.values()) or len(
        set(result.values())
    ) != len(result):
        raise ValueError("Описания тематик должны быть непустыми и уникальными")
    return result


def select_channels(rows, limit, seed, rules):
    # Fixed, deterministic round-robin by existing topic: cover small classes too.
    buckets = defaultdict(list)
    for row in sorted(rows, key=lambda r: r["username"]):
        if not informative_description(row.get("description")):
            continue
        current = resolve_topic(row, **rules)
        buckets[current["subcategory"]].append((row, current))
    rng = random.Random(seed)
    for bucket in buckets.values():
        rng.shuffle(bucket)
    selected = []
    keys = sorted(buckets)
    while len(selected) < limit and any(buckets.values()):
        for key in keys:
            if buckets[key] and len(selected) < limit:
                selected.append(buckets[key].pop())
    if not selected:
        raise ValueError("Нет каналов с содержательным описанием")
    return selected


def load_source(args):
    if args.database:
        from src import db

        conn = db.connect()
        try:
            conn.set_session(readonly=True)
            raw = db.get_channels_with_meta(conn)
            # Return only fields required for classification; no DB credentials/dates.
            keys = (
                "username",
                "name",
                "description",
                "category",
                "subcategory",
                "region",
                "region_hint",
                "country",
                "channel_type",
                "region_review_required",
            )
            rows = [{k: r.get(k) for k in keys} for r in raw]
            return rows, "database:active_channels"
        finally:
            conn.close()
    rows, _ = load_channels(args.input)
    return rows, str(Path(args.input).resolve())


def save_report(base, manifest, results):
    atomic_write_text(
        str(base) + ".json",
        json.dumps(
            dict(manifest=manifest, results=results), ensure_ascii=False, indent=2
        )
        + "\n",
    )
    fields = [
        "username",
        "name",
        "description",
        "model_text",
        "current_topic",
        "current_source",
        "current_review_required",
        "model_topic",
        "model_score",
        "runner_up",
        "runner_up_score",
        "margin",
        "disagrees",
        "expected_topic",
        "reviewed",
    ]
    stream = io.StringIO(newline="")
    writer = csv.DictWriter(
        stream, fields, delimiter=";", lineterminator="\n", extrasaction="ignore"
    )
    writer.writeheader()
    for row in results:
        # Prevent spreadsheet interpretation of user-provided profile text.
        writer.writerow(
            {
                k: (
                    "'" + str(row.get(k, ""))
                    if str(row.get(k, "")).lstrip().startswith(("=", "+", "-", "@"))
                    else row.get(k, "")
                )
                for k in fields
            }
        )
    atomic_write_text(str(base) + ".csv", "\ufeff" + stream.getvalue())
    summary = dict(
        completed=len(results),
        selected=len(manifest["sample"]),
        disagreements=sum(r["disagrees"] for r in results),
        model_topics=dict(Counter(r["model_topic"] for r in results)),
        note="Расхождение с правилами не является ошибкой модели. Нужна независимая ручная проверка; score не является измеренной вероятностью правильности.",
    )
    atomic_write_text(
        str(base) + ".summary.json",
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
    )


def prepare_model(classifier):
    """Pipeline loaders may choose checkpoint FP16 despite requested dtype.

    Cast the actual loaded module and check every floating parameter before
    inference. This is deliberately independent of Transformers dtype kwargs.
    """
    model = classifier.model
    model.float()
    model.eval()
    dtypes = {str(p.dtype) for p in model.parameters() if p.is_floating_point()}
    if dtypes != {"torch.float32"}:
        raise ValueError(
            f"Не удалось включить float32: фактические типы {sorted(dtypes)}"
        )
    id2label = {str(k): str(v) for k, v in model.config.id2label.items()}
    entailment_id = getattr(classifier, "entailment_id", None)
    if entailment_id is not None:
        if not id2label.get(str(entailment_id), "").lower().startswith("entail"):
            raise ValueError(
                "Pipeline не распознал класс entailment; проверьте конфигурацию NLI"
            )
    versions = {}
    for package in ("torch", "transformers", "tokenizers", "sentencepiece"):
        try:
            versions[package] = version(package)
        except PackageNotFoundError:
            versions[package] = None
    return dict(
        actual_dtype="torch.float32",
        id2label=id2label,
        entailment_id=entailment_id,
        package_versions=versions,
        python_version=sys.version,
    )


def run(args, factory=None):
    rows, source = load_source(args)
    rules = load_classification_rules()
    chosen = select_channels(rows, args.limit, args.seed, rules)
    labels = label_map(args.labels)
    ids = list(labels)
    candidates = list(labels.values())
    reverse = {value: key for key, value in labels.items()}
    sample = []
    for row, current in chosen:
        text = (
            (row.get("name") or row["username"])
            + ". "
            + informative_description(row.get("description"))
        )[: args.max_chars]
        sample.append(
            dict(
                username=row["username"],
                name=row.get("name") or row["username"],
                description=row.get("description") or "",
                model_text=text,
                current_topic=current["subcategory"],
                current_source=current["topic_source"],
                current_review_required=current["topic_review_required"],
            )
        )
    settings = dict(
        experiment_version=3,
        requested_dtype="float32",
        device=args.device,
        model=args.model,
        revision=args.revision,
        taxonomy_version=TAXONOMY["version"],
        labels=labels,
        hypothesis=args.hypothesis,
        mode="single_label",
        seed=args.seed,
        max_chars=args.max_chars,
        sample=sample,
    )
    fingerprint = hashlib.sha256(
        json.dumps(settings, sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()
    base = Path(args.output)
    if not base.is_absolute():
        base = ROOT / base
    # Keep report paths away from catalogue/config files.
    for suffix in (".json", ".csv", ".summary.json"):
        target = Path(str(base) + suffix).resolve()
        if (
            target.is_relative_to((ROOT / "config").resolve())
            or target.is_relative_to((ROOT / "web").resolve())
            or (args.input and target == Path(args.input).resolve())
        ):
            raise ValueError("Отчёт не должен заменять конфигурацию или каталог")
    results = []
    manifest = dict(
        **settings,
        fingerprint=fingerprint,
        source=source,
        created_at=datetime.now(timezone.utc).isoformat(),
        score_note="Relative NLI scores among the supplied labels; not calibrated correctness probabilities.",
    )
    report = Path(str(base) + ".json")
    if report.exists():
        if not args.resume:
            raise ValueError(
                "Отчёт уже существует: используйте --resume или другой --output"
            )
        previous = json.loads(report.read_text(encoding="utf-8"))
        if previous["manifest"]["fingerprint"] != fingerprint:
            raise ValueError(
                "Выборка, правила или параметры изменились: используйте другой --output"
            )
        results, manifest = previous["results"], previous["manifest"]
        if [r["username"] for r in results] != [
            r["username"] for r in sample[: len(results)]
        ] or len(results) > len(sample):
            raise ValueError("Повреждена контрольная точка")
    elif any(Path(str(base) + s).exists() for s in (".csv", ".summary.json")):
        raise ValueError("Найдены отчёты с этим именем: используйте другой --output")
    print(
        f"Выбрано {len(sample)} из {len(rows)} каналов; тематик {len(ids)}; завершено {len(results)}",
        flush=True,
    )
    if len(results) == len(sample):
        return
    if factory is None:
        try:
            from transformers import pipeline
        except ImportError as exc:
            raise ValueError(
                "Установите зависимости: pip install -r requirements-zero-shot.txt"
            ) from exc
        factory = pipeline
    kwargs = dict(model=args.model, device=args.device, trust_remote_code=False)
    if args.revision:
        kwargs["revision"] = args.revision
    classifier = factory("zero-shot-classification", **kwargs)
    runtime = prepare_model(classifier)
    manifest["runtime"] = runtime
    print("Фактический тип весов:", runtime["actual_dtype"], flush=True)
    print("NLI-классы:", runtime["id2label"], flush=True)
    resolved_revision = getattr(
        getattr(classifier.model, "config", None), "_commit_hash", None
    )
    if (
        args.resume
        and manifest.get("resolved_revision")
        and resolved_revision != manifest["resolved_revision"]
    ):
        raise ValueError(
            "Изменилась ревизия модели: укажите прежнюю --revision и другой --output"
        )
    manifest["resolved_revision"] = resolved_revision
    save_report(base, manifest, results)
    for row in sample[len(results) :]:
        result = classifier(
            row["model_text"],
            candidate_labels=candidates,
            hypothesis_template=args.hypothesis,
            multi_label=False,
            batch_size=args.batch_size,
        )
        ranked = [
            dict(topic=reverse[label], score=float(score))
            for label, score in zip(result["labels"], result["scores"])
        ]
        if len(ranked) != len(ids) or {r["topic"] for r in ranked} != set(ids):
            raise ValueError("Модель вернула неполный список тематик")
        top, second = ranked[:2]
        results.append(
            dict(
                **row,
                model_topic=top["topic"],
                model_score=top["score"],
                runner_up=second["topic"],
                runner_up_score=second["score"],
                margin=top["score"] - second["score"],
                disagrees=top["topic"] != row["current_topic"],
                top_candidates=ranked[:5],
                expected_topic="",
                reviewed="no",
            )
        )
        save_report(base, manifest, results)
        print(
            f"[{len(results)}/{len(sample)}] @{row['username']}: {row['current_topic']} → {top['topic']} ({top['score']:.3f})",
            flush=True,
        )
    print(f"Готово: {base}.csv, .json, .summary.json", flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group()
    source.add_argument("--input", default=str(ROOT / "web/data/channels.json"))
    source.add_argument(
        "--database", action="store_true", help="активные каналы из БД .env, read-only"
    )
    parser.add_argument("--limit", type=int, default=100)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--model", default=MODEL)
    parser.add_argument("--revision", help="commit модели для воспроизводимости")
    parser.add_argument("--device", default="cpu", help="cpu или cuda:0")
    parser.add_argument("--batch-size", type=int, default=4)
    parser.add_argument("--max-chars", type=int, default=2000)
    parser.add_argument(
        "--labels", help="JSON: ID темы → описание; все темы кроме other"
    )
    parser.add_argument("--hypothesis", default="Основная тема этого канала — {}.")
    parser.add_argument(
        "--output", default="reports/zero-shot-100", help="путь без расширения"
    )
    parser.add_argument("--resume", action="store_true")
    args = parser.parse_args()
    if (
        args.limit < 1
        or args.batch_size < 1
        or args.max_chars < 50
        or args.hypothesis.count("{}") != 1
    ):
        parser.error(
            "Нужны положительные limit/batch-size, max-chars ≥ 50 и один {} в шаблоне"
        )
    try:
        run(args)
        return 0
    except KeyboardInterrupt:
        print("Прервано; сохранённые результаты доступны для --resume", file=sys.stderr)
        return 130
    except (ValueError, OSError, ImportError) as exc:
        print(str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
