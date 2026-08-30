import os
import pytest
import psycopg2
from datetime import date, timedelta

pytestmark = pytest.mark.integration


def test_get_vehicles_all():
    from db.client import get_vehicles_filtered
    vehicles = get_vehicles_filtered("all")
    assert len(vehicles) == 7
    assert all("registration_number" in v for v in vehicles)


def test_get_vehicles_by_owner():
    from db.client import get_vehicles_filtered
    vehicles = get_vehicles_filtered("by_owner", value="Thomas")
    assert len(vehicles) == 2
    assert all("Thomas" in v["owner_name"] for v in vehicles)


def test_get_vehicles_by_registration():
    from db.client import get_vehicles_filtered
    vehicles = get_vehicles_filtered("by_registration", value="KL04AS1371")
    assert len(vehicles) == 1
    assert vehicles[0]["nickname"] == "Honda Highness"


def test_get_vehicles_by_nickname():
    from db.client import get_vehicles_filtered
    vehicles = get_vehicles_filtered("by_nickname", value="activa")
    assert len(vehicles) == 1
    assert vehicles[0]["registration_number"] == "KL04AQ2807"


def test_get_vehicles_expiring_soon():
    from db.client import get_vehicles_filtered
    vehicles = get_vehicles_filtered("expiring_soon", days=365)
    assert len(vehicles) > 0


def test_get_vehicles_expired():
    from db.client import get_vehicles_filtered
    vehicles = get_vehicles_filtered("expired")
    regs = [v["registration_number"] for v in vehicles]
    assert "KL04AB6528" in regs  # Toyota Etios has expired fitness


def test_get_vehicles_unknown_filter():
    from db.client import get_vehicles_filtered
    with pytest.raises(ValueError, match="Unknown filter_type"):
        get_vehicles_filtered("bad_filter")


def test_update_vehicle_field_rejects_bad_field():
    from db.client import update_vehicle_field
    with pytest.raises(ValueError, match="not updatable"):
        update_vehicle_field("KL04AS1371", "owner_name", "2027-01-01")


def test_reminder_log_round_trip():
    from db.client import reminder_already_sent, log_reminder
    vid, field, expiry, offset = 1, "insurance_valid_until", date(2099, 1, 1), -999
    assert not reminder_already_sent(vid, field, expiry, offset)
    log_reminder(vid, field, expiry, offset)
    assert reminder_already_sent(vid, field, expiry, offset)
    conn = psycopg2.connect(os.environ["DATABASE_URI"])
    with conn:
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM reminder_log WHERE vehicle_id=%s AND expiry_date=%s AND trigger_offset=%s",
                (vid, expiry, offset),
            )
    conn.close()


def test_reminder_log_idempotent():
    from db.client import log_reminder, reminder_already_sent
    vid, field, expiry, offset = 1, "pucc_valid_until", date(2098, 6, 1), -888
    log_reminder(vid, field, expiry, offset)
    log_reminder(vid, field, expiry, offset)  # must not raise
    assert reminder_already_sent(vid, field, expiry, offset)
    conn = psycopg2.connect(os.environ["DATABASE_URI"])
    with conn:
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM reminder_log WHERE vehicle_id=%s AND expiry_date=%s AND trigger_offset=%s",
                (vid, expiry, offset),
            )
    conn.close()


def _clean_chat(user_id: str) -> None:
    conn = psycopg2.connect(os.environ["DATABASE_URI"])
    with conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM chat_messages WHERE user_id=%s", (user_id,))
            cur.execute("DELETE FROM chat_summary WHERE user_id=%s", (user_id,))
    conn.close()


def test_chat_context_empty_for_new_user():
    from db.client import get_chat_context
    uid = "test:unit_empty"
    _clean_chat(uid)
    ctx = get_chat_context(uid)
    assert ctx["summary"] is None
    assert ctx["messages"] == []


def test_chat_save_and_retrieve():
    from db.client import get_chat_context, save_turn, get_message_count
    uid = "test:unit_save"
    _clean_chat(uid)
    save_turn(uid, "What expires soon?", "Your PUCC is due in 7 days.")
    assert get_message_count(uid) == 2
    ctx = get_chat_context(uid)
    assert len(ctx["messages"]) == 2
    assert ctx["messages"][0]["role"] == "user"
    assert ctx["messages"][1]["role"] == "assistant"
    _clean_chat(uid)

