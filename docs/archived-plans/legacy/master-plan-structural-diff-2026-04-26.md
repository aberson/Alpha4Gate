# Master plan structural diff — 2026-04-26 pivot

> **PURPOSE:** Review-only document. Captures the proposed structural
> changes to `alpha4gate-master-plan.md` from the 2026-04-26 plan-shape
> conversation (Utility / Observable split + 4 new tracks). Once
> approved, pass 2 applies these changes to the master plan, archives
> `selfplay-viewer-plan.md`, creates 4 scoped build-plan stubs, and
> updates memory.
>
> **Status:** DRAFT — awaiting user review.

## 1. The pivot in one paragraph

The master plan's vision section assumes a single substrate that both
*evolves* and is *watched*. The 2026-04-24 selfplay-viewer block
(SC2 server caps at 2 API clients) made this implicit assumption
contradictory: the watching path can't share the SC2 box with the
evolving path. The pivot resolves it by splitting into a **Utility
Stack** (training, evolution, headless, no viewer) and an **Observable
Stack** (one-off exhibition matches with full-map vision, decoupled
from rated training). Phase 8 (Linux + Docker) becomes a Utility-Stack
substrate; the previously-blocked viewer plan becomes the seed for an
expanded Observable-Stack track.

## 2. Vision section — proposed rewrite

Current paragraph (master plan §Vision):

> Alpha4Gate is an **autonomous improvement platform** whose first
> domain is SC2 Protoss. The platform already plays, evaluates, trains,
> promotes, and rolls back models unattended, with full dashboard
> transparency (phases 1–4.5 shipped). The next era breaks the "stuck
> at difficulty 4–5" ceiling by:
> 1. Validating the pending `feat/lstm-kl-imitation` PR on the current stack.
> 2. Versioning the entire bot stack — every improvement snapshot is a
>    self-contained `bots/vN/` directory…
> 3. Layering AlphaStar-inspired PPO upgrades…
> 4. Keeping the existing daemon/evaluator/promotion/rollback loop running…

Proposed addition (after item 4, new § "Two stacks, one platform"):

