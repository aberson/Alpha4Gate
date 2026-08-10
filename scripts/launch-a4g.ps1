#Requires -Version 5.1
<#
  launch-a4g.ps1 - one-click all-in-one launcher for the Alpha4Gate dashboard.

  What it does:
    1. Starts the backend (FastAPI dashboard API on :8765) in its own window,
       unless something is already answering on :8765 (then it is reused).
    2. Starts the frontend (Vite dev server on :3000, proxies to :8765) in its
       own window, unless :3000 is already answering (then it is reused).
    3. Waits for the frontend to answer, then opens the dashboard UI in the
       browser -- pass -Tab <name> to open a specific tab via the frontend's
       /?tab=<name> deep link (advisor/evolution/models/observable/processes/help).

  This does NOT start a game -- it brings up the web dashboard (front end + back end).
  Close the spawned backend/frontend windows to stop the servers.

  NOTE: scripts/start-dev.sh is a DIFFERENT script for `build-step --ui` capture
  (kills the backend when the foreground exits); this one keeps everything running.
#>
[CmdletBinding()]
param(
    # Dashboard tab to open (frontend /?tab= deep link). Empty = default tab.
    [string]$Tab = ''
)

$ErrorActionPreference = 'Stop'
$root = Split-Path $PSScriptRoot -Parent
Set-Location $root

# TCP-level "is anything listening?" probe. Tries BOTH loopbacks explicitly:
# under Windows PowerShell 5.1 (.NET Framework) a default TcpClient is
# IPv4-only and never attempts ::1 -- and Vite on Windows binds [::1]:3000
# ONLY (see dev/docs/lessons-learned.md), so a 127.0.0.1-only probe would
# miss a running frontend and double-start it.
function Test-PortOpen([int]$Port) {
    foreach ($addr in @([System.Net.IPAddress]::Loopback, [System.Net.IPAddress]::IPv6Loopback)) {
        $client = New-Object System.Net.Sockets.TcpClient($addr.AddressFamily)
        try {
            $async = $client.BeginConnect($addr, $Port, $null, $null)
            if ($async.AsyncWaitHandle.WaitOne(500)) {
                $client.EndConnect($async)
                return $true
            }
        } catch {
            # fall through to the next loopback
        } finally {
            $client.Close()
        }
    }
    return $false
}

Write-Host "=== Alpha4Gate dashboard launcher ===" -ForegroundColor Cyan

# 1. Backend in its own persistent window (FastAPI on :8765), unless already up.
#    bots.current is the MetaPathFinder alias to the active bot version.
if (Test-PortOpen 8765) {
    Write-Host "Backend already answering on http://localhost:8765 -- reusing it." -ForegroundColor Green
} else {
    $backendCmd = "Set-Location '$root'; uv run python -m bots.current.runner --serve"
    Start-Process powershell -ArgumentList '-NoExit', '-Command', $backendCmd | Out-Null
    Write-Host "Backend starting on http://localhost:8765 ..." -ForegroundColor Green
}

# 2. Frontend in its own persistent window (Vite on :3000), unless already up.
if (Test-PortOpen 3000) {
    Write-Host "Frontend already answering on http://localhost:3000 -- reusing it." -ForegroundColor Green
} else {
    $frontendCmd = "Set-Location '$root\frontend'; npm run dev"
    Start-Process powershell -ArgumentList '-NoExit', '-Command', $frontendCmd | Out-Null
    Write-Host "Frontend starting on http://localhost:3000 ..." -ForegroundColor Green
}

# 3. Wait for the frontend, then open the dashboard UI on this machine.
$url = 'http://localhost:3000/'
if ($Tab -ne '') { $url = "http://localhost:3000/?tab=$Tab" }
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
    Start-Process $url
    Write-Host "Dashboard opened: $url" -ForegroundColor Green
} else {
    Write-Host "Frontend did not answer in 60s -- check its window, then open $url manually." -ForegroundColor Yellow
}

Write-Host ""
Read-Host "Press Enter to close this launcher (the backend/frontend windows keep running)"
