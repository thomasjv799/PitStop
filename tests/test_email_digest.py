"""The reminder digest: wording, ordering, escaping and the send guard."""

from datetime import date
from unittest.mock import patch

import pytest

from utils import email_digest as ed

TODAY = date(2026, 8, 30)


def item(nickname="Swift", reg="KL04AS1371", owner="Thomas V",
         label="Insurance", expiry=date(2026, 9, 4), days=5):
    return {
        "nickname": nickname, "registration_number": reg, "owner_name": owner,
        "label": label, "expiry": expiry, "days": days,
    }


# ── wording ──────────────────────────────────────────────────────────────


@pytest.mark.parametrize("days, expected", [
    (-11, "expired 11 days ago"),
    (-1, "expired yesterday"),
    (0, "due today"),
    (1, "due tomorrow"),
    (5, "due in 5 days"),
])
def test_phrasing(days, expected):
    assert ed._phrase(days) == expected


def test_subject_distinguishes_overdue_from_upcoming():
    over = [item(days=-11), item(days=-3)]
    soon = [item(days=5)]
    assert ed._subject(over, TODAY) == "PitStop · 2 expired documents · 30 Aug 2026"
    assert ed._subject(soon, TODAY) == "PitStop · 1 document due · 30 Aug 2026"
    assert ed._subject(over + soon, TODAY) == (
        "PitStop · 2 expired, 1 due soon · 30 Aug 2026"
    )


def test_subject_singular_for_one_expired():
    assert "1 expired document ·" in ed._subject([item(days=-1)], TODAY)


# ── the digest ───────────────────────────────────────────────────────────


def test_digest_puts_the_most_overdue_first():
    items = [item(nickname="Soon", days=5), item(nickname="Worst", days=-380),
             item(nickname="Bad", days=-11)]
    _, html, text = ed.build_digest(items, TODAY)
    assert text.index("Worst") < text.index("Bad") < text.index("Soon")
    assert html.index("Worst") < html.index("Bad") < html.index("Soon")


def test_digest_returns_subject_html_and_text():
    subject, html, text = ed.build_digest([item()], TODAY)
    assert subject.startswith("PitStop ·")
    assert html.lstrip().startswith("<!doctype html>")
    assert "Insurance due in 5 days" in text
    assert "KL04AS1371" in text and "KL04AS1371" in html


def test_digest_layout_spans_both_columns():
    # The item rows are two columns; without colspan the header and footer
    # backgrounds stop at the column boundary.
    _, html, _ = ed.build_digest([item()], TODAY)
    assert html.count('colspan="2"') == 2


def test_digest_marks_overdue_and_upcoming_differently():
    _, over_html, _ = ed.build_digest([item(days=-11)], TODAY)
    _, soon_html, _ = ed.build_digest([item(days=5)], TODAY)
    assert ed._OVER in over_html and ed._OVER not in soon_html
    assert ed._SOON in soon_html


def test_digest_omits_an_absent_owner():
    _, html, text = ed.build_digest([item(owner="")], TODAY)
    assert "&middot; </div>" not in html
    assert "()" not in text


def test_digest_escapes_vehicle_text():
    # Nicknames are user-supplied through the web form and the bot.
    _, html, _ = ed.build_digest([item(nickname='<script>alert(1)</script>')], TODAY)
    assert "<script>" not in html
    assert "&lt;script&gt;" in html


def test_digest_has_a_preheader():
    _, html, _ = ed.build_digest([item(days=-1)], TODAY)
    assert "1 document expired" in html


# ── sending ──────────────────────────────────────────────────────────────


@pytest.fixture
def configured(monkeypatch):
    monkeypatch.setenv("RESEND_API_KEY", "re_test")
    monkeypatch.setenv("EMAIL_FROM", "PitStop <aiassistant@thomasjvarghese.com>")
    monkeypatch.setenv("EMAIL_TO", "owner@example.com")
    monkeypatch.delenv("ENABLE_EMAIL", raising=False)


