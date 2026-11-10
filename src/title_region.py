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
