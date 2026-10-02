-- I18: automatic outbound intents are bound to the CASE that created them.
-- This prevents a queued message from a closed CASE being revived by a later CASE.

ALTER TABLE outbox_messages
    ADD COLUMN IF NOT EXISTS case_id BIGINT REFERENCES cases(case_id) ON DELETE SET NULL,
    ADD COLUMN IF NOT EXISTS automatic BOOLEAN NOT NULL DEFAULT FALSE;

CREATE INDEX IF NOT EXISTS idx_outbox_automatic_case
    ON outbox_messages(case_id, status)
    WHERE automatic = TRUE;

CREATE OR REPLACE FUNCTION cancel_case_automatic_outbox()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
    v_user_id TEXT;
BEGIN
    IF NEW.status = 'CLOSED' AND OLD.status <> 'CLOSED' THEN
        SELECT p.channel_user_id
          INTO v_user_id
          FROM persons p
         WHERE p.person_id = NEW.person_id
           AND p.channel = 'max';

        -- Same per-person lock as the worker. If the worker already owns it,
        -- the automatic message is allowed to finish before close commits.
        -- If close owns it first, the worker sees the cancelled row.
        IF v_user_id IS NOT NULL THEN
            PERFORM pg_advisory_xact_lock(hashtextextended(v_user_id, 0));
        END IF;

        UPDATE outbox_messages
           SET status = 'cancelled',
               locked_at = NULL,
               locked_by = NULL
         WHERE case_id = NEW.case_id
           AND automatic = TRUE
           AND status IN ('pending', 'sending');
    END IF;
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS trg_cases_cancel_automatic_outbox ON cases;
CREATE TRIGGER trg_cases_cancel_automatic_outbox
AFTER UPDATE OF status ON cases
FOR EACH ROW
EXECUTE FUNCTION cancel_case_automatic_outbox();
