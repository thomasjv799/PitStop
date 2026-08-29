"""FastAPI app: dashboard, fleet matrix, timeline, vehicle detail, costs stub.

Every write goes through db/client.py — the same helpers the bot's tools
call — so the web tier adds no second way to change a vehicle.
"""

import logging
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Optional
from urllib.parse import urlencode

from fastapi import Depends, FastAPI, Form, HTTPException, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

from db import client as db
from web import auth, service
from web.config import (
    ADMIN_NAV, DOCUMENT_FIELDS, DOCUMENTS, LONG_LABELS, NAV, SNOOZE_OPTIONS,
    SOON_DAYS, TIMELINE_FUTURE, TIMELINE_PAST, settings,
)

logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).parent

app = FastAPI(title="PitStop", docs_url=None, redoc_url=None)
app.add_middleware(
    SessionMiddleware,
    secret_key=settings.session_secret,
    max_age=settings.session_max_age,
    https_only=settings.session_https_only,
    same_site="lax",
)
app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")

templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))
templates.env.globals.update(
    provider_name=settings.oidc_provider_name,
    documents=DOCUMENTS,
    snooze_options=SNOOZE_OPTIONS,
    soon_days=SOON_DAYS,
)


def _today() -> date:
    return date.today()


def _load_fleet(today: date, include_archived: bool = False) -> list[dict]:
    return service.build_fleet(
        db.get_all_vehicles_with_expiry(include_archived=include_archived),
        today,
        db.get_active_snoozes(),
        db.get_reminder_counts(),
        SOON_DAYS,
    )


def _sweep_line() -> str:
    """`Last sweep 30 Aug 2026, 07:00 · 2 reminders sent`, or ''."""
    try:
        sweep = db.get_last_sweep()
    except Exception:
        logger.warning("could not read last sweep", exc_info=True)
        return ""
    if not sweep or not sweep.get("last_sent"):
        return ""
    when = sweep["last_sent"]
    stamp = when.strftime("%d %b %Y, %H:%M") if isinstance(when, datetime) else str(when)
    count = int(sweep.get("sent") or 0)
    return f"Last sweep {stamp} · {count} reminder{'s' if count != 1 else ''} sent"


def _nav_for(user: dict) -> tuple[tuple[str, str, str], ...]:
    return NAV + ADMIN_NAV if user.get("role") == "admin" else NAV


def _pending_badge(user: dict) -> int:
    """How many accounts are waiting — shown on the admin's Users tab."""
    if user.get("role") != "admin" or user.get("mode") == "dev":
        return 0
    try:
        return db.count_pending_web_users()
    except Exception:
        logger.warning("could not count pending users", exc_info=True)
        return 0


def _page(request: Request, name: str, active: str, *, user: dict, **context):
    return templates.TemplateResponse(request, name, {
        "active": active,
        "user": user,
        "nav": _nav_for(user),
        "pending_badge": _pending_badge(user),
        "today_text": service.format_long_date(_today()),
        **context,
    })


# ── sign-in ──────────────────────────────────────────────────────────────


@app.get("/login", response_class=HTMLResponse)
def login(request: Request, error: Optional[str] = None):
    if auth.current_user(request):
        return RedirectResponse("/", status_code=status.HTTP_303_SEE_OTHER)
    # Nothing about the fleet — not even counts — is shown before sign-in.
    # With Google as the provider anyone can reach this page, and access to
    # the data is the thing approval is meant to gate.
    return templates.TemplateResponse(
        request, "login.html",
        {"settings": settings, "error": error, "labels": [d[2] for d in DOCUMENTS]},
    )


@app.post("/login")
async def start_login(request: Request):
    if not settings.is_oidc:
        return auth.sign_in_dev(request)
    return await auth.begin_oidc(request, str(request.url_for("callback")))


@app.get("/auth/callback", name="callback")
async def callback(request: Request):
    if not settings.is_oidc:
        return RedirectResponse("/login", status_code=status.HTTP_303_SEE_OTHER)
    try:
        result = await auth.complete_oidc(request)
    except Exception:
        logger.exception("sign-in callback failed")
        return RedirectResponse(
            "/login?error=Sign-in+failed.+Please+try+again.",
            status_code=status.HTTP_303_SEE_OTHER,
        )
    destination = "/" if result["approved"] else "/pending"
    return RedirectResponse(destination, status_code=status.HTTP_303_SEE_OTHER)


@app.post("/logout")
def logout(request: Request):
    auth.sign_out(request)
    return RedirectResponse("/login", status_code=status.HTTP_303_SEE_OTHER)


# ── pages ────────────────────────────────────────────────────────────────


@app.get("/", response_class=HTMLResponse)
def dashboard(
    request: Request,
    notice: Optional[str] = None,
    error: Optional[str] = None,
    user: dict = Depends(auth.require_approved),
):
    """Counts, then the queue of documents that actually need doing."""
    today = _today()
    fleet = _load_fleet(today)
    return _page(
        request, "dashboard.html", "dashboard",
        user=user,
        stats=service.summarise(fleet),
        queue=service.queue_items(fleet),
        sweep=_sweep_line(),
        notice=notice, error=error,
    )


