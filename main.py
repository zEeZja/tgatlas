#!/usr/bin/env python3
"""CLI Telegram Channel Atlas.

Рабочие команды каталога, классификации и карт; отдельные команды для
источников кандидатов и read-only Telegram Account Connector.
"""

import argparse
import logging
import sys


def positive_int(value):
    number = int(value)
    if number < 1:
        raise argparse.ArgumentTypeError("значение должно быть больше нуля")
    return number


def setup_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s  %(levelname)-7s  %(message)s",
        datefmt="%H:%M:%S",
    )


def cmd_init_db(args) -> int:
    from src import db

    conn = db.connect()
    try:
        applied = db.init_schema(conn)
        print(
            "✓ Миграции применены: "
            + (", ".join(applied) if applied else "схема актуальна")
        )
        return 0
    finally:
        conn.close()


def cmd_db_status(args) -> int:
    from src import db
    from src.migrations import migration_status

    conn = db.connect()
    try:
        status = migration_status(conn)
        print("Версия БД:", status["current"] or "нет истории миграций")
        print("Версия приложения:", status["latest"])
        print("Ожидают применения:", ", ".join(status["pending"]) or "нет")
        return 1 if status["pending"] else 0
    finally:
        conn.close()


def cmd_scrape(args) -> int:
    from src import pipeline

    result = pipeline.run(
        channels_filter=args.channel,
        workers=args.workers,
        full_history=args.full_history,
        max_pages=args.pages,
    )
    return 1 if result["failed"] or not result["total"] else 0


def cmd_audit_classification(args):
    from src.classification_audit import run

    run(args.input, args.report)
    return 0


def cmd_refresh_metadata(args):
    from src import public_metadata

    result = public_metadata.run(
        channels=args.channel, workers=args.workers, from_config=args.from_config
    )
    return 1 if result["failed"] or not result["total"] else 0


def cmd_stats(args) -> int:
    from src import db

    conn = db.connect()
    try:
        stats = db.stats(conn)
    finally:
        conn.close()
    print(f"Каналов:  {stats['channels']}")
    print(f"Постов:   {stats['posts']}")
    print()
    if not stats["per_channel"]:
        print("(пока пусто)")
        return 0
    print(f"{'канал':<28} {'постов':>8}  {'подписчики':>12}  {'последний пост':<20}")
    print("-" * 78)
    for row in stats["per_channel"]:
        last = (
            row["last_post_at"].strftime("%Y-%m-%d %H:%M")
            if row["last_post_at"]
            else "—"
        )
        name = row["name"] or row["username"]
        name = (name[:26] + "..") if len(name) > 28 else name
        subs = (
            f"{row['subscribers']:,}".replace(",", " ") if row["subscribers"] else "—"
        )
        print(f"{name:<28} {row['post_count']:>8}  {subs:>12}  {last:<20}")
    return 0


def cmd_export(args) -> int:
    from src import db, exporter

    conn = db.connect()
    try:
        count = exporter.export_channels(conn, db)
    finally:
        conn.close()
    print(f"✓ Экспортировано каналов: {count}")
    print("  → web/data/channels.json")
    print("  → web/data/channels.js")
    return 0


def cmd_build_geo_map(args) -> int:
    from src import build_geo_map

    build_geo_map.run()
    return 0


def cmd_build_semantic_map(args) -> int:
    if not args.confirm_content_rights:
        print(
            "Запуск остановлен. Семантическая карта использует embeddings/ML. "
            "Telegram запрещает использовать полученные с платформы данные для "
            "разработки, улучшения и развёртывания AI/ML без предусмотренного "
            "согласия. Используйте команду только если весь набор данных имеет "
            "необходимые права и согласия; затем добавьте --confirm-content-rights.",
            file=sys.stderr,
        )
        return 2
    try:
        from src import build_semantic_map
    except ImportError:
        print(
            "Установите requirements-semantic.txt для семантической карты.",
            file=sys.stderr,
        )
        return 1
    build_semantic_map.run()
    return 0


def cmd_discover_tgden(args) -> int:
    from src import tgden_search

    tgden_search.run(
        queries=args.query or args.city,
        per_query_limit=args.limit_per_query,
    )
    return 0


