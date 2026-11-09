"""Построение географической раскладки каналов.

Региональные каналы располагаются вокруг географических центров субъектов РФ.
Используется коническая проекция Ламберта, подходящая для протяжённой территории
России. Федеральные и нераспознанные каналы остаются в каталоге, но не попадают
в географическую плоскость без достоверных координат.

Запуск: python main.py build-geo-map
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from .region_geo import FEDERAL_SUBJECTS, get_coords

log = logging.getLogger(__name__)
OUT_PATH = Path(__file__).resolve().parent.parent / "web" / "data" / "geo_map.json"
MAP_WIDTH = 1600.0
MAP_PADDING = 42.0


def project_lcc(lat: float, lon: float) -> tuple[float, float]:
    """Проекция Ламберта (φ1=50°, φ2=70°, φ0=60°, λ0=100°)."""
    phi1, phi2, phi0, lon0 = map(math.radians, (50.0, 70.0, 60.0, 100.0))
    phi, lam = math.radians(lat), math.radians(lon)
    n = math.log(math.cos(phi1) / math.cos(phi2)) / math.log(
        math.tan(math.pi / 4 + phi2 / 2) / math.tan(math.pi / 4 + phi1 / 2)
    )
    factor = math.cos(phi1) * math.tan(math.pi / 4 + phi1 / 2) ** n / n
    rho = factor / math.tan(math.pi / 4 + phi / 2) ** n
    rho0 = factor / math.tan(math.pi / 4 + phi0 / 2) ** n
    return rho * math.sin(n * (lam - lon0)), rho0 - rho * math.cos(n * (lam - lon0))


def _stable_offset(username: str, index: int, count: int, radius: float) -> tuple[float, float]:
    """Детерминированно разложить точки по компактному диску."""
    if count <= 1:
        return 0.0, 0.0
    digest = hashlib.blake2b(username.encode("utf-8"), digest_size=4).digest()
    phase = int.from_bytes(digest, "big") / (2**32) * 2 * math.pi
    angle = phase + index * math.pi * (3 - math.sqrt(5))
    distance = radius * math.sqrt((index + 0.5) / count)
    return math.cos(angle) * distance, math.sin(angle) * distance


def _map_bounds() -> tuple[float, float, float, float]:
    # Фиксированный охват: масштаб карты не зависит от состава базы каналов.
    projected = [project_lcc(lat, lon) for lat, lon in FEDERAL_SUBJECTS.values()]
    xs = [point[0] for point in projected]
    ys = [point[1] for point in projected]
    return min(xs), min(ys), max(xs), max(ys)


def build_payload(rows: list[dict]) -> dict:
    """Чистая функция построения JSON-карты; удобна для тестирования."""
    by_region: dict[str, list[dict]] = {}
    federal_count = 0
    unknown_count = 0
    missing_coordinates = 0

    for row in rows:
        channel_type = row.get("channel_type") or "unknown"
        region = row.get("region")
        if channel_type == "federal":
            federal_count += 1
            continue
        if channel_type != "regional" or not region:
            unknown_count += 1
            continue
        if not get_coords(region):
            missing_coordinates += 1
            unknown_count += 1
            continue
        by_region.setdefault(region, []).append(row)

    min_x, min_y, max_x, max_y = _map_bounds()
    span_x = max(max_x - min_x, 1e-9)
    span_y = max(max_y - min_y, 1e-9)
    scale = (MAP_WIDTH - 2 * MAP_PADDING) / span_x
    map_height = span_y * scale + 2 * MAP_PADDING

    points = []
    region_counts = Counter()
    for region, channels in sorted(by_region.items()):
        lat, lon = get_coords(region)
        raw_x, raw_y = project_lcc(lat, lon)
        center_x = MAP_PADDING + (raw_x - min_x) * scale
        center_y = MAP_PADDING + (max_y - raw_y) * scale
        radius = min(8.0 + math.sqrt(len(channels)) * 0.9, 22.0)
        channels = sorted(channels, key=lambda ch: str(ch["username"]).casefold())

        for index, channel in enumerate(channels):
            username = str(channel["username"])
            dx, dy = _stable_offset(username, index, len(channels), radius)
            points.append({
                "id": username,
                "username": username,
                "name": channel.get("name") or username,
                "x": round(center_x + dx, 2),
                "y": round(center_y + dy, 2),
                "category": channel.get("subcategory") or channel.get("category") or "other",
                "region": region,
                "subscribers": channel.get("subscribers"),
                "channel_type": "regional",
            })
            region_counts[region] += 1

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "projection": "Lambert Conformal Conic; parallels 50°/70°; central meridian 100°",
        "width": round(MAP_WIDTH, 2),
        "height": round(map_height, 2),
        "points": points,
        "meta": {
            "total": len(rows),
            "mapped": len(points),
            "regional": sum(region_counts.values()),
            "federal": federal_count,
            "unknown": unknown_count,
            "missing_coordinates": missing_coordinates,
            "regions": dict(sorted(region_counts.items())),
        },
    }


def write_payload(payload: dict, out_path: Path = OUT_PATH) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    out_path.with_suffix(".js").write_text(
        "window.TG_GEO_MAP = " + json.dumps(payload, ensure_ascii=False) + ";\n",
        encoding="utf-8",
    )


def run() -> dict[str, int]:
    from psycopg2.extras import RealDictCursor
    from .db import connect

    conn = connect()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                """SELECT username, name, category, subcategory, region,
                          subscribers, channel_type
                     FROM channels
                    WHERE is_active = TRUE
                    ORDER BY region, username"""
            )
            rows = list(cur.fetchall())
    finally:
        conn.close()

    if not rows:
        log.warning("Нет активных каналов — сначала запустите scrape")
        return {"total": 0, "mapped": 0, "federal": 0, "unknown": 0}

    payload = build_payload(rows)
    write_payload(payload)
    meta = payload["meta"]
    log.info(
        "Карта: %(mapped)d/%(total)d каналов; региональных=%(regional)d, "
        "федеральных=%(federal)d, без геопривязки=%(unknown)d",
        meta,
    )
    if meta["missing_coordinates"]:
        log.warning("Нет координат у %d распознанных региональных каналов", meta["missing_coordinates"])
    log.info("Сохранено: %s и %s", OUT_PATH, OUT_PATH.with_suffix(".js"))
    return {key: meta[key] for key in ("total", "mapped", "federal", "unknown")}
