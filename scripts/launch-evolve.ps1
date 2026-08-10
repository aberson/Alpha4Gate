#Requires -Version 5.1
<#
  launch-evolve.ps1 - start an overnight evolution run AND open the dashboard
  on the Evolution tab. This is the command behind the dev-observatory
  run-evolution launch button.

  What it does:
    1. Starts scripts/evolve.py (the headless evolve runner behind the
       /improve-bot-evolve skill) in its own persistent window -- visible and
       cancellable, exactly like running it by hand.
    2. Delegates to launch-a4g.ps1 -Tab evolution: brings up the backend (:8765)
       and frontend (:3000) only if they are not already running, then opens
       http://localhost:3000/?tab=evolution. The Evolution tab OBSERVES the run
       (via the data/evolve_*.json state files the runner writes); closing the
       dashboard does not stop the run.

  CAUTION: evolve.py creates new bot versions, flips bots/current, and
  AUTO-COMMITS [evo-auto] promotions to master. Close the evolve window (or use
  the dashboard's stop control) to stop the run.
#>
[CmdletBinding()]
param(
    # Wall-clock budget forwarded to evolve.py --hours.
    [double]$Hours = 4
)

$ErrorActionPreference = 'Stop'
$root = Split-Path $PSScriptRoot -Parent
Set-Location $root

Write-Host "=== Alpha4Gate evolution launcher ===" -ForegroundColor Cyan

# 1. The evolve run in its own persistent window -- unless one is already
#    running. Two concurrent runs race on data/evolve_*.json, both flip
#    bots/current, and both auto-commit to master, so this button doubles as
#    "open the Evolution dashboard" while a run is active. Probe python
#    processes only (the finished run's -NoExit window is powershell.exe and
#    must not count). Probe failure fails open: spawning is the pre-guard
#    behavior.
$evolveRunning = $false
try {
    $procs = @(Get-CimInstance Win32_Process -Filter "Name LIKE 'python%' AND CommandLine LIKE '%evolve.py%'" -ErrorAction Stop)
    if ($procs.Count -gt 0) { $evolveRunning = $true; $evolvePid = $procs[0].ProcessId }
} catch {
    Write-Host "Could not probe for a running evolve ($($_.Exception.Message)) -- assuming none." -ForegroundColor Yellow
}
if ($evolveRunning) {
    Write-Host "Evolution run ALREADY ACTIVE (python PID $evolvePid) -- not starting a second one." -ForegroundColor Yellow
    Write-Host "Opening the dashboard on the Evolution tab only." -ForegroundColor Yellow
} else {
    $evolveCmd = "Set-Location '$root'; uv run python scripts/evolve.py --hours $Hours"
    Start-Process powershell -ArgumentList '-NoExit', '-Command', $evolveCmd | Out-Null
    Write-Host "Evolution run started (--hours $Hours) in its own window." -ForegroundColor Green
}

# 2. Dashboard up (reusing already-running servers) + open the Evolution tab.
& (Join-Path $PSScriptRoot 'launch-a4g.ps1') -Tab evolution
