# Smart Reminder System
 
A self-hosted vehicle document expiry tracker. Sends escalating reminders via Telegram (and optionally Discord) before and after documents expire. Responds to natural language queries via an AI bot powered by Groq + LangGraph, and puts the whole fleet on a web dashboard behind Google sign-in with an admin approval gate.

---

## What it tracks

Insurance, Pollution (PUCC), Fitness / RC validity, MV Tax, Permit — for every vehicle in the local Postgres `vehicles` table.

---

## Architecture

```
Telegram / Discord DM
        │
   bot/message.py         platform-agnostic Message dataclass
        │
   ai/graph.py            LangGraph agent (Groq / Llama-3)
    ├── load_memory        chat history from Postgres
    ├── agent              tool calling
    ├── execute_tools      query_vehicles, update_vehicle_expiry
    └── save_memory        persist turn + rolling summarisation
        │
   db/client.py           psycopg2 — public schema (vehicles, reminder_log, etc.)

cron/reminder_sweep.py    daily GitHub Actions job — escalating reminder schedule
utils/notify.py           platform router — Telegram / Discord send

Browser
        │
   web/auth.py            Google sign-in + the admin approval gate
   web/app.py             FastAPI routes over the same db/client.py
   web/service.py         vehicle rows → dashboard / matrix / timeline / detail
```

**Reminder schedule (per document, per vehicle):**

| Offset | When fired |
|---|---|
| −7, −3, −1, 0 days | Before expiry |
| +1, +3, +7, +15, +30 days | After expiry (until renewed) |

Each `(vehicle, field, expiry_date, offset)` fires exactly once — tracked in `reminder_log`. Renewing a document resets the cycle automatically.

**Platform routing:** A message from Telegram is always replied to on Telegram; Discord likewise. The `Message` dataclass in `bot/message.py` carries the platform so the agent never needs to know. Cron alerts go to whichever platform is set in `CRON_NOTIFY_PLATFORM`.

---

## Project structure

```
ai/
  base.py              AIProvider ABC
  groq_provider.py     Groq / Llama-3 implementation
  graph.py             LangGraph agent graph
bot/
  message.py           Platform-agnostic Message dataclass
  telegram_bot.py      Telegram listener (python-telegram-bot)
  discord_bot.py       Discord listener (discord.py)
  functions.py         LangGraph tools: query_vehicles, update_vehicle_expiry
cron/
  reminder_sweep.py    Daily sweep — fires reminders, deduplicates via reminder_log
db/
  client.py            psycopg2 helpers (public schema — vehicles, reminder_log, etc.)
utils/
  notify.py            notify(msg, platform, chat_id) — Telegram / Discord send
web/
  app.py               FastAPI routes — pages, actions, admin
  auth.py              AUTH_MODE=dev | google — sign-in and the approval gate
  config.py            The five documents, the nine offsets, nav, Settings
  service.py           Pure view-model builders — status, queue, timeline, detail
  templates/           Jinja2 — shell, login, pending, dashboard, fleet,
                       timeline, vehicle, users, costs
  static/              nocturne.css (design system), app.css, app.js
tests/                 pytest unit tests
main.py                Entrypoint — starts Telegram + Discord bots in threads
```

---

## Environment variables

| Variable | Description |
|---|---|
| `DATABASE_URI` | `postgresql://user:pass@localhost:5432/homelab` |
| `TELEGRAM_BOT_TOKEN` | From @BotFather |
| `TELEGRAM_CHAT_ID` | Your Telegram chat ID (for cron alerts) |
| `DISCORD_BOT_TOKEN` | Optional — enables Discord bot listener |
| `DISCORD_CHANNEL_ID` | Optional — Discord channel for cron alerts |
| `GROQ_API_KEY` | From console.groq.com |
| `AI_PROVIDER` | `groq` |
| `CRON_NOTIFY_PLATFORM` | `telegram` or `discord` (where cron alerts go) |
| `CRON_NOTIFY_CHAT_ID` | Override chat ID for cron (defaults to `TELEGRAM_CHAT_ID`) |

---

## GitHub Actions

| Workflow | Schedule | Runner | What it does |
|---|---|---|---|
| Reminder Sweep | Daily 07:00 IST / 01:30 UTC | self-hosted | Runs `cron/reminder_sweep.py` — fires escalating reminders via Telegram |

`workflow_dispatch` enabled for manual runs from the GitHub Actions UI.

**GitHub secrets required:** `DATABASE_URI`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`

---

## Running locally

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # fill in credentials
python main.py
```

Run the cron sweep manually:

