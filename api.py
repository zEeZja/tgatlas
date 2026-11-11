"""Опциональный HTTP API для фронтенда и внутренних инструментов."""

from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import JSONResponse
import psycopg2
from src.migrations import check_schema_ready, MigrationError

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


@app.get("/api/ready", responses={503: {"description": "Database or schema not ready"}})
def ready():
    conn = None
    try:
        conn = db.connect()
        with conn.cursor() as cursor:
            cursor.execute("SELECT set_config('statement_timeout', '3000', true)")
        status = check_schema_ready(conn)
        return {
            "status": "ready",
            "database": "ok",
            "schema_version": status["current"],
        }
    except (psycopg2.Error, MigrationError):
        # Never put connection details or SQL errors into a public health response.
        return JSONResponse(status_code=503, content={"status": "not_ready"})
    finally:
        if conn is not None:
            conn.close()


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
