# Check if .venv exists, if not create it
if (-not (Test-Path ".venv")) {
    Write-Host "Creating virtual environment..."
    python -m venv .venv
}

# Activate venv
Write-Host "Activating virtual environment..."
.\.venv\Scripts\Activate.ps1

# Install dependencies
Write-Host "Installing dependencies..."
pip install -r requirements.txt

# Run migrations
Write-Host "Running migrations..."
python manage.py migrate

# Enable development settings for this session (DEBUG defaults to False so the
# app is production-safe; turn it on here so local runs work out of the box).
$env:DJANGO_DEBUG = "True"

# Start Honcho (runs both web and worker from Procfile.dev)
Write-Host "Starting web server and background worker via Honcho..."
honcho start -f Procfile.dev
