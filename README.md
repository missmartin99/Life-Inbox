# Life Inbox

This is the local-project version of the Life Inbox Google Colab notebook.
It preserves the features already present in the notebook:

- save a text note;
- list, edit, and delete saved notes;
- organize every saved note automatically into buckets, priorities, and ToDos (only new or edited notes are analyzed each time);
- use the OpenAI API when an API key is available;
- use the notebook's deterministic fallback organizer otherwise.

## 1. Requirements

- Python 3.10 or newer
- A terminal
- An OpenAI API key is optional. The fallback organizer works without one.

## 2. Set up the project

Open a terminal, enter this folder, and create a virtual environment:

```bash
python3 -m venv .venv
source .venv/bin/activate
```

On Windows, activate it with:

```powershell
.venv\Scripts\activate
```

Install the dependencies:

```bash
python -m pip install -r requirements.txt
```

## 3. Configure and migrate the database

Life Inbox uses SQLite by default at `data/life_inbox.db`. To use another
database location, set `DATABASE_URL` before running Alembic or the app:

```bash
export DATABASE_URL="sqlite:////absolute/path/to/life_inbox.db"
```

For a new or empty local database, apply all migrations with:

```bash
python -m alembic upgrade head
```

For an existing `data/life_inbox.db`, make a backup and then run the same
upgrade:

```bash
cp data/life_inbox.db data/life_inbox.db.backup
python -m alembic upgrade head
```

The initial migration detects the existing `notes` table, validates its
required columns, and adopts it without deleting or copying any notes. Check
the applied revision with:

```bash
python -m alembic current
```

The application does not run Alembic migrations automatically at startup.
Always `alembic upgrade head` against the target database before running the
app.

### Canonical inbox-item model

The canonical API is `/api/items`. The browser uses this API for text-item
creation, listing, editing, and deletion. Items include:

- `id`, `item_type`, and `processing_status`;
- optional `title`, `source_url`, `original_filename`, and `mime_type`;
- `raw_text`, `created_at`, and optional `updated_at`.

Supported captures: `text` notes, `url` article links (`POST /api/items/url`),
and `pdf` uploads (`POST /api/items/pdf`, multipart field `file`). Image and
voice capture are planned. PDF originals are stored under `data/uploads/` (or
`LIFE_INBOX_UPLOAD_DIR`); preview via `GET /api/items/{id}/file`.

`GET /api/items` accepts optional `q`, `item_type`, `category`, `priority`,
`processing_status`, `limit` (1–500, default 100), `offset`, and `sort`
(`oldest`, the default, or `newest`) query parameters. Text search is case-insensitive and matches raw text, title,
extracted text, or original filename.
Category and priority filters use the latest saved analysis insights. The
browser shows notes newest first, in pages of 100 with a “Load more” control.
Every capture type is listed (text, links, PDFs, images, voice notes), each
with its topic and priority once it has been organized. The filters apply as
soon as they change.

The `/api/notes` routes remain temporarily available as deprecated
compatibility adapters. They read and write the same `inbox_items` records
while preserving the former note response shape. Migration `0003` copies
legacy notes into `inbox_items` with the same IDs and timestamps. The legacy
`notes` table is retained unchanged as a migration safety copy and is no
longer authoritative.

### Persisted analysis

Every saved note is analyzed automatically in the background right after it is
saved (text, link, PDF, image and voice captures, and edits). Only notes that are
new, or edited since their last analysis, are analyzed, and the result is merged
into the saved view so earlier notes keep their results. A single worker runs
the analyses one at a time, so no note is analyzed twice. If a background run
fails the note stays pending and is retried on the next save or page load.
Set `LIFE_INBOX_AUTO_ANALYZE=0` to turn automatic analysis off.

`POST /api/analyze` is the manual trigger for the same thing. When nothing is
new it returns the saved analysis without calling the AI. Use
`POST /api/analyze?scope=all` to re-analyze the whole inbox, and
`GET /api/analyze/pending` to see which notes are still waiting. Successful
validated results are stored in `analysis_runs`, and each text item's latest category,
concise title, and priority are stored in `item_insights`.

`GET /api/analyze/latest` returns the latest successful result without calling
OpenAI. The browser uses it on page load and displays when the result was
generated. Each successful result stores an inbox content fingerprint; the API
sets `is_stale` when items have changed since that run. Failed runs are
recorded separately and never replace the previous successful result or its
item insights.

Schema evolution is owned by Alembic. The application does not create tables at
startup unless `LIFE_INBOX_AUTO_CREATE_TABLES=1` (automated tests only).

### Persistent tasks

Successful analysis runs convert extracted ToDos into persistent tasks.
Generated tasks start as `open` and retain links to every source inbox item.
The default task view shows only open tasks; completed and dismissed tasks can
be viewed separately and reopened.

Task APIs:

- `GET /api/tasks?status=open` lists tasks (`completed`, `dismissed`, and
  `all` are also supported);
