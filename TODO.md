# TODO

State as of 31 Aug 2026. Written down because most of these are one step each,
and the reason for them is easy to lose.

---

## 1. Reminders are not going out — `DATABASE_URI` secret is stale

**Blocking. Nothing has been sent since ~26 Aug 2026.**

The GitHub Actions secret still holds the old homelab connection string (dated
20 Jun). The box is gone; the database is Supabase now.

- GitHub → Settings → Secrets and variables → Actions → `DATABASE_URI`
- Use the **session pooler** string, port `5432` — the same one already working
  on Vercel, with the port changed from `6543`.
- Not `db.<ref>.supabase.co`: that host is IPv6-only and hosted runners have no
  IPv4 route to it.
- Verify first, without printing the password:
  ```bash
  DATABASE_URI='…5432/postgres' python -m scripts.check_db
  ```
- Then run **Actions → Reminder Sweep → Run workflow** once by hand. The daily
  schedule alone would leave a day of doubt, and a failure now posts to Discord
  rather than passing silently.

---

## 2. Vercel is running in dev mode on a public URL

`pit-stop-six.vercel.app` currently has `AUTH_MODE=dev`, which means **anyone
who finds the URL is signed in as an administrator** over the real fleet. The
red banner on every page is the reminder, not the fix.

Everything needed is in `.env.vercel` (gitignored, in this repo):

1. Google Cloud Console → Credentials → the OAuth client → **Authorised
   redirect URIs** → add `https://pit-stop-six.vercel.app/auth/callback`
2. If the consent screen is in *Testing*, add the admin address under
   **Audience → Test users**, or sign-in fails with `403: access_denied`
3. Vercel env: `AUTH_MODE=google`, plus `SESSION_SECRET`, `OIDC_CLIENT_ID`,
   `OIDC_CLIENT_SECRET`, `ADMIN_EMAIL`, `OIDC_REDIRECT_URI`,
   `SESSION_HTTPS_ONLY=1`
4. **Redeploy** — Vercel only applies env changes on the next build

Then `/healthz` should read `{"status":"ok","auth_mode":"google"}`.

---

## 3. Rotate the Google client secret

It was pasted into a chat transcript during setup. Nothing is broken and there
is no sign of misuse, but it should not stay live.

Google console → the OAuth client → add a new secret, remove the old one →
update `.env`, `.env.vercel` and the Vercel env → redeploy.

---

## 4. Verify the approval gate end to end

The one thing never proven against reality. Tests cover it thoroughly; a real
second Google account never has.

Sign in with a non-`ADMIN_EMAIL` Google account and confirm: it lands in the
waiting room, sees nothing about the fleet, reaches the dashboard on its **next
page load** after approval at `/admin/users`, and bounces straight back to
`/pending` when revoked.

---

## Open issues

| | | |
|---|---|---|
| [#17](https://github.com/thomasjv799/PitStop/issues/17) | **The bots have nowhere to run** | Their host was the homelab box. They cannot go on Vercel — they hold open connections and a serverless function is killed between requests. Needs Railway, Fly.io, a VPS, or the rebuilt box. The `Dockerfile` already works; it needs `DATABASE_URI`, `DISCORD_BOT_TOKEN`, `GROQ_API_KEY`. |
| [#11](https://github.com/thomasjv799/PitStop/issues/11) | Sweep does not write `ops.job_runs` | Genuinely blocked: the `ops` schema does not exist yet, and the table is shared across repos so its shape should be settled in one place first. |
| [#5](https://github.com/thomasjv799/PitStop/issues/5) | Structured Output | Pre-existing. |
| [#4](https://github.com/thomasjv799/PitStop/issues/4) | Add Omniroute support | Pre-existing. |

---

## Smaller things, when convenient

- **`/costs` is an honest stub.** When it lands it needs a `vehicle_costs`
  table (vehicle_id, expiry_field, amount, paid_on) and an optional amount
  field on the renew action. The nav is data-driven in `web/config.py:NAV`, so
  the page itself is already wired.
- **Decide whether Telegram stays.** Disabled by default since the Discord
  cutover, but every change touching notification routing still carries it.
- **Revisit the backup arrangement.** DropHunter's `cron/supabase_backup.py`
  backs the fleet data *up to* Supabase every 3 days. Supabase is now the
  primary, so that is backing up to itself.
- **Email stops once a document lapses.** `EMAIL_OFFSETS` is `-7,-1`, so an
  expired document never emails again — Discord keeps escalating. Add `7` for
  one nudge a week after expiry if that silence turns out to be wrong.
- **Consider renaming the Vercel project.** `pit-stop-six` is what was free.
  Renaming changes the URL, so it must be done *before* the redirect URI is
  registered with Google, or done in both places together.
