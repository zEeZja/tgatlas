-- Merge legacy case-only duplicates without losing their original evidence.
-- All changes (including the archive) share the migration transaction.
LOCK TABLE channels, posts, channel_sources, channel_candidates,
    scrape_checkpoints IN ACCESS EXCLUSIVE MODE;

CREATE TABLE IF NOT EXISTS channel_merge_archive (
    id BIGSERIAL PRIMARY KEY,
    canonical_username TEXT NOT NULL,
    kept_channel_id INTEGER NOT NULL,
    archived_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    records JSONB NOT NULL
);

DO $$
DECLARE
    duplicate RECORD;
    old_channel channels%ROWTYPE;
    keeper INTEGER;
BEGIN
    -- A recycled username with different known Telegram IDs is not a safe merge.
    IF EXISTS (
        SELECT 1 FROM channels GROUP BY lower(username)
        HAVING count(DISTINCT telegram_id) > 1
    ) THEN
        RAISE EXCEPTION 'Conflicting Telegram IDs for a case-insensitive username; manual review required';
    END IF;

    FOR duplicate IN
        SELECT lower(username) AS username, array_agg(id ORDER BY id) AS ids
        FROM channels GROUP BY lower(username) HAVING count(*) > 1
    LOOP
        -- Keep the oldest internal identity so existing external references survive.
        keeper := duplicate.ids[1];
        INSERT INTO channel_merge_archive(canonical_username, kept_channel_id, records)
        SELECT duplicate.username, keeper, jsonb_build_object(
            'channels', (SELECT jsonb_agg(to_jsonb(c)) FROM channels c WHERE id = ANY(duplicate.ids)),
            'posts', (SELECT COALESCE(jsonb_agg(to_jsonb(p)), '[]'::jsonb) FROM posts p WHERE channel_id = ANY(duplicate.ids)),
            'sources', (SELECT COALESCE(jsonb_agg(to_jsonb(s)), '[]'::jsonb) FROM channel_sources s WHERE channel_id = ANY(duplicate.ids)),
            'checkpoints', (SELECT COALESCE(jsonb_agg(to_jsonb(s)), '[]'::jsonb) FROM scrape_checkpoints s WHERE channel_id = ANY(duplicate.ids)),
            'candidates', (SELECT COALESCE(jsonb_agg(to_jsonb(c)), '[]'::jsonb) FROM channel_candidates c WHERE imported_channel_id = ANY(duplicate.ids))
        );

        FOR old_channel IN
            SELECT * FROM channels WHERE id = ANY(duplicate.ids) AND id <> keeper
            ORDER BY last_meta_at NULLS FIRST, id
        LOOP
            -- Move the unique Telegram ID before assigning it to the keeper.
            UPDATE channels SET telegram_id = NULL WHERE id = old_channel.id;
            UPDATE channels SET
                telegram_id = COALESCE(telegram_id, old_channel.telegram_id),
                name = CASE WHEN COALESCE(old_channel.last_meta_at, '-infinity') > COALESCE(last_meta_at, '-infinity') THEN COALESCE(old_channel.name, name) ELSE COALESCE(name, old_channel.name) END,
                description = CASE WHEN COALESCE(old_channel.last_meta_at, '-infinity') > COALESCE(last_meta_at, '-infinity') THEN COALESCE(old_channel.description, description) ELSE COALESCE(description, old_channel.description) END,
                subscribers = CASE WHEN COALESCE(old_channel.last_meta_at, '-infinity') > COALESCE(last_meta_at, '-infinity') THEN COALESCE(old_channel.subscribers, subscribers) ELSE COALESCE(subscribers, old_channel.subscribers) END,
                category = COALESCE(category, old_channel.category),
                subcategory = COALESCE(subcategory, old_channel.subcategory),
                country = COALESCE(country, old_channel.country),
                region = COALESCE(region, old_channel.region),
                region_hint = COALESCE(region_hint, old_channel.region_hint),
                source = COALESCE(source, old_channel.source),
                channel_type = COALESCE(channel_type, old_channel.channel_type),
                is_active = is_active AND old_channel.is_active,
                last_meta_at = GREATEST(last_meta_at, old_channel.last_meta_at),
                last_parsed_at = GREATEST(last_parsed_at, old_channel.last_parsed_at),
                created_at = LEAST(created_at, old_channel.created_at),
                updated_at = GREATEST(updated_at, old_channel.updated_at)
            WHERE id = keeper;

            -- Existing post IDs stay stable; retain fresher content on collisions.
            INSERT INTO posts(channel_id, tg_message_id, url, text, title, posted_at, views, fetched_at)
            SELECT keeper, tg_message_id, url, text, title, posted_at, views, fetched_at
            FROM posts p WHERE channel_id = old_channel.id
                AND EXISTS (SELECT 1 FROM posts kept WHERE kept.channel_id = keeper AND kept.tg_message_id = p.tg_message_id)
            ON CONFLICT (channel_id, tg_message_id) DO UPDATE SET
                text = CASE WHEN EXCLUDED.fetched_at > posts.fetched_at THEN COALESCE(EXCLUDED.text, posts.text) ELSE COALESCE(posts.text, EXCLUDED.text) END,
                title = CASE WHEN EXCLUDED.fetched_at > posts.fetched_at THEN COALESCE(EXCLUDED.title, posts.title) ELSE COALESCE(posts.title, EXCLUDED.title) END,
                views = GREATEST(posts.views, EXCLUDED.views),
                posted_at = COALESCE(posts.posted_at, EXCLUDED.posted_at),
                fetched_at = GREATEST(posts.fetched_at, EXCLUDED.fetched_at);

            UPDATE posts p SET channel_id = keeper
            WHERE p.channel_id = old_channel.id AND NOT EXISTS (
                SELECT 1 FROM posts kept WHERE kept.channel_id = keeper AND kept.tg_message_id = p.tg_message_id
            );

            INSERT INTO channel_sources(channel_id, source_name, source_ref, source_url, first_seen_at, last_seen_at, raw_data)
            SELECT keeper, source_name, source_ref, source_url, first_seen_at, last_seen_at, raw_data
            FROM channel_sources WHERE channel_id = old_channel.id
            ON CONFLICT (channel_id, source_name, source_ref) DO UPDATE SET
                first_seen_at = LEAST(channel_sources.first_seen_at, EXCLUDED.first_seen_at),
                last_seen_at = GREATEST(channel_sources.last_seen_at, EXCLUDED.last_seen_at),
                source_url = COALESCE(channel_sources.source_url, EXCLUDED.source_url);

            -- Replay an overlapping interval rather than risk skipping a gap.
            INSERT INTO scrape_checkpoints(channel_id, before_id, target_id)
            SELECT keeper, before_id, target_id FROM scrape_checkpoints WHERE channel_id = old_channel.id
            ON CONFLICT (channel_id) DO UPDATE SET
                before_id = GREATEST(scrape_checkpoints.before_id, EXCLUDED.before_id),
                target_id = LEAST(scrape_checkpoints.target_id, EXCLUDED.target_id);

            UPDATE channel_candidates SET imported_channel_id = keeper WHERE imported_channel_id = old_channel.id;
            DELETE FROM channels WHERE id = old_channel.id;
        END LOOP;
    END LOOP;
END $$;

UPDATE channels SET username = lower(username) WHERE username <> lower(username);
DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conrelid = 'channels'::regclass
                   AND conname = 'channels_username_lowercase') THEN
        ALTER TABLE channels ADD CONSTRAINT channels_username_lowercase CHECK (username = lower(username));
    END IF;
END $$;
CREATE UNIQUE INDEX IF NOT EXISTS idx_channels_username_lower ON channels(lower(username));
