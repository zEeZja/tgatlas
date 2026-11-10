"""Coverage is independent of topic; explicit fields and unambiguous place names are resolved conservatively."""

import json
from pathlib import Path
from .title_region import infer_title_region, infer_foreign_title


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
    # Recover clear title geography even when an old export marked it for review.
    federal = channel.get('channel_type') == 'federal'
    federal_path = Path(__file__).resolve().parent.parent / 'config/federal_channels.json'
    federal = federal or key in {x.lower() for x in json.loads(federal_path.read_text(encoding='utf-8'))['channels']}
    foreign = None
    if not regions and not override and not federal and not channel.get('geographic_scope'):
        inferred = infer_title_region(channel.get('name') or '')
        candidate_foreign = infer_foreign_title(channel.get('name') or '')
        if inferred and not candidate_foreign:
            regions = [inferred]
        elif candidate_foreign and not inferred:
            foreign = candidate_foreign
            if foreign['scope']=='region':regions=[foreign['label']]
    explicit = channel.get('geographic_scope')
    if explicit in ('global', 'country', 'unknown'):
        scope, regions = explicit, []
    elif foreign and foreign['scope']=='country':
        scope='country'
    elif len(regions) > 1:
        scope = 'multi_region'
    elif regions:
        scope = 'region'
    elif federal:
        scope = 'country'
    else:
        scope = 'unknown'
    country = str(channel.get('country') or '').strip().upper()
    if foreign:
        country=foreign['country']
    if scope == 'country' and not country:
        scope = 'unknown'
    return dict(geography_scope=scope, geography_regions=regions, geography_country=country,
                geography_review_required=scope == 'unknown')
