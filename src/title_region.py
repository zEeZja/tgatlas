"""Conservative title-only recovery, shared rules with the static browser."""
from functools import lru_cache
from .region_aliases import REGION_ALIASES
from .region_classifier import normalize_text

RULES = [(region, tuple(normalize_text(alias).split())) for region, aliases in REGION_ALIASES.items() for alias in aliases]

@lru_cache(maxsize=4096)
def infer_title_region(name: str) -> str | None:
    tokens=normalize_text(name).split()
    hits=[]
    for region, parts in RULES:
        for start in range(len(tokens)-len(parts)+1):
            if all(a==b or (len(b)>=5 and a.startswith(b)) for a,b in zip(tokens[start:],parts)):
                hits.append((region,start,start+len(parts),sum(map(len,parts))))
    # A longer overlapping place name wins (e.g. Нижний Новгород over Новгород).
    kept=[h for h in hits if not any(k[3]>h[3] and k[1]<=h[1] and k[2]>=h[2] for k in hits)]
    regions={h[0] for h in kept}
    return next(iter(regions)) if len(regions)==1 else None

import json
import re
from pathlib import Path
FOREIGN = json.loads((Path(__file__).resolve().parent.parent/'config/foreign_geography.json').read_text(encoding='utf-8'))

def infer_foreign_title(name):
    text=normalize_text(name).split()
    matches=[]
    for place in FOREIGN:
        if any(any(t==alias or (len(alias)>=5 and t.startswith(alias)) for t in text) for alias in place['aliases']):
            matches.append(place)
    # An explicit city is more precise than its country; conflicting countries stay unknown.
    if len({p['country'] for p in matches}) != 1:
        return None
    cities=[p for p in matches if p['scope']=='region']
    return cities[0] if len(cities)==1 else matches[0] if not cities and len(matches)==1 else None


def local_news_evidence(name, description):
    normalized=normalize_text(name)
    tokens=normalized.split()
    region=infer_title_region(name)
    foreign=infer_foreign_title(name)
    if not region and not (foreign and foreign['scope']=='region'):
        return None
    markers={'сейчас','сегодня','онлайн','online','live','подслушано','типичный','типичная','главный','события'}
    if markers.intersection(tokens) or re.search(r"№\s*1\b|\.(?:ru|рф)\b",name,re.IGNORECASE):
        return 'name:городской формат (город + сейчас/онлайн/подслушано)'
    desc=normalize_text(description)
    if any(x in desc for x in ['поделиться новост','прислать новост','присылайте новост','новости города','новости нашего города']):
        return 'description:приём городских новостей'
    aliases=REGION_ALIASES[region] if region else foreign['aliases']
    if normalized in {normalize_text(a) for a in aliases}:
        return 'name:название города (требует проверки темы)'
    return None
