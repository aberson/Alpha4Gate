# Jev v2 handoff: browser fixture update, then operator Step 222

Status 2026-10-10: build span 214-221 (#331-#338 closed) is DONE on `master-plan/phase-ev` (`0b46350`..`f52d4f6`; Manual UAT appended in `b83fd69`), not merged to `master`. 10 of 13 J2 steps are DONE (212, 224, 214-221). Remaining: Step 213 (#330, operator-deferred, not passed), Step 222 (#339, operator) and Step 223 (#340, wait). No SC2 match and no hosted Typesafe call were made during 214-221. No build span is pending. After the build, `57f7274` (2026-10-11, dashboard only) made the Jev tab branch-focused: **Branch overview** cards, one focused branch with **Full graph (debugging)** and **Fullscreen** controls, and a **Recent decisions** list. The gates below were measured at `f52d4f6`; at `57f7274` vitest is 393 passed, 6 skipped (399 tests). The three live-graph browser fixtures (`tests/test_jev_live_graph.py`) predate `57f7274` and need updating: at least two read lane-legend attributes the focused page no longer renders.

Progress: [J2 inventory](../jev-v2-progress.html). Read the [plan](jev-v2-plan.md) (Build notes 214-221 and `## Manual UAT`), [v2 operator guide section 13](../operator/jev-v2-validation.md#13-the-v2-player-and-the-live-graph-steps-214-221) (its "Before Step 222" part) and the [validation report](jev-v2-validation.md).

## Immediate action: adapt the browser fixtures

Update `tests/test_jev_live_graph.py` for the branch-focused viewer before the next
live acceptance session. `_lanes()` currently reads `data-active`/`data-waiting`
from the former legend; the new branch cards expose their counts as visible text.
Tests that require every graph lane must click **Full graph (debugging)** explicitly.
Keep assertions for the single-branch default and stable selection, rather than
restoring the cramped default just to satisfy the old fixtures.

Then run all nine browser fixtures from the repository root, using the harness's
mock game and mock Typesafe transport (no real SC2 or paid service call):

```powershell
$env:JEV_BROWSER_TESTS = '1'
try {
    uv run --with playwright pytest tests/test_jev_live_graph.py tests/test_jev_launch.py -k "LiveGraph or RealBrowser" -p no:cacheprovider
} finally {
    Remove-Item Env:JEV_BROWSER_TESTS
}
```

Record the exit code and results before proceeding. Before freezing v2, explicitly
resolve the unbuilt D3/section-5 items recorded in Step 218: saturation-based worker
growth (current `probe_target` is 16), max health/shield facts and low-durability
regrouping, and `available_abilities`. These are scope decisions, not shipped features.

## Next gameplay gate: operator Step 222 (Manual UAT M1)

Owner: the operator, with SC2 + Simple64, the saved encrypted Typesafe key and a visible browser. Limit: at most three paid dashboard-first matches (Terran, difficulties 3, 4, 5, seed 11, in that order), each within 450 requests / 900 game s / 1200 wall s. Stop as soon as the evidence is complete. If natural play omits a required item, record that gate as unverified and pending; never stage a scenario, and never count offline fixtures. Write the report into the [validation report](jev-v2-validation.md).

Run in: fresh PowerShell window @ Alpha4Gate · Model: any (no build work)

```powershell
uv run python -m bots.jev.v2 --validate-policy
```

Expected: `policy_hash=a1c73ea8b3335a335a742031d5bd9990471fda536ce35cc541c1eeab7a2351d3 nodes=292`. Then match 1, with the launcher's defaults (v2, Typesafe, Terran, difficulty 3, seed 11):

```powershell
powershell -File scripts/launch-jev.ps1
```

Play match 2 (`-Difficulty 4`) and match 3 (`-Difficulty 5`) only if evidence is still missing. After each match, verify its archive against the API (dashboard backend on :8765):

```powershell
$runId = (Get-ChildItem data\jev\runs -Directory | Sort-Object CreationTime | Select-Object -Last 1).Name
uv run python scripts\validate_jev.py --run-id $runId --api-base http://localhost:8765
```

Live tunables to note (never tune against held-out seeds): the 30 s posture/choice holds, the 28 s macro answer wait (`answer_wait_seconds`), the 5 s / 10 s execution intervals, and `air_threat_age` 30 s. Not verifiable offline: SC2 `query_pathing` from the start location (Step 217), and the Core 150 / Stalker 125/50/2 prices via `calculate_cost` (Step 218).

## Then Step 223 (wait)

Only when the candidate is final, and before its first held-out game, run `uv run python scripts/benchmark_jev.py --freeze-candidate v2` (write-once). Then run the `heldout-a`, `heldout-b` and `attribution` panels (18 games, plan D6). Without the freeze they stop with `candidate_not_frozen`.

## Preserved identities

The v1 policy hash `510a72dfb167b7200123cbc8436c912f02d3b0fb5798657db3c62e3f5f8f68ee` is unchanged; a 700-step v1 production-runner trace stayed byte-identical through every step. The frozen v1 baseline is `v1-b53039a693cadf75`, fingerprint `b53039a693cadf756e6cec4fc44c10534bcd9194079410a98b59a9e441bb1bfd` (`data/jev/benchmarks/baselines/`). It is gitignored and exists only in this primary checkout. Never recapture v1: in a checkout without it, a real `--panel baseline` would capture today's modified shared runtime as v1. The v2 hash is `a1c73ea8b3335a335a742031d5bd9990471fda536ce35cc541c1eeab7a2351d3` (292 nodes), not frozen. Baseline batch `94ac2c35b17f4cbaa80020b1c859ba00` holds one interrupted case and five pending; resume it only on a new operator request.

## Gates at f52d4f6 (full suites)

- pytest: 3345 passed, 12 skipped, 2 deselected with the `[viewer]` extra; 3310 passed, 32 skipped without it. The hygiene test fails only while the uncommitted derived `.claude/task-state/current.md` carries home paths.
- vitest: 390 passed, 6 skipped (27 files).
- mypy --strict clean (828 files); ruff clean; eslint 0 errors (1 pre-existing warning); vite build ok.
- Opt-in real-browser suite (`JEV_BROWSER_TESTS=1`): 9/9 at `f52d4f6`, before `57f7274`; the 3 live-graph fixtures must be updated before they are run again.

Review evidence: `.build-step/jev-v2-214/` .. `.build-step/jev-v2-221/` (gitignored). `.claude/task-state/current.md` and `dev.code-workspace` remain excluded from commits.
