import atexit
import logging
import os
import threading
from contextlib import contextmanager
from datetime import date
from typing import Optional

import psycopg2
from psycopg2 import pool as pgpool
from psycopg2 import sql as pgsql
from psycopg2.extras import RealDictCursor

logger = logging.getLogger(__name__)

_ALLOWED_UPDATE_FIELDS = frozenset({
    "insurance_valid_until",
    "pucc_valid_until",
    "fitness_valid_until",
    "mv_tax_valid_until",
    "permit_valid_until",
})

# An archived vehicle keeps its row but drops out of the fleet, the cron
# sweep and the bot. `status` is NOT NULL DEFAULT 'ACTIVE' on the live table
# and every existing row holds 'ACTIVE', so archiving writes 'ARCHIVED' to
# match that convention and restoring writes 'ACTIVE' — never NULL, which
# the NOT NULL constraint would reject. Compared case-insensitively so any
# other value the VAHAN import may write still reads as live.
ARCHIVED = "ARCHIVED"
ACTIVE = "ACTIVE"
_NOT_ARCHIVED = "upper(status) IS DISTINCT FROM 'ARCHIVED'"

_VEHICLE_COLS = """
    id, nickname, registration_number, status, vehicle_class,
    fuel_type, emission_norms, model, manufacturer, rto, state,
    owner_name, registration_date,
    insurance_company, insurance_policy_no,
    insurance_valid_until, pucc_valid_until, fitness_valid_until,
    mv_tax_valid_until, permit_type, permit_no, permit_valid_until
"""

_ORDER_BY_NEAREST = """ORDER BY LEAST(
    COALESCE(insurance_valid_until, '9999-01-01'::date),
    COALESCE(pucc_valid_until,      '9999-01-01'::date),
    COALESCE(fitness_valid_until,   '9999-01-01'::date),
    COALESCE(mv_tax_valid_until,    '9999-01-01'::date),
    COALESCE(permit_valid_until,    '9999-01-01'::date)
)"""


# Every helper below opens a connection, and against a managed Postgres in
# another region that is a TCP+TLS handshake per query — a dashboard render
# makes four. A small pool keeps them warm. It is created lazily so importing
# this module never needs DATABASE_URI, and it is thread-safe because
# main.py runs the bots in threads and uvicorn runs sync routes in a
# threadpool.
_POOL = None
_POOL_LOCK = threading.Lock()


def _get_pool():
    global _POOL
    if _POOL is None:
        with _POOL_LOCK:
            if _POOL is None:
                _POOL = pgpool.ThreadedConnectionPool(
                    minconn=int(os.getenv("DB_POOL_MIN", "1")),
                    maxconn=int(os.getenv("DB_POOL_MAX", "8")),
                    dsn=os.environ["DATABASE_URI"],
                    connect_timeout=int(os.getenv("DB_CONNECT_TIMEOUT", "10")),
                    application_name=os.getenv("DB_APP_NAME", "pitstop"),
                )
                logger.info("database pool opened")
    return _POOL


def close_pool() -> None:
    """Release every pooled connection. Registered at exit; safe to call twice."""
    global _POOL
    with _POOL_LOCK:
        if _POOL is not None:
            _POOL.closeall()
            _POOL = None
            logger.info("database pool closed")


atexit.register(close_pool)


@contextmanager
def _conn():
    """A pooled connection, returned to the pool on the way out.

    Yielding inside `with conn` keeps the existing commit-on-success,
    rollback-on-exception behaviour every caller already relies on; psycopg2's
    connection context manager does not close, which is exactly what a pool
    wants.
    """
    pool = _get_pool()
    conn = pool.getconn()
    try:
        with conn:
            yield conn
    finally:
        pool.putconn(conn)


