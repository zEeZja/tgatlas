"""Coverage is independent of topic and is never guessed from a city mention."""

import json
from pathlib import Path


def resolve_geography(channel: dict, overrides: dict | None = None) -> dict:
    if overrides is None:
        path = Path(__file__).resolve().parent.parent / 'config/geography_overrides.json'
        overrides = json.loads(path.read_text(encoding='utf-8')) if path.exists() else {}
    key = str(channel.get('username') or '').lstrip('@').lower()
    override = overrides.get(key)
    if override:
        if override.get('scope') not in ('region','multi_region','country','global','unknown'):
            raise ValueError(f'Invalid geography override for {key}')
        channel = {**channel, **override, 'geographic_scope':override['scope'], 'region_review_required':False}

    raw = channel.get('regions')
    regions = sorted(set(x.strip() for x in raw if isinstance(x, str) and x.strip())) if isinstance(raw, list) else []
    if not regions and isinstance(channel.get('region'), str) and channel['region'].strip():
        regions = [channel['region'].strip()]
    if channel.get('region_review_required'):
        regions = []
    explicit = channel.get('geographic_scope')
    if explicit in ('global', 'country', 'unknown'):
        scope, regions = explicit, []
    elif len(regions) > 1:
        scope = 'multi_region'
    elif regions:
        scope = 'region'
    elif channel.get('channel_type') == 'federal':
        scope = 'country'
    else:
        scope = 'unknown'
    country = str(channel.get('country') or '').strip().upper()
    if scope == 'country' and not country:
        scope = 'unknown'
    return dict(geography_scope=scope, geography_regions=regions, geography_country=country,
                geography_review_required=scope == 'unknown')
