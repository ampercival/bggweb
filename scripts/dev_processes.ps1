# Identify only this checkout's venv entry points, then their descendants.
# Never infer ownership from the shared system Python executable or port alone.
function Get-BggDevProcessTree {
    param([string]$ProjectRoot, [object[]]$ProcessSnapshot)
    $venvScripts = Join-Path ([IO.Path]::GetFullPath($ProjectRoot)) '.venv\Scripts'
    $pythonPath = Join-Path $venvScripts 'python.exe'
    $honchoPath = Join-Path $venvScripts 'honcho.exe'
    $owned = @{}
    foreach ($entry in $ProcessSnapshot) {
        $isPythonEntry = $entry.ExecutablePath -eq $pythonPath -and (
            $entry.CommandLine -match '(?:^|\s)manage\.py\s+(?:runserver|run_worker)(?:\s|$)' -or
            ($entry.CommandLine -like "*$honchoPath*" -and $entry.CommandLine -match '\bstart\s+-f\s+Procfile\.dev\b')
        )
        if ($isPythonEntry -or ($entry.ExecutablePath -eq $honchoPath -and
                $entry.CommandLine -match '\bstart\s+-f\s+Procfile\.dev\b')) {
            $owned[[int]$entry.ProcessId] = $entry
        }
    }
    do {
        $added = $false
        foreach ($entry in $ProcessSnapshot) {
            if ($owned.ContainsKey([int]$entry.ParentProcessId) -and -not $owned.ContainsKey([int]$entry.ProcessId)) {
                $owned[[int]$entry.ProcessId] = $entry
                $added = $true
            }
        }
    } while ($added)
    @($owned.Values)
}

function Stop-BggDevProcesses {
    param([string]$ProjectRoot)
    $snapshot = @(Get-CimInstance Win32_Process -ErrorAction Stop)
    $owned = @(Get-BggDevProcessTree -ProjectRoot $ProjectRoot -ProcessSnapshot $snapshot)
    # Include every captured descendant so Python venv wrapper exits cannot
    # leave the actual interpreter behind on Windows.
    foreach ($entry in $owned) {
        $current = Get-CimInstance Win32_Process -Filter "ProcessId=$($entry.ProcessId)" -ErrorAction SilentlyContinue
        if ($current -and $current.CreationDate -eq $entry.CreationDate) {
            Stop-Process -Id $entry.ProcessId -Force -ErrorAction SilentlyContinue
        }
    }
}

function Assert-BggDevCanRestart {
    param([string]$ProjectRoot, [int]$Port)
    $snapshot = @(Get-CimInstance Win32_Process -ErrorAction Stop)
    $owned = @(Get-BggDevProcessTree -ProjectRoot $ProjectRoot -ProcessSnapshot $snapshot)
    $ownedIds = @($owned | ForEach-Object { [int]$_.ProcessId })
    $listeners = @(Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction SilentlyContinue)
    if (@($listeners | Where-Object { [int]$_.OwningProcess -notin $ownedIds }).Count) {
        throw "Port $Port is used by another application. Close it or choose a different port; it has not been stopped."
    }
    if ($owned.Count) {
        $activeJobs = Get-BggActiveJobCount -ProjectRoot $ProjectRoot
        if ([int]$activeJobs -gt 0) { throw 'An import is active or queued. Wait for it to finish (or cancel it in the app), then run start_dev.ps1 again.' }
    }
}

function Get-BggActiveJobCount {
    param([string]$ProjectRoot)
    $python = Join-Path $ProjectRoot '.venv\Scripts\python.exe'
    $activeJobs = & $python -c "import os; os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'bggweb.settings'); import django; django.setup(); from games.models import FetchJob; print(FetchJob.objects.filter(status__in=['pending', 'running', 'cancelling']).count())"
    if ($LASTEXITCODE -ne 0) { throw 'Could not check active imports. Existing services have not been stopped.' }
    [int]$activeJobs
}
