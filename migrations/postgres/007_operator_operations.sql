-- Persist operator command identities independently from outbox retention.
-- This keeps operation-level idempotency valid even after the corresponding
-- sent outbox row has been pruned into a tombstone.
CREATE TABLE IF NOT EXISTS operator_operations (
    operation_id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES operator_cases(user_id) ON DELETE CASCADE,
    delivery_key TEXT NOT NULL UNIQUE,
    payload_sha256 TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_operator_operations_user_time
    ON operator_operations(user_id, created_at, operation_id);
