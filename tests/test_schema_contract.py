"""What the code assumes about the live Postgres schema.

These are pure assertions over module constants — no database — so they run
everywhere and fail loudly if someone edits the column lists without checking
them against the real table. The schema they encode was read from the live
Supabase project on 2026-08-30.
"""


def test_archive_marker_matches_the_live_status_convention():
    """The live vehicles.status is NOT NULL DEFAULT 'ACTIVE'.

    Restoring a vehicle used to write NULL, which the constraint rejects.
    """
    from db import client
    assert client.ARCHIVED == "ARCHIVED"
    assert client.ACTIVE == "ACTIVE"
    # The predicate must be case-insensitive so 'ACTIVE' reads as live.
    assert "upper(status)" in client._NOT_ARCHIVED


def test_vehicle_columns_cover_the_live_table():
    """Every column on the Supabase vehicles table is selected."""
    from db.client import _VEHICLE_COLS
    selected = {c.strip() for c in _VEHICLE_COLS.replace("\n", " ").split(",")}
    live = {
        "id", "nickname", "registration_number", "status", "vehicle_class",
        "fuel_type", "emission_norms", "model", "manufacturer", "rto", "state",
        "owner_name", "registration_date", "insurance_company",
        "insurance_policy_no", "insurance_valid_until", "pucc_valid_until",
        "fitness_valid_until", "mv_tax_valid_until", "permit_type",
        "permit_no", "permit_valid_until",
    }
    assert selected == live


# ── DATABASE_URI shape ───────────────────────────────────────────────────


def test_direct_supabase_host_is_flagged_as_unroutable():
    """db.<ref>.supabase.co has no A record — IPv4 is a paid add-on. GitHub
    runners and Vercel functions are IPv4-only, so a direct URI there fails to
    resolve rather than failing on credentials, which is a confusing way to
    lose an afternoon."""
    from db import client

    warning = client.dsn_warning(
        "postgresql://postgres:pw@db.abcdefghijklmnop.supabase.co:5432/postgres")
    assert warning is not None
    assert "pooler" in warning.lower()


def test_pooler_host_is_not_flagged():
    from db import client

    for dsn in (
        "postgresql://postgres.abc:pw@aws-0-ap-southeast-1.pooler.supabase.com:5432/postgres",
        "postgresql://postgres.abc:pw@aws-0-ap-southeast-1.pooler.supabase.com:6543/postgres",
        "postgresql://claude_rw:pw@localhost:5432/homelab",
    ):
        assert client.dsn_warning(dsn) is None


def test_dsn_warning_tolerates_nothing():
    from db import client

    assert client.dsn_warning("") is None
    assert client.dsn_warning(None) is None


def test_reminder_log_insert_names_its_conflict_target():
    """A bare `ON CONFLICT DO NOTHING` swallows *any* unique violation,
    including one on the primary key.

    That is how a broken identity sequence stayed invisible: rows restored
    from a backup with explicit ids left the sequence behind, so every insert
    drew an id that already existed, collided, and was silently discarded —
    while the sweep reported success and the reminders went out unlogged,
    ready to fire again the next day.
    """
    import inspect
    import re

    from db import client

    source = inspect.getsource(client.log_reminder)
    # Only the SQL, not the comments — which necessarily quote the bare form
    # in order to warn about it.
    sql = re.search(r'"""(.*?)"""', source, re.S).group(1)
    assert "ON CONFLICT (vehicle_id, expiry_field, expiry_date, trigger_offset)" in sql
    assert not re.search(r"ON\s+CONFLICT\s+DO\s+NOTHING", sql)
