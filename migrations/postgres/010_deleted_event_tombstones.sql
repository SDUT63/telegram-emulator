-- Replay tombstones survive user-data deletion without retaining the user id.
-- event_id remains the provider replay key; event_type/hash allow collision detection.
CREATE TABLE IF NOT EXISTS deleted_event_tombstones (
    event_id TEXT PRIMARY KEY,
    event_type TEXT NOT NULL,
    event_hash TEXT NOT NULL,
    deleted_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_deleted_event_tombstones_deleted_at
    ON deleted_event_tombstones(deleted_at);
