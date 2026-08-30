"""The daily reminder digest, sent through Resend.

The sweep fires once per document, which is right for a chat message but
wrong for email — five due documents would be five separate emails. So the
sweep collects what it sent and this module posts a single digest at the end.

`build_digest` is pure and returns (subject, html, text); only `send_digest`
touches the network.
"""

import logging
import os
from datetime import date
from typing import Any, Iterable, Optional

import requests

logger = logging.getLogger(__name__)

RESEND_ENDPOINT = "https://api.resend.com/emails"

# Which of the sweep's nine offsets are worth an email.
#
# Discord gets all of them — a chat message is cheap and scrolls away. A
# mailbox is not: at every offset, one document that nobody renews would
# arrive nine times over five weeks. The default is a heads-up a week out and
# a final nudge the day before, which is what the inbox is actually good for.
#
# Note this means a lapsed document stops emailing after -1. Add 7 to get one
# follow-up a week after it expires.
DEFAULT_EMAIL_OFFSETS = (-7, -1)


def email_offsets() -> set[int]:
    """The offsets that earn an email, from EMAIL_OFFSETS or the default."""
    raw = os.getenv("EMAIL_OFFSETS", "").strip()
    if not raw:
        return set(DEFAULT_EMAIL_OFFSETS)
    out = set()
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        try:
            out.add(int(part))
        except ValueError:
            logger.warning("Ignoring unparseable EMAIL_OFFSETS entry %r", part)
    return out or set(DEFAULT_EMAIL_OFFSETS)


def select_for_email(items: Iterable[dict]) -> list[dict]:
    """Keep only the reminders whose offset is worth mailing about.

    An item with no `offset` is kept, so a caller that does not track them
    still gets a digest.
    """
    wanted = email_offsets()
    return [i for i in items if i.get("offset") is None or i["offset"] in wanted]

# The same semantic palette the web app uses: red is overdue, amber is due,
# and nothing else in the email carries a hue.
_OVER = "#b42318"
_OVER_BG = "#fef3f2"
_SOON = "#b54708"
_SOON_BG = "#fffaeb"
_TEXT = "#101828"
_MUTED = "#667085"
_FAINT = "#98a2b3"
_BORDER = "#e4e7ec"
_GROUND = "#f4f5f8"
_ACCENT = "#2563eb"


def _enabled() -> bool:
    flag = os.getenv("ENABLE_EMAIL")
    if flag is not None:
        return flag.strip().lower() in {"1", "true", "yes", "on"}
    # No explicit flag: send whenever a key is configured.
    return bool(os.getenv("RESEND_API_KEY"))


def _phrase(days: int) -> str:
    """`expired 11 days ago` / `due in 5 days` / `due today`."""
    if days < -1:
        return f"expired {-days} days ago"
    if days == -1:
        return "expired yesterday"
    if days == 0:
        return "due today"
    if days == 1:
        return "due tomorrow"
    return f"due in {days} days"


def _subject(items: list[dict], today: date) -> str:
    overdue = sum(1 for i in items if i["days"] < 0)
    if not items:
        return f"PitStop · nothing due · {today:%d %b %Y}"
    if overdue == len(items):
        noun = "document" if overdue == 1 else "documents"
        return f"PitStop · {overdue} expired {noun} · {today:%d %b %Y}"
    if overdue:
        return (
            f"PitStop · {overdue} expired, {len(items) - overdue} due soon "
            f"· {today:%d %b %Y}"
        )
    noun = "document" if len(items) == 1 else "documents"
    return f"PitStop · {len(items)} {noun} due · {today:%d %b %Y}"


