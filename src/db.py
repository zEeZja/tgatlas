"""Операции с PostgreSQL. Без ORM — psycopg2 + явный SQL.

Изменения по сравнению с предыдущей версией:
  — добавлен контекстный менеджер `transaction(conn)`;
  — функции записи (upsert_channel, insert_posts, mark_parsed,
    update_channel_meta) больше НЕ коммитят сами — ожидают, что вызывающий
    код обернёт их в `with transaction(conn):`;
  — `init_schema` по-прежнему коммитит сам: DDL удобнее держать отдельно.

Это даёт атомарность на уровне одного канала: если что-то упало на середине
обхода, весь канал откатывается целиком, и в БД не остаётся «полуканала».
"""

import logging
from contextlib import contextmanager
from typing import Iterable, List

import psycopg2
from psycopg2.extras import RealDictCursor, Json

from . import config
from .models import Channel, Post
from .identity import normalize_username
from .metrics import parse_count

log = logging.getLogger(__name__)


def connect():
    """Открыть соединение с PostgreSQL."""
    return psycopg2.connect(**config.PG)


@contextmanager
def transaction(conn):
    """Транзакция: commit при успехе, rollback при исключении."""
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise


@contextmanager
def _cursor(conn):
    cur = conn.cursor(cursor_factory=RealDictCursor)
    try:
        yield cur
    finally:
        cur.close()


def init_schema(conn) -> list[str]:
    """Apply versioned migrations atomically; safe for pre-migration catalogues."""
    from .migrations import apply_migrations

    return apply_migrations(conn)


# ─── Запись (без commit — вызывающий оборачивает в transaction) ────────


def upsert_channel(conn, ch: Channel) -> int:
    """Вставить или обновить канал, предпочитая устойчивый Telegram ID."""
    username = normalize_username(ch.username)
    if not username:
        raise ValueError(f"Некорректный публичный username канала: {ch.username!r}")

    channel_id = None
    with _cursor(conn) as cur:
        # Username может меняться. Если Telegram ID уже известен, обновляем
        # существующую сущность по ID, а не создаём вторую запись.
        if ch.telegram_id:
            cur.execute(
                "SELECT id, username FROM channels WHERE telegram_id = %s FOR UPDATE",
                (ch.telegram_id,),
            )
            by_telegram_id = cur.fetchone()
            if by_telegram_id:
                cur.execute(
                    "SELECT id, telegram_id FROM channels WHERE username = %s FOR UPDATE",
                    (username,),
                )
                by_username = cur.fetchone()
                if by_username and by_username["id"] != by_telegram_id["id"]:
                    raise ValueError(
                        f"Username @{username} уже связан с другой записью channels "
                        f"(id={by_username['id']}); автоматическое слияние отменено"
                    )
                cur.execute(
                    """
                    UPDATE channels
                       SET username = %s,
                           name = COALESCE(%s, name),
                           category = COALESCE(%s, category),
                           country = COALESCE(%s, country),
                           subcategory = COALESCE(%s, subcategory),
                           region_hint = COALESCE(%s, region_hint),
                           source = COALESCE(%s, source),
                           description = COALESCE(%s, description),
                           subscribers = COALESCE(%s, subscribers),
                           updated_at = NOW()
                     WHERE id = %s
                    RETURNING id
                    """,
                    (
                        username,
                        ch.name,
                        ch.category,
                        ch.country,
                        ch.subcategory,
                        ch.region_hint,
                        ch.source,
                        ch.description,
                        ch.subscribers,
                        by_telegram_id["id"],
                    ),
                )
                channel_id = cur.fetchone()["id"]

        if channel_id is None:
            cur.execute(
                """
                INSERT INTO channels
                    (username, name, category, country, subcategory, region_hint,
                     source, telegram_id, description, subscribers)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (username) DO UPDATE
                   SET name        = COALESCE(EXCLUDED.name, channels.name),
                       category    = COALESCE(EXCLUDED.category, channels.category),
                       country     = COALESCE(EXCLUDED.country, channels.country),
                       subcategory = COALESCE(EXCLUDED.subcategory, channels.subcategory),
                       region_hint = COALESCE(EXCLUDED.region_hint, channels.region_hint),
                       source      = COALESCE(EXCLUDED.source, channels.source),
                       telegram_id = COALESCE(EXCLUDED.telegram_id, channels.telegram_id),
                       description = COALESCE(EXCLUDED.description, channels.description),
                       subscribers = COALESCE(EXCLUDED.subscribers, channels.subscribers),
                       updated_at  = NOW()
                 WHERE channels.telegram_id IS NULL
                    OR EXCLUDED.telegram_id IS NULL
                    OR channels.telegram_id = EXCLUDED.telegram_id
                RETURNING id
                """,
                (
                    username,
                    ch.name,
                    ch.category,
                    ch.country,
                    ch.subcategory,
                    ch.region_hint,
                    ch.source,
                    ch.telegram_id,
                    ch.description,
                    ch.subscribers,
                ),
            )
            row = cur.fetchone()
            if row is None:
                raise ValueError(
                    f"Username @{username} уже связан с другим Telegram ID; "
                    "автоматическое объединение отменено"
                )
            channel_id = row["id"]

        if ch.source:
            cur.execute(
                """
                INSERT INTO channel_sources (channel_id, source_name, source_ref)
                VALUES (%s, %s, %s)
                ON CONFLICT (channel_id, source_name, source_ref)
                DO UPDATE SET last_seen_at = NOW()
                """,
                (channel_id, ch.source, str(ch.telegram_id or username)),
            )
    return channel_id


