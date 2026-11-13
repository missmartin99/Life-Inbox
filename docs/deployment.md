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
LIFE_INBOX_TIMEZONE=Asia/Kolkata               # default: India time (IST) for "today", deadlines and times shown
LIFE_INBOX_MAX_UPLOAD_MB=5
LIFE_INBOX_CALENDAR_PROVIDER=mock|google       # old preview-only adapter (/api/calendar/preview)
LIFE_INBOX_GOOGLE_CALENDAR=1|0                # Google Calendar sync; on when Google sign-in is set up
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
   app in **Testing** while you add beta users as test users. Sign-in needs
   only the `openid`, `email` and `profile` scopes; Google Calendar adds
   `calendar.app.created` (see [Google Calendar](#google-calendar)).
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
revokes the session immediately (`POST /api/auth/logout-all` ends every
session for the user). **Sign in from another account** signs out and opens
Google's account picker. Sessions last 14 days.

Profile photos: the Account button shows the Google account picture. People
can upload their own photo (PNG, JPEG, WebP or GIF up to 5 MB) from the
Account menu; it is cropped to a 256×256 JPEG with camera and location data
removed, stored under `LIFE_INBOX_UPLOAD_DIR/avatars/`, and only its owner
can load it. **Remove** goes back to the Google picture. Migration
`0014_profile_photos` adds the two columns (plain `ADD COLUMN`, no rebuild).

Profile page (`/profile`, from **Account → Edit profile**): photo, name, email
(read-only; it comes from Google), an optional date of birth, and the Google
connection. `PATCH /api/me/profile` saves the name and date of birth (a real
date between 1900 and today). Only the account's owner can see either.
Migration `0015_profile_details` adds `date_of_birth`.

Settings page (`/settings`, from **Account → Settings**), saved per person with
`PATCH /api/me/settings` (migration `0016_user_settings` adds `settings_json`):

- **Notification settings:** show the count on the bell, turn it red when
  something is overdue, and what it lists (all pending tasks, only overdue and
  due today, or only high priority). `/api/notifications` applies these.
- **Theme preferences:** Dark (default), Light, or Match my device.
  `app/static/theme.js` applies it in `<head>` before the page paints; the
  browser keeps a copy so the first paint is right.
- **Connected apps** (`/api/me/connections`): Google (sign-in, with a link to
  remove access in the Google Account), Google Calendar (Connect / Sync now /
  Disconnect, below), and OpenAI (on only when the server has
  `OPENAI_API_KEY`; it says so, because note text is then sent to OpenAI).

## Google Calendar

Each person can choose to put their tasks that have a due date on a separate
**"Life Inbox"** calendar in their own Google Calendar (**Settings → Connected
apps → Connect Google Calendar**).

- **Permission:** only `https://www.googleapis.com/auth/calendar.app.created`.
  Life Inbox can create its own calendar and manage the events on it, and
  nothing else: it cannot see or change any other calendar or event.
- **What goes on it:** every open task with a due date. A task with a time of
  day ("tomorrow at 3pm") becomes a 30-minute event, otherwise it is an
  all-day event. Events are coloured by priority (High red, Medium yellow,
  Low grey) and marked *free*, so they never block your time. Finishing,
  dismissing or deleting a task takes it off; reopening or re-dating it puts
  it back or moves it.
- **When it syncs:** in a background thread after anything that can change
  tasks (saving or deleting notes, analysis, completing, dismissing, snoozing
  or re-dating tasks), when the inbox is opened (this also retries after an
  error), and on **Sync now**. Only changes are sent to Google.
- **Tokens:** the refresh token and access token are stored encrypted
  (`app/token_crypto.py`, a key derived from `LIFE_INBOX_SESSION_SECRET`).
  If that secret changes, people are asked to reconnect.
- **Disconnect** deletes the "Life Inbox" calendar from Google Calendar,
  revokes the access, and forgets the tokens. Tasks stay in Life Inbox.
- If someone removes access in their Google Account, or Google expires it,
  the Settings card says **Reconnect**. If they delete the "Life Inbox"
  calendar in Google Calendar while still connected, it is made again on the
  next sync (disconnect to stop it for good).

Setting it up in Google Cloud (same project and OAuth client as sign-in):

1. **APIs & Services → Library → Google Calendar API → Enable.** Until this
   is on, the Settings card says the API is turned off.
2. **OAuth consent screen → Data access → Add or remove scopes:** add
   `.../auth/calendar.app.created`. No new redirect URI is needed; connecting
   reuses `/api/auth/google/callback`.
3. While the app is in **Testing**, Google shows an "unverified app" screen
   and expires refresh tokens after 7 days, so people will be asked to
   reconnect weekly. Publishing the app removes both (Google may first ask
   to verify the app for this scope).

Set `LIFE_INBOX_GOOGLE_CALENDAR=0` to hide the feature. Routes:
`GET /api/auth/google/calendar/start` (to Google), `GET /api/calendar/google`
(status), `POST /api/calendar/google/sync`, `DELETE /api/calendar/google`.
Migration `0017_google_calendar` adds `calendar_connections` and
`calendar_task_events`.

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
- [ ] Google Calendar API enabled in the Cloud project (or `LIFE_INBOX_GOOGLE_CALENDAR=0`)
- [ ] Session secret kept stable (Calendar tokens are encrypted with it)
- [ ] User A cannot read/change User B data (covered by `tests/test_auth_isolation.py`)
- [ ] Health endpoint reports env + warnings
- [ ] No secrets committed; `.env`, uploads and DBs gitignored
- [ ] Owner approval before public DNS / paid hosting

## What this does **not** do

- Reading your existing Google Calendar events (Life Inbox only manages its
  own "Life Inbox" calendar)
- Object storage (S3) wiring
- Public internet deployment
- Purchases or paid managed services
