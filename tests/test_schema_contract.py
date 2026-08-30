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
