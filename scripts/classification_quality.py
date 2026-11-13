"""Prepare, review, measure, apply and train using explicit owner-confirmed labels."""

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def main():
    from src import classification_review as review
    from src.scraper import ScrapeError

    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    prepare = commands.add_parser(
        "prepare",
        help="создать CSV и автономную HTML-форму; метки пока не подтверждены",
    )
    prepare.add_argument("--input", default="web/data/channels.json")
    prepare.add_argument("--output", default="reports/classification-review.csv")
    prepare.add_argument("--size", type=int, default=200)
    prepare.add_argument("--seed", type=int, default=42)
    prepare.add_argument("--focus-groups", help="крупные группы через запятую для целевой разметки")
    prepare.add_argument("--exclude-review", help="CSV с уже подтверждёнными темами, которые нужно исключить")
    evaluate = commands.add_parser(
        "evaluate",
        help="измерить автоматические правила на подтверждённых метках без ручных overrides",
    )
    evaluate.add_argument("--review", required=True)
    evaluate.add_argument("--split", choices=["train", "test", "all"], default="test")
    evaluate.add_argument("--output")
    evaluate.add_argument("--dimension", choices=["all", "topic", "geography"], default="all")
    inspect = commands.add_parser("inspect", help="проверить все ответы и показать ошибки без изменения разметки")
    inspect.add_argument("--review", required=True)
    inspect.add_argument("--output")
    apply = commands.add_parser(
        "apply", help="предпросмотр или сохранение подтверждённых исправлений владельца"
    )
    apply.add_argument("--review", required=True)
    apply.add_argument("--apply", action="store_true")
    apply.add_argument("--dimension", choices=["all", "topic", "geography"], default="all")
    train = commands.add_parser(
        "train",
        help="обучить локальную модель тем на подтверждённом train, оценить на test",
    )
    train.add_argument("--review", required=True)
    train.add_argument("--output", default="models/topic-model.json")
    train.add_argument("--threshold", type=float, default=0.8)
    train.add_argument("--target", choices=["topic", "group"], default="topic")
    suggest = commands.add_parser(
        "suggest", help="получить предложения модели, не меняя рабочие данные"
    )
    suggest.add_argument("--model", required=True)
    suggest.add_argument("--input", default="web/data/channels.json")
    suggest.add_argument("--output", default="reports/model-suggestions.json")
    samples = commands.add_parser(
        "sample-posts",
        help="ограниченная HTTP-выборка сообщений в отдельный кеш без изменения БД",
    )
    samples.add_argument("--channel", required=True)
    samples.add_argument("--cache-dir", default="reports/classification-samples")
    samples.add_argument("--pages", type=int, choices=range(1, 4), default=2)
    samples.add_argument("--limit", type=int, choices=range(3, 21), default=12)
    samples.add_argument("--refresh", action="store_true")
    samples.add_argument("--source", choices=["database", "http"], default="database",
                         help="сначала локальные посты; HTTP требует явного выбора")
    samples.add_argument("--days", type=int, default=30, help="период локальных постов, 1–90 дней")
    compare = commands.add_parser(
        "compare-posts",
        help="оценить предложения из кеша на подтверждённом test; ничего не применять",
    )
    compare.add_argument("--review", required=True)
    compare.add_argument("--cache-dir", default="reports/classification-samples")
    compare.add_argument("--output")
    args = parser.parse_args()
    try:
        if args.command == "prepare":
            result = review.prepare(args.input, args.output, args.size, args.seed,
                                    focus_groups=[x.strip() for x in (args.focus_groups or "").split(",") if x.strip()],
                                    exclude_review=args.exclude_review)
        elif args.command in ("evaluate", "inspect"):
            result = review.inspect_review(args.review) if args.command == "inspect" else review.evaluate(args.review, args.split, args.dimension)
            if args.output:
                from src.file_io import atomic_write_text

                if Path(args.output).resolve() == Path(args.review).resolve():
                    raise ValueError("Отчёт не должен заменять разметку")
                atomic_write_text(
                    args.output, json.dumps(result, ensure_ascii=False, indent=2) + "\n"
                )
        elif args.command == "apply":
            result = review.apply_review(args.review, args.apply, dimension=args.dimension)
        elif args.command == "train":
            from src.topic_model import train

            result = train(args.review, args.output, args.threshold, target=args.target)
        elif args.command == "sample-posts":
            from src.classification_samples import sample_channel, sample_from_database, evidence

            if args.source == "database":
                from src import db
                conn = db.connect()
                try:
                    sample = sample_from_database(args.channel, conn, args.cache_dir, args.limit, args.days)
                finally:
                    conn.close()
            else:
                sample = sample_channel(args.channel, args.cache_dir, args.pages, args.limit, refresh=args.refresh)
            result = {
                k: sample[k]
                for k in (
                    "username",
                    "fetched_at",
                    "pages_requested" if args.source == "http" else "rows_examined",
                    "distinct_dates",
                    "low_diversity",
                    "cache_hit",
                )
            }
            result["evidence"] = evidence(sample)
        elif args.command == "compare-posts":
            from src.classification_samples import compare

            result = compare(args.review, args.cache_dir)
            if args.output:
                from src.file_io import atomic_write_text

                if Path(args.output).resolve() == Path(args.review).resolve():
                    raise ValueError("Отчёт не должен заменять разметку")
                atomic_write_text(
                    args.output, json.dumps(result, ensure_ascii=False, indent=2) + "\n"
                )
        else:
            from src.topic_model import suggest

            result = suggest(args.model, args.input, args.output)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (ValueError, OSError, ImportError, ScrapeError) as exc:
        print(str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
