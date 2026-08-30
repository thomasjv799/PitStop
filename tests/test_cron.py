import pytest
from datetime import date, timedelta
from unittest.mock import patch


def test_build_message_days_before():
    from cron.reminder_sweep import _build_message
    v = {"nickname": "Honda Highness", "registration_number": "KL04AS1371", "owner_name": "Thomas J Varghese"}
    expiry = date.today() + timedelta(days=3)
    msg = _build_message(v, "Insurance", expiry, 3)
    assert "Honda Highness" in msg
    assert "due in 3 days" in msg
    assert "<b>" not in msg  # no raw HTML — Discord renders it literally


def test_build_message_expired():
    from cron.reminder_sweep import _build_message
    v = {"nickname": "Toyota Etios", "registration_number": "KL04AB6528", "owner_name": "Varghese Joseph"}
    msg = _build_message(v, "Fitness / RC validity", date.today() - timedelta(days=10), -10)
    assert "expired 10 days ago" in msg
    assert "Fitness" in msg  # short doc name, not "Fitness / RC validity"


def test_build_message_today():
    from cron.reminder_sweep import _build_message
    v = {"nickname": "Vespa", "registration_number": "KL04AF2342", "owner_name": "Varghese Joseph"}
    msg = _build_message(v, "Insurance", date.today(), 0)
    assert "due today" in msg


def test_all_offsets_coverage():
    from cron.reminder_sweep import ALL_OFFSETS
    for expected in [-7, -3, -1, 0, 1, 3, 7, 15, 30]:
        assert expected in ALL_OFFSETS


@patch("cron.reminder_sweep.notify")
@patch("cron.reminder_sweep.db.log_reminder")
@patch("cron.reminder_sweep.db.reminder_already_sent", return_value=False)
@patch("cron.reminder_sweep.db.get_all_vehicles_with_expiry")
@patch("cron.reminder_sweep.db.is_snoozed", return_value=False)
def test_sweep_fires_at_trigger_day(mock_snoozed, mock_get, mock_sent, mock_log, mock_notify, monkeypatch):
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "12345")
    today = date.today()
    expiry = today + timedelta(days=7)
    mock_get.return_value = [{
        "id": 1, "nickname": "Honda Highness", "registration_number": "KL04AS1371",
        "owner_name": "Thomas J Varghese",
        "insurance_valid_until": expiry, "pucc_valid_until": None,
        "fitness_valid_until": None, "mv_tax_valid_until": None, "permit_valid_until": None,
    }]
    from cron.reminder_sweep import sweep
    count = sweep()
    assert count == 1
    mock_notify.assert_called_once()
    mock_log.assert_called_once_with(1, "insurance_valid_until", expiry, -7)


@patch("cron.reminder_sweep.notify")
@patch("cron.reminder_sweep.db.reminder_already_sent", return_value=True)
@patch("cron.reminder_sweep.db.get_all_vehicles_with_expiry")
@patch("cron.reminder_sweep.db.is_snoozed", return_value=False)
def test_sweep_skips_already_sent(mock_snoozed, mock_get, mock_sent, mock_notify, monkeypatch):
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "12345")
    today = date.today()
    mock_get.return_value = [{
        "id": 1, "nickname": "Vespa", "registration_number": "KL04AF2342",
        "owner_name": "X", "insurance_valid_until": today + timedelta(days=7),
        "pucc_valid_until": None, "fitness_valid_until": None,
        "mv_tax_valid_until": None, "permit_valid_until": None,
    }]
    from cron.reminder_sweep import sweep
    assert sweep() == 0
    mock_notify.assert_not_called()


@patch("cron.reminder_sweep.notify")
@patch("cron.reminder_sweep.db.log_reminder")
@patch("cron.reminder_sweep.db.reminder_already_sent", return_value=False)
@patch("cron.reminder_sweep.db.get_all_vehicles_with_expiry")
@patch("cron.reminder_sweep.db.is_snoozed", return_value=False)
def test_sweep_catches_up_missed_trigger(mock_snoozed, mock_get, mock_sent, mock_log, mock_notify, monkeypatch):
    """Trigger was yesterday (day 6 remaining), still within 2-day catch-up window."""
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "12345")
    today = date.today()
    expiry = today + timedelta(days=6)
    mock_get.return_value = [{
        "id": 1, "nickname": "Vespa", "registration_number": "KL04AF2342",
        "owner_name": "X", "insurance_valid_until": expiry,
        "pucc_valid_until": None, "fitness_valid_until": None,
        "mv_tax_valid_until": None, "permit_valid_until": None,
    }]
    from cron.reminder_sweep import sweep
    assert sweep() == 1


