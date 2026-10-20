# Project review and improvement plan

Reviewed: October 20, 2026. Baseline: `fe996f7`.

## Overall assessment

The project has a useful, focused purpose: help someone choose a board game for a particular group size using BGG ratings, player-count polls, tracked collections, and online availability. Django, server-rendered templates, plain JavaScript, and a database-backed worker are appropriate for this size of application. Keep that architecture.

The next investment should be dependable imports and accurate filtering. Successful unit tests currently coexist with reproducible data-loss and job-lifecycle failures. Once those are fixed, simplify the catalog around the practical decision: how many players, which games are available, and why a recommendation is trustworthy.

This review changes documentation only. Failure probes used an isolated in-memory database and mocked external requests. The existing catalog was used only for reading and local browsing; no real refresh, prune, or platform scrape was launched.

## Architecture and strengths

| Area | Current design | Assessment |
| --- | --- | --- |
| Web application | One Django app; page and JSON views share query/serialization code | Appropriate, easy to deploy and understand |
| Storage | SQLite with WAL by default; configurable database URL | Suitable for a small installation; deployment persistence and concurrent writes need explicit planning |
| Import worker | `django-background-tasks`; separate `process_tasks` process | Reasonable lightweight queue; application job lifecycle needs hardening |
| Integrations | Dedicated BGG and Rally the Troops clients | Good separation; parsing, completeness checks, and retry policies need improvement |
| Catalog | Database filtering, scoring and pagination; prefetched relations | Good foundation; avoid unnecessary facet calculations on live updates |
| Frontend | Static CSS/JavaScript, accessible labels, responsive breakpoints | No frontend rebuild needed; mobile information hierarchy and state consistency need work |
| Quality | 53 Django tests, Ruff, CI checks, migration and static collection steps | Useful baseline; missing import-failure and browser interaction coverage |

Preserve bulk database operations, the serialization N+1 guard, account-free catalog browsing, and the existing owner-style platform shortcuts. Add clearer semantics around platform availability without removing those shortcuts.

## Verification performed

| Check | Result |
| --- | --- |
| `python manage.py test games --noinput` | 53 tests passed |
| `python -m ruff check .` | Passed |
| `python manage.py check` | Passed with explicit development settings |
| `python manage.py makemigrations games --check --dry-run` | No changes detected |
| `python manage.py collectstatic --dry-run --noinput` | Passed; 137 existing assets reported unmodified. This was a dry run, not a fresh production collection |
| `python manage.py check --deploy` | Passed with temporary production-style environment values; does not verify an actual deployed environment |
| Browser inspection | Dashboard and populated catalog; reproduced mechanic selection mismatch; inspected 390 × 844 viewport |
| Failure probes | Reproduced cancelled job execution, deletion before failed apply, missing-details data loss, orphaned queue task, invalid numeric filters, broken authentication redirect, and unbounded collection queue retries |

The default local environment could not run ordinary management checks until development settings were supplied: production mode correctly required a secret key. This is not a security defect, but the README sets development mode too late for its migration step.

External BGG/RTT behavior, production hosting, database restore, PostgreSQL operation, and authenticated browser refresh workflows were not verified. API failure cases below were simulated against the current code.

## Findings, in priority order

### R1 — High: database application can delete valid data before an import fails

Evidence: `games/tasks.py:314` defines `_sync_catalog`; pruning occurs at line 372 before synchronization. There is no transaction enclosing the apply sequence. With two existing games, a simulated `_sync_games` exception after pruning permanently removed the excluded game.

The streamed refresh also changes live records batch by batch (`games/tasks.py:1015`), and purges untracked collections before fetching or validating the source (`games/tasks.py:780`). A failure or cancellation can leave a mixed catalog. Platform ownership tags removed by `_sync_refresh_chunk` are only restored at successful completion.

Improvement: validate the fetched dataset before destructive work; make database application atomic and keep network calls outside transactions. Stage streamed results until completeness is established. Preserve the previous valid catalog on failure or cancellation. Treat intentional replacement by Top N as an explicit operation, because it currently deletes collection-only catalog games too.

