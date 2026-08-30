# CLAUDE.md

Guidance for Claude Code when working in this repository.

## Project overview

Smart Reminder System is a self-hosted vehicle document expiry tracker. It has three parts:

1. **Bot** — Telegram + Discord listener with a LangGraph / Groq agent. Users can query vehicle data and update expiry dates in natural language.
2. **Cron** — daily GitHub Actions sweep that fires escalating reminders (Telegram by default) for upcoming and overdue document expirations.
3. **Web** — FastAPI app behind Google sign-in with an admin approval gate: dashboard, fleet matrix, 90-day timeline, per-vehicle detail, and renew / snooze / archive / delete.

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
web/
  app.py             FastAPI routes — pages, actions, admin
  auth.py            AUTH_MODE=dev|google; sign-in + the approval gate
  config.py          DOCUMENTS, REMINDER_OFFSETS, SNOOZE_OPTIONS, NAV, Settings
  service.py         Pure view-model builders (no DB) — status, queue, timeline,
                     detail, ladder
  templates/         Jinja2 — _shell (nav), login, pending, dashboard, fleet,
                     timeline, vehicle, vehicle_form, users, costs
  static/            theme.css (tokens + primitives), app.css, app.js
tests/               pytest unit tests
main.py              Entrypoint — starts both bot listeners in threads
```

## Common commands

```bash
pip install -r requirements.txt   # install deps
python main.py                    # run both bots
python -m cron.reminder_sweep     # run cron sweep manually
python -m web                     # run the web dashboard on :8000
pytest                            # run tests
```

`tests/test_db.py` and the sweep tests in `tests/test_cron.py` need a live
`DATABASE_URI`; everything else runs offline.

Migrations are applied by hand, in order:

```bash
psql "$DATABASE_URI" -f db/migrations/004_vehicle_archive_and_delete.sql
psql "$DATABASE_URI" -f db/migrations/005_web_users.sql
psql "$DATABASE_URI" -f db/migrations/006_enable_rls.sql
```

**Do not point `DATABASE_URI` at Supabase while running `pytest`.** The tests
in `tests/test_db.py` are marked `integration` and write real rows; they skip
when `DATABASE_URI` is unset, which is why the default is to leave it unset
locally.

## Architecture notes

- **Entry point:** `main.py` runs the Discord bot on the main thread (blocking, required) and starts the Telegram bot in a daemon thread only when `ENABLE_TELEGRAM` is truthy. Telegram is disabled by default (blocked in India).
- **Platform routing:** `bot/message.py` defines `Message(platform, user_id, chat_id, text)`. Both bot listeners normalise incoming messages to this dataclass before passing to the agent. Reply routing uses `msg.platform` + `msg.chat_id` — the agent is platform-agnostic.
- **AI layer:** `GroqProvider` implements the `AIProvider` ABC. The LangGraph graph in `ai/graph.py` has nodes: `load_memory → agent → execute_tools → save_memory`. Tools are defined in `bot/functions.py`.
- **Cron (`cron/reminder_sweep.py`):** Fires at offsets `[-7, -3, -1, 0, +1, +3, +7, +15, +30]` days relative to each document's expiry date. Each `(vehicle_id, expiry_field, expiry_date, trigger_offset)` is unique-constrained in `reminder_log` — if a row already exists the reminder was already sent. Renewing a document (changing the expiry date) naturally creates new rows with the new date, resetting the cycle.
- **Database:** Supabase Postgres 17 — the shared `DropHunter_DB` project (ref and region in the Supabase dashboard, not recorded here — this repo is public), `public` schema, via psycopg2. Moved off the homelab box in Aug 2026 when its SSD failed; the homelab Postgres was the original home and Supabase was only a backup target. Connection string via `DATABASE_URI`.
  - **Use a pooler host.** `db.<ref>.supabase.co` has no A record at all — Supabase made direct connections IPv6-only, with IPv4 a paid add-on — and GitHub-hosted runners and Vercel functions are both IPv4-only. A direct URI there fails to *resolve*, not to authenticate, which reads as a timeout and sends you looking at credentials. Same database, same password, same psycopg2; only the hostname and the `postgres.<ref>` username format differ. Session pooler on 5432 for the sweep and local; transaction pooler on 6543 for serverless.
  - `db.dsn_warning()` detects a direct host and is logged at pool creation and appended to the `OperationalError`, so the failure explains itself. `python -m scripts.check_db` validates a candidate URI — host, port, A/AAAA records, a real query, and whether `web_users` exists — without printing the password.
  - `db/client.py` keeps a `ThreadedConnectionPool` (`DB_POOL_MIN`/`DB_POOL_MAX`), created lazily so importing the module never needs `DATABASE_URI`. A remote database makes the old connect-per-query pattern cost a TLS handshake per call, and a dashboard render makes four.
  - The project is **shared with DropHunter** — `games`, `watches`, `price_history`, `allowed_users` and friends live in the same schema. Migrations here affect that project's database too.
  - `vehicles.status` is `NOT NULL DEFAULT 'ACTIVE'` and every row holds `'ACTIVE'`. Archiving writes `'ARCHIVED'`; restoring writes `'ACTIVE'`, never NULL, which the constraint rejects. Comparisons are case-insensitive.
  - The live `vehicles` table carries eight VAHAN-sourced columns beyond the expiry dates: `manufacturer`, `model`, `emission_norms`, `rto`, `state`, `insurance_company`, `insurance_policy_no`, `permit_no`. `tests/test_schema_contract.py` pins the column list so it cannot drift silently.
  - **Row-level security** is enabled on every table by `006_enable_rls.sql`, with no policies. Supabase exposes `public` through PostgREST to `anon` and `authenticated`; neither role has `BYPASSRLS`, so no policies means no access — which is right, because nothing here uses the anon key. `postgres` (what `DATABASE_URI` connects as) and `service_role` (DropHunter's backup) both carry `BYPASSRLS`, so applications are unaffected. Add policies only if a browser client is ever pointed at this database. Reverse one table with `ALTER TABLE public.<t> DISABLE ROW LEVEL SECURITY;`.
- **Notifications (`utils/notify.py`):** `notify(msg, platform, chat_id)` dispatches to the correct sender. Cron uses `CRON_NOTIFY_PLATFORM` + `CRON_NOTIFY_CHAT_ID` to decide where alerts go (default: Telegram).
- **Web (`web/`):** FastAPI + Jinja2, sharing `db/client.py` with the bots so there is no second path that writes a vehicle row. Pages: `/` dashboard (counts + a queue of documents needing action), `/fleet` (the five-column matrix), `/timeline` (a −60d…+90d rail), `/vehicles/{registration}` (every column plus each document's reminder ladder), `/costs` (a stub), `/admin/users`.
- **Web view model (`web/service.py`):** pure functions over plain dicts — no database, no request. Status, chips, the action queue, timeline positions, the ladder and the detail page are all built here, which is where the expiry rules are tested. Routes fetch rows and hand them over; templates render only what comes back.
- **Web auth (`web/auth.py`):** two modes. `AUTH_MODE=dev` signs in as `DEV_USER` with no IdP and treats them as an approved admin — local work only. `AUTH_MODE=google` runs Google's OIDC flow (setup: `docs/google-oauth.md`). The callback URL is derived from the request unless `OIDC_REDIRECT_URI` is set — behind a TLS-terminating proxy the derived value is `http://` and Google rejects it with `redirect_uri_mismatch`. **Signing in and being allowed to see anything are separate.** Anyone with a Google account can complete the flow and lands in `web_users` with `approved_at` NULL, seeing only `/pending`; an admin approves them at `/admin/users`. `ADMIN_EMAIL` is auto-approved as an admin on its first sign-in only — otherwise nobody could approve the first account. The account is re-read from the database on every gated request, so revoking access takes effect on the next page load. Actions are attributed as `web:<sub>`, matching the bots' `platform:id` shape.
- **Archive vs delete:** `vehicles.status = 'archived'` hides a vehicle from the fleet, the cron sweep and the bot while keeping the row — reversible, and the default suggestion. Permanent deletion requires the registration typed back and cascades `reminder_log` and `reminder_snooze`. Both are reachable from the fleet list's per-row actions (edit / archive-or-restore / delete) as well as the vehicle page; `_delete_dialog.html` is shared between the two and filled from the trigger's `data-reg`/`data-nickname`. Every vehicle query filters on `status IS DISTINCT FROM 'archived'`, which leaves rows holding any other value — NULL included — alone.
- **Design:** `web/static/theme.css` holds the tokens, base elements and primitives; `web/static/app.css` holds page layouts and introduces no colour of its own. Two themes come from one token set: the root carries `data-theme="light"|"dark"` once the viewer picks, and with no attribute the CSS follows `prefers-color-scheme`. An inline script in `base.html` applies the stored choice before first paint so a dark viewer never sees a white flash. The shape language is bento — discrete rounded cards on a soft ground, pill nav and badges, and status carried by soft tinted panels. Red/amber/green are semantic (overdue / due soon / clear) and are used for nothing else. The earlier Nocturne design system was dropped.
- **Vehicle CRUD:** `/vehicles/new` and `/vehicles/{registration}/edit` share `vehicle_form.html`. Validation lives in `service.validate_vehicle` (pure — it never touches the database); the route adds the uniqueness error after asking `db.registration_exists`, and still catches the `UniqueViolation` because that pre-check is racy. Registration marks are normalised to alphanumerics-only uppercase (`kl 04-as 1371` → `KL04AS1371`) so the bot, the sweep and the web app agree on what a vehicle is called. `db.create_vehicle`/`update_vehicle` write only `_WRITABLE_COLS` — identity and lifecycle columns are not form fields.
- **Route order matters:** `/vehicles/new` must stay declared above `/vehicles/{registration}`, or "new" gets matched as a registration.
- **The costs page** is a deliberate stub reading nothing. When it lands it will need a `vehicle_costs` table (vehicle_id, expiry_field, amount, paid_on) and an optional amount field on the renew action; the nav is data-driven in `web/config.py:NAV`, so the page itself is already wired.
- **Email digest (`utils/email_digest.py`):** one email per sweep, through Resend, from `aiassistant@thomasjvarghese.com` (the `thomasjvarghese.com` domain is verified there). The sweep fires per document, which is right for a chat message and wrong for email — five due documents would be five emails — so the sweep collects what it sent and posts a single digest at the end. `build_digest` is pure and returns `(subject, html, text)`; only `send_digest` touches the network. It swallows its own errors and is called last, because by then the reminders have already gone out and been logged. Skipped entirely when `RESEND_API_KEY` is unset, so local runs never mail anyone. Recipients live in `notification_recipients` and are managed at `/admin/users` — adding someone is a form field, not a redeploy. The managed list is the source of truth; `EMAIL_TO` is the fallback for before anyone is added and for when that one read fails. Addresses are stored lowercased under a unique index on `lower(email)`, so one mailbox receives one copy; re-adding reactivates rather than failing, and `RETURNING (xmax = 0) AS inserted` tells a new recipient from a revived one. **Email fires on only a subset of the nine offsets** — `EMAIL_OFFSETS`, default `-7,-1` (a week before, then the day before). Discord still gets all nine; a chat message is cheap and scrolls away, while at every offset a single unrenewed document would land in the inbox nine times over five weeks. The consequence is that a lapsed document stops emailing after `-1`; add `7` for one follow-up. The footer sentence is generated from whatever `EMAIL_OFFSETS` holds, so the email never describes a cadence it does not have. The HTML is table-and-inline-styles only — no flexbox, grid or stylesheet survives an email client — and header/footer cells carry `colspan="2"` to span the two-column item rows.
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