def cmd_discover_tgstat(args) -> int:
    from src import tgstat_search

    tgstat_search.run(
        queries=args.query,
        categories=args.category,
        country=args.country,
        language=args.language,
        limit=args.limit,
        search_by_description=args.search_by_description,
        delay=args.delay,
    )
    return 0


def cmd_import_candidates_file(args) -> int:
    from src import file_discovery

    file_discovery.run(
        file_path=args.file,
        source_name=args.source_name,
        source_url=args.source_url,
        apply=args.apply,
    )
    return 0


def cmd_import_cloudseeding(args) -> int:
    from src import cloudseeding

    cloudseeding.run(file_path=args.file)
    return 0


def cmd_discover_validate(args) -> int:
    from src import discover

    discover.run(limit=args.limit)
    return 0


def cmd_candidate_stats(args) -> int:
    from src import db

    conn = db.connect()
    try:
        stats = db.candidate_stats(conn)
    finally:
        conn.close()
    print(f"Всего кандидатов: {stats.get('total', 0)}")
    print(f"  Ожидают проверки: {stats.get('pending', 0)}")
    print(f"  Подтверждены:     {stats.get('validated', 0)}")
    print(f"  Импортированы:    {stats.get('imported', 0)}")
    print(f"  Отклонены:        {stats.get('rejected', 0)}")
    print(f"  Связей источник↔канал: {stats.get('source_linked', 0)}")
    return 0


def cmd_candidate_list(args) -> int:
    from src import db

    conn = db.connect()
    try:
        rows = db.list_candidates(conn, status=args.status, limit=args.limit)
    finally:
        conn.close()
    if not rows:
        print("Кандидатов с таким статусом нет.")
        return 0
    print(f"{'username':<28} {'подписчики':>12} {'status':<12} {'sources'}")
    print("-" * 86)
    for row in rows:
        subs = (
            f"{row['subscribers']:,}".replace(",", " ")
            if row["subscribers"] is not None
            else "—"
        )
        sources = ", ".join(row.get("sources") or [])
        note = f" | {row['validation_note']}" if row.get("validation_note") else ""
        print(f"@{row['username']:<27} {subs:>12} {row['status']:<12} {sources}{note}")
    return 0


def cmd_candidate_import(args) -> int:
    from src import db, config

    conn = db.connect()
    try:
        if not args.apply:
            from src.audience import meets_minimum_subscribers

            # Ограничение --limit применяется после отбора по аудитории,
            # чтобы маленькие/неизвестные каналы не занимали места в партии.
            rows = db.list_candidates(conn, status="all", limit=1_000_000)
            wanted = {u.lstrip("@").casefold() for u in (args.username or [])}
            if wanted:
                rows = [r for r in rows if r["username"].casefold() in wanted]

            validated = [r for r in rows if r["status"] == "validated"]
            pending = [r for r in rows if r["status"] == "pending"]
            rejected = [r for r in rows if r["status"] == "rejected"]
            imported = [r for r in rows if r["status"] == "imported"]

            eligible_statuses = {"validated"}
            if args.include_pending:
                eligible_statuses.add("pending")
            eligible = [
                r
                for r in rows
                if r["status"] in eligible_statuses
                and meets_minimum_subscribers(
                    r.get("subscribers"), config.MIN_CHANNEL_SUBSCRIBERS
                )
            ]
            below_minimum = [
                r
                for r in rows
                if r["status"] != "imported"
                and r.get("subscribers") is not None
                and not meets_minimum_subscribers(
                    r.get("subscribers"), config.MIN_CHANNEL_SUBSCRIBERS
                )
            ]
            unknown_audience = [
                r
                for r in rows
                if r["status"] != "imported" and r.get("subscribers") is None
            ]
            available = eligible[: args.limit] if args.limit else eligible

            print(
                f"Минимум аудитории: {config.MIN_CHANNEL_SUBSCRIBERS:,} подписчиков.".replace(
                    ",", " "
                )
            )
            print(f"Кандидатов в выборке: {len(rows)}")
            print(f"  Подтверждены:             {len(validated)}")
            print(f"  Ожидают проверки:         {len(pending)}")
            print(f"  Отклонены:                {len(rejected)}")
            print(f"  Уже импортированы:        {len(imported)}")
            print(f"  Ниже порога аудитории:    {len(below_minimum)}")
            print(f"  Без данных об аудитории:  {len(unknown_audience)}")
            print(f"Готовы к импорту: {len(eligible)}; в этой партии: {len(available)}")
            if args.include_pending:
                print(
                    "В расчёт допуска включены pending с известной аудиторией не ниже порога."
                )
            print("Для записи в channels выполните ту же команду с --apply.")
            for row in available[:30]:
                subscribers = row.get("subscribers")
                subs_label = (
                    f"{subscribers:,}".replace(",", " ")
                    if subscribers is not None
                    else "—"
                )
                print(
                    f"  @{row['username']} — {row['name'] or 'без названия'} "
                    f"({subs_label}) [{row['status']}]"
                )
            return 0

        with db.transaction(conn):
            count = db.import_candidates(
                conn,
                usernames=args.username,
                limit=args.limit,
                include_pending=args.include_pending,
                min_subscribers=config.MIN_CHANNEL_SUBSCRIBERS,
            )
    finally:
        conn.close()
    print(f"✓ Импортировано в рабочий каталог: {count}")
    print(
        f"Минимальный размер аудитории: {config.MIN_CHANNEL_SUBSCRIBERS:,}".replace(
            ",", " "
        )
    )
    print("Следующий шаг: python main.py scrape")
    return 0


