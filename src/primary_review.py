"""Наглядное сравнение основной V11 с исходным снимком, без ML."""

import json

from .classification_runtime import (
    ROOT,
    fingerprint,
    policy,
    taxonomy,
    validate_results,
)
from .topic_keywords import TOPICS


def render_view(snapshot, result):
    predictions = validate_results(result)
    if result.get("input_sha256") != fingerprint(snapshot):
        raise ValueError("Отчёт получен по другому снимку каналов")
    rows = []
    for row in snapshot:
        username = row["username"]
        proposal = predictions.get(username)
        if proposal is None:
            continue
        rows.append(
            dict(
                username=username,
                name=row.get("name") or username,
                description=row.get("description") or "",
                current=row.get("subcategory") or "other",
                current_source=row.get("topic_source") or "unknown",
                predicted=proposal["subcategory"],
                candidates=proposal["topic_candidates"],
                reasons=proposal["classification_review_reasons"],
                review_required=proposal["topic_review_required"],
                evidence=proposal["topic_evidence"],
                routing=proposal["post_routing"],
                geography_current={
                    k: row.get(k)
                    for k in (
                        "geography_scope",
                        "geography_country",
                        "geography_regions",
                    )
                },
                geography_proposal={
                    k: v for k, v in proposal.items() if k.startswith("geography_")
                },
            )
        )
    tx = taxonomy()
    countries = json.loads(
        (ROOT / "config/countries_v11.json").read_text(encoding="utf-8")
    )
    payload = dict(
        format="atlas-v11-view-v1",
        engine_label="V11",
        fingerprint=fingerprint(result),
        model_sha256=fingerprint(policy()["encoder"]),
        rows=rows,
        snapshot_channels=len(snapshot),
        topics=[
            dict(id=k, label=v["label"] + " · прежняя сетка") for k, v in TOPICS.items()
        ]
        + [dict(id=t["id"], label=t["label"]) for t in tx["topics"]],
        country_labels={x["country"]: x["label"] for x in countries["countries"]},
    )
    data = (
        json.dumps(payload, ensure_ascii=False, allow_nan=False)
        .replace("<", "\\u003c")
        .replace("\u2028", "\\u2028")
        .replace("\u2029", "\\u2029")
    )
    template = (ROOT / "web/classification-v10-template.html").read_text(
        encoding="utf-8"
    )
    script = (
        (ROOT / "web/classification-v10-view.js")
        .read_text(encoding="utf-8")
        .replace("</script", "<\\/script")
    )
    return template.replace("/*__DATA__*/", data).replace("/*__SCRIPT__*/", script)
