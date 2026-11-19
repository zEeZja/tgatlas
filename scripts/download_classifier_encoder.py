"""Скачать только открытые веса V10, с фиксированной ревизией и лимитом байтов."""

import argparse
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--config", type=Path, default=ROOT / "config/classification_v10.json"
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="разрешить скачивание; без флага только размер и ревизия",
    )
    args = parser.parse_args()
    os.environ.setdefault("HF_HUB_DISABLE_XET", "1")
    from huggingface_hub import HfApi, hf_hub_download
    from src.file_io import atomic_write_text

    policy = json.loads(args.config.read_text(encoding="utf-8"))
    encoder = policy["encoder"]
    info = HfApi(token=False).model_info(
        encoder["name"], revision=encoder["revision"], files_metadata=True
    )
    if info.sha != encoder["revision"] or info.private or info.gated:
        raise ValueError(
            "Нужны общедоступные веса без отдельной авторизации и с точной ревизией"
        )
    allowed = {
        "config.json",
        "model.safetensors",
        "tokenizer.json",
        "tokenizer_config.json",
        "special_tokens_map.json",
        "sentencepiece.bpe.model",
        "vocab.txt",
    }
    files = [f for f in info.siblings if f.rfilename in allowed]
    if not {"config.json", "model.safetensors", "tokenizer_config.json"}.issubset(
        f.rfilename for f in files
    ):
        raise ValueError("Нет обязательных безопасных файлов encoder")
    if any(not isinstance(f.size, int) or f.size < 0 for f in files):
        raise ValueError("Источник не сообщил размеры всех файлов")
    total = sum(f.size for f in files)
    if total > policy["limits"]["download_bytes"]:
        raise ValueError("Размер файлов превышает разрешённый бюджет скачивания")
    result = dict(
        model=info.id,
        revision=info.sha,
        bytes=total,
        files=[dict(name=f.rfilename, bytes=f.size) for f in files],
        applied=False,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2), flush=True)
    if args.apply:
        for file in files:
            hf_hub_download(info.id, file.rfilename, revision=info.sha, token=False)
        result["applied"] = True
        atomic_write_text(
            ROOT / "models/v10/encoder-download.json",
            json.dumps(result, ensure_ascii=False, indent=2) + "\n",
        )
        print("Encoder downloaded; no channel text was sent to an API.", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
