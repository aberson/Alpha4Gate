# Soak-volume statistical robustness investigation

**Status:** OPEN — investigation skeleton, no findings yet.

**Gates:** Phase R (Wilson CIs + SPRT in evolve gates), and at least one
follow-up phase (Phase S+) for additional robustness primitives.

**Branch:** to be cut from master at investigation start.

**Date opened:** 2026-04-26.

## 1. Problem statement

Phase 8 (Linux + Docker headless training) unlocks 2-3× game throughput
per wall-clock hour on a Linux box vs the Windows-only stack. The 2026-04-26
spike-3 measurement showed 2.9× speedup with 0% crash on the Phase 7
smoke gate (DB 610 → 611 in 55s). Multiplied across an 8-hour overnight
soak, this means we're moving from ~30 evolve gate games to ~80-90 gate
games per night.

Today's promotion gates were designed for the small-`n` regime: "≥3/5"
strict majority, raw counts, no confidence intervals. They produce the
null-hit problem (`feedback_evolve_fitness_5game_noise_floor.md`) where
the gate's noise floor scales with `n` rather than tightening as `n`
grows. They also waste wall-clock by playing all `n` games even when the
outcome is decided after game 5 of 30.

This investigation answers: with 2-3× more games available per gate
without paying more wall-clock, which statistical primitives have the
highest payoff, in what order, and what's the wall-clock saved per
generation?

## 2. Existing context

**Plan refs.**

- Master plan Track 10 (proposed): Phase R + flagged Phase S+. Gated on
  this investigation.
- Phase 8 (Linux + Docker CI workflows) — currently shipping Steps
  1-7. Step 7 smoke gate produced the speedup measurement.
  `documentation/plans/phase-8-build-plan.md`.
- Phase 9 evolve gate-reduction plan
  (`documentation/plans/evolve-gate-reduction-plan.md`) — current 2-gate
  pipeline (fitness + regression), thresholds tuned for `n=5-9`.

**Code refs.**

- `src/orchestrator/evolve.py` — `run_fitness_eval`, `run_regression_eval`.
  Threshold logic: `wins >= games // 2 + 1` (strict majority).
- `src/orchestrator/ladder.py` — Elo K=32, ≥20 games per cross-version
  promotion. Wilson interval would replace the raw `wins/games` here too.
- `src/orchestrator/selfplay.py` — `run_batch`, single-game timing.
  Cost data for SPRT economics.
- `bots/v0/data/training.db` — historical game-level data; lets us
  retrofit Wilson / SPRT analysis on past runs to validate the math
  before shipping.

**Memory refs.**

- `feedback_evolve_fitness_5game_noise_floor.md` — null-hit table at
  small `n`. The motivating evidence.
- `project_evolve_2gate_validated.md` — 2-gate pipeline validated;
  promotion rate ~0.53 per generation. Adding a tighter gate must not
  drop this below ~0.40 or the loop stalls again.
- `feedback_verify_primary_source_in_writing.md` — confidence intervals
  must come from primary statistical sources, not LLM recall. Use
  `scipy.stats.binom.ppf` etc. directly; don't transcribe formulas.

**Prior art.**

- Wilson score interval: standard for binomial proportion CIs at small
  `n`. `statsmodels.stats.proportion.proportion_confint(method='wilson')`
  is the canonical impl.
- Sequential Probability Ratio Test (SPRT, Wald 1947): early-stop when
  the LLR crosses a threshold. Used by A/B testing platforms (Optimizely,
  GrowthBook). `scipy.stats` doesn't ship it directly; `numpy` does it
  in ~30 LOC.
- Bayesian gates with beta priors: AlphaStar used this for league
  membership decisions. More flexible than Wilson but adds prior-tuning
  work.

## 3. Investigation scope

**In scope.**

- Define each candidate statistical primitive precisely: Wilson CIs,
  SPRT early-stop, Bayesian gates with beta priors, build-order
  diversity metric (entropy), time-to-win distribution capture,
  per-difficulty WR breakdowns at higher `n`.
- For each primitive, estimate cost (LOC, dependency adds, CI runtime)
  and benefit (wall-clock saved, false-positive rate reduction,
  decision quality lift).
- Retrofit-validate the top 2-3 primitives on at least one past
  evolve soak: would Wilson CIs have promoted/rejected the same
  candidates? Would SPRT have saved how much wall-clock?
- Recommend a phased rollout: which primitive is Phase R, which is
  Phase S, which is "flag for later".
- Identify the gate threshold semantics that change. E.g., today's
  "≥ +10 Elo over 20 games" becomes "Wilson 95% lower bound on Elo
  delta ≥ +10 with `n` chosen by SPRT." Spell out the new contract.

**Out of scope.**

- Implementing any primitive. This investigation produces specs and
  the retrofit-validation table.
- Gate-threshold tuning beyond the retrofit validation. Phase R does
  the tuning.
- Stats for non-evolve gates (e.g., dashboard alerts). Track 10 may
  pick those up later but not in scope here.

## 4. Key questions