def test_send_posts_one_digest_for_many_reminders(configured):
    items = [item(days=-11), item(days=3), item(days=5)]
    with patch("utils.email_digest.requests.post") as post:
        assert ed.send_digest(items, TODAY) is True
    post.assert_called_once()                      # one email, not three
    body = post.call_args.kwargs["json"]
    assert body["from"] == "PitStop <aiassistant@thomasjvarghese.com>"
    assert body["to"] == ["owner@example.com"]
    assert body["html"] and body["text"]
    headers = post.call_args.kwargs["headers"]
    assert headers["Authorization"] == "Bearer re_test"
    # A re-run on the same day must not produce a second email.
    assert headers["Idempotency-Key"] == "pitstop-digest-2026-08-30"


def test_send_splits_multiple_recipients(configured, monkeypatch):
    monkeypatch.setenv("EMAIL_TO", "a@x.com, b@y.com")
    with patch("utils.email_digest.requests.post") as post:
        ed.send_digest([item()], TODAY)
    assert post.call_args.kwargs["json"]["to"] == ["a@x.com", "b@y.com"]


def test_send_is_a_no_op_when_nothing_fired(configured):
    with patch("utils.email_digest.requests.post") as post:
        assert ed.send_digest([], TODAY) is False
    post.assert_not_called()


def test_send_is_skipped_without_an_api_key(monkeypatch):
    monkeypatch.delenv("RESEND_API_KEY", raising=False)
    monkeypatch.delenv("ENABLE_EMAIL", raising=False)
    with patch("utils.email_digest.requests.post") as post:
        assert ed.send_digest([item()], TODAY) is False
    post.assert_not_called()


def test_send_respects_the_off_switch(configured, monkeypatch):
    monkeypatch.setenv("ENABLE_EMAIL", "0")
    with patch("utils.email_digest.requests.post") as post:
        assert ed.send_digest([item()], TODAY) is False
    post.assert_not_called()


def test_send_skips_when_no_recipient_is_configured(configured, monkeypatch):
    monkeypatch.setenv("EMAIL_TO", "   ")
    with patch("utils.email_digest.requests.post") as post:
        assert ed.send_digest([item()], TODAY) is False
    post.assert_not_called()


def test_a_failing_mailbox_never_raises(configured):
    # The reminders have already gone out and been logged by this point;
    # a bad mailbox must not take the sweep down with it.
    with patch("utils.email_digest.requests.post", side_effect=OSError("smtp down")):
        assert ed.send_digest([item()], TODAY) is False


def test_an_http_error_never_raises(configured):
    class Resp:
        def raise_for_status(self):
            raise RuntimeError("422 Unprocessable")
    with patch("utils.email_digest.requests.post", return_value=Resp()):
        assert ed.send_digest([item()], TODAY) is False


# ── which offsets earn an email ──────────────────────────────────────────


def test_default_offsets_are_a_week_before_and_the_day_before(monkeypatch):
    monkeypatch.delenv("EMAIL_OFFSETS", raising=False)
    assert ed.email_offsets() == {-7, -1}


def test_offsets_are_configurable(monkeypatch):
    monkeypatch.setenv("EMAIL_OFFSETS", "-7, -1, 7")
    assert ed.email_offsets() == {-7, -1, 7}


def test_unparseable_offsets_fall_back_rather_than_crashing(monkeypatch):
    monkeypatch.setenv("EMAIL_OFFSETS", "banana, ,")
    assert ed.email_offsets() == {-7, -1}
    monkeypatch.setenv("EMAIL_OFFSETS", "-7, banana")
    assert ed.email_offsets() == {-7}


def test_select_keeps_only_emailed_offsets(monkeypatch):
    monkeypatch.delenv("EMAIL_OFFSETS", raising=False)
    items = [
        dict(item(days=7), offset=-7),      # emailed
        dict(item(days=3), offset=-3),      # Discord only
        dict(item(days=1), offset=-1),      # emailed
        dict(item(days=0), offset=0),       # Discord only
        dict(item(days=-30), offset=30),    # Discord only
    ]
    assert [i["offset"] for i in ed.select_for_email(items)] == [-7, -1]


def test_select_keeps_items_that_carry_no_offset(monkeypatch):
    # A caller that does not track offsets still gets a digest.
    monkeypatch.delenv("EMAIL_OFFSETS", raising=False)
    assert len(ed.select_for_email([item(), item()])) == 2