AUTH_MODE               dev | google (default dev — signs in as DEV_USER)
DEV_USER                username used by AUTH_MODE=dev
SESSION_SECRET          required whenever AUTH_MODE is not "dev"
ADMIN_EMAIL             owner's Google address — auto-approved as admin once
OIDC_CLIENT_ID          Google OAuth client id
OIDC_CLIENT_SECRET      Google OAuth client secret
OIDC_ISSUER             defaults to https://accounts.google.com
WEB_HOST / WEB_PORT     dev server bind (default 127.0.0.1:8000)
WEB_SOON_DAYS           "due soon" window in days (default 30)
```

## Logging and PII

`utils/redact.py` keeps identifying data out of log lines. This matters most
for the cron sweep, which runs in GitHub Actions on a **public** repository —
its job log is world readable — but the same rule is applied throughout.

- `redact.vehicle(row)` → `#7`. Preferred: an id identifies nobody outside the
  database and debugs just as well. The sweep uses this.
- `redact.reg("KL04AS1371")` → `KL04••••71`, for the web routes that only hold
  the mark. Keeps the RTO prefix and last two digits, enough to tell vehicles
  apart in a log.
- `redact.email(addr)` → `t•••@gmail.com`. The domain survives because "did it
  reach the right provider" is a real question; the local part does not.