def update_channel_meta(
    conn,
    channel_id: int,
    description: str = None,
    subscribers: int = None,
    name: str = None,
    telegram_id: int = None,
) -> None:
    """Обновить только известные метаданные, не затирая неполными ответами."""
    with _cursor(conn) as cur:
        cur.execute(
            """
            UPDATE channels
               SET name         = COALESCE(%s, name),
                   description  = COALESCE(%s, description),
                   subscribers  = COALESCE(%s, subscribers),
                   telegram_id  = COALESCE(telegram_id, %s),
                   last_meta_at = NOW(),
                   updated_at   = NOW()
             WHERE id = %s
            """,
            (name, description, subscribers, telegram_id, channel_id),
        )


def insert_posts(conn, posts: Iterable[Post]) -> int:
    """Вставить посты. Вернуть количество реально новых (не дублей)."""
    posts = list(posts)
    if not posts:
        return 0

    from psycopg2.extras import execute_values

    rows = [
        (p.channel_id, p.tg_message_id, p.url, p.text, p.title, p.posted_at, p.views)
        for p in posts
    ]
    with _cursor(conn) as cur:
        result = execute_values(
            cur,
            """
            INSERT INTO posts (channel_id, tg_message_id, url, text, title, posted_at, views)
            VALUES %s ON CONFLICT (channel_id, tg_message_id) DO NOTHING RETURNING id
        """,
            rows,
            page_size=500,
            fetch=True,
        )
    return len(result)


def get_post_watermarks(conn, channel_ids=None) -> dict:
    if channel_ids is not None and not channel_ids:
        return {}
    with _cursor(conn) as cur:
        query = "SELECT channel_id, MAX(tg_message_id) AS last_id FROM posts"
        if channel_ids is None:
            cur.execute(query + " GROUP BY channel_id")
        else:
            cur.execute(
                query + " WHERE channel_id = ANY(%s) GROUP BY channel_id",
                (list(channel_ids),),
            )
        return {r["channel_id"]: r["last_id"] for r in cur.fetchall()}


def mark_parsed(conn, channel_id: int) -> None:
    """Отметить время последнего успешного обхода канала."""
    with _cursor(conn) as cur:
        cur.execute(
            "UPDATE channels SET last_parsed_at = NOW() WHERE id = %s",
            (channel_id,),
        )


