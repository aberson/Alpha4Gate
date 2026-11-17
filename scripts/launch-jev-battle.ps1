#Requires -Version 5.1
<# Observer Rush: Jev v2, native SC2, graph and caster in one window.
   Stop through the Battle View's Stop battle button, not by closing this console.
   -CheckOnly checks prerequisites without starting a game or changing OBS.
   -DecisionProvider typesafe (default) plays hosted Typesafe Jev and spends paid
   requests; scripted plays the local graph only and never reads the key.
   The isolated OBS recorder starts hidden with --multi. Without it, OBS 32 shows an
   invisible crash prompt after any unclean exit and never opens port 4457; a hidden
   recorder in that state is restarted (docs/investigations/repair-2026-11-16.md).
#>
[CmdletBinding()]
param([switch]$AutoRecord, [switch]$CheckOnly,
    [ValidateSet('typesafe', 'scripted')][string]$DecisionProvider = 'typesafe',
    [ValidateRange(1, 450)][int]$DecisionMaxRequests = 450,
    [ValidateSet(0, 1, 2, 4, 8, 16)][int]$GameSpeed = 0,
    [string]$SourceSnapshot,
    [ValidateRange(0, 86400)][int]$MaxGameSeconds = 0,
    [ValidateRange(0, 86400)][int]$MaxWallSeconds = 0,
    [ValidateRange(1, 10)][int]$Difficulty = 3,
    [ValidateRange(0, 2147483647)][int]$Seed = 11,
    [string]$RunId)
$ErrorActionPreference = 'Stop'
$root = Split-Path $PSScriptRoot -Parent
Set-Location $root
$obsRoot = Join-Path $env:LOCALAPPDATA 'Alpha4Gate\obs-portable'
$obsExe = Join-Path $obsRoot 'bin\64bit\obs64.exe'

function Test-ObsSocket {
    $socket = New-Object System.Net.Sockets.TcpClient
    try {
        $async = $socket.BeginConnect('127.0.0.1', 4457, $null, $null)
        if (-not $async.AsyncWaitHandle.WaitOne(1000)) { return $false }
        $socket.EndConnect($async)
        return $true
    } catch { return $false }
    finally { $socket.Dispose() }
}

# Only the isolated recorder this launcher starts; the operator's own OBS is never touched.
function Get-IsolatedObs {
    @(Get-CimInstance Win32_Process -Filter "Name='obs64.exe'" |
        Where-Object { $_.ExecutablePath -and ($_.ExecutablePath -ieq $obsExe) })
}

# A recorder up for a minute without its websocket is waiting on a prompt nobody can see.
function Get-StuckObs {
    if (Test-ObsSocket) { return @() }
    @(Get-IsolatedObs | Where-Object { ((Get-Date) - $_.CreationDate).TotalSeconds -ge 60 })
}

