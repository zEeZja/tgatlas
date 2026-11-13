"""Bundle a self-contained, backwards-compatible owner review form."""

import json
from pathlib import Path
from .topic_keywords import TAXONOMY, load_classification_rules, resolve_topic
from .classification_review import FIELDS, upgrade_record

ROOT = Path(__file__).resolve().parent.parent


def render_review(records):
    rules = load_classification_rules()
    prepared = []
    for record in records:
        row = upgrade_record(record)
        current = resolve_topic(dict(username=row["username"], name=row["name"], description=row["description"],
                                     country=row.get("input_country", ""), region_hint=row.get("input_region_hint", "")),
                                {}, geography_overrides={}, tag_overrides={}, federal_channels=rules["federal_channels"])
        row["current_suggestion"] = dict(
            expected_topic=current["subcategory"], expected_group=current["topic_group"],
            expected_secondary_topics=",".join(current["secondary_topics"]),
            expected_formats=",".join(current["publication_formats"]),
            expected_scope=current["geography_scope"], expected_regions="|".join(current["geography_regions"]),
            expected_country=current["geography_country"])
        prepared.append(row)
    data = dict(
        records=prepared,
        fields=FIELDS,
        topics=[
            dict(id=t["id"], label=t["label"], group=t["group"], definition=t["definition"])
            for t in TAXONOMY["topics"]
        ],
        groups=TAXONOMY["groups"],
        formats=TAXONOMY["formats"],
        scopes=TAXONOMY["geography_scopes"],
        taxonomyVersion=TAXONOMY["version"],
    )
    payload = (
        json.dumps(data, ensure_ascii=False)
        .replace("<", "\\u003c")
        .replace("\u2028", "\\u2028")
        .replace("\u2029", "\\u2029")
    )
    script = (
        (ROOT / "web/review.js")
        .read_text(encoding="utf-8")
        .replace("</script", "<\\/script")
    )
    template = (ROOT / "web/review-template.html").read_text(encoding="utf-8")
    return template.replace("/*__DATA__*/", payload).replace("/*__SCRIPT__*/", script)
