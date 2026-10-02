-- Модель обращения, редакция 3: усиление 015. 015 не меняется — она уже
-- применена; функция триггера cases_r3_close_rules заменяется целиком,
-- сам триггер остаётся тем же.
--
-- В 015 правила закрытия проверялись только при первом закрытии
-- (OLD.close_reason IS NULL). Прямой записью можно было «перезакрыть»
-- обращение с другой причиной — например, refused → consent_not_given
-- у обращения режима Б или abandoned_draft → ward_died — или
-- переоткрыть его, обнулив причину. В таблице 5.2 из CLOSED переходов
-- нет, а история только дописывается (раздел 3, п. 2): причина
-- закрытия, однажды записанная, не меняется.
CREATE OR REPLACE FUNCTION cases_r3_close_rules() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP = 'INSERT' THEN
        IF NEW.close_reason = 'consent_not_given' THEN
            RAISE EXCEPTION 'И7: consent_not_given — только переходом из CONTACTED';
        END IF;
        RETURN NEW;
    END IF;
    IF OLD.close_reason IS NOT NULL THEN
        IF NEW.close_reason IS DISTINCT FROM OLD.close_reason THEN
            RAISE EXCEPTION 'И7: причина закрытия не меняется, закрытое обращение не переоткрывается — из CLOSED переходов нет (5.2)';
        END IF;
        RETURN NEW;
    END IF;
    IF NEW.close_reason IS NOT NULL THEN
        IF OLD.status = 'DRAFT' AND NEW.close_reason <> 'abandoned_draft' THEN
            RAISE EXCEPTION 'Г5: черновик закрывается только как abandoned_draft';
        END IF;
        IF NEW.close_reason = 'consent_not_given' AND OLD.status <> 'CONTACTED' THEN
            RAISE EXCEPTION 'И7: consent_not_given — только из CONTACTED';
        END IF;
    END IF;
    RETURN NEW;
END $$;