# ─── Чтение (без commit) ───────────────────────────────────────────────


def get_known_usernames(conn):
    """Include inactive rows: catalogue sync must not overwrite or reactivate them."""
    with _cursor(conn) as cur:
        cur.execute("SELECT username FROM channels")
        return {row["username"].lower() for row in cur.fetchall()}


def get_active_channels(conn) -> List[dict]:
    with _cursor(conn) as cur:
        cur.execute(
            """
            SELECT id, telegram_id, username, name, category, subcategory,
                   country, region, region_hint, source
              FROM channels WHERE is_active = TRUE
             ORDER BY username
            """
        )
        return list(cur.fetchall())


def get_channels_with_meta(conn) -> List[dict]:
    with _cursor(conn) as cur:
        cur.execute(
            """
            SELECT ch.id, ch.telegram_id, ch.username, ch.name, ch.category,
                   ch.subcategory, ch.region, ch.region_hint, ch.country,
                   ch.description, ch.subscribers, ch.source, ch.channel_type,
                   ch.region_confidence, ch.region_source, ch.region_reason,
                   ch.region_candidates, ch.region_review_required,
                   ch.last_parsed_at, ch.last_meta_at,
                   COALESCE(array_agg(DISTINCT cs.source_name)
                       FILTER (WHERE cs.source_name IS NOT NULL), ARRAY[]::TEXT[]) AS sources
              FROM channels ch
              LEFT JOIN channel_sources cs ON cs.channel_id = ch.id
             WHERE ch.is_active = TRUE
             GROUP BY ch.id
             ORDER BY ch.username
            """
        )
        return list(cur.fetchall())


def stats(conn) -> dict:
    with _cursor(conn) as cur:
        cur.execute("SELECT COUNT(*) AS c FROM channels")
        channels_count = cur.fetchone()["c"]

        cur.execute("SELECT COUNT(*) AS c FROM posts")
        posts_count = cur.fetchone()["c"]

        cur.execute(
            """
            SELECT ch.username,
                   ch.name,
                   ch.subscribers,
                   COUNT(p.id)  AS post_count,
                   MAX(p.posted_at) AS last_post_at
              FROM channels ch
              LEFT JOIN posts p ON p.channel_id = ch.id
             GROUP BY ch.id, ch.username, ch.name, ch.subscribers
             ORDER BY post_count DESC, ch.username
            """
        )
        per_channel = list(cur.fetchall())

    return {
        "channels": channels_count,
        "posts": posts_count,
        "per_channel": per_channel,
    }


# ─── Единый каталог кандидатов и история источников ────────────────────


