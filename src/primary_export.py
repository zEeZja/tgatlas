"""Применить опубликованный кеш к текущему JSON, не сокращая каталог до БД."""

from datetime import datetime, timezone
import json
from pathlib import Path

from .classification_runtime import ROOT, load_results, merge_result
from .file_io import atomic_write_text
from .topic_keywords import load_classification_rules, resolve_topic


def export_current(source=None, *, apply=False, output=None, backup_root=None):
    source = Path(source or ROOT / "web/data/channels.json")
    data = json.loads(source.read_text(encoding="utf-8"))
    rows = data["channels"] if isinstance(data, dict) else data
    if not isinstance(rows, list) or len({r["username"] for r in rows}) != len(rows):
        raise ValueError("Нужен текущий каталог без повторных username")
    predictions = load_results()
    if not predictions:
        raise ValueError("Сначала явно опубликуйте результаты V11 или 11v2")
    rules = load_classification_rules()
    refreshed = []
    for original in rows:
        row = dict(original)
        username = row["username"]
        if username in rules["overrides"] or username in rules["geography_overrides"]:
            confirmed = resolve_topic(row, **rules)
            for key, value in confirmed.items():
                if username in rules["overrides"] and (
                    key.startswith(("topic_", "primary_", "secondary_", "broader_"))
                    or key in ("subcategory", "classification_review_stale")
                ):
                    row[key] = value
                if username in rules["geography_overrides"] and key.startswith(
                    "geography_"
                ):
                    row[key] = value
        refreshed.append(row)
    merged = [merge_result(row, predictions) for row in refreshed]
    payload = {
        **(data if isinstance(data, dict) else {}),
        "channels": merged,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
    text = json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
    if not apply:
        output = Path(output or ROOT / "reports/classification-v11/map-preview.json")
        if output.exists() or output.resolve() == source.resolve():
            raise ValueError("Нужно новое имя предварительного экспорта")
        atomic_write_text(output, text)
        return dict(channels=len(rows), applied=False, output=str(output))
    # JSON and JS may contain different user work: archive each independently.
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%f")
    backup = (
        Path(backup_root or ROOT / "reports/classification-v11/export-backups") / stamp
    )
    for name in ("channels.json", "channels.js"):
        path = ROOT / "web/data" / name
        if path.exists():
            atomic_write_text(backup / name, path.read_text(encoding="utf-8"))
    atomic_write_text(ROOT / "web/data/channels.json", text)
    atomic_write_text(
        ROOT / "web/data/channels.js", "window.TG_DATA = " + text.rstrip() + ";\n"
    )
    return dict(channels=len(rows), applied=True, backup=str(backup))
