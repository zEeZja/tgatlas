-- Durable resume cursor for bounded incremental public-preview polling.
CREATE TABLE IF NOT EXISTS scrape_checkpoints (
    channel_id BIGINT PRIMARY KEY REFERENCES channels(id) ON DELETE CASCADE,
    before_id BIGINT NOT NULL,
    target_id BIGINT NOT NULL
);