def upsert_candidate(
    conn,
    candidate: dict,
    source_name: str,
    source_ref: str = "",
    source_url: str = None,
    status: str = "pending",
) -> bool:
    """Добавить/обновить кандидата и записать отдельный источник.

    Кандидат с известной аудиторией ниже config.MIN_CHANNEL_SUBSCRIBERS сразу
    получает rejected. Статус validated без числа подписчиков не допускается.
    """
    from .audience import normalize_candidate_state

    username = normalize_username(candidate.get("username") or candidate.get("url"))
    if not username:
        return False
    if status not in {"pending", "validated"}:
        raise ValueError("Новый кандидат может иметь статус pending или validated")

    raw = {
        key: value for key, value in dict(candidate).items() if value not in (None, "")
    }
    name = candidate.get("name") or candidate.get("title") or None
    subscribers = parse_count(candidate.get("subscribers"))
    candidate_status, validation_note = normalize_candidate_state(
        status,
        subscribers,
        config.MIN_CHANNEL_SUBSCRIBERS,
    )
    telegram_id = candidate.get("telegram_id")
    try:
        telegram_id = int(telegram_id) if telegram_id not in (None, "") else None
    except (TypeError, ValueError):
        telegram_id = None

    with _cursor(conn) as cur:
        cur.execute(
            """
            INSERT INTO channel_candidates
                (username, telegram_id, name, category, subcategory, country,
                 region_hint, description, subscribers, status, validation_note, raw_data)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (username) DO UPDATE SET
                telegram_id = COALESCE(EXCLUDED.telegram_id, channel_candidates.telegram_id),
                name = COALESCE(EXCLUDED.name, channel_candidates.name),
                category = COALESCE(EXCLUDED.category, channel_candidates.category),
                subcategory = COALESCE(EXCLUDED.subcategory, channel_candidates.subcategory),
                country = COALESCE(EXCLUDED.country, channel_candidates.country),
                region_hint = COALESCE(EXCLUDED.region_hint, channel_candidates.region_hint),
                description = COALESCE(EXCLUDED.description, channel_candidates.description),
                subscribers = COALESCE(EXCLUDED.subscribers, channel_candidates.subscribers),
                raw_data = channel_candidates.raw_data || EXCLUDED.raw_data,
                status = CASE
                    WHEN channel_candidates.status = 'imported' THEN 'imported'
                    WHEN EXCLUDED.status = 'rejected' THEN 'rejected'
                    WHEN channel_candidates.status = 'rejected'
                         AND EXCLUDED.subscribers >= %s THEN EXCLUDED.status
                    WHEN channel_candidates.status = 'rejected' THEN 'rejected'
                    WHEN EXCLUDED.status = 'validated' THEN 'validated'
                    ELSE channel_candidates.status
                END,
                validation_note = CASE
                    WHEN channel_candidates.status = 'rejected'
                         AND channel_candidates.validation_note LIKE 'below_min_subscribers:%%'
                         AND EXCLUDED.validation_note = 'subscriber_count_unknown'
                        THEN channel_candidates.validation_note
                    WHEN EXCLUDED.validation_note IS NOT NULL THEN EXCLUDED.validation_note
                    WHEN EXCLUDED.subscribers >= %s THEN NULL
                    ELSE channel_candidates.validation_note
                END,
                last_seen_at = NOW(), updated_at = NOW()
            RETURNING (xmax = 0) AS inserted, imported_channel_id
            """,
            (
                username,
                telegram_id,
                name,
                candidate.get("category"),
                candidate.get("subcategory"),
                candidate.get("country"),
                candidate.get("region_hint") or candidate.get("region"),
                candidate.get("description"),
                subscribers,
                candidate_status,
                validation_note,
                Json(raw),
                config.MIN_CHANNEL_SUBSCRIBERS,
                config.MIN_CHANNEL_SUBSCRIBERS,
            ),
        )
        candidate_row = cur.fetchone()
        inserted = bool(candidate_row["inserted"])
        imported_channel_id = candidate_row["imported_channel_id"]
        cur.execute(
            """
            INSERT INTO channel_candidate_sources
                (username, source_name, source_ref, source_url, raw_data)
            VALUES (%s, %s, %s, %s, %s)
            ON CONFLICT (username, source_name, source_ref)
            DO UPDATE SET last_seen_at = NOW(),
                          source_url = COALESCE(EXCLUDED.source_url, channel_candidate_sources.source_url),
                          raw_data = channel_candidate_sources.raw_data || EXCLUDED.raw_data
            """,
            (username, source_name, str(source_ref or ""), source_url, Json(raw)),
        )
        # Новые низкие показатели не должны портить уже импортированную запись.
        safe_subscribers = (
            subscribers
            if subscribers is not None and subscribers >= config.MIN_CHANNEL_SUBSCRIBERS
            else None
        )
        if imported_channel_id is not None:
            cur.execute(
                """
                UPDATE channels AS target
                   SET telegram_id = CASE
                           WHEN target.telegram_id IS NULL
                            AND %s IS NOT NULL
                            AND NOT EXISTS (
                                SELECT 1 FROM channels AS other
                                 WHERE other.telegram_id = %s
                                   AND other.id <> target.id
                            )
                           THEN %s ELSE target.telegram_id END,
                       name = COALESCE(%s, target.name),
                       category = COALESCE(%s, target.category),
                       country = COALESCE(%s, target.country),
                       subcategory = COALESCE(%s, target.subcategory),
                       region_hint = COALESCE(%s, target.region_hint),
                       description = COALESCE(%s, target.description),
                       subscribers = COALESCE(%s, target.subscribers),
                       updated_at = NOW()
                 WHERE target.id = %s
                """,
                (
                    telegram_id,
                    telegram_id,
                    telegram_id,
                    name,
                    candidate.get("category"),
                    candidate.get("country"),
                    candidate.get("subcategory"),
                    candidate.get("region_hint") or candidate.get("region"),
                    candidate.get("description"),
                    safe_subscribers,
                    imported_channel_id,
                ),
            )
            cur.execute(
                """
                INSERT INTO channel_sources
                    (channel_id, source_name, source_ref, source_url, raw_data)
                VALUES (%s, %s, %s, %s, %s)
                ON CONFLICT (channel_id, source_name, source_ref)
                DO UPDATE SET last_seen_at = NOW(),
                              source_url = COALESCE(EXCLUDED.source_url, channel_sources.source_url),
                              raw_data = channel_sources.raw_data || EXCLUDED.raw_data
                """,
                (
                    imported_channel_id,
                    source_name,
                    str(source_ref or ""),
                    source_url,
                    Json(raw),
                ),
            )
    return inserted


