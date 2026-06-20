# Phase EL Step 6 — multi-lineage smoke gate (2026-06-20)

**Verdict: PASS.** The full EL substrate (parallel-lineage scheduling + baseline
gauntlet + diversity fingerprint persistence) ran end-to-end on real SC2 games
without crashing.

## Run config

```
python scripts/evolve.py --lineages 2 --generations 2 --pool-size 1 \
  --games-per-eval 1 --fitness-mode both --population-cap 2 --no-commit \
  --game-time-limit 900 --hard-timeout 1200 --hours 1.5
```

Trimmed from the plan's nominal command (`--pool-size 2 --games-per-eval 3`)
to `--pool-size 1 --games-per-eval 1` to keep the smoke bounded — the gate
validates that the pipeline *runs*, not statistical rigor. `--generations 2`
(one per lineage) was used instead of the plan's `--generations 1` so BOTH
lineages actually schedule (a generation == one lineage's round-robin turn).

**Pre-seed** (smoke fixtures, removed after the run): `data/baselines.json`
(`base-v0` → v0); `data/lineages.json` (`main` → v13, `line-2` → v7).

Started 15:50:12 UTC, finished 16:21:31 UTC (~31 min wall). Exit 0,
`status: completed`, stop reason `generations-reached`.

## Done-when checklist

| Criterion | Result |
|---|---|
| Round completes without crash | **PASS** — exit 0, status `completed`, 2/2 generations |
| Both lineages schedule | **PASS** — `multi-lineage scheduling engaged across 2 lineage(s): line-2@v7, main@v13`; gen 1 → `line-2 (head v7)`, gen 2 → `main (head v13)` (pointer flipped each turn) |
| Both lineages produced a fitness result | **PASS** — gen 1 cand 1-0 vs v7 (pass); gen 2 cand 1-0 vs v13 (pass) |
| Baseline gauntlet ran (`--fitness-mode both`) | **PASS** — `gauntlet: v14 vs base-v0 (v0) -> 1/1`; `gauntlet: v15 vs base-v0 (v0) -> 1/1`; 2 `phase:"gauntlet"` rows in evolve_results.jsonl |
| `data/fingerprints.json` populated | **PASS** — `{v14: {base-v0: 1.0}, v15: {base-v0: 1.0}}` |
| Extinction logic exercised | N/A by design — at `--population-cap 2` with exactly 2 lineages, population is AT cap so no cull fires (extinction needs *over* cap; cull logic is exhaustively unit-tested in `tests/test_population.py`). 0 `phase:"extinction"` rows, as expected. |
| No orphan SC2 / python processes | **PASS** — zero `SC2_x64`/`python` processes after exit |
| `git status` clean (`--no-commit`) | **PASS** — pointer ended at v13 (gen-2 regression rollback restored it); no tracked-file changes; scratch `bots/v14`, `bots/v15` from the no-commit promotions removed in cleanup |

## What the round did (full pipeline)

| gen | lineage (head) | fitness | promote | regression | net |
|---|---|---|---|---|---|
| 1 | line-2 (v7) | cand 1-0 vs v7 (pass) | v14 | pass (v14 1-0 vs v7) | **v7 → v14 promoted** |
| 2 | main (v13) | cand 1-0 vs v13 (pass) | v15 | **rollback** (v15 0-1 vs v13) | v13 retained (v15 reverted) |

Both Claude pool-generation and the dev-apply sub-agents ran (real imps:
"Shield-aware focus fire (effective-HP target selection)", "Deepen Forge
upgrade queue"). The regression gate correctly rolled back the weaker v15 — a
real demonstration that the promotion gate still works under multi-lineage
scheduling.

## Non-issues observed (benign)

- `sc2.proxy ... Caught unknown exception: Cannot write to closing transport`
  during the v15 gauntlet — known burnysc2 proxy-teardown race; the game
  result was recorded immediately after (`v15 vs base-v0 -> 1/1`) and the round
  completed exit 0. Not a defect.
- `v14 � mean win rate` in the log — cp1252 console mangling of the `→` arrow
  in the INFO message; cosmetic.

## Cleanup performed

- Removed scratch `bots/v14/`, `bots/v15/` (untracked `--no-commit` promotions).
- Removed the smoke-seeded `data/{lineages,baselines,fingerprints}.json` to
  restore the single-lineage default (a stray `lineages.json` would make the
  next bare `improve-bot-evolve` run unexpectedly multi-lineage). EL.7 setup
  re-seeds its own registries.
- Pointer confirmed at `v13`; no orphan processes.

## Not covered here (operator follow-up)

- **Dashboard visual** (the EL.5 Evolution-tab lineage cards / diversity matrix
  / extinction timeline rendering against live data) — not eyeballed; the
  `/api/evolve/lineages` endpoint is covered by TestClient + the components by
  vitest. Verify visually whenever the dashboard is next up with seeded data.
- **EL.7** (multi-hour soak with more lineages than the cap to actually fire
  extinction under sustained load) remains.
