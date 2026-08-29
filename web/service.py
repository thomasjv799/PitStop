"""Turning vehicle rows into the dashboard's view model.

Pure functions over plain dicts — no database, no request. The routes fetch
rows and hand them here; the templates only render what comes back.
"""

from datetime import date
from typing import Any, Iterable, Optional

from web.config import (
    DOCUMENTS, REMINDER_OFFSETS, SOON_DAYS, TIMELINE_FUTURE, TIMELINE_PAST,
)

# U+2212 MINUS SIGN — the design uses it for overdue counts, and it lines up
# with digits in a way the ASCII hyphen does not.
MINUS = "−"

# Statuses that put a vehicle in the "needs attention" bucket.
ATTENTION = ("overdue", "soon")


def format_date(value: Optional[date]) -> str:
    """`12 Jan 27` — the compact form the matrix uses."""
    return value.strftime("%d %b %y") if value else ""


def format_long_date(value: Optional[date]) -> str:
    """`12 Jan 2027` — for the action dialog, where there is room."""
    return value.strftime("%d %b %Y") if value else ""


def format_days(days: int) -> str:
    """`5d` ahead, `−11d` behind."""
    return f"{days}d" if days >= 0 else f"{MINUS}{abs(days)}d"


def classify(days: Optional[int], snoozed: bool, soon_days: int = SOON_DAYS) -> str:
    """One of: na, snoozed, overdue, soon, ok.

    Snooze wins over urgency because a snoozed document is deliberately out
    of the reminder cycle — showing it as overdue would re-raise something
    the user already dismissed.
    """
    if days is None:
        return "na"
    if snoozed:
        return "snoozed"
    if days < 0:
        return "overdue"
    if days <= soon_days:
        return "soon"
    return "ok"


def next_offset(days: Optional[int]) -> Optional[int]:
    """The next cron offset that will fire for a document `days` out.

    The sweep fires at `expiry_date + offset`, so an offset is still ahead
    of today exactly when `offset > -days`.
    """
    if days is None:
        return None
    ahead = [o for o in REMINDER_OFFSETS if o > -days]
    return min(ahead) if ahead else None


def offset_date(expiry: date, offset: int) -> date:
    from datetime import timedelta

    return expiry + timedelta(days=offset)


def build_cell(
    vehicle: dict,
    field: str,
    short_label: str,
    long_label: str,
    today: date,
    snoozes: dict[tuple[int, str], dict],
    reminders: dict[tuple[int, str, date], dict],
    soon_days: int = SOON_DAYS,
) -> dict[str, Any]:
    """One document of one vehicle, ready to render."""
    expiry = vehicle.get(field)
    snooze = snoozes.get((vehicle["id"], field))
    days = (expiry - today).days if expiry else None
    status = classify(days, snooze is not None, soon_days)

    sent = 0
    if expiry:
        log = reminders.get((vehicle["id"], field, expiry))
        sent = int(log["sent"]) if log else 0

    upcoming = next_offset(days)
    return {
        "field": field,
        "label": short_label,
        "long_label": long_label,
        "date": expiry,
        "date_text": format_date(expiry),
        "long_date_text": format_long_date(expiry),
        "days": days,
        "days_text": format_days(days) if days is not None else "",
        "status": status,
        "snooze": snooze,
        "snoozed_until": snooze.get("snoozed_until") if snooze else None,
        "snooze_reason": (snooze.get("reason") if snooze else None) or "",
        "snooze_by": (snooze.get("created_by") if snooze else None) or "",
        "reminders_sent": sent,
        "reminders_total": len(REMINDER_OFFSETS),
        "next_offset": upcoming,
        "next_offset_date": (
            format_date(offset_date(expiry, upcoming))
            if expiry is not None and upcoming is not None
            else ""
        ),
    }


def build_row(
    vehicle: dict,
    today: date,
    snoozes: dict[tuple[int, str], dict],
    reminders: dict[tuple[int, str, date], dict],
    soon_days: int = SOON_DAYS,
) -> dict[str, Any]:
    cells = [
        build_cell(vehicle, field, short, long, today, snoozes, reminders, soon_days)
        for field, short, long in DOCUMENTS
    ]
    statuses = {c["status"] for c in cells}
    return {
        "id": vehicle["id"],
        "nickname": vehicle.get("nickname") or vehicle["registration_number"],
        "registration_number": vehicle["registration_number"],
        "owner_name": vehicle.get("owner_name") or "",
        "cells": cells,
        # A row's own worst status, used for sorting and for the filter.
        "worst": (
            "overdue" if "overdue" in statuses
            else "soon" if "soon" in statuses
            else "ok"
        ),
        "soonest": min(
            (c["days"] for c in cells if c["days"] is not None and c["status"] != "snoozed"),
            default=None,
        ),
    }


