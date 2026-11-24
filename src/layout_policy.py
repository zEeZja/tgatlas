"""Общая политика географической значимости для раскладки, не геоклассификатор."""

import hashlib
import json
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parent.parent


def load_policy():
    data = json.loads((ROOT / "config/map_layout.json").read_text(encoding="utf-8"))
    if data.get("format") != "atlas-layout-policy-v1":
        raise ValueError("Неизвестная политика раскладки")
    return data


def policy_fingerprint(data):
    return hashlib.sha256(
        json.dumps(
            data, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode()
    ).hexdigest()


def geography_role(row, policy):
    scope = row.get("geography_scope")
    regions = sorted(
        set(row.get("geography_regions") or ([] if scope else [row.get("region")]))
        - {None, ""}
    )
    country = (
        row.get("geography_country") or (row.get("country") if not scope else "") or ""
    )
    if country and country != "RU":
        regions = []
    if scope in ("unknown", "nonlocal", "global"):
        regions = []
    text = (
        (
            str(row.get("name") or "")
            + " "
            + " ".join(
                line
                for line in str(row.get("description") or "").splitlines()
                if not re.search(policy["biography_pattern"], line, re.I)
            )
        )
        .lower()
        .replace("ё", "е")
    )
    topic = row.get("subcategory") or "other"
    source = row.get("geography_source") or ""
    matched = [
        c
        for c in row.get("geography_candidates", [])
        if isinstance(c, dict)
        and (
            c.get("region") in regions
            or scope == "country"
            and c.get("country") == country
            and not c.get("region")
        )
    ]
    proof = (
        source == "manual"
        or source in ("title", "title+description", "profile_title", "profile")
        or any(c.get("title_match") or c.get("description_match") for c in matched)
    )
    conditional = (
        topic in policy["conditional_topics"]
        and proof
        and bool(re.search(policy["local_context_pattern"], text, re.I))
    )
    explicit = proof and bool(re.search(policy["explicit_local_pattern"], text, re.I))
    relevant = topic in policy["regional_topics"] or conditional or explicit
    if row.get("classification_engine") == "semantic-v11v2":
        relevant = (
            relevant
            or (
                row.get("topic_granularity") == "group"
                and row.get("topic_group") == "v11_news_society"
            )
            or (
                topic in ("v11_medicine", "v11_beauty", "v11_psychology")
                and "local_service_profile_location"
                in row.get("geography_review_reasons", [])
            )
        )
    if re.search(policy["international_pattern"], text, re.I) and source != "manual":
        relevant = False
    provisional = bool(row.get("geography_review_required"))
    if relevant and regions:
        return dict(
            kind="region",
            key=" · ".join(regions),
            label=" · ".join(regions),
            regions=regions,
            country=country or "RU",
            weight=(
                policy.get("profile_region_weight", 0.9)
                if proof
                else policy["proposal_region_weight"]
            )
            if provisional
            else 1.0,
            provisional=provisional,
        )
    if relevant and scope == "country" and country:
        return dict(
            kind="country",
            key="country:" + country,
            label=country,
            regions=[],
            country=country,
            weight=0,
            provisional=provisional,
        )
    return dict(
        kind="topic",
        key="",
        label="",
        regions=[],
        country=country,
        weight=0,
        provisional=provisional,
    )


def row_binding(row):
    """Only public projection inputs; exact matching, not a probabilistic hash."""
    values = [
        str(row.get("id") or row["username"]),
        row["username"],
        row.get("name") or "",
        row.get("description") or "",
        row.get("subcategory") or "other",
        row.get("topic_group") or "",
        row.get("geography_scope") or "",
        row.get("geography_country") or "",
        row.get("geography_regions") or [],
        row.get("region") or "",
        row.get("country") or "",
        row.get("geography_source") or "",
        bool(row.get("geography_review_required")),
        [
            [
                c.get("region") or "",
                bool(c.get("title_match")),
                bool(c.get("description_match")),
            ]
            for c in row.get("geography_candidates", [])
            if isinstance(c, dict)
        ],
    ]
    if row.get("classification_engine") == "semantic-v11v2":
        values.append(
            [
                "semantic-v11v2",
                row.get("topic_granularity") or "",
                row.get("geography_review_reasons") or [],
                row.get("classification_policy_sha256") or "",
                row.get("classification_implementation_sha256") or "",
            ]
        )
    return json.dumps(values, ensure_ascii=False, separators=(",", ":"))
