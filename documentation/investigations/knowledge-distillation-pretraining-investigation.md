# Knowledge distillation + foundational pretraining investigation

**Status:** OPEN — investigation skeleton, no findings yet.

**Gates:** Phase P (knowledge distillation / pretraining build).

**Branch:** to be cut from master at investigation start.

**Date opened:** 2026-04-26.

## 1. Problem statement

The bot's current cold-start is `v0_pretrain.zip`, an imitation-pretrained
PPO checkpoint behavior-cloned from rule-based decisions in
`bots/v0/data/training.db`. This is *self-imitation* — the policy is
seeded from the same rule engine it'll eventually replace. It's cheap and
unblocks RL, but it caps the ceiling at "rule engine plays this well",
which is observably difficulty 3-4 territory.

Two adjacent ideas could break that ceiling:

1. **Pro-replay imitation pretraining.** Cold-start the policy from a
   corpus of human Protoss replays (or community SC2 AI replays from
   stronger bots) instead of from our own rule engine. AlphaStar did
   exactly this — every league agent started from a behavior-cloned
   prior on Blizzard's anonymized human-replay dataset.
2. **Distillation from a foundational model.** The Claude advisor
   (already wired into the bot) makes high-quality strategic decisions
   per game. Distilling Claude's decision distribution into the policy
   head (or a value head) bakes that intelligence into the policy
   without requiring Claude in the inference loop. Adjacent: distilling
   future Hydra experts (Phase O) into a single compact policy.

This investigation answers: which (or which combination) of these is
worth a build phase, given replay-corpus availability, the licensing /
legal posture of using replays, and the cost-benefit at our scale.

## 2. Existing context

**Plan refs.**

- Master plan Track 9 (proposed): Phase P. Gated on this investigation.
- Phase A (LSTM + KL-to-rules + imitation init) — already shipped.
  The KL-to-rules loss is a *light* form of distillation (rule engine
  → policy head). Phase P would extend this to richer teacher signals.
- Phase O (Hydra) — sibling investigation. Distillation may be the
  training mode for Hydra's gate or for compressing experts.

**Code refs.**

- `bots/v0/learning/imitation.py` — current imitation pretraining
  pipeline. Reads `training.db`, fits PPO to rule-engine actions.
  This is where pro-replay or Claude-distilled trajectories would
  inject as an alternative data source.
- `bots/v0/learning/trainer.py` — PPO trainer. KL-to-rules loss
  (`kl_rules_coef`) is wired here. New KL-to-{teacher} losses would
  follow the same pattern.
- `bots/v0/advisor/` — Claude advisor module. Decision logging is
  already in `data/decision_audit.json` (per the master-plan
  `feedback_check_logs_during_debug.md` memory). Per-step Claude
  decisions are NOT currently logged in a distillation-friendly
  format; this is a prerequisite gap.

**Memory refs.**

- `project_phase_a_complete.md` — full-stack imitation-init validated.
  Phase P builds on the same pretraining infra.
- `project_advisor_threading_bug.md` — advisor batch+hybrid mode is
  broken. Live distillation from Claude during a hybrid-mode game is
  blocked until that's fixed. Offline distillation (replay → Claude
  decisions → DB) is unaffected.
- `feedback_higher_tech_army.md` — user wants Archons / Colossus.
  Pro replays would showcase this naturally; the rule engine can't.

**Replay corpus question.**

- **Blizzard's replay archive.** AlphaStar used millions of anonymized
  replays via a Blizzard data-sharing arrangement. This is NOT
  publicly available; access requires a research partnership.
- **Spawning Tool / SC2ReplayStats.** Public replay archives, mostly
  community-uploaded. Variable quality, mixed races. Licensing
  unclear; usage-for-research is informally permitted but not
  contractually granted.
- **AI Arena (sc2ai.net) replays.** Bot-vs-bot replays from the
  competitive ladder. Licensing more permissive (community ladder
  encourages sharing). Skill ceiling: the strongest bots there are
  arguably stronger than us, but not strictly stronger in all cases.
- **Self-generated replays.** From our own evolve loop and ladder.
  No licensing concern, but the ceiling is exactly today's bot,
  which is the original problem.

## 3. Investigation scope

**In scope.**

- Survey the replay-corpus options: Blizzard (likely unavailable),
  Spawning Tool, SC2ReplayStats, AI Arena, self-generated. For each:
  size, race coverage, skill distribution, licensing posture, parse
  cost.
- Survey distillation teachers we have access to: rule engine
  (already used), Claude advisor (logged but format gap), future Hydra
  experts (Phase O dependent).
- Define the data pipeline: replay → parsed decision sequence →
  feature alignment with our 24-dim observation → DB rows → PPO
  imitation loss. Identify gaps and cost.
- Estimate ceiling lift: at a cost of N hours and a corpus of M
  games, what's the realistic delta vs `v0_pretrain`?
- Recommend Phase P scope: pro-replay only / Claude-distill only /
  both / neither.

**Out of scope.**

- Implementing replay parsing. The investigation specs the gap; Phase P
  builds it.
- Negotiating Blizzard data access. If §6 concludes Blizzard is the
  only viable corpus, the recommendation is "defer Phase P and
  document the path to data partnership."
- Distillation-as-Hydra-training-mode. That's Phase O's concern;
  this investigation flags the overlap.