@app.get("/fleet", response_class=HTMLResponse)
def fleet(
    request: Request,
    scope: str = "all",
    notice: Optional[str] = None,
    error: Optional[str] = None,
    user: dict = Depends(auth.require_approved),
):
    today = _today()
    scope = scope if scope in {"all", "attention", "archived"} else "all"

    if scope == "archived":
        rows = service.build_fleet(db.get_archived_vehicles(), today, db.get_active_snoozes())
        stats = service.summarise(_load_fleet(today))
    else:
        live = _load_fleet(today)
        rows = service.filter_rows(live, scope)
        stats = service.summarise(live)

    return _page(
        request, "fleet.html", "fleet",
        user=user, rows=rows, stats=stats, scope=scope,
        sweep=_sweep_line(), notice=notice, error=error,
    )


@app.get("/timeline", response_class=HTMLResponse)
def timeline(request: Request, user: dict = Depends(auth.require_approved)):
    today = _today()
    fleet = _load_fleet(today)
    rows = service.timeline_rows(fleet)
    return _page(
        request, "timeline.html", "timeline",
        user=user, rows=rows, stats=service.summarise(fleet),
        clear=len(fleet) - len(rows),
        today_position=service.TODAY_POSITION,
        past=TIMELINE_PAST, future=TIMELINE_FUTURE,
        ticks=[
            {"label": f"−{TIMELINE_PAST}d", "left": 0},
            {"label": "TODAY", "left": service.TODAY_POSITION, "is_today": True},
            {"label": "+30d", "left": service.timeline_position(30)},
            {"label": "+60d", "left": service.timeline_position(60)},
            {"label": f"+{TIMELINE_FUTURE}d", "left": 100},
        ],
    )


@app.get("/vehicles/{registration}", response_class=HTMLResponse)
def vehicle_detail(
    request: Request,
    registration: str,
    notice: Optional[str] = None,
    error: Optional[str] = None,
    user: dict = Depends(auth.require_approved),
):
    vehicle = db.get_vehicle(registration)
    if not vehicle:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"No vehicle {registration}")
    detail = service.build_detail(
        vehicle, _today(), db.get_active_snoozes(), db.get_reminder_offsets(vehicle["id"])
    )
    return _page(
        request, "vehicle.html", "fleet",
        user=user, v=detail, notice=notice, error=error,
    )


@app.get("/costs", response_class=HTMLResponse)
def costs(request: Request, user: dict = Depends(auth.require_approved)):
    """Placeholder. Reads nothing — see CLAUDE.md for where this is going."""
    return _page(request, "costs.html", "costs", user=user)


@app.get("/pending", response_class=HTMLResponse)
def pending(request: Request):
    """Signed in, not yet approved. The only page such an account can see."""
    user = auth.account(request)
    if user["approved"]:
        return RedirectResponse("/", status_code=status.HTTP_303_SEE_OTHER)
    return templates.TemplateResponse(
        request, "pending.html", {"user": user, "admin_email": settings.admin_email}
    )


# ── admin: who can see the fleet ─────────────────────────────────────────


@app.get("/admin/users", response_class=HTMLResponse)
def admin_users(
    request: Request,
    notice: Optional[str] = None,
    error: Optional[str] = None,
    user: dict = Depends(auth.require_admin),
):
    if user.get("mode") == "dev":
        return _page(request, "users.html", "users", user=user, users=[], dev=True,
                     notice=notice, error=error)
    return _page(
        request, "users.html", "users", user=user,
        users=service.build_users(db.list_web_users()), dev=False,
        notice=notice, error=error,
    )


@app.post("/admin/users/{subject}/approve")
def approve_user(
    subject: str,
    approved: str = Form("1"),
    user: dict = Depends(auth.require_admin),
):
    want = approved not in ("0", "false", "")
    # Revoking your own approval would lock you out of the page that undoes it.
    if not want and subject == user["sub"]:
        return _back("/admin/users", error="You cannot revoke your own access.")
    if not want and db.count_web_admins(exclude_subject=subject) == 0:
        return _back("/admin/users", error="That is the last admin — approve someone else first.")
    if not db.set_web_user_approved(subject, want, auth.actor(user)):
        return _back("/admin/users", error="No such account.")
    logger.info("%s %s %s", auth.actor(user), "approved" if want else "revoked", subject)
    return _back("/admin/users", notice="Access granted." if want else "Access revoked.")


@app.post("/admin/users/{subject}/role")
def set_role(
    subject: str,
    role: str = Form("member"),
    user: dict = Depends(auth.require_admin),
):
    if role not in ("admin", "member"):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Unknown role {role!r}")
    if role == "member" and db.count_web_admins(exclude_subject=subject) == 0:
        return _back("/admin/users", error="That is the last admin — promote someone else first.")
    if not db.set_web_user_role(subject, role):
        return _back("/admin/users", error="No such account.")
    logger.info("%s set %s to %s", auth.actor(user), subject, role)
    return _back("/admin/users", notice=f"Role set to {role}.")


