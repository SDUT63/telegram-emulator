-- Preserve delivery-key identity after sent-row pruning and allow successful
-- deliveries to shed their potentially sensitive outbound payload.
ALTER TABLE outbox_messages
    ADD COLUMN IF NOT EXISTS payload_sha256 TEXT;

CREATE TABLE IF NOT EXISTS outbox_delivery_tombstones (
    delivery_key TEXT PRIMARY KEY,
    payload_sha256 TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_outbox_sent_retention
    ON outbox_messages(status, sent_at, id)
    WHERE status = 'sent';
