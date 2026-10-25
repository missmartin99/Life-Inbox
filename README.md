# Life Inbox

This is the local-project version of the Life Inbox Google Colab notebook.
It preserves the features already present in the notebook:

- save a text note;
- list, edit, and delete saved notes;
- organize the entire inbox into buckets, priorities, and ToDos;
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
`processing_status`, `limit` (1–500, default 100), and `offset` query
parameters. Text search is case-insensitive and matches raw text, title,
extracted text, or original filename.
Category and priority filters use the latest saved analysis insights. The
browser loads notes in pages of 100 with a “Load more” control.

The `/api/notes` routes remain temporarily available as deprecated
compatibility adapters. They read and write the same `inbox_items` records
while preserving the former note response shape. Migration `0003` copies
legacy notes into `inbox_items` with the same IDs and timestamps. The legacy
`notes` table is retained unchanged as a migration safety copy and is no
longer authoritative.

### Persisted analysis

`POST /api/analyze` still organizes the complete inbox. Successful validated
results are stored in `analysis_runs`, and each text item's latest category,
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

Each inbox item can source at most one generated task. Reanalysis updates that
task's latest text, priority, and timing in place instead of creating a
duplicate when AI wording or priority changes. Its open, completed, or
dismissed status is never changed by AI analysis.

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

Do not put the real key in source code or commit it to Git.

## 5. Start Life Inbox

From the top-level `life-inbox` folder, run:

```bash
python -m uvicorn app.main:app --reload
```

Then open:

<http://127.0.0.1:8000>

Stop the server by pressing `Control+C` in the terminal.

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
