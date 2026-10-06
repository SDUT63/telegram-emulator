-- Редакция 5 контракта (решения по архиву, К4): задача «связаться по
-- сигналу безопасности». Ставит бот после согласия — на кризис, острое
-- состояние, просьбу близкого дать умереть и «на пределе». Срок — по
-- приоритету и рабочему времени службы; людям он не называется (К5).
--
-- 015 не меняется — миграции после применения не правятся.

ALTER TABLE tasks DROP CONSTRAINT IF EXISTS tasks_kind_ck;
ALTER TABLE tasks ADD CONSTRAINT tasks_kind_ck CHECK (kind IN (
    'first_contact', 'confirm_route', 'referral_followup',
    'control_d7', 'control_d30', 'control_extra', 'escalation',
    'safety_contact'));
