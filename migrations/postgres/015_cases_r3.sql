-- Модель обращения, редакция 3 (docs/МОДЕЛЬ-ОБРАЩЕНИЯ.md).
-- Дополняет 014, ничего в ней не меняя: 014 уже применена.
--
-- Правила живут в cases.py. Здесь — последний рубеж базы: новые причины
-- закрытия и вид задачи, ограничения для consent_not_given и черновика,
-- И16 — судьба открытых задач, с блокировкой строки обращения.

-- Г1: вид задачи control_extra — продление контроля (5.5).
ALTER TABLE tasks DROP CONSTRAINT IF EXISTS tasks_kind_check;
ALTER TABLE tasks DROP CONSTRAINT IF EXISTS tasks_kind_ck;
ALTER TABLE tasks ADD CONSTRAINT tasks_kind_ck CHECK (kind IN (
    'first_contact', 'confirm_route', 'referral_followup',
    'control_d7', 'control_d30', 'control_extra', 'escalation'));

-- Г2, Г3: причины consent_not_given и ward_died (5.3).
ALTER TABLE cases DROP CONSTRAINT IF EXISTS cases_close_reason_ck;
ALTER TABLE cases ADD CONSTRAINT cases_close_reason_ck CHECK (close_reason IN (
    'help_received', 'help_partial', 'solved_otherwise', 'consultation_enough',
    'refused', 'not_eligible', 'unable_to_contact', 'duplicate',
    'abandoned_draft', 'migrated', 'consent_not_given', 'ward_died'));

-- И7: consent_not_given — только у обращения режима Б.
ALTER TABLE cases DROP CONSTRAINT IF EXISTS cases_consent_not_given_ck;
ALTER TABLE cases ADD CONSTRAINT cases_consent_not_given_ck CHECK (
    close_reason IS DISTINCT FROM 'consent_not_given' OR legal_basis = 'vital_interest');

-- Правила закрытия, которых CHECK не видит, потому что зависят от статуса
-- до перехода: consent_not_given — только из CONTACTED (И7); черновик
-- закрывается только как abandoned_draft (Г5, 5.2).
CREATE OR REPLACE FUNCTION cases_r3_close_rules() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP = 'INSERT' THEN
        IF NEW.close_reason = 'consent_not_given' THEN
            RAISE EXCEPTION 'И7: consent_not_given — только переходом из CONTACTED';
        END IF;
        RETURN NEW;
    END IF;
    IF OLD.close_reason IS NULL AND NEW.close_reason IS NOT NULL THEN
        IF OLD.status = 'DRAFT' AND NEW.close_reason <> 'abandoned_draft' THEN
            RAISE EXCEPTION 'Г5: черновик закрывается только как abandoned_draft';
        END IF;
        IF NEW.close_reason = 'consent_not_given' AND OLD.status <> 'CONTACTED' THEN
            RAISE EXCEPTION 'И7: consent_not_given — только из CONTACTED';
        END IF;
    END IF;
    RETURN NEW;
END $$;

DROP TRIGGER IF EXISTS cases_r3_close_rules ON cases;
CREATE TRIGGER cases_r3_close_rules BEFORE INSERT OR UPDATE ON cases
    FOR EACH ROW EXECUTE FUNCTION cases_r3_close_rules();

-- И16, сериализация: любое изменение задачи блокирует строку её
-- обращения до конца транзакции. Задача не может появиться между
-- закрытием обращения и отменой его задач — даже в обход cases.py.
CREATE OR REPLACE FUNCTION tasks_lock_case() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    PERFORM 1 FROM cases WHERE case_id = NEW.case_id FOR UPDATE;
    RETURN NEW;
END $$;

DROP TRIGGER IF EXISTS tasks_lock_case ON tasks;
CREATE TRIGGER tasks_lock_case BEFORE INSERT OR UPDATE ON tasks
    FOR EACH ROW EXECUTE FUNCTION tasks_lock_case();

-- И16 (а), (б): проверка при фиксации транзакции. Переход и отмена задач
-- пишутся вместе, поэтому проверять можно только итог.
CREATE OR REPLACE FUNCTION cases_tasks_i16(p_case_id BIGINT) RETURNS void
LANGUAGE plpgsql AS $$
DECLARE
    v_status TEXT;
    v_before TEXT;
BEGIN
    SELECT status, status_before_escalation INTO v_status, v_before
      FROM cases WHERE case_id = p_case_id;
    IF NOT FOUND THEN
        RETURN;                     -- обращение удалено вместе с задачами
    END IF;
    IF v_status = 'CLOSED' AND EXISTS (
        SELECT 1 FROM tasks
         WHERE case_id = p_case_id AND status IN ('open', 'overdue')
    ) THEN
        RAISE EXCEPTION 'И16: у закрытого обращения не может быть открытых задач';
    END IF;
    IF NOT (v_status = 'CONTROL' OR (v_status = 'ESCALATED' AND v_before = 'CONTROL'))
       AND EXISTS (
        SELECT 1 FROM tasks
         WHERE case_id = p_case_id AND status IN ('open', 'overdue')
           AND kind IN ('control_d7', 'control_d30', 'control_extra')
    ) THEN
        RAISE EXCEPTION 'И16: открытые контрольные задачи есть только у обращения в контроле';
    END IF;
END $$;

CREATE OR REPLACE FUNCTION cases_check_i16() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    PERFORM cases_tasks_i16(NEW.case_id);
    RETURN NULL;
END $$;

DROP TRIGGER IF EXISTS cases_i16 ON cases;
CREATE CONSTRAINT TRIGGER cases_i16 AFTER UPDATE ON cases
    DEFERRABLE INITIALLY DEFERRED
    FOR EACH ROW EXECUTE FUNCTION cases_check_i16();

DROP TRIGGER IF EXISTS tasks_i16 ON tasks;
CREATE CONSTRAINT TRIGGER tasks_i16 AFTER INSERT OR UPDATE ON tasks
    DEFERRABLE INITIALLY DEFERRED
    FOR EACH ROW EXECUTE FUNCTION cases_check_i16();