### R2 — High: API errors and incomplete responses can be accepted as successful imports

Evidence: `games/services/bgg_client.py:232` and `:347` parse matching `item` elements without consistently validating the response envelope or checking requested IDs against returned IDs. Mocked HTTP 200 XML error responses were returned as empty collection/details dictionaries.

For Top N, feeding empty details into the current synchronization path erased an existing game's year, rank, and poll rows, then marked the job `done` (`games/tasks.py:119`, `:191`, `:688`). An invalid collection response can similarly be mistaken for a legitimate empty collection and remove ownership. The streamed path is more defensive about omitted detail IDs, but it still reports completion without an explicit completeness contract.

RTT has a related problem: individual request failures become skipped games (`games/services/rtt_client.py:163`), and `run_scrape_rtt` subsequently prunes against the partial result (`games/tasks.py:1153`).

Improvement: distinguish valid empty data, missing data, queued responses, application errors, and partial results. Reject unexpected envelopes. Reconcile requested and returned IDs. Preserve known data for unavailable records and disallow pruning from incomplete source results.

### R3 — High: cancelling and clearing jobs do not reliably control queued work

Evidence: `cancel_job` writes `cancelling` (`games/views.py:134`), but every task entry point immediately writes `running` before checking cancellation (`games/tasks.py:649`, `:713`, `:774`, `:1099`, `:1178`). A mocked Top N job starting in `cancelling` fetched data and ended `done`.

`clear_jobs` deletes all `FetchJob` rows (`games/views.py:144`) without removing the queue's `Task` rows. A probe left one queued task after its associated job was deleted. Task entry then looks up a missing job before entering its exception handler.

There is also no application-level catalog lock or heartbeat. One worker is serial by default, but multiple workers or expired queue locks can allow overlapping destructive jobs. Exceptions inside the main task bodies are swallowed after setting the visible job error, so the queue cannot automatically retry those failures.

Improvement: centralize allowed state transitions, atomically claim work, honor cancellation before any write or fetch, clear completed history only, and coordinate queue/application records. Add a catalog mutation lock, heartbeat, stale-job recovery, and explicit retry policy.

### R4 — High: collection fetching can occupy the worker indefinitely

Evidence: `games/services/bgg_client.py:217` increments its retry counter for HTTP 202 but never checks an upper bound in that branch. A mock returned 61 consecutive queued responses before the probe deliberately stopped it. Cancellation is not checked inside this loop. Other callbacks broadly swallow exceptions, which can swallow cancellation signals too.

Improvement: use an overall deadline and bounded attempts for collection queueing; retry transient errors with backoff; fail promptly for permanent authentication/input errors; make waits and retry loops cancellation-aware. Keep cancellation exceptions out of broad callback suppression.

### R5 — Medium: visible filter state and returned results disagree

Evidence: `static/js/games_list.js:61` appends repeated values for categories and owners, but uses `set` for mechanics. Browser reproduction: Dice Rolling and Hand Management were both checked, while the URL contained only `mechanics=Hand+Management` and results showed only that mechanic's count.

Additional code findings:

- Overlapping requests have no cancellation or sequence guard (`static/js/games_list.js:70`), allowing an older response to replace newer results.
- `sort=playable` is offered by the page but absent from the query ordering map; it falls back to overall score (`games/views.py:295`, `games/utils.py:171`).
- Non-JavaScript sort links append a second `?` and existing sort parameters (`templates/games_list.html:259`). The browser displayed links ending in `dir=desc?`.
- Reset defaults selects all types and 200 rows, while server defaults are base games and 50 rows (`static/js/games_list.js:185`, `:199`; `games/utils.py:93`; `games/views.py:160`).
- Live responses update rows and totals but leave facet counts/options unchanged.

Improvement: use one canonical filter representation for initial rendering, AJAX, URLs, reset, sort, pagination, and export. Preserve every repeated selection. Cancel or ignore superseded requests. Define facet counts explicitly as games or game/player-count rows.

### R6 — Medium: input validation and authentication routing have visible failures

