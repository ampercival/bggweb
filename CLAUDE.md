# CLAUDE.md

Guidance for AI assistants (and humans) working in this repository.

## Project Overview

**bggweb** is a single-app Django website that fetches [BoardGameGeek](https://boardgamegeek.com) (BGG)
data — a ranked Top-N list, optional user-owned collections, and per-game details/player-count polls —
stores it locally, and presents a fast, filterable, sortable table with CSV export.

The headline feature is a **"player count optimizer"**: each game/player-count combination gets a
computed score so you can find which games play best at a given table size. Data is gathered by
**background jobs** that report multi-phase live progress (Top N → Collection → Details → Cleanup).

- Language/framework: **Python 3.11+ / Django ≥ 5.2**
- Datastore: **SQLite** by default (`db.sqlite3`), Postgres-capable via `DATABASE_URL`
- Background work: **`django-background-tasks`** (a `process_tasks` worker — no Celery/Redis)
- External data source: BGG XML API2 (`thing`, `collection`) + the BGG ranks **CSV data-dump ZIP**
- Frontend: server-rendered Django templates; CSS/JS live in **static files** under `static/css/` and `static/js/` (no build step, no JS framework), served by WhiteNoise
- Static files served in production by **WhiteNoise** (`collectstatic` → compressed/hashed)
- Tests: Django test runner under `games/tests/`; CI runs them via `.github/workflows/ci.yml`

## Repository Layout

```
bggweb/                     # repo root
├── manage.py               # Django entrypoint
├── requirements.txt        # pinned-ish deps (Django, gunicorn, dj-database-url, etc.)
├── Procfile                # production: gunicorn `web` + `process_tasks` `worker`
├── Procfile.dev            # dev: runserver `web` + `process_tasks` `worker` (run via honcho)
├── start_dev.ps1           # Windows helper: venv + install + migrate + honcho start
├── bggweb/                 # Django project (settings/urls/wsgi/asgi)
│   ├── settings.py         # env-driven config; SQLite default; logging; WAL pragmas
│   └── urls.py             # admin/ + includes games.urls at root
├── games/                  # the ONLY app — all domain logic lives here
│   ├── models.py           # Game, Category, Family, Mechanic, BGGUser, RTTGame,
│   │                       #   PlayerCountRecommendation, Collection, OwnedGame, FetchJob
│   ├── views.py            # page + AJAX views; orchestrates jobs and the games table
│   ├── utils.py            # GameFilter (query building) + serialize_game_row (scoring)
│   ├── tasks.py            # @background jobs + DB sync helpers (the heavy lifting)
│   ├── services/
│   │   ├── bgg_client.py   # BGGClient: all HTTP to BGG, throttling, retries, XML/CSV parsing
│   │   └── rtt_client.py   # RTTClient: scrapes rally-the-troops.com library → BGG ids
│   ├── urls.py             # app routes
│   ├── admin.py            # Django admin registrations
│   ├── apps.py             # enables SQLite WAL/synchronous pragmas on connect
│   ├── migrations/         # 0001–0007
│   └── tests/              # test package (test_sync, test_scoring, test_views, test_bgg_client,
│                           #   test_rtt_client, test_rtt_sync)
├── templates/              # project-level templates (DIRS = BASE_DIR/templates)
│   ├── base.html           # layout, shared inline CSS, nav, messages, loads static/js/site.js
│   ├── home.html           # landing page
│   ├── refresh.html        # job-launch + user-management UI (superuser-gated)
│   ├── job_detail.html     # live phase-progress UI (polls JSON)
│   ├── games_list.html     # the main filterable/sortable table
│   ├── game_detail.html    # single game + player-count breakdown
│   └── partials/games_rows.html  # AJAX-rendered table rows
├── static/                 # source static assets (collected into staticfiles/)
│   ├── css/                # site.css (global) + per-page: games_list, home, refresh, job_detail, game_detail
│   └── js/                 # site.js (shared), games_list.js, job_detail.js
└── .github/workflows/ci.yml  # runs check + migration check + tests
```

## Data Model (games/models.py)

- **`Game`** — core record keyed by `bgg_id` (a `CharField`, unique). Holds title, `type`
  (`Base Game` | `Expansion`), `year` (nullable `IntegerField`), `avg_rating`, `num_voters`, `weight`,
  `weight_votes`, `bgg_rank`, and denormalized ownership: `owned` (bool) + `owned_by` (JSON list of
  usernames). M2M to `Category`, `Family`, `Mechanic`. Frequently filtered/sorted columns
  (`type`, `year`, `avg_rating`, `weight`, `bgg_rank`, `owned`) are indexed.
- **`Category` / `Family` / `Mechanic`** — simple `name`-unique vocab tables. "Families" are the eight
  pinned BGG top-level buckets (Strategy, Thematic, Abstract, Children's Game, Customizable, Family,
  Party Game, Wargame); categories and mechanics come straight from BGG links.
- **`PlayerCountRecommendation`** — one row per `(game, count)`; stores poll vote splits
  (best/rec/notrec pct + votes, total votes). This is the table the games list iterates over.
- **`BGGUser`** — a tracked BGG username (drives which collections a refresh pulls).
- **`Collection` / `OwnedGame`** — a user's owned games (M2M through table). Ownership is also
  denormalized onto `Game.owned`/`Game.owned_by` for fast filtering/display. The **"Rally the Troops"
  availability tag reuses this**: it is a pseudo-`Collection` named `"Rally the Troops"` (see
  `RTT_OWNER_LABEL` in `tasks.py`) so it shows up in the owner filter cloud / Owners column exactly
  like a tracked BGG owner — no dedicated games-table column.
- **`RTTGame`** — source of truth for Rally the Troops availability (`bgg_id`, `slug`, `title`),
  populated by scraping [rally-the-troops.com](https://www.rally-the-troops.com/games/library). Kept
  separate from the catalog so a scraped game is remembered even when it is not currently in the
  catalog, and tagged if/when a later refresh pulls it in. `tasks.sync_rtt_collection()` projects
  `RTTGame ∩ catalog` onto the "Rally the Troops" collection's `OwnedGame` rows.
- **`FetchJob`** — tracks a background job: `kind` (`top_n`|`collection`|`refresh`|`rtt`), `params`
  (JSON, includes per-phase progress under `params["phases"]`), `status`, `progress`/`total`, `error`.

## Key Workflows

### Background jobs (games/tasks.py)
Three `@background(schedule=0)` task functions, each kicked off from `views.refresh`:

1. **`run_fetch_top_n(job_id, n, ranks_zip_url)`** — downloads the ranks ZIP, fetches details, then
   `_sync_catalog(..., prune=True)` (replaces the whole catalog).
2. **`run_fetch_collection(job_id, username)`** — pulls one user's owned collection + details, syncs
   without pruning.
3. **`run_refresh(job_id, n, usernames, batch_size, ranks_zip_url)`** — the main flow: Top N → each
   user's collection → streamed batched details (`_sync_refresh_chunk` per chunk) → prune untracked.
   Maintains the four-phase progress object (`top_n`, `collection`, `details`, `cleanup`) in
   `job.params["phases"]`, throttling DB writes (`save_throttled`, ~0.5s).

A fourth job, **`run_scrape_rtt(job_id)`** (`kind='rtt'`), scrapes the Rally the Troops library via
`services/rtt_client.RTTClient` (the only place that talks to rally-the-troops.com; throttled, polite
User-Agent, `RTT_THROTTLE_SEC` env). It upserts/prunes `RTTGame` rows, then calls
`sync_rtt_collection()`. That reconcile helper is **also called at the end of `run_refresh`,
`run_fetch_top_n`, and `run_fetch_collection`** so the tag survives the pruning refreshes, and
`_purge_untracked_collections` protects the `"Rally the Troops"` collection from deletion. Launched
from the "Scrape Rally the Troops" button on `/refresh/` (POST `action=rtt`).

Cancellation: views set `status='cancelling'`; tasks poll via `check_cancel()` and raise `JobCancelled`.

The DB-sync helpers (`_sync_games`, `_sync_player_counts`, `_sync_ownership`, `_sync_catalog`,
`_sync_refresh_chunk`) are written for **bulk efficiency** — `in_bulk`, `bulk_create`, `bulk_update`,
`ignore_conflicts`, vocab pre-collection (`_collect_vocab`) to avoid N+1 queries. Preserve this style.
Shared logic is factored into helpers reused by both sync paths: `_apply_relations` (M2M vocab),
`_sync_player_counts` (poll rows), and `recompute_owned_flags` in `utils.py` (the single source of
truth for the denormalized `Game.owned`/`owned_by` fields).

### BGG HTTP client (games/services/bgg_client.py)
`BGGClient` is the only place that talks to BGG. It handles:
- Throttling (`BGG_THROTTLE_SEC`, `BGG_DETAILS_THROTTLE_SEC` env, defaults 1.5s / max(1.5, 2.5)s).
- Retries with backoff, **429** rate-limit handling (honors `Retry-After`), **202** collection
  queueing, and BGG's "cannot load more than" batch-size pushback.
- `fetch_top_games_ranks` parses the **ranks CSV inside the ZIP** (requires a fresh data-dump link).
- `fetch_owned_collection` uses XML API2 `collection` for boardgame + boardgameexpansion subtypes.
- `_iter_details_batches` / `stream_details_batches` parse XML API2 `thing` (year, weight, rank,
  categories, mechanics, family ranks, and the `suggested_numplayers` poll → percentages).
- Optional `BGG_API_TOKEN` → `Authorization: Bearer` header.

### Games table, filtering & scoring (games/utils.py + views.py)
- **`GameFilter`** builds the queryset over `PlayerCountRecommendation` (select/prefetch related),
  applies all GET-param filters (search, owners, type, playable, player_count, year/rating/weight
  sliders, voters, categories/families, mechanics), and computes scores via DB annotations.
- **Scoring**: `pc_score_unadj = best%*3 + rec%*2 − notrec%*2`; "Playable" means `pc_score_unadj ≥ 150`.
  `pc_score` is that value min-max normalized to 0–10 against the **min/max across all
  player-count rows** (a stable, filter-independent scale computed from `qs_for_norm` before
  filters are applied — see `GameFilter.get_queryset`); `score_factor = (avg_rating*3 + pc_score) / 4`. **If you change a formula, change it in BOTH `utils.py`'s
  annotations AND `serialize_game_row` so DB-sort order matches displayed values.**
- **`serialize_game_row`** turns a record into the dict the templates/CSV consume.
- `views._compute_rows_context` is the shared source for `games_list` (full page), `games_rows`
  (AJAX rows JSON), and `export_csv` — keep their columns in sync.

### Request flow / URLs (games/urls.py)
`/` home · `/refresh/` launch+manage (superuser) · `/jobs/<id>/` detail (JSON when `XMLHttpRequest`)
· `/jobs/<id>/cancel/` · `/jobs/clear/` · `/games/` table · `/games/rows/` AJAX rows · `/games/<bgg_id>/`
detail · `/export.csv`.

## Development

Setup and run (macOS/Linux):
```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python manage.py migrate
honcho start -f Procfile.dev        # runs BOTH web + the background worker
```
Windows: `./start_dev.ps1` does venv + install + migrate + `honcho start -f Procfile.dev`.

Run web and worker separately if you prefer:
```bash
python manage.py runserver
python manage.py process_tasks      # REQUIRED — background jobs do nothing without this
```

Common commands:
```bash
python manage.py makemigrations games
python manage.py migrate
python manage.py createsuperuser    # needed to access /refresh/ and admin
python manage.py collectstatic      # required before/at deploy (WhiteNoise serves these)
python manage.py shell
python manage.py test games          # run the test suite
```

### Linting & dev tooling
Dev/CI tooling lives in `requirements-dev.txt` (`pip install -r requirements-dev.txt`). Lint with
**ruff** (`ruff check .`); config is in `pyproject.toml` (conservative `E4/E7/E9/F` set, migrations
excluded). CI runs `ruff check .` before the Django checks and tests. Copy `.env.example` → `.env`
for a documented starting point on environment variables.

> **Local dev:** `DJANGO_DEBUG` now defaults to **`False`** (a safe production posture). Set
> `DJANGO_DEBUG=True` in your shell or `.env` for development (better error pages, permissive
> `ALLOWED_HOSTS`). With `DEBUG=False`, set `DJANGO_ALLOWED_HOSTS` and `DJANGO_SECRET_KEY`.

### Environment variables (read in settings.py / bgg_client.py)
- `DATABASE_URL` — non-SQLite DB (via `dj-database-url`); defaults to local SQLite.
- `DJANGO_SECRET_KEY`, `DJANGO_DEBUG` (default `False`), `DJANGO_LOG_LEVEL` (default `INFO`).
- `DJANGO_ALLOWED_HOSTS` (comma-separated; defaults to localhost in DEBUG, empty otherwise).
- `DJANGO_CSRF_TRUSTED_ORIGINS` (comma-separated), `RENDER_EXTERNAL_HOSTNAME` (auto host/CSRF on Render).
- Security toggles applied when `DEBUG=False` (all sensible defaults): `DJANGO_SECURE_SSL_REDIRECT`,
  `DJANGO_SESSION_COOKIE_SECURE`, `DJANGO_CSRF_COOKIE_SECURE`, `DJANGO_SECURE_HSTS_SECONDS`.
- `BGG_API_TOKEN`, `BGG_THROTTLE_SEC`, `BGG_DETAILS_THROTTLE_SEC`.

`.env` is supported (loaded via `python-dotenv`) and git-ignored.

### Tests
Tests live in `games/tests/` and cover the sync/upsert helpers (`test_sync.py`), scoring +
filtering + the N+1 guard (`test_scoring.py`), the views incl. `clear_jobs` auth and full CSV
export (`test_views.py`), and the BGG client parsing of the ranks ZIP / `thing` XML / collection
XML (`test_bgg_client.py`). View tests use `@override_settings(SECURE_SSL_REDIRECT=False)` because
the suite runs with `DEBUG=False`. Run with `python manage.py test games`.

## Conventions & Gotchas

- **Tests live in `games/tests/`** and run via `python manage.py test games` (also in CI). Add new
  tests there; keep the N+1 guard in `test_scoring.py` green.
- **`Game.bgg_id` is a string** — code defensively casts IDs to `str()` throughout the sync helpers.
  `Game.year` is now a nullable `IntegerField` (BGG year strings are converted via `_to_int` on
  ingest; migration `0006` backfilled existing data and dropped non-numeric values to `NULL`).
- **A refresh prunes.** `run_fetch_top_n` and `run_refresh` delete games not in the fetched set. The
  ranks ZIP link **expires** — failures there usually mean a stale data-dump URL, not a code bug.
- **Always run the worker.** Jobs created in the DB stay `pending` until `process_tasks` is running.
- **Two sync paths still exist:** `_sync_catalog` (top_n/collection) and `_sync_refresh_chunk`
  (refresh). They now share `_apply_relations` / `_sync_player_counts`, but the orchestration differs;
  a behavioral fix may still belong in both — `test_sync.py` exercises each.
- **SQLite is in WAL mode** (set in `games/apps.py`) for better job/web concurrency.
- **Permissions:** `/refresh/`, `cancel_job`, `clear_jobs`, and user add/delete are all
  `@user_passes_test(is_superuser)`.
- **CSV export** (`export_csv`) streams the **full filtered result set** (not the current page) via
  `StreamingHttpResponse`; its columns must stay in sync with `games_list`/`games_rows` (25 columns).
- Page styling/JS lives in `static/css/` and `static/js/` (a global `site.css`/`site.js` loaded by
  `base.html`, plus per-page files linked from each template's `extra_head`/`extra_scripts`). Templated
  values a script needs (e.g. the games-list rows endpoint, default sort/dir, year bounds) are passed
  via `data-*` attributes, since static JS can't use `{% templatetag openblock %} ... {% templatetag closeblock %}` tags. There's no asset build, no Node,
  no CSS framework — WhiteNoise + `collectstatic` handle static files.
- License: **CC BY-NC 4.0** (see `LICENSE`).

## Git / Contribution Notes

- Make changes on the branch you were assigned; commit with clear messages; push with
  `git push -u origin <branch>`. Do **not** open a PR unless explicitly asked.
- `db.sqlite3`, `.venv/`, `.env*`, logs, and build artifacts are git-ignored — never commit them.
</content>
</invoke>
