# Hydra hierarchical PPO investigation

**Status:** OPEN — investigation skeleton, no findings yet.

**Gates:** Phase O-v2 (learned-controller Hydra). Phase O v1 (scripted
controller) is unblocked and proceeds independently — see §1 reframe.

**Branch:** to be cut from master at investigation start.

**Date opened:** 2026-04-26. **Reframed:** 2026-04-27 — investigation
scope shifted from "scope the build of learned Hydra" to "post-v1,
evaluate whether learned-controller v2 is worth doing given what
scripted v1 taught us."

## 1. Problem statement

The plan-shape conversation introduced two evolutionary species:

- **Monolith** — one policy, differentiated only by which mini-games
  it practiced and architectural mutations.
- **Hydra** — a meta-controller that dispatches to one of N trained
  sub-policies (themed experts: Skytoss, Ground, Cheese-rush, etc.).

The user's initial preference was a *learned* hierarchical PPO
controller. Subsequent conversation (2026-04-27) reframed this:
**ship a scripted controller as Phase O v1 first, then use this
investigation to evaluate whether a learned-controller v2 is worth
the additional cost.**

The reasoning: the expensive part of Hydra is producing the experts
(themed `improve-bot --theme=X` runs are multi-day training
investments per expert). The controller is the cheap part. A scripted
controller answers the prerequisite question — *do two themed experts
even beat one monolith?* — at a fraction of the cost. A scripted v1
also produces the expert-registry / switching-API / debug-inspection
plumbing that a learned v2 would also need, plus a tunable baseline
to A/B against. AlphaStar's "league" was effectively a scripted
matchmaker — strong precedent for ship-scripted-first.

This investigation answers: **after Phase O v1 (scripted) ships and
runs for some validation period, is a learned-controller Phase O-v2
buildable on this codebase, with this hardware (single Windows 11 box,
CPU-only PyTorch), in a defensible time-box, and is its expected
marginal lift over scripted v1 worth the cost?**

The risk is real. Truly learned hierarchies — Options framework,
Feudal Networks, HIRO — have a brittle reputation in the RL
literature, especially without GPU-scale compute. AlphaStar achieved
hierarchical-feeling behavior via *league composition* (many
specialists trained independently, matched by Elo) rather than
*learned hierarchy* (one meta-controller picking sub-policies in real
time). The investigation must address whether anything has changed in
the published literature since AlphaStar, and whether scripted v1's
empirical results suggest the marginal lift is worth chasing.

## 2. Existing context

**Plan refs.**

- Master plan Track 9 (proposed): Phase O. Gated on this investigation.
- Phase E (autoregressive action head, deferred): adjacent — also
  introduces hierarchy at the *action* level (strategic_state →
  target). Hydra is hierarchy at the *policy* level (controller →
  expert policy → expert action).
- Phase F (entity transformer, deferred): orthogonal architectural
  change; could compose with Hydra but doesn't gate it.

**Code refs.**

- `bots/v0/decision_engine.py` — the rule-based "controller" today.
  ACTION_TO_STATE list maps PPO action indices to StrategicState. A
  scripted Hydra would extend this map.
- `bots/v0/learning/neural_engine.py` — current PPO net (MlpPolicy,
  2×128). Learned Hydra means a custom `ActorCriticPolicy` subclass
  with a gating head + N expert heads, sharing or duplicating the
  feature trunk.
- `src/orchestrator/registry.py` — version registry. Hydra "experts"
  could be either separate `bots/<expert>_v0/` versions (closer to
  league) or sub-policies inside one `bots/hydra_v0/` (closer to
  classic MoE).

**Memory refs.**

- `project_phase_a_complete.md` — full-stack LSTM + KL-to-rules +
  imitation-init. The recurrent policy infra is in place; hierarchical
  PPO would build on it.
- `feedback_higher_tech_army.md` — user wants Archons, Colossus,
  tech upgrades. A Hydra "Skytoss expert" + "Ground expert" maps
  cleanly onto this preference.
- `project_master_plan.md` — Phase F (transformer) is the standing
  "deep architectural change." Hydra is a second one. Sequencing
  matters: do Hydra and F compete for the same compute / time
  budget?

**Prior art landscape.**

- **AlphaStar (2019).** League with main agents, main exploiters,
  league exploiters. NOT hierarchical PPO; each agent is monolithic.
  Hierarchy is *implicit* via PFSP matchmaking.
