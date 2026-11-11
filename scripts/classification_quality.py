"""Prepare, review, measure, apply and train using explicit owner-confirmed labels."""

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def main():
    from src import classification_review as review

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
    evaluate = commands.add_parser(
        "evaluate",
        help="измерить автоматические правила на подтверждённых метках без ручных overrides",
    )
    evaluate.add_argument("--review", required=True)
    evaluate.add_argument("--split", choices=["train", "test", "all"], default="test")
    evaluate.add_argument("--output")
    apply = commands.add_parser(
        "apply", help="предпросмотр или сохранение подтверждённых исправлений владельца"
    )
    apply.add_argument("--review", required=True)
    apply.add_argument("--apply", action="store_true")
    train = commands.add_parser(
        "train",
        help="обучить локальную модель тем на подтверждённом train, оценить на test",
    )
    train.add_argument("--review", required=True)
    train.add_argument("--output", default="models/topic-model.json")
    train.add_argument("--threshold", type=float, default=0.8)
    suggest = commands.add_parser(
        "suggest", help="получить предложения модели, не меняя рабочие данные"
    )
    suggest.add_argument("--model", required=True)
    suggest.add_argument("--input", default="web/data/channels.json")
    suggest.add_argument("--output", default="reports/model-suggestions.json")
    args = parser.parse_args()
    try:
        if args.command == "prepare":
            result = review.prepare(args.input, args.output, args.size, args.seed)
        elif args.command == "evaluate":
            result = review.evaluate(args.review, args.split)
            if args.output:
                from src.file_io import atomic_write_text

                if Path(args.output).resolve() == Path(args.review).resolve():
                    raise ValueError("Отчёт не должен заменять разметку")
                atomic_write_text(
                    args.output, json.dumps(result, ensure_ascii=False, indent=2) + "\n"
                )
        elif args.command == "apply":
            result = review.apply_review(args.review, args.apply)
        elif args.command == "train":
            from src.topic_model import train

            result = train(args.review, args.output, args.threshold)
        else:
            from src.topic_model import suggest

            result = suggest(args.model, args.input, args.output)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (ValueError, OSError, ImportError) as exc:
        print(str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
