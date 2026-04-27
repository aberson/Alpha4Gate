# Mini-game role investigation: gate, reward, or scorecard?

**Status:** OPEN — investigation skeleton, no findings yet.

**Gates:** Phase H (mini-game substrate), Phase I (custom Protoss maps), Phase J (mini-game role decision).

**Branch:** to be cut from master at investigation start.

**Date opened:** 2026-04-26.

## 1. Problem statement

Mini-games (PySC2 canonical maps + custom Protoss-specific maps like
"blink-stalker micro" and "create-attack-concave") are a way to give the
bot directed practice on isolated skills. The plan-shape conversation
identified three ways mini-games could plug into the evolve loop:

1. **Gate** — every candidate must pass a mini-game suite before earning
   a full-SC2 evaluation. Cheap (3-min mini-game blocks 18-min full
   evaluation), interpretable (fail → specific skill), per-skill /build-step
   to add new gates.
2. **Reward shape** — mini-game scores feed into the policy reward
   function so the bot keeps practicing the skill in regular play.
3. **Scorecard** — mini-game suite runs once per promoted version, results
   stored alongside the manifest, used for analysis but never gates a
   promotion.

The choice is not obvious. The user proposed "gate"; the plan-shape
review surfaced five concrete drawbacks (false negatives, Goodhart,
gate-set rot, brittleness, scorecard signal lost). This investigation
answers which role(s) survive a closer look on this codebase, with this
team, on this evolve cadence.

## 2. Existing context

**Plan refs.**

- Master plan Track 7 (proposed): Phase H/I/J for mini-games. Gated on
  this investigation.
- Phase 9 (improve-bot-evolve) — current evolve loop, 2-gate pipeline
  (fitness + regression) per
  [evolve-gate-reduction-plan.md](../plans/evolve-gate-reduction-plan.md).
  Adding mini-games as a third gate undoes the gate-reduction work
  unless we're explicit about why mini-games are different from the
  composition gate that was just removed.

**Code refs.**

- `src/orchestrator/evolve.py` — `run_fitness_eval`, `run_regression_eval`,
  the gate primitives. New gate(s) would live here.
- `src/orchestrator/selfplay.py` — `run_batch`, `_run_single_game`. The
  full-SC2 game loop. Mini-games would need their own analogous "run a
  short SC2 session on a specific map with specific units" primitive.
- `bots/v0/` — the bot being practiced. A mini-game runner needs to
  invoke the bot's micro / production / scouting code in isolation,
  which the current bot architecture doesn't cleanly support.
- PySC2 (NOT a current dep): `pysc2.lib.maps` ships the canonical
  mini-game suite. Adding it is a `pyproject.toml` change.

**Memory refs.**

- `feedback_evolve_fitness_5game_noise_floor.md` — strict-majority gate
  at small `n` is null-hit-prone. Any new gate has the same risk.
- `feedback_evolve_composition_stack_crash.md` — composition gate was
  removed because it added a third Bernoulli filter without unique
  detection capability. Mini-game gate must demonstrate it doesn't fall
  in the same trap.
- `project_evolve_2gate_validated.md` — 2026-04-24 soak validated the
  2-gate pipeline (2 promotions in 7h 15m). Adding a third gate must
  not undo this.

**Prior art.**

- AlphaStar used mini-games (and their own internal benchmarks) for
  *analysis*, NOT as gates. League promotions were driven by Elo
  alone. Worth understanding why.
- DeepMind's PySC2 paper introduced mini-games as a way to *measure*
  agent skill, not to drive curriculum.
- OpenAI Five used handcrafted reward shaping (analogous to "reward"
  option) but no mini-game gates.

The dominant prior-art pattern is **scorecard, not gate.** This
investigation must give a sharp reason to deviate, or commit to
scorecard.

## 3. Investigation scope

**In scope.**

- Define each role precisely (gate / reward / scorecard) including the
  exact code path, gate threshold semantics, reward integration point,
  scorecard storage schema.
- For each role, enumerate failure modes specific to our codebase:
  evolve-loop wall-clock impact, false-negative rate at our gate
  thresholds, infrastructure cost (new SC2 map files, new bot launch
  modes, new DB schema).
- Cost a 5-skill mini-game suite (3 PySC2 + 2 custom Protoss) under
  each role.
- Identify which mini-games we actually want, in priority order. Tie
  each candidate to a known bot deficiency (e.g., "lost to bunkered
  Marines" → "DefeatRoaches" mini-game, "loses to skytoss" →
  "anti-air-positioning" custom map).
- Decide whether the role is one-of-three or whether scorecard +
  reward are stackable (they are — different injection points).

**Out of scope.**

- Building any mini-game maps. The investigation produces specs, not
  maps.
- Implementing PySC2 integration. This investigation gates Phase H, which
  does the wiring.
- Multi-race mini-games. Phase G concern; flag any per-race differences
  but don't design for them.
- Building a learned win-prob signal that uses mini-game scores as
  features. That's the win-prob investigation's territory.

## 4. Key questions

1. **What's the false-negative rate of a 5-skill gate at p=0.60 per
   skill?** With composition this was ~0.39 expected promotion rate
   per generation; mini-game gate as a *third* filter would
   compound similarly. Is this acceptable?
2. **Which 5 mini-games would actually trigger improvements that
   evolve has been missing?** Tie each to soak data: which past
   evolve-rejected improvements would have *passed* a mini-game gate
   and earned a full evaluation they didn't get?
3. **What's the wall-clock cost per mini-game per candidate?** PySC2
   mini-games are 1-3 min; custom Protoss maps probably 3-5 min. With
   `--pool-size 4` and 5 mini-games, that's 4×5×4min = 80 min
   added per generation. Compare to current ~50 min/generation total.