def get_vehicles_filtered(
    filter_type: str,
    value: Optional[str] = None,
    days: int = 30,
) -> list[dict]:
    if filter_type == "all":
        sql = (
            f"SELECT {_VEHICLE_COLS} FROM vehicles "
            f"WHERE {_NOT_ARCHIVED} ORDER BY registration_number"
        )
        params: dict = {}
    elif filter_type == "expiring_soon":
        sql = f"""
            SELECT {_VEHICLE_COLS} FROM vehicles
            WHERE {_NOT_ARCHIVED} AND (
                insurance_valid_until BETWEEN CURRENT_DATE
                    AND CURRENT_DATE + %(days)s * INTERVAL '1 day'
                OR pucc_valid_until BETWEEN CURRENT_DATE
                    AND CURRENT_DATE + %(days)s * INTERVAL '1 day'
                OR fitness_valid_until BETWEEN CURRENT_DATE
                    AND CURRENT_DATE + %(days)s * INTERVAL '1 day'
                OR mv_tax_valid_until BETWEEN CURRENT_DATE
                    AND CURRENT_DATE + %(days)s * INTERVAL '1 day'
                OR (permit_valid_until IS NOT NULL
                    AND permit_valid_until BETWEEN CURRENT_DATE
                        AND CURRENT_DATE + %(days)s * INTERVAL '1 day'))
            {_ORDER_BY_NEAREST}
        """
        params = {"days": days}
    elif filter_type == "expired":
        sql = f"""
            SELECT {_VEHICLE_COLS} FROM vehicles
            WHERE {_NOT_ARCHIVED} AND (
                insurance_valid_until < CURRENT_DATE
                OR pucc_valid_until < CURRENT_DATE
                OR fitness_valid_until < CURRENT_DATE
                OR mv_tax_valid_until < CURRENT_DATE
                OR (permit_valid_until IS NOT NULL AND permit_valid_until < CURRENT_DATE))
            {_ORDER_BY_NEAREST}
        """
        params = {}
    elif filter_type == "by_owner":
        sql = f"""
            SELECT {_VEHICLE_COLS} FROM vehicles
            WHERE {_NOT_ARCHIVED} AND owner_name ILIKE %(value)s
            ORDER BY registration_number
        """
        params = {"value": f"%{value}%"}
    elif filter_type == "by_registration":
        sql = (
            f"SELECT {_VEHICLE_COLS} FROM vehicles "
            f"WHERE {_NOT_ARCHIVED} AND registration_number = %(value)s"
        )
        params = {"value": value}
    elif filter_type == "by_nickname":
        sql = (
            f"SELECT {_VEHICLE_COLS} FROM vehicles "
            f"WHERE {_NOT_ARCHIVED} AND nickname ILIKE %(value)s"
        )
        params = {"value": f"%{value}%"}
    else:
        raise ValueError(f"Unknown filter_type: {filter_type!r}")

    with _conn() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(sql, params)
            return [dict(r) for r in cur.fetchall()]


def update_vehicle_field(registration_number: str, field: str, new_date: str) -> bool:
    if field not in _ALLOWED_UPDATE_FIELDS:
        raise ValueError(f"Field {field!r} is not updatable")
    query = pgsql.SQL(
        "UPDATE vehicles SET {col} = %(new_date)s, updated_at = now() "
        "WHERE registration_number = %(reg)s"
    ).format(col=pgsql.Identifier(field))
    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute(query, {"new_date": new_date, "reg": registration_number})
            return cur.rowcount > 0


def get_all_vehicles_with_expiry(include_archived: bool = False) -> list[dict]:
    """Every vehicle. Archived ones are excluded unless asked for."""
    where = "" if include_archived else f"WHERE {_NOT_ARCHIVED}"
    sql = f"SELECT {_VEHICLE_COLS} FROM vehicles {where} ORDER BY id"
    with _conn() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(sql)
            return [dict(r) for r in cur.fetchall()]


