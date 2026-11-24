"""11v2: локальная классификация, просмотр и явная публикация, без Telegram."""

import argparse
import json
import os
from pathlib import Path
import random
import re
import sys

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    build = commands.add_parser("classify")
    build.add_argument("--input", type=Path, default=ROOT / "web/data/channels.json")
    build.add_argument(
        "--post-cache",
        type=Path,
        default=ROOT / "reports/classification-v11/catalog-run/posts",
    )
    build.add_argument(
        "--output", type=Path, required=True, help="новая папка результатов"
    )
    build.add_argument("--limit", type=int)
    build.add_argument(
        "--include-development",
        action="store_true",
        help="включить примеры владельца в пилот; это НЕ независимый тест",
    )
    build.add_argument("--seconds", type=int, default=3600)
    build.add_argument(
        "--frozen-evidence",
        type=Path,
        help="evidence.json прежнего опыта; нужен его snapshot.json, без --limit",
    )
    view = commands.add_parser("view")
    view.add_argument("--run", type=Path, required=True)
    evaluate = commands.add_parser("evaluate")
    evaluate.add_argument("--run", type=Path, required=True)
    evaluate.add_argument("--labels", type=Path, required=True)
    publish = commands.add_parser(
        "publish", help="проверить полный результат; записать только с --apply"
    )
    publish.add_argument("--run", type=Path, required=True)
    publish.add_argument("--apply", action="store_true")
    publish.add_argument(
        "--allow-partial",
        action="store_true",
        help="явно разрешить неполный каталог/пилот",
    )
    export = commands.add_parser(
        "export", help="экспортировать опубликованную 11v2 с резервными копиями"
    )
    export.add_argument("--input", type=Path, default=ROOT / "web/data/channels.json")
    export.add_argument("--output", type=Path)
    export.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    os.environ.setdefault("HF_HUB_OFFLINE", "1")
    os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")
    try:
        from src.classification_runtime import fingerprint, policy
        from src.file_io import atomic_write_text

        if args.command == "publish":
            from src.classification_v11v2_runtime import publish_run

            print(
                json.dumps(
                    publish_run(
                        args.run, apply=args.apply, allow_partial=args.allow_partial
                    ),
                    ensure_ascii=False,
                )
            )
            return 0
        if args.command == "export":
            from src.classification_v11v2_runtime import load_results
            from src.primary_export import export_current

            if not load_results():
                raise ValueError(
                    "Сначала опубликуйте результат 11v2 командой publish --apply"
                )
            print(
                json.dumps(
                    export_current(
                        args.input,
                        apply=args.apply,
                        output=args.output
                        or ROOT / "reports/classification-v11v2/map-preview.json",
                        backup_root=ROOT
                        / "reports/classification-v11v2/export-backups",
                    ),
                    ensure_ascii=False,
                )
            )
            return 0

        from src.classification_v11v2 import classify, evaluate, render_view, settings

        if args.command == "classify":
            if args.output.exists():
                raise ValueError(
                    "Нужна новая папка: существующий эксперимент не заменяется"
                )
            if not 1 <= args.seconds <= 28800:
                raise ValueError("Лимит времени: 1–28800 секунд")
            data = json.loads(args.input.read_text(encoding="utf-8"))
            rows = data["channels"] if isinstance(data, dict) else data
            if not isinstance(rows, list) or not 1 <= len(rows) <= 6000:
                raise ValueError("Нужен каталог от 1 до 6000 каналов")
            if any(
                not isinstance(r, dict)
                or not re.fullmatch(
                    r"[a-z][a-z0-9_]{0,31}", str(r.get("username") or "")
                )
                for r in rows
            ):
                raise ValueError("Некорректный username")
            if len({r["username"] for r in rows}) != len(rows):
                raise ValueError("Повторный username")
            if args.frozen_evidence and (
                args.limit is not None or args.include_development
            ):
                raise ValueError(
                    "Замороженные посты используются с полным исходным snapshot.json, без выборки"
                )
            if args.frozen_evidence:
                evidence = json.loads(args.frozen_evidence.read_text(encoding="utf-8"))
                if not isinstance(evidence, list) or len(evidence) != len(rows):
                    raise ValueError("Посты не соответствуют снимку")
                pairs = sorted(
                    zip(rows, evidence), key=lambda pair: pair[0]["username"]
                )
                rows = [pair[0] for pair in pairs]
                evidence = [pair[1] for pair in pairs]
            else:
                rows = sorted(rows, key=lambda r: r["username"])
            if args.limit is not None:
                if not 1 <= args.limit <= len(rows):
                    raise ValueError("Недопустимый размер пилота")
                development = json.loads(
                    (ROOT / "config/classification_v11v2_development.json").read_text(
                        encoding="utf-8"
                    )
                )
                mandatory = (
                    {c["username"] for c in development["cases"]}
                    if args.include_development
                    else set()
                )
                chosen = [r for r in rows if r["username"] in mandatory]
                if len(chosen) > args.limit:
                    raise ValueError("Пилот меньше обязательного набора")
                others = [r for r in rows if r["username"] not in mandatory]
                random.Random(settings()["seed"]).shuffle(others)
                rows = sorted(
                    chosen + others[: args.limit - len(chosen)],
                    key=lambda r: r["username"],
                )
            if not args.frozen_evidence:
                evidence = []
            for index, row in enumerate(rows):
                file = args.post_cache / (row["username"] + ".json")
                sample = (
                    evidence[index]
                    if args.frozen_evidence
                    else json.loads(file.read_text(encoding="utf-8"))
                    if file.exists()
                    else {}
                )
                if sample and (
                    sample.get("username") != row["username"]
                    or sample.get("purpose") != "classification_only"
                ):
                    raise ValueError("Кеш постов не соответствует каналу")
                if not args.frozen_evidence:
                    evidence.append(sample)
            args.output.mkdir(parents=True)
            for name, value in [
                ("snapshot.json", rows),
                ("evidence.json", evidence),
                ("policy.json", settings()),
            ]:
                atomic_write_text(
                    args.output / name,
                    json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False)
                    + "\n",
                )
            from src.classification_encoder import CachedEncoder, peak_memory_mb

            encoder = CachedEncoder(
                policy()["encoder"],
                ROOT / "reports/classification-v11/primary-embeddings.sqlite",
            )
            try:
                result = classify(rows, evidence, encoder, seconds=args.seconds)
                result["benchmark"]["peak_memory_mb"] = peak_memory_mb()
            finally:
                encoder.close()
            result["evidence_sha256"] = fingerprint(evidence)
            atomic_write_text(
                args.output / "results.json",
                json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False)
                + "\n",
            )
            atomic_write_text(args.output / "review.html", render_view(rows, result))
            print(
                json.dumps(
                    {k: v for k, v in result.items() if k != "channels"},
                    ensure_ascii=False,
                    indent=2,
                )
            )
            print(args.output / "review.html")
            return 0 if not result["remaining"] else 2
        result = json.loads((args.run / "results.json").read_text(encoding="utf-8"))
        if result.get("format") != "atlas-results-v11v2":
            raise ValueError("Это не результат 11v2")
        if args.command == "view":
            rows = json.loads((args.run / "snapshot.json").read_text(encoding="utf-8"))
            if fingerprint(rows) != result["input_sha256"]:
                raise ValueError("Несовместимый снимок")
            atomic_write_text(args.run / "review.html", render_view(rows, result))
            print(args.run / "review.html")
        else:
            labels = json.loads(args.labels.read_text(encoding="utf-8"))
            print(json.dumps(evaluate(result, labels), ensure_ascii=False, indent=2))
        return 0
    except (ValueError, OSError, KeyError, ImportError, TypeError) as exc:
        print(str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