Evidence: `/games/?min_avg_rating=abc` and `/games/rows/?max_weight=abc` raised `ValueError: Cannot use None as a query value` (`games/utils.py:119`). Validation also needs finite-number and range checks.

Anonymous `/refresh/` redirected to `/accounts/login/?next=/refresh/`, which has no route. The actual sign-in route is `/admin/login/`. The same default login destination applies to privileged job actions.

Import forms accept unchecked server-side values, including ranks URLs and potentially expensive catalog sizes. Ranks downloads buffer the entire response in memory (`games/services/bgg_client.py:133`). These inputs are superuser-only, so this is an administrative robustness concern rather than an anonymous exploit demonstrated by this review.

Improvement: introduce Django forms or equivalent shared validation; reject invalid ranges and non-finite numbers; configure the actual login URL; validate supported HTTPS ranks download locations/redirects and archive size limits. Preserve intentional “all ranked games” behavior through an explicit option with clear cost expectations.

### R7 — Medium: the export control does not export the current view

Evidence: the catalog's Export CSV link always points to bare `/export.csv` (`templates/base.html:24`). The export endpoint correctly accepts filters, but the visible link never supplies them. This was also observed after live filtering in the browser.

Both table and export have 25 columns, but their schemas differ: the table includes Families; the CSV substitutes Owned (`games/views.py:267`, `:327`). Column-count tests do not catch this semantic mismatch.

Improvement: add an explicit “Export filtered results” action using canonical current filters; label a separate full-catalog export if retained. Share a defined column schema and verify names and values, not merely width. Neutralize spreadsheet formula prefixes in imported text when producing CSV.

### R8 — Medium: job progress can stop updating or hide the cause of failure

Evidence: a non-OK polling response becomes `null` and returns without scheduling another poll (`static/js/job_detail.js:210`). Although JSON includes `error`, the script does not render it. The error panel is only rendered if the page initially loads with an error (`templates/job_detail.html:180`). Failures occurring while the page is open therefore require a manual reload for the explanation.

The fixed Top N/Collection/Details/Cleanup cards are also used for platform jobs, which do not populate those phases. Public viewers see Cancel controls even though the action requires a superuser.

Improvement: return job-specific phase metadata; update errors and finished times live; retry appropriate polling failures with a visible connection state; render controls according to permissions. Distinguish “worker unavailable” from ordinary pending work.

### R9 — Medium: the catalog is difficult to use on phones and obscures evidence quality

Browser evidence: at 390 × 844, the expanded filter card was about 1,851 CSS pixels tall and results started around document y=2,463. The introduction and count badge remained side by side, squeezing the text. The full 25-column results table requires extensive horizontal scanning even on desktop.

The primary result score is a 75% average-rating / 25% normalized poll-score blend. A recommendation based on one poll vote can receive the same poll score as one backed by hundreds of votes. Minimum voters currently filters overall game ratings, not the player-count poll sample. This is a product limitation, not proof that the formula is mathematically wrong.

BGG open-ended poll buckets such as `8+` are discarded (`games/services/bgg_client.py:450`), while the UI offers “8 or more.” That selection currently represents numeric counts of 8 and above only. Games without player-count rows cannot appear in this catalog query even when other filters say “All.”

Improvement: put player count, availability and results first; collapse advanced filters on phones; show a compact default result with optional detailed columns. Explain the formula and threshold, display poll sample size, and offer a poll-vote minimum. Clearly handle missing evidence and open-ended buckets. Treat any confidence-adjusted ranking as a separately validated scoring change.

### R10 — Medium: operations and maintainability need a small, practical foundation

README instructions describe thread-based jobs, scraping the ranked list, no API keys, and an atomic apply. Current code uses a separate queue worker, a user-supplied ranks ZIP, optional bearer-token configuration, and non-atomic writes. `CLAUDE.md` is closer to the implementation but also contains stale details.

CI is useful but does not exercise a production settings branch, JavaScript behavior, real worker restart, database restore, or PostgreSQL. Dependencies use broad version ranges without a reproducible resolved set. There is no documented persistent-disk arrangement, worker health signal, or restore procedure. These are gaps in repository evidence, not claims about the actual host's configuration.

