"""Опциональный HTTP API для фронтенда и внутренних инструментов."""

from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles

from src import db
from src.exporter import _channel_to_row

ROOT = Path(__file__).resolve().parent
WEB_DIR = ROOT / "web"
GEO_MAP_FILE = WEB_DIR / "data" / "geo_map.json"

app = FastAPI(title="Unofficial Channel Atlas API", version="0.7.0")


@app.get("/api/health")
def health() -> dict:
    return {"status": "ok"}


@app.get("/api/channels")
def channels() -> dict:
    conn = db.connect()
    try:
        rows = db.get_channels_with_meta(conn)
        return {"channels": [_channel_to_row(dict(row)) for row in rows]}
    finally:
        conn.close()


@app.get("/api/geo-map")
def geo_map() -> dict:
    if not GEO_MAP_FILE.exists():
        raise HTTPException(
            status_code=404,
            detail="Geo map is missing. Run `python main.py build-geo-map` first.",
        )
    import json
    return json.loads(GEO_MAP_FILE.read_text(encoding="utf-8"))


# Mount after API routes so /api/* is not swallowed by StaticFiles.
app.mount("/", StaticFiles(directory=str(WEB_DIR), html=True), name="web")