1. **What's the Wilson 95% lower bound for "≥ 3 of 5" today?**
   Concretely: at `wins=3, n=5`, Wilson lower 95% is ~0.19 — i.e., we
   can't reject a true win-rate of 19% at the current gate. At
   `wins=12, n=20` (Phase 4 Elo gate), Wilson lower 95% is ~0.39.
   What's the actual false-positive cost of these intervals?
2. **Which primitive saves more wall-clock per generation, SPRT or
   "shift to higher fixed `n`"?** SPRT early-stops decided games but
   pays a fixed planning overhead. Higher fixed `n` is simpler but
   wastes games on already-decided candidates. Math + retrofit data.
3. **Is build-order entropy a useful regression-detection metric?**
   Hypothesis: a promoted version with collapsed build-order entropy
   (single canonical opening) is at risk of mode collapse. Today we
   only catch this at a future regression gate. Cheap to add, cheap
   to ignore if it doesn't correlate.
4. **Should we capture time-to-win distributions, or just mean game
   length?** Distribution gives "won fast vs won slow" which is a
   skill signal; mean alone obscures it.
5. **Does Bayesian gating actually beat Wilson + SPRT at our scale?**
   Bayesian gates are slick on paper but add prior-tuning work.
   Concrete decision: would the prior come from the *parent's* posterior
   (lineage-flavored), or a flat Beta(1,1) (uninformative), and does
   the answer matter at `n=80`?
6. **What changes downstream for the dashboard?** Today's gate panels
   show raw `wins/games`. Wilson interval display needs a CI bar.
   `useEvolveRun.ts` schema would gain `wilson_lower`, `wilson_upper`,
   `sprt_decision` fields.
7. **What's the bare-minimum primitive set that's worth building?**
   If we only ship one primitive, which one? (Preliminary read:
   Wilson CIs alone are higher-value than SPRT alone — they fix
   honest reporting, and SPRT's wall-clock save is moot if the
   reported gates are already statistically loose.)

## 5. Methodology

**Data we need to collect:**

1. **Historical soak retrofit.** Pull every gate decision (fitness +
   regression) from the last 5 evolve runs. For each, recompute
   Wilson 95% lower bound on the win rate. Tabulate: how many would
   have flipped (passed → failed or vice versa)? How many were
   marginal (the new gate would now defer-and-extend rather than
   decide)?
2. **SPRT economics.** Same data set. Simulate SPRT with `α=0.05,
   β=0.05, p0=0.40, p1=0.60`. For each gate, what `n` would SPRT
   have stopped at? Sum the wall-clock saved across the run.
3. **Build-order entropy retrofit.** Pull build-order signatures from
   `data/training.db` for promoted vs rolled-back versions over the
   last 10 promotions. Is there a difference in entropy?
4. **Cost estimates.** LOC for each primitive (Wilson: ~5 LOC if
   `statsmodels` is added; ~15 LOC pure-numpy; SPRT: ~30 LOC; build-
   order entropy: ~20 LOC + DB schema migration).

**Artifacts produced:**

- This document, with §6 "Findings" appended.
- A retrofit table per primitive showing decision-flip rate vs the
  current gates.
- A wall-clock-savings estimate per primitive in
  minutes-per-generation.
- A recommendation: which primitive ships in Phase R, which in
  Phase S, which is flagged.

## 6. Findings

(To be filled by the investigation. Skeleton only.)

## 7. Success criteria

The investigation is **done** when:

- All 7 key questions in §4 have answers grounded in retrofit data,
  not pure analysis.
- Phase R has a specific scope statement: which primitives, which
  gate(s) they replace, what the new threshold contract reads.
- Phase S (and beyond) has a prioritized backlog of remaining
  primitives.
- A migration story for the dashboard is sketched (schema + UI bar
  primitive).

## 8. Constraints

- **Time-box: 0.5-1 day** of focused investigation. The retrofit-
  validation step is the long pole. If `training.db` access requires
  schema work to extract gate decisions, drop to "Wilson only" and
  defer SPRT retrofit.
- **No production code.** Pure-numpy or `statsmodels` analysis only.
  Phase R does the wiring.
- **No new external services.** Frequentist (Wilson, SPRT) and
  Bayesian-with-flat-priors are the design space. No A/B testing
  platform integrations.

## 9. Downstream decisions gated on this

- **Phase R scope.** Specific primitive list + gate-contract change.
- **Phase 9 follow-up sequencing.** If Wilson + SPRT save enough
  wall-clock, the PFSP-lineage gate (already on the backlog) becomes
  cheaper to add (more games per generation budget).
- **Dashboard schema.** `useEvolveRun.ts` updates queue up next to
  the cacheKey bump rule from `feedback_useapi_cache_schema_break.md`.
- **Time budget table in master plan.** Phase R replaces "open-
  ended" with a concrete day estimate after this investigation.

## Appendix — Cross-references

- Phase 8 build plan (Linux throughput): `documentation/plans/phase-8-build-plan.md`
- Evolve gate-reduction plan: `documentation/plans/evolve-gate-reduction-plan.md`
- Null-hit memory: `feedback_evolve_fitness_5game_noise_floor.md`
- Win-probability investigation (separate stats axis): `documentation/investigations/win-probability-forecast-investigation.md`