def index_snoozes(rows: Iterable[dict]) -> dict[tuple[int, str], dict]:
    return {(r["vehicle_id"], r["expiry_field"]): dict(r) for r in rows}


def index_reminders(rows: Iterable[dict]) -> dict[tuple[int, str, date], dict]:
    return {
        (r["vehicle_id"], r["expiry_field"], r["expiry_date"]): dict(r) for r in rows
    }


def build_fleet(
    vehicles: Iterable[dict],
    today: date,
    snooze_rows: Iterable[dict] = (),
    reminder_rows: Iterable[dict] = (),
    soon_days: int = SOON_DAYS,
) -> list[dict[str, Any]]:
    """Every vehicle as a matrix row, nearest expiry first."""
    snoozes = index_snoozes(snooze_rows)
    reminders = index_reminders(reminder_rows)
    rows = [build_row(v, today, snoozes, reminders, soon_days) for v in vehicles]
    # Vehicles with nothing dated at all sink to the bottom rather than
    # sorting alongside the most urgent.
    rows.sort(key=lambda r: (r["soonest"] is None, r["soonest"] or 0, r["nickname"]))
    return rows


def summarise(rows: Iterable[dict]) -> dict[str, int]:
    """Fleet counts for the header and the sign-in panel.

    `overdue` and `soon` are disjoint vehicle counts — a vehicle with one
    overdue document and one due next week is counted once, as overdue —
    so `overdue + soon` is the number of vehicles needing attention.
    """
    rows = list(rows)
    return {
        "total": len(rows),
        "overdue": sum(1 for r in rows if r["worst"] == "overdue"),
        "soon": sum(1 for r in rows if r["worst"] == "soon"),
        "attention": sum(1 for r in rows if r["worst"] in ATTENTION),
    }


def filter_rows(rows: Iterable[dict], scope: str) -> list[dict]:
    """`all` or `attention` — the two filter buttons on the header."""
    rows = list(rows)
    if scope == "attention":
        return [r for r in rows if r["worst"] in ATTENTION]
    return rows


# ── the action queue (dashboard) ─────────────────────────────────────────


def queue_items(rows: Iterable[dict]) -> list[dict[str, Any]]:
    """Every document that needs doing, most urgent first.

    One entry per *document*, not per vehicle — a vehicle with two lapsed
    documents is two things to deal with, and collapsing them would hide one.
    Snoozed documents come last: they are shown so a snooze can be undone,
    not because they need action.
    """
    items: list[dict[str, Any]] = []
    for row in rows:
        for cell in row["cells"]:
            if cell["status"] not in ("overdue", "soon", "snoozed"):
                continue
            items.append({
                **cell,
                "vehicle_id": row["id"],
                "nickname": row["nickname"],
                "registration_number": row["registration_number"],
                "owner_name": row["owner_name"],
                "headline": _headline(cell),
            })
    items.sort(key=lambda i: (i["status"] == "snoozed", i["days"] if i["days"] is not None else 0))
    return items


def _headline(cell: dict) -> str:
    """`Pollution (PUCC) expired 11 days ago` — the queue card's one line."""
    label, days = cell["long_label"], cell["days"]
    if days is None:
        return f"{label} has no date recorded"
    if cell["status"] == "snoozed":
        word = "overdue" if days < 0 else "due"
        return f"{label} {word} {abs(days)} days — reminders paused"
    if days < 0:
        return f"{label} expired {abs(days)} day{'' if abs(days) == 1 else 's'} ago"
    if days == 0:
        return f"{label} expires today"
    return f"{label} due in {days} day{'' if days == 1 else 's'}"


# ── the timeline page ────────────────────────────────────────────────────


def timeline_position(days: int) -> float:
    """Where a document sits on the −60d…+90d rail, as a percentage.

    Anything outside the window is clamped to its edge so a long-overdue
    document still appears rather than being drawn off the rail.
    """
    span = TIMELINE_PAST + TIMELINE_FUTURE
    pct = (days + TIMELINE_PAST) / span * 100
    return max(0.0, min(100.0, pct))


TODAY_POSITION = TIMELINE_PAST / (TIMELINE_PAST + TIMELINE_FUTURE) * 100


