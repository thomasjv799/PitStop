"""Environment-backed settings for the web tier.

Read at import time so a missing SESSION_SECRET fails at start-up rather
than on the first request that tries to set a cookie.
"""

import os
import secrets

from utils.env import env_bool, env_int, env_str

# Google's OIDC discovery document lives at a fixed, well-known address.
GOOGLE_ISSUER = "https://accounts.google.com"

# The five expiry columns, in the order the dashboard shows them. The short
# label is the matrix column head; the long one is what the action dialog and
# the reminder copy say.
DOCUMENTS: tuple[tuple[str, str, str], ...] = (
    ("insurance_valid_until", "Insurance", "Insurance"),
    ("pucc_valid_until", "PUCC", "Pollution (PUCC)"),
    ("fitness_valid_until", "Fitness", "Fitness / RC"),
    ("mv_tax_valid_until", "MV Tax", "MV Tax"),
    ("permit_valid_until", "Permit", "Permit"),
)

DOCUMENT_FIELDS = tuple(f for f, _, _ in DOCUMENTS)
LONG_LABELS = {f: long for f, _, long in DOCUMENTS}

# The cron sweep's escalating schedule, in cron/reminder_sweep.py order.
REMINDER_OFFSETS = (-7, -3, -1, 0, 1, 3, 7, 15, 30)

# Snooze windows offered in the UI. "forever" stores NULL in
# reminder_snooze.snoozed_until, which the schema reads as a permanent ignore.
SNOOZE_OPTIONS = (("7", "7 days"), ("14", "14 days"), ("30", "30 days"),
                  ("forever", "Indefinitely"))

# The timeline page's window, in days either side of today.
TIMELINE_PAST = 60
TIMELINE_FUTURE = 90

# The nav. Adding a page is one line here — nothing else enumerates them.
NAV = (
    ("dashboard", "/", "Dashboard"),
    ("fleet", "/fleet", "Fleet"),
    ("timeline", "/timeline", "Timeline"),
    ("costs", "/costs", "Costs"),
)

# Shown only to admins, appended to NAV.
ADMIN_NAV = (("users", "/admin/users", "People"),)

# A document this many days out or nearer counts as "due soon".
SOON_DAYS = env_int("WEB_SOON_DAYS", 30)


class Settings:
    """Resolved once at import; see .env.example for the full list."""

    def __init__(self) -> None:
        # dev    — DEV_USER is signed in by the sign-in button, no IdP, and
        #          treated as an approved admin. Local work only.
        # google — Google sign-in. Anyone with a Google account can sign in;
        #          an admin must approve them before they see any fleet data.
        self.auth_mode = env_str("AUTH_MODE", "dev").lower()
        self.dev_user = env_str("DEV_USER", "dev")
        self.dev_user_name = env_str("DEV_USER_NAME", "Local dev")

        self.oidc_issuer = env_str("OIDC_ISSUER", GOOGLE_ISSUER).rstrip("/")
        self.oidc_client_id = env_str("OIDC_CLIENT_ID")
        self.oidc_client_secret = env_str("OIDC_CLIENT_SECRET")
        self.oidc_scopes = env_str("OIDC_SCOPES", "openid email profile")
        self.oidc_provider_name = env_str("OIDC_PROVIDER_NAME", "Google")

        # The callback URL registered with the provider. Normally derived
        # from the request, but behind a TLS-terminating reverse proxy that
        # yields http:// while the provider has https:// registered, and the
        # handshake fails with redirect_uri_mismatch. Set this explicitly and
        # the guesswork goes away.
        self.oidc_redirect_uri = env_str("OIDC_REDIRECT_URI")

        # The owner. On their first sign-in this address is approved as an
        # admin automatically — otherwise there would be nobody able to
        # approve the first account. Matched case-insensitively, and only
        # ever applied when creating the row.
        self.admin_email = env_str("ADMIN_EMAIL").lower()

        # Misconfiguration is collected, not raised. Raising here happens at
        # import, which on a serverless host means the function dies before it
        # can say why — an opaque 500 with the reason only in a log you have to
        # go find. The app starts, refuses to serve, and explains itself.
        # Two kinds of wrong, and they deserve different treatment.
        #   errors   — cannot serve safely at all (auth and session config).
        #              Every request is refused.
        #   warnings — will fail when it is used, not before. DATABASE_URI is
        #              read lazily, and the tests deliberately leave it unset so
        #              the integration suite skips; blocking on it would mean
        #              the app refuses to start in its own test environment.
        self.config_errors: list[str] = []
        self.config_warnings: list[str] = []

        # A generated secret is fine for AUTH_MODE=dev — it only means sessions
        # do not survive a restart — but a real sign-in must not silently
        # invalidate its own state cookie mid-handshake, or between instances.
        secret = env_str("SESSION_SECRET")
        if not secret:
            if self.auth_mode != "dev":
                self.config_errors.append(
                    "SESSION_SECRET is required when AUTH_MODE is not 'dev'. "
                    "Generate one with: "
                    'python -c "import secrets;print(secrets.token_urlsafe(32))"'
                )
            secret = secrets.token_urlsafe(32)
        self.session_secret = secret

        if self.auth_mode not in ("dev", "google", "oidc"):
            self.config_errors.append(
                f"AUTH_MODE={self.auth_mode!r} is not one of: dev, google"
            )
        if self.is_oidc and not self.oidc_client_id:
            self.config_errors.append(
                f"OIDC_CLIENT_ID is required when AUTH_MODE={self.auth_mode}"
            )
        if self.is_oidc and not self.oidc_client_secret:
            self.config_errors.append(
                f"OIDC_CLIENT_SECRET is required when AUTH_MODE={self.auth_mode}"
            )
        if self.is_oidc and not self.admin_email:
            self.config_errors.append(
                "ADMIN_EMAIL is required when AUTH_MODE is not 'dev' — without "
                "it no account can ever be approved, because there is no admin"
            )
        if not env_str("DATABASE_URI"):
            self.config_warnings.append(
                "DATABASE_URI is not set — pages that read the fleet will fail"
            )

        self.session_https_only = env_bool("SESSION_HTTPS_ONLY", False)
        self.session_max_age = env_int("SESSION_MAX_AGE", 60 * 60 * 12)

    @property
    def is_oidc(self) -> bool:
        """True whenever a real identity provider is in play."""
        return self.auth_mode != "dev"

    @property
    def issuer_host(self) -> str:
        """Just the host, for the 'you'll be redirected to …' line."""
        without_scheme = self.oidc_issuer.split("://", 1)[-1]
        return without_scheme.split("/", 1)[0]


settings = Settings()
