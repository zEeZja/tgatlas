"""Semantic V9 pilot training and profile→posts classification, no news writes."""

import argparse
import csv
import io
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from src.file_io import atomic_write_text  # noqa: E402


def main():
    p = argparse.ArgumentParser(description=__doc__)
    commands = p.add_subparsers(dest="command", required=True)
    train = commands.add_parser(
        "train-pilot",
        help="обучить на подтверждённых train, оценить на замороженном test",
    )
    train.add_argument("--review", required=True)
    train.add_argument("--output", default="models/semantic-v9-pilot.json")
    classify = commands.add_parser(
        "classify",
        help="профиль → посты при недостатке данных → отчёт, явная публикация",
    )
    source = classify.add_mutually_exclusive_group()
    source.add_argument("--input", default="web/data/channels.json")
    source.add_argument("--database", action="store_true")
    classify.add_argument("--model", default="models/semantic-v9-pilot.json")
    classify.add_argument("--limit", type=int, default=100)
    classify.add_argument(
        "--offset", type=int, default=0, help="начало партии в сортировке по username"
    )
    classify.add_argument("--output", default="reports/semantic-v9/comparison.json")
    classify.add_argument(
        "--apply",
        action="store_true",
        help="явно опубликовать предложения для экспорта/карты, сохраняя флаги проверки",
    )
    for command in (train, classify):
        command.add_argument("--encoder", default="intfloat/multilingual-e5-small")
        command.add_argument("--revision")
        command.add_argument("--device", default="cpu")
        command.add_argument("--cache-dir", default="reports/classification-samples")
        command.add_argument(
            "--allow-http",
            action="store_true",
            help="HTTP-пробы постов при отсутствии пригодной локальной выборки",
        )
        command.add_argument("--max-http-channels", type=int, default=10)
        command.add_argument("--pages", type=int, choices=[1, 2, 3], default=2)
        command.add_argument(
            "--local-posts",
            action="store_true",
            help="читать посты из БД .env перед HTTP",
        )
    args = p.parse_args()
    if args.max_http_channels < 0 or (
        args.command == "classify" and (not 1 <= args.limit <= 500 or args.offset < 0)
    ):
        p.error("Некорректный бюджет")
    output = Path(args.output).resolve()
    if any(
        output.is_relative_to((ROOT / d).resolve())
        for d in ("config", "web", "src", "scripts")
    ):
        p.error("Используйте models/ или reports/ для вывода")
    if output == (ROOT / "reports/semantic-v9/catalog.json").resolve():
        p.error("catalog.json публикуется только через --apply; выберите другой отчёт")
    conn = None
    try:
        from src.semantic_model import Encoder, load_model, train_pilot
        from src.semantic_pipeline import (
            PostProvider,
            needs_posts,
            predict_channel,
            publish_results,
        )
        from src.classification_review import load_channels, inspect_review

        if args.local_posts or (args.command == "classify" and args.database):
            from src import db

            conn = db.connect()
            conn.set_session(readonly=True)
        provider = PostProvider(
            args.cache_dir, conn, args.allow_http, args.max_http_channels, args.pages
        )
        if args.command == "train-pilot":
            if Path(args.output).resolve() == Path(args.review).resolve():
                raise ValueError("Модель не должна заменять разметку")
            validation = inspect_review(args.review)
            report = Path(args.output).with_suffix(".review-validation.json")
            atomic_write_text(
                report, json.dumps(validation, ensure_ascii=False, indent=2) + "\n"
            )
            print(
                f"Подтверждено тем: {validation['valid']['topic']}; географий: {validation['valid']['geography']}; ошибок: {len(validation['errors'])}",
                flush=True,
            )
            encoder = Encoder(args.encoder, args.revision, device=args.device)

            def route(row):
                reasons = needs_posts(row)
                if reasons:
                    posts, detail = provider(row)
                    print("@" + row["username"] + ": " + detail["status"], flush=True)
                    return posts, dict(reasons=reasons, **detail)
                return [], dict(status="not_requested", reasons=[])

            model = train_pilot(args.review, args.output, encoder, route)
            summary = {k: v for k, v in model.items() if k != "heads"}
            atomic_write_text(
                Path(args.output).with_suffix(".report.json"),
                json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
            )
            print(json.dumps(summary, ensure_ascii=False, indent=2))
        else:
            model = load_model(args.model)
            encoder = Encoder(model["encoder"], model["revision"], device=args.device)
            if args.database:
                from src import db
                from src.exporter import channel_rows

                rows = channel_rows(db.get_channels_with_meta(conn))
            else:
                rows, _ = load_channels(args.input)
            rows = sorted(rows, key=lambda r: r["username"])[
                args.offset : args.offset + args.limit
            ]
            if not rows:
                raise ValueError("Пустая партия: проверьте offset и источник")
            if conn is not None:
                conn.commit()
            if Path(args.output).resolve() in (
                Path(args.input).resolve(),
                Path(args.model).resolve(),
            ):
                raise ValueError("Отчёт не должен заменять вход или модель")
            results = {}
            from src.topic_keywords import load_classification_rules

            rules = load_classification_rules()
            for i, row in enumerate(rows):
                result = predict_channel(row, model, encoder, provider, rules)
                results[row["username"].lower()] = result
                atomic_write_text(
                    args.output,
                    json.dumps(
                        dict(
                            format="atlas-semantic-results-v9",
                            channels=results,
                            applied=False,
                        ),
                        ensure_ascii=False,
                        indent=2,
                    )
                    + "\n",
                )
                print(
                    f"[{i + 1}/{len(rows)}] @{row['username']}: {result['subcategory']}; посты: {result['post_routing']['status']}",
                    flush=True,
                )
            stream = io.StringIO(newline="")
            fields = [
                "username",
                "topic",
                "group",
                "review_required",
                "reasons",
                "post_status",
                "scope",
                "regions",
            ]
            writer = csv.DictWriter(stream, fields, delimiter=";")
            writer.writeheader()
            for username, r in results.items():
                writer.writerow(
                    dict(
                        username=username,
                        topic=r["subcategory"],
                        group=r["topic_group"],
                        review_required=r["topic_review_required"],
                        reasons=",".join(r["classification_review_reasons"]),
                        post_status=r["post_routing"]["status"],
                        scope=r["geography_scope"],
                        regions="|".join(r["geography_regions"]),
                    )
                )
            atomic_write_text(
                Path(args.output).with_suffix(".csv"), "\ufeff" + stream.getvalue()
            )
            # A standard owner review form keeps post evidence with the proposed answers.
            from src.classification_review import FIELDS, write_review, group_id
            from src.review_page import render_review

            review_rows = []
            for row in rows:
                r = results[row["username"].lower()]
                v = {key: "" for key in FIELDS}
                v.update(
                    username=row["username"],
                    name=row.get("name") or row["username"],
                    description=row.get("description") or "",
                    input_country=row.get("country") or "",
                    input_region_hint=row.get("region_hint") or "",
                    group_id=group_id(row),
                    split="test" if int(group_id(row)[:8], 16) % 4 == 0 else "train",
                    review_schema_version="3",
                    taxonomy_version=str(model["taxonomy_version"]),
                    reviewed="no",
                    topic_reviewed="no",
                    geography_reviewed="no",
                    formats_reviewed="no",
                    expected_topic=r["subcategory"],
                    expected_group=r["topic_group"],
                    expected_secondary_topics=",".join(r["secondary_topics"]),
                    expected_scope=r["geography_scope"],
                    expected_regions="|".join(r["geography_regions"]),
                    expected_country=r["geography_country"],
                    note="V9: " + ", ".join(r["classification_review_reasons"]),
                )
                for field in (
                    "topic",
                    "secondary_topics",
                    "scope",
                    "regions",
                    "country",
                ):
                    v["suggested_" + field] = v["expected_" + field]
                v["post_evidence"] = "\n\n".join(
                    f"#{e.get('message_id', '')} {e.get('url', '')}\n{e.get('text', '')}"
                    for e in r["topic_evidence"]
                    if e["source"] == "post"
                )
                v["semantic_suggestion"] = {
                    key: value
                    for key, value in v.items()
                    if key.startswith("expected_")
                }
                review_rows.append(v)
            review_path = Path(args.output).with_suffix(".review.csv")
            write_review(review_path, review_rows)
            atomic_write_text(
                review_path.with_suffix(".html"), render_review(review_rows)
            )
            if args.apply:
                publish_results(results)
                print(
                    "Предложения опубликованы для export/API; флаги проверки сохранены."
                )
        return 0
    except (ValueError, OSError, ImportError) as e:
        print(str(e), file=sys.stderr)
        return 1
    finally:
        if conn is not None:
            conn.close()


if __name__ == "__main__":
    sys.exit(main())