$originalKey = [Environment]::GetEnvironmentVariable('TYPESAFE_API_KEY', 'Process')
[Environment]::SetEnvironmentVariable('TYPESAFE_API_KEY', $null, 'Process')
try {
    $gameKey = $null
    if ($DecisionProvider -eq 'typesafe') {
        $gameKey = $originalKey
        if ([string]::IsNullOrWhiteSpace($gameKey)) {
            $keyFile = Join-Path $env:LOCALAPPDATA 'Alpha4Gate\typesafe-key.dpapi'
            if (-not (Test-Path -LiteralPath $keyFile)) { throw 'No saved Typesafe key. See docs/operator/jev-validation.md.' }
            $secret = ConvertTo-SecureString -String ((Get-Content -LiteralPath $keyFile -Raw).Trim())
            try { $gameKey = [System.Net.NetworkCredential]::new('', $secret).Password }
            finally { $secret.Dispose() }
        }
        if ([string]::IsNullOrWhiteSpace($gameKey)) { throw 'Typesafe API key is empty. Nothing started.' }
    }
    foreach ($command in @('uv', 'npm')) { Get-Command $command -ErrorAction Stop | Out-Null }
    foreach ($path in @($obsExe, (Join-Path $obsRoot 'probe-password.txt'))) {
        if (-not (Test-Path -LiteralPath $path)) {
            throw 'Battle View needs the isolated OBS setup. See docs/operator/jev-battle-view.md.'
        }
    }
    $edge = @("${env:ProgramFiles(x86)}\Microsoft\Edge\Application\msedge.exe",
        "$env:ProgramFiles\Microsoft\Edge\Application\msedge.exe") | Where-Object { Test-Path -LiteralPath $_ }
    if (-not $edge) { throw 'Battle View needs Microsoft Edge. Nothing started.' }
    if (Get-Process SC2_x64 -ErrorAction SilentlyContinue) {
        throw 'An SC2 game is already running. Finish it before starting another Battle View.'
    }
    if ($CheckOnly) {
        foreach ($stuck in @(Get-StuckObs)) {
            Write-Host "The isolated OBS recorder (PID $($stuck.ProcessId), started $($stuck.CreationDate)) is not answering on port 4457. A launch will restart it." -ForegroundColor Yellow
        }
        Write-Host 'Battle View prerequisites found. No game started.'
        exit 0
    }
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
    $obsReady = Test-ObsSocket
    if (-not $obsReady) {
        foreach ($stuck in @(Get-StuckObs)) {
            Write-Host "Restarting the isolated OBS recorder (PID $($stuck.ProcessId)): it never opened port 4457." -ForegroundColor Yellow
            try { Stop-Process -Id $stuck.ProcessId -Force } catch { }
            Wait-Process -Id $stuck.ProcessId -Timeout 10 -ErrorAction SilentlyContinue
        }
        if (@(Get-IsolatedObs).Count -eq 0) {
            Start-Process -FilePath $obsExe -WorkingDirectory (Split-Path $obsExe -Parent) -WindowStyle Hidden -ArgumentList @(
                '--portable', '--multi', '--disable-shutdown-check', '--disable-updater', '--minimize-to-tray',
                '--profile', 'BattleProbe', '--collection', 'BattleProbe', '--websocketipv4only'
            ) | Out-Null
        }
        for ($attempt = 0; $attempt -lt 30 -and -not $obsReady; $attempt++) {
            Start-Sleep -Seconds 1
            $obsReady = Test-ObsSocket
        }
    }
    if (-not $obsReady) {
        $obsLogs = Join-Path $obsRoot 'config\obs-studio\logs'
        throw "The isolated OBS recorder did not open port 4457 within 30 seconds. See its newest log in $obsLogs."
    }
    if ($DecisionProvider -eq 'typesafe') {
        Write-Host "Observer Rush: Typesafe Jev v2 (jev-1.13.0), up to $DecisionMaxRequests paid requests. Use Stop battle to exit." -ForegroundColor Cyan
    } else {
        Write-Host 'Observer Rush: scripted Jev v2 (local graph, no paid requests). Use Stop battle to exit.' -ForegroundColor Cyan
    }
    $probeArgs = @('run', '--extra', 'viewer', '--with', 'playwright', '--with', 'obsws-python', 'python',
        'scripts/probes/jev_battle_view.py', '--interactive', '--decision-provider', $DecisionProvider,
        '--decision-max-requests', "$DecisionMaxRequests")
    $probeArgs += @('--max-game-seconds', "$MaxGameSeconds", '--max-wall-seconds', "$MaxWallSeconds",
        '--difficulty', "$Difficulty", '--seed', "$Seed")
    if ($GameSpeed -gt 0) { $probeArgs += @('--game-speed', "$GameSpeed") }
    if ($SourceSnapshot) { $probeArgs += @('--source-snapshot', (Resolve-Path -LiteralPath $SourceSnapshot).Path) }
    if ($RunId) { $probeArgs += @('--run-id', $RunId) }
    if ($AutoRecord) { $probeArgs += '--auto-record' }
    if ($gameKey) { [Environment]::SetEnvironmentVariable('TYPESAFE_API_KEY', $gameKey, 'Process') }
    & uv @probeArgs
    if ($LASTEXITCODE -ne 0) { throw "Battle View exited with code $LASTEXITCODE. See the message above." }
} finally {
    [Environment]::SetEnvironmentVariable('TYPESAFE_API_KEY', $originalKey, 'Process')
    $gameKey = $null
}
