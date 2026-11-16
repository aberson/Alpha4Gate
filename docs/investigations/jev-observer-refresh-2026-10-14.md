# Observer Rush crash repair and recorded tests — 2026-10-14

The shortcut already disables both match timers. Its apparent timeout was a
crash, not a duration limit: run `9cf18a18e3e349e98e14ecaba06ef219` ended at
109.4196 game seconds with `match_crashed`; the viewer's camera read a Probe's
gas-gathering order and burnysc2 raised `KeyError: 4135`.
Evidence: `scripts/launch-jev-battle.ps1:11-12,72`, that run's
`diagnostics.json:1`, and local
`.build-step/jev-live-validation/battle-9cf18a18e3e349e98e14ecaba06ef219.log`.

## Repair

Continued the existing `fix/burnysc2-730` worktree after the operator confirmed
its owner was no longer working. The compatibility shim translates known new
SC2 ability IDs to burnysc2's known ability representations. Jev and v13 install
it before their games (`src/jev/bot.py:110-113`, `bots/v13/bot.py:49`).

Review exposed an older-build regression: ID 4124 could be translated from
Mothership cloak into Baneling morph. The shim now checks the server's ability
link/button names before translating (`src/orchestrator/sc2_compat.py:49,94`).
Regression tests cover gas orders, reused older IDs, unknown future semantics
and fresh-process installation by both production callers
(`tests/test_sc2_compat.py:61,114,120,128`). No gameplay graph or frozen v1
snapshot was changed or recaptured.

The compatibility changes are preserved and pushed at `fc66956` on
`fix/burnysc2-730`. They are also applied in the current checkout for the
desktop launcher, but remain uncommitted there alongside the operator's
documentation migration. No migration files were reset or swept into a commit.

Validation:

- Focused compatibility/army/launcher tests: 73 passed in the compatibility
  worktree. Current-checkout compatibility/launcher tests: 29 passed.
- Strict mypy: 833 source files passed. Ruff: passed.
- Full compatibility-worktree Python suite: **3407 passed, 1 failed,
  12 skipped, 2 deselected**. The failure is
  `test_each_strategic_choice_selects_its_graph_branch[recover]` at
  `tests/test_jev_v2.py:1381`: the test expects no macro plan after the entire
  simulated match, but observes a local-fallback tech plan. This is not a green
  full-suite result and does not resolve the separate held Step 232 safety audit.
  Logs: `.build-step/jev-live-validation/compat-full-pytest.log`,
  `compat-mypy.log`, and `compat-ruff.log`.

## Recorded live checks

Both tests use scripted Jev v2, Simple64, Terran, seed 11, native real time,
automatic recording and zero game/wall limits. They make no Typesafe requests.
The second test was requested by the operator to observe a harder opponent.

| Difficulty | Run | Outcome |
|---|---|---|
| Medium (3) | `1a3ed6bc861c482aaa7421dfb7c8e47e` | Win at 254.5089 game seconds; no error; final event sequence 79881 |
| Hard (5) | `525a289465684dbb830f0abc05d88eb5` | Win at 378.1696 game seconds; no error; final event sequence 126161 |

Medium evidence: run `diagnostics.json:1` and `state.json:1`.
`scripts/validate_jev.py --run-id 1a3ed6bc861c482aaa7421dfb7c8e47e
--api-base http://localhost:8765` passed: disk and API agree.
The same validator passed for the Hard run. Its diagnostics record zero model
calls, five unit losses and zero structure losses; its final persisted
observation is at 377.9911 seconds, just before the outcome timestamp above
(`diagnostics.json:1`, `state.json:1`). Total hosted spend for these two tests: $0.
These are functional observations, not a matched-seed hosted/scripted strength
comparison. No claim about better model decisions follows from either match.

The refreshed `docs/images/jev-battle-preview.gif` uses Hard-run source seconds
202–210: 1440×800, 6 fps, 8 seconds, 48 frames. Full decode passed and sampled
frames show changing reserve/army node details with Follow decisions enabled.
The operator's finished migration moved the requested asset out of the former
`documentation/images/` directory. [Media provenance](../media/README.md)
records the exact source and reproduction command; the older MP4 is unchanged.

## Offline Step 225

The resumed candidate remains isolated in
`worktree_build-step-225-jdi-contracts`: five files implementing immutable
offline preparation, typed offline scoring and an explicitly unverified CLI.
Its focused 11 tests, Ruff and strict mypy (834 source files) passed. The full
suite finished with **3406 passed, 1 failed, 13 skipped, 2 deselected**. The same
recovery-branch assertion failed in isolation on its unchanged gameplay source.
The candidate is preserved and pushed at **`84c8525`**, unaccepted and unmerged.
No execution authority, paid campaign or Step 226/227 implementation was added.
The frozen review input is under local `.build-step/review-225-offline-r4/`;
**zero review calls were made** because the mechanical prerequisite failed.
This does not reset the earlier three failed rounds or consume another review
batch. The developer's evidence is in that worktree's
`.build-step/dev-report-offline-resume.md`.

The build-step contract says, "If automated gates fail, the developer fixes them
before code reviewers see the diff" (`%USERPROFILE%/.agents/skills/build-step/core.md`,
Review styles / code). The failing recovery test is outside Step 225's five-file
scope. It was not weakened, skipped or relabeled as passing to advance this step.
Its assertion failure and the separate Step 232 actor-order safety finding must
not be treated as interchangeable diagnoses.
