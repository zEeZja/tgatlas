-- Attempt state is independent of the last successfully obtained metadata.
ALTER TABLE channels ADD COLUMN IF NOT EXISTS last_meta_attempt_at TIMESTAMPTZ;
ALTER TABLE channels ADD COLUMN IF NOT EXISTS last_meta_error TEXT;
CREATE INDEX IF NOT EXISTS idx_channels_metadata_due ON channels(last_meta_at NULLS FIRST)
    WHERE is_active = TRUE;
