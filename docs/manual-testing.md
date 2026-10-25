# Manual testing / browser smoke checklist

Use a **temporary** database only.

```bash
export DATABASE_URL="sqlite:////tmp/life_inbox_manual.db"
unset OPENAI_API_KEY
python3 -m alembic upgrade head
python3 -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Open http://127.0.0.1:8000

## Core smoke (Milestone 1+)

- [ ] Home page loads with capture box, analyze button, tasks, notes
- [ ] Save a note → appears under Raw saved notes
- [ ] Edit a note → text updates; analysis status becomes stale if prior analysis existed
- [ ] Delete a note → removed; linked orphan task disappears after analyze+delete
- [ ] Analyze Entire Inbox → buckets, todos, priority view, mode shown
- [ ] Reload page → latest analysis restored; tasks persist
- [ ] Complete / dismiss / reopen a task; filter by status
- [ ] Re-analyze after completing a task → task status unchanged
- [ ] Search/filter notes; Clear resets filters
- [ ] With >100 notes, “Load more” appears and appends
- [ ] After editing inbox post-analysis, stale banner / status appears
- [ ] Invalid empty note rejected with friendly status
- [ ] `/api/health` returns `{"status":"ok"}`

## Organization smoke (Milestone 2+)

- [ ] Dashboard shows Topics, High Priority, Open Tasks, Recently Captured, Needs Review, Related
- [ ] Add a custom topic from the Topics form
- [ ] Multi-action note creates multiple tasks from one source
- [ ] Confirm date on a task → labeled confirmed; survives re-analyze
- [ ] Near-duplicate notes appear under Related Items; dismiss removes them from the active list
- [ ] `PATCH /api/items/{id}/insight` user title/topic/priority survives re-analysis

## Ask My Inbox smoke (Milestone 4+)

- [ ] Save notes mentioning a specific topic (book, renewal, restaurant)
- [ ] Ask a matching question → answer cites the correct item(s)
- [ ] Ask an unrelated factual question → insufficient evidence message, no citations
- [ ] Citation “Open source” opens URL or PDF preview where available
- [ ] `GET /api/search?q=...` returns ranked excerpts

## PDF capture smoke (Milestone 3B+)

- [ ] Choose a small text-based PDF → item appears with `item_type=pdf`
- [ ] Processing reaches `ready` with title + extracted preview (or `failed` with error)
- [ ] “Open PDF” link downloads/previews the stored file
- [ ] Retry on failed PDF succeeds when the file is readable
- [ ] Analyze inbox includes ready PDF items
- [ ] Non-PDF or empty/image-only PDF rejected or marked failed with clear message

## Image capture smoke (Milestone 3C+)

- [ ] Choose a PNG/JPEG → item appears with `item_type=image`
- [ ] Thumbnail preview and **Open image** link work
- [ ] OCR (extracted) and AI description (inferred) are labeled separately
- [ ] **Edit OCR text** saves corrected extracted text without changing AI description
- [ ] Retry on failed image succeeds when the file is readable
- [ ] Analyze inbox includes ready image items (after OCR text or description exists)
- [ ] Non-image upload rejected with a clear message

## Voice capture smoke (Milestone 3D+)

- [ ] Upload a short wav/webm → item appears with `item_type=voice`
- [ ] Audio player works from the item card
- [ ] Transcript empty or placeholder when transcription is unconfigured
- [ ] **Edit transcript** saves text and analyze includes the voice item
- [ ] Optional: Record → Stop uploads a browser recording (mic permission required)
- [ ] Retry on failed voice processing succeeds
- [ ] Non-audio upload rejected with a clear message

## URL capture smoke (Milestone 3A+)

- [ ] Paste a public https URL → item appears with `item_type=url`
- [ ] Processing reaches `ready` with title + extracted text (or `failed` with error)
- [ ] Retry on failed URL succeeds when upstream recovers
- [ ] Analyze inbox includes ready URL items
- [ ] Private/local URLs (127.0.0.1, localhost) rejected before fetch

## Multi-user / auth smoke (Milestone 6+)

- [ ] Dev login with email A; create a note; appears in list
- [ ] Dev login with email B; note from A is not visible; delete of A’s id returns 404
- [ ] Sign out clears session cookie
- [ ] `/api/auth/google/start` returns 501 stub (no real OAuth)
- [ ] `/api/health` returns status + environment metadata
- [ ] Spoofing `X-User-Id` does not change ownership

## Attention / Calendar smoke (Milestone 5+)

- [ ] After analysis, Attention section shows Today / Upcoming / Overdue / Unscheduled
- [ ] Daily briefing summary and suggested focus appear
- [ ] Confirm date on a timed task; original phrase preserved; due_on normalized
- [ ] Snooze 1 day moves task into Snoozed bucket
- [ ] Preview calendar shows provider notes and requires explicit confirm
- [ ] Confirm (mock) records a local receipt; `/api/calendar/status` shows writes_enabled=false
- [ ] With `LIFE_INBOX_CALENDAR_PROVIDER=google`, confirm returns blocked (no OAuth/writes)

## Pagination regression (delete + Load more)

Automated coverage: `tests/test_pagination_state.py` (pure JS helper via Node).

Browser procedure (temp DB only):

1. Create notes “Pagination note 001” … “Pagination note 102” (102 notes).
2. Reload http://127.0.0.1:8000 — first 100 appear; “Load more” visible.
3. Delete “Pagination note 001”.
4. Click “Load more”.
5. Confirm notes 002–102 each appear exactly once (including 101); ID order.
6. Rapid double-click “Load more” on a >200-note inbox — no duplicates/skips;
   button disables while a list request is in flight.
7. Force a failed append (stop server, click Load more) — friendly error;
   already-displayed notes remain.
8. Failed delete (stop server, delete) — note stays visible; pagination unchanged.
9. Search / filters / Clear / create note / Back / Forward still reload from
   offset 0. Editing a note does not skip or duplicate pages.

## Error states

- [ ] Stop the server mid-request → friendly client error, text preserved in textarea
- [ ] Analyze with empty inbox → empty_inbox mode, no crash
- [ ] Failed “Load more” → friendly error; notes already on screen kept

## Do not

- Point `DATABASE_URL` at a real personal inbox database
- Commit API keys or generated `.db` files