def list_candidates(conn, status: str = "pending", limit: int = 100) -> List[dict]:
    with _cursor(conn) as cur:
        cur.execute(
            """
            SELECT c.username, c.telegram_id, c.name, c.category, c.subcategory,
                   c.country, c.region_hint, c.description, c.subscribers,
                   c.status, c.validation_note, c.first_seen_at, c.last_seen_at,
                   COALESCE(array_agg(DISTINCT s.source_name)
                       FILTER (WHERE s.source_name IS NOT NULL), ARRAY[]::TEXT[]) AS sources
              FROM channel_candidates c
              LEFT JOIN channel_candidate_sources s USING (username)
             WHERE (%s = 'all' OR c.status = %s)
             GROUP BY c.username
             ORDER BY c.last_seen_at DESC, c.username
             LIMIT %s
            """,
            (status, status, limit),
        )
        return list(cur.fetchall())


def candidate_stats(conn) -> dict:
    with _cursor(conn) as cur:
        cur.execute(
            "SELECT status, COUNT(*) AS count FROM channel_candidates GROUP BY status"
        )
        rows = cur.fetchall()
        cur.execute(
            "SELECT COUNT(DISTINCT username) AS count FROM channel_candidate_sources"
        )
        source_linked = cur.fetchone()["count"]
    result = {row["status"]: row["count"] for row in rows}
    result["source_linked"] = source_linked
    result["total"] = sum(
        result.get(k, 0) for k in ("pending", "validated", "imported", "rejected")
    )
    return result


def update_candidate_validation(
    conn,
    username: str,
    *,
    status: str,
    name: str = None,
    description: str = None,
    subscribers: int = None,
    validation_note: str = None,
) -> None:
    from .audience import normalize_candidate_state

    normalized_status, policy_note = normalize_candidate_state(
        status,
        subscribers,
        config.MIN_CHANNEL_SUBSCRIBERS,
    )
    note = validation_note or policy_note
    with _cursor(conn) as cur:
        cur.execute(
            """
            UPDATE channel_candidates
               SET status = %s,
                   name = COALESCE(%s, name),
                   description = COALESCE(%s, description),
                   subscribers = COALESCE(%s, subscribers),
                   validation_note = %s,
                   validated_at = CASE WHEN %s = 'validated' THEN NOW() ELSE validated_at END,
                   updated_at = NOW()
             WHERE username = %s
            """,
            (
                normalized_status,
                name,
                description,
                subscribers,
                note,
                normalized_status,
                username,
            ),
        )


