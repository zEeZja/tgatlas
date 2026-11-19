"""Создать автономный HTML по готовым прогнозам V10, не меняя карту."""

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
        "--results",
        type=Path,
        default=ROOT / "reports/classification-v10/benchmark-small-cold.json",
    )
    parser.add_argument(
        "--output", type=Path, default=ROOT / "reports/classification-v10/view.html"
    )
    args = parser.parse_args()
    if args.output.exists() or args.output.resolve() in (
        args.snapshot.resolve(),
        args.results.resolve(),
    ):
        parser.error("Выберите новое имя HTML; существующие файлы не заменяются")
    from src.classification_v10_view import build_view
    from src.file_io import atomic_write_text

    try:
        snapshot = json.loads(args.snapshot.read_text(encoding="utf-8"))
        results = json.loads(args.results.read_text(encoding="utf-8"))
        atomic_write_text(args.output, build_view(snapshot, results))
    except (ValueError, OSError) as exc:
        parser.error(str(exc))
    print(str(args.output.resolve()))
    return 0


if __name__ == "__main__":
    sys.exit(main())
