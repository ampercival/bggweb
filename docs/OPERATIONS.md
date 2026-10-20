# Setup, deployment and recovery

## Configuration and deployment

Use Python 3.11 or 3.12 and install `requirements-lock.txt`. For production, configure a unique secret key, `DJANGO_DEBUG=False`, exact allowed hosts, CSRF trusted origins and HTTPS/proxy handling appropriate to the actual host. Default secure-cookie/HTTPS settings are enabled outside development.

Run, in order:

```text
python manage.py check --deploy --fail-level WARNING
python manage.py migrate --noinput
python manage.py collectstatic --noinput
```

Start the `web` and `worker` commands in `Procfile`. On a host that does not consume Procfiles, configure both commands explicitly. The worker command is now `python manage.py run_worker`, which wraps the existing queue runner with idle heartbeats and startup recovery. The old `process_tasks` command still executes jobs but does not report idle worker health.

If SQLite is used, web and worker must share the same database file on a persistent local volume with supported file locking. Separate service disks are not a shared database. For separate machines/services, use an authoritative PostgreSQL database via `DATABASE_URL`. Do not put a live WAL SQLite database on an unverified network filesystem.

Set `BGG_API_TOKEN`, source throttles and, only when necessary, `BGG_RANKS_ALLOWED_HOSTS`. The latter is an exact trusted-host list: HTTPS download destinations and every redirect are checked. Default hosts are `boardgamegeek.com` and `geek-export-stats.s3.amazonaws.com`; extend it only for a trusted official download host. Downloads are capped at 50 MB compressed and 250 MB uncompressed CSV.

The code does not provision hosting, persistent disks, paid services, credentials, monitoring accounts or a backup schedule. Confirm these against the actual deployment. CI PostgreSQL/browser checks validate disposable environments, not the production host.

## Health and worker recovery

Monitor `/health/` for web/database health and `/health/worker/` independently for queue execution. Responses contain no credentials, job errors or database addresses. A worker can be idle and healthy; an active job's heartbeat also counts.

Jobs check cancellation between requests and during retry waits. A request already in flight may take up to its timeout (60 seconds for BGG). The short final database transaction must complete or roll back; cancellation is checked at its boundaries. SQLite may delay concurrent cancellation writes while this transaction holds its write lock.

The shared writer lease prevents competing application jobs from replacing the catalog. Missing/terminal/duplicate deliveries are safe no-ops. Source HTTP errors receive bounded client retries; failed imports remain visible and require an explicit Retry action or a new import with a fresh ranks link. Retrying restarts from retrieval; it does not resume half-applied data.

After an unexpected worker stop, restart `run_worker`. Startup recovery marks jobs without a heartbeat for 15 minutes as failed and releases their lease. For a more recent stop, wait for that threshold and run `python manage.py recover_jobs`, then retry. Do not manually release a healthy writer's lease.

Back up before upgrades. Apply all migrations before starting the new web/worker code. The ownership migration removes duplicate relationships while preserving one copy of each link; later migrations add open-ended polls and health/revision records. Existing fetched data is not automatically refreshed.

## SQLite backup and restore drill

Create a consistent online backup using SQLite's backup API; do not copy only `db.sqlite3` while its WAL contains writes:

```text
python manage.py backup_catalog backups/catalog-2026-10-20.sqlite3
```

Choose a new dated filename each time. The command refuses overwrites and checks SQLite integrity and game count. Store a copy outside the host's failure domain using your existing backup arrangements. No recurring backup service or off-host storage is created by this change.

For a restore drill, keep production processes running against their original database and restore into a **new disposable path**. Example PowerShell:

```powershell
Copy-Item -LiteralPath backups/catalog-2026-10-20.sqlite3 -Destination backups/restore-drill.sqlite3
$env:DATABASE_URL = 'sqlite:///C:/absolute/path/to/bggweb/backups/restore-drill.sqlite3'
$env:DJANGO_DEBUG = 'True'
python manage.py migrate --noinput
python manage.py check
python manage.py shell -c "from games.models import Game; print(Game.objects.count())"
```

Use a new restore filename; check the resolved destination before copying. Inspect a known game's title, polls and ownership in the restored database, and verify integrity with Python's `sqlite3` module. Start a separate loopback-only server against it if a visual check is needed. Remove the temporary `DATABASE_URL` environment setting afterward. Actual replacement of a production database requires stopping web/worker first and retaining the prior database for rollback; this work does not perform that replacement.

An automated `BackupRestoreTests` drill creates and restores disposable databases and verifies that later source changes do not alter the backup. It is skipped in PostgreSQL CI because the command is SQLite-specific.

For PostgreSQL, use your host's snapshot/restore facilities or `pg_dump --format=custom` and `pg_restore` into a new disposable database. Validate migrations, row counts, polls and ownership there before any production cutover. PostgreSQL backup tooling and actual host restore permissions are not assumed available locally.

## Dependencies and acceptance

Runtime versions are recorded in `requirements-lock.txt`, including Django 5.2.18 from the supported 5.2 LTS line. The version was checked against [Django's release table](https://www.djangoproject.com/download/) on October 20, 2026. Resolve future updates in a clean virtual environment and audit the production lock with `pip-audit -r requirements-lock.txt`. Dependabot proposes reviewed updates; no automatic merges are configured.

CI covers SQLite on Python 3.11/3.12, PostgreSQL 16, migration/static checks, production settings, browser filter/export/mobile/job-error workflows, and advisories. Browser screenshots are uploaded for review. A green workflow does not verify live BGG credentials or hosting persistence.

Before declaring deployment acceptance, verify: the expected commit is running; all migrations/static assets are present; web and worker share the same database; health endpoints behave correctly; a safe import completes with the configured sources; and a real backup can be restored into a disposable environment. Outstanding external acceptance items stay recorded in IMPLEMENTATION_PROGRESS.md.

## Catalog caches and freshness

Normalization/facet caches are process-local, expire after five minutes, and include the database revision in their keys. Import application, platform reconciliations, tracked-user changes and supported admin edits advance that revision. Custom bulk scripts must call `games.catalog_cache.invalidate_catalog()` inside their mutation transaction.

The dashboard shows successful source import times separately from the latest refresh attempt. These records begin when the new tracking is enabled; it does not invent historical fetch times from unrelated record updates.