def import_candidates(
    conn,
    usernames: Iterable[str] = None,
    limit: int = None,
    include_pending: bool = False,
    min_subscribers: int = None,
) -> int:
    """Импортировать кандидатов только при известной аудитории >= порога.

    --include-pending разрешает пропустить проверку страницы, но не обходит
    обязательный фильтр минимального числа подписчиков.
    """
    minimum = (
        config.MIN_CHANNEL_SUBSCRIBERS
        if min_subscribers is None
        else int(min_subscribers)
    )
    if minimum < 0:
        raise ValueError("min_subscribers не может быть отрицательным")
    wanted = [normalize_username(u) for u in usernames] if usernames else None
    wanted = [u for u in (wanted or []) if u]
    statuses = ("validated", "pending") if include_pending else ("validated",)
    with _cursor(conn) as cur:
        if wanted:
            cur.execute(
                """
                SELECT * FROM channel_candidates
                 WHERE username = ANY(%s) AND status = ANY(%s)
                   AND subscribers IS NOT NULL AND subscribers >= %s
                 ORDER BY username
                """,
                (wanted, list(statuses), minimum),
            )
        else:
            cur.execute(
                """
                SELECT * FROM channel_candidates
                 WHERE status = ANY(%s)
                   AND subscribers IS NOT NULL AND subscribers >= %s
                 ORDER BY last_seen_at DESC, username
                 LIMIT %s
                """,
                (list(statuses), minimum, limit or 1000000),
            )
        rows = list(cur.fetchall())

    imported = 0
    for row in rows:
        from .models import Channel

        channel = Channel(
            username=row["username"],
            name=row["name"],
            category=row["category"],
            country=row["country"],
            subcategory=row["subcategory"],
            region_hint=row["region_hint"],
            source="candidate_import",
            telegram_id=row["telegram_id"],
            description=row["description"],
            subscribers=row["subscribers"],
        )
        channel_id = upsert_channel(conn, channel)
        with _cursor(conn) as cur:
            cur.execute(
                """
                INSERT INTO channel_sources
                    (channel_id, source_name, source_ref, source_url, raw_data)
                SELECT %s, source_name, source_ref, source_url, raw_data
                  FROM channel_candidate_sources WHERE username = %s
                ON CONFLICT (channel_id, source_name, source_ref)
                DO UPDATE SET last_seen_at = NOW(), raw_data = channel_sources.raw_data || EXCLUDED.raw_data
                """,
                (channel_id, row["username"]),
            )
            cur.execute(
                """
                UPDATE channel_candidates
                   SET status = 'imported', imported_at = NOW(),
                       imported_channel_id = %s, validation_note = NULL,
                       updated_at = NOW()
                 WHERE username = %s
                """,
                (channel_id, row["username"]),
            )
        imported += 1
    return imported


def get_scrape_checkpoints(conn) -> dict:
    with _cursor(conn) as cur:
        cur.execute("SELECT channel_id, before_id, target_id FROM scrape_checkpoints")
        return {
            r["channel_id"]: (r["before_id"], r["target_id"]) for r in cur.fetchall()
        }


def save_scrape_checkpoint(conn, channel_id, checkpoint):
    if checkpoint is False:
        return
    with _cursor(conn) as cur:
        if checkpoint is None:
            cur.execute(
                "DELETE FROM scrape_checkpoints WHERE channel_id=%s", (channel_id,)
            )
        else:
            cur.execute(
                """INSERT INTO scrape_checkpoints(channel_id,before_id,target_id)
                VALUES (%s,%s,%s) ON CONFLICT(channel_id) DO UPDATE
                SET before_id=EXCLUDED.before_id,target_id=EXCLUDED.target_id""",
                (channel_id, *checkpoint),
            )
