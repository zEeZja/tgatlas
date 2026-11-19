"""Подготовить план или собрать согласованный пилот для пересмотра тематик."""

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--snapshot",
        type=Path,
        default=ROOT / "reports/classification-v10/benchmark-snapshot.json",
    )
    parser.add_argument(
        "--predictions",
        type=Path,
        default=ROOT / "reports/classification-v10/benchmark-small-cold.json",
    )
    parser.add_argument(
        "--directory", type=Path, default=ROOT / "reports/classification-v11/pilot-120"
    )
    parser.add_argument(
        "--collect",
        action="store_true",
        help="явно разрешить согласованный сбор; иначе только план",
    )
    args = parser.parse_args()
    from src.taxonomy_pilot import select_pilot, collect_pilot
    from src.file_io import atomic_write_text

    plan_path = args.directory / "plan.json"
    if plan_path.exists():
        plan = json.loads(plan_path.read_text(encoding="utf-8"))
    else:
        rows = json.loads(args.snapshot.read_text(encoding="utf-8"))
        predictions = json.loads(args.predictions.read_text(encoding="utf-8"))[
            "channels"
        ]
        plan = select_pilot(rows, predictions)
        atomic_write_text(
            plan_path, json.dumps(plan, ensure_ascii=False, indent=2) + "\n"
        )
    print(
        f"План: {len(plan)} каналов, до {len(plan) * 2} запросов; {plan_path}",
        flush=True,
    )
    if args.collect:
        import requests
        from src.config import USER_AGENT

        with requests.Session() as session:
            session.headers.update(
                {"User-Agent": USER_AGENT, "Accept-Language": "ru,en;q=0.8"}
            )
            state = collect_pilot(plan, args.directory, session)
        print(
            json.dumps(
                {k: v for k, v in state.items() if k != "channels"},
                ensure_ascii=False,
                indent=2,
            )
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
