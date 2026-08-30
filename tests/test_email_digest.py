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
    monkeypatch.setenv("EMAIL_TO", "thomasjvarghese49@gmail.com")
    monkeypatch.delenv("ENABLE_EMAIL", raising=False)


def test_send_posts_one_digest_for_many_reminders(configured):
    items = [item(days=-11), item(days=3), item(days=5)]
    with patch("utils.email_digest.requests.post") as post:
        assert ed.send_digest(items, TODAY) is True
    post.assert_called_once()                      # one email, not three
    body = post.call_args.kwargs["json"]
    assert body["from"] == "PitStop <aiassistant@thomasjvarghese.com>"
    assert body["to"] == ["thomasjvarghese49@gmail.com"]
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
