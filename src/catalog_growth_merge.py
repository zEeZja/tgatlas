"""Stage insert-only additions for the map; never replace it with a DB subset."""

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

from .classification_runtime import ROOT
from .file_io import atomic_write_text
from .identity import normalize_username


def digest(path):
    path = Path(path)
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None


def unpack(payload):
    rows = payload.get("channels") if isinstance(payload, dict) else payload
    if not isinstance(rows, list):
        raise ValueError("Нужен массив каналов")
    seen = set()
    ids = set()
    for row in rows:
        if (
            not isinstance(row, dict)
            or not isinstance(row.get("username"), str)
            or not isinstance(row.get("name"), str)
        ):
            raise ValueError("Повреждён канал")
        name = normalize_username(row["username"])
        identifier = str(row.get("id") or name)
        if not name or name != row["username"] or name in seen or identifier in ids:
            raise ValueError("Некорректный/повторный username или id")
        seen.add(name)
        ids.add(identifier)
    return rows


def prepare(additions, output, *, root=ROOT, maximum=6000, run_ids=()):
    """additions are already publicly verified and imported by selected runs."""
    root, output = Path(root), Path(output)
    if output.exists():
        raise ValueError("Используйте новую папку слияния")
    source = root / "web/data/channels.json"
    original = json.loads(source.read_text(encoding="utf-8"))
    old = unpack(original)
    incoming = unpack(additions)
    known = {normalize_username(row["username"]) for row in old}
    new = [row for row in incoming if normalize_username(row["username"]) not in known]
    new.sort(key=lambda row: (-int(row.get("subscribers") or 0), row["username"]))
    merged = old + new
    unpack(merged)
    if len(merged) > maximum:
        raise ValueError(
            f"Слияние даст {len(merged)} каналов, лимит {maximum}; существующие записи не удалены"
        )
    payload = {
        **(original if isinstance(original, dict) else {}),
        "channels": merged,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
    text = json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
    manifest = dict(
        format="atlas-growth-merge-v1",
        before=len(old),
        added=len(new),
        after=len(merged),
        maximum=maximum,
        run_ids=list(run_ids),
        source_json_sha256=digest(source),
        source_js_sha256=digest(source.with_suffix(".js")),
        preview_sha256=hashlib.sha256(text.encode("utf-8")).hexdigest(),
    )
    output.mkdir(parents=True)
    atomic_write_text(output / "channels.json", text)
    atomic_write_text(
        output / "manifest.json",
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
    )
    return manifest


def publish(directory, *, apply=False, root=ROOT):
    root, directory = Path(root), Path(directory)
    source = root / "web/data/channels.json"
    js = source.with_suffix(".js")
    manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
    if manifest.get("format") != "atlas-growth-merge-v1":
        raise ValueError("Неизвестный формат слияния")
    if manifest.get("source_json_sha256") != digest(source) or manifest.get(
        "source_js_sha256"
    ) != digest(js):
        raise ValueError(
            "Рабочие JSON/JS изменились после предпросмотра; подготовьте новое слияние"
        )
    preview = directory / "channels.json"
    if manifest.get("preview_sha256") != digest(preview):
        raise ValueError("Предпросмотр изменён после подготовки")
    payload = json.loads(preview.read_text(encoding="utf-8"))
    merged = unpack(payload)
    old = unpack(json.loads(source.read_text(encoding="utf-8")))
    by_name = {normalize_username(r["username"]): r for r in merged}
    if any(by_name.get(normalize_username(row["username"])) != row for row in old):
        raise ValueError("Слияние меняет существующие каналы")
    if len(merged) != manifest.get("after") or len(merged) > 6000:
        raise ValueError("Неверный размер слияния")
    text = json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
    result = dict(
        before=len(old), after=len(merged), added=len(merged) - len(old), applied=apply
    )
    if not apply:
        return result
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%f")
    backup = root / "reports/catalog-growth-6000/map-backups" / stamp
    for path in (source, js):
        if path.exists():
            atomic_write_text(backup / path.name, path.read_text(encoding="utf-8"))
    atomic_write_text(source, text)
    atomic_write_text(
        js, "window.TG_DATA = " + text.replace("<", "\\u003c").rstrip() + ";\n"
    )
    return dict(result, backup=str(backup))
