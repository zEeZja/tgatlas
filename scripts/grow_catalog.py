"""Расширение каталога с приоритетами. Без --apply только просмотр плана."""

import argparse
import json
import logging
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--target",
        type=int,
        default=3000,
        help="целевой общий размер активного каталога",
    )
    parser.add_argument(
        "--state",
        default="reports/catalog-growth/state.json",
        help="контрольная точка для продолжения",
    )
    parser.add_argument(
        "--plan",
        type=Path,
        default=ROOT / "config/catalog_growth.json",
        help="JSON-план источников и тематик",
    )
    parser.add_argument(
        "--apply", action="store_true", help="выполнить сетевой сбор и импорт в БД"
    )
    parser.add_argument(
        "--allow-other",
        action="store_true",
        help="остальные темы только после приоритетного этапа без ошибок",
    )
    parser.add_argument(
        "--workers", type=int, default=4, help="сетевые загрузчики, от 1 до 8"
    )
    parser.add_argument(
        "--max-profiles",
        type=int,
        default=6000,
        help="предел проверок профилей за запуск",
    )
    parser.add_argument(
        "--max-requests",
        type=int,
        default=12000,
        help="предел HTTP-запросов к источникам (не Telegram)",
    )
    parser.add_argument(
        "--max-minutes", type=int, default=180, help="бюджет времени, минут"
    )
    parser.add_argument(
        "--log",
        type=Path,
        help="дополнительный журнал UTF-8; дополняется при продолжении",
    )
    parser.add_argument(
        "--status", action="store_true", help="прочитать состояние, не запускать сбор"
    )
    args = parser.parse_args()
    handlers = [logging.StreamHandler()]
    if args.log:
        args.log.parent.mkdir(parents=True, exist_ok=True)
        handlers.append(logging.FileHandler(args.log, encoding="utf-8"))
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=handlers,
    )
    if args.status:
        path = Path(args.state)
        if not (path if path.is_absolute() else ROOT / path).exists():
            parser.error(
                "Файл состояния ещё не создан: сначала выполните запуск с --apply"
            )
        state = json.loads(
            (path if path.is_absolute() else ROOT / path).read_text(encoding="utf-8")
        )
        print(
            json.dumps(
                {
                    k: state.get(k)
                    for k in (
                        "status",
                        "phase",
                        "initial_count",
                        "current_count",
                        "target",
                        "total_imported",
                        "this_run",
                        "imported_topics",
                        "imported_sources",
                        "updated_at",
                    )
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0
    from src.catalog_growth import run

    result = run(
        target=args.target,
        state_path=args.state,
        plan_path=args.plan,
        apply=args.apply,
        allow_other=args.allow_other,
        workers=args.workers,
        max_profiles=args.max_profiles,
        max_requests=args.max_requests,
        max_minutes=args.max_minutes,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if not args.apply or result["status"] == "target_reached" else 1


if __name__ == "__main__":
    sys.exit(main())
