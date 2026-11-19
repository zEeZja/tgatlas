"""Локальная V10: обучение, пакетная классификация и явная публикация отчёта."""

import argparse
import copy
import json
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

PRESETS = {
    "base": dict(
        name="intfloat/multilingual-e5-base",
        revision="d128750597153bb5987e10b1c3493a34e5a4502a",
        dimension=768,
        pooling="mean",
        prefix="query: ",
    ),
    "small": dict(
        name="intfloat/multilingual-e5-small",
        revision="614241f622f53c4eeff9890bdc4f31cfecc418b3",
        dimension=384,
        pooling="mean",
        prefix="query: ",
    ),
    "tiny": dict(
        name="cointegrated/rubert-tiny2",
        revision="e8ed3b0c8bbf4fb6984c3de043bf7d2f4e5969ae",
        dimension=312,
        pooling="cls",
        prefix="",
    ),
}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--config", type=Path, default=ROOT / "config/classification_v10.json"
    )
    commands = parser.add_subparsers(dest="command", required=True)
    train_parser = commands.add_parser(
        "train", help="обучить индекс и пороги только на train; оценить frozen test"
    )
    train_parser.add_argument("--review", type=Path, required=True)
    train_parser.add_argument("--preset", choices=PRESETS, default=None)
    train_parser.add_argument("--precision", choices=("int8", "fp32"))
    train_parser.add_argument(
        "--post-cache",
        type=Path,
        help="заморозить имеющиеся локальные снимки для обучения; без HTTP",
    )
    classify_parser = commands.add_parser(
        "classify", help="готовый JSON/БД → отчёт; без HTTP к Telegram"
    )
    source = classify_parser.add_mutually_exclusive_group()
    source.add_argument("--input", type=Path, default=ROOT / "web/data/channels.json")
    source.add_argument("--database", action="store_true", help="только чтение БД")
    classify_parser.add_argument("--model", type=Path, required=True)
    classify_parser.add_argument("--limit", type=int, default=100)
    classify_parser.add_argument("--offset", type=int, default=0)
    classify_parser.add_argument("--seconds", type=int, default=600)
    classify_parser.add_argument(
        "--apply",
        action="store_true",
        help="явно опубликовать предложения; подтверждения не меняются",
    )
    classify_parser.add_argument(
        "--post-cache",
        type=Path,
        help="при необходимости читать только свежий локальный кеш постов",
    )
    for command in (train_parser, classify_parser):
        command.add_argument(
            "--output",
            type=Path,
            required=True,
            help="новый файл в models/ или reports/",
        )
        command.add_argument(
            "--cache",
            type=Path,
            default=ROOT / "reports/classification-v10/embeddings.sqlite",
        )
        command.add_argument("--threads", type=int, choices=range(1, 9), default=4)
        command.add_argument("--batch-size", type=int, choices=range(1, 65), default=16)
    args = parser.parse_args()
    from src.classification_v10 import (
        load_policy,
        train,
        load_model,
        classify,
        publish_results,
    )
    from src.classification_encoder import CachedEncoder, peak_memory_mb
    from src.file_io import atomic_write_text

    output = args.output.resolve()
    if not any(output.is_relative_to(ROOT / d) for d in ("models", "reports")):
        parser.error(
            "Вывод должен быть в models/ или reports/; исходные данные не заменяются"
        )
    if (
        output.exists()
        or output == (ROOT / "reports/classification-v10/catalog.json").resolve()
    ):
        parser.error(
            "Используйте новое имя отчёта; catalog.json записывается только через --apply"
        )
    encoder = conn = None
    started = time.perf_counter()
    try:
        policy = copy.deepcopy(load_policy(args.config))
        if args.command == "train":
            policy["encoder"].update(
                PRESETS[args.preset] if args.preset else {},
                precision=args.precision or policy["encoder"]["precision"],
                threads=args.threads,
                batch_size=args.batch_size,
            )
            encoder = CachedEncoder(policy["encoder"], args.cache)
            model = train(args.review, output, encoder, policy, args.post_cache)
            result = {
                k: model[k]
                for k in (
                    "format",
                    "training_examples",
                    "test_examples",
                    "anchor_weight",
                    "calibration",
                    "selection",
                    "evaluation",
                    "benchmark",
                )
            }
        else:
            model = load_model(args.model)
            if (
                not 1 <= args.limit <= model["policy"]["limits"]["batch_channels"]
                or args.offset < 0
                or not 1 <= args.seconds <= 3600
            ):
                parser.error("Некорректный бюджет партии")
            settings = {
                **model["encoder"],
                "threads": args.threads,
                "batch_size": args.batch_size,
            }
            encoder = CachedEncoder(settings, args.cache)
            if args.database:
                from src import db

                conn = db.connect()
                conn.set_session(readonly=True)
                rows = db.get_channels_with_meta(conn)
                conn.commit()
            else:
                from src.classification_review import load_channels

                rows, _ = load_channels(args.input)
            rows = sorted(rows, key=lambda row: row["username"])[
                args.offset : args.offset + args.limit
            ]
            if not rows:
                raise ValueError("Пустая партия")
            provider = None
            if args.post_cache:
                from src.semantic_pipeline import PostProvider

                provider = PostProvider(args.post_cache, allow_http=False)
            predictions, benchmark = classify(
                rows, model, encoder, provider, seconds=args.seconds
            )
            result = dict(
                format="atlas-semantic-results-v10",
                channels=predictions,
                benchmark=benchmark,
                model_sha256=model["artifact_sha256"],
                input_sha256=__import__("hashlib")
                .sha256(
                    json.dumps(
                        rows, sort_keys=True, ensure_ascii=False, default=str
                    ).encode()
                )
                .hexdigest(),
                applied=False,
            )
            if args.apply:
                publish_results(predictions)
                result["applied"] = True
            atomic_write_text(
                output,
                json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False)
                + "\n",
            )
            result = {k: v for k, v in result.items() if k != "channels"} | dict(
                channels=len(predictions),
                review_required=sum(
                    r["topic_review_required"] for r in predictions.values()
                ),
            )
        result["runtime"] = dict(
            wall_seconds=time.perf_counter() - started,
            peak_memory_mb=peak_memory_mb(),
            encoder=encoder.stats,
        )
        atomic_write_text(
            output.with_suffix(".runtime.json"),
            json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        )
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (ValueError, OSError, ImportError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    finally:
        if encoder:
            encoder.close()
        if conn:
            conn.close()


if __name__ == "__main__":
    sys.exit(main())