4. **Can we get the scorecard value without paying gate cost?** I.e.,
   run the mini-game suite ONCE on the freshly-promoted parent (after
   regression) rather than per-candidate. Per-version cost not
   per-candidate cost. This is the "scorecard" framing.
5. **For the reward role, where does it inject?** Per-step reward
   from mini-game performance is hard (the bot isn't playing the
   mini-game during a full SC2 game). Two viable framings: (a) reward
   bonus paid *while practicing the mini-game* (curriculum
   pretraining), (b) reward shaping in full SC2 games keyed to
   mini-game-flavored events ("scored an attack-concave: +5"). (a) is
   simpler.
6. **Are mini-game gates a Goodhart trap?** Concretely: would a "blink
   stalker micro" gate reward survival-via-blink behavior in full
   games where blink isn't optimal? Look for evidence in past evolve
   runs of single-skill optimization eclipsing strategic play.
7. **What if we just used mini-games for the win-prob feature set?**
   I.e., the mini-game scorecard becomes one of the features the win-
   probability classifier looks at. That sidesteps the role question
   by making mini-games an analytical input rather than a control point.

## 5. Methodology

**Data we need to collect, in order:**

1. **Wall-clock measurements.** Run 1 PySC2 mini-game on `bots/v0` end-to-end.
   Measure: setup time (load SC2, spawn bot), play time, teardown
   time. Repeat for one custom-mapped scenario (use an existing
   Simple64 game truncated to 3 min as a stand-in if no custom map
   exists yet).
2. **Past-soak counterfactual.** Pull every evolve run since
   2026-04-22. For each rejected improvement, classify whether it
   *would have passed* a hypothetical 5-skill mini-game gate by
   reasoning from the improvement description. (Manual classification
   — coarse but informative.)
3. **Skill-deficiency mapping.** Read the last 3 soak reports, the
   tactical-bugs doc (`documentation/sc2/protoss/tactical-bugs.md`),
   and the `improve-bot-advised` run notes. Extract a list of
   "skills the bot is bad at." Cross-reference with PySC2 catalog
   and feasible custom maps. Output: priority-ordered candidate list.
4. **Gate-noise simulation.** Re-use the null-hit math from
   `feedback_evolve_fitness_5game_noise_floor.md`. For p=0.60,
   p=0.70, p=0.80 per-skill pass rates and 3-of-5 / 4-of-5 / all-5
   thresholds, compute the gate's expected pass rate and false-
   negative rate.

**Artifacts produced:**

- This document, with §6 "Findings" appended.
- A spreadsheet (or markdown table) of candidate mini-games with
  cost, deficiency-tie, and feasibility.
- A null-hit table for the proposed gate threshold(s).
- A recommendation: scorecard / gate / reward / hybrid + which
  mini-games ship in Phase H.

## 6. Findings

(To be filled by the investigation. Skeleton only.)

## 7. Success criteria

The investigation is **done** when:

- All 7 key questions in §4 have an answer (even if the answer is
  "deferred to phase implementation").
- A specific list of 3-7 mini-games is recommended, with priority,
  cost estimate, and skill-deficiency tie.
- The role recommendation (scorecard / gate / reward / hybrid) is
  made with a 1-paragraph rationale tied to the §4 answers.
- Phase H scope can be drafted from the recommendation without
  further open questions.

## 8. Constraints

- **Time-box: 1 day** of focused investigation. Investigation is
  blocking Phase H/I/J but not blocking Phase 9 / Phase L / Phase N
  work. If the time-box overruns, narrow scope to "scorecard vs gate"
  only and defer reward-role analysis.
- **No new SC2 map authoring.** If custom maps are required for an
  answer, write the spec, not the map.
- **No PPO retraining.** This investigation does not run training cycles.

## 9. Downstream decisions gated on this

- **Phase H scope.** Substrate is "wire PySC2 + a mini-game runner
  primitive." If role = scorecard, runner is per-version-once. If
  role = gate, runner is per-candidate. Different cost / different
  code paths.
- **Phase I scope.** Custom Protoss maps. Ordered list comes from §5.3.
- **Phase J existence.** If §6 concludes "scorecard only", Phase J
  collapses to a Phase H sub-step. If §6 says "gate", Phase J is the
  gate-integration phase. If §6 says "stackable", Phase J is split.
- **Phase 9 follow-up.** If mini-game gate is rejected, the next
  Phase 9 follow-up is PFSP-lineage regression (already on the
  backlog). If accepted, mini-games slot in *before* PFSP-lineage.

## Appendix A — Candidate mini-games (placeholder, to be ordered in §5.3)

PySC2 canonical (Terran-leaning):
- MoveToBeacon
- CollectMineralShards
- FindAndDefeatZerglings
- DefeatRoaches
- DefeatZerglingsAndBanelings
- CollectMineralsAndGas
- BuildMarines

Custom Protoss (require map authoring; flagged for race extension):
- Blink-stalker-micro (3 stalkers vs 6 marines, blink to kite)
- Attack-concave (8 zealots into 4 marines, must surround)
- Force-field-choke (3 sentries, deny enemy reinforcement)
- Storm-clump (1 HT + storm vs 8 zerglings clustered)
- Phoenix-vs-mutas (4 phoenix vs 6 mutas, lift kiting)

## Appendix B — Cross-references

- Win-probability investigation: `documentation/investigations/win-probability-forecast-investigation.md`
- Evolve gate-reduction plan: `documentation/plans/evolve-gate-reduction-plan.md`
- Tactical bugs (deficiency source): `documentation/sc2/protoss/tactical-bugs.md`
- Soak reports (deficiency source): `documentation/soak-test-runs/`
