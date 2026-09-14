CREATE TABLE IF NOT EXISTS outbox_messages (
    id BIGSERIAL PRIMARY KEY,
    delivery_key TEXT NOT NULL UNIQUE,
    user_id TEXT NOT NULL,
    chat_id TEXT,
    payload JSONB NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    attempts INTEGER NOT NULL DEFAULT 0,
    available_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    locked_at TIMESTAMPTZ,
    locked_by TEXT,
    last_error TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    sent_at TIMESTAMPTZ,
    CONSTRAINT outbox_messages_status_ck CHECK (status IN ('pending','sending','sent','dead')),
    CONSTRAINT outbox_messages_attempts_ck CHECK (attempts >= 0)
);

CREATE INDEX IF NOT EXISTS idx_outbox_pending
    ON outbox_messages(status, available_at, id)
    WHERE status = 'pending';

CREATE INDEX IF NOT EXISTS idx_outbox_user_created
    ON outbox_messages(user_id, created_at);

CREATE INDEX IF NOT EXISTS idx_outbox_stale_sending
    ON outbox_messages(locked_at)
    WHERE status = 'sending';
