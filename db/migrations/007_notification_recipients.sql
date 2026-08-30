-- Who receives the reminder digest.
--
-- This was the EMAIL_TO environment variable, which meant adding a person
-- required a redeploy and a GitHub secret edit. Recipients now live here and
-- are managed from /admin/users.
--
-- Addresses are stored lowercased and trimmed, under a unique index on the
-- same expression: the same mailbox added twice — capitalised differently, or
-- with a stray space pasted in — must not receive two copies of the digest.
-- lower() alone is not enough; it does not trim, so "  a@b.com " and
-- "a@b.com" would be two different index keys and two rows.
--
-- `active` rather than deletion for the common case — switching someone off
-- for a while should not lose the record of who added them and when.

CREATE TABLE IF NOT EXISTS notification_recipients (
    id         BIGSERIAL PRIMARY KEY,
    email      TEXT NOT NULL,
    name       TEXT,
    active     BOOLEAN NOT NULL DEFAULT true,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    created_by TEXT
);

CREATE UNIQUE INDEX IF NOT EXISTS notification_recipients_email
    ON notification_recipients (lower(btrim(email)));

CREATE INDEX IF NOT EXISTS notification_recipients_active
    ON notification_recipients (active) WHERE active;
