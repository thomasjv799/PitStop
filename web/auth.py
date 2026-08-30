"""Sign-in and the approval gate.

`AUTH_MODE=google` runs Google's OIDC authorization-code flow. `dev`
short-circuits it to DEV_USER so the app runs locally without a Google
client. Both end in the same place: a `user` dict in the signed session.

Signing in and being allowed to see anything are two separate things.
Anyone with a Google account can complete the flow; they land in
`web_users` with `approved_at` NULL and see the waiting-room page until an
admin approves them. The account is re-read from the database on every
gated request, so revoking access takes effect on the next page load rather
than whenever the visitor's session happens to expire.
"""

import logging
from typing import Any, Optional

from fastapi import HTTPException, Request, status
from fastapi.responses import RedirectResponse

from db import client as db
from utils import redact
from web.config import settings

logger = logging.getLogger(__name__)

SESSION_KEY = "user"

_oauth = None


def oauth():
    """The Authlib client, built lazily so `dev` never imports authlib."""
    global _oauth
    if _oauth is None:
        from authlib.integrations.starlette_client import OAuth

        if not settings.oidc_client_id:
            raise RuntimeError(f"AUTH_MODE={settings.auth_mode} needs OIDC_CLIENT_ID")
        registry = OAuth()
        registry.register(
            name="idp",
            server_metadata_url=(
                f"{settings.oidc_issuer}/.well-known/openid-configuration"
            ),
            client_id=settings.oidc_client_id,
            client_secret=settings.oidc_client_secret,
            client_kwargs={"scope": settings.oidc_scopes},
        )
        _oauth = registry
    return _oauth


# ── session ──────────────────────────────────────────────────────────────


def current_user(request: Request) -> Optional[dict[str, Any]]:
    user = request.session.get(SESSION_KEY)
    return user if isinstance(user, dict) else None


def _deny(request: Request, location: str, detail: str) -> HTTPException:
    """Redirect a browser, but give a fetch() an honest status code."""
    if request.headers.get("accept", "").startswith("application/json"):
        return HTTPException(status.HTTP_403_FORBIDDEN, detail)
    return HTTPException(
        status.HTTP_303_SEE_OTHER, detail, headers={"Location": location}
    )


def require_user(request: Request) -> dict[str, Any]:
    """Signed in — but not necessarily allowed to see anything."""
    user = current_user(request)
    if user:
        return user
    if request.headers.get("accept", "").startswith("application/json"):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Not signed in")
    raise HTTPException(
        status.HTTP_303_SEE_OTHER, "Not signed in", headers={"Location": "/login"}
    )


def account(request: Request) -> dict[str, Any]:
    """The live account behind the session.

    In dev mode there is no `web_users` row and no database dependency: the
    single local user is an approved admin by construction.
    """
    user = require_user(request)
    if user.get("mode") == "dev":
        return {**user, "role": "admin", "approved": True}

    row = db.get_web_user(user["sub"])
    if row is None:
        # The account was deleted while the session was still alive.
        request.session.pop(SESSION_KEY, None)
        raise _deny(request, "/login", "Account no longer exists")
    return {
        **user,
        "email": row["email"],
        "name": row["name"] or user.get("name", ""),
        "role": row["role"],
        "approved": row["approved_at"] is not None,
    }


def require_approved(request: Request) -> dict[str, Any]:
    """The gate on every page that shows fleet data."""
    user = account(request)
    if not user["approved"]:
        raise _deny(request, "/pending", "Awaiting approval")
    return user


def require_admin(request: Request) -> dict[str, Any]:
    user = require_approved(request)
    if user["role"] != "admin":
        raise _deny(request, "/", "Admins only")
    return user


def actor(user: dict[str, Any]) -> str:
    """`web:<sub>` — how this user is recorded on rows they change."""
    return f"web:{user.get('sub', 'unknown')}"


# ── the two sign-in paths ────────────────────────────────────────────────


def sign_in_dev(request: Request) -> RedirectResponse:
    request.session[SESSION_KEY] = {
        "sub": settings.dev_user,
        "name": settings.dev_user_name,
        "email": "",
        "mode": "dev",
    }
    logger.info("dev sign-in as %s", settings.dev_user)
    return RedirectResponse("/", status_code=status.HTTP_303_SEE_OTHER)


async def begin_oidc(request: Request, redirect_uri: str):
    return await oauth().create_client("idp").authorize_redirect(request, redirect_uri)


async def complete_oidc(request: Request) -> dict[str, Any]:
    """Finish the flow, record the account, and report where to go next."""
    client = oauth().create_client("idp")
    token = await client.authorize_access_token(request)
    claims = token.get("userinfo") or await client.userinfo(token=token)

    subject = claims.get("sub")
    if not subject:
        raise RuntimeError("identity provider returned no subject claim")
    email = claims.get("email", "")

    # Google is the only provider we ask for `email`, and an unverified one
    # must not be able to claim the owner's address.
    verified = claims.get("email_verified", True)
    is_owner = bool(
        verified and settings.admin_email and email.lower() == settings.admin_email
    )

    row = db.upsert_web_user(
        subject=subject,
        email=email,
        name=claims.get("name") or email,
        bootstrap_admin=is_owner,
    )

    request.session[SESSION_KEY] = {
        "sub": subject,
        "name": row["name"] or email,
        "email": email,
        "mode": settings.auth_mode,
    }
    approved = row["approved_at"] is not None
    logger.info(
        "%s sign-in as %s (role=%s, approved=%s)",
        settings.auth_mode, redact.email(email), row["role"], approved,
    )
    return {"approved": approved, "role": row["role"]}


def sign_out(request: Request) -> None:
    request.session.pop(SESSION_KEY, None)