- `redact.text(body)` → `<25 chars>`. The bots used to log up to 80 characters
  of whatever a user typed.

Opaque identifiers are deliberately **not** masked: the OIDC subject and the
`web:<sub>` actor string are the audit trail, and masking them would lose who
did what without gaining any privacy.

One known gap: a psycopg2 `UniqueViolation` message embeds the conflicting key
value, so a duplicate-registration error logged with `exc_info` can carry the
mark. That path is web-only, where logs are private to the host.

## Deployment

- **Web app → Vercel.** `api/index.py` is the ASGI entrypoint, `api/requirements.txt` is a **web-only** dependency set (the bots' AI stack would blow past the 250 MB bundle cap for code the web app never calls), and `vercel.json` sets `includeFiles: "web/**"` — without it the app imports fine and then 500s on the first template render. Use Supabase's **transaction** pooler (port 6543) with `DB_POOL_MIN=0`: each invocation may be a fresh instance, and `ThreadedConnectionPool` otherwise opens connections eagerly on every cold start. Full notes in `docs/deploy-vercel.md`.
- **Bots cannot run on Vercel.** `main.py` holds open Discord/Telegram connections; a serverless function is killed between requests. They need a process host.
- **Cron stays on GitHub Actions** and is unaffected by where the web app lives.

## GitHub Actions

- **Reminder Sweep** (`.github/workflows/reminder_sweep.yml`) — self-hosted runner, runs daily at 07:00 IST (01:30 UTC). Routes to Discord. Secrets: `DATABASE_URI`, `DISCORD_BOT_TOKEN`, `DISCORD_CHANNEL_ID`.
- `workflow_dispatch` is enabled — trigger manually from the GitHub Actions UI for testing.
