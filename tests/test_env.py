"""Environment reading, with the blank-value case that took down Vercel.

`os.getenv(name, default)` returns the default only when a variable is
*absent*. Present-but-empty comes back as `""`, so `int(os.getenv(...))`
raises at import and the serverless function dies before it can say why.
Adding a key and leaving the box blank is ordinary in a hosting dashboard.
"""

import pytest

from utils.env import env_bool, env_int, env_list, env_optional, env_str


# ── the blank case ───────────────────────────────────────────────────────


def test_blank_reads_as_absent_not_as_a_value(monkeypatch):
    monkeypatch.setenv("PITSTOP_T", "")
    assert env_str("PITSTOP_T", "fallback") == "fallback"
    assert env_int("PITSTOP_T", 30) == 30
    assert env_bool("PITSTOP_T", True) is True


def test_whitespace_only_also_reads_as_absent(monkeypatch):
    monkeypatch.setenv("PITSTOP_T", "   ")
    assert env_str("PITSTOP_T", "fallback") == "fallback"
    assert env_int("PITSTOP_T", 30) == 30


def test_absent_uses_the_default(monkeypatch):
    monkeypatch.delenv("PITSTOP_T", raising=False)
    assert env_str("PITSTOP_T", "fallback") == "fallback"
    assert env_int("PITSTOP_T", 30) == 30


def test_values_are_stripped(monkeypatch):
    monkeypatch.setenv("PITSTOP_T", "  hello  ")
    assert env_str("PITSTOP_T") == "hello"
    monkeypatch.setenv("PITSTOP_T", "  42 ")
    assert env_int("PITSTOP_T", 0) == 42


def test_a_malformed_number_falls_back_rather_than_crashing(monkeypatch):
    # A bad tuning value should not stop the app booting.
    monkeypatch.setenv("PITSTOP_T", "thirty")
    assert env_int("PITSTOP_T", 30) == 30


@pytest.mark.parametrize("raw, expected", [
    ("1", True), ("true", True), ("TRUE", True), ("yes", True), ("on", True),
    ("0", False), ("false", False), ("no", False), ("banana", False),
])
def test_bool_parsing(monkeypatch, raw, expected):
    monkeypatch.setenv("PITSTOP_T", raw)
    assert env_bool("PITSTOP_T") is expected


def test_list_drops_blanks(monkeypatch):
    monkeypatch.setenv("PITSTOP_T", "a@x.com, ,b@y.com,")
    assert env_list("PITSTOP_T") == ["a@x.com", "b@y.com"]
    monkeypatch.setenv("PITSTOP_T", "")
    assert env_list("PITSTOP_T") == []


def test_optional_returns_none_for_blank(monkeypatch):
    monkeypatch.setenv("PITSTOP_T", "  ")
    assert env_optional("PITSTOP_T") is None


# ── the real thing: importing with everything blank ──────────────────────


BLANK_VARS = [
    "WEB_SOON_DAYS", "SESSION_MAX_AGE", "SESSION_HTTPS_ONLY", "AUTH_MODE",
    "DEV_USER", "DEV_USER_NAME", "OIDC_ISSUER", "OIDC_CLIENT_ID",
    "OIDC_CLIENT_SECRET", "OIDC_SCOPES", "OIDC_PROVIDER_NAME",
    "OIDC_REDIRECT_URI", "ADMIN_EMAIL", "SESSION_SECRET", "DATABASE_URI",
    "DB_POOL_MIN", "DB_POOL_MAX", "DB_CONNECT_TIMEOUT", "DB_APP_NAME",
    "EMAIL_OFFSETS", "ENABLE_EMAIL", "EMAIL_TO", "EMAIL_FROM",
    "RESEND_API_KEY", "CRON_NOTIFY_PLATFORM", "CRON_NOTIFY_CHAT_ID",
    "WEB_HOST", "WEB_PORT", "WEB_RELOAD",
]


def test_every_setting_survives_being_present_but_empty(monkeypatch):
    """This is exactly the Vercel failure: keys added, values left blank."""
    for name in BLANK_VARS:
        monkeypatch.setenv(name, "")

    import importlib

    import web.config
    importlib.reload(web.config)

    settings = web.config.Settings()
    assert settings.auth_mode == "dev"          # blank -> the default
    assert web.config.env_int("WEB_SOON_DAYS", 30) == 30
    assert settings.session_max_age == 60 * 60 * 12
    assert settings.session_https_only is False
    # dev mode with a blank secret generates one, so nothing blocks.
    assert settings.config_errors == []


def test_blank_enable_email_does_not_silently_disable_email(monkeypatch):
    # A blank ENABLE_EMAIL must read as "not set" and fall through to the key,
    # not as "off".
    from utils import email_digest as ed

    monkeypatch.setenv("ENABLE_EMAIL", "")
    monkeypatch.setenv("RESEND_API_KEY", "re_test")
    assert ed._enabled() is True

    monkeypatch.setenv("ENABLE_EMAIL", "0")
    assert ed._enabled() is False


def test_blank_database_uri_is_refused_not_sent_to_psycopg2(monkeypatch):
    """An empty DSN makes psycopg2 fall back to a local Unix socket and fail
    with 'No such file or directory', which reads like a broken server rather
    than an unset variable. This is what the Vercel deploy actually hit."""
    import db.client as client

    monkeypatch.setenv("DATABASE_URI", "")
    monkeypatch.setattr(client, "_POOL", None)
    with pytest.raises(client.DatabaseUnavailable, match="DATABASE_URI is not set"):
        client._get_pool()


def test_missing_database_uri_is_refused_too(monkeypatch):
    import db.client as client

    monkeypatch.delenv("DATABASE_URI", raising=False)
    monkeypatch.setattr(client, "_POOL", None)
    with pytest.raises(client.DatabaseUnavailable, match="pooler"):
        client._get_pool()


# ── session secret on serverless ─────────────────────────────────────────


def test_generated_session_secret_is_refused_on_serverless(monkeypatch):
    """Each instance would generate its own, so a cookie signed by one fails
    on the next: sign in, then every subsequent page bounces to /login."""
    import web.config as cfg

    monkeypatch.setenv("AUTH_MODE", "dev")
    monkeypatch.delenv("SESSION_SECRET", raising=False)
    monkeypatch.setenv("VERCEL", "1")
    settings = cfg.Settings()
    assert any("serverless" in e for e in settings.config_errors)


def test_generated_session_secret_is_fine_for_a_local_process(monkeypatch):
    import web.config as cfg

    monkeypatch.setenv("AUTH_MODE", "dev")
    monkeypatch.delenv("SESSION_SECRET", raising=False)
    for marker in cfg.SERVERLESS_MARKERS:
        monkeypatch.delenv(marker, raising=False)
    settings = cfg.Settings()
    assert settings.config_errors == []
    assert settings.session_secret


def test_an_explicit_secret_satisfies_serverless(monkeypatch):
    import web.config as cfg

    monkeypatch.setenv("AUTH_MODE", "dev")
    monkeypatch.setenv("SESSION_SECRET", "a-real-secret")
    monkeypatch.setenv("VERCEL", "1")
    settings = cfg.Settings()
    assert settings.config_errors == []
    assert settings.session_secret == "a-real-secret"
