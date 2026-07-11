# One-command local dev launcher: sets up the environment and starts BOTH the
# web server and the background worker. Just run:  .\start_dev.ps1
#
# (If PowerShell blocks the script with "running scripts is disabled", run this
# once in the same window first:  Set-ExecutionPolicy -Scope Process -Bypass)

# Stop on the first error so we don't try to start the server after a failed
# install/migrate.
$ErrorActionPreference = "Stop"

# Run from the repo root regardless of where the script was invoked from.
Set-Location -Path $PSScriptRoot

$DevUrl = "http://127.0.0.1:8001/"

# Check if .venv exists, if not create it
if (-not (Test-Path ".venv")) {
    Write-Host "Creating virtual environment..."
    python -m venv .venv
}

# Activate venv
Write-Host "Activating virtual environment..."
.\.venv\Scripts\Activate.ps1

# Install dependencies (quiet: only show problems, not the full satisfied list)
Write-Host "Installing dependencies..."
pip install --quiet --disable-pip-version-check -r requirements.txt

# Enable development settings for this session (DEBUG defaults to False so the
# app is production-safe; turn it on here so local runs work out of the box).
# This MUST come before manage.py runs: with DEBUG=False and no DJANGO_SECRET_KEY
# the settings refuse to load, which would fail the migrate step below.
$env:DJANGO_DEBUG = "True"

# Run migrations
Write-Host "Running migrations..."
python manage.py migrate

# Open the browser shortly after the server has had time to start. Honcho runs
# in the foreground below, so do this from a background job.
Start-Job -ScriptBlock {
    param($u)
    Start-Sleep -Seconds 4
    Start-Process $u
} -ArgumentList $DevUrl | Out-Null

# Start Honcho (runs both web and worker from Procfile.dev). This blocks until
# you press Ctrl+C, which stops both processes.
Write-Host "Starting web server and background worker via Honcho..."
Write-Host "App will open at $DevUrl  (press Ctrl+C to stop everything)"
honcho start -f Procfile.dev
