"""Validate DATABASE_URI before trusting it.

    python -m scripts.check_db                    # uses .env / the environment
    DATABASE_URI='postgresql://...' python -m scripts.check_db

Reports what the URI points at, whether that host is routable from an
IPv4-only environment (GitHub Actions, Vercel), and whether a real query
succeeds. The password is never printed.
"""

import logging
import os
import socket
import sys
from urllib.parse import urlparse

from dotenv import load_dotenv

from db import client as db

OK, BAD, WARN = "  ok  ", " FAIL ", " warn "


def main() -> int:
    # db.client warns about a direct host too; this script says it better, so
    # keep the library's copy out of the output.
    logging.basicConfig(level=logging.ERROR)
    load_dotenv()
    dsn = os.getenv("DATABASE_URI", "")
    if not dsn:
        print(f"{BAD} DATABASE_URI is not set")
        return 1

    parsed = urlparse(dsn)
    host, port = parsed.hostname or "?", parsed.port or 5432
    print(f"       host {host}")
    print(f"       port {port}  ({'session pooler' if port == 5432 else 'transaction pooler' if port == 6543 else 'non-standard'})")
    print(f"       user {parsed.username or '?'}")
    print(f"       db   {parsed.path.lstrip('/') or '?'}")
    print()

    # ── routability ──────────────────────────────────────────────────────
    v4 = v6 = []
    try:
        v4 = sorted({r[4][0] for r in socket.getaddrinfo(host, port, socket.AF_INET)})
    except socket.gaierror:
        pass
    try:
        v6 = sorted({r[4][0] for r in socket.getaddrinfo(host, port, socket.AF_INET6)})
    except socket.gaierror:
        pass

    print(f"       IPv4 {', '.join(v4) if v4 else 'none'}")
    print(f"       IPv6 {', '.join(v6) if v6 else 'none'}")
    if v4:
        print(f"{OK} routable from an IPv4-only host (GitHub Actions, Vercel)")
    else:
        print(f"{BAD} NO IPv4 address — unreachable from GitHub Actions and Vercel")

    hint = db.dsn_warning(dsn)
    if hint:
        print(f"{WARN} {hint}")
    print()

    # ── does it actually work ────────────────────────────────────────────
    try:
        with db._conn() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT current_database(), current_user, version()")
                name, user, version = cur.fetchone()
                cur.execute("SELECT count(*) FROM vehicles")
                vehicles = cur.fetchone()[0]
                cur.execute("SELECT to_regclass('public.web_users') IS NOT NULL")
                has_web_users = cur.fetchone()[0]
    except Exception as exc:
        print(f"{BAD} could not query: {type(exc).__name__}: {exc}")
        return 1
    finally:
        db.close_pool()

    print(f"{OK} connected to {name} as {user}")
    print(f"       {version.split(',')[0]}")
    print(f"{OK} vehicles table readable — {vehicles} row(s)")
    print(f"{OK if has_web_users else BAD} web_users exists "
          f"{'' if has_web_users else '(run db/migrations/005)'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