```bash
python -m cron.reminder_sweep
```

Run the web dashboard:

```bash
python -m web            # http://127.0.0.1:8000
```

With `AUTH_MODE=dev` (the default) the sign-in button signs you in as `DEV_USER`
and treats you as an approved admin — no Google client needed. Everything else
is live: real vehicles, and every action writes to Postgres.

Apply the migrations once before first run:

```bash
psql "$DATABASE_URI" -f db/migrations/004_vehicle_archive_and_delete.sql
psql "$DATABASE_URI" -f db/migrations/005_web_users.sql
```

In production run uvicorn behind the reverse proxy that terminates TLS:

```bash
uvicorn web.app:app --host 0.0.0.0 --port 8000 --proxy-headers
```

Run tests:

```bash
pytest
```

`tests/test_db.py` and the sweep tests in `tests/test_cron.py` talk to a real
database and need `DATABASE_URI` set; the rest run offline.

---

## Sign-in and access

PitStop uses Google sign-in, and **signing in is not the same as being allowed
in.** Anyone with a Google account can complete the flow; they land in a
waiting room and see nothing about the fleet until an administrator approves
them at `/admin/users`.

Create an OAuth client at *console.cloud.google.com → APIs & Services →
Credentials → OAuth client ID → Web application*, with authorised redirect URI
`https://<your-host>/auth/callback`, then:

```bash
AUTH_MODE=google
SESSION_SECRET=$(python -c "import secrets;print(secrets.token_urlsafe(32))")
OIDC_CLIENT_ID=...
OIDC_CLIENT_SECRET=...
ADMIN_EMAIL=you@gmail.com     # approved as admin on first sign-in
```

`ADMIN_EMAIL` is the bootstrap: that address is approved as an admin the first
time it signs in, because otherwise there would be nobody able to approve the
first account. It applies only when the account row is created — re-signing in
never re-grants anything, and an admin who was deliberately demoted stays
demoted. The address must be verified by Google before it counts.

On `/admin/users` an admin can approve, revoke, promote to admin, demote, or
remove an account. Three things are refused: revoking your own access, demoting
or removing the last admin, and removing your own account — each would leave
the instance with nobody able to undo it.

---

## The web app

| Page | What it is |
| --- | --- |
| `/` Dashboard | Fleet counts, then a queue of the individual documents that need doing, nearest expiry first |
| `/fleet` | The document matrix — one row per vehicle, five document columns |
| `/timeline` | A −60d…+90d rail with every document in the window plotted against today |
| `/vehicles/<reg>` | Every column on the vehicle, plus each document's nine-step reminder ladder |
| `/costs` | Stub — premium and tax tracking, not built yet |
| `/admin/users` | Approve and manage sign-ins (admins only) |

**How urgency reads.** A document more than `WEB_SOON_DAYS` (default 30) away
shows its date plainly; anything nearer or already past gets a day-count chip —
amber for due, red for overdue. A snoozed document is struck through and tagged
instead, so a deliberate dismissal never reads as an alarm, and it drops out of
the overdue count.

**Actions.** Clicking any document opens its dialog:

- **Renew** — write a new expiry date. `reminder_log` is keyed on the expiry
  date, so a new date restarts the escalation at −7d on its own.
- **Snooze** — suppress reminders for 7, 14 or 30 days, or indefinitely, with a
  reason. Recorded in `reminder_snooze` as `web:<user>`, the same
  `platform:id` shape the bots write, so the cron sweep skips it exactly as it
  does for a snooze set from Discord.

**Archive and delete** live on the vehicle page. *Archive* sets
`vehicles.status = 'archived'`: the row and its history stay, but the vehicle
drops out of the fleet, the cron sweep and the bot, and an *Archived* filter
brings it back. *Delete* is permanent — it removes the vehicle and cascades its
reminder history — and requires the registration typed back to confirm.

The look is the Nocturne design system, vendored to `web/static/nocturne.css`
from the Claude Design project *PitStop SSO UI mockups*. Re-export it rather
than editing it by hand.

---

## Deployment

Runs as a Docker container on the homelab Mac mini alongside the local Postgres DB.

```bash
docker build -t smart-reminder .
docker run -d --name smart-reminder --restart unless-stopped \
  --env-file .env --network host smart-reminder
```

`--network host` is required to reach local Postgres on port 5432.

---

## Adding notification channels

Add a send function in `utils/` and register it in `utils/notify.py`'s `_CHANNELS` dict. The bot and cron both call `notify(msg, platform, chat_id)` — no other changes needed.

---

## License

MIT — see `LICENSE.txt`.