`OwnedGame` also has no uniqueness constraint for `(collection, game)` (`games/models.py:128`), despite creation paths using `ignore_conflicts=True`. Concurrent or repeated writes should be protected at the database level.

Improvement: update setup and operations documentation, add focused integration/browser checks, introduce repeatable dependency resolution and update review, constrain ownership relationships, and document persistence, backup and recovery. Django recommends checking the real production configuration and establishing database backups: [deployment checklist](https://docs.djangoproject.com/en/5.2/howto/deployment/checklist/).

## Performance observations

Read-only profiling against the existing local snapshot found 10,093 games and 47,898 player-count rows:

| Request | Elapsed | Queries | SQL time |
| --- | --- | --- | --- |
| Initial `/games/` | 1.239 s | 12 | 0.501 s |
| Rows for four players | 0.239 s | 12 | 0.187 s |
| Rows matching Catan | 0.106 s | 12 | 0.064 s |

These are individual development-mode samples, not production benchmarks or percentile guarantees. The initial measurement includes cold-start effects.

The largest queries on the initial request were mechanic/category counts. `_compute_rows_context` recomputes facets for every AJAX update, yet `games_rows` does not return them. Global score min/max is also recomputed per request. First decide to return useful current facets or skip their unused calculation; then cache stable normalization data by successful catalog version. Benchmark before adding indexes or changing databases.

## Implementation roadmap

Effort is relative: S = small focused change; M = several connected changes; L = a larger change involving import lifecycle or storage. Estimates are planning guidance, not delivery commitments. Each phase should be a reviewable change with the relevant regression checks.

### Phase 1 — Protect the catalog and its ownership data (L)

- [ ] Add regression cases for failed apply after prune, incomplete detail batches, API error envelopes, partial RTT results, failed collection fetches, and ownership/tag preservation.
- [ ] Define source-result completeness and distinguish valid empty collections from failed retrievals.
- [ ] Stage streamed refresh payloads; validate them before touching the live catalog or purging ownership.
- [ ] Apply validated database updates and cleanup atomically, with no network I/O inside the transaction. Measure SQLite lock duration on a representative import before accepting the design.
- [ ] Make replacement scope explicit for Top N versus combined refresh; prevent unintended deletion of collection-only games.
- [ ] Add database uniqueness for `(collection, game)` after deduplicating existing rows safely.

Acceptance: simulated failure or cancellation leaves the previous valid catalog, polls and ownership intact; incomplete source responses cannot trigger pruning; successful reruns are idempotent; platform tags remain consistent. Imports at the current catalog size do not cause unacceptable web timeouts or lock failures.

### Phase 2 — Make job execution recoverable and understandable (M–L; depends on Phase 1)

- [ ] Centralize transitions and atomically claim pending work; honor cancellation before starting.
- [ ] Bound collection retries and total waiting time; check cancellation during waits and requests.
- [ ] Limit clear-history to terminal jobs; coordinate queue rows and tolerate missing/duplicate delivery safely.
- [ ] Add a catalog mutation lock, heartbeat timestamps, stale-job detection and a documented retry/resume policy.
- [ ] Keep enqueueing and job creation consistent, and classify transient versus permanent failures.
- [ ] Render job-specific phases, live error text, finished times and worker/polling health; hide unauthorized controls.

Acceptance: cancelled queued jobs perform no fetch or mutation; clearing history leaves active work intact; worker termination is detected; retrying a job does not duplicate or corrupt data; two workers cannot apply competing catalog replacements; terminal failures are visible without page reload.

### Phase 3 — Make filters, sorting and export agree (M; small pieces can proceed independently)

- [ ] Fix repeated mechanic serialization and add a browser regression reproducing the two-checkbox case.
- [ ] Normalize all filter inputs server-side, including finite numbers, bounds and reversed ranges.
- [ ] Ignore superseded AJAX responses and preserve the last valid results when loading fails.
- [ ] Fix playable sorting, add deterministic game/count tie-breakers, repair fallback links, and unify reset defaults.
- [ ] Configure working sign-in redirects for privileged pages/actions.
- [ ] Connect filtered export to current state and define a shared table/export column schema.
- [ ] Decide and implement consistent facet-count semantics, including live updates.

Acceptance: multiple selections survive live updates, reload, shared URLs and CSV export; malformed query values do not produce 500s; quick filter changes cannot display old responses; browser and CSV agree on rows and column meaning; page and reset defaults match; anonymous protected-route redirects reach a real login page.

### Phase 4 — Make choosing a game easier (M; depends on stable filter behavior)

- [ ] Move player count and availability above taxonomy filters; show active-filter chips and result count nearby.
- [ ] On phones, collapse advanced filters and stack the heading/count layout; show results within the first screen or one short scroll.
- [ ] Default to compact columns: game, players, score, rating, weight, poll votes, availability. Keep the full data table available.
- [ ] Add score explanations, poll-vote minimum, missing-data indicators, and separate labels for owned copies versus online availability while retaining platform filter shortcuts.
- [ ] Represent open-ended polls explicitly rather than silently dropping them; distinguish “no poll data” from “not recommended.”
- [ ] Improve keyboard focus, table-region labeling, loading/error announcements, and selected-filter visibility.
- [ ] Consider local saved presets and a shareable game-night view after these core improvements work. Keep browsing account-free.

Acceptance: at 390 × 844, a user can select a group size and inspect useful results without passing a full expanded filter wall; keyboard operation works; one-vote recommendations are clearly identified; scoring explanations match displayed and sorted values; the detailed view/export remain available.

### Phase 5 — Reduce unnecessary work and separate responsibilities (M)

- [ ] Measure representative default, owner, category and mechanic queries before and after changes.
- [ ] Split row retrieval from expensive facet calculation; return current facets when needed rather than computing and discarding them.
- [ ] Cache global normalization/facet data with explicit successful-import invalidation; preserve filter-independent score behavior.
- [ ] Separate validation, catalog queries, scoring definitions, import application and job lifecycle into focused modules. Preserve the bulk sync approach and shared helpers across both import paths.
- [ ] Consolidate column metadata so templates, sorting, serialization and exports cannot drift independently.

Acceptance: query cost stays bounded as page size grows; the no-N+1 test remains green; representative live updates improve measurably without stale facets or scores; sorted values match displayed values; only measured bottlenecks drive further database/index changes.

### Phase 6 — Make setup, deployment and recovery repeatable (S–M)

- [ ] Rewrite README setup with environment configuration before migration, superuser creation, ranks ZIP acquisition, token configuration, and separate web/worker startup.
- [ ] Make the Windows launcher stop on failed native commands using explicit exit-code checks; document loopback versus trusted-LAN development.
- [ ] Add a production-settings CI smoke check, focused browser scenarios, and ownership/import integrity regressions. Run PostgreSQL integration checks if PostgreSQL support is to be advertised.
- [ ] Record reproducible dependency versions and establish reviewed updates/security checks.
- [ ] Document persistent storage for the actual hosting arrangement. If web and worker are separate services, confirm they share one authoritative database.
- [ ] Add a small health/readiness surface and worker heartbeat visibility without exposing credentials or internal errors.
- [ ] Document and test database backup/restore, migrations, static collection, and worker startup/restart.
- [ ] Show last successful source fetch times separately from job attempts and general record updates.

Acceptance: a fresh Windows setup can browse and complete a mocked import from the documented steps; CI exercises development and production configuration; a backup is restored into a disposable database and checked; worker unavailability is visible; the deployment's persistence and database sharing are verified rather than assumed.

## Recommended first change

Start with Phase 1's failing regression cases and the safe application boundary. The review has already demonstrated that a failed Top N apply can delete existing data. Fixing that, then rejecting incomplete API responses, delivers more immediate value than adding features or changing frameworks.

Keep a full application rewrite, a new JavaScript framework, Redis/Celery, and a database migration out of the initial scope. Reassess infrastructure only if measured contention, import duration or actual hosting requirements justify it.
