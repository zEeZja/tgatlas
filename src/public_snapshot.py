"""Publish a metadata-only, reproducible static map without modifying working data."""

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

from .atlas_projection import row_binding, validate_artifact
from .file_io import atomic_write_text
from .layout_policy import load_policy, policy_fingerprint

PUBLIC_FIELDS = frozenset("""
id username name description subscribers url category subcategory primary_category
topic_group topic_group_label topic_category topic_path topic_granularity topic_source
topic_score topic_score_kind topic_margin topic_tags topic_candidates topic_group_candidates
topic_review_required topic_assignment_status topic_version topic_suggested suggested_topic
secondary_topics broader_topics publication_formats original_subcategory
country region channel_type region_hint region_review_required region_source
geography_scope geography_country geography_regions geography_source geography_candidates
geography_review_required geography_review_reasons geography_explanation geography_relevance
classification_engine classification_version classification_created_at classification_profile
classification_model classification_policy_sha256 classification_implementation_sha256
classification_geography_policy_sha256 classification_taxonomy_sha256
classification_manual_dimensions classification_review_reasons classification_review_stale
post_routing last_meta_at
""".split())


def compact(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


def public_row(row):
    result = {k: v for k, v in row.items() if k in PUBLIC_FIELDS}
    if isinstance(result.get("post_routing"), dict):
        result["post_routing"] = {k: v for k, v in result["post_routing"].items()
                                  if k in ("usable", "fetched_at", "status", "requested")}
    for key in ("topic_candidates", "topic_group_candidates"):
        if isinstance(result.get(key), list):
            result[key] = [{k: v for k, v in candidate.items() if k in ("id", "score")}
                           for candidate in result[key] if isinstance(candidate, dict)]
    if isinstance(result.get("geography_candidates"), list):
        fields = {"id", "label", "country", "region", "post_support", "title_match", "description_match"}
        result["geography_candidates"] = [
            {k: v for k, v in candidate.items() if k in fields}
            for candidate in result["geography_candidates"] if isinstance(candidate, dict)
        ]
    return result


def prepare(channels_path, layout_path, *, now=None):
    channels_path, layout_path = Path(channels_path), Path(layout_path)
    original = json.loads(channels_path.read_text(encoding="utf-8"))
    rows = original.get("channels") if isinstance(original, dict) else original
    if not isinstance(rows, list) or not 3 <= len(rows) <= 6000:
        raise ValueError("Нужен полный каталог 3–6000 каналов")
    if any(not isinstance(r, dict) or not isinstance(r.get("username"), str) or not isinstance(r.get("name"), str) for r in rows):
        raise ValueError("Повреждена запись каталога")
    if len({r["username"] for r in rows}) != len(rows):
        raise ValueError("Повторный username")
    artifact = json.loads(layout_path.read_text(encoding="utf-8"))
    policy = load_policy()
    if artifact.get("format") != "atlas-semantic-layout-v1" or artifact.get("policy_sha256") != policy_fingerprint(policy):
        raise ValueError("Проекция не соответствует политике карты")
    now = now or datetime.now(timezone.utc)
    created = datetime.fromisoformat(artifact["created_at"])
    if created.tzinfo is None or not 0 <= (now-created).total_seconds() <= 7*86400:
        raise ValueError("Для публикации нужен свежий совместимый снимок")
    public = [public_row(row) for row in rows]
    bindings = {str(r.get("id") or r["username"]): row_binding(r) for r in public}
    if bindings != artifact.get("bindings"):
        raise ValueError("После удаления внутренних полей изменились привязки t-SNE")
    validate_artifact(artifact, public, policy)
    snapshot_id = hashlib.sha256((compact(public)+compact(artifact)).encode("utf-8")).hexdigest()
    metadata = dict(format="atlas-public-snapshot-v1", id=snapshot_id,
                    reference_at=artifact["created_at"], published_at=now.isoformat(), channels=len(public))
    payload = {"_snapshot": metadata, "channels": public}
    layout = {**artifact, "_snapshot_id": snapshot_id}
    files = {
        "channels.json": compact(payload)+"\n",
        "channels.js": "window.TG_PUBLISHED_CHANNELS="+compact(payload).replace("<", "\\u003c")+";\n",
        "layout.json": compact(layout)+"\n",
        "layout.js": "window.TG_PUBLISHED_LAYOUT="+compact(layout).replace("<", "\\u003c")+";\n",
    }
    manifest = {**metadata, "method": artifact["method"],
                "with_posts": artifact.get("evidence", {}).get("with_posts", 0),
                "source_channels_sha256": hashlib.sha256(channels_path.read_bytes()).hexdigest(),
                "source_layout_sha256": hashlib.sha256(layout_path.read_bytes()).hexdigest(),
                "files": {name: {"bytes": len(text.encode("utf-8")), "sha256": hashlib.sha256(text.encode("utf-8")).hexdigest()} for name, text in files.items()},
                "privacy": "Public channel metadata and saved labels/coordinates only; no posts, cookies, DB dumps or collection state."}
    files["manifest.json"] = json.dumps(manifest, ensure_ascii=False, indent=2)+"\n"
    return files, manifest


def publish(channels_path, layout_path, output, *, apply=False):
    output = Path(output)
    if output.resolve() in {Path(channels_path).parent.resolve(), Path(layout_path).parent.resolve()}:
        raise ValueError("Публичный снимок нельзя записывать поверх рабочих данных")
    files, manifest = prepare(channels_path, layout_path)
    if apply:
        for name, text in files.items():
            atomic_write_text(output/name, text)
    return {**manifest, "applied": apply}
