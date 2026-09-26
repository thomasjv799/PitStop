from datetime import date, timedelta
from unittest.mock import patch

import pytest
from utils.job_runs import track_run
from cron.reminder_sweep import sweep


@pytest.fixture
def cursor():
    with patch("utils.job_runs.db._conn") as connection:
        yield connection.return_value.__enter__.return_value.cursor.return_value.__enter__.return_value


def record(cursor):
    args = cursor.execute.call_args.args[1]
    return args[4], args[5], args[6].adapted


def test_empty_success_is_recorded(cursor):
    with patch("cron.reminder_sweep.db.get_all_vehicles_with_expiry", return_value=[]), patch("cron.reminder_sweep.send_digest", return_value=False):
        assert sweep() == 0
    assert record(cursor) == ("ok", None, {"vehicles_checked": 0, "reminders_sent": 0, "emails_sent": 0})


def test_failure_record_keeps_partial_counts_and_reraises(cursor):
    with pytest.raises(RuntimeError):
        with track_run("test") as counts:
            counts["reminders_sent"] = 2
            raise RuntimeError("secret-db-password")
    status, error, counts = record(cursor)
    assert (status, error, counts["reminders_sent"]) == ("error", "RuntimeError", 2)


def test_record_failure_does_not_break_run_or_mask_error(caplog):
    with patch("utils.job_runs.db._conn", side_effect=RuntimeError("private DSN")):
        with track_run("test"):
            pass
        with pytest.raises(ValueError, match="original"):
            with track_run("test"):
                raise ValueError("original")
    assert "private DSN" not in caplog.text


def test_sweep_records_notification_and_digest_counts(cursor):
    vehicle = {"id": 1, "nickname": "Sample", "registration_number": "DEMO1",
               "insurance_valid_until": date.today() + timedelta(days=7)}
    with patch("cron.reminder_sweep.db.get_all_vehicles_with_expiry", return_value=[vehicle]), \
         patch("cron.reminder_sweep.db.is_snoozed", return_value=False), \
         patch("cron.reminder_sweep.db.reminder_already_sent", return_value=False), \
         patch("cron.reminder_sweep.db.log_reminder"), \
         patch("cron.reminder_sweep.notify"), \
         patch("cron.reminder_sweep.send_digest", return_value=True):
        assert sweep() == 1
    assert record(cursor)[2] == {"vehicles_checked": 1, "reminders_sent": 1, "emails_sent": 1}


def test_database_read_failure_is_recorded(cursor):
    with patch("cron.reminder_sweep.db.get_all_vehicles_with_expiry", side_effect=RuntimeError("offline")):
        with pytest.raises(RuntimeError):
            sweep()
    assert record(cursor)[:2] == ("error", "RuntimeError")
