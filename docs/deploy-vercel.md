# Deploying the web app to Vercel

**Vercel hosts the web app only.** That is not a limitation of the config here —
it is what serverless means:

| Part | Where it runs | Why |
|---|---|---|
| Web app (`web/`) | **Vercel** | Request/response. A perfect fit. |
| Reminder sweep (`cron/`) | **GitHub Actions** | Already scheduled there; unchanged. |
| Discord + Telegram bots (`bot/`, `main.py`) | **Somewhere else** | They hold open websocket/long-poll connections. A serverless function is killed after each request, so a bot on Vercel would be offline between invocations. |

So the bots need a host that runs a process: Railway, Fly.io, a small VPS, or the
homelab box once it is rebuilt. Nothing about this deployment breaks them — they
keep reading the same Supabase database.

---

## 1. Files

Already in the repo:

- **`api/index.py`** — the ASGI entrypoint. Vercel imports `app` from it. It
  puts the repo root on `sys.path`, without which `from web.app import app`
  fails at cold start.
- **`api/requirements.txt`** — the **web-only** dependency set. Vercel installs
  from here, not the root `requirements.txt`. Pulling in `discord.py`,
  `python-telegram-bot`, `groq`, `langgraph`, `langchain` and `langfuse` would
  add well over a hundred megabytes to a bundle capped at 250 MB unzipped, to
  run code the web app never touches.
- **`vercel.json`** — rewrites every path to the function, and crucially sets
  `includeFiles: "web/**"` so the Jinja templates and static assets are bundled.
  Without that the app imports fine and then 500s on the first render.

## 2. Import the project

Vercel dashboard → **Add New → Project** → import the GitHub repo. Framework
preset **Other**; leave build and output settings empty.

## 3. Environment variables

*Project → Settings → Environment Variables.*

```
DATABASE_URI=postgresql://postgres.[PROJECT-REF]:[PASSWORD]@aws-0-[REGION].pooler.supabase.com:6543/postgres
DB_POOL_MIN=0
DB_POOL_MAX=2

AUTH_MODE=google
SESSION_SECRET=<python -c "import secrets;print(secrets.token_urlsafe(32))">
SESSION_HTTPS_ONLY=1
OIDC_CLIENT_ID=...apps.googleusercontent.com
OIDC_CLIENT_SECRET=...
OIDC_REDIRECT_URI=https://<your-app>.vercel.app/auth/callback
ADMIN_EMAIL=you@gmail.com
```

Three of those are not obvious:

- **Port `6543`, not `5432`.** That is Supabase's *transaction* pooler. Each
  serverless invocation may be a fresh instance opening its own connections;
  the session pooler runs out of them far sooner. The GitHub Actions sweep can
  keep using either.
- **`DB_POOL_MIN=0`.** `ThreadedConnectionPool` opens `minconn` connections
  when it is constructed, which would put a database round trip on every cold
  start. Zero means connect on first use.
- **`SESSION_HTTPS_ONLY=1`.** Vercel is always HTTPS; leaving this at `0`
  hands out a cookie without the Secure flag.

`OIDC_REDIRECT_URI` matters more here than anywhere else — see below.

## 4. Add the Vercel URL to Google

*Cloud Console → Credentials → your OAuth client → Authorised redirect URIs.*

Add, alongside the localhost entries:

```
https://<your-app>.vercel.app/auth/callback
```

Vercel also generates a unique URL per deployment
(`<project>-<hash>-<scope>.vercel.app`). Those will **not** match the registered
URI, so OAuth only works on the stable production domain — or on a custom domain
you add to both Vercel and Google. This is the single most common way this
deployment appears broken while being fine.

Setting `OIDC_REDIRECT_URI` explicitly is what makes this deterministic: without
it, the app derives the callback from the incoming request, which behind
Vercel's proxy can be `http://` and gets rejected as `redirect_uri_mismatch`.

## 5. Deploy and check

```
https://<your-app>.vercel.app/healthz   -> {"status":"ok"}
https://<your-app>.vercel.app/login     -> Continue with Google
```

Then sign in as `ADMIN_EMAIL`, and confirm a second Google account lands in the
waiting room rather than the dashboard.

---

## Things worth knowing before you rely on it

**Migrations are not run by the deploy.** Apply them with `psql` against
`DATABASE_URI` first; `db/migrations/004`–`007` are already applied to the
current database.

**Cold starts.** A first request after idle pays the import plus a fresh
database connection. For a handful of users a second or two occasionally is
fine; it is not a busy-app setup.

**Static files go through the function.** `web/static` is served by
`StaticFiles`, so it costs an invocation. Fine at this scale; move it to a
static route if it ever matters.

**The free tier sleeps nothing but caps invocations.** The reminder sweep does
not touch Vercel, so reminders keep working even if the web app is never
visited.

**Secrets live in Vercel, not the repo.** `.env` is gitignored and only used
locally.
