#Requires -Version 5.1
<#
  launch-evolve.ps1 - start an overnight evolution run AND open the dashboard
  on the Evolution tab. This is the command behind the dev-observatory
  run-evolution launch button.

  What it does:
    1. Starts scripts/evolve.py (the evolve runner behind the
       /improve-bot-evolve skill) in its own persistent console window --
       visible and closable (see HOW TO STOP THE RUN below) -- and passes
       --viewer, so the run's SC2 games render inside the themed self-play
       viewer container instead of raw, unmanaged SC2 windows. The container
       needs the optional [viewer] extra, which is why the command is
       "uv run --extra viewer"; on a machine without it (or off Windows)
       --viewer degrades to a WARNING and the run continues headless.
    2. Delegates to launch-a4g.ps1 -Tab evolution: brings up the backend (:8765)
       and frontend (:3000) only if they are not already running, then opens
       http://localhost:3000/?tab=evolution. The Evolution tab OBSERVES the run
       (via the data/evolve_*.json state files the runner writes); closing the
       dashboard does not stop the run.

  CAUTION: evolve.py creates new bot versions, flips bots/current, and
  AUTO-COMMITS [evo-auto] promotions to master.

  HOW TO STOP THE RUN: close the evolve CONSOLE window this script opens.
  That is the only stop gesture. Two things that look like one are not:
    * Closing the themed viewer container only DETACHES the display -- the run
      keeps going headless to its --hours budget. That is deliberate, so an
      operator who dismisses the window does not lose hours of evolution.
    * The dashboard's Stop button is NOT wired to this runner yet. It writes
      data/evolve_run_control.json, but nothing in scripts/evolve.py or
      src/orchestrator/ reads that file, so the run ignores it. See
      .claude/skills/improve-bot-evolve/SKILL.md (the stop-condition and
      control-file sections both record this as a future enhancement).

  DO NOT PRESS Ctrl+C in the evolve console window. Under --viewer the
  evolution loop runs off the main thread, so burnysc2's SIGINT kill-switch is
  never armed, and Ctrl+C can leave ORPHANED SC2 processes behind. Cleaning
  those up by hand is forbidden by .claude/rules/bot-runtime.md (never kill
  SC2_x64.exe), so close the console window instead.
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
    $evolveCmd = "Set-Location '$root'; uv run --extra viewer python scripts/evolve.py --hours $Hours --viewer"
    Start-Process powershell -ArgumentList '-NoExit', '-Command', $evolveCmd | Out-Null
    Write-Host "Evolution run started (--hours $Hours) in its own window." -ForegroundColor Green
}

# 2. Dashboard up (reusing already-running servers) + open the Evolution tab.
& (Join-Path $PSScriptRoot 'launch-a4g.ps1') -Tab evolution
