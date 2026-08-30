# PitStop

Self-hosted vehicle document expiry tracking. It watches insurance, pollution,
fitness, MV tax and permit dates for a small fleet, escalates reminders as each
one approaches, and keeps nagging until the document is renewed or explicitly
snoozed.

Three ways in: a **web app**, a **chat bot** that answers questions in natural
language, and a **daily sweep** that pushes reminders to Discord and email.

---

## What it tracks

Five documents per vehicle — Insurance, Pollution (PUCC), Fitness / RC validity,
MV Tax and Permit — for every vehicle in the `vehicles` table.

**The reminder schedule, per document:**

| Offset | |
| --- | --- |
| −7, −3, −1, 0 days | before expiry |
| +1, +3, +7, +15, +30 days | after, until renewed |

Each `(vehicle, field, expiry_date, offset)` fires exactly once, tracked in
`reminder_log`. Renewing a document changes the expiry date, which starts a
fresh cycle on its own — no bookkeeping required.

Email is deliberately quieter: it fires at **−7 and −1 only** (`EMAIL_OFFSETS`).
One unrenewed document would otherwise arrive nine times over five weeks. A chat
message is cheap and scrolls away; an inbox does not.

---

## Where it runs

Three parts with genuinely different shapes, so three homes:

| Part | Runs on | Why |
| --- | --- | --- |
| Web app (`web/`) | **Vercel** — [docs/deploy-vercel.md](docs/deploy-vercel.md) | Request/response |
| Reminder sweep (`cron/`) | **GitHub Actions**, daily 07:00 IST | Scheduled, short-lived |
| Discord + Telegram bots (`bot/`) | **A process host** — see [#17](https://github.com/thomasjv799/PitStop/issues/17) | They hold open connections; a serverless function is killed between requests |

All three read the same Supabase Postgres, so they stay in step wherever they
run.

---

## The web app

| Page | |
| --- | --- |
| `/` | Dashboard — fleet counts, then the individual documents needing action |
| `/fleet` | Document matrix: one row per vehicle, five columns, per-row edit / archive / delete |
| `/timeline` | A −60d…+90d rail with everything in the window plotted against today |
| `/vehicles/<reg>` | Every column, plus each document's nine-step reminder ladder |
| `/vehicles/new`, `/<reg>/edit` | Add and edit vehicles |
| `/admin/users` | Approve sign-ins, and manage who receives reminder emails |
| `/costs` | Stub — premium and tax tracking, not built |

**How urgency reads.** A document more than `WEB_SOON_DAYS` (default 30) away
shows its date plainly; anything nearer or already past gets a day-count chip —
amber for due, red for overdue. A snoozed document is struck through and tagged
instead, so a deliberate dismissal never reads as an alarm, and it drops out of
the overdue count.

**Actions.** Clicking any document opens its dialog: **Renew** (write a new
date — the escalation restarts at −7d by itself) or **Snooze** for 7, 14 or 30
days, or indefinitely, with a reason. Snoozes are recorded as `web:<user>`, the
same `platform:id` shape the bots write, so the sweep skips them exactly as it
does for a snooze set from Discord.

**Archive vs delete.** Archive keeps the row and its full reminder history but
drops the vehicle from the fleet, the sweep and the bot — reversible, and the
one to reach for. Delete is permanent, cascades the reminder history, and asks
for the registration typed back.

**Themes.** Light and dark, with a three-way header control (system / light /
dark), remembered per browser and applied before first paint.

---

## Sign-in

Google, with an approval gate. **Signing in and being allowed in are separate.**
Anyone with a Google account can complete the flow; they land in `web_users`
unapproved, see only a waiting room, and an admin approves them at
`/admin/users`.

`ADMIN_EMAIL` is the bootstrap — that address is created approved-and-admin on
its first sign-in, because otherwise there would be nobody able to approve the
first account. It applies only when the row is created, so re-signing in never
re-grants anything and a deliberate demotion sticks.

Access is re-read from the database on every gated request, so revoking someone
takes effect on their next page load rather than whenever their session expires.

`AUTH_MODE=dev` short-circuits all of this to a local user for development. It
paints a red banner on every page, because on a public URL it means anyone with
the link is an administrator.

Setup: **[docs/google-oauth.md](docs/google-oauth.md)**.

---

## Project structure

```
ai/
  base.py              AIProvider ABC
  groq_provider.py     Groq / Llama-3 implementation
  graph.py             LangGraph agent graph
bot/
  message.py           Platform-agnostic Message dataclass
  telegram_bot.py      Telegram listener
  discord_bot.py       Discord listener
  functions.py         LangGraph tools: query_vehicles, update_vehicle_expiry
cron/
  reminder_sweep.py    Daily sweep — fires reminders, deduplicates via reminder_log
db/
  client.py            psycopg2 helpers; connection pool; the only writer
  migrations/          002-007, applied by hand
web/
  app.py               FastAPI routes — pages, actions, admin
  auth.py              Google sign-in and the approval gate
  config.py            Documents, offsets, nav, Settings
  service.py           Pure view-model builders — no database, no request
  templates/           Jinja2
  static/              theme.css (tokens), app.css (layout), app.js
utils/
  notify.py            notify(msg, platform, chat_id) — Discord / Telegram
  email_digest.py      One email per sweep, through Resend
  redact.py            Keeps identifying data out of logs
  env.py               Environment reading that survives blank values
api/index.py           Vercel ASGI entrypoint
scripts/check_db.py    Validate DATABASE_URI before trusting it
main.py                Entrypoint — starts the bots
```

---

## Running locally

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env      # fill in DATABASE_URI at minimum
```

Check the connection string before trusting it. This reports the host, whether
it is reachable from an IPv4-only environment, runs a real query, and never
prints the password:

```bash
python -m scripts.check_db
```

Then:

```bash
python -m web                  # web app on :8000
python main.py                 # Discord (+ Telegram if enabled)
python -m cron.reminder_sweep  # run the sweep by hand
pytest
```

`AUTH_MODE=dev` is the default, so the sign-in button signs you in locally with
no Google client needed.

**Do not point `DATABASE_URI` at Supabase while running `pytest`.** The tests in
`tests/test_db.py` are marked `integration` and write real rows; they skip when
`DATABASE_URI` is unset, which is why the default is to leave it unset locally.

---

## Database

Supabase Postgres 17, in the shared `DropHunter_DB` project. Moved off the
homelab box in August 2026 when its SSD failed — Supabase had been a backup
target until then.

**Use a pooler host.** `db.<ref>.supabase.co` has no A record at all: Supabase
made direct connections IPv6-only, with IPv4 a paid add-on, and GitHub Actions
runners and Vercel functions are both IPv4-only. A direct URI there fails to
*resolve*, not to authenticate, which reads as a timeout and sends you auditing
credentials. Same database, same password; only the hostname and the
`postgres.<ref>` username format differ.

- **Session pooler**, port `5432` — the sweep, and local work.
- **Transaction pooler**, port `6543` — Vercel. Serverless opens far more
  short-lived connections than the session pooler tolerates.

Migrations are applied by hand, in order:

```bash
for f in db/migrations/*.sql; do psql "$DATABASE_URI" -f "$f"; done
```

`006` enables row-level security on every table with no policies. Supabase
exposes `public` through PostgREST to the `anon` and `authenticated` roles, and
neither has `BYPASSRLS` — so no policies means no access through the publishable
key. Nothing here uses that key; the apps connect as `postgres`, which does have
`BYPASSRLS`, and are unaffected.

---

## Environment variables

```
DATABASE_URI            Supabase pooler URI (see above)
DB_POOL_MIN/MAX         connection pool bounds (0/2 on serverless)

DISCORD_BOT_TOKEN       required — primary transport
DISCORD_CHANNEL_ID      cron alert target
ENABLE_TELEGRAM         1/true/yes to re-enable Telegram (off; blocked in India)
TELEGRAM_BOT_TOKEN      from @BotFather
GROQ_API_KEY            from console.groq.com

RESEND_API_KEY          email digest; unset disables it entirely
EMAIL_FROM / EMAIL_TO   sender, and the fallback recipient
EMAIL_OFFSETS           which offsets earn an email (default -7,-1)

AUTH_MODE               dev | google
SESSION_SECRET          required outside dev — and on any serverless host
ADMIN_EMAIL             owner's Google address; approved as admin once
OIDC_CLIENT_ID/SECRET   Google OAuth client
OIDC_REDIRECT_URI       set behind a TLS-terminating proxy
WEB_SOON_DAYS           "due soon" window (default 30)
```

A variable that exists but is **blank** counts as unset everywhere — adding a
key and leaving the box empty is ordinary in a hosting dashboard, and used to
crash the app at import.

---

## GitHub Actions

**Reminder Sweep** (`.github/workflows/reminder_sweep.yml`) — `ubuntu-latest`,
daily at 07:00 IST (01:30 UTC), routes to Discord and sends the email digest.
`workflow_dispatch` is enabled for manual runs.

On failure it posts to the same Discord channel with a link to the run. A silent
sweep is indistinguishable from a quiet day, which is how it went unnoticed that
the job had been dead since the self-hosted runner's disk failed.

Secrets: `DATABASE_URI`, `DISCORD_BOT_TOKEN`, `DISCORD_CHANNEL_ID`,
`RESEND_API_KEY`. Variables: `EMAIL_FROM`, `EMAIL_TO`.

---

## Backup

Vehicle data is backed up to Supabase every 3 days by the DropHunter repo's
`cron/supabase_backup.py`. Now that Supabase *is* the primary, that arrangement
wants revisiting.

---

## License

See [LICENSE.txt](LICENSE.txt).
