-- Immutable baseline; adopt existing catalogues without replacing records.
-- Схема БД парсера Telegram-каналов.
-- Идемпотентна: можно запускать многократно через `python main.py init-db`.

CREATE TABLE IF NOT EXISTS channels (
    id              SERIAL PRIMARY KEY,
    username        TEXT UNIQUE NOT NULL,
    name            TEXT,
    category        TEXT,
    country         TEXT,
    description     TEXT,
    subscribers     INTEGER,
    is_active       BOOLEAN     NOT NULL DEFAULT TRUE,
    last_parsed_at  TIMESTAMPTZ,
    last_meta_at    TIMESTAMPTZ,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_channels_active
    ON channels (is_active) WHERE is_active = TRUE;

CREATE TABLE IF NOT EXISTS posts (
    id              BIGSERIAL PRIMARY KEY,
    channel_id      INTEGER     NOT NULL REFERENCES channels(id) ON DELETE CASCADE,
    tg_message_id   BIGINT      NOT NULL,
    url             TEXT        NOT NULL,
    text            TEXT,
    title           TEXT,
    posted_at       TIMESTAMPTZ,
    views           INTEGER,
    fetched_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (channel_id, tg_message_id)
);

CREATE INDEX IF NOT EXISTS idx_posts_channel_posted
    ON posts (channel_id, posted_at DESC);

CREATE INDEX IF NOT EXISTS idx_posts_posted_at
    ON posts (posted_at DESC);

-- ─── Миграция для уже существующих баз ─────────────────────────────────
-- Безопасно: ADD COLUMN IF NOT EXISTS ничего не сломает, если колонка есть.

ALTER TABLE channels ADD COLUMN IF NOT EXISTS description   TEXT;
ALTER TABLE channels ADD COLUMN IF NOT EXISTS subscribers   INTEGER;
ALTER TABLE channels ADD COLUMN IF NOT EXISTS last_meta_at  TIMESTAMPTZ;
ALTER TABLE channels ADD COLUMN IF NOT EXISTS subcategory   TEXT;
ALTER TABLE channels ADD COLUMN IF NOT EXISTS region        TEXT;
ALTER TABLE channels ADD COLUMN IF NOT EXISTS region_hint   TEXT;
ALTER TABLE channels ADD COLUMN IF NOT EXISTS source        TEXT DEFAULT 'manual';

-- Новое в v0.3: тип канала (federal / regional / unknown)
ALTER TABLE channels ADD COLUMN IF NOT EXISTS channel_type  TEXT;

-- Прозрачность автоматической классификации: уверенность, источник и проверка.
ALTER TABLE channels ADD COLUMN IF NOT EXISTS region_confidence REAL;
ALTER TABLE channels ADD COLUMN IF NOT EXISTS region_source TEXT;
ALTER TABLE channels ADD COLUMN IF NOT EXISTS region_reason TEXT;
ALTER TABLE channels ADD COLUMN IF NOT EXISTS region_candidates JSONB NOT NULL DEFAULT '[]'::jsonb;
ALTER TABLE channels ADD COLUMN IF NOT EXISTS region_review_required BOOLEAN NOT NULL DEFAULT FALSE;

CREATE INDEX IF NOT EXISTS idx_channels_type
    ON channels (channel_type) WHERE channel_type IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_channels_region_review
    ON channels (region_review_required) WHERE region_review_required = TRUE;


-- Идентификатор Telegram устойчивее username: username может измениться.
ALTER TABLE channels ADD COLUMN IF NOT EXISTS telegram_id BIGINT;
CREATE UNIQUE INDEX IF NOT EXISTS idx_channels_telegram_id
    ON channels (telegram_id) WHERE telegram_id IS NOT NULL;

-- Единый реестр обнаруженных кандидатов из внешних каталогов и подписок аккаунта.
CREATE TABLE IF NOT EXISTS channel_candidates (
    username        TEXT PRIMARY KEY,
    telegram_id     BIGINT,
    name            TEXT,
    category        TEXT,
    subcategory     TEXT,
    country         TEXT,
    region_hint     TEXT,
    description     TEXT,
    subscribers     INTEGER,
    validation_note TEXT,
    status          TEXT NOT NULL DEFAULT 'pending'
                    CHECK (status IN ('pending', 'validated', 'imported', 'rejected')),
    first_seen_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_seen_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    validated_at    TIMESTAMPTZ,
    imported_at     TIMESTAMPTZ,
    imported_channel_id INTEGER REFERENCES channels(id) ON DELETE SET NULL,
    raw_data        JSONB NOT NULL DEFAULT '{}'::jsonb,
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_channel_candidates_status
    ON channel_candidates (status, last_seen_at DESC);

-- Причина отклонения/ожидания и безопасная миграция существующей БД.
ALTER TABLE channel_candidates ADD COLUMN IF NOT EXISTS validation_note TEXT;

-- Источник хранится отдельно: один канал может быть найден в нескольких местах.
CREATE TABLE IF NOT EXISTS channel_candidate_sources (
    username        TEXT NOT NULL REFERENCES channel_candidates(username) ON DELETE CASCADE,
    source_name     TEXT NOT NULL,
    source_ref      TEXT NOT NULL DEFAULT '',
    source_url      TEXT,
    first_seen_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_seen_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    raw_data        JSONB NOT NULL DEFAULT '{}'::jsonb,
    PRIMARY KEY (username, source_name, source_ref)
);

-- История происхождения уже импортированных каналов.
CREATE TABLE IF NOT EXISTS channel_sources (
    id              BIGSERIAL PRIMARY KEY,
    channel_id      INTEGER NOT NULL REFERENCES channels(id) ON DELETE CASCADE,
    source_name     TEXT NOT NULL,
    source_ref      TEXT NOT NULL DEFAULT '',
    source_url      TEXT,
    first_seen_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_seen_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    raw_data        JSONB NOT NULL DEFAULT '{}'::jsonb,
    UNIQUE (channel_id, source_name, source_ref)
);

CREATE INDEX IF NOT EXISTS idx_channel_sources_channel
    ON channel_sources (channel_id);

