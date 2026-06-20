# CLAUDE.md

Guidance for Claude Code when working in this repository.

## Project overview

Smart Reminder System is a self-hosted vehicle document expiry tracker. It has two parts:

1. **Bot** — Telegram + Discord listener with a LangGraph / Groq agent. Users can query vehicle data and update expiry dates in natural language.
2. **Cron** — daily GitHub Actions sweep that fires escalating reminders (Telegram by default) for upcoming and overdue document expirations.

## Directory structure

```
ai/                  AIProvider ABC + GroqProvider + LangGraph graph
bot/
  message.py         Platform-agnostic Message dataclass (platform, user_id, chat_id, text)
  telegram_bot.py    python-telegram-bot listener
  discord_bot.py     discord.py listener
  functions.py       LangGraph tools exposed to the LLM
cron/
  reminder_sweep.py  Daily sweep — escalating schedule, deduplicates via reminder_log
db/
  client.py          psycopg2 helpers for the public schema
utils/
  notify.py          notify(msg, platform, chat_id) — routes to Telegram or Discord
tests/               pytest unit tests
main.py              Entrypoint — starts both bot listeners in threads
```

## Common commands

```bash
pip install -r requirements.txt   # install deps
python main.py                    # run both bots
python -m cron.reminder_sweep     # run cron sweep manually
pytest                            # run tests
```

## Architecture notes

- **Entry point:** `main.py` runs the Discord bot on the main thread (blocking, required) and starts the Telegram bot in a daemon thread only when `ENABLE_TELEGRAM` is truthy. Telegram is disabled by default (blocked in India).
- **Platform routing:** `bot/message.py` defines `Message(platform, user_id, chat_id, text)`. Both bot listeners normalise incoming messages to this dataclass before passing to the agent. Reply routing uses `msg.platform` + `msg.chat_id` — the agent is platform-agnostic.
- **AI layer:** `GroqProvider` implements the `AIProvider` ABC. The LangGraph graph in `ai/graph.py` has nodes: `load_memory → agent → execute_tools → save_memory`. Tools are defined in `bot/functions.py`.
- **Cron (`cron/reminder_sweep.py`):** Fires at offsets `[-7, -3, -1, 0, +1, +3, +7, +15, +30]` days relative to each document's expiry date. Each `(vehicle_id, expiry_field, expiry_date, trigger_offset)` is unique-constrained in `reminder_log` — if a row already exists the reminder was already sent. Renewing a document (changing the expiry date) naturally creates new rows with the new date, resetting the cycle.
- **Database:** Local homelab Postgres (`homelab` DB, `public` schema) via psycopg2. Key tables: `vehicles`, `reminder_log`, `reminder_snooze`, `chat_messages`, `chat_summary`. Connection string via `DATABASE_URI` env var.
- **Notifications (`utils/notify.py`):** `notify(msg, platform, chat_id)` dispatches to the correct sender. Cron uses `CRON_NOTIFY_PLATFORM` + `CRON_NOTIFY_CHAT_ID` to decide where alerts go (default: Telegram).
- **Backup:** Vehicle data (`vehicles`, `reminder_log`, `reminder_snooze`) is backed up to Supabase every 3 days by the DropHunter repo's `cron/supabase_backup.py`.

## Environment variables

```
DATABASE_URI            postgresql://user:pass@localhost:5432/homelab
DISCORD_BOT_TOKEN       required (primary transport)
DISCORD_CHANNEL_ID      required — cron alert target for Discord
ENABLE_TELEGRAM         1/true/yes to re-enable Telegram (default off; blocked in India)
TELEGRAM_BOT_TOKEN      from @BotFather (only used when ENABLE_TELEGRAM set)
TELEGRAM_CHAT_ID        your chat ID
GROQ_API_KEY            from console.groq.com
AI_PROVIDER             groq
CRON_NOTIFY_PLATFORM    discord | telegram (default discord)
CRON_NOTIFY_CHAT_ID     override for cron chat ID
```

## GitHub Actions

- **Reminder Sweep** (`.github/workflows/reminder_sweep.yml`) — self-hosted runner, runs daily at 07:00 IST (01:30 UTC). Routes to Discord. Secrets: `DATABASE_URI`, `DISCORD_BOT_TOKEN`, `DISCORD_CHANNEL_ID`.
- `workflow_dispatch` is enabled — trigger manually from the GitHub Actions UI for testing.
