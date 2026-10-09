#Requires -Version 5.1
<#
  launch-jev.ps1 - dashboard-first single Jev match (Jev v2 plan D7, Step 224).

  Usage, from the repository root:
    powershell -File scripts/launch-jev.ps1 -Version v2 -DecisionProvider typesafe
    powershell -File scripts/launch-jev.ps1 -Version v1 -DecisionProvider scripted -Difficulty 1 -Seed 1

  What it does (all of it in `python -m jev.launch`, which this script calls):
    1. Starts or reuses the dashboard servers (scripts/launch-a4g.ps1 -NoBrowser
       -NoWait: hidden helper windows) and verifies actual Jev API responses from
       the backend and through the frontend proxy, within 60 seconds.
    2. Creates one launch session in data\jev\launches\<session_id>\ and opens
       http://localhost:3000/?tab=jev&launch=<session_id> ONCE. If no browser
       can be opened, the exact URL is printed: open it within 60 seconds.
    3. Runs the match through the production runner (python -m bots.jev.<Version>
       --launch-session <session_id> --realtime ...). The runner records the run,
       publishes it to the session and starts SC2 only after the page rendered
       that exact run and its archived policy and acknowledged it.
  A dashboard that is not healthy, serves another checkout's data, or never shows
  the run stops the launch before SC2 starts; it never falls back to headless.

  Parameters: -Version v1|v2 (default v2), -DecisionProvider scripted|typesafe
  (default typesafe), -Difficulty 1-10 (default 3), -Seed (default 11),
  -OpponentRace (default Terran). Match limits, map and model are the plan's:
  Simple64, 900 game seconds, 1200 wall seconds, jev-1.13.0, 450 requests.

  The Typesafe key (typesafe only): an existing TYPESAFE_API_KEY in this process,
  else the saved encrypted key %LOCALAPPDATA%\Alpha4Gate\typesafe-key.dpapi. It is
  removed from this process before anything starts, never printed and never put
  in a command line; the launcher hands it to the game process only, so the
  dashboard servers and the browser never inherit it. When the script ends, this
  process's key is put back exactly as it was (none, or your own).

  Stop with Ctrl+C once: before the page acknowledged, nothing starts and the run
  records `stopped`; during the match the bot leaves the game cleanly.
#>
[CmdletBinding()]
param(
    # Jev package to play (bots.jev.<Version>).
    [ValidateSet('v1', 'v2')]
    [string]$Version = 'v2',
    # Army decision source; typesafe needs the service key.
    [ValidateSet('scripted', 'typesafe')]
    [string]$DecisionProvider = 'typesafe',
    # Built-in AI difficulty (3 = Medium, 4 = MediumHard).
    [ValidateRange(1, 10)]
    [int]$Difficulty = 3,
    # SC2 game seed.
    [ValidateRange(0, 2147483647)]
    [long]$Seed = 11,
    # Built-in opponent race.
    [ValidateSet('Terran', 'Protoss', 'Zerg', 'Random')]
    [string]$OpponentRace = 'Terran'
)

$ErrorActionPreference = 'Stop'
$root = Split-Path $PSScriptRoot -Parent
Set-Location $root
$keyName = 'TYPESAFE_API_KEY'

# Take the key out of this process first: nothing started below may inherit it.
# (Run as .\scripts\launch-jev.ps1 the script shares your session: the key you had
# is put back when it ends.)
$originalKey = [Environment]::GetEnvironmentVariable($keyName, 'Process')
$gameKey = $originalKey
[Environment]::SetEnvironmentVariable($keyName, $null, 'Process')

if ($DecisionProvider -eq 'typesafe' -and [string]::IsNullOrWhiteSpace($gameKey)) {
    $keyFile = Join-Path $env:LOCALAPPDATA 'Alpha4Gate\typesafe-key.dpapi'
    if (-not (Test-Path -LiteralPath $keyFile)) {
        Write-Host "No Typesafe key: set $keyName or save the encrypted key at $keyFile (see documentation/operator/jev-validation.md). Nothing was started." -ForegroundColor Red
        exit 1
    }
    $secret = ConvertTo-SecureString -String ((Get-Content -LiteralPath $keyFile -Raw).Trim())
    $gameKey = [System.Net.NetworkCredential]::new('', $secret).Password
    $secret.Dispose()
}
if ($DecisionProvider -eq 'scripted') {
    $gameKey = $null  # a scripted match never receives the key
}

Write-Host "=== Jev dashboard-first launch: $Version $DecisionProvider, $OpponentRace difficulty $Difficulty seed $Seed ===" -ForegroundColor Cyan

$launchArgs = @(
    'run', 'python', '-m', 'jev.launch',
    '--version', $Version,
    '--decision-provider', $DecisionProvider,
    '--difficulty', "$Difficulty",
    '--seed', "$Seed",
    '--opponent-race', $OpponentRace
)

$code = 1
try {
    if (-not [string]::IsNullOrWhiteSpace($gameKey)) {
        # Only the launcher process gets it; it removes it from its own environment
        # at once and passes it to the game process alone.
        [Environment]::SetEnvironmentVariable($keyName, $gameKey, 'Process')
    }
    & uv @launchArgs
    $code = $LASTEXITCODE
} finally {
    # Restore exactly what this process had before (nothing, or the operator's own key).
    [Environment]::SetEnvironmentVariable($keyName, $originalKey, 'Process')
    $gameKey = $null
    $originalKey = $null
}
exit $code
