"""Best-effort run history using the shared OneRing ops.job_runs contract."""
import logging
from contextlib import contextmanager
from datetime import datetime, timezone
from uuid import uuid4

from psycopg2.extras import Json
from db import client as db

logger = logging.getLogger(__name__)


@contextmanager
def track_run(job_name: str):
    started = datetime.now(timezone.utc)
    counts = {"vehicles_checked": 0, "reminders_sent": 0, "emails_sent": 0}
    status, error = "ok", None
    try:
        yield counts
    except BaseException as exc:
        status = "error"
        # Exception messages may contain DSNs, registration marks or addresses.
        error = type(exc).__name__
        raise
    finally:
        try:
            with db._conn() as conn, conn.cursor() as cur:
                cur.execute(
                    """INSERT INTO ops.job_runs
                       (id, job_name, started_at, finished_at, status, error_text, counts)
                       VALUES (%s, %s, %s, %s, %s, %s, %s)""",
                    (str(uuid4()), job_name, started, datetime.now(timezone.utc),
                     status, error, Json(counts)),
                )
        except Exception as exc:
            logger.warning("Could not record job run (%s)", type(exc).__name__)