## 4. Key questions

1. **Is there a usable Protoss replay corpus that's licensing-clean
   for our use?** Concrete options + size + race coverage. If only
   Blizzard works, Phase P is gated on a partnership we don't have.
2. **What's the skill ceiling of each available corpus?** A pro-replay
   corpus gives a higher ceiling than self-generated replays; AI
   Arena replays are middle. Concretely: if we cold-start from corpus
   X, what's the realistic difficulty-3 WR after 0 RL cycles?
3. **What's the parsing / feature-alignment cost?** SC2 replays are
   protobuf streams. We need to extract observations matching our 24-
   dim spec at each decision step. PySC2 has replay-parsing utilities
   but our observation spec is custom — adapter cost.
4. **Is Claude-distillation worth the prereq work?** The prereq is
   logging Claude's decision distribution per step (not just the
   advisor recommendation). That's a few-hour change to the advisor
   logger. Then we'd need 50-100 advised-mode games to get a
   training corpus. Cost-benefit vs a pro-replay corpus.
5. **What's the distillation loss formulation?** KL(student ‖
   teacher) or KL(teacher ‖ student)? Auxiliary loss with
   `coef` annealing or hard pretraining? Same KL-to-rules pattern
   as Phase A, or richer (per-action soft probabilities)?
6. **Does pro-replay pretraining still need RL fine-tuning?** Yes —
   imitation alone caps at the corpus skill. The question is how
   *many* RL cycles to recover the win-rate after the imitation reset
   vs the current `v0_pretrain` baseline.
7. **What about race mismatch?** A Terran-vs-Protoss replay teaches
   the policy nothing useful about Protoss-vs-Protoss. Filtering by
   race-matchup needed; corpus size after filtering is the actual
   training-set size.
8. **What's the cold-start regression risk?** Today's `v0_pretrain`
   is *known* to bootstrap RL successfully. A new pretrain that
   fails to bootstrap (e.g., distribution shift between replay
   features and our env) is a 1-2 week setback. Cheap rollback:
   keep both pretrain checkpoints, A/B them.

## 5. Methodology

**Data we collect:**

1. **Corpus availability matrix.** For each candidate (Spawning Tool,
   SC2ReplayStats, AI Arena, self-generated, Blizzard): size, race
   coverage, license, parse-tool maturity. 1-2 hours of web
   research.
2. **PySC2 replay-parsing dry run.** Pull 10 free Protoss-vs-Protoss
   replays from a public source. Try to extract observations matching
   our 24-dim spec. Document what's missing or wrong.
3. **Claude-distillation prereq scope.** Read the current advisor
   logger; determine what changes to log per-step decision
   distributions. Hours-estimate.
4. **Teacher-quality estimate.** For each candidate teacher (rule
   engine, Claude, AI Arena top bot), estimate the WR ceiling. The
   rule engine is known (~75% diff 3 today). Claude advisor in
   hybrid mode hit ~75-100% diff 3 in past advised runs. Pro replays
   are >>>; AI Arena top is comparable to top advised runs.

**Artifacts produced:**

- This document, with §6 "Findings" appended.
- A corpus availability matrix.
- A 1-page Phase P scope with chosen teacher(s), kill criterion, and
  fallback ("if pretrain fails to bootstrap, restore prior
  v0_pretrain in N min").

## 6. Findings

(To be filled by the investigation. Skeleton only.)

## 7. Success criteria

The investigation is **done** when:

- All 8 key questions in §4 have answers. Q1 (corpus availability)
  must be backed by primary-source verification (visit the site,
  read the terms, count files).
- A primary teacher recommendation is made: pro-replay imitation,
  Claude distillation, both, or "neither and here's why."
- A scoped Phase P (or "deferred until corpus access" recommendation)
  is drafted.
- The replay-parsing gap (specifically, the feature-alignment work) is
  estimated in concrete LOC + hours.

## 8. Constraints

- **Time-box: 1 day** of focused investigation. Most of it is web
  research + a small replay-parse prototype.
- **No production code.** The PySC2 parse dry-run is a throwaway
  script; nothing committed.
- **Verify primary sources** for licensing. Per
  `feedback_verify_primary_source_in_writing.md`, do not summarize
  licensing terms from memory or LLM recall — quote the actual page.
- **Race-matchup filter.** Protoss-vs-Protoss only for v1. Race
  extension is Phase G's concern.

## 9. Downstream decisions gated on this

- **Phase P scope.** Teacher choice, corpus, fallback story.
- **Phase O Hydra training mode.** If distillation-from-specialists
  is the chosen Hydra path, Phase P deliverables become Phase O
  prerequisites.
- **Advisor logger upgrade.** If Claude-distillation is recommended,
  a small-but-real advisor change is required. Slots in next to
  `project_advisor_threading_bug.md` outstanding work.

## Appendix — Cross-references

- Phase A (existing imitation pipeline): `documentation/archived/alphastar-upgrade-plan.md`
- Hydra investigation (sibling): `documentation/investigations/hydra-hierarchical-ppo-investigation.md`
- harvest-engineer investigation (papers may surface relevant techniques): `documentation/investigations/harvest-engineer-skill-scope-investigation.md`
- Master plan: `documentation/plans/alpha4gate-master-plan.md`
