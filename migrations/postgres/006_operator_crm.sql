-- Transactional operator CRM state. Outbound operator messages reuse the
-- canonical outbox_messages table and MaxOutboundTransport; there is no
-- second delivery queue or transport.
CREATE TABLE IF NOT EXISTS operator_cases (
    user_id TEXT PRIMARY KEY,
    status TEXT NOT NULL DEFAULT 'Новое',
    assigned TEXT NOT NULL DEFAULT '',
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT operator_cases_status_ck CHECK (status IN ('Новое', 'В работе', 'Закрыто'))
);

CREATE TABLE IF NOT EXISTS operator_notes (
    id BIGSERIAL PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES operator_cases(user_id) ON DELETE CASCADE,
    who TEXT NOT NULL,
    text TEXT NOT NULL,
    system BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_operator_notes_user_time
    ON operator_notes(user_id, created_at, id);

CREATE TABLE IF NOT EXISTS operator_calls (
    user_id TEXT NOT NULL REFERENCES operator_cases(user_id) ON DELETE CASCADE,
    stage SMALLINT NOT NULL,
    who TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (user_id, stage),
    CONSTRAINT operator_calls_stage_ck CHECK (stage IN (7, 30))
);

CREATE INDEX IF NOT EXISTS idx_operator_calls_due
    ON operator_calls(stage, created_at, user_id);
