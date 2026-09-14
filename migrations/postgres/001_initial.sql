-- SDUT MAX bot PostgreSQL schema, migration 001.
-- Safe to run repeatedly only through the migration runner.

CREATE TABLE IF NOT EXISTS survey_state (
    user_id TEXT PRIMARY KEY,
    state_json JSONB NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS event_leases (
    event_id TEXT PRIMARY KEY,
    claimed_at DOUBLE PRECISION NOT NULL,
    last_seen_at DOUBLE PRECISION NOT NULL
);

CREATE TABLE IF NOT EXISTS audit_events (
    id BIGSERIAL PRIMARY KEY,
    user_id TEXT,
    event_type TEXT NOT NULL,
    event_json JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_audit_user_created
    ON audit_events(user_id, created_at);

CREATE INDEX IF NOT EXISTS idx_event_leases_seen
    ON event_leases(last_seen_at);
