"""Рабочая карта → компактный публичный снимок; без --apply только проверка."""

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=ROOT/"web/data/channels.json")
    parser.add_argument("--layout", type=Path, default=ROOT/"web/data/atlas_layout.json")
    parser.add_argument("--output", type=Path, default=ROOT/"web/snapshot")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    try:
        from src.public_snapshot import publish
        print(json.dumps(publish(args.input, args.layout, args.output, apply=args.apply), ensure_ascii=False, indent=2))
        return 0
    except (ValueError, KeyError, TypeError, OSError) as exc:
        print(str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
