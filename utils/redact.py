"""Keeping identifying data out of log files.

The cron sweep runs in GitHub Actions on a public repository, so its job log
is world readable. The web app's and the bots' logs are private to whoever
hosts them, but the same rule is applied throughout: a log line should carry
enough to debug with and nothing that identifies a person or a vehicle.

Anything opaque and already non-identifying is left alone — the OIDC subject
and `web:<sub>` actor strings are the audit trail, and masking them would
lose the ability to tell who did what without gaining any privacy.
"""

from typing import Any, Optional

BULLET = "•"


def reg(registration: Optional[str]) -> str:
    """`KL04AS1371` -> `KL04••••71`.

    Keeps the RTO prefix and the last two digits, which is enough to tell two
    vehicles apart in a log without writing down the full mark. Prefer
    `vehicle()` where the row is to hand — an id identifies nobody at all.
    """
    value = (registration or "").strip()
    if not value:
        return "?"
    if len(value) <= 6:
        return value[0] + BULLET * (len(value) - 1)
    return f"{value[:4]}{BULLET * (len(value) - 6)}{value[-2:]}"


def vehicle(row: Any) -> str:
    """`#7` from a vehicle row or id — meaningless outside the database."""
    if isinstance(row, dict):
        vid = row.get("id")
        return f"#{vid}" if vid is not None else reg(row.get("registration_number"))
    return f"#{row}"


def email(address: Optional[str]) -> str:
    """`thomas@gmail.com` -> `t•••@gmail.com`.

    The domain survives because "did it go to the right provider" is a real
    debugging question; the local part does not.
    """
    value = (address or "").strip()
    if not value or "@" not in value:
        return "?"
    local, _, domain = value.partition("@")
    return f"{local[:1]}{BULLET * 3}@{domain}"


def text(value: Optional[str]) -> str:
    """`<42 chars>` — for message bodies, which are none of a log's business."""
    return f"<{len(value or '')} chars>"
