"""Создать автономный черновик тематик; опционально исследовать готовый пилот."""

import argparse
import json
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--taxonomy", type=Path, default=ROOT / "config/taxonomy_v11_draft.json"
    )
    parser.add_argument(
        "--pilot", type=Path, default=ROOT / "reports/classification-v11/pilot-120"
    )
    parser.add_argument("--output", type=Path, required=True)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--analysis", type=Path, help="готовый JSON для нового HTML без запуска encoder"
    )
    mode.add_argument(
        "--analyze",
        action="store_true",
        help="локальные embeddings постов и группы-кандидаты; без HTTP",
    )
    args = parser.parse_args()
    if (
        args.output.suffix != ".html"
        or args.output.exists()
        or args.output.with_suffix(".json").exists()
    ):
        parser.error("Нужно новое имя выходного HTML; существующие отчёты сохраняются")
    from src.taxonomy_experiment import (
        load_taxonomy,
        render_review,
        analyze_pilot,
        load_analysis,
    )
    from src.file_io import atomic_write_text

    taxonomy = load_taxonomy(args.taxonomy)
    analysis = None
    if args.analysis:
        analysis = load_analysis(args.analysis, taxonomy)
    if args.analyze:
        from src.classification_encoder import CachedEncoder, peak_memory_mb
        from src.classification_v10 import load_policy

        settings = load_policy()["encoder"]
        started = time.perf_counter()
        encoder = CachedEncoder(
            settings, ROOT / "reports/classification-v11/embeddings.sqlite"
        )
        try:
            analysis = analyze_pilot(args.pilot, taxonomy, encoder, settings)
            analysis["runtime"] = dict(
                seconds=time.perf_counter() - started,
                encoder=dict(encoder.stats),
                peak_memory_mb=peak_memory_mb(),
            )
        finally:
            encoder.close()
        atomic_write_text(
            args.output.with_suffix(".json"),
            json.dumps(analysis, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        )
    atomic_write_text(args.output, render_review(taxonomy, analysis))
    print(args.output.resolve())
    if analysis:
        print(json.dumps(analysis["summary"], ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
