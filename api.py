"""Опциональный HTTP API для фронтенда и внутренних инструментов."""

from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles

from src import db
from src.exporter import channel_rows

ROOT = Path(__file__).resolve().parent
WEB_DIR = ROOT / "web"
GEO_MAP_FILE = WEB_DIR / "data" / "geo_map.json"

app = FastAPI(
    title="Unofficial Channel Atlas API",
    version=(ROOT / "VERSION").read_text(encoding="utf-8").strip(),
)


@app.get("/api/health")
def health() -> dict:
    return {"status": "ok"}


@app.get("/api/channels")
def channels() -> dict:
    conn = db.connect()
    try:
        rows = db.get_channels_with_meta(conn)
        return {"channels": channel_rows(rows)}
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
