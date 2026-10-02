-- User deletion tombstones coordinate privacy deletion with outbound delivery.
-- The row contains only the platform user identifier and deletion timestamp so
-- a worker can refuse new delivery after deletion has committed.
CREATE TABLE IF NOT EXISTS deleted_users (
    user_id TEXT PRIMARY KEY,
    deleted_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);
