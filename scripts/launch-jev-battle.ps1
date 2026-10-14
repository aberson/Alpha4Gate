#Requires -Version 5.1
<# Observer Rush: hosted Typesafe Jev, native SC2, graph and caster in one window.
   Stop through the Battle View's Stop battle button, not by closing this console.
   -CheckOnly checks prerequisites without starting a game or changing OBS.
#>
[CmdletBinding()]
param([switch]$AutoRecord, [switch]$CheckOnly,
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
$originalKey = [Environment]::GetEnvironmentVariable('TYPESAFE_API_KEY', 'Process')
[Environment]::SetEnvironmentVariable('TYPESAFE_API_KEY', $null, 'Process')
try {
    $gameKey = $originalKey
    if ([string]::IsNullOrWhiteSpace($gameKey)) {
        $keyFile = Join-Path $env:LOCALAPPDATA 'Alpha4Gate\typesafe-key.dpapi'
        if (-not (Test-Path -LiteralPath $keyFile)) { throw 'No saved Typesafe key. See documentation/operator/jev-validation.md.' }
        $secret = ConvertTo-SecureString -String ((Get-Content -LiteralPath $keyFile -Raw).Trim())
        try { $gameKey = [System.Net.NetworkCredential]::new('', $secret).Password }
        finally { $secret.Dispose() }
    }
    if ([string]::IsNullOrWhiteSpace($gameKey)) { throw 'Typesafe API key is empty. Nothing started.' }
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
    Write-Host "Observer Rush: Typesafe Jev v2 (jev-1.13.0), up to $DecisionMaxRequests paid requests. Use Stop battle to exit." -ForegroundColor Cyan
    $probeArgs = @('run', '--extra', 'viewer', '--with', 'playwright', '--with', 'obsws-python', 'python',
        'scripts/probes/jev_battle_view.py', '--interactive', '--decision-provider', 'typesafe',
        '--decision-max-requests', "$DecisionMaxRequests")
    $probeArgs += @('--max-game-seconds', "$MaxGameSeconds", '--max-wall-seconds', "$MaxWallSeconds",
        '--difficulty', "$Difficulty", '--seed', "$Seed")
    if ($GameSpeed -gt 0) { $probeArgs += @('--game-speed', "$GameSpeed") }
    if ($SourceSnapshot) { $probeArgs += @('--source-snapshot', (Resolve-Path -LiteralPath $SourceSnapshot).Path) }
    if ($RunId) { $probeArgs += @('--run-id', $RunId) }
    if ($AutoRecord) { $probeArgs += '--auto-record' }
    [Environment]::SetEnvironmentVariable('TYPESAFE_API_KEY', $gameKey, 'Process')
    & uv @probeArgs
    if ($LASTEXITCODE -ne 0) { throw "Battle View exited with code $LASTEXITCODE. See the message above." }
} finally {
    [Environment]::SetEnvironmentVariable('TYPESAFE_API_KEY', $originalKey, 'Process')
    $gameKey = $null
}
