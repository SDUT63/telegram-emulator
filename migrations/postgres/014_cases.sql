-- Модель обращения, доменный контракт v1 (docs/МОДЕЛЬ-ОБРАЩЕНИЯ.md, ред. 2).
-- Новые таблицы рядом со старыми: survey_state, operator_cases и остальные
-- не трогаются. Перенос данных — отдельный шаг (раздел 11 контракта).
--
-- Правила живут в cases.py. Здесь — последний рубеж: ограничения, которые
-- база держит даже при ошибке в коде. Номера И1–И15 — инварианты
-- раздела 15 контракта.

CREATE TABLE IF NOT EXISTS persons (
    person_id BIGSERIAL PRIMARY KEY,
    channel TEXT NOT NULL,
    channel_user_id TEXT NOT NULL,
    first_seen_at TIMESTAMPTZ,
    deleted_at TIMESTAMPTZ,
    CONSTRAINT persons_channel_user_uq UNIQUE (channel, channel_user_id)
);

CREATE TABLE IF NOT EXISTS case_number_counters (
    year INTEGER PRIMARY KEY,
    last_value INTEGER NOT NULL CHECK (last_value > 0)
);

CREATE TABLE IF NOT EXISTS cases (
    case_id BIGSERIAL PRIMARY KEY,
    person_id BIGINT NOT NULL REFERENCES persons(person_id),
    source TEXT NOT NULL,
    legal_basis TEXT NOT NULL,
    status TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL,
    number TEXT,
    status_before_escalation TEXT,
    requester_kind TEXT,
    district TEXT,
    urgency TEXT,
    priority TEXT,
    priority_reason TEXT,
    priority_rules_version TEXT,
    suggested_route JSONB,
    final_route TEXT,
    route_changed_by TEXT,
    route_change_reason TEXT,
    assigned_to TEXT,
    due_at TIMESTAMPTZ,
    opened_at TIMESTAMPTZ,
    contacted_at TIMESTAMPTZ,
    route_confirmed_at TIMESTAMPTZ,
    referred_at TIMESTAMPTZ,
    service_started_at TIMESTAMPTZ,
    closed_at TIMESTAMPTZ,
    close_reason TEXT,
    duplicate_of BIGINT REFERENCES cases(case_id) ON DELETE SET NULL,
    CONSTRAINT cases_number_uq UNIQUE (number),
    CONSTRAINT cases_status_ck CHECK (status IN (
        'DRAFT', 'NEW', 'ASSIGNED', 'CONTACTED', 'ROUTE_CONFIRMED', 'REFERRED',
        'SERVICE_STARTED', 'CONTROL', 'CLOSED', 'NO_CONTACT', 'WAITING_EXTERNAL', 'ESCALATED')),
    CONSTRAINT cases_legal_basis_ck CHECK (legal_basis IN ('consent', 'vital_interest')),
    CONSTRAINT cases_close_reason_ck CHECK (close_reason IN (
        'help_received', 'help_partial', 'solved_otherwise', 'consultation_enough',
        'refused', 'not_eligible', 'unable_to_contact', 'duplicate',
        'abandoned_draft', 'migrated')),
    -- И7: закрыто тогда и только тогда, когда названа причина.
    CONSTRAINT cases_closed_has_reason_ck CHECK ((status = 'CLOSED') = (close_reason IS NOT NULL)),
    -- И2: номер есть ровно у тех, кто выходил из черновика (Ч2).
    CONSTRAINT cases_number_iff_opened_ck CHECK ((number IS NULL) = (opened_at IS NULL)),
    CONSTRAINT cases_draft_has_no_number_ck CHECK (status <> 'DRAFT' OR number IS NULL),
    CONSTRAINT cases_working_has_number_ck CHECK (status IN ('DRAFT', 'CLOSED') OR number IS NOT NULL),
    CONSTRAINT cases_number_format_ck CHECK (number ~ '^SDUT-[0-9]{4}-[0-9]{5,}$'),
    CONSTRAINT cases_escalated_returns_ck CHECK ((status = 'ESCALATED') = (status_before_escalation IS NOT NULL)),
    CONSTRAINT cases_urgency_ck CHECK (urgency IN ('P0')),
    CONSTRAINT cases_priority_ck CHECK (priority IN ('P1', 'P2', 'P3')),
    CONSTRAINT cases_route_ck CHECK (final_route IN ('М1', 'М2', 'М3', 'М4', 'М5', 'СМП')),
    CONSTRAINT cases_not_duplicate_of_itself_ck CHECK (duplicate_of IS NULL OR duplicate_of <> case_id)
);

-- И1: открытых обращений у человека не больше одного.
CREATE UNIQUE INDEX IF NOT EXISTS cases_one_open_per_person
    ON cases(person_id) WHERE status <> 'CLOSED';
CREATE INDEX IF NOT EXISTS idx_cases_status_created ON cases(status, created_at);

