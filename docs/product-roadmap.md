# Product roadmap

Life Inbox evolves through cumulative milestones. Each milestone must remain
understandable, testable, and recoverable for a teaching program.

## Milestone 1 — Polished, trustworthy core

Reliable capture / list / edit / delete; durable tasks; stale analysis marking;
pagination; Alembic-owned schema; coherent UI; CI; smoke checklist.

## Milestone 2 — Intelligent organization / second brain

User-editable topics/spaces; AI suggestions (title, topic, priority, tasks,
dates, tags); preserve user corrections; duplicates/related items; multi-task
per source; organized dashboard.

## Milestone 3 — Rich capture

Shared ingestion lifecycle: capture → save original → process → extract →
understand → organize → ready/failed.

- **3A** URL / article capture (done)
- **3B** PDF / document upload (done)
- **3C** Images / screenshots (+ OCR adapter) (done)
- **3D** Voice notes (+ transcription adapter) (done)

## Milestone 4 — Retrieval + grounded “Ask My Inbox” (done)

Keyword + lightweight semantic retrieval; citations; insufficient-evidence
honesty; evaluation fixtures.

## Milestone 5 — Attention, reminders, safe actions (done)

Deadline views; in-app reminders; optional Google Calendar behind an adapter
with preview + explicit confirmation only.

## Milestone 6 — Deployable multi-user beta (done)

Auth, ownership scoping, Postgres for prod, storage abstraction, health,
deployment guide. Stop before paid infra / public deploy without approval.

## Execution priority (time-constrained)

M1 (fast) → M2 showcase → M3A → M3B → M4 → M3C images → M3D voice → M5 → M6.
