# BGG Player Count Optimizer

A Django app for choosing board games by group size, BGG ratings, player-count poll evidence, tracked collections, and Board Game Arena / Rally the Troops availability. Catalog browsing does not require an account; importing and administration require a superuser.

## Features

- Ranked games from a BGG ranks CSV ZIP, plus tracked users' owned collections.
- Validated, staged imports: remote failures preserve the existing catalog; database application rolls back on failure.
- A separate database-backed worker with progress, cancellation, retry, heartbeat and stale-job recovery.
- Shareable filters, live sorting/pagination and CSV export of the full filtered result.
- BGG play-time ranges in minutes, minimum/maximum duration filters, sorting and export.
- A compact mobile catalog with optional detailed columns, poll-vote thresholds and score explanations.
- Exact and open-ended player-count polls; a separate browse view for games without polls.
- SQLite with WAL by default; a PostgreSQL integration suite in CI.

## Local setup (Python 3.11 or 3.12)

Windows users can run `./start_dev.ps1`. It installs locked runtime dependencies, enables development settings, migrates and starts both web and worker on port 8471. It also enables access from the trusted local network; use the loopback-only commands below when LAN access is unnecessary.

The launcher replaces leftover web/worker processes belonging to this checkout before starting, and cleans up its children on exit. It refuses to interrupt active/queued imports or stop an unrelated application using the port. Only one launcher may run for this checkout: press Ctrl+C in its window before starting it again. This prevents an older server from continuing to serve outdated pages after an update.

For manual PowerShell setup, configure development mode **before migrations**:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements-dev-lock.txt
$env:DJANGO_DEBUG = 'True'
python manage.py migrate
python manage.py createsuperuser
python manage.py runserver 127.0.0.1:8471
```

In a second activated terminal, with the same environment settings:

```powershell
python manage.py run_worker
```

On macOS/Linux activate with `source .venv/bin/activate` and use `export DJANGO_DEBUG=True`. `honcho start -f Procfile.dev` runs web and worker together (the development Procfile binds to the local network).

Copy `.env.example` to `.env` for persistent local configuration. `.env` is ignored. Production mode is the default and requires a real `DJANGO_SECRET_KEY` and allowed hosts. Sign in through `/admin/login/`, then open `/refresh/`.

## Importing data

1. Configure `BGG_API_TOKEN` for authenticated BGG API access. A valid token is needed for endpoints that reject anonymous requests.
2. Get a fresh "Click to Download" ranks ZIP link from [BGG's data dump page](https://boardgamegeek.com/data_dumps/bg_ranks). Links expire; a retry may need a new link.
3. Add tracked BGG usernames and start a full refresh. Zero for Top N explicitly means all ranked CSV entries.
4. Follow the job page. All details are fetched and validated before applying data. Partial/error responses cannot prune the catalog.

Top N refreshes preserve collection-only games. Full refreshes reconcile tracked collections and prune untracked data only after successful retrieval. Platform availability continues to appear in the availability filter. Poll buckets previously discarded by older versions appear after the next successful import.

Play times are fetched with game details during imports. Existing rows show Unknown until their next successful import. Time filters require the listed range to fit within your bounds (e.g. maximum 60 minutes excludes a 45–90 minute game). A single listed duration is used when a range bound is absent; unknown durations do not match an active time bound. Zero/malformed source values are unknown. Play-time sorting uses the maximum duration, with unknown values last. These estimates are independent of player-count polls and do not measure actual sessions.

The in-memory staging and final atomic apply are intended for this small catalog. Benchmark substantially larger installations with `python scripts/benchmark_import.py` before increasing import scope.

## Checks

```powershell
python manage.py test games
python -m ruff check .
python manage.py makemigrations games --check --dry-run
python manage.py collectstatic --noinput
```

Optional browser acceptance uses disposable test data:

```powershell
python -m pip install -r requirements-browser.txt
python -m playwright install chromium
python manage.py test browser_tests
```

CI runs the backend suite on Python 3.11/3.12 and PostgreSQL, production settings/static checks, real browser workflows and a dependency advisory audit. Browser screenshots are retained as CI artifacts.

Use `requirements-lock.txt` for runtime installs and `requirements-dev-lock.txt` for development/CI. `requirements.txt` declares supported dependency ranges. When updating dependencies, resolve in a clean environment, regenerate the lock, run checks and the audit, then review the changes. Do not freeze unrelated packages from a shared environment.

## Operations

- `/health/`: web/database readiness; does not require an idle worker.
- `/health/worker/`: 200 when a worker heartbeat is recent, otherwise 503.
- `python manage.py recover_jobs`: fail jobs without a heartbeat for 15 minutes and release their lease. `run_worker` runs recovery at startup.
- `python manage.py backup_catalog backups/catalog-YYYY-MM-DD.sqlite3`: creates a new verified SQLite backup; refuses to overwrite existing files.
- See [operations and restore instructions](docs/OPERATIONS.md), [review and plan](docs/PROJECT_REVIEW_AND_PLAN.md), and [implementation evidence](docs/IMPLEMENTATION_PROGRESS.md).

Keep web and worker on the same authoritative database. Verify persistent storage, backups and HTTPS in the actual deployment; a Procfile alone does not configure or prove those properties.

## License

[CC BY-NC 4.0](LICENSE): attribution required; noncommercial use only.
