-- Вложения оператора: памятка по уходу, бланк согласия, скан направления.
--
-- Содержимое живёт здесь, а не на диске веб-процесса: в production инстансов
-- несколько и общего диска у них нет, поэтому путь, записанный CRM, воркеру
-- недоступен. Заодно это снимает копию медицинского документа с локальной
-- файловой системы — данные видит только база.
--
-- ON DELETE CASCADE делает privacy автоматическим: вложение исчезает вместе
-- со строкой очереди — при доставке (prune_sent), при отказе и при удалении
-- данных пользователя. Отдельного места, где оно могло бы остаться, нет.
CREATE TABLE IF NOT EXISTS outbox_attachments (
    id BIGSERIAL PRIMARY KEY,
    delivery_key TEXT NOT NULL REFERENCES outbox_messages(delivery_key) ON DELETE CASCADE,
    ordinal SMALLINT NOT NULL,
    filename TEXT NOT NULL,
    content BYTEA NOT NULL,
    sha256 TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT outbox_attachments_unique_slot UNIQUE (delivery_key, ordinal),
    CONSTRAINT outbox_attachments_ordinal_ck CHECK (ordinal >= 0)
);

CREATE INDEX IF NOT EXISTS idx_outbox_attachments_key
    ON outbox_attachments(delivery_key, ordinal);
