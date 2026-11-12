"""Bundle a self-contained, backwards-compatible owner review form."""

import json
from pathlib import Path
from .topic_keywords import TAXONOMY
from .classification_review import FIELDS, upgrade_record

ROOT = Path(__file__).resolve().parent.parent


def render_review(records):
    data = dict(
        records=[upgrade_record(r) for r in records],
        fields=FIELDS,
        topics=[
            dict(id=t["id"], label=t["label"], group=t["group"])
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
