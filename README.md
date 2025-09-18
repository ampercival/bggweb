# bggweb

A small Django web app that fetches BoardGameGeek (BGG) data (Top N list, optional user-owned collection, and per-game details/polls), stores it locally, and presents a fast, filterable view with CSV export. Jobs run in the background with live, multi-phase progress (Top N → Collection → Details → Apply).

## Features
- Top N fetch: Scrapes the BGG Top N list.
- Collection merge: Optionally pulls a user’s owned collection and marks overlap as Owned.
- Batched details: Fetches per-game details and player-count poll data in batches.
- Durable storage: Persists games, categories, families, and player-count recommendations to SQLite by default.
- Job progress UI: Shows overall progress and per-phase status (Top N, Collection, Details, Apply) with elapsed/ETA.
- Filtering and CSV: Browse and filter games, then export the table as CSV.

## Requirements
- Python 3.11+ (3.10+ likely fine)
- Django >= 5.2
- requests >= 2.31
- beautifulsoup4 >= 4.12

See `requirements.txt` for exact versions.

## Quickstart
1) Create and activate a virtual environment

- Windows (PowerShell)
```
python -m venv .venv
.\.venv\Scripts\Activate.ps1
```
- macOS/Linux
```
python3 -m venv .venv
source .venv/bin/activate
```

2) Install dependencies
```
pip install -r requirements.txt
```

3) Run migrations
```
python manage.py migrate
```

4) Start the dev server
```
python manage.py runserver
```

5) Open the app
- Navigate to http://127.0.0.1:8000/
- From Home, start a job:
  - Top N: fetches the current Top N games
  - Collection: fetches a user’s owned collection
  - Refresh: Top N (+ optional collection) → batched details → apply to DB
- Click into the job to watch phase progress.
- After completion, use View Games and Export CSV.

## Data Model (brief)
- `Game`: Core game info plus categories and families (many-to-many)
- `PlayerCountRecommendation`: Per-player-count poll stats and derived scores
- `Collection`/`OwnedGame`: Optional user-owned relationships
- `FetchJob`: Tracks background job status, overall progress, and phase details

## Background Jobs
Jobs run in a background thread (no Celery required). The job page polls for status and shows:
- Top N: scrape progress
- Collection: owned items progress
- Details: processed/total with batch size
- Apply: database apply/commit step (single atomic transaction)

## Configuration
No API keys are required. Defaults to a local SQLite database (`db.sqlite3`). You can switch to Postgres/MySQL by updating `bggweb/settings.py`.

## Development Tips
- Don’t commit local DB/venv: `.gitignore` excludes `db.sqlite3`, `.venv/`, etc.
- If you tweak polling or batch size, see `games/tasks.py` and `templates/job_detail.html`.

## License
Not specified. Add a LICENSE file if you plan to open source.

---

### Suggested GitHub Description
Django app for fetching and browsing BoardGameGeek data with background jobs, detailed phase progress, and CSV export.