def reminder_already_sent(
    vehicle_id: int, expiry_field: str, expiry_date: date, offset: int
) -> bool:
    sql = """
        SELECT 1 FROM reminder_log
        WHERE vehicle_id = %(vid)s
          AND expiry_field = %(field)s
          AND expiry_date = %(edate)s
          AND trigger_offset = %(offset)s
    """
    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, {
                "vid": vehicle_id, "field": expiry_field,
                "edate": expiry_date, "offset": offset,
            })
            return cur.fetchone() is not None


def log_reminder(
    vehicle_id: int, expiry_field: str, expiry_date: date, offset: int
) -> None:
    sql = """
        INSERT INTO reminder_log (vehicle_id, expiry_field, expiry_date, trigger_offset)
        VALUES (%(vid)s, %(field)s, %(edate)s, %(offset)s)
        ON CONFLICT DO NOTHING
    """
    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, {
                "vid": vehicle_id, "field": expiry_field,
                "edate": expiry_date, "offset": offset,
            })


def is_snoozed(vehicle_id: int, expiry_field: str) -> bool:
    sql = """
        SELECT 1 FROM reminder_snooze
        WHERE vehicle_id = %(vid)s
          AND expiry_field = %(field)s
          AND (snoozed_until IS NULL OR snoozed_until >= CURRENT_DATE)
    """
    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, {"vid": vehicle_id, "field": expiry_field})
            return cur.fetchone() is not None


def snooze_reminder(
    vehicle_id: int,
    expiry_field: str,
    snoozed_until,   # date | None
    reason: str,
    created_by: str,
) -> None:
    sql = """
        INSERT INTO reminder_snooze
            (vehicle_id, expiry_field, snoozed_until, reason, created_by)
        VALUES (%(vid)s, %(field)s, %(until)s, %(reason)s, %(by)s)
        ON CONFLICT (vehicle_id, expiry_field) DO UPDATE
            SET snoozed_until = EXCLUDED.snoozed_until,
                reason        = EXCLUDED.reason,
                created_by    = EXCLUDED.created_by,
                created_at    = now()
    """
    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, {
                "vid": vehicle_id, "field": expiry_field,
                "until": snoozed_until, "reason": reason, "by": created_by,
            })


def unsnooze_reminder(vehicle_id: int, expiry_field: str) -> bool:
    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM reminder_snooze WHERE vehicle_id=%(vid)s AND expiry_field=%(field)s",
                {"vid": vehicle_id, "field": expiry_field},
            )
            return cur.rowcount > 0


def get_chat_context(user_id: str) -> dict:
    with _conn() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                "SELECT summary FROM chat_summary WHERE user_id = %(uid)s",
                {"uid": user_id},
            )
            row = cur.fetchone()
            summary = row["summary"] if row else None
            cur.execute(
                """SELECT role, content FROM chat_messages
                   WHERE user_id = %(uid)s
                   ORDER BY created_at DESC LIMIT 10""",
                {"uid": user_id},
            )
            messages = list(reversed([dict(r) for r in cur.fetchall()]))
    return {"summary": summary, "messages": messages}


def save_turn(user_id: str, user_message: str, assistant_message: str) -> None:
    sql = (
        "INSERT INTO chat_messages (user_id, role, content) "
        "VALUES (%(uid)s, %(role)s, %(content)s)"
    )
    with _conn() as conn:
        with conn.cursor() as cur:
            cur.executemany(sql, [
                {"uid": user_id, "role": "user",      "content": user_message},
                {"uid": user_id, "role": "assistant", "content": assistant_message},
            ])


def get_message_count(user_id: str) -> int:
    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT COUNT(*) FROM chat_messages WHERE user_id = %(uid)s",
                {"uid": user_id},
            )
            return cur.fetchone()[0]


