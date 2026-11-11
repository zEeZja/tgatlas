"""Семантическая раскладка каналов: эмбеддинги + снижение размерности → 2D.

Изменения по сравнению с предыдущей версией:
  — параметры UMAP и ATTRACTION берутся из конфига (.env);
  — параметры автоматически адаптируются к размеру набора;
  — для t-SNE perplexity пересчитывается под n_samples.

Особенности подхода:
  — в текст для эмбеддинга включается регион — это группирует каналы
    одного региона семантически (без этого Москва разлетается);
  — после снижения размерности точки одного региона мягко стягиваются
    к центру своего региона (ATTRACTION = доля сдвига, 0..1).

UMAP — если установлен, иначе t-SNE из sklearn.
Пишет semantic_map.json и semantic_map.js (для file://).
"""

import json
import logging
from pathlib import Path

from psycopg2.extras import RealDictCursor

from . import config
from .db import connect

log = logging.getLogger(__name__)

MODEL_NAME = "cointegrated/rubert-tiny2"
OUT_PATH = Path(__file__).resolve().parent.parent / "web" / "data" / "semantic_map.json"


def _reduce_umap(embeddings):
    import umap

    n_samples = len(embeddings)
    if n_samples < 3:
        raise ValueError("UMAP требует не менее трёх каналов")
    # n_neighbors не может быть больше, чем n_samples - 1
    n_neighbors = min(config.UMAP_N_NEIGHBORS, max(2, n_samples - 1))
    log.info(
        "Снижаю размерность UMAP (n_neighbors=%d, min_dist=%.2f)…",
        n_neighbors,
        config.UMAP_MIN_DIST,
    )
    reducer = umap.UMAP(
        n_components=2,
        n_neighbors=n_neighbors,
        min_dist=config.UMAP_MIN_DIST,
        metric="cosine",
        random_state=42,
    )
    return reducer.fit_transform(embeddings)


def _reduce_tsne(embeddings):
    from sklearn.manifold import TSNE

    n_samples = len(embeddings)
    if n_samples < 2:
        raise ValueError("t-SNE требует не менее двух каналов")
    perplexity = float(min(30.0, max(0.5, n_samples / 3.0), n_samples - 1))
    log.info("Снижаю размерность t-SNE (perplexity=%.1f)…", perplexity)
    tsne = TSNE(
        n_components=2,
        perplexity=perplexity,
        init="pca",
        learning_rate="auto",
        metric="cosine",
        random_state=42,
        n_jobs=-1,
    )
    return tsne.fit_transform(embeddings)


def _attract_by_region(coords, regions, alpha=None):
    """Притяжение точек к центру их региона."""
    if alpha is None:
        alpha = config.SEMANTIC_ATTRACTION
    if alpha <= 0:
        return coords

    import numpy as np

    coords = np.array(coords, copy=True)

    by_region = {}
    for i, r in enumerate(regions):
        if not r:
            continue
        by_region.setdefault(r, []).append(i)

    touched = 0
    for region, idxs in by_region.items():
        if len(idxs) < 2:
            continue
        center = coords[idxs].mean(axis=0)
        for i in idxs:
            coords[i] = coords[i] * (1 - alpha) + center * alpha
        touched += 1

    log.info(
        "Притяжение по регионам: alpha=%.2f, регионов с 2+ точками: %d", alpha, touched
    )
    return coords


def run():
    conn = connect()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                """
                SELECT username, name, description, category, subcategory,
                       region, subscribers
                  FROM channels
                 WHERE is_active = TRUE
                 ORDER BY username
                """
            )
            rows = cur.fetchall()
    finally:
        conn.close()

    log.info("Каналов: %d", len(rows))
    if not rows:
        log.warning("Нет каналов для раскладки — сначала запустите scrape")
        return

    # ─── Текст для эмбеддинга — регион впереди для веса ────────────────
    texts = []
    for r in rows:
        region = (r["region"] or "").strip()
        name = (r["name"] or "").strip()
        desc = (r["description"] or "").strip()
        if region:
            text = f"{region}. {region}. {name}. {desc}".strip()
        else:
            text = f"{name}. {desc}".strip()
        texts.append(text or r["username"])

    if len(rows) < 3:
        import numpy as np

        coords = np.array([[float(i) * 200.0, 0.0] for i in range(len(rows))])
    else:
        from sentence_transformers import SentenceTransformer

        log.info("Загружаю модель %s…", MODEL_NAME)
        model = SentenceTransformer(MODEL_NAME)

        log.info("Считаю эмбеддинги…")
        embeddings = model.encode(texts, show_progress_bar=True, batch_size=32)

        coords = None
        try:
            coords = _reduce_umap(embeddings)
        except Exception as e:
            log.warning("UMAP недоступен (%s), использую t-SNE", e)
            coords = _reduce_tsne(embeddings)

    regions = [r["region"] for r in rows]
    coords = _attract_by_region(coords, regions)

    # Нормализация
    x_min, y_min = coords.min(axis=0)
    x_max, y_max = coords.max(axis=0)
    span = max(x_max - x_min, y_max - y_min) or 1
    coords = (coords - [x_min, y_min]) / span * 1000

    result = []
    for r, (x, y) in zip(rows, coords):
        result.append(
            {
                "id": r["username"],
                "username": r["username"],
                "name": r["name"] or r["username"],
                "x": round(float(x), 2),
                "y": round(float(y), 2),
                "category": r["subcategory"] or r["category"] or "other",
                "region": r["region"],
                "subscribers": r["subscribers"],
            }
        )

    payload = {"points": result}
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)

    OUT_PATH.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    js_path = OUT_PATH.with_suffix(".js")
    js_path.write_text(
        "window.TG_SEMANTIC = " + json.dumps(payload, ensure_ascii=False) + ";\n",
        encoding="utf-8",
    )

    log.info("Сохранено %d точек", len(result))
    log.info("  → %s", OUT_PATH)
    log.info("  → %s", js_path)