- `POST /api/tasks/{id}/complete` completes a task;
- `POST /api/tasks/{id}/reopen` reopens a task;
- `POST /api/tasks/{id}/dismiss` dismisses a task.

There is no separate Tasks page in the browser. Open tasks are on the
Dashboard, grouped as Overdue, Today, Coming up and No date. Tick a task to mark
it done, or use the × to remove one that is not really a task; either can be
undone for a few seconds. Tasks are also answered through Ask. The Dashboard
shows counts (notes, open tasks, overdue, due today), high-priority notes,
recent captures and related notes by title, and refreshes itself every minute.

Dates are taken as read when a note is saved; the app never
asks the user to confirm them (an ambiguous phrase such as "the 30th" or "this
weekend" simply gets the best reading).

Tasks are placed on the attention board (Overdue, Today, Upcoming, Unscheduled,
Snoozed; `GET /api/attention`) from their timing phrase. A phrase with a clock time ("12:15 pm today")
moves the task to Overdue as soon as that time has passed. Set
`LIFE_INBOX_TIMEZONE` (for example `Asia/Kolkata`) so "today" and the clock use
your timezone; it defaults to UTC.

Questions about tasks in Ask My Inbox ("what do I have to do today?", "what is
overdue?", "what is coming up?") are answered from the task board, so every
matching task is listed with its due date and time. Other questions search the
note text as before.

Each inbox item can source at most one generated task. Reanalysis updates that
task's latest text, priority, and timing in place instead of creating a
duplicate when AI wording or priority changes. Its open, completed, or
dismissed status is never changed by AI analysis.

### Privacy layer

Every text AI call goes through `ask_ai()` in `app/privacy.py`, which:

1. replaces phone numbers, emails, card, Aadhaar and PAN numbers with
   placeholders such as `[PHONE_1]` before anything leaves the app;
2. puts the real values back into the AI reply locally;
3. records the call (feature, model, characters sent, number of hidden
   values, never the text) in the `ai_call_log` table.

Items can be marked sensitive; their content is never sent to the AI.

- `POST /api/items/{id}/sensitive` with `{"sensitive": true}` (or `false`);
- `GET /api/privacy/ai-log` lists recent AI calls;
- `POST /api/privacy/redact-preview` with `{"text": "..."}` shows what the AI
  would see.

Image descriptions and voice transcription send the media file itself, so
they are not covered by text redaction.

### Smart templates

An item can be turned into a typed "card": `recipe`, `book`, `contact`,
`event`, or `other`, with structured fields (for example ingredients and
steps for a recipe). The logic lives in `app/item_kinds.py`; to add a new kind,
add one entry to `KIND_FIELDS`.

- `POST /api/items/{id}/card` works out the kind and fields and saves them
  (the AI is used when configured; sensitive items and failures use a simple
  keyword-based fallback);
- `GET /api/cards?kind=recipe` lists items that have a card;
- `GET /api/cards/kinds` lists every kind and its fields.

### Receipts and expenses

`receipt` is a smart-template kind with `vendor`, `amount`, `date` and
`category`. Make a card from a photo of a bill (its OCR text is used) or a
typed note with `POST /api/items/{id}/card`. Without AI, the amount and date
are found with simple patterns (`₹1,250`, `Rs. 300`, `05/11/2026`).

- `GET /api/expenses` returns the total, totals per month and per category,
  and the list of receipts;
- `GET /api/expenses?month=2026-11` limits it to one month.

Receipts need no extra table: their fields live in `inbox_items.kind_fields`.

### People and entities

`app/entities.py` finds the people, places and organizations an item
mentions. Each entity is stored once in `entities` and linked to items through
`item_entities` (many-to-many), so you can open "everything about Dr. Mehta".
Without AI (or for sensitive items) only people are found, from clues like
"Dr. Mehta" or "call Priya".

- `POST /api/items/{id}/entities` finds and saves the entities for one item;
- `GET /api/entities?type=person` lists entities, most-mentioned first;
- `GET /api/entities/{id}` returns the entity and every item that mentions it.

### Semantic search (meaning, not just keywords)

With embeddings on, search and Ask My Inbox match by meaning: "teeth checkup"
finds a dentist note even with no shared words. A small model
(`all-MiniLM-L6-v2`, ~90 MB, downloaded on first use) runs locally through
`fastembed`, so note text is never sent anywhere. Vectors are cached in the
`embedding_cache` table by a hash of the text.

Turn it on (e.g. in `.env`):

```bash
LIFE_INBOX_EMBEDDINGS=1
```

Search results include `meaning_score` next to `keyword_score`. Measure the
difference on sample data with `python -m scripts.eval_search`:

| Search | Reworded questions (hit@1) | Exact-word questions (hit@1) |
|---|---|---|
| Keyword only | 0/12 | 5/5 |
| Keyword + embeddings | 12/12 | 5/5 |

### Resurfacing and flashcards

**Resurfacing** (`app/resurface.py`): `GET /api/resurface` returns notes saved
exactly 1 week, 1 month, 3 months or 1 year ago today ("on this day"), plus up
to 3 notes older than two weeks picked at random (the same pick all day).

**Flashcards** (`app/flashcards.py`) use the Leitner box system: a card you
remember moves up a box and comes back later (1, 3, 7, 14, then 30 days); a card
you forget goes back to box 0 and comes back tomorrow.

- `POST /api/items/{id}/flashcards` makes up to 3 cards from an item (AI when
  configured, otherwise "fill in the blank" cards that hide the longest word);
- `GET /api/flashcards/due` lists cards to review today;
- `POST /api/flashcards/{id}/review` with `{"remembered": true}` (or `false`)
  schedules the next review.

### Public API, webhooks and MCP server

**API tokens** let programs use Life Inbox without logging in:

- `POST /api/tokens` with `{"name": "my script"}` returns a `li_...` token
  (shown only once; only its hash is stored);
- send it as `Authorization: Bearer li_...`;
- `GET /api/tokens` lists tokens, `DELETE /api/tokens/{id}` revokes one.

**Webhooks** POST to your URL when an item is created:

- `POST /api/webhooks` with `{"url": "https://..."}` returns a `secret`;
- each delivery has `X-LifeInbox-Signature: sha256=<HMAC of the body>`;
- local and private addresses are rejected.

**MCP server** (`mcp_server/server.py`) gives AI assistants four tools:
`search_inbox`, `ask_inbox`, `add_note`, `what_needs_attention`. It is a
separate program that calls the API with a token:

```bash
LIFE_INBOX_URL=http://127.0.0.1:8000 LIFE_INBOX_TOKEN=li_... python -m mcp_server.server
```

**Deploying** on Oracle Cloud's free tier with HTTPS: see
[docs/deploy-oracle.md](docs/deploy-oracle.md).

## 4. Optional: configure OpenAI

If you want AI analysis instead of the local fallback, set your API key in the
terminal before starting the application:

```bash
export OPENAI_API_KEY="your-api-key"
export OPENAI_MODEL="gpt-5-mini"
```

On Windows PowerShell:

```powershell
$env:OPENAI_API_KEY="your-api-key"
$env:OPENAI_MODEL="gpt-5-mini"
```

To use DeepSeek instead (it speaks the same API), put these in `.env`:

```bash
OPENAI_API_KEY=your-deepseek-key
OPENAI_BASE_URL=https://api.deepseek.com
OPENAI_MODEL=deepseek-chat
```

Do not put the real key in source code or commit it to Git.

## 5. Start Life Inbox

From the top-level `life-inbox` folder, run:

```bash
python -m uvicorn app.main:app --reload
```

Then open:

<http://127.0.0.1:8000>

Stop the server by pressing `Control+C` in the terminal.

### Sign-in

By default (`LIFE_INBOX_AUTH_MODE=dev`, local only) you are the built-in
Local Owner and no sign-in is needed. To use real Google sign-in, set
`LIFE_INBOX_AUTH_MODE=google`, `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET` and
`LIFE_INBOX_PUBLIC_URL` (template in `.env.example`). Google setup steps,
beta requirements, and how to move your existing Local Owner inbox to your
Google account (`python -m scripts.claim_local_data`) are in
[docs/deployment.md](docs/deployment.md).

## 6. Run the automated tests

From the top-level `life-inbox` folder, run:

```bash
python -m pytest
```

The tests use temporary SQLite databases and do not call OpenAI.

## 7. Optional: add demonstration notes

The original notebook included a utility that adds realistic sample notes.
While the server is stopped, run:

```bash
python -m scripts.seed_demo_notes
```

It uses OpenAI when an API key is available and otherwise uses the notebook's
local fallback list.

## Project layout

```text
life-inbox/
├── app/
│   ├── main.py
│   ├── database.py
│   ├── schemas.py
│   ├── organizer.py
│   ├── templates/index.html
│   └── static/
│       ├── app.js
│       └── styles.css
├── migrations/
│   ├── env.py
│   └── versions/
│       ├── 0001_create_notes_table.py
│       ├── 0002_add_updated_at_to_notes.py
│       ├── 0003_create_inbox_items.py
│       ├── 0004_persist_analysis_results.py
│       ├── 0005_create_tasks.py
│       └── 0006_deduplicate_tasks_by_source.py
├── alembic.ini
├── data/
├── docs/
│   ├── architecture.md
│   ├── manual-testing.md
│   ├── milestone-status.md
│   └── product-roadmap.md
├── scripts/seed_demo_notes.py
├── tests/
│   ├── test_api.py
│   └── test_migrations.py
├── reference/original_life_inbox_notebook.ipynb
├── .github/workflows/ci.yml
├── AGENTS.md
├── .env.example
├── .gitignore
├── requirements.txt
└── README.md
```

The original notebook remains in `reference/` so you can compare the local
project with the classroom version.
