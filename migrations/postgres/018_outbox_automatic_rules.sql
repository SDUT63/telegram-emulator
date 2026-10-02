-- И18 (9.1 контракта) поверх 017: правила для автоматических сообщений.
--
-- 017 не меняется — миграции после применения не правятся. Здесь
-- исправлено то, что в ней не сходилось с базой и с контрактом:
--
-- 1. Триггер 017 ставил отменённому сообщению статус 'cancelled', а
--    outbox_messages_status_ck (004) его не допускал: первое же закрытие
--    обращения с автоматическим сообщением в очереди падало бы целиком.
-- 2. Автоматическое сообщение без обращения (case_id IS NULL) воркер не
--    брал никогда, а при перепроверке перед отправкой — пропускал. Теперь
--    его просто нельзя поставить: автоматическое — только в контексте
--    обращения (9.1 д).
-- 3. Обращение удаляется вместе с его автоматическими сообщениями
--    (CASCADE вместо SET NULL): иначе удаление по просьбе человека
--    (И14) оставляло бы текст, привязанный к стёртому обращению.
-- 4. Отменённое сообщение теряет текст и вложения сразу, как 'dead':
--    отправлено оно уже не будет, а хранить его незачем. Отпечаток
--    payload_sha256 и ключ доставки остаются — повторная постановка
--    с тем же ключом не «воскресит» отменённое (9.1 д).

-- Код при 017 помечал автоматическим каждое сообщение, кроме прощания, то
-- есть и ответы бота на сообщения человека. Автоматических сообщений в
-- системе пока нет вовсе (9.1), поэтому всё, что так помечено, — ответы
-- бота: снимаем пометку и привязку, иначе закрытие обращения отменило бы
-- их, а сообщения без обращения не ушли бы никогда.
UPDATE outbox_messages SET automatic = FALSE, case_id = NULL WHERE automatic;

ALTER TABLE outbox_messages DROP CONSTRAINT outbox_messages_status_ck;
ALTER TABLE outbox_messages ADD CONSTRAINT outbox_messages_status_ck
    CHECK (status IN ('pending', 'sending', 'sent', 'dead', 'cancelled'));

ALTER TABLE outbox_messages ADD CONSTRAINT outbox_messages_automatic_case_ck
    CHECK (NOT automatic OR case_id IS NOT NULL);

ALTER TABLE outbox_messages DROP CONSTRAINT outbox_messages_case_id_fkey;
ALTER TABLE outbox_messages ADD CONSTRAINT outbox_messages_case_id_fkey
    FOREIGN KEY (case_id) REFERENCES cases(case_id) ON DELETE CASCADE;

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

        -- Тот же замок человека, что держит отправитель очереди на время
        -- вызова MAX (9.2): сообщение либо ушло до фиксации закрытия, либо
        -- не уйдёт вовсе. Модуль обращений берёт этот замок раньше строки
        -- обращения (cases_postgres.get_case), здесь он уже свой.
        IF v_user_id IS NOT NULL THEN
            PERFORM pg_advisory_xact_lock(hashtextextended(v_user_id, 0));
        END IF;

        -- Отменяются автоматические сообщения именно этого обращения. Этого
        -- достаточно и для «всех автоматических сообщений этому человеку»
        -- (9.1 а): без обращения автоматическое не ставится
        -- (outbox_messages_automatic_case_ck), а открытое обращение у
        -- человека одно (И1) — сообщения прежних отменены при их закрытии.
        WITH cancelled AS (
            UPDATE outbox_messages
               SET status = 'cancelled',
                   payload = '{"kind": "redacted"}'::jsonb,
                   locked_at = NULL,
                   locked_by = NULL,
                   last_error = 'case closed: ' || COALESCE(NEW.close_reason, '')
             WHERE case_id = NEW.case_id
               AND automatic
               AND status IN ('pending', 'sending')
            RETURNING delivery_key
        )
        DELETE FROM outbox_attachments
         WHERE delivery_key IN (SELECT delivery_key FROM cancelled);
    END IF;
    RETURN NEW;
END;
$$;
