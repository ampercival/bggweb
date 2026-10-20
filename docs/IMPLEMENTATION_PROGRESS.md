# Improvement implementation

The baseline review and acceptance criteria are in [PROJECT_REVIEW_AND_PLAN.md](PROJECT_REVIEW_AND_PLAN.md).

## October 20, 2026

- Review and plan committed and pushed as `28cd266`.
- Import safety: all detail batches are staged in memory and validated before live catalog writes. Database application, collection cleanup and platform tagging share a transaction; no network calls occur inside it. Failed or cancelled application rolls back. Top N preserves collection-only games. Invalid XML envelopes, omitted detail IDs and partial RTT fetches fail safely. Ownership links gain a deduplicating uniqueness migration. Relation synchronization now uses bulk through-table writes.
- Validation for import safety: 63 Django tests pass (10 added), Ruff passes, and migration drift check passes. Failure, cancellation, incomplete payload, successful refresh, and ownership/tag regression cases use disposable test databases and mocked requests.
- A file-backed SQLite benchmark of 10,000 games / 40,000 polls initially exposed a 48.4-second unchanged reapply. Skipping unchanged rows and using prepared SQLite updates removes that overhead. Reproduce with `python scripts/benchmark_import.py`; measured final timings are recorded with the phase commit.

No real catalog refreshes or external scrapes are run as part of automated validation.
- Final disposable SQLite benchmark: initial 2.807 s, unchanged 2.114 s, all ratings/poll votes changed 3.032 s. These are local samples; real host concurrency still needs deployment acceptance.

## Job execution

- Added atomic job claiming and a shared catalog writer lease. Cancelled queued jobs and duplicate/missing deliveries do not execute. Only terminal history can be cleared. Job creation and enqueueing share a transaction.
- Added ten-second heartbeats, explicit stale-job recovery (`recover_jobs`), new-job retry actions, and bounded/cancellable BGG queue waits. Permanent authentication/input errors fail without repeated sleeps.
- Job pages render source-specific phases, show new errors without reload, recover from polling failures, warn about missing heartbeats, and hide privileged controls from public viewers.
- Validation: 72 Django tests pass, Ruff passes, migration drift check passes. Nine new checks cover queued cancellation, duplicates, lease contention/release, stale recovery, active-history preservation, public permissions and bounded waits. Actual external-service and deployment behavior remains a separate acceptance step.

## Catalog consistency

- Added canonical bounded filters for pages, AJAX and CSV; invalid/non-finite values recover safely. Fixed repeated mechanics, request supersession, playable sorting, stable tie-breakers, reset defaults, fallback sort links and actual sign-in routing.
- Added filtered export, a shared 25-column table/export contract (including Families), spreadsheet-formula text escaping, and live facet HTML. Facet counts describe game/player-count rows matching the basic filters, before taxonomy selections.
- Import forms validate usernames, counts and HTTPS ranks hosts. Downloads validate redirects and cap compressed/uncompressed archive sizes.
- Validation: 80 Django tests pass, Ruff and JavaScript syntax checks pass, full static collection passes. Browser at a fresh local origin retained Dice Rolling + Hand Management in selected controls, canonical URL and export URL and returned 12,207 combined rows. Migrations 0009/0010 applied locally. The browser had cached the pre-change assets at the earlier origin; fresh-origin verification confirmed the shipped source.