- **Options framework (Sutton-Precup-Singh 1999).** Classical HRL.
  Termination + initiation conditions. Mostly toy domains.
- **Feudal Networks (Vezhnevets et al. 2017).** Manager + workers,
  shared trunk. DeepMind reported wins on Atari but with significant
  tuning.
- **HIRO (Nachum et al. 2018).** Off-policy hierarchical. Two-level.
  Reasonable on MuJoCo; SC2 unproven.
- **Mixture of Experts in RL (Shazeer et al. 2017 was language; the
  RL adaptations are sparser).** Sparse vs dense gating; load-balancing
  loss; expert-collapse failure mode.
- **Hierarchical policy distillation.** Train experts independently
  (cheaper), distill into a gated policy at the end. Less learning
  pressure on the gate during training.

The open question is which of these literatures actually applies to
SC2 strategic decision-making at our scale (Discrete(6) action, no
GPU).

## 3. Investigation scope

**In scope.**

- Survey the four candidate hierarchical formulations:
  1. **Sparse-gated MoE** (one expert per step, learned gate).
  2. **Dense-gated MoE** (weighted blend of expert logits, learned gate).
  3. **Manager-Worker (Feudal-style)** (manager picks goal, worker
     pursues it for K steps, learned termination).
  4. **Distill-from-specialists** (experts trained as separate
     `bots/<expert>_vN/`, gated post-hoc).
- For each, scope the SB3 implementation effort: what subclass tree
  do we extend, what tests break, what's the training-stability risk
  at our scale.
- Identify the failure modes most likely to bite us: expert collapse
  (gate always picks one expert), gate non-stationarity, cross-expert
  reward attribution, frozen-expert vs joint-train decision.
- Decide what "an expert" means in our codebase. Candidates:
  - A **theme**: "Skytoss", "Ground", "Cheese-rush". Defined by a
    soft constraint (reward shaping) during a themed `improve-bot
    --theme=skytoss` training run.
  - A **strategic mode**: "Defending", "Attacking", "Teching". Maps
    onto existing StrategicState.
  - A **build order**: a specific opening tree. Definable today.
- Recommend ONE approach for Phase O v1, with explicit kill criteria.

**Out of scope.**

- Implementing any of the four formulations. This investigation is
  scoping; Phase O does the build.
- GPU support. Stays out of scope per master plan.
- Multi-race Hydra (per-race experts). Phase G concern; flag any
  per-race blockers but don't design.
- Comparing against existing PPO baseline empirically. Phase O
  validation step does that.

## 4. Key questions

1. **Is there published evidence that learned hierarchical PPO beats
   monolithic PPO on a strategic discrete-action domain at CPU scale?**
   Honest answer to this gates the entire phase. If the answer is "no
   public evidence", we ship a *scripted* controller (Phase O′)
   instead and revisit hierarchy when GPU is on the table.
2. **What's the minimum number of experts to justify a hierarchy?**
   2 experts is barely-MoE; 4-6 is conventional; 16+ is large-scale
   MoE territory we can't afford. Recommend a number tied to (the
   answer to question 3).
3. **What does "an expert" mean for us?** Theme, strategic mode, or
   build order? Each has different training cost and different gate-
   collapse risk. The user's "Skytoss expert" example points to
   *theme*; the existing ACTION_TO_STATE points to *strategic mode*.
4. **What's the gate's input?** Same observation as the experts (24
   game features), or a higher-level abstracted view (army comp,
   game time, opponent inferred strategy)? Higher-level is harder to
   define but reduces gate-overfitting risk.
5. **Frozen experts or joint training?** Frozen is cheaper (train
   experts separately, then train just the gate) and lower-risk
   (each expert is a known quantity). Joint is more powerful but
   destabilizes.
6. **How does Hydra interact with evolve?** Is each expert a separate
   `bots/<expert>_vN/`, evolved independently? Or is the whole Hydra
   one `bots/hydra_vN/` that evolves as a unit? The first composes
   with existing Phase 9 infrastructure; the second is a new
   evolution lineage.
7. **What's the rollback story if Hydra fails?** Every other phase
   rolls back to a single prior version. Hydra rollback = "delete
   the gate, run one expert as the policy" is a sensible fallback.
