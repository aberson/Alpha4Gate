#Requires -Version 5.1
<# Local scripted Battle View. Uses the installed isolated OBS BattleProbe profile.
   Stop through the Battle View's Stop battle button, not by closing this console.
   -CheckOnly checks prerequisites without starting a game or changing OBS.
#>
[CmdletBinding()]
param([switch]$AutoRecord, [switch]$CheckOnly)
$ErrorActionPreference = 'Stop'
$root = Split-Path $PSScriptRoot -Parent
Set-Location $root
$originalKey = [Environment]::GetEnvironmentVariable('TYPESAFE_API_KEY', 'Process')
[Environment]::SetEnvironmentVariable('TYPESAFE_API_KEY', $null, 'Process')
try {
    foreach ($command in @('uv', 'npm')) { Get-Command $command -ErrorAction Stop | Out-Null }
    $obsRoot = Join-Path $env:LOCALAPPDATA 'Alpha4Gate\obs-portable'
    $obsExe = Join-Path $obsRoot 'bin\64bit\obs64.exe'
    foreach ($path in @($obsExe, (Join-Path $obsRoot 'probe-password.txt'))) {
        if (-not (Test-Path -LiteralPath $path)) {
            throw 'Battle View needs the isolated OBS setup. See documentation/operator/jev-battle-view.md.'
        }
    }
    if (Get-Process SC2_x64 -ErrorAction SilentlyContinue) {
        throw 'An SC2 game is already running. Finish it before starting another Battle View.'
    }
    if ($CheckOnly) { Write-Host 'Battle View prerequisites found. No game started.'; exit 0 }
    & (Join-Path $PSScriptRoot 'launch-a4g.ps1') -NoBrowser -NoWait
    $ready = $false
    for ($attempt = 0; $attempt -lt 60; $attempt++) {
        try {
            $null = Invoke-RestMethod 'http://localhost:3000/api/jev/runs' -TimeoutSec 2
            $ready = $true
            break
        } catch { Start-Sleep -Seconds 1 }
    }
    if (-not $ready) { throw 'Dashboard did not become ready. See logs/dashboard-*.log.' }
    $obsProcesses = @(Get-Process obs64 -ErrorAction SilentlyContinue)
    if ($obsProcesses.Count -eq 0) {
        Start-Process -FilePath $obsExe -WorkingDirectory (Split-Path $obsExe -Parent) -WindowStyle Hidden -ArgumentList @(
            '--portable', '--disable-shutdown-check', '--disable-updater', '--minimize-to-tray',
            '--profile', 'BattleProbe', '--collection', 'BattleProbe', '--websocketipv4only'
        ) | Out-Null
    }
    $obsReady = $false
    for ($attempt = 0; $attempt -lt 30; $attempt++) {
        $socket = New-Object System.Net.Sockets.TcpClient
        try { $socket.Connect('127.0.0.1', 4457); $obsReady = $true; break }
        catch { Start-Sleep -Seconds 1 }
        finally { $socket.Dispose() }
    }
    if (-not $obsReady) { throw 'The isolated OBS recorder is unavailable on port 4457. Close other OBS instances and retry.' }
    Write-Host 'Local Jev v2: no paid requests. Use Record Battle, Unlock camera, and Stop battle in the viewer.' -ForegroundColor Cyan
    $probeArgs = @('run', '--extra', 'viewer', '--with', 'playwright', '--with', 'obsws-python', 'python',
        'scripts/probes/jev_battle_view.py', '--interactive')
    if ($AutoRecord) { $probeArgs += '--auto-record' }
    & uv @probeArgs
    if ($LASTEXITCODE -ne 0) { throw "Battle View exited with code $LASTEXITCODE. See the message above." }
} finally {
    [Environment]::SetEnvironmentVariable('TYPESAFE_API_KEY', $originalKey, 'Process')
}