> ### Two stacks, one platform
>
> As of 2026-04-26 the platform splits its substrate into two stacks
> with disjoint operational requirements:
>
> - **Utility Stack** — training, evolution, intra-version promotion,
>   cross-version Elo, ladder, headless Linux + Docker batch runs. Two
>   SC2 clients per match. No viewer. Optimised for game throughput
>   per wall-clock hour. Phases 0-9, B/D/E/F, 6/7, R, P, O, N, Q, H/I/J
>   live here.
> - **Observable Stack** — exhibition matches between any two seed
>   models drawn from the version pool, watched via full-map exhibition
>   viewer. Decoupled from rated training (uses `disable_fog=True` or
>   replay-stream rendering, both training-unsafe by design — by design
>   because Observable matches don't feed PPO). One off-the-pool match
>   at a time. Phases K/L/M live here.
>
> The two stacks share infrastructure (registry, snapshot, manifest,
> dashboard) but operate on disjoint code paths and disjoint SC2
> processes. A Utility soak and an Observable exhibition can run on
> the same box concurrently iff the Utility soak is on Linux/Docker
> (Phase 8) and the Observable exhibit is on Windows.

## 3. Track structure — proposed addition

Current track table:

```
Track 1 — Validation   [Phase A]    on current src/alpha4gate/                                  ✅
Track 2 — Versioning   [0–5]        subprocess spike → bots/v0/ → registry → self-play → ladder → sandbox  ✅
Track 5 — Operational  [9, 6, 7]    NEXT — Phase 9 (improve-bot-evolve) is the priority; substrate-not-just-phase
Track 3 — Capability   [B, D, E]    per-version improvements inside bots/current/**             after Phase 9
Track 4 — Capability-F [F]          deferred; only if B/D/E insufficient                        after Phase 9
Track 6 — Multi-race   [G]          post-Phase-6 operational; Zerg then Terran via per-race bots/<race>_v0/ stacks
```

Proposed addition (4 new tracks below the existing 6):

```
Track 7 — Directed Practice  [H, I, J]   mini-games substrate → custom Protoss maps → role decision (gate/reward/scorecard)
Track 8 — Observable         [K, L, M]   pool organisation + metadata → exhibition viewer revival → NL-prompt seed selector
Track 9 — Capability research [N, O, P, Q]  win-prob + give-up → Hydra HRL → distillation/pretraining → harvest-engineer skill
Track 10 — Statistical robust [R, S+]   Wilson CIs + SPRT (Phase R) → backlog of further primitives
```

All four new tracks are append-only. No existing track is renumbered.

## 4. Phase pointer-stubs — to insert after Phase G in master plan

Each follows the existing pointer-style pattern from Phases B/D/E/F/G.

---

### Phase H — Mini-game substrate (PySC2 + custom-map runner)

**Track:** Directed Practice. **Status:** Investigation-blocked.
**Prerequisites:** Phase 5; Investigation
[mini-game-role-investigation.md](../investigations/mini-game-role-investigation.md)
must conclude before scope finalises.

> **Build detail TBD.** Build plan stub at
> `documentation/plans/phase-h-build-plan.md` once investigation
> concludes.

**Goal:** Add a mini-game runner primitive to `src/orchestrator/` that
can launch a PySC2 canonical mini-game with a snapshot of the bot's
relevant capability code, record results, and return a structured
score. Substrate phase — does not change rated play.

**Scope summary (will firm up post-investigation):** PySC2 dependency,
mini-game launcher, score schema, DB column for per-version mini-game
results, smoke-gate on 1 PySC2 mini-game end-to-end.

**Tests:** TBD.

**Effort:** ~2-3 days (investigation-dependent).

**Validation:** A `bots/v0/` mini-game run on `MoveToBeacon` returns a
score within published PySC2 baselines.

**Gate:** TBD per investigation outcome.

**Kill criterion:** Investigation concludes mini-games are scorecard-
only and the substrate cost is not justified by the analytical value;
defer Phase H indefinitely.

**Rollback:** Delete the runner module + DB column migration; PySC2
dep stays as no-op.

---

### Phase I — Custom Protoss mini-games

**Track:** Directed Practice. **Status:** Future.
**Prerequisites:** Phase H. Investigation §5.3 produces the priority-
ordered candidate list.

**Goal:** Author 2-5 custom SC2 maps targeting specific Protoss skill
deficiencies (blink-stalker micro, attack-concave, force-field choke,
etc.). Race-extension flag throughout — when Phase G ships Zerg, this
track gets per-race subdirs.

**Effort:** ~1 day per map (SC2 editor) + ~0.5 day reward-shape
per map.

**Kill criterion:** Investigation §6 concludes custom maps don't
justify cost vs PySC2 alone.

---

### Phase J — Mini-game role decision (gate / reward / scorecard)

**Track:** Directed Practice. **Status:** Investigation-blocked.
**Prerequisites:** Phase H; mini-game-role investigation conclusion.

**Goal:** Wire the chosen role(s) into the evolve loop. If scorecard,
slot into per-version manifest. If gate, slot into `run_fitness_eval`
or as a new pre-gate. If reward, slot into curriculum pretraining.

**Kill criterion:** None — phase shape *is* the decision; if no role
ships, Phase J collapses into a Phase H sub-step.

---

### Phase K — Observable pool organisation + metadata

**Track:** Observable. **Status:** Future. **Prerequisites:** Phase 4
(ladder) — the pool is the version registry plus tagging.

**Goal:** Make the version registry searchable for "fun matchups."
Add registry metadata: themed labels (Skytoss, Carrier-rush, Ground-
army), notable-moments (first promotion, beat-vN-9-0), build-order
signature, cross-version WR matrix (already exists via ladder).

**Effort:** ~1-2 days. Registry change + DB migration + dashboard
view.

**Validation:** A pool-pick API call returns a sensible matchup given
text-or-tag input.

**Kill criterion:** Registry metadata gets stale faster than it gets
useful (more than 50% of new versions land without tags). Switch to
auto-tagging from manifest fingerprints.

---

### Phase L — Exhibition viewer revival (`disable_fog` single-pane)

**Track:** Observable. **Status:** Replaces archived
[`selfplay-viewer-plan.md`](./selfplay-viewer-plan.md). **Prerequisites:** Phase 4.

**Goal:** Revive the previously-blocked viewer plan with a narrower,
training-decoupled scope: spawn a 2-bot match with `disable_fog=True`
at game-create, embed bot1's window in the existing themed pygame
container, render full-map vision. Three-process observer architecture
is abandoned (server cap of 2 API clients makes it unbuildable);
replay-stream-as-live (Spike C from observer-restriction-workarounds-
investigation §4.2) is on deck for Phase L v2 if `disable_fog`
exhibition feels unfaithful.

**Scope summary:** Refactor `src/selfplay_viewer/` to single-pane;
add `--exhibition` flag to selfplay.py that sets `disable_fog=True`;
explicit "exhibition only — not for rated play" guard.

**Tests:** Unit tests for the single-pane refactor; manual smoke-gate
for visual fidelity.

**Effort:** ~2 days.

**Validation:** A `python scripts/selfplay.py --exhibition --p1 v0
--p2 v0` produces one full-map-vision viewer window; Utility-stack
soak running concurrently is unaffected.

**Gate:** Visual fidelity passes; no Utility-stack regression.

**Kill criterion:** `disable_fog=True` produces visibly broken bot
behavior (bots react to ghost-vision in disruptive ways) that makes
exhibitions unwatchable. Reroute to Spike C (replay-stream-as-live).

**Rollback:** `git revert` the viewer revival; `selfplay-viewer-plan.md`
stays archived; Observable Stack functionally pauses until Phase L v2.

---

### Phase M — NL-prompt seed selector

**Track:** Observable. **Status:** Future. **Prerequisites:** Phase K
(metadata) + Phase L (viewer).

**Goal:** A small Claude wrapper that takes a natural-language prompt
("show me a game where the carrier-build bot loses to a defensive
build") and translates to `(p1, p2, optional win-filter)` from the
metadata-tagged registry, then launches a Phase L viewer with that
matchup.

**Effort:** ~1 day.

**Validation:** Three sample NL prompts each produce a runnable
viewer match.

**Kill criterion:** Metadata is too sparse for NL queries to land
sensibly (>50% of queries fall back to "random"); defer until Phase K
metadata fills in over more promoted versions.

---

### Phase N — Win-probability heuristic + give-up logic

**Track:** Capability research. **Status:** Investigation already
done — see
[win-probability-forecast-investigation.md](../investigations/win-probability-forecast-investigation.md).
**Prerequisites:** Phase 5.

**Goal:** Ship Option (c) heuristic from the win-probability
investigation as the per-step P(win) signal: heuristic formula in
`bots/v0/learning/winprob_heuristic.py`, `win_prob` column added to
`transitions`, INFO log line every 10 decision steps. Pair with a
**give-up trigger** (`RequestLeaveGame`) that fires when
`winprob < 0.05 for 30 consecutive decision steps AND game_time > 8
min` — saves wall-clock on lost games, enables "fold-em" behavior.

**Scope summary:** 5 build steps per win-prob investigation §8 + 1
give-up step.

**Tests:** `tests/test_winprob_heuristic.py` (synthetic snapshots),
`tests/test_give_up_trigger.py`.

**Effort:** ~1 day.

**Validation:** Heuristic separates win-game and loss-game mean
scores by ≥0.10 absolute (already shown in investigation §5.1 at
0.145). Give-up trigger fires <5% of games in winning soaks, ≥30%
in losing soaks.

**Gate:** Both validation criteria.

**Kill criterion:** Heuristic separation collapses on more recent
versions (< 0.05). Pivot to Option (b) classifier per investigation
§4.

**Rollback:** Drop heuristic module + DB column; remove give-up
trigger.

---

### Phase O — Hydra hierarchical PPO

**Track:** Capability research. **Status:** Investigation-blocked.
**Prerequisites:** Phase N (win-prob is a candidate gate signal);
[hydra-hierarchical-ppo-investigation.md](../investigations/hydra-hierarchical-ppo-investigation.md)
must conclude.

**Goal:** Per the user's preference, a *learned* hierarchical PPO
with a meta-controller dispatching among N expert sub-policies
(themes: Skytoss, Ground, Cheese-rush, etc.).

**Scope summary:** TBD per investigation outcome (sparse-gated MoE,
dense-gated MoE, manager-worker, or distill-from-specialists).

**Effort:** Investigation suggests 2-6 weeks depending on chosen
formulation. CPU-only constraint may force "distill-from-specialists"
which is closest to AlphaStar's league + post-hoc distillation.

**Validation:** TBD. Includes a "first 3 days look like X or kill"
gate per investigation §7.

**Gate:** TBD.

**Kill criterion:** Investigation §6 concludes learned hierarchy is
infeasible at our scale → reroute to **Phase O′** (scripted
controller / FSM over StrategicState with tech-tree gates), same
eval contract, much smaller build.

**Rollback:** Delete the Hydra version directory; prior monolithic
versions unaffected.

---

### Phase P — Knowledge distillation + foundational pretraining

**Track:** Capability research. **Status:** Investigation-blocked.
**Prerequisites:** Phase 5;
[knowledge-distillation-pretraining-investigation.md](../investigations/knowledge-distillation-pretraining-investigation.md)
must conclude.

**Goal:** Replace or augment the rule-engine-derived `v0_pretrain`
with a richer teacher (pro replays, Claude advisor distribution,
Hydra-experts distillation).

**Scope summary:** TBD per investigation outcome.

**Effort:** TBD. Pro-replay corpus access is the long pole if not
locally available.

**Kill criterion:** Investigation §6 concludes no licensing-clean
corpus exists; defer Phase P; document the path to data partnership.

---

### Phase Q — harvest-engineer skill

**Track:** Capability research. **Status:** Investigation-blocked.
**Prerequisites:** Phase 5;
[harvest-engineer-skill-scope-investigation.md](../investigations/harvest-engineer-skill-scope-investigation.md)
must conclude.

**Goal:** Reactive skill that ingests external knowledge (a pasted
paper, or a "look into X" suggestion) and produces a scoped
investigation/plan + optional implementation path. Differs from
`/improve-bot-advised` by intake (papers vs game logs) and resolution
(architectural vs code-edit).

**Scope summary:** SKILL.md + sandbox-mode decision + output-artifact
contract. v1 is Mode A only (paste-a-paper); Mode B (investigate-
from-suggestion) is a thin wrapper.

**Effort:** ~1-2 days post-investigation.

**Validation:** Three test runs on different paper inputs each
produce an investigation doc that survives review.

**Gate:** Output quality on three retroactive cases (papers that
inspired existing investigations).

**Kill criterion:** Investigation §6 concludes harvest is functionally
indistinguishable from /improve-bot-advised; recommend extending
advised's intake instead of new skill.

---

### Phase R — Statistical robustness (Wilson CIs + SPRT)

**Track:** Statistical robustness. **Status:** Investigation-blocked.
**Prerequisites:** Phase 9 (gates exist), Phase 8 (throughput unlocks
matter);
[soak-volume-statistical-robustness-investigation.md](../investigations/soak-volume-statistical-robustness-investigation.md)
must conclude.

**Goal:** Replace raw-count gate thresholds with Wilson 95% lower
bound. Add SPRT early-stop in `run_fitness_eval` and
`run_regression_eval`.

**Scope summary:** Wilson interval helper + SPRT helper + gate-
threshold contract update + dashboard schema bump (`useEvolveRun.ts`
cacheKey + new fields).

**Tests:** `tests/test_stats_primitives.py`,
`tests/test_evolve_gates_with_wilson.py`.

**Effort:** ~1-2 days.

**Validation:** Retrofit on past 5 evolve runs shows decision-flip
rate ≤20% AND wall-clock saved ≥15% per generation. (Numbers come
from investigation §5.)

**Gate:** Both retrofit criteria.

**Kill criterion:** Retrofit shows decision-flip rate >30% OR wall-
clock saved <5%; investigation under-promised; revisit primitive
choice.

**Rollback:** Revert gate-threshold contract; primitives stay as
helpers, unused.

**Phase S+ backlog:** build-order entropy metric, time-to-win
distribution, per-difficulty WR breakdowns at high `n`, Bayesian
gates with beta priors. Each is its own future phase, scoped
by investigation §6 backlog.

---

## 5. Decision graph — proposed addition

Existing graph (master plan §Decision graph) ends at "Phase 6 / G / F"
post-Phase-9. Append:

```
                                                                          Phase 9 ✅
                                                                                     │
                                                                     ┌───────────────┼────────────────────┐
                                                                     ▼               ▼                    ▼
                                                          Track 7 (Directed)   Track 8 (Observable)   Track 10 (Stats)
                                                          Phase H → I → J      Phase K → L → M        Phase R → S+

                                                                   (independent of) ↓
                                                                   Track 9 (Research):
                                                                   Phase N (win-prob) → Phase O (Hydra)
                                                                                      → Phase P (distillation)
                                                                                      → Phase Q (harvest skill)
```

Phase N is the only Track-9 phase that is NOT investigation-blocked
(its investigation already shipped). Phases O, P, Q each unblock once
their investigation finishes. Tracks 7 / 8 / 10 are independent of
Track 9 and of each other; capacity-permitting they run in parallel.

## 6. Time budget — proposed additions

Append to the existing master-plan time-budget table:

| Phase | Optimistic | Realistic | Pessimistic |
|-------|-----------|-----------|-------------|
| H | 2 d | 3 d | 1 w (PySC2 integration friction) |
| I | 1 d/map | 1.5 d/map | 3 d/map (SC2 editor learning curve) |
| J | 1 d | 2 d | 1 w (gate tuning is noisy) |
| K | 1 d | 2 d | 4 d (metadata schema iteration) |
| L | 2 d | 3 d | 1 w (`disable_fog` rendering quirks) |
| M | 1 d | 1-2 d | 4 d (NL-prompt brittleness) |
| N | 1 d | 1-2 d | 4 d (give-up trigger tuning) |
| O | 2 w | 4-6 w | 8 w (hierarchical PPO unstable) |
| O′ (fallback) | 3 d | 1 w | 2 w |
| P | 1 w | 2 w | 4 w (corpus access blocker) |
| Q | 1-2 d | 3 d | 1 w |
| R | 1 d | 2 d | 1 w |

Track 7-10 collective optimistic ~3 weeks; realistic ~6-8 weeks;
pessimistic ~14 weeks. (Phase O dominates the spread.)

## 7. What's NOT in this plan — proposed additions

Append to existing "What's NOT in this plan" section:

- **Cron-driven proactive harvest-engineer.** Reactive (Mode A + B)
  ships in Phase Q; cron is deferred until reactive proves out.
- **Replay-stream-as-live exhibition viewer (Spike C).** Phase L v1
  uses `disable_fog`; Spike C is the v2 fallback if v1 feels
  unfaithful.
- **Bayesian gates with informative priors.** Phase R uses Wilson +
  SPRT (frequentist); Bayesian is Phase S+ backlog.
- **Mini-games as a PPO reward shape integrated into rated games.**
  The "reward role" from the mini-game investigation, if recommended,
  applies to *curriculum pretraining* only. Rated full-SC2 game
  reward stays as-is.

## 8. Plan history — proposed entry

Append to master-plan history (top of list, append-only):

> - *2026-04-26* — **Utility / Observable split + four new tracks
>   (7-10).** Plan-shape conversation pivoted the master plan from a
>   single-substrate model to a two-stack model: Utility (training,
>   evolution, headless) and Observable (exhibition, decoupled from
>   rated play). The pivot resolves the latent contradiction surfaced
>   by the 2026-04-24 selfplay-viewer block (SC2 server caps at 2 API
>   clients; viewer + evolve cannot share a substrate). Five new
>   investigations filed:
>   `mini-game-role-investigation.md` (gate vs reward vs scorecard),
>   `soak-volume-statistical-robustness-investigation.md` (Wilson +
>   SPRT under Phase 8 throughput),
>   `hydra-hierarchical-ppo-investigation.md` (learned HRL feasibility
>   on CPU),
>   `knowledge-distillation-pretraining-investigation.md` (replay
>   corpus + Claude distillation),
>   `harvest-engineer-skill-scope-investigation.md` (paper-intake skill
>   v1 scope). Four new tracks added: Track 7 (Directed Practice —
>   mini-games, Phase H/I/J), Track 8 (Observable Exhibition — pool
>   metadata, viewer revival via `disable_fog`, NL-prompt seed
>   selector, Phase K/L/M), Track 9 (Capability research — win-prob +
>   give-up, Hydra HRL, distillation, harvest-engineer, Phase
>   N/O/P/Q), Track 10 (Statistical robustness — Wilson+SPRT,
>   Phase R + Phase S+ backlog). All 4 tracks append-only; no
>   existing phase renumbered. `documentation/plans/selfplay-viewer-plan.md`
>   archived (superseded by Phase L). Hydra design choice: the user
>   committed to *learned* hierarchical PPO over a scripted FSM, with
>   acknowledgment of the difficulty; Phase O′ (scripted fallback) is
>   the kill-criterion reroute.

## 9. Companion changes (for pass 2)

Beyond editing `alpha4gate-master-plan.md`, pass 2 should also:

1. **Archive** `documentation/plans/selfplay-viewer-plan.md` →
   `documentation/archived/selfplay-viewer-observer-plan.md` (it's
   the second viewer plan to be archived; the first 2-screen plan
   was archived 2026-04-24).
2. **Create** four scoped build-plan stubs:
   - `documentation/plans/phase-h-build-plan.md` (PySC2 substrate)
   - `documentation/plans/phase-k-build-plan.md` (pool metadata)
   - `documentation/plans/phase-n-build-plan.md` (win-prob + give-up)
   - `documentation/plans/phase-q-build-plan.md` (harvest-engineer SKILL.md)
3. **Update memory** (after pass 2 lands):
   - New `project_master_plan.md` update reflecting the 4 new tracks.
   - New `feedback_two_stack_split.md` capturing the Utility /
     Observable split as a guiding architectural principle.
   - New `project_observable_disable_fog_v1.md` capturing the
     decision to use `disable_fog=True` for Phase L v1 (with Spike C
     as v2 fallback).
4. **Cross-link** Phase 9 (improve-bot-evolve) to Phase R: the
   gate-reduction plan's PFSP-lineage follow-up benefits from
   Phase R primitives.

## 10. Review questions for the user

Before pass 2, answer these to lock the diff:

1. **Phase L v1 = `disable_fog`** — the spiciest call. Are you OK
   with "exhibition is full-info, by design" framing, or do you want
   me to scope replay-stream-as-live (Spike C) as v1 instead? Cost
   delta: v1 ships in 2 days vs v1 ships in ~1 week.
2. **Phase O kill-criterion timing.** I proposed "first 3 days look
   like X or kill" — is that the right timeline or should the
   investigation set the bar?
3. **Track 9 ordering.** Proposed N → O / P / Q in parallel after
   their investigations land. Alternative: serialize N → O → P → Q.
   Parallel is faster but harder to track. Your call.
4. **Investigation execution order.** Five new investigations are
   filed. If you want to limit to two-at-a-time (operator load), say
   so and I'll propose a sequencing. Default is "any order, run as
   capacity allows."

---

## Appendix — Files touched in pass 1 (this pass)

- **Created:** 5 investigation skeletons in
  `documentation/investigations/` (mini-game-role,
  soak-volume-statistical-robustness, hydra-hierarchical-ppo,
  knowledge-distillation-pretraining, harvest-engineer-skill-scope).
- **Created:** this structural-diff document.
- **Not touched (pass 2 work):** master plan, selfplay-viewer-plan.md,
  build-plan stubs, memory.
