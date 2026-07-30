#Requires -Version 5.1
<#
  launch-a4g.ps1 - one-click all-in-one launcher for the Alpha4Gate dashboard.

  What it does:
    1. Starts the backend (FastAPI dashboard API on :8765) in its own window.
    2. Starts the frontend (Vite dev server on :3000, proxies to :8765) in its own window.
    3. Waits for the frontend to answer, then opens the dashboard UI in the browser.

  This does NOT start a game -- it brings up the web dashboard (front end + back end).
  Close the spawned backend/frontend windows to stop the servers.

  NOTE: scripts/start-dev.sh is a DIFFERENT script for `build-step --ui` capture
  (kills the backend when the foreground exits); this one keeps everything running.
#>
[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$root = Split-Path $PSScriptRoot -Parent
Set-Location $root

Write-Host "=== Alpha4Gate dashboard launcher ===" -ForegroundColor Cyan

# 1. Backend in its own persistent window (FastAPI on :8765).
#    bots.current is the MetaPathFinder alias to the active bot version.
$backendCmd = "Set-Location '$root'; uv run python -m bots.current.runner --serve"
Start-Process powershell -ArgumentList '-NoExit', '-Command', $backendCmd | Out-Null
Write-Host "Backend starting on http://localhost:8765 ..." -ForegroundColor Green

# 2. Frontend in its own persistent window (Vite on :3000).
$frontendCmd = "Set-Location '$root\frontend'; npm run dev"
Start-Process powershell -ArgumentList '-NoExit', '-Command', $frontendCmd | Out-Null
Write-Host "Frontend starting on http://localhost:3000 ..." -ForegroundColor Green

# 3. Wait for the frontend, then open the dashboard UI on this machine.
Write-Host "Waiting for the frontend on http://localhost:3000 ..." -ForegroundColor Cyan
$deadline = (Get-Date).AddSeconds(60)
$up = $false
while ((Get-Date) -lt $deadline) {
    try {
        Invoke-WebRequest -Uri 'http://localhost:3000/' -UseBasicParsing -TimeoutSec 2 | Out-Null
        $up = $true; break
    } catch { Start-Sleep -Milliseconds 800 }
}
if ($up) {
    Start-Process 'http://localhost:3000/'
    Write-Host "Dashboard opened: http://localhost:3000/" -ForegroundColor Green
} else {
    Write-Host "Frontend did not answer in 60s -- check its window, then open http://localhost:3000/ manually." -ForegroundColor Yellow
}

Write-Host ""
Read-Host "Press Enter to close this launcher (the backend/frontend windows keep running)"
