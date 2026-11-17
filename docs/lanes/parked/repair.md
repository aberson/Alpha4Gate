# Parked lane: repair

Parked 2026-11-17 because it is waiting on free memory. Central owns this file and
ships it. Reopen the lane with `lane-open` under the same id, `repair`.

## What the lane is for

Fix the Alpha4Gate "front doors": the desktop shortcuts and documented launchers that
opened but never started a game. Findings are in
`docs/investigations/repair-2026-11-16.md`.

## Done (all committed on `master-plan/phase-ev`)

| Commit | What |
|---|---|
| `08709ef` | `scripts/launch-jev-battle.ps1`: the isolated OBS recorder starts with `--multi`, so it no longer hangs on an invisible crash prompt. The launcher recognises only the isolated recorder and restarts one that is stuck. `-CheckOnly` reports a stuck recorder. A new `-DecisionProvider typesafe\|scripted` option was added. |
| `8915098` | `docs/operator/jev-battle-view.md`: the free scripted command and a note on the recorder (shipped in the docs migration commit). |
| `cac7881` | `scripts/probes/jev_battle_view.py`: a minimised Edge child is restored while it is measured, and a geometry failure now reports the window state. Two tests were added to `tests/test_jev_battle_launcher.py`. Paid-run sections were added to the investigation doc. |

Verified: 3 free scripted runs passed. Paid run 1 (`5778e27a`) failed once with a
146x21 Edge geometry fault and spent no credits. Paid run 2 (`1ae0cc5e`) passed end to
end in hosted mode but made 0 hosted calls, which is by design for a 90 game-second cap.
Both Observer Rush desktop shortcuts work again.

## Pending, and why it is parked

**One longer paid Observer Rush run**, to prove that a hosted Typesafe request is
answered in a live match. Jev v2 asks the model only once a question has more than one
option. A 90-second match can end before that, so this run uses 300 game-seconds.

- **Operator approval: given** (relayed by central on 2026-11-16). It covers this one
  run only. Do not run a second paid match without a new approval.
- Attempt 1 (2026-11-16, about 22:57Z) was stopped by Claude Code's low-memory reap
  while the probe was starting. No run directory was created, no SC2 process was left
  and no request was sent.
- **Wait for:** at least 3.5 GB of free physical memory, or central's word that
  evolve's build has finished. That is central's hold condition. SC2, Edge, the probe
  and OBS together need about that much.

Check free memory (PowerShell):

```powershell
[int]((Get-CimInstance Win32_OperatingSystem).FreePhysicalMemory / 1024)  # MB free; need >= 3500
```

## How to resume

1. Run `passing-notes inbox --project alpha4gate --lane repair` and set the card active.
2. Check free memory as shown above, and that no `SC2_x64` is running. Never kill SC2 by hand.
3. From the repository root, claim the client. The Heads Up checkout sits beside this
   repository, and the session id is this window's:
   `uv run --project ..\heads-up heads-up claim --json --resource-kind named-resource --resource windows-sc2-client --repository (Get-Location).Path --owner repair --session-id <session> --ttl 30m`
4. From the repository root, run the match in the foreground or the background:

   ```powershell
   powershell -NoProfile -File scripts/launch-jev-battle.ps1 -Difficulty 1 -MaxGameSeconds 300 -MaxWallSeconds 600
   ```

5. In the newest `data\jev\runs\<run_id>\events.1.jsonl`, report the strategy facts'
   `calls`, tokens and sources (`typesafe` versus `local_fallback`). Append the result
   to `docs/investigations/repair-2026-11-16.md`. Release the claim, and send central
   a handoff note.

If the 146x21 geometry fault recurs, the error text now names Edge's iconic flag,
parent and rect. Record it in the investigation doc.

## Still unverified (lower priority, no approval needed)

- `scripts/launch-jev.ps1` live. Its limits are fixed at 900 game / 1200 wall seconds,
  so a free scripted test holds SC2 for up to 20 minutes.
- The interactive start path of `scripts/launch-a4g.ps1`. Only the reuse path was run.

## Ownership

Repair owns:
- `scripts/launch-jev-battle.ps1`
- `scripts/launch-jev.ps1`
- `scripts/launch-a4g.ps1`
- `scripts/probes/jev_battle_view.py`
- `frontend/src/components/JevBattleView.tsx`
- `docs/operator/jev-battle-view.md`
- `docs/investigations/repair-*.md`
- the desktop shortcuts

`scripts/launch-evolve.ps1` belongs to lane evolve; its problems were reported to it.
`tests/test_jev_battle_launcher.py` belongs to no lane. Central is the shipper.

There is no branch, worktree or issue of the lane's own. The work rides
`master-plan/phase-ev` in the main checkout.

## Reported elsewhere, still open

- The SC2 98310 compatibility shim (`orchestrator.sc2_compat`) and the bot trees
  `bots/v0`-`v12`, which do not install it. Those belong to scoreboard and central
  (ES.14 #370).
- `tests/test_repo_hygiene.py` fails on absolute paths in committed October Jev
  evidence files and in `docs/plan-reconciliation/`, not in repair's files.