8. **What's the training-data story?** Each expert needs its own
   training trajectories. With 4 experts, training trajectories are
   diluted 4× unless we shape rewards per-expert.
9. **What's the dashboard story?** Today's Decisions tab shows policy
   action probabilities. With Hydra, we'd want gate probabilities
   *plus* per-expert action probabilities. That's a 2-level
   visualization.

## 5. Methodology

**Reading list, in order:**

1. SB3 docs on `ActorCriticPolicy` extension. Specifically: can we
   override `forward` to insert a gating step between feature trunk
   and action head, or do we need a from-scratch policy class?
2. Vezhnevets et al. 2017 (Feudal Networks) for manager-worker
   feasibility on small budgets.
3. AlphaStar 2019 supplementary materials on why they chose league
   over hierarchy.
4. Recent (2023+) MoE-in-RL papers via web search; specifically anything
   on PPO + sparse gating without distillation.

**Data we collect:**

1. **SB3 extension cost.** Hours-estimate to subclass
   `ActorCriticPolicy` for sparse MoE on the 24-feature obs. Does
   `learn()` work without `_setup_learn` overrides?
2. **Per-expert training trajectory cost.** Given current 50
   min/generation evolve cycle, how long to train 4 specialists from
   scratch? From `v0_pretrain`?
3. **Toy validation feasibility.** Could we ship a 2-expert Hydra on
   a simpler distinction (defend-mode vs attack-mode) and measure
   gate utilization before committing to full theme-experts?

**Artifacts produced:**

- This document, with §6 "Findings" appended.
- A 1-page "design-doc" for Phase O v1: chosen formulation, expert
  definition, training plan, kill criteria.
- A literature-survey one-liner for each of the 4 candidate formulations.

## 6. Findings

(To be filled by the investigation. Skeleton only.)

## 7. Success criteria

The investigation is **done** when:

- All 9 key questions in §4 have answers, with the literature
  question (Q1) answered with a citation list.
- Phase O has a chosen formulation with a kill criterion that fires
  *before* a 2-week build runs to completion (i.e., a "first 3
  days look like X or kill" gate).
- A scripted-controller fallback ("Phase O′") is sketched with rough
  scope so we have an alternative if hierarchical PPO is judged
  infeasible.

## 8. Constraints

- **Time-box: 1-2 days** of focused investigation. Reading-heavy
  rather than coding-heavy. Investigation timeline is loose — fold
  into a slow week, not a blocking item — per the 2026-04-27 reframe
  (see §1).
- **Phase O v1 has already shipped or is in flight.** Investigation
  consumes Phase O v1 results: gate-utilization data, win-rate
  delta vs monolith, expert-collapse signals. Do not re-derive these
  from first principles.
- **No PPO code changes.** This is scoping; Phase O-v2 builds if
  recommended.
- **CPU-only.** Any literature finding that requires GPU at training
  time is a hard kill for the cited approach.
- **Honest about novelty.** If the answer is "no one has shipped
  this," say so. Phase O v1 is already a working Hydra (scripted
  controller); v2 must demonstrate marginal lift to justify itself.

## 9. Downstream decisions gated on this

- **Phase O-v2 scope.** Whole phase shape depends on the chosen
  formulation AND on Phase O v1 results. Could be 2 weeks (sparse MoE)
  or 4-6 weeks (joint-trained Manager-Worker), or "do not build."
- **Phase O-v2 deferral.** If §6 concludes scripted v1 is sufficient
  OR learned v2 is infeasible, Phase O-v2 stays deferred alongside
  Phase F. Track 9 capstone item, not active.
- **Track 9 sequencing.** Investigation runs in parallel with Phase N
  (win-prob), Phase P (distillation), Phase Q (harvest-engineer);
  not on the critical path.
- **Multi-race interaction (Phase G).** If Hydra v2 ships per-race,
  G doubles in complexity. Flag.

## Appendix — Cross-references

- Master plan: `documentation/plans/alpha4gate-master-plan.md`
- Phase E (action-level hierarchy): `documentation/plans/phase-e-build-plan.md`
- Phase F (transformer): `documentation/plans/phase-f-build-plan.md`
- Win-probability investigation (gating signal): `documentation/investigations/win-probability-forecast-investigation.md`
- Knowledge-distillation investigation (sibling — distillation is one Hydra training mode): `documentation/investigations/knowledge-distillation-pretraining-investigation.md`