def summarize_if_needed(user_id: str, provider) -> None:
    if get_message_count(user_id) <= 20:
        return
    with _conn() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                """SELECT id, role, content FROM chat_messages
                   WHERE user_id = %(uid)s ORDER BY created_at ASC LIMIT 15""",
                {"uid": user_id},
            )
            oldest = [dict(r) for r in cur.fetchall()]
            if not oldest:
                return
            cur.execute(
                "SELECT summary FROM chat_summary WHERE user_id = %(uid)s",
                {"uid": user_id},
            )
            row = cur.fetchone()
            existing = row["summary"] if row else "None"

    text = "\n".join(f"{m['role']}: {m['content']}" for m in oldest)
    new_summary = provider.generate_text(
        f"Summarise this vehicle-bot conversation into ≤150 words. "
        f"Focus on vehicles discussed, dates updated, user preferences. "
        f"Merge with existing summary.\n\nExisting: {existing}\n\nMessages:\n{text}"
    )

    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO chat_summary (user_id, summary, updated_at)
                   VALUES (%(uid)s, %(s)s, now())
                   ON CONFLICT (user_id) DO UPDATE
                   SET summary = EXCLUDED.summary, updated_at = now()""",
                {"uid": user_id, "s": new_summary},
            )
            cur.execute(
                "DELETE FROM chat_messages WHERE id = ANY(%(ids)s)",
                {"ids": [m["id"] for m in oldest]},
            )


def get_active_snoozes() -> list[dict]:
    """Every snooze still in force, for the whole fleet.

    Mirrors ``is_snoozed`` but in one round trip so the web dashboard does
    not issue a query per vehicle-field pair.
    """
    sql = """
        SELECT vehicle_id, expiry_field, snoozed_until, reason, created_by, created_at
        FROM reminder_snooze
        WHERE snoozed_until IS NULL OR snoozed_until >= CURRENT_DATE
    """
    with _conn() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(sql)
            return [dict(r) for r in cur.fetchall()]


def get_reminder_counts() -> list[dict]:
    """How many of the escalating reminders have fired, per document.

    Grouped by expiry_date as well as vehicle_id/expiry_field: renewing a
    document starts a fresh cycle against the new date, and only the rows
    matching the *current* date describe where that cycle stands.
    """
    sql = """
        SELECT vehicle_id, expiry_field, expiry_date,
               COUNT(*) AS sent, MAX(sent_at) AS last_sent
        FROM reminder_log
        GROUP BY vehicle_id, expiry_field, expiry_date
    """
    with _conn() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(sql)
            return [dict(r) for r in cur.fetchall()]


def get_last_sweep() -> Optional[dict]:
    """The most recent cron sweep that actually sent something."""
    sql = """
        SELECT MAX(sent_at) AS last_sent, COUNT(*) AS sent
        FROM reminder_log
        GROUP BY sent_at::date
        ORDER BY 1 DESC
        LIMIT 1
    """
    with _conn() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(sql)
            row = cur.fetchone()
            return dict(row) if row else None


def get_vehicle(registration_number: str, include_archived: bool = True) -> Optional[dict]:
    """One vehicle, every column the detail page shows."""
    where = "registration_number = %(reg)s"
    if not include_archived:
        where += f" AND {_NOT_ARCHIVED}"
    sql = f"SELECT {_VEHICLE_COLS} FROM vehicles WHERE {where}"
    with _conn() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(sql, {"reg": registration_number})
            row = cur.fetchone()
            return dict(row) if row else None


def get_archived_vehicles() -> list[dict]:
    sql = (
        f"SELECT {_VEHICLE_COLS} FROM vehicles "
        f"WHERE upper(status) = 'ARCHIVED' ORDER BY registration_number"
    )
    with _conn() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(sql)
            return [dict(r) for r in cur.fetchall()]


def set_vehicle_archived(registration_number: str, archived: bool) -> bool:
    """Archive or restore. Restoring writes 'ACTIVE' — the column is NOT NULL,
    and that is the value every row in the live table already carries."""
    sql = """
        UPDATE vehicles SET status = %(status)s, updated_at = now()
        WHERE registration_number = %(reg)s
    """
    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                sql,
                {"status": ARCHIVED if archived else ACTIVE, "reg": registration_number},
            )
            return cur.rowcount > 0


def delete_vehicle(registration_number: str) -> bool:
    """Permanently remove a vehicle. reminder_log and reminder_snooze cascade
    (see db/migrations/004_vehicle_archive_and_delete.sql) — there is no undo."""
    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM vehicles WHERE registration_number = %(reg)s",
                {"reg": registration_number},
            )
            return cur.rowcount > 0


def get_reminder_offsets(vehicle_id: int) -> list[dict]:
    """Which individual reminders have fired for one vehicle.

    The counts from ``get_reminder_counts`` drive the fleet chips; the detail
    page's ladder needs to know *which* offsets are lit, not how many.
    """
    sql = """
        SELECT expiry_field, expiry_date, trigger_offset, sent_at
        FROM reminder_log
        WHERE vehicle_id = %(vid)s
        ORDER BY expiry_date, trigger_offset
    """
    with _conn() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(sql, {"vid": vehicle_id})
            return [dict(r) for r in cur.fetchall()]


# ── web sign-in accounts (see db/migrations/005_web_users.sql) ────────────


_USER_COLS = """
    id, subject, email, name, role, approved_at, approved_by,
    created_at, last_seen_at
