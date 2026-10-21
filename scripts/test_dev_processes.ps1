# Windows regression checks use synthetic process snapshots; no real process is stopped.
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'dev_processes.ps1')
$project = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$python = Join-Path $project '.venv\Scripts\python.exe'
$otherPython = Join-Path (Split-Path $project) 'other-project\.venv\Scripts\python.exe'
$snapshot = @(
    [pscustomobject]@{ ProcessId=10; ParentProcessId=1; ExecutablePath=$python; CommandLine='python -u manage.py runserver 0.0.0.0:8471 --noreload' },
    [pscustomobject]@{ ProcessId=11; ParentProcessId=10; ExecutablePath='C:\Python\python.exe'; CommandLine='python -u manage.py runserver 0.0.0.0:8471 --noreload' },
    [pscustomobject]@{ ProcessId=12; ParentProcessId=1; ExecutablePath=$otherPython; CommandLine='python -u manage.py runserver 0.0.0.0:8471 --noreload' },
    [pscustomobject]@{ ProcessId=13; ParentProcessId=12; ExecutablePath='C:\Python\python.exe'; CommandLine='python -u manage.py runserver 0.0.0.0:8471 --noreload' },
    [pscustomobject]@{ ProcessId=14; ParentProcessId=1; ExecutablePath=$python; CommandLine='python manage.py test games' },
    [pscustomobject]@{ ProcessId=15; ParentProcessId=11; ExecutablePath='C:\Python\python.exe'; CommandLine='child' }
)
$owned = @(Get-BggDevProcessTree -ProjectRoot $project -ProcessSnapshot $snapshot | Sort-Object ProcessId)
if (($owned.ProcessId -join ',') -ne '10,11,15') { throw 'Project ownership included an unrelated process or missed a child.' }
function Get-CimInstance {
    param($ClassName, $Filter)
    if ($Filter) {
        $targetId = [int]($Filter -replace 'ProcessId=', '')
        $snapshot | Where-Object { $_.ProcessId -eq $targetId }
    } else { $snapshot }
}
$listeners = @([pscustomobject]@{ OwningProcess=11 })
function Get-NetTCPConnection { $listeners }
$activeCount = 0
function Get-BggActiveJobCount { $activeCount }
Assert-BggDevCanRestart -ProjectRoot $project -Port 8471
$activeCount = 1
try {
    Assert-BggDevCanRestart -ProjectRoot $project -Port 8471
    throw 'Active job was not protected.'
} catch {
    if ($_.Exception.Message -notmatch 'An import is active') { throw }
}
$activeCount = 0
$listeners = @([pscustomobject]@{ OwningProcess=13 })
try {
    Assert-BggDevCanRestart -ProjectRoot $project -Port 8471
    throw 'An unrelated port owner was not protected.'
} catch {
    if ($_.Exception.Message -notmatch 'another application') { throw }
}
$script:stopped = [Collections.Generic.List[int]]::new()
function Stop-Process {
    param([int]$Id, [switch]$Force)
    $script:stopped.Add($Id)
}
Stop-BggDevProcesses -ProjectRoot $project
if ((@($script:stopped | Sort-Object) -join ',') -ne '10,11,15') { throw 'Cleanup stopped an unrelated process or missed a project descendant.' }
Write-Host 'Passed: project-only process ownership and cleanup, venv descendants, active-import guard and unrelated-port guard.'
