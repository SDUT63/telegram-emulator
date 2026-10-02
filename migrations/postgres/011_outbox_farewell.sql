-- A deletion confirmation is the one outbound message that must survive the
-- purge that triggered it: the person asked us to erase their data and is
-- entitled to be told it is done (ФЗ-152, ст. 14 и 21).
--
-- The row carries no personal data — a constant text plus the platform user id
-- needed to address it. It is queued inside the deletion transaction, so it is
-- durable exactly when the purge is, and mark_sent drops the user id as it does
-- for every other delivered row.
ALTER TABLE outbox_messages
    ADD COLUMN IF NOT EXISTS farewell BOOLEAN NOT NULL DEFAULT FALSE;