# ── the email digest ─────────────────────────────────────────────────────


def _vehicle(**over):
    row = {
        "id": 1, "nickname": "Swift", "registration_number": "KL04AS1371",
        "owner_name": "Thomas V",
        "insurance_valid_until": None, "pucc_valid_until": None,
        "fitness_valid_until": None, "mv_tax_valid_until": None,
        "permit_valid_until": None,
    }
    row.update(over)
    return row


@patch("cron.reminder_sweep.send_digest")
@patch("cron.reminder_sweep.notify")
@patch("cron.reminder_sweep.db.log_reminder")
@patch("cron.reminder_sweep.db.reminder_already_sent", return_value=False)
@patch("cron.reminder_sweep.db.get_all_vehicles_with_expiry")
@patch("cron.reminder_sweep.db.is_snoozed", return_value=False)
def test_sweep_sends_one_digest_for_several_reminders(
    mock_snoozed, mock_get, mock_sent, mock_log, mock_notify, mock_digest, monkeypatch
):
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "12345")
    today = date.today()
    # One offset each: CATCH_UP_DAYS=2 means a date sitting near several
    # trigger days legitimately fires more than once.
    mock_get.return_value = [_vehicle(
        insurance_valid_until=today + timedelta(days=7),   # fires at -7
        pucc_valid_until=today + timedelta(days=3),        # fires at -3
    )]

    from cron.reminder_sweep import sweep
    assert sweep() == 2

    # Two chat messages, but a single digest carrying both.
    assert mock_notify.call_count == 2
    mock_digest.assert_called_once()
    items = mock_digest.call_args.args[0]
    assert len(items) == 2
    assert {i["label"] for i in items} == {"Insurance", "Pollution (PUCC)"}
    assert {i["days"] for i in items} == {7, 3}
    assert all(i["registration_number"] == "KL04AS1371" for i in items)


@patch("cron.reminder_sweep.send_digest")
@patch("cron.reminder_sweep.notify")
@patch("cron.reminder_sweep.db.log_reminder")
@patch("cron.reminder_sweep.db.reminder_already_sent", return_value=True)
@patch("cron.reminder_sweep.db.get_all_vehicles_with_expiry")
@patch("cron.reminder_sweep.db.is_snoozed", return_value=False)
def test_sweep_digest_is_empty_when_nothing_fires(
    mock_snoozed, mock_get, mock_sent, mock_log, mock_notify, mock_digest, monkeypatch
):
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "12345")
    mock_get.return_value = [_vehicle(
        insurance_valid_until=date.today() + timedelta(days=7))]

    from cron.reminder_sweep import sweep
    assert sweep() == 0
    # Called, but with nothing — send_digest itself no-ops on an empty list.
    mock_digest.assert_called_once()
    assert mock_digest.call_args.args[0] == []


@patch("cron.reminder_sweep.send_digest", side_effect=RuntimeError("resend down"))
@patch("cron.reminder_sweep.notify")
@patch("cron.reminder_sweep.db.log_reminder")
@patch("cron.reminder_sweep.db.reminder_already_sent", return_value=False)
@patch("cron.reminder_sweep.db.get_all_vehicles_with_expiry")
@patch("cron.reminder_sweep.db.is_snoozed", return_value=False)
def test_a_broken_digest_does_not_lose_the_reminders(
    mock_snoozed, mock_get, mock_sent, mock_log, mock_notify, mock_digest, monkeypatch
):
    """send_digest swallows its own errors, but if one ever escaped the
    reminders would already be sent and logged — the sweep must not pretend
    otherwise by dying before it returns."""
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "12345")
    mock_get.return_value = [_vehicle(
        insurance_valid_until=date.today() + timedelta(days=7))]

    from cron.reminder_sweep import sweep
    with pytest.raises(RuntimeError):
        sweep()
    # The chat reminder went out and was logged before the digest was tried.
    mock_notify.assert_called_once()
    mock_log.assert_called_once()
