# Telegram → Discord cutover ("disable, don't delete")

**Date:** 2026-06-20
**Status:** Approved
**Repos affected:** OneRingToRuleThemAll, Smart_Reminder_System (Pitstop). DropHunter already Discord-only.

## Problem

Telegram is blocked in India. All homelab bots must run on Discord. Telegram code
stays in the tree (reversible) but is disabled by default.

Current state that forces Telegram:
- OneRing `main.py`: Telegram runs as the blocking main thread; Discord is a daemon thread.
- Pitstop `main.py`: hard-`raise` if `TELEGRAM_BOT_TOKEN` missing; Telegram is the blocking thread.
- Pitstop `cron/reminder_sweep.py`: `CRON_NOTIFY_PLATFORM` defaults to `"telegram"`.

Both bots already ship full Discord code (`bot/discord_bot.py`, `utils/notify.py` discord branch).
DropHunter already alerts via Discord webhooks.

## Design

Gate Telegram behind an explicit `ENABLE_TELEGRAM` flag (default off). Make Discord the
required, blocking primary transport. Flip cron notify default to Discord.

### OneRing `main.py`
- Discord = blocking primary (`run_discord()`).
- Start Telegram thread only when `ENABLE_TELEGRAM` is truthy AND `TELEGRAM_BOT_TOKEN` set.

### Pitstop `main.py`
- Same flip. Discord blocking primary; raise on missing `DISCORD_BOT_TOKEN` instead of Telegram.
- Telegram thread gated behind `ENABLE_TELEGRAM` + token.

### Pitstop `cron/reminder_sweep.py`
- `CRON_NOTIFY_PLATFORM` default `"telegram"` → `"discord"`. Env still overrides.
- `chat_id` falls back to `DISCORD_CHANNEL_ID` before `TELEGRAM_CHAT_ID`.

### DropHunter
- Verify no Telegram path. Expect no code change.

`ENABLE_TELEGRAM` truthiness: `"1"`, `"true"`, `"yes"` (case-insensitive).

## Deployment / live cutover
- Ensure `DISCORD_BOT_TOKEN`, `DISCORD_CHANNEL_ID`, `CRON_NOTIFY_PLATFORM=discord`,
  `CRON_NOTIFY_CHAT_ID=<discord channel>` in each repo's `.env` / compose.
- Leave `TELEGRAM_*` present; `ENABLE_TELEGRAM` unset.
- Rebuild + restart `onering-bot`, `pitstop-bot`, `drophunter`.
- Smoke: Discord conversational query to each bot; manual reminder sweep posts to Discord.

## Testing
- Unit: cron default-platform flip (`tests/test_cron.py`).
- `main.py` is `__main__`-guarded; verified by live smoke, not unit tests.

## Out of scope
- Deleting Telegram code, repo renames, merging bots. "Palantir" = Discord display name only.
