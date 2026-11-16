# Seed: Phase ES — evolve for strength

**Status:** PLANNED, 2026-11-11. `/plan-feature` turned this seed into master plan steps ES.1–ES.13 with the design in §17; the plan is authoritative where they differ. Planning decisions: Simple64/96/128 maps, a lean Stage 0, a posterior panel gate, and headless WSL evolution with live evolution deprecated (D16). The six open questions below are resolved in §17.6.
**Decisions:** D1–D6 and D8 in [master_plan.md §16](../plans/master_plan.md#16-direction-reset-and-decision-register-2026-11-11).
**Pre-registered claim:** a promoted version beats the current champion on a fixed,
external scoreboard, not only its immediate parent in self-play.

## Why this phase exists (evidence gathered 2026-11-11)

- **No promotion since 2026-05-07.** `data/selfplay_results.jsonl` holds 1,759 self-play
  games from April–May 2026 and 46 from June through October.
- **No proof v13 beats v0.** Against VeryHard, v0 won 2 of 5 (2026-04-27) and v13 won
  4 of 10 (2026-10-14). Both are about 40%; the samples cannot separate them.
- **The Elo ladder cannot rank versions.** In `data/bot_ladder.json`, v13's 25 games are
  18 against itself, 5 against v12 and 2 others. It has never played v0, v3 or v7.
  Ratings are seeded from the parent and chained, so v13's −24 is not a strength measurement.
- **Promotion is noise-prone.** A candidate only has to beat its parent in a Protoss mirror
  on Simple64 by a simple majority of 5–9 games. A no-better candidate passes that 50% of the
  time ([noise-floor investigation](../investigations/evolve-judging-alternatives.md)).
- **What was tested is not what shipped.** Fitness scores a candidate tree that is then
  deleted; stack-apply re-authors the improvement from its text
  ([round-2 investigation](../investigations/evolve-restructure-round2.md) §3).
- **Older versions cannot play.** SC2 build 98310 (2026-10-14) crashes v0–v12 about 45
  game-seconds in. The fix (`fc66956`, #364) is on `fix/burnysc2-730`, unmerged.
- **Fitness cannot see the real game.** Evolve fitness is bot-vs-bot only: frozen Protoss
  versions on one `--map`, with no built-in AI and no Zerg or Terran opponents.

## Scope

### ES-A — SC2 patch compatibility (D2)

- Merge `fc66956` (#364). Install `orchestrator.sc2_compat` in every tree used as an
  opponent: at least v0, v3, v7, v11, v12 and v13.
- Patching frozen trees needs an explicit infra-only exception (gameplay bytes unchanged),
  like the reference-only exception in master §10. Decide this in redline.
- Land the uncommitted working tree first (BL.1). `[evo-auto]` commits sweep staged files
  until EH.8 lands, and `docs/wiki/` is currently staged.
- After any SC2 update, run a short smoke per anchor tree. Parent-control games (Jev harvest
  H9) catch environment drift like the 98310 incident.

### ES-B — The scoreboard (D1; absorbs S.3 and R.1)

- **Opponents:** built-in AI at the stage's difficulty, all three races.
- **Maps:** Simple64 plus two ladder maps, if they are installed (open question 2).
- **Sample:** proposed 20 games per race per difficulty. Report the win rate and a Wilson 95%
  interval per race and overall.
- **Anchors at Stage 0:** v0, v3, v7, v11, v12, v13.
- **Sealed schedule** (Jev harvest H6): fixed seed list per stage and no result-driven reruns.
  Draws, timeouts and crashes count as losses (intention-to-test) and are also reported
  separately.
- **Evidence:** save a replay of every loss. No replay is saved today. Label each loss with
  the Jev failure classes (H16): execution, information, composition, strategy or
  infrastructure.
- **Output:** one JSON per scoreboard run, a markdown summary and a small dashboard surface.
- **Cost:** about 2.2 minutes per game at Hard on Simple64 (50 games in 1h51m, April 2026).
  Ladder maps and harder AIs run longer; measure the real cost at Stage 0.

### ES-C — Staged targets (operator decision, 2026-11-11)

Each stage's opponent becomes the next stage's regression floor.

| Stage | Opponent (all three races) | Proposed pass rule |
|---|---|---|
| 0 Baseline | Harder (6) and VeryHard (7), every anchor | none — establishes the numbers |
| 1 | VeryHard (7) | ≥60% overall, no race below 50% |
| 2 | CheatVision (8) | ≥50% overall, no race below 40%; VeryHard stays ≥60% |
| 3 | CheatMoney (9) | ≥50% overall |
| 4 | CheatInsane (10) | ≥50% overall |
| 5 | Beyond built-in AI | frozen-panel Elo plus external opponents; ties to D13 diversification |

The exact rule (point estimate or Wilson lower bound) is fixed in ES planning and
pre-registered before each stage's first run.

### ES-D — Champion challenge (operator decision, 2026-11-11)

If Stage 0 ranks an older anchor above v13:

1. The challenger plays v11, v12 and v13: 10 games each, alternating seats.
2. It must win at least 6 of 10 against each one. If it were only equal, the chance of
   passing all three is about 5%. It must also be no worse on the scoreboard.
3. If it passes, repoint `bots/current` to it through the existing pointer and rollback path,
   and resume evolution from it.

### ES-E — Promote the code that was tested (D3; ER.2 moved earlier)

- Keep each scored candidate's exact tree or patch and promote those bytes. Stop re-authoring
  winners from their text.
- For multi-winner stacks: apply the exact patches in order and re-test the stacked result, or
  cap stacks at one winner until exact-patch stacking exists.
- Jev harvest H7: freeze source bytes before evaluation and make one assessment per candidate
  fingerprint.
- ES planning decides whether this retires EI.7 (patch capture), EI.9 (stack size) and
  EI.10 (stack model), all parked under D8.

### ES-F — Panel-gated promotion (D4; absorbs EJ.7, EJ.8, ER.3, ER.4)

- Promote only if the candidate:
  - beats its parent, and
  - is not worse on a reduced scoreboard panel at the current stage's difficulty, and
  - is not worse against the ancestor anchors (ER.4).
- `--fitness-mode both` and `--panel-floor` already exist, default off. Register anchors with
  `scripts/baseline.py add`, then extend the panel to include built-in AI opponents.
- Replace the strict-majority fitness bar (50% null pass) with a pre-registered bar (ER.3).
  Keep EJ's posterior rollback.
- Weighted references (Jev harvest H8: parent 50 / original 25 / distinct 25) are one option.

### ES-G — Revalidate tactical bugs (D5; T.1–T.12, T.2.1)

- Check each T-bug against v13 using the Stage 0 replays: reproduces, fixed or unclear.
- Fix reproducing bugs directly (one change, verified on the scoreboard), or seed them into
  the evolve pool with the replay as evidence.
- Operator flags from the viewer ([strength-viewer seed](strength-viewer.md)) are a third
  source of findings.

### ES-H — Proposals from real evidence (D8: EI.1–EI.4, EI.6, smaller EI.13)

- Per-game logs, trace parsing, proposer provenance and parent win counts, as already
  specified in the EI steps.
- Add scoreboard loss summaries, failure-class labels and operator flags to the proposal prompt.

### ES-I — Run safety (D6)

EH.3 and EH.6–EH.10 stay in the master plan. ES needs EH.3, EH.7 and EH.8 before any
unattended run.

## Proposed order

1. ES-A.
2. ES-B Stage 0, plus ES-D if an older anchor wins.
3. ES-G alongside EH.3/EH.6/EH.7/EH.8.
4. ES-E, ES-F and ES-H.
5. Evolution runs judged by the scoreboard, advancing through the ES-C stages.

## Measurement guardrails

These follow the workspace rule in `dev/.claude/rules/measurement-validity.md`.

- Score the production artifact: the real `bots/vN` tree through its real runner.
- Abort on any fallback configuration.
- Calibrate before trusting numbers: a known-good anchor (v13 at Easy) must score near 100%,
  and a deliberately broken tree must lose.
- Pre-register each stage's one-sentence claim.

## Open questions for planning and redline

1. Infra-only exception for installing the compatibility shim in frozen trees.
2. Which ladder maps to use, and whether they are installed under `Maps/`.
3. Built-in AI build type: pinned or random.
4. Exact pass rule per stage.
5. Machine-time budget per stage: scoreboard cost against evolve time.
6. Substrate: Windows (build 98310) and headless Linux (an older 4.10 package) are different
   game versions, so the scoreboard needs one substrate for comparable numbers.
