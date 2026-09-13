-- Privacy hardening for user-requested deletion.
-- Sent/dead outbox rows no longer need the recipient identifier after terminal
-- delivery, so user_id can be cleared without losing delivery tombstones.
ALTER TABLE outbox_messages
    ALTER COLUMN user_id DROP NOT NULL;