def cmd_telegram_auth(args) -> int:
    from src import telegram_account

    telegram_account.authorize()
    return 0


def cmd_telegram_import(args) -> int:
    from src import telegram_account

    telegram_account.import_subscriptions(
        limit=args.limit, offset=args.offset, apply=args.apply
    )
    return 0


def cmd_telegram_recommendations(args) -> int:
    from src import telegram_account

    telegram_account.discover_recommendations(
        seeds=args.seed,
        from_subscriptions=args.from_subscriptions,
        global_recommendations=args.global_recommendations,
        max_seeds=args.max_seeds,
        limit=args.limit,
        apply=args.apply,
    )
    return 0


def cmd_mtproto_enrich(args) -> int:
    from src import mtproto_enrich

    mtproto_enrich.run(limit=args.limit, channel=args.channel)
    return 0


def cmd_mtproto_scrape(args) -> int:
    from src import mtproto_scrape

    mtproto_scrape.run(
        channels=args.channel,
        limit=args.limit,
        messages_per_channel=args.messages,
    )
    return 0


def cmd_backfill_region(args) -> int:
    from src import backfill_region

    backfill_region.run(
        force=args.force,
        debug=args.debug,
        username=args.username,
        dry_run=args.dry_run,
        report_path=args.report,
    )
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="tg-atlas",
        description="Unofficial Channel Atlas — каталог, классификация и карты публичных каналов.",
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="подробный лог")
    sub = parser.add_subparsers(dest="command", required=True)

    sp = sub.add_parser("init-db", help="создать/обновить схему PostgreSQL")
    sp.set_defaults(func=cmd_init_db)

    sp = sub.add_parser(
        "db-status", help="проверить историю и ожидающие миграции без изменения БД"
    )
    sp.set_defaults(func=cmd_db_status)

    sp = sub.add_parser(
        "scrape", help="собрать HTML-публичных страниц каналов, уже внесённых в БД"
    )
    sp.add_argument("--channel", "-c", action="append", metavar="USERNAME")
    sp.add_argument(
        "--workers",
        type=int,
        choices=range(1, 9),
        help="число сетевых загрузчиков (1–8)",
    )
    sp.add_argument(
        "--pages",
        type=positive_int,
        help="лимит страниц на канал (по умолчанию MAX_PAGES)",
    )
    sp.add_argument(
        "--full-history",
        action="store_true",
        help="читать историю без остановки на уже сохранённых постах, в пределах лимита страниц",
    )
    sp.set_defaults(func=cmd_scrape)

    sp = sub.add_parser(
        "refresh-metadata",
        help="обновить публичные название, описание и подписчиков без публикаций и Telethon",
    )
    sp.add_argument("--channel", "-c", action="append", metavar="USERNAME")
    sp.add_argument(
        "--workers", type=int, choices=range(1, 9), help="сетевые загрузчики (1–8)"
    )
    sp.add_argument(
        "--from-config",
        action="store_true",
        help="импортировать каталог config/channels.json перед обновлением",
    )
    sp.set_defaults(func=cmd_refresh_metadata)

    sp = sub.add_parser(
        "audit-classification",
        help="проверить классификацию выгрузки без запросов к Telegram и БД",
    )
    sp.add_argument("--input", default="web/data/channels.json")
    sp.add_argument("--report", default="reports/classification-review.csv")
    sp.set_defaults(func=cmd_audit_classification)

    sp = sub.add_parser("stats", help="статистика каналов и публикаций")
    sp.set_defaults(func=cmd_stats)

    sp = sub.add_parser("export", help="экспортировать каналы для веб-карты")
    sp.set_defaults(func=cmd_export)

    sp = sub.add_parser(
        "build-geo-map", help="пересчитать географические координаты карты"
    )
    sp.set_defaults(func=cmd_build_geo_map)

    sp = sub.add_parser(
        "build-semantic-map",
        help="экспериментальная ML-раскладка только для данных с подтверждёнными правами",
    )
    sp.add_argument(
        "--confirm-content-rights",
        action="store_true",
        help="подтверждаю права и необходимые согласия для всего набора данных",
    )
    sp.set_defaults(func=cmd_build_semantic_map)

    sp = sub.add_parser(
        "discover-tgden",
        help="найти публичные каналы через прежний suggest-поиск tgden",
    )
    sp.add_argument(
        "--query",
        action="append",
        help="поисковый запрос; можно повторять; без него используется config/discovery_queries.json",
    )
    sp.add_argument("--city", action="append", help=argparse.SUPPRESS)
    sp.add_argument(
        "--limit-per-query",
        type=int,
        default=20,
        help="не более 100 подсказок на запрос (по умолчанию 20)",
    )
    sp.set_defaults(func=cmd_discover_tgden)

    sp = sub.add_parser(
        "discover-tgstat", help="искать публичные каналы через официальный TGStat API"
    )
    sp.add_argument("--query", action="append", help="ключевое слово; можно повторять")
    sp.add_argument(
        "--category", action="append", help="категория TGStat; можно повторять"
    )
    sp.add_argument(
        "--country", default="ru", help="код страны TGStat (по умолчанию ru)"
    )
    sp.add_argument(
        "--language", default="russian", help="язык TGStat (по умолчанию russian)"
    )
    sp.add_argument("--limit", type=int, default=20, help="до 100 каналов на запрос")
    sp.add_argument(
        "--search-by-description",
        action="store_true",
        help="искать также в описании канала",
    )
    sp.add_argument(
        "--delay", type=float, default=1.5, help="пауза между запросами в секундах"
    )
    sp.set_defaults(func=cmd_discover_tgstat)

    sp = sub.add_parser(
        "import-candidates",
        help="предпросмотр/импорт списка каналов из TXT, CSV, TSV или JSON",
    )
    sp.add_argument("--file", required=True, help="путь к файлу со списком каналов")
    sp.add_argument(
        "--source-name", default="manual_file_import", help="метка источника в базе"
    )
    sp.add_argument("--source-url", help="ссылка на страницу источника, если есть")
    sp.add_argument(
        "--apply",
        action="store_true",
        help="записать кандидатов в channel_candidates; по умолчанию preview",
    )
    sp.set_defaults(func=cmd_import_candidates_file)

    sp = sub.add_parser(
        "import-cloudseeding",
        help="импортировать локальный CSV/JSON-экспорт CloudSeeding",
    )
    sp.add_argument(
        "--file", required=True, help="путь к CSV, TSV или JSON-файлу экспорта"
    )
    sp.set_defaults(func=cmd_import_cloudseeding)

    sp = sub.add_parser(
        "discover-validate",
        help="проверить каналы через t.me/s/ и применить порог подписчиков",
    )
    sp.add_argument("--limit", "-l", type=int, help="проверить не более N кандидатов")
    sp.set_defaults(func=cmd_discover_validate)

    sp = sub.add_parser("candidate-stats", help="статистика единого реестра кандидатов")
    sp.set_defaults(func=cmd_candidate_stats)

    sp = sub.add_parser("candidate-list", help="показать кандидатов")
    sp.add_argument(
        "--status",
        choices=["pending", "validated", "imported", "rejected", "all"],
        default="pending",
    )
    sp.add_argument("--limit", type=int, default=100)
    sp.set_defaults(func=cmd_candidate_list)

    sp = sub.add_parser(
        "candidate-import", help="импорт только кандидатов с достаточной аудиторией"
    )
    sp.add_argument(
        "--username",
        action="append",
        help="импортировать выбранный username; можно повторять",
    )
    sp.add_argument(
        "--limit", type=int, help="ограничить число импортируемых кандидатов"
    )
    sp.add_argument(
        "--include-pending",
        action="store_true",
        help="разрешить импорт pending только при известной аудитории не ниже порога",
    )
    sp.add_argument(
        "--apply",
        action="store_true",
        help="действительно записать в channels; без флага только preview",
    )
    sp.set_defaults(func=cmd_candidate_import)

    sp = sub.add_parser(
        "telegram-auth", help="авторизовать отдельный резервный Telegram-аккаунт"
    )
    sp.set_defaults(func=cmd_telegram_auth)

    sp = sub.add_parser(
        "telegram-import-subscriptions",
        help="предпросмотр публичных каналов из подписок аккаунта",
    )
    sp.add_argument(
        "--limit",
        type=int,
        default=500,
        help="число публичных каналов в партии (1–1000, по умолчанию 500)",
    )
    sp.add_argument(
        "--offset",
        type=int,
        default=0,
        help="пропустить N публичных каналов для следующей партии",
    )
    sp.add_argument(
        "--apply",
        action="store_true",
        help="сохранить кандидатов в PostgreSQL; без флага только CSV-preview",
    )
    sp.set_defaults(func=cmd_telegram_import)

    sp = sub.add_parser(
        "telegram-discover-recommendations",
        help="получить рекомендации публичных каналов от Telegram",
    )
    mode = sp.add_mutually_exclusive_group(required=True)
    mode.add_argument(
        "--seed",
        action="append",
        metavar="USERNAME",
        help="канал, на который резервный аккаунт уже подписан; можно повторять",
    )
    mode.add_argument(
        "--from-subscriptions",
        action="store_true",
        help="взять первые публичные каналы из подписок как seed",
    )
    mode.add_argument(
        "--global",
        dest="global_recommendations",
        action="store_true",
        help="получить общие рекомендации на основе подписок аккаунта",
    )
    sp.add_argument(
        "--max-seeds",
        type=int,
        default=5,
        help="максимум seed-каналов при --from-subscriptions; предел задаётся в .env",
    )
    sp.add_argument(
        "--limit",
        type=int,
        default=100,
        help="максимум кандидатов в preview/БД (1–500)",
    )
    sp.add_argument(
        "--apply",
        action="store_true",
        help="сохранить найденных кандидатов в PostgreSQL; по умолчанию только CSV-preview",
    )
    sp.set_defaults(func=cmd_telegram_recommendations)

    sp = sub.add_parser(
        "mtproto-enrich",
        help="точечно получить метаданные выбранных каналов через MTProto",
    )
    sp.add_argument("--channel", "-c", help="один username")
    sp.add_argument("--limit", "-l", type=int, help="не более 25 каналов")
    sp.set_defaults(func=cmd_mtproto_enrich)

    sp = sub.add_parser(
        "mtproto-scrape",
        help="собрать ограниченное число последних постов через резервный аккаунт",
    )
    sp.add_argument(
        "--channel",
        "-c",
        action="append",
        help="username канала в channels; параметр можно повторять",
    )
    sp.add_argument(
        "--limit", "-l", type=int, help="не более 25 активных каналов из БД"
    )
    sp.add_argument(
        "--messages",
        type=int,
        default=50,
        help="не более 100 сообщений на канал (по умолчанию 50)",
    )
    sp.set_defaults(func=cmd_mtproto_scrape)

    sp = sub.add_parser("backfill-region", help="пересчитать регион и тип канала")
    sp.add_argument("--force", action="store_true")
    sp.add_argument("--debug", action="store_true")
    sp.add_argument("--username", metavar="NAME")
    sp.add_argument("--dry-run", action="store_true")
    sp.add_argument("--report", metavar="PATH")
    sp.set_defaults(func=cmd_backfill_region)

    return parser


def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    setup_logging(args.verbose)
    try:
        return args.func(args)
    except KeyboardInterrupt:
        print("\nПрервано пользователем", file=sys.stderr)
        return 130
    except Exception as exc:
        logging.error("Ошибка: %s", exc, exc_info=args.verbose)
        return 1


if __name__ == "__main__":
    sys.exit(main())