@app.post("/admin/users/{subject}/delete")
def delete_user(subject: str, user: dict = Depends(auth.require_admin)):
    """Remove the account row. They can sign in again, but land unapproved."""
    if subject == user["sub"]:
        return _back("/admin/users", error="You cannot remove your own account.")
    if db.count_web_admins(exclude_subject=subject) == 0:
        return _back("/admin/users", error="That is the last admin.")
    if not db.delete_web_user(subject):
        return _back("/admin/users", error="No such account.")
    logger.warning("%s removed account %s", auth.actor(user), subject)
    return _back("/admin/users", notice="Account removed.")


@app.get("/healthz")
def healthz():
    return {"status": "ok"}


# ── actions ──────────────────────────────────────────────────────────────


def _back(to: str, *, notice: str = "", error: str = "") -> RedirectResponse:
    params = {k: v for k, v in (("notice", notice), ("error", error)) if v}
    sep = "&" if "?" in to else "?"
    url = f"{to}{sep}{urlencode(params)}" if params else to
    return RedirectResponse(url, status_code=status.HTTP_303_SEE_OTHER)


def _check_field(field: str) -> None:
    if field not in DOCUMENT_FIELDS:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Unknown document {field!r}")


@app.post("/vehicles/{registration}/renew")
def renew(
    registration: str,
    field: str = Form(...),
    new_date: str = Form(...),
    back: str = Form("/"),
    user: dict = Depends(auth.require_approved),
):
    """Set a new expiry date.

    Writing a new date is all that is needed to restart the reminder cycle:
    reminder_log is keyed on the expiry date, so rows against the old date
    stop matching and the sweep begins again at −7d.
    """
    _check_field(field)
    try:
        parsed = date.fromisoformat(new_date)
    except ValueError:
        return _back(back, error=f"{new_date!r} is not a valid date.")

    if not db.update_vehicle_field(registration, field, parsed.isoformat()):
        return _back(back, error=f"No vehicle {registration}.")

    logger.info("%s renewed %s.%s to %s", auth.actor(user), registration, field, parsed)
    return _back(back, notice=(
        f"{LONG_LABELS[field]} for {registration} now expires "
        f"{service.format_long_date(parsed)}."
    ))


@app.post("/vehicles/{vehicle_id}/snooze")
def snooze(
    vehicle_id: int,
    field: str = Form(...),
    duration: str = Form("30"),
    reason: str = Form(""),
    back: str = Form("/"),
    user: dict = Depends(auth.require_approved),
):
    """Suppress reminders for one document — for N days, or indefinitely."""
    _check_field(field)
    if duration == "forever":
        until, window = None, "indefinitely"
    else:
        try:
            days = int(duration)
        except ValueError:
            return _back(back, error=f"{duration!r} is not a number of days.")
        if days < 1:
            return _back(back, error="Snooze at least one day.")
        until = date.today() + timedelta(days=days)
        window = f"until {service.format_long_date(until)}"

    db.snooze_reminder(vehicle_id, field, until, reason.strip(), auth.actor(user))
    logger.info("%s snoozed %s.%s %s", auth.actor(user), vehicle_id, field, window)
    return _back(back, notice=f"{LONG_LABELS[field]} snoozed {window}.")


@app.post("/vehicles/{vehicle_id}/unsnooze")
def unsnooze(
    vehicle_id: int,
    field: str = Form(...),
    back: str = Form("/"),
    user: dict = Depends(auth.require_approved),
):
    _check_field(field)
    if not db.unsnooze_reminder(vehicle_id, field):
        return _back(back, error="That document was not snoozed.")
    logger.info("%s unsnoozed %s.%s", auth.actor(user), vehicle_id, field)
    return _back(back, notice=f"{LONG_LABELS[field]} is back in the reminder cycle.")


@app.post("/vehicles/{registration}/archive")
def archive(
    registration: str,
    archived: str = Form("1"),
    user: dict = Depends(auth.require_approved),
):
    """Archive or restore. Reversible: the row and its history stay put."""
    want = archived not in ("0", "false", "")
    if not db.set_vehicle_archived(registration, want):
        return _back("/fleet", error=f"No vehicle {registration}.")
    logger.info("%s %s %s", auth.actor(user), "archived" if want else "restored", registration)
    if want:
        return _back("/fleet", notice=f"{registration} archived — it will not be reminded about.")
    return _back(f"/vehicles/{registration}", notice=f"{registration} restored.")


@app.post("/vehicles/{registration}/delete")
def delete(
    registration: str,
    confirm: str = Form(""),
    user: dict = Depends(auth.require_approved),
):
    """Permanently remove a vehicle and its reminder history.

    The typed registration must match — this is the one action in the app
    with no undo, and a misplaced click should not be enough to trigger it.
    """
    if confirm.strip().upper() != registration.upper():
        return _back(
            f"/vehicles/{registration}",
            error="Type the registration exactly to confirm deletion.",
        )
    if not db.delete_vehicle(registration):
        return _back("/fleet", error=f"No vehicle {registration}.")
    logger.warning("%s permanently deleted %s", auth.actor(user), registration)
    return _back("/fleet", notice=f"{registration} and its reminder history were deleted.")
