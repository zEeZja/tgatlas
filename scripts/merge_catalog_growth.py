"""Безопасно добавить к карте только каналы выбранных запусков grow_catalog."""

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    prepare = commands.add_parser(
        "prepare", help="read-only БД → новый предпросмотр, карта не меняется"
    )
    prepare.add_argument(
        "--state",
        type=Path,
        action="append",
        required=True,
        help="можно повторить для нескольких этапов поиска",
    )
    prepare.add_argument("--output", type=Path, required=True)
    publish = commands.add_parser("publish")
    publish.add_argument(
        "--input", type=Path, required=True, help="папка предпросмотра"
    )
    publish.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    try:
        from src.catalog_growth_merge import (
            prepare as make_preview,
            publish as apply_preview,
        )

        if args.command == "publish":
            result = apply_preview(args.input, apply=args.apply)
        else:
            run_ids = []
            for path in args.state:
                state = json.loads(path.read_text(encoding="utf-8"))
                if state.get("version") != 2 or not isinstance(
                    state.get("run_id"), str
                ):
                    raise ValueError("Неверный файл состояния роста")
                run_ids.append(state["run_id"])
            from src import db, config, exporter
            import psycopg2

            try:
                conn = psycopg2.connect(**{**config.PG, "connect_timeout": 5})
            except psycopg2.Error as exc:
                raise ValueError(
                    "Не удалось подключиться к БД: " + type(exc).__name__
                ) from exc
            try:
                conn.set_session(readonly=True)
                with conn.cursor() as cursor:
                    cursor.execute("SET statement_timeout = '30s'")
                    cursor.execute(
                        "SELECT c.username FROM channel_candidates c JOIN channels ch ON ch.id=c.imported_channel_id WHERE c.status='imported' AND ch.is_active AND c.raw_data->>'growth_run_id'=ANY(%s)",
                        (run_ids,),
                    )
                    usernames = {row[0] for row in cursor.fetchall()}
                rows = [
                    row
                    for row in db.get_channels_with_meta(conn)
                    if row["username"] in usernames
                ]
                additions = exporter.channel_rows(rows)
            except psycopg2.Error as exc:
                raise ValueError("Ошибка чтения БД: " + type(exc).__name__) from exc
            finally:
                conn.close()
            result = make_preview(additions, args.output, run_ids=run_ids)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (ValueError, OSError, KeyError, ImportError) as exc:
        print(str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
