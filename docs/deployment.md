# Deployment guide (private beta)

Life Inbox supports a private multi-user beta with real **Google sign-in**.
It still stops before paid infra or a public production deploy unless an
owner explicitly authorizes those steps.

## Environments

| Env | DB | Sign-in | Notes |
| --- | --- | --- | --- |
| `local` (default) | SQLite | `dev`: anonymous maps to the Local Owner, dev login allowed. `google`: real Google sign-in (for testing it locally) | Temp DBs for agents/tests |
| `beta` | PostgreSQL required | Google only; dev login must be off | Server-side sessions, `Secure` cookies, https required |
| `production` | PostgreSQL required | Same as beta | Not enabled in this teaching build |

In `beta`/`production` the app **refuses to start** if the configuration is
unsafe (SQLite, session secret shorter than 32 characters, dev login on,
auth mode not `google`, missing Google keys, or a non-https public URL).

## Environment variables

See `.env.example` for a template. The app reads real environment variables;
`docker compose` also reads a `.env` file next to `docker-compose.yml`.
`.env` is gitignored — never commit it.

```bash
LIFE_INBOX_ENV=local|beta|production
DATABASE_URL=sqlite:////tmp/life_inbox.db      # or postgresql+psycopg://...
LIFE_INBOX_UPLOAD_DIR=/data/uploads
LIFE_INBOX_SESSION_SECRET=<32+ random chars>  # required in beta/production
LIFE_INBOX_AUTH_MODE=dev|google
LIFE_INBOX_ALLOW_DEV_LOGIN=1|0                # defaults to 0 when AUTH_MODE=google
GOOGLE_CLIENT_ID=<from Google Cloud>
GOOGLE_CLIENT_SECRET=<from Google Cloud>
LIFE_INBOX_PUBLIC_URL=https://inbox.example.com  # no trailing slash
LIFE_INBOX_ALLOWED_EMAILS=a@gmail.com,b@gmail.com   # optional invite list
LIFE_INBOX_ALLOWED_EMAIL_DOMAINS=example.com        # optional
LIFE_INBOX_TIMEZONE=UTC
LIFE_INBOX_MAX_UPLOAD_MB=5
LIFE_INBOX_CALENDAR_PROVIDER=mock|google
```

Generate a session secret with:

```bash
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

Optional: `OPENAI_API_KEY` for live organization only (never in automated tests).

If both invite lists are empty, **any** verified Google account can sign in
(the health endpoint warns about this). For a private beta, set one.

## Setting up Google sign-in

1. In Google Cloud Console, open **APIs & Services → OAuth consent screen**.
   Choose *External*, fill in the app name and support email, and keep the
   app in **Testing** while you add beta users as test users. Only the
   `openid`, `email` and `profile` scopes are needed.
2. Open **APIs & Services → Credentials → Create credentials → OAuth client
   ID**, type **Web application**.
3. Under **Authorized redirect URIs**, add exactly
   `<LIFE_INBOX_PUBLIC_URL>/api/auth/google/callback`, for example
   `https://inbox.example.com/api/auth/google/callback`. For local testing,
   also add `http://localhost:8000/api/auth/google/callback`.
4. Copy the client ID and secret into `GOOGLE_CLIENT_ID` and
   `GOOGLE_CLIENT_SECRET`.

How it works: `/api/auth/google/start` sends the browser to Google with a
`state` value and PKCE. The callback checks both, requires a verified email,
applies the invite list, and starts a server-side session. The cookie holds
only a random token; the database stores its SHA-256 hash, so **Sign out**
and **Sign out everywhere** revoke sessions immediately. Sessions last 14 days.

## Local run (dev login)

```bash
export DATABASE_URL="sqlite:////tmp/life_inbox_beta.db"
export LIFE_INBOX_UPLOAD_DIR="/tmp/life_inbox_uploads"
unset OPENAI_API_KEY
python3 -m pip install -r requirements.txt
python3 -m alembic upgrade head
python3 -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Dev login (local only; refused in beta/production):

```bash
curl -c cookies.txt -X POST http://127.0.0.1:8000/api/auth/dev-login \
  -H 'Content-Type: application/json' \
  -d '{"email":"you@example.com","display_name":"You"}'
```

## Local run (Google sign-in)

Same as above, plus:

```bash
export LIFE_INBOX_AUTH_MODE=google
export GOOGLE_CLIENT_ID="..."
export GOOGLE_CLIENT_SECRET="..."
export LIFE_INBOX_PUBLIC_URL="http://localhost:8000"
```

Open <http://localhost:8000> (use `localhost`, matching the redirect URI you
registered — not `127.0.0.1`). You will be sent to `/login`.

## Moving your existing local inbox to your Google account

In plain local mode everything belongs to the built-in **Local Owner**
(`local@life-inbox.local`). After switching to Google sign-in that data is
not visible to anyone. To hand it to your account:

1. Stop the server and back up the database.
2. Sign in with Google once, so your account exists.
3. Preview, then apply:

```bash
python -m scripts.claim_local_data --email you@gmail.com
```

```bash
python -m scripts.claim_local_data --email you@gmail.com --yes
```

Items, tasks, analysis runs and topics move in one transaction. Topics with
the same name are merged. Uploaded files need no move.

## Container / Postgres

`docker-compose.yml` boots Postgres + the app image in `beta` mode. It is for
private evaluation only. It will not start until `.env` provides
`LIFE_INBOX_SESSION_SECRET`, `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET` and an
https `LIFE_INBOX_PUBLIC_URL`. Because beta cookies are `Secure`, serve the
app behind https (a reverse proxy or tunnel) at that public URL.

Before any shared beta:

1. Use a unique `LIFE_INBOX_SESSION_SECRET`
2. Keep `LIFE_INBOX_ALLOW_DEV_LOGIN=0`
3. Set an invite list (`LIFE_INBOX_ALLOWED_EMAILS` or `_DOMAINS`)
4. Mount durable upload storage and database volumes
5. Take a backup before migrations

The Postgres driver (`psycopg[binary]`) is already in `requirements.txt`.

## Migrations / backups / rollback

- Alembic is the only schema owner: `alembic upgrade head`
- Backup SQLite file or Postgres dump **before** upgrading. SQLite runs in
  WAL mode, so recent writes may sit in `life_inbox.db-wal`: stop the server
  before copying the `.db` file (or copy the `-wal`/`-shm` files with it)
- Rollback of data-bearing revisions (0011, 0012, 0013) is disabled by
  design; restore from backup
- 0013 makes topic names unique per user (0007 had made them globally
  unique, which blocked every second account). On SQLite it rebuilds the
  `topics` table; stop the server while upgrading
- Uploads live under `LIFE_INBOX_UPLOAD_DIR` (local filesystem backend)

## Security / readiness checklist

- [ ] Session secret is unique and long
- [ ] Dev login disabled outside local (enforced at startup)
- [ ] Google redirect URI matches `LIFE_INBOX_PUBLIC_URL` exactly, over https
- [ ] Invite list set for the private beta
- [ ] Calendar writes still disabled
- [ ] User A cannot read/change User B data (covered by `tests/test_auth_isolation.py`)
- [ ] Health endpoint reports env + warnings
- [ ] No secrets committed; `.env`, uploads and DBs gitignored
- [ ] Owner approval before public DNS / paid hosting

## What this does **not** do

- Google Calendar OAuth / calendar writes (sign-in scopes only)
- Object storage (S3) wiring
- Public internet deployment
- Purchases or paid managed services