def test_send_skips_when_nothing_is_at_an_emailed_offset(configured, monkeypatch):
    monkeypatch.delenv("EMAIL_OFFSETS", raising=False)
    # The sweep fired, but only at offsets Discord handles.
    items = [dict(item(days=0), offset=0), dict(item(days=-3), offset=3)]
    with patch("utils.email_digest.requests.post") as post:
        assert ed.send_digest(items, TODAY) is False
    post.assert_not_called()


def test_send_narrows_a_mixed_sweep_to_the_emailed_offsets(configured, monkeypatch):
    monkeypatch.delenv("EMAIL_OFFSETS", raising=False)
    items = [
        dict(item(nickname="Weekly", days=7), offset=-7),
        dict(item(nickname="Chatty", days=0), offset=0),
        dict(item(nickname="Tomorrow", days=1), offset=-1),
    ]
    with patch("utils.email_digest.requests.post") as post:
        assert ed.send_digest(items, TODAY) is True
    body = post.call_args.kwargs["json"]
    assert "Weekly" in body["text"] and "Tomorrow" in body["text"]
    assert "Chatty" not in body["text"]


# ── the footer describes the real cadence ────────────────────────────────


def test_footer_states_the_default_cadence(monkeypatch):
    monkeypatch.delenv("EMAIL_OFFSETS", raising=False)
    _, html, text = ed.build_digest([item()], TODAY)
    assert "a week before an expiry and again the day before" in html
    assert "a week before an expiry and again the day before" in text


def test_footer_follows_a_custom_cadence(monkeypatch):
    monkeypatch.setenv("EMAIL_OFFSETS", "-30,-1,7")
    _, html, _ = ed.build_digest([item()], TODAY)
    # The email must never describe a schedule it does not actually keep.
    assert "30 days before, the day before and 7 days after" in html


def test_footer_still_points_at_discord_for_the_full_escalation():
    _, html, _ = ed.build_digest([item()], TODAY)
    assert "goes to Discord" in html


# ── who receives it ──────────────────────────────────────────────────────


def test_the_managed_list_is_the_source_of_truth(configured, monkeypatch):
    monkeypatch.setenv("EMAIL_TO", "stale@example.com")
    rows = [{"email": "a@example.com"}, {"email": "b@example.com"}]
    with patch("db.client.list_notification_recipients", return_value=rows), \
         patch("utils.email_digest.requests.post") as post:
        ed.send_digest([item()], TODAY)
    # EMAIL_TO must not leak in once the list is managed in the database.
    assert post.call_args.kwargs["json"]["to"] == ["a@example.com", "b@example.com"]


def test_env_is_the_fallback_before_anyone_is_added(configured, monkeypatch):
    monkeypatch.setenv("EMAIL_TO", "owner@example.com")
    with patch("db.client.list_notification_recipients", return_value=[]), \
         patch("utils.email_digest.requests.post") as post:
        ed.send_digest([item()], TODAY)
    assert post.call_args.kwargs["json"]["to"] == ["owner@example.com"]


def test_an_unreadable_list_falls_back_rather_than_dropping_the_digest(configured, monkeypatch):
    # The sweep has already reached the database by this point, but one
    # failing read must not silence the reminders.
    monkeypatch.setenv("EMAIL_TO", "owner@example.com")
    with patch("db.client.list_notification_recipients", side_effect=OSError("db down")), \
         patch("utils.email_digest.requests.post") as post:
        assert ed.send_digest([item()], TODAY) is True
    assert post.call_args.kwargs["json"]["to"] == ["owner@example.com"]


def test_no_recipients_anywhere_sends_nothing(configured, monkeypatch):
    monkeypatch.setenv("EMAIL_TO", "")
    with patch("db.client.list_notification_recipients", return_value=[]), \
         patch("utils.email_digest.requests.post") as post:
        assert ed.send_digest([item()], TODAY) is False
    post.assert_not_called()


def test_recipient_addresses_are_never_logged(configured, caplog, monkeypatch):
    """The sweep runs in GitHub Actions on a public repo, where job logs are
    world readable."""
    import logging
    monkeypatch.setenv("EMAIL_TO", "secret.person@example.com")
    with patch("db.client.list_notification_recipients", return_value=[]), \
         patch("utils.email_digest.requests.post"), \
         caplog.at_level(logging.INFO):
        ed.send_digest([item()], TODAY)
    assert "secret.person@example.com" not in caplog.text
    assert "1 recipient(s)" in caplog.text
