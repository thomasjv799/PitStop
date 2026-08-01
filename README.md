# Smart Reminder System
 
A self-hosted vehicle document expiry tracker. Sends escalating reminders via Telegram (and optionally Discord) before and after documents expire. Responds to natural language queries via an AI bot powered by Groq + LangGraph.

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

Run tests:

```bash
pytest
```

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
