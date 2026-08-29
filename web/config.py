"""Environment-backed settings for the web tier.

Read at import time so a missing SESSION_SECRET fails at start-up rather
than on the first request that tries to set a cookie.
"""

import os
import secrets

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
ADMIN_NAV = (("users", "/admin/users", "Users"),)

# A document this many days out or nearer counts as "due soon".
SOON_DAYS = int(os.getenv("WEB_SOON_DAYS", "30"))


def _truthy(value: str | None) -> bool:
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


class Settings:
    """Resolved once at import; see .env.example for the full list."""

    def __init__(self) -> None:
        # dev    — DEV_USER is signed in by the sign-in button, no IdP, and
        #          treated as an approved admin. Local work only.
        # google — Google sign-in. Anyone with a Google account can sign in;
        #          an admin must approve them before they see any fleet data.
        self.auth_mode = os.getenv("AUTH_MODE", "dev").strip().lower()
        self.dev_user = os.getenv("DEV_USER", "dev")
        self.dev_user_name = os.getenv("DEV_USER_NAME", "Local dev")

        self.oidc_issuer = os.getenv("OIDC_ISSUER", GOOGLE_ISSUER).rstrip("/")
        self.oidc_client_id = os.getenv("OIDC_CLIENT_ID", "")
        self.oidc_client_secret = os.getenv("OIDC_CLIENT_SECRET", "")
        self.oidc_scopes = os.getenv("OIDC_SCOPES", "openid email profile")
        self.oidc_provider_name = os.getenv("OIDC_PROVIDER_NAME", "Google")

        # The owner. On their first sign-in this address is approved as an
        # admin automatically — otherwise there would be nobody able to
        # approve the first account. Matched case-insensitively, and only
        # ever applied when creating the row.
        self.admin_email = os.getenv("ADMIN_EMAIL", "").strip().lower()

        # A generated secret is fine for AUTH_MODE=dev — it only means
        # sessions do not survive a restart — but a real sign-in must not
        # silently invalidate its own state cookie mid-handshake.
        secret = os.getenv("SESSION_SECRET", "")
        if not secret:
            if self.auth_mode != "dev":
                raise RuntimeError(
                    "SESSION_SECRET is required when AUTH_MODE is not 'dev'"
                )
            secret = secrets.token_urlsafe(32)
        self.session_secret = secret

        self.session_https_only = _truthy(os.getenv("SESSION_HTTPS_ONLY", "0"))
        self.session_max_age = int(os.getenv("SESSION_MAX_AGE", str(60 * 60 * 12)))

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