CREATE TABLE IF NOT EXISTS case_consents (
    consent_id BIGSERIAL PRIMARY KEY,
    case_id BIGINT NOT NULL REFERENCES cases(case_id) ON DELETE CASCADE,
    kind TEXT NOT NULL CHECK (kind IN ('processing', 'medical_transfer')),
    version TEXT NOT NULL,
    text_hash TEXT,
    given_at TIMESTAMPTZ NOT NULL,
    given_via TEXT NOT NULL CHECK (given_via IN ('bot', 'paper')),
    withdrawn_at TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_case_consents_case ON case_consents(case_id);

CREATE TABLE IF NOT EXISTS intakes (
    case_id BIGINT NOT NULL REFERENCES cases(case_id) ON DELETE CASCADE,
    version INTEGER NOT NULL CHECK (version > 0),
    questionnaire_version TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL,
    answers JSONB NOT NULL DEFAULT '{}'::jsonb,
    alerts JSONB NOT NULL DEFAULT '[]'::jsonb,
    story TEXT CHECK (char_length(story) <= 4000),
    story_hints JSONB NOT NULL DEFAULT '[]'::jsonb,
    checkpoint_at TIMESTAMPTZ,
    completed_at TIMESTAMPTZ,
    PRIMARY KEY (case_id, version)
);

CREATE TABLE IF NOT EXISTS route_directory (
    directory_entry_id BIGSERIAL PRIMARY KEY,
    provider_key TEXT NOT NULL,
    route TEXT NOT NULL CHECK (route IN ('М1', 'М2', 'М3', 'М4', 'М5', 'СМП')),
    provider TEXT NOT NULL,
    available BOOLEAN NOT NULL,
    valid_from TIMESTAMPTZ NOT NULL,
    fallback TEXT,
    phone TEXT,
    hours TEXT,
    address TEXT,
    conditions TEXT,
    documents TEXT,
    valid_to TIMESTAMPTZ,
    CONSTRAINT route_directory_fallback_ck CHECK (available OR fallback IS NOT NULL),
    CONSTRAINT route_directory_period_ck CHECK (valid_to IS NULL OR valid_to >= valid_from)
);
CREATE UNIQUE INDEX IF NOT EXISTS route_directory_one_current
    ON route_directory(provider_key) WHERE valid_to IS NULL;

CREATE TABLE IF NOT EXISTS tasks (
    task_id BIGSERIAL PRIMARY KEY,
    case_id BIGINT NOT NULL REFERENCES cases(case_id) ON DELETE CASCADE,
    kind TEXT NOT NULL CHECK (kind IN (
        'first_contact', 'confirm_route', 'referral_followup',
        'control_d7', 'control_d30', 'escalation')),
    due_at TIMESTAMPTZ NOT NULL,                          -- И9
    status TEXT NOT NULL CHECK (status IN ('open', 'done', 'cancelled', 'overdue')),
    created_at TIMESTAMPTZ NOT NULL,
    assigned_to TEXT,
    done_at TIMESTAMPTZ,
    done_by TEXT,
    result TEXT,
    escalated_at TIMESTAMPTZ,
    escalated_to TEXT,
    CONSTRAINT tasks_done_has_result_ck CHECK (status <> 'done' OR (done_at IS NOT NULL AND result IS NOT NULL))
);
CREATE INDEX IF NOT EXISTS idx_tasks_case ON tasks(case_id);
CREATE INDEX IF NOT EXISTS idx_tasks_open_due ON tasks(due_at) WHERE status IN ('open', 'overdue');

CREATE TABLE IF NOT EXISTS referrals (
    referral_id BIGSERIAL PRIMARY KEY,
    case_id BIGINT NOT NULL REFERENCES cases(case_id) ON DELETE CASCADE,
    directory_entry_id BIGINT NOT NULL REFERENCES route_directory(directory_entry_id),
    referred_at TIMESTAMPTZ NOT NULL,
    channel TEXT NOT NULL CHECK (channel IN ('call', 'letter', 'in_person')),
    response TEXT,
    response_at TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_referrals_case ON referrals(case_id);

CREATE TABLE IF NOT EXISTS outcomes (
    outcome_id BIGSERIAL PRIMARY KEY,
    case_id BIGINT NOT NULL REFERENCES cases(case_id) ON DELETE CASCADE,
    need TEXT NOT NULL CHECK (need IN (
        'palliative', 'home_social_service', 'ltc_care', 'assistive_devices',
        'family_training', 'needs_assessment', 'documents', 'caregiver_support', 'other')),
    action TEXT NOT NULL CHECK (action IN (
        'consultation', 'referral', 'accompaniment', 'visit',
        'assistive_devices_issue', 'training')),
    result TEXT NOT NULL CHECK (result IN (
        'received', 'partially', 'waiting', 'refused', 'not_eligible',
        'unable_to_contact', 'solved_otherwise')),
    recorded_at TIMESTAMPTZ NOT NULL,
    recorded_by TEXT NOT NULL,
    directory_entry_id BIGINT REFERENCES route_directory(directory_entry_id),
    service_started_at TIMESTAMPTZ,
    service_received_at TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_outcomes_case ON outcomes(case_id);

CREATE TABLE IF NOT EXISTS case_events (
    event_id BIGSERIAL PRIMARY KEY,
    case_id BIGINT NOT NULL REFERENCES cases(case_id) ON DELETE CASCADE,
    at TIMESTAMPTZ NOT NULL,
    who TEXT NOT NULL,
    kind TEXT NOT NULL,
    payload JSONB NOT NULL DEFAULT '{}'::jsonb
);
CREATE INDEX IF NOT EXISTS idx_case_events_case ON case_events(case_id, event_id);

-- И2, И5: номер и предложенный маршрут записываются один раз.
CREATE OR REPLACE FUNCTION cases_write_once() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF OLD.number IS NOT NULL AND NEW.number IS DISTINCT FROM OLD.number THEN
        RAISE EXCEPTION 'И2: номер обращения не меняется';
    END IF;
    IF OLD.suggested_route IS NOT NULL AND NEW.suggested_route IS DISTINCT FROM OLD.suggested_route THEN
        RAISE EXCEPTION 'И5: предложенный маршрут записывается один раз';
    END IF;
    RETURN NEW;
END $$;

DROP TRIGGER IF EXISTS cases_write_once ON cases;
CREATE TRIGGER cases_write_once BEFORE UPDATE ON cases
    FOR EACH ROW EXECUTE FUNCTION cases_write_once();

-- И11: обращение на основании согласия фиксируется только со снимком
-- согласия на обработку. Проверка — в момент COMMIT: снимок пишется
-- в той же транзакции сразу после обращения.
CREATE OR REPLACE FUNCTION cases_require_consent() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM cases c
        WHERE c.case_id = NEW.case_id AND c.legal_basis = 'consent'
          AND NOT EXISTS (
              SELECT 1 FROM case_consents k
              WHERE k.case_id = c.case_id AND k.kind = 'processing')
    ) THEN
        RAISE EXCEPTION 'И11: обращение на основании согласия без снимка согласия';
    END IF;
    RETURN NULL;
END $$;

DROP TRIGGER IF EXISTS cases_require_consent ON cases;
CREATE CONSTRAINT TRIGGER cases_require_consent AFTER INSERT OR UPDATE ON cases
    DEFERRABLE INITIALLY DEFERRED
    FOR EACH ROW EXECUTE FUNCTION cases_require_consent();

-- И3: события только дописываются. Удалить их можно только вместе
-- с обращением (удаление по просьбе или по сроку хранения).
CREATE OR REPLACE FUNCTION case_events_append_only() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP = 'UPDATE' THEN
        RAISE EXCEPTION 'И3: события обращения только дописываются';
    END IF;
    IF EXISTS (SELECT 1 FROM cases WHERE case_id = OLD.case_id) THEN
        RAISE EXCEPTION 'И3: событие удаляется только вместе с обращением';
    END IF;
    RETURN OLD;
END $$;

DROP TRIGGER IF EXISTS case_events_append_only ON case_events;
CREATE TRIGGER case_events_append_only BEFORE UPDATE OR DELETE ON case_events
    FOR EACH ROW EXECUTE FUNCTION case_events_append_only();

-- И11: снимок согласия не меняется, кроме однократной отметки отзыва,
-- и удаляется только вместе с обращением.
CREATE OR REPLACE FUNCTION case_consents_snapshot() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP = 'UPDATE' THEN
        IF OLD.withdrawn_at IS NOT NULL OR NEW.withdrawn_at IS NULL
           OR (to_jsonb(NEW) - 'withdrawn_at') IS DISTINCT FROM (to_jsonb(OLD) - 'withdrawn_at') THEN
            RAISE EXCEPTION 'И11: снимок согласия не меняется — только однократная отметка отзыва';
        END IF;
        RETURN NEW;
    END IF;
    IF EXISTS (SELECT 1 FROM cases WHERE case_id = OLD.case_id) THEN
        RAISE EXCEPTION 'И11: снимок согласия удаляется только вместе с обращением';
    END IF;
    RETURN OLD;
END $$;

DROP TRIGGER IF EXISTS case_consents_snapshot ON case_consents;
CREATE TRIGGER case_consents_snapshot BEFORE UPDATE OR DELETE ON case_consents
    FOR EACH ROW EXECUTE FUNCTION case_consents_snapshot();

-- И12: версия записи справочника не меняется; можно только один раз
-- закрыть её сроком действия, когда появилась новая.
CREATE OR REPLACE FUNCTION route_directory_versioned() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF OLD.valid_to IS NOT NULL OR NEW.valid_to IS NULL
       OR (to_jsonb(NEW) - 'valid_to') IS DISTINCT FROM (to_jsonb(OLD) - 'valid_to') THEN
        RAISE EXCEPTION 'И12: версия записи справочника не меняется — создайте новую';
    END IF;
    RETURN NEW;
END $$;

DROP TRIGGER IF EXISTS route_directory_versioned ON route_directory;
CREATE TRIGGER route_directory_versioned BEFORE UPDATE ON route_directory
    FOR EACH ROW EXECUTE FUNCTION route_directory_versioned();
