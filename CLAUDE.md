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
- Frontend: server-rendered Django templates with **inline CSS/JS** (no build step, no JS framework)

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
│   ├── models.py           # Game, Category, Family, Mechanic, BGGUser,
│   │                       #   PlayerCountRecommendation, Collection, OwnedGame, FetchJob
│   ├── views.py            # page + AJAX views; orchestrates jobs and the games table
│   ├── utils.py            # GameFilter (query building) + serialize_game_row (scoring)
│   ├── tasks.py            # @background jobs + DB sync helpers (the heavy lifting)
│   ├── services/
│   │   └── bgg_client.py   # BGGClient: all HTTP to BGG, throttling, retries, XML/CSV parsing
│   ├── urls.py             # app routes
│   ├── admin.py            # Django admin registrations
│   ├── apps.py             # enables SQLite WAL/synchronous pragmas on connect
│   └── migrations/         # 0001–0005
├── templates/              # project-level templates (DIRS = BASE_DIR/templates)
│   ├── base.html           # layout, shared inline CSS, nav
│   ├── home.html           # landing page
│   ├── refresh.html        # job-launch + user-management UI (superuser-gated)
│   ├── job_detail.html     # live phase-progress UI (polls JSON)
│   ├── games_list.html     # the main filterable/sortable table
│   ├── game_detail.html    # single game + player-count breakdown
│   └── partials/games_rows.html  # AJAX-rendered table rows
├── check_mechanics.py      # one-off debug script (NOT a Django mgmt command)
├── refresh_mechanics.py    # one-off debug script
└── verify_mechanics.py     # one-off debug script
```

> Note: `settings.py` lists `STATICFILES_DIRS = [BASE_DIR / 'static']`, but there is currently no
> `static/` directory. All styling/JS is inline in templates. Don't assume a static-asset pipeline exists.

## Data Model (games/models.py)

- **`Game`** — core record keyed by `bgg_id` (a `CharField`, unique). Holds title, `type`
  (`Base Game` | `Expansion`), `year` (stored as a string!), `avg_rating`, `num_voters`, `weight`,
  `weight_votes`, `bgg_rank`, and denormalized ownership: `owned` (bool) + `owned_by` (JSON list of
  usernames). M2M to `Category`, `Family`, `Mechanic`.
- **`Category` / `Family` / `Mechanic`** — simple `name`-unique vocab tables. "Families" are the eight
  pinned BGG top-level buckets (Strategy, Thematic, Abstract, Children's Game, Customizable, Family,
  Party Game, Wargame); categories and mechanics come straight from BGG links.
- **`PlayerCountRecommendation`** — one row per `(game, count)`; stores poll vote splits
  (best/rec/notrec pct + votes, total votes). This is the table the games list iterates over.
- **`BGGUser`** — a tracked BGG username (drives which collections a refresh pulls).
- **`Collection` / `OwnedGame`** — a user's owned games (M2M through table). Ownership is also
  denormalized onto `Game.owned`/`Game.owned_by` for fast filtering/display.
- **`FetchJob`** — tracks a background job: `kind` (`top_n`|`collection`|`refresh`), `params` (JSON,
  includes per-phase progress under `params["phases"]`), `status`, `progress`/`total`, `error`.

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

Cancellation: views set `status='cancelling'`; tasks poll via `check_cancel()` and raise `JobCancelled`.

The DB-sync helpers (`_sync_games`, `_sync_player_counts`, `_sync_ownership`, `_sync_catalog`,
`_sync_refresh_chunk`) are written for **bulk efficiency** — `in_bulk`, `bulk_create`, `bulk_update`,
`ignore_conflicts`, vocab pre-collection (`_collect_vocab`) to avoid N+1 queries. Preserve this style.

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
  `pc_score` is that value min-max normalized to 0–10 across the result set; `score_factor =
  (avg_rating*3 + pc_score) / 4`. **If you change a formula, change it in BOTH `utils.py`'s
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
python manage.py shell
```

### Environment variables (read in settings.py / bgg_client.py)
- `DATABASE_URL` — non-SQLite DB (via `dj-database-url`); defaults to local SQLite.
- `DJANGO_SECRET_KEY`, `DJANGO_DEBUG` (default `True`), `DJANGO_LOG_LEVEL` (default `INFO`).
- `DJANGO_CSRF_TRUSTED_ORIGINS` (comma-separated), `RENDER_EXTERNAL_HOSTNAME` (auto host/CSRF on Render).
- `BGG_API_TOKEN`, `BGG_THROTTLE_SEC`, `BGG_DETAILS_THROTTLE_SEC`.

`.env` is supported (loaded via `python-dotenv`) and git-ignored.

## Conventions & Gotchas

- **No test suite exists.** There are no `tests.py`/`pytest` files; CI is not configured. The
  `*_mechanics.py` scripts at the repo root are ad-hoc debug scripts run as `python check_mechanics.py`
  (they call `django.setup()` themselves) — they are not management commands and not maintained tests.
  If you add tests, prefer Django's test runner under `games/tests/`.
- **`Game.bgg_id` is a string, and `Game.year` is a string** — code defensively casts IDs to `str()`
  throughout the sync helpers and casts year to int in SQL (`year_int`) for filtering/sorting.
- **A refresh prunes.** `run_fetch_top_n` and `run_refresh` delete games not in the fetched set. The
  ranks ZIP link **expires** — failures there usually mean a stale data-dump URL, not a code bug.
- **Always run the worker.** Jobs created in the DB stay `pending` until `process_tasks` is running.
- **Keep the four parallel write paths consistent.** `_sync_catalog` (used by top_n/collection) and
  `_sync_refresh_chunk` (used by refresh) implement similar logic; a fix in one usually belongs in both.
- **SQLite is in WAL mode** (set in `games/apps.py`) for better job/web concurrency.
- **Permissions:** `/refresh/`, `cancel_job`, and user add/delete are `@user_passes_test(is_superuser)`.
- Styling and JS are inline in templates; there's no asset build, no Node, no CSS framework.
- License: **CC BY-NC 4.0** (see `LICENSE`).

## Git / Contribution Notes

- Make changes on the branch you were assigned; commit with clear messages; push with
  `git push -u origin <branch>`. Do **not** open a PR unless explicitly asked.
- `db.sqlite3`, `.venv/`, `.env*`, logs, and build artifacts are git-ignored — never commit them.
</content>
</invoke>
