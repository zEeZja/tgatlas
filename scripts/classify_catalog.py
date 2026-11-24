"""V11: локальные посты → явный сбор → классификация → явная публикация."""

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    collect = commands.add_parser(
        "collect", help="по умолчанию только БД/локальный кеш, без Telegram"
    )
    collect.add_argument("--input", type=Path, default=ROOT / "web/data/channels.json")
    transport = collect.add_mutually_exclusive_group()
    transport.add_argument(
        "--http",
        action="store_true",
        help="явно разрешить запросы Telegram в пределах лимитов",
    )
    transport.add_argument(
        "--rsshub-url", help="явно разрешить RSSHub, например http://127.0.0.1:1200"
    )
    collect.add_argument(
        "--switch-to-rsshub",
        action="store_true",
        help="переключить существующий --run без сброса постов, дедлайна и счётчиков",
    )
    collect.add_argument(
        "--resume-rsshub",
        action="store_true",
        help="после восстановления RSSHub явно продолжить тот же запуск; Retry-After и счётчики сохраняются",
    )
    collect.add_argument(
        "--change-rsshub-url",
        action="store_true",
        help="явно сменить недоступный RSSHub без сброса лимитов; требует --resume-rsshub, не снимает 403/429",
    )
    collect.add_argument("--no-database", action="store_true")
    collect.add_argument("--posts", type=int, default=8)
    collect.add_argument("--requests", type=int, default=3000)
    collect.add_argument("--hours", type=int, default=8)
    collect.add_argument(
        "--reuse-posts",
        type=Path,
        action="append",
        default=[],
        help="дополнительный готовый кеш постов; старый запуск не меняется",
    )
    collect.add_argument(
        "--extend-hours",
        type=int,
        help="явно продлить истёкший срок на 1–8 часов без сброса запросов; не снимает 403/429",
    )
    classify = commands.add_parser(
        "classify", help="только локальная CPU-модель; исходные выгрузки не заменяет"
    )
    classify.add_argument("--seconds", type=int, default=3600)
    classify.add_argument("--output", type=Path, required=True)
    publish = commands.add_parser(
        "publish", help="опубликовать предложения для основного экспорта/API"
    )
    publish.add_argument("--results", type=Path, required=True)
    publish.add_argument("--apply", action="store_true")
    publish.add_argument(
        "--allow-partial", action="store_true", help="явно разрешить неполный результат"
    )
    status = commands.add_parser(
        "status", help="состояние локального запуска, без HTTP и моделей"
    )
    view = commands.add_parser(
        "view", help="автономный HTML с темами, постами и географией до публикации"
    )
    view.add_argument("--results", type=Path, required=True)
    view.add_argument("--output", type=Path, required=True)
    export = commands.add_parser(
        "export",
        help="применить готовый кеш к текущей карте, сохранив весь JSON-каталог и резервные копии",
    )
    export.add_argument("--input", type=Path, default=ROOT / "web/data/channels.json")
    export.add_argument("--output", type=Path)
    export.add_argument("--apply", action="store_true")
    for command in (collect, classify, status, view):
        command.add_argument(
            "--run", type=Path, default=ROOT / "reports/classification-v11/catalog-run"
        )
    args = parser.parse_args()
    from src.file_io import atomic_write_text

    try:
        if args.command == "collect":
            from src.classification_review import load_channels
            from src.catalog_collection import collect as run_collection
            import requests
            from src.config import USER_AGENT

            snapshot = args.run / "snapshot.json"
            if snapshot.exists():
                rows = json.loads(snapshot.read_text(encoding="utf-8"))
            else:
                rows, _ = load_channels(args.input)
                rows = sorted(rows, key=lambda r: r["username"])
            with requests.Session() as session:
                session.headers.update(
                    {"User-Agent": USER_AGENT, "Accept-Language": "ru,en;q=0.8"}
                )
                result = run_collection(
                    rows,
                    args.run,
                    http=args.http,
                    database=not args.no_database,
                    maximum=args.posts,
                    request_budget=args.requests,
                    hours=args.hours,
                    extend_hours=args.extend_hours,
                    rsshub_url=args.rsshub_url,
                    switch_to_rsshub=args.switch_to_rsshub,
                    resume_rsshub=args.resume_rsshub,
                    change_rsshub=args.change_rsshub_url,
                    session=session,
                    cache_directories=[
                        *args.reuse_posts,
                        ROOT / "reports/classification-v11/pilot-120/posts",
                        ROOT / "reports/classification-samples",
                    ],
                )
            print(
                json.dumps(
                    {
                        k: v
                        for k, v in result.items()
                        if k not in ("channels", "requests_by_channel")
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            )
        elif args.command == "classify":
            if args.output.exists() or not 1 <= args.seconds <= 28800:
                raise ValueError("Нужно новое имя отчёта и лимит 1–28800 секунд")
            from src.classification_runtime import policy
            from src.classification_encoder import CachedEncoder, peak_memory_mb
            from src.classifier_v11 import classify as run_classifier

            rows = json.loads((args.run / "snapshot.json").read_text(encoding="utf-8"))
            encoder = CachedEncoder(
                policy()["encoder"],
                ROOT / "reports/classification-v11/primary-embeddings.sqlite",
            )
            try:
                result = run_classifier(rows, args.run, encoder, seconds=args.seconds)
                result["benchmark"]["peak_memory_mb"] = peak_memory_mb()
                atomic_write_text(
                    args.output,
                    json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False)
                    + "\n",
                )
            finally:
                encoder.close()
            print(
                json.dumps(
                    {k: v for k, v in result.items() if k != "channels"},
                    ensure_ascii=False,
                    indent=2,
                )
            )
        elif args.command == "status":
            from collections import Counter

            state = json.loads(
                (args.run / "collection.json").read_text(encoding="utf-8")
            )
            print(
                json.dumps(
                    dict(
                        channels=dict(
                            Counter(x["status"] for x in state["channels"].values())
                        ),
                        requests=state["requests"],
                        request_budget=state["request_budget"],
                        stopped=state["stopped"],
                        stop_reason=state.get("stop_reason"),
                        transport=state.get("transport", "telegram"),
                        rsshub_url=state.get("rsshub_url"),
                        retry_not_before=state.get("retry_not_before"),
                    ),
                    ensure_ascii=False,
                    indent=2,
                )
            )
        elif args.command == "view":
            from src.primary_review import render_view

            if args.output.exists() or args.output.suffix != ".html":
                raise ValueError("Нужно новое имя HTML")
            snapshot = json.loads(
                (args.run / "snapshot.json").read_text(encoding="utf-8")
            )
            result = json.loads(args.results.read_text(encoding="utf-8"))
            atomic_write_text(args.output, render_view(snapshot, result))
            print(args.output.resolve())
        elif args.command == "export":
            from src.primary_export import export_current

            print(
                json.dumps(
                    export_current(args.input, apply=args.apply, output=args.output),
                    ensure_ascii=False,
                    indent=2,
                )
            )
        else:
            from src.classification_runtime import validate_results, publish_results

            result = json.loads(args.results.read_text(encoding="utf-8"))
            validate_results(result)
            if result.get("remaining", 0) and not args.allow_partial:
                raise ValueError(
                    "Неполный результат: используйте --allow-partial только осознанно"
                )
            if args.apply:
                publish_results(result)
            print(
                f"channels={len(result['channels'])}; applied={args.apply}; scores are not probabilities"
            )
    except (ValueError, OSError, KeyError, ImportError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
