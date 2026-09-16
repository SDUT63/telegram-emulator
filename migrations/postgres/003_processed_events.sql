-- MAX event idempotency ledger.
-- The row is inserted in the same PostgreSQL transaction as survey state and audit.

CREATE TABLE IF NOT EXISTS processed_events (
    event_id TEXT PRIMARY KEY,
    user_id TEXT,
    event_type TEXT NOT NULL,
    event_hash TEXT NOT NULL,
    processed_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_processed_events_user_time
    ON processed_events(user_id, processed_at);