"""Coverage is independent of topic; explicit fields and unambiguous place names are resolved conservatively."""

import json
from pathlib import Path
from .title_region import profile_geography


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
    explicit = channel.get("geographic_scope")
    if explicit in ("global", "country", "unknown"):
        scope, regions = explicit, []
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
    return dict(
        geography_scope=scope,
        geography_regions=regions,
        geography_country=country,
        geography_review_required=scope == "unknown",
        geography_source=source,
    )
