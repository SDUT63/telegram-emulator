-- Migration metadata is separate so future migrations can be applied
-- deterministically without relying on table-existence heuristics.

CREATE TABLE IF NOT EXISTS schema_migrations (
    version TEXT PRIMARY KEY,
    applied_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);
