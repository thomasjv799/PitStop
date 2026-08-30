-- Web sign-in accounts.
--
-- Anyone with a Google account can sign in; nobody sees fleet data until an
-- admin approves them. `approved_at IS NULL` is the waiting room, and it is
-- the default, so a new account is never accidentally live.
--
-- `subject` is Google's `sub` claim, which is stable for the life of the
-- account — email addresses can be changed and reused, so the sub is the
-- identity and the email is only a label.

CREATE TABLE IF NOT EXISTS web_users (
    id           BIGSERIAL PRIMARY KEY,
    subject      TEXT NOT NULL UNIQUE,
    email        TEXT NOT NULL,
    name         TEXT,
    role         TEXT NOT NULL DEFAULT 'member' CHECK (role IN ('admin', 'member')),
    approved_at  TIMESTAMPTZ,
    approved_by  TEXT,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_seen_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS web_users_pending
    ON web_users (created_at) WHERE approved_at IS NULL;

CREATE INDEX IF NOT EXISTS web_users_email ON web_users (lower(email));