def _esc(value: Any) -> str:
    return (
        str(value if value is not None else "")
        .replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def _row(item: dict) -> str:
    """One document, as a table row. Email HTML: tables and inline styles
    only — no flexbox, no grid, no stylesheet."""
    overdue = item["days"] < 0
    chip_fg, chip_bg = (_OVER, _OVER_BG) if overdue else (_SOON, _SOON_BG)
    days = item["days"]
    chip = f"{days}d" if days >= 0 else f"&minus;{abs(days)}d"

    owner = f' &middot; {_esc(item["owner_name"])}' if item.get("owner_name") else ""
    return f"""
      <tr>
        <td style="padding:14px 20px;border-top:1px solid {_BORDER};vertical-align:top">
          <div style="font:600 15px/1.3 -apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;color:{_TEXT}">
            {_esc(item["nickname"])}
          </div>
          <div style="font:400 12px/1.5 ui-monospace,Menlo,monospace;color:{_MUTED};padding-top:2px">
            {_esc(item["registration_number"])}{owner}
          </div>
          <div style="font:400 14px/1.5 -apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;color:{chip_fg};padding-top:6px">
            {_esc(item["label"])} {_phrase(days)}
          </div>
          <div style="font:400 12px/1.5 -apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;color:{_FAINT};padding-top:2px">
            Expires {item["expiry"]:%d %b %Y}
          </div>
        </td>
        <td align="right" style="padding:14px 20px;border-top:1px solid {_BORDER};vertical-align:top;white-space:nowrap">
          <span style="display:inline-block;padding:4px 11px;border-radius:100px;background:{chip_bg};color:{chip_fg};font:600 13px/1.4 -apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif">
            {chip}
          </span>
        </td>
      </tr>"""


def _cadence_sentence() -> str:
    """Describe, in the footer, when email actually arrives."""
    offsets = sorted(email_offsets())
    if set(offsets) == set(DEFAULT_EMAIL_OFFSETS):
        return "You get an email a week before an expiry and again the day before."
    parts = []
    for o in offsets:
        if o < -1:
            parts.append(f"{-o} days before")
        elif o == -1:
            parts.append("the day before")
        elif o == 0:
            parts.append("on the day")
        elif o == 1:
            parts.append("the day after")
        else:
            parts.append(f"{o} days after")
    joined = ", ".join(parts[:-1]) + (" and " + parts[-1] if len(parts) > 1 else parts[0])
    return f"Email arrives {joined}."


def build_digest(items: Iterable[dict], today: Optional[date] = None) -> tuple[str, str, str]:
    """(subject, html, text) for one sweep's worth of reminders.

    Overdue documents lead, then the nearest due. Each item needs
    nickname, registration_number, owner_name, label, expiry and days.
    """
    today = today or date.today()
    items = sorted(items, key=lambda i: i["days"])

    overdue = [i for i in items if i["days"] < 0]
    upcoming = [i for i in items if i["days"] >= 0]
    if overdue and upcoming:
        headline = f"{len(overdue)} expired, {len(upcoming)} coming up"
    elif overdue:
        headline = f"{len(overdue)} document{'' if len(overdue) == 1 else 's'} expired"
    else:
        headline = f"{len(upcoming)} document{'' if len(upcoming) == 1 else 's'} coming up"

    rows = "".join(_row(i) for i in items)
    cadence = _cadence_sentence()

    html = f"""<!doctype html>
<html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="color-scheme" content="light only">
<title>PitStop reminders</title></head>
<body style="margin:0;padding:0;background:{_GROUND};-webkit-font-smoothing:antialiased">
  <!-- Preheader: the line inboxes show next to the subject. -->
  <div style="display:none;font-size:1px;color:{_GROUND};max-height:0;overflow:hidden">
    {headline} &mdash; {today:%d %B %Y}
  </div>
  <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0"
         style="background:{_GROUND};padding:32px 12px">
    <tr><td align="center">
      <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0"
             style="max-width:560px;background:#ffffff;border-radius:16px;overflow:hidden;border:1px solid {_BORDER}">

        <tr><td colspan="2" style="padding:24px 20px 18px">
          <div style="font:700 16px/1.2 -apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;color:{_TEXT};letter-spacing:-.01em">
            <span style="color:{_ACCENT}">&#9679;</span>&nbsp; PitStop
          </div>
          <div style="font:600 22px/1.3 -apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;color:{_TEXT};padding-top:14px;letter-spacing:-.02em">
            {headline}
          </div>
          <div style="font:400 13px/1.5 -apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;color:{_MUTED};padding-top:4px">
            Vehicle document check &middot; {today:%d %B %Y}
          </div>
        </td></tr>

        {rows}

        <tr><td colspan="2" style="padding:18px 20px 24px;border-top:1px solid {_BORDER};background:#fcfcfd">
          <div style="font:400 12px/1.6 -apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;color:{_FAINT}">
            Sent by PitStop's daily sweep. {cadence} The full escalation
            &mdash; 7, 3 and 1 days before, on the day, then 1, 3, 7, 15 and 30
            days after &mdash; goes to Discord.
          </div>
        </td></tr>

      </table>
    </td></tr>
  </table>
</body></html>"""

    lines = [headline.upper(), f"Vehicle document check - {today:%d %B %Y}", ""]
    for i in items:
        owner = f" ({i['owner_name']})" if i.get("owner_name") else ""
        lines.append(
            f"- {i['nickname']} [{i['registration_number']}]{owner}\n"
            f"  {i['label']} {_phrase(i['days'])} - expires {i['expiry']:%d %b %Y}"
        )
    lines += ["", f"Sent by PitStop's daily sweep. {cadence}"]

    return _subject(items, today), html, "\n".join(lines)


def send_digest(items: Iterable[dict], today: Optional[date] = None) -> bool:
    """Post the digest to Resend. Returns whether anything was sent.

    Never raises: a failed digest must not fail the sweep, because the
    reminders themselves have already gone out and been logged.
    """
    items = select_for_email(items)
    if not items:
        logger.info("Nothing at an emailed offset today; no digest to send.")
        return False
    if not _enabled():
        logger.info("Email digest disabled (no RESEND_API_KEY).")
        return False

    api_key = os.environ["RESEND_API_KEY"]
    sender = os.getenv("EMAIL_FROM", "PitStop <aiassistant@thomasjvarghese.com>")
    recipients = [a.strip() for a in os.getenv("EMAIL_TO", "").split(",") if a.strip()]
    if not recipients:
        logger.warning("EMAIL_TO is unset; skipping the digest.")
        return False

    subject, html, text = build_digest(items, today)
    try:
        resp = requests.post(
            RESEND_ENDPOINT,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
                # One digest per recipient per day: a retried run must not
                # produce a second email.
                "Idempotency-Key": f"pitstop-digest-{(today or date.today()).isoformat()}",
            },
            json={"from": sender, "to": recipients, "subject": subject,
                  "html": html, "text": text},
            timeout=15,
        )
        resp.raise_for_status()
    except Exception:
        logger.exception("Could not send the reminder digest")
        return False

    logger.info("Digest emailed to %s (%d item(s))", ", ".join(recipients), len(items))
    return True
