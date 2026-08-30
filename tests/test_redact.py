"""Log redaction.

The cron sweep runs in GitHub Actions on a public repository, so its job log
is world readable. These pin the shape of what reaches a log line.
"""

import logging
from datetime import date, timedelta
from unittest.mock import patch

import pytest

from utils import redact


# ── registrations ────────────────────────────────────────────────────────


@pytest.mark.parametrize("value, expected", [
    ("KL04AS1371", "KL04••••71"),
    ("KL07CH8842", "KL07••••42"),
    ("KL02B8086", "KL02•••86"),
])
def test_registration_keeps_the_rto_prefix_and_last_two(value, expected):
    # Enough to tell two vehicles apart in a log, without the full mark.
    assert redact.reg(value) == expected


def test_registration_never_returns_the_whole_mark():
    for value in ("KL04AS1371", "KL07CH8842", "MH12AB1234"):
        assert redact.reg(value) != value
        assert redact.BULLET in redact.reg(value)


def test_registration_handles_short_and_empty_values():
    assert redact.reg("ABC12") == "A••••"
    assert redact.reg("") == "?"
    assert redact.reg(None) == "?"


def test_vehicle_prefers_the_id_which_identifies_nobody():
    assert redact.vehicle({"id": 7, "registration_number": "KL04AS1371"}) == "#7"
    assert redact.vehicle(7) == "#7"
    # Falls back to a masked mark when there is no id.
    assert redact.vehicle({"registration_number": "KL04AS1371"}) == "KL04••••71"


# ── emails ───────────────────────────────────────────────────────────────


def test_email_keeps_the_domain_and_drops_the_local_part():
    # "did it go to the right provider" is a real debugging question;
    # "which person" is not the log's business.
    assert redact.email("thomasjvarghese49@gmail.com") == "t•••@gmail.com"
    assert redact.email("priya@example.co.in") == "p•••@example.co.in"


def test_email_handles_rubbish():
    assert redact.email("") == "?"
    assert redact.email(None) == "?"
    assert redact.email("notanemail") == "?"


# ── message bodies ───────────────────────────────────────────────────────


def test_text_reports_only_a_length():
    assert redact.text("renew my insurance please") == "<25 chars>"
    assert redact.text("") == "<0 chars>"
    assert redact.text(None) == "<0 chars>"


# ── the sweep's log line, which is world readable ────────────────────────


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
def test_sweep_never_logs_a_registration_or_an_owner(
    mock_snoozed, mock_get, mock_sent, mock_log, mock_notify, mock_digest,
    caplog, monkeypatch,
):
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "12345")
    mock_get.return_value = [_vehicle(
        insurance_valid_until=date.today() + timedelta(days=7))]

    from cron.reminder_sweep import sweep
    with caplog.at_level(logging.INFO):
        assert sweep() == 1

    assert "KL04AS1371" not in caplog.text
    assert "Thomas V" not in caplog.text
    # Still debuggable: the vehicle id and which document fired.
    assert "vehicle #1" in caplog.text
    assert "insurance_valid_until" in caplog.text