def timeline_rows(rows: Iterable[dict]) -> list[dict[str, Any]]:
    """Only vehicles with something inside the window, each with its marks."""
    out = []
    for row in rows:
        marks = []
        for cell in row["cells"]:
            days = cell["days"]
            if days is None:
                continue
            # Overdue documents are never dropped, however stale — those are
            # exactly the ones that must not vanish off the rail. They pin to
            # the left edge instead. Only far-future dates are filtered out.
            if days > TIMELINE_FUTURE:
                continue
            marks.append({
                "label": cell["label"],
                "long_label": cell["long_label"],
                "field": cell["field"],
                "status": cell["status"],
                "days": days,
                "days_text": cell["days_text"],
                "date_text": cell["date_text"],
                "left": round(timeline_position(days), 2),
                "clamped": days < -TIMELINE_PAST,
            })
        if marks:
            marks.sort(key=lambda m: m["left"])
            out.append({**row, "marks": marks})
    return out


# ── the vehicle detail page ──────────────────────────────────────────────


# Everything on the vehicle row that is not one of the five expiry columns.
DETAIL_FIELDS = (
    ("registration_number", "Registration"),
    ("nickname", "Nickname"),
    ("owner_name", "Owner"),
    ("vehicle_class", "Class"),
    ("fuel_type", "Fuel"),
    ("permit_type", "Permit type"),
    ("registration_date", "Registered"),
    ("status", "Status"),
)


def detail_fields(vehicle: dict) -> list[dict[str, str]]:
    """The non-expiry columns, formatted, with empties shown as such."""
    out = []
    for key, label in DETAIL_FIELDS:
        value = vehicle.get(key)
        if isinstance(value, date):
            text = format_long_date(value)
        elif value in (None, ""):
            text = "—"
        else:
            text = str(value)
        out.append({"key": key, "label": label, "value": text, "empty": text == "—"})
    return out


def ladder(cell: dict, fired: set[int]) -> list[dict[str, Any]]:
    """The nine cron offsets for one document, each marked lit or not.

    `fired` is the set of trigger_offsets already logged against this
    document's *current* expiry date.
    """
    steps = []
    for offset in REMINDER_OFFSETS:
        sign = "−" if offset < 0 else "+"
        steps.append({
            "offset": offset,
            "label": f"{sign}{abs(offset)}d",
            "fired": offset in fired,
            "date_text": format_date(offset_date(cell["date"], offset)) if cell["date"] else "",
            "is_next": offset == cell["next_offset"],
        })
    return steps


def index_fired(log_rows: Iterable[dict]) -> dict[tuple[str, date], set[int]]:
    """reminder_log rows → {(field, expiry_date): {offsets fired}}."""
    out: dict[tuple[str, date], set[int]] = {}
    for row in log_rows:
        key = (row["expiry_field"], row["expiry_date"])
        out.setdefault(key, set()).add(int(row["trigger_offset"]))
    return out


def build_detail(
    vehicle: dict,
    today: date,
    snooze_rows: Iterable[dict] = (),
    log_rows: Iterable[dict] = (),
    soon_days: int = SOON_DAYS,
) -> dict[str, Any]:
    """One vehicle: every column, plus each document's reminder ladder."""
    snoozes = index_snoozes(snooze_rows)
    fired = index_fired(log_rows)
    row = build_row(vehicle, today, snoozes, {}, soon_days)

    documents = []
    for cell in row["cells"]:
        already = fired.get((cell["field"], cell["date"]), set()) if cell["date"] else set()
        documents.append({
            **cell,
            "reminders_sent": len(already),
            "ladder": ladder(cell, already),
            "headline": _headline(cell),
        })

    return {
        **row,
        "archived": (vehicle.get("status") or "").lower() == "archived",
        "fields": detail_fields(vehicle),
        "documents": documents,
    }


# ── accounts (admin page) ────────────────────────────────────────────────


def build_users(rows: Iterable[dict]) -> list[dict[str, Any]]:
    """web_users rows, formatted for the admin table."""
    out = []
    for row in rows:
        approved = row["approved_at"] is not None
        out.append({
            "subject": row["subject"],
            "email": row["email"],
            "name": row["name"] or row["email"],
            "role": row["role"],
            "approved": approved,
            "state": "Approved" if approved else "Awaiting approval",
            "approved_text": format_long_date(row["approved_at"].date()) if approved else "",
            "created_text": format_long_date(row["created_at"].date()),
            "last_seen_text": format_long_date(row["last_seen_at"].date()),
        })
    return out
