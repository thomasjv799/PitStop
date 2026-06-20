import logging
import os
from datetime import date, timedelta

from dotenv import load_dotenv

from db import client as db
from utils.notify import notify

logger = logging.getLogger(__name__)

PRE_OFFSETS  = [-7, -3, -1, 0]
POST_OFFSETS = [1, 3, 7, 15, 30]
ALL_OFFSETS  = PRE_OFFSETS + POST_OFFSETS
CATCH_UP_DAYS = 2

_FIELD_LABELS: dict[str, str] = {
    "insurance_valid_until": "Insurance",
    "pucc_valid_until":      "Pollution (PUCC)",
    "fitness_valid_until":   "Fitness / RC validity",
    "mv_tax_valid_until":    "MV Tax",
    "permit_valid_until":    "Permit",
}


def _build_message(vehicle: dict, label: str, expiry: date, remaining: int) -> str:
    name  = vehicle.get("nickname") or vehicle["registration_number"]
    owner = vehicle.get("owner_name") or "Unknown"
    if remaining < 0:
        status = f"EXPIRED {-remaining} day(s) ago"
    elif remaining == 0:
        status = "expires TODAY"
    else:
        status = f"in {remaining} day(s)"
    return (
        f"<b>Vehicle Reminder</b>\n"
        f"{name} ({vehicle['registration_number']}) — {owner}\n"
        f"{label}: {expiry}  ({status})"
    )


def sweep() -> int:
    today    = date.today()
    vehicles = db.get_all_vehicles_with_expiry()
    platform = os.environ.get("CRON_NOTIFY_PLATFORM", "discord")
    chat_id  = (
        os.environ.get("CRON_NOTIFY_CHAT_ID")
        or os.environ.get("DISCORD_CHANNEL_ID")
        or os.environ.get("TELEGRAM_CHAT_ID")
    )
    sent = 0

    for v in vehicles:
        for field, label in _FIELD_LABELS.items():
            expiry: date | None = v.get(field)
            if expiry is None:
                continue
            for offset in ALL_OFFSETS:
                trigger_date = expiry + timedelta(days=offset)
                in_window = trigger_date <= today <= trigger_date + timedelta(days=CATCH_UP_DAYS)
                if not in_window:
                    continue
                if db.is_snoozed(v["id"], field):
                    continue
                if db.reminder_already_sent(v["id"], field, expiry, offset):
                    continue
                remaining = (expiry - today).days
                notify(_build_message(v, label, expiry, remaining), platform=platform, chat_id=chat_id)
                db.log_reminder(v["id"], field, expiry, offset)
                sent += 1
                logger.info("Sent: %s %s offset=%d", v["registration_number"], field, offset)
    return sent


def run() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    load_dotenv()
    count = sweep()
    logger.info("Sweep complete. %d reminder(s) sent.", count)


if __name__ == "__main__":
    run()