"""


def upsert_web_user(
    subject: str, email: str, name: str, bootstrap_admin: bool = False
) -> dict:
    """Record a sign-in and return the account as it now stands.

    A first-time account lands unapproved — `approved_at` stays NULL — unless
    `bootstrap_admin` is set, which is how the ADMIN_EMAIL owner gets in
    before there is anybody to approve them. An existing row keeps its role
    and approval: re-signing in never re-grants anything, and the bootstrap
    flag cannot quietly promote an account that was demoted on purpose.
    """
    sql = f"""
        INSERT INTO web_users (subject, email, name, role, approved_at, approved_by)
        VALUES (
            %(sub)s, %(email)s, %(name)s,
            CASE WHEN %(boot)s THEN 'admin' ELSE 'member' END,
            CASE WHEN %(boot)s THEN now() ELSE NULL END,
            CASE WHEN %(boot)s THEN 'bootstrap' ELSE NULL END
        )
        ON CONFLICT (subject) DO UPDATE
            SET email        = EXCLUDED.email,
                name         = EXCLUDED.name,
                last_seen_at = now()
        RETURNING {_USER_COLS}
    """
    with _conn() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(sql, {
                "sub": subject, "email": email, "name": name,
                "boot": bootstrap_admin,
            })
            return dict(cur.fetchone())


def get_web_user(subject: str) -> Optional[dict]:
    """Re-read an account. Called on every request that touches fleet data,
    so revoking approval takes effect on the visitor's next page load rather
    than whenever their session happens to expire."""
    with _conn() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                f"SELECT {_USER_COLS} FROM web_users WHERE subject = %(sub)s",
                {"sub": subject},
            )
            row = cur.fetchone()
            return dict(row) if row else None


def list_web_users() -> list[dict]:
    """Everyone, pending first — that is the list an admin has to act on."""
    sql = f"""
        SELECT {_USER_COLS} FROM web_users
        ORDER BY (approved_at IS NOT NULL), created_at DESC
    """
    with _conn() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(sql)
            return [dict(r) for r in cur.fetchall()]


def count_pending_web_users() -> int:
    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM web_users WHERE approved_at IS NULL")
            return int(cur.fetchone()[0])


def set_web_user_approved(subject: str, approved: bool, by: str) -> bool:
    sql = """
        UPDATE web_users
        SET approved_at = CASE WHEN %(ok)s THEN now() ELSE NULL END,
            approved_by = CASE WHEN %(ok)s THEN %(by)s ELSE NULL END
        WHERE subject = %(sub)s
    """
    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, {"ok": approved, "by": by, "sub": subject})
            return cur.rowcount > 0


def set_web_user_role(subject: str, role: str) -> bool:
    if role not in ("admin", "member"):
        raise ValueError(f"Unknown role {role!r}")
    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE web_users SET role = %(role)s WHERE subject = %(sub)s",
                {"role": role, "sub": subject},
            )
            return cur.rowcount > 0


def count_web_admins(exclude_subject: Optional[str] = None) -> int:
    """How many approved admins there are — used to refuse the change that
    would leave the app with nobody able to approve anyone."""
    sql = """
        SELECT COUNT(*) FROM web_users
        WHERE role = 'admin' AND approved_at IS NOT NULL
          AND (%(skip)s IS NULL OR subject <> %(skip)s)
    """
    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, {"skip": exclude_subject})
            return int(cur.fetchone()[0])


def delete_web_user(subject: str) -> bool:
    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM web_users WHERE subject = %(sub)s", {"sub": subject})
            return cur.rowcount > 0


# ── creating and editing vehicles ────────────────────────────────────────


# Columns the web form is allowed to write. `id`, `status`, `created_at` and
# `updated_at` are deliberately absent: identity and lifecycle are not form
# fields, and archiving goes through set_vehicle_archived.
_WRITABLE_COLS = (
    "registration_number", "nickname", "owner_name", "registration_date",
    "manufacturer", "model", "vehicle_class", "fuel_type", "emission_norms",
    "rto", "state",
    "insurance_company", "insurance_policy_no",
    "permit_type", "permit_no",
    "insurance_valid_until", "pucc_valid_until", "fitness_valid_until",
    "mv_tax_valid_until", "permit_valid_until",
)


def registration_exists(registration_number: str, excluding_id: Optional[int] = None) -> bool:
    """Uniqueness check for the add/edit form.

    Racy on its own — two simultaneous inserts could both pass — so the
    insert also catches the unique-violation. This is here to produce a
    readable field error in the normal case.
    """
    sql = "SELECT 1 FROM vehicles WHERE registration_number = %(reg)s"
    params: dict = {"reg": registration_number}
    if excluding_id is not None:
        sql += " AND id <> %(skip)s"
        params["skip"] = excluding_id
    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            return cur.fetchone() is not None


def create_vehicle(values: dict) -> dict:
    """Insert a vehicle and return the stored row.

    Raises psycopg2.errors.UniqueViolation if the registration is taken —
    the route turns that into a field error.
    """
    cols = [c for c in _WRITABLE_COLS if c in values]
    sql = pgsql.SQL("INSERT INTO vehicles ({cols}) VALUES ({vals}) RETURNING {out}").format(
        cols=pgsql.SQL(", ").join(pgsql.Identifier(c) for c in cols),
        vals=pgsql.SQL(", ").join(pgsql.Placeholder(c) for c in cols),
        out=pgsql.SQL(_VEHICLE_COLS),
    )
    with _conn() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(sql, {c: values[c] for c in cols})
            return dict(cur.fetchone())


def update_vehicle(vehicle_id: int, values: dict) -> Optional[dict]:
    """Overwrite a vehicle's writable columns. Returns the stored row."""
    cols = [c for c in _WRITABLE_COLS if c in values]
    if not cols:
        return None
    sql = pgsql.SQL(
        "UPDATE vehicles SET {sets}, updated_at = now() "
        "WHERE id = %(vehicle_id)s RETURNING {out}"
    ).format(
        sets=pgsql.SQL(", ").join(
            pgsql.SQL("{} = {}").format(pgsql.Identifier(c), pgsql.Placeholder(c))
            for c in cols
        ),
        out=pgsql.SQL(_VEHICLE_COLS),
    )
    params = {c: values[c] for c in cols}
    params["vehicle_id"] = vehicle_id
    with _conn() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(sql, params)
            row = cur.fetchone()
            return dict(row) if row else None
