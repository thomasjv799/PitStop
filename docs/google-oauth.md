# Setting up Google sign-in

PitStop's web app uses Google as its identity provider. **Signing in and being
allowed in are separate things**: anyone with a Google account can complete the
flow, but they see nothing until an admin approves them. Setting this up is two
halves — a Google OAuth client, and the environment that points at it.

Until you do this, leave `AUTH_MODE=dev` and the app signs you in locally as
`DEV_USER` with no IdP at all.

---

## 1. Create the OAuth client

**Google Cloud Console → https://console.cloud.google.com**

1. **Pick or create a project.** Top-left project selector → *New project*.
   Any name; it only groups the credential.

2. **Configure the consent screen.**
   *APIs & Services → OAuth consent screen*.

   - **User type: External.** "Internal" only exists for Google Workspace
     organisations. A personal Gmail account has to use External.
   - App name, your support email, developer contact email. Nothing else is
     required.
   - **Scopes: leave empty.** PitStop asks only for `openid`, `email` and
     `profile`. Those are non-sensitive, so there is **no verification review**
     and nothing to justify.

3. **Choose a publishing status** — this is a real decision, not a formality:

   | Status | What it means |
   |---|---|
   | **Testing** | Only addresses you list as *Test users* can sign in. Up to 100. Refresh tokens expire after 7 days. |
   | **In production** | Anyone with a Google account can sign in — and then sits unapproved in PitStop's waiting room. No review needed at these scopes. |

   **Testing is the better default here.** It gives you a second gate outside
   the app: an address that is not on the test-user list cannot even reach the
   waiting room. Add yourself as a test user before going further.

   The 7-day refresh token expiry does not matter — PitStop does not use refresh
   tokens. It reads the identity once at sign-in and keeps its own session.

4. **Create the credential.**
   *APIs & Services → Credentials → Create credentials → OAuth client ID*.

   - **Application type: Web application.**
   - **Authorised redirect URIs** — add the callback, exactly:

     ```
     https://your-host/auth/callback
     ```

     For local testing Google also permits plain http on loopback:

     ```
     http://localhost:8000/auth/callback
     ```

   - *Authorised JavaScript origins* can stay empty. This is a server-side
     flow; no browser JS talks to Google.

5. **Copy the client ID and client secret.** The secret is shown once.

---

## 2. Point PitStop at it

```bash
AUTH_MODE=google
SESSION_SECRET=$(python -c "import secrets;print(secrets.token_urlsafe(32))")
OIDC_CLIENT_ID=...apps.googleusercontent.com
OIDC_CLIENT_SECRET=...
ADMIN_EMAIL=you@gmail.com

# Set this if a reverse proxy terminates TLS — see the gotcha below.
OIDC_REDIRECT_URI=https://your-host/auth/callback

SESSION_HTTPS_ONLY=1
```

`SESSION_SECRET` is **required** whenever `AUTH_MODE` is not `dev`. In dev mode
a random one is generated per start, which only means sessions do not survive a
restart; for a real sign-in it would invalidate the state cookie mid-handshake.

Run behind whatever terminates your TLS:

```bash
uvicorn web.app:app --host 0.0.0.0 --port 8000 --proxy-headers
```

---

## 3. First sign-in

`ADMIN_EMAIL` is the bootstrap. The first time that address signs in, its
account is created **approved, with the admin role** — otherwise there would be
nobody able to approve the first account.

Three things about that, all deliberate:

- It applies **only when the row is created.** Re-signing in never re-grants
  anything, so an admin who was deliberately demoted stays demoted.
- The address must be **verified by Google**. An unverified email claim cannot
  be used to claim the owner address.
- Everyone else lands with `approved_at` NULL and sees only `/pending` until
  you approve them at `/admin/users`.

---

## Gotchas

**`redirect_uri_mismatch` is almost always the proxy.** PitStop derives the
callback URL from the incoming request. Behind a reverse proxy that terminates
TLS, the app sees plain http, builds `http://your-host/auth/callback`, and
Google — which has the `https://` form registered — rejects it. Two fixes, and
you want both:

- Set `OIDC_REDIRECT_URI` explicitly. It wins over the derived value.
- Run uvicorn with `--proxy-headers` (and `--forwarded-allow-ips` set to your
  proxy) so the rest of the app sees the right scheme too.

**The redirect URI must match to the character** — scheme, host, port and path.
`https://host/auth/callback/` with a trailing slash is a different URI.

**`Error 403: access_denied` while in Testing** means the address is not on the
test-user list. Add it in the consent screen.

**Changing `ADMIN_EMAIL` later does nothing** to an account that already exists.
Promote the new address from `/admin/users` instead.

**Locked out with nobody able to approve?** The app refuses to demote or revoke
the last admin, so this should not happen. If it somehow does, flip a row
directly:

```sql
UPDATE web_users SET role = 'admin', approved_at = now() WHERE email = 'you@gmail.com';
```

---

## Verifying it works

1. `/login` should show *Continue with Google* rather than *Continue as …*.
2. Sign in as `ADMIN_EMAIL` → you land on the dashboard.
3. Sign in as any other Google account → you land on `/pending` and see
   nothing about the fleet.
4. Approve that account at `/admin/users` → it reaches the dashboard on its
   next page load, without signing out and back in.
5. Revoke it → the very next page load bounces back to `/pending`. Access is
   re-read from the database on every gated request, not cached in the session.
