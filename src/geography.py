"""Coverage is independent of topic; explicit fields and unambiguous place names are resolved conservatively."""

import json
from pathlib import Path
from .title_region import profile_geography, find_title_regions, infer_foreign_title
from .classification_text import informative_description
from .region_classifier import normalize_text
from .places import resolve_places

POLICY = json.loads(
    (Path(__file__).resolve().parent.parent / "config/topics.json").read_text(
        encoding="utf-8"
    )
)


def resolve_geography(
    channel: dict, overrides: dict | None = None, *, federal_channels=None
) -> dict:
    if overrides is None:
        path = (
            Path(__file__).resolve().parent.parent / "config/geography_overrides.json"
        )
        overrides = (
            json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
        )
        labels_path = (
            Path(__file__).resolve().parent.parent / "config/classification_labels.json"
        )
        labels = json.loads(labels_path.read_text(encoding="utf-8"))["channels"]
        overrides = {
            **overrides,
            **{
                u: {k: v[k] for k in ("scope", "regions", "country")}
                for u, v in labels.items()
            },
        }
    key = str(channel.get("username") or "").lstrip("@").lower()
    override = overrides.get(key)
    if override:
        if override.get("scope") not in (
            "region",
            "multi_region",
            "country",
            "global",
            "unknown",
            "nonlocal",
        ):
            raise ValueError(f"Invalid geography override for {key}")
        channel = {
            **channel,
            **override,
            "geographic_scope": override["scope"],
            "region_review_required": False,
        }

    raw = channel.get("regions")
    regions = (
        sorted(set(x.strip() for x in raw if isinstance(x, str) and x.strip()))
        if isinstance(raw, list)
        else []
    )
    if (
        not regions
        and isinstance(channel.get("region"), str)
        and channel["region"].strip()
    ):
        regions = [channel["region"].strip()]
    if channel.get("region_review_required"):
        regions = []
    # Recover clear title geography even when an old export marked it for review.
    federal = channel.get("channel_type") == "federal"
    if federal_channels is None:
        federal_path = (
            Path(__file__).resolve().parent.parent / "config/federal_channels.json"
        )
        federal_channels = {
            x.lower()
            for x in json.loads(federal_path.read_text(encoding="utf-8"))["channels"]
        }
    federal = federal or key in federal_channels
    foreign = None
    source = "manual" if override else "stored" if regions else "none"
    if federal and not override and not channel.get("geographic_scope"):
        regions = []
        source = "federal"
    if (
        not regions
        and not override
        and not federal
        and not channel.get("geographic_scope")
    ):
        regions, foreign, source = profile_geography(channel)
    cleaned = informative_description(channel.get("description"))
    if source == "none" and not regions and not override:
        foreign_desc = infer_foreign_title(cleaned)
        words = normalize_text(cleaned)
        if (
            foreign_desc
            and foreign_desc["scope"] == "country"
            and any(
                x in words
                for x in (
                    "налог",
                    "законодатель",
                    "экономик",
                    "рынок",
                    "рынке",
                    "политик",
                    "туризм",
                    "путешеств",
                )
            )
        ):
            foreign = foreign_desc
            source = "description"
        elif not find_title_regions(cleaned) and any(
            " " + normalize_text(phrase) + " " in " " + words + " "
            for phrase in POLICY["nonlocal_evidence"]
        ):
            source = "nonlocal_evidence"
    explicit = channel.get("geographic_scope")
    if explicit in ("global", "country", "unknown", "nonlocal"):
        scope, regions = explicit, []
    elif source == "nonlocal_evidence":
        scope = "nonlocal"
    elif foreign and foreign["scope"] == "country":
        scope = "country"
    elif len(regions) > 1:
        scope = "multi_region"
    elif regions:
        scope = "region"
    elif federal:
        scope = "country"
    else:
        scope = "unknown"
    country = str(channel.get("country") or "").strip().upper()
    if foreign:
        country = foreign["country"]
    if scope == "country" and not country:
        scope = "unknown"
    if scope == "nonlocal":
        country = ""
    places, ambiguous = resolve_places(
        channel.get("name") or "", channel.get("region_hint") or ""
    )
    if not places:
        places, ambiguous = resolve_places(
            cleaned, channel.get("region_hint") or "", True
        )
    return dict(
        geography_relevance="nonterritorial"
        if scope == "nonlocal"
        else "undetermined"
        if scope == "unknown"
        else "territorial",
        geography_places=[
            {k: p[k] for k in ("id", "label", "region", "country", "kind")}
            for p in places
            if p["region"] in regions and not ambiguous
        ],
        geography_candidates=sorted({p["region"] for p in places}) if ambiguous else [],
        geography_evidence=[source] if source != "none" else [],
        geography_scope=scope,
        geography_regions=regions,
        geography_country=country,
        geography_review_required=scope == "unknown",
        geography_source=source,
    )
