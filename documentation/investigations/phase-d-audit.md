# Phase D Step D.1 — `reward_rules.json` audit

**Source file:** `bots/current/data/reward_rules.json` (resolves to `bots/v13/data/reward_rules.json` via `bots/current/current.txt`).
**Total rules:** 63 (all `active: true`; 0 inactive).
**Date:** 2026-05-19.

This document is the authoritative migration manifest for Phase D Step D.4. D.4
mechanically reads `category == "a"` to identify build-order rules to extract into
trajectory files. Every rule whose tagging was non-obvious is itemized below with
explicit rationale.

## Category definitions (per Phase D plan §D.1)

| Tag | Name | Definition |
|---|---|---|
| `a` | build-order | `game_time_seconds` paired with structure/tech-existence (positive or negative). The (a) set is what D.4 will mechanically migrate. |
| `b` | tactical | Predicates on `army_supply`, `enemy_army_supply_visible`, engagement, combat, scouting, threat detection. |
| `c` | economy | Predicates on worker counts, gas/mineral saturation, expansions, probe counts. |
| `d` | other | Everything else. Default for ambiguous. Includes rules with no `requires` block, generic counters, supply-cap penalties, meta-state penalties, and structure-count predicates not gated by time. |

## Per-category counts

| Category | Count |
|---|---|
| (a) build-order | 7 |
| (b) tactical | 27 |
| (c) economy | 12 |
| (d) other | 17 |
| **Total** | **63** |

## (a) Build-order rules (7)

These are the rules D.4 will mechanically migrate to trajectory files. Pattern:
one of `condition`/`requires` is `game_time_seconds`, the other is a
structure-existence or tech-progression predicate.

| id | condition | requires | rationale |
|---|---|---|---|
| `tech-progress` | `robo_count >= 1` | `game_time_seconds < 360` | Time-gated structure existence (robo). |
| `forge-built` | `forge_count >= 1` | `game_time_seconds < 300` | Time-gated structure existence (forge). |
| `too-few-gateways` | `gateway_count < 4` | `game_time_seconds >= 240` | Time-gated negative structure existence — "by T you should have N gateways". Mechanically equivalent to a positive build-order target with an inverted sign. |
| `tech-progress-tight` | `robo_count >= 1` | `game_time_seconds < 300` | Tighter time gate, same structure (robo). |
| `tech-progress-strong` | `robo_count >= 2` | `game_time_seconds < 480` | Time-gated structure-count threshold (2× robo). |
| `no-upgrades-late` | `upgrade_count == 0` | `game_time_seconds >= 360` | Time-gated negative tech progression. Spec includes tech existence; `upgrade_count` is tech state. See borderline note below. |
| `defensive-batteries` | `battery_count >= 1` | `game_time_seconds >= 240` | Time-gated structure existence (shield battery). |

### Borderline note: `no-upgrades-late`

This is the only (a) rule that targets `upgrade_count` rather than a building
count. The spec text says "structure/tech existence", which we read as
including tech upgrades (`+1 Attack`, `+1 Armor`, etc.). D.4 will need a
trajectory-target shape that supports an `upgrade_count` predicate — the
other six (a) rules all reference building counts. If D.4 cannot represent
the upgrade-count target in its schema, this rule should remain in `rewards.py`.

## (b) Tactical rules (27)

Predicates on army, engagement, scouting, threat detection. Not migrated by
D.4 (build-order signal only).

| id | condition.field | requires.field | rationale |
|---|---|---|---|
| `scout-early` | `game_time_seconds` | `has_scouted` | Scouting predicate (not structure existence) — borderline a/b, classified (b). See borderline notes. |
| `defend-rush` | `enemy_structure_near_base_early` | `army_supply` | Combat readiness vs early threat. |
| `army-buildup` | `army_supply` | — | Army threshold. |
| `army-ratio` | `army_stronger_than_enemy` | — | Engagement comparison. |
| `react-to-rush` | `is_defending_rush` | — | Threat response state. |
| `map-awareness` | `enemy_structure_count` | — | Scouting (enemy structure visibility). |
| `early-scout-tight` | `game_time_seconds` | `has_scouted` | Scouting predicate — see borderline. |
| `attack-state-mid` | `current_state == attack` | `game_time_seconds` | Strategic state choice (combat). |
| `attack-state-late` | `current_state == attack` | `game_time_seconds` | Strategic state choice (combat). |
| `passive-army-idle` | `current_state != attack` | `army_supply` | Idle army penalty (engagement). |
| `defend-too-long` | `current_state == defend` | `game_time_seconds` | Strategic state penalty. |
| `defend-with-army` | `current_state == defend` | `army_supply` | Engagement state + army. |
| `defend-with-strong-army` | `current_state == defend` | `army_supply` | Engagement state + army. |
| `aggressive-finish` | `current_state == attack` | `army_supply >= 60` | Engagement state + army. |
| `early-attack-timing` | `current_state == attack` | `game_time_seconds` | Engagement state. |
| `army-worker-balance` | `army_supply >= worker_count` | `game_time_seconds` | Compound army-vs-economy comparison. Classified (b) because the threshold IS `army_supply` (combat readiness). |
| `army-scaling-25` | `army_supply >= 25` | — | Army threshold. |
| `army-scaling-40` | `army_supply >= 40` | — | Army threshold. |
| `passive-late-penalty` | `army_supply < 10` | `game_time_seconds` | Army threshold. |
| `low-army-4min` | `army_supply < 20` | `game_time_seconds` | Army threshold. |
| `low-army-6min` | `army_supply < 30` | `game_time_seconds` | Army threshold. |
| `low-army-8min` | `army_supply < 40` | `game_time_seconds` | Army threshold. |
| `attack-state-early` | `strategic_state == ATTACK` | `game_time_seconds` | Strategic state choice. |
| `army-oversupply-idle` | `current_state != attack` | `army_supply >= 40` | Idle army penalty. |
| `army-oversupply-idle-late` | `current_state != attack` | `army_supply >= 60` | Idle army penalty. |
| `enemy-visibility-midgame` | `enemy_structure_count >= 3` | `game_time_seconds` | Scouting (enemy structure visibility). |
| `enemy-visibility-late` | `enemy_structure_count >= 5` | `game_time_seconds` | Scouting (enemy structure visibility). |

### Borderline note: `scout-early` / `early-scout-tight`

Both pair `game_time_seconds` with `has_scouted`. By the spec's literal wording
("AND `requires` checks structure/tech existence"), scouting is NOT structure
existence — it's a tactical reconnaissance predicate. D.4's trajectory file
shape is "at time T, you should have N of structure X built"; "at time T, you
should have scouted" is a different shape (it's a tactical action, not a
construction target). Classified (b).

### Borderline note: `army-worker-balance`

This is a compound predicate (`army_supply >= worker_count`) and could
arguably belong in (b) tactical (army-based) or (c) economy (worker-based).
Classified (b) because the gate is the combat-readiness comparison, not a
worker-saturation threshold.

### Borderline note: `enemy-visibility-*` and `map-awareness`

These reward enemy-structure visibility. They are scouting outcomes, classified
(b) tactical per spec definition ("scouting" is a (b) predicate).

## (c) Economy rules (12)

Worker counts, gas/mineral saturation, expansion timing.

| id | rule | rationale |
|---|---|---|
| `worker-saturation` | `worker_count >= 22` | Worker threshold. |
| `expand-on-time` | `base_count >= 2` (time-gated) | Expansion. Borderline a/c — see note. |
| `mineral-floating-moderate` | `minerals > 500` | Resource saturation. |
| `mineral-floating` | `minerals > 1000` | Resource saturation. |
| `mineral-floating-severe` | `minerals > 2000` | Resource saturation. |
| `mineral-floating-extreme` | `minerals > 3000` | Resource saturation. |
| `gas-floating` | `vespene > 500` | Resource saturation. |
| `excess-workers-50` | `worker_count > 50` | Worker count. |
| `excess-workers-65` | `worker_count > 65` | Worker count. |
| `worker-production` | `worker_count >= 16` (time-gated) | Worker count + early-game gate. |
| `three-base-economy` | `base_count >= 3` (time-gated) | Expansion. |
| `three-base-early` | `base_count >= 3` (time-gated) | Expansion. |

### Borderline note: `expand-on-time`, `three-base-economy`, `three-base-early`, `worker-production`

All four pair `game_time_seconds` with an expansion or worker count
(`base_count`, `worker_count`). They could plausibly be (a) build-order since a
Nexus IS a structure and `base_count` is structure-existence. The reason we
classify them (c) and NOT (a):

- Build-order trajectories (D.4 / Step D.2) are about TECH/PRODUCTION
  structures — gateways, robos, forges, batteries, cannons, cyber cores. The
  example trajectories in D.2 (`4-gate-aggression`, `robo-colossus`) are
  named after these. `base_count` and `worker_count` are economy fundamentals
  every build shares; they're not what distinguishes one build order from
  another.
- The Phase D plan's category definitions list "expansions" and "worker counts"
  explicitly under (c) economy.

If D.4 wants to extract base-count timing as a trajectory target, it should
add (c)-tagged rules whose `condition.field == base_count` AND a
`game_time_seconds` requires, but D.1 reads the category-list definition as
authoritative and tags these as (c).

## (d) Other rules (17)

Everything not cleanly (a)/(b)/(c). Includes meta-state penalties, supply-cap
infrastructure penalties, time-less structure counts, and compound resource
+ supply predicates.

| id | rule | rationale |
|---|---|---|
| `no-supply-block` | `supply_used == supply_cap` | Generic state; no requires. |
| `upgrade-started` | `upgrade_count >= 1` | Tech state without time gate — fails (a) "time-gated" half. |
| `upgrade-progression` | `upgrade_count >= 3` | Tech state without time gate. |
| `upgrade-progression-strong` | `upgrade_count >= 5` | Tech state without time gate. |
| `gateway-efficiency` | `gateway_count >= 3` | Structure count without time gate. |
| `gateway-scaling-6` | `gateway_count >= 6`, `base_count >= 2` | Structure-vs-structure ratio; no time gate. |
| `gateway-scaling-8` | `gateway_count >= 8`, `base_count >= 3` | Structure-vs-structure ratio; no time gate. |
| `too-few-gateways-5` | `gateway_count < 5`, `base_count >= 2` | Structure-vs-structure ratio; no time gate. (Sibling `too-few-gateways` IS time-gated → (a).) |
| `natural-defense-cannons` | `cannon_count >= 2`, `base_count >= 2` | Structure-vs-structure; no time gate. |
| `late-game-stall` | `current_state == late_game` (time-gated) | Meta-state penalty (not engagement). |
| `expand-stall-penalty` | `current_state == expand` (time-gated) | Meta-state penalty (not engagement). |
| `low-supply-cap-4min` | `supply_cap < 60` | Supply cap is a derived metric. See borderline. |
| `low-supply-cap-6min` | `supply_cap < 80` | Supply cap. See borderline. |
| `low-supply-cap-8min` | `supply_cap < 100` | Supply cap. See borderline. |
| `low-supply-cap-12min` | `supply_cap < 140` | Supply cap. See borderline. |
| `low-supply-cap-15min` | `supply_cap < 180` | Supply cap. See borderline. |
| `supply-block-with-minerals` | `minerals >= 300` AND `supply_used == supply_cap` | Compound resource+supply. |

### Borderline note: `low-supply-cap-*min` (5 rules)

These pair `game_time_seconds` with `supply_cap < N`. `supply_cap` is set by
Pylons + Nexus, which ARE structures, so one could argue these belong in (a)
as "time-gated negative pylon-existence". The reason they're (d):

- `supply_cap` is a derived aggregate, not a discrete structure count. A
  trajectory file representing "build 4 pylons by 4:00" is cleaner than
  "supply_cap should be >= 60 by 4:00", because the former is what the bot
  actually commands and the latter is a downstream metric.
- D.4 will mechanically migrate (a) rules into trajectory targets. If
  `supply_cap < N` migrates, D.4 has to invent a Pylon-count interpretation,
  which isn't a mechanical operation.
- These rules describe "you should have enough supply infrastructure by T",
  which is a build-order CONCERN, but the predicate shape doesn't fit D.4's
  trajectory target shape. They stay in `rewards.py` as supply-infrastructure
  penalties.

If D.4's trajectory schema later supports a `pylon_count` or `supply_cap`
target, these can be re-tagged (a) in a follow-up.

### Borderline note: `gateway-scaling-6`, `gateway-scaling-8`, `too-few-gateways-5`, `natural-defense-cannons`

All four are structure-vs-structure ratios (e.g., 6+ gateways per 2+ bases)
without a `game_time_seconds` gate. They fail the "time-gated" half of (a)
and aren't tactical/economy either. Classified (d). D.4 will not migrate
them; they remain as `rewards.py` shaped-reward signals tied to
infrastructure density rather than absolute timing.

The contrast with `too-few-gateways` (no `-5`) is illustrative:
- `too-few-gateways`: `gateway_count < 4` AND `game_time_seconds >= 240` → (a).
- `too-few-gateways-5`: `gateway_count < 5` AND `base_count >= 2` → (d).

The time gate is the deciding feature.

### Borderline note: `late-game-stall`, `expand-stall-penalty`

Both pair `current_state == X` with `game_time_seconds`. They could superficially
look like (b) tactical (current_state predicate) but they target meta-states
(`late_game`, `expand`) rather than engagement states (`attack`, `defend`).
Classified (d) as "strategic-pacing penalties not tied to combat or build
choices".

## What D.4 will do with this audit

D.4 reads `category == "a"` from the JSON to identify rules to extract into
trajectory files at `bots/current/data/build_orders/<label>.json`. For each
(a) rule, D.4:

1. Computes the implied trajectory step (e.g., `tech-progress` → "robo by 6:00").
2. Removes the rule from `reward_rules.json` (or flips `active: false` —
   D.4 will specify).
3. Adds the equivalent target to the relevant trajectory file.

D.4 should consult this doc's "Borderline note: `no-upgrades-late`" before
migrating `no-upgrades-late`, since it's the only (a) rule with an `upgrade_count`
target rather than a building count.

## D.4 migration outcome

**Date:** 2026-05-20. **Backup:** `bots/v13/data/reward_rules.pre-phase-d-20260520-0020.json`
(byte-for-byte copy of `reward_rules.json` as it existed at the START of D.4 — all
seven (a)-tagged rules still `active: true`). The backup exists so the §7
kill-criterion restore is a single `cp` away.

**Weight derivation.** Per plan §D.4 "weight derived from reward magnitude":
divide each rule's `|reward|` by the median magnitude across the (a) set
(`0.006`) and clamp to `[0.25, 4.0]`. The resulting weights are:

| rule | reward | weight |
|---|---|---|
| `tech-progress` | 0.005 | 0.83 |
| `tech-progress-tight` | 0.008 | 1.33 |
| `tech-progress-strong` | 0.012 | 2.0 |
| `forge-built` | 0.005 | 0.83 |
| `too-few-gateways` | -0.01 | 1.67 |
| `defensive-batteries` | 0.006 | 1.0 |
| `no-upgrades-late` | -0.005 | 0.83 |

### Migrated (5 of 7) — `active: false` in `reward_rules.json`

| rule id | trajectory file | target tuple | notes |
|---|---|---|---|
| `tech-progress` | `robo-colossus.json` | `("build", "roboticsfacility", 210, weight=2.0)` (EXISTING target — covered by widened tolerance) | All three `tech-progress*` rules are "robo by T" — they collapse into the two `roboticsfacility` rows in `robo-colossus.json` (one at 210s, one at 420s) under the file's widened `tolerance_seconds: 60`. The 0.83 weight derived above is dominated by the trajectory's pre-existing 2.0 weight on the first robo row; collapsing into the stronger weight is consistent with §D.4 "rules referencing the same structure at different timings collapse into one target". |
| `tech-progress-tight` | `robo-colossus.json` | `("build", "roboticsfacility", 210, weight=2.0)` (same row as above) | Collapsed into the same `roboticsfacility@210` target. With `tolerance_seconds: 60`, the 300s rule-time deadline maps onto the 210s target via the widened window (`|exec_time - 210| <= 60` admits all robo builds finishing between 150s and 270s; the rule's own deadline at 300s is the "by T" bound the trajectory's edit-distance scoring penalizes when missed). |
| `tech-progress-strong` | `robo-colossus.json` | `("build", "roboticsfacility", 420, weight=2.0)` (NEW row) | "2× robo by 8:00" → second roboticsfacility row at 420s. Edit-distance scoring naturally requires both robo targets to be matched, modeling the 2-robo requirement. |
| `forge-built` | `robo-colossus.json` | `("build", "forge", 240, weight=0.83)` (NEW row) | Forge is a defensive/upgrade tech. Adding to `robo-colossus.json` (the macro-game trajectory) rather than `4-gate-aggression.json` (the timing-attack trajectory that does not tech to upgrades). Weight 0.83 from the table above. |
| `too-few-gateways` | `4-gate-aggression.json` | Migrated by flipping `active: false`. The 4-gate-aggression trajectory already includes 4 gateway rows by t=230 (lines 5, 8, 9, 10). | The 4-gate-aggression trajectory already includes 4 gateways by t=230, so 4-gate bots are still rewarded for hitting that target. Bots running other trajectories (e.g., robo-colossus, which has only 2 gateways at t=300) previously received this penalty regardless of build choice; now they do not. **This is an intentional per-build differentiation** — globalized gateway-count penalties don't fit a build-aware trajectory reward model. Confirmed by inspection: `4-gate-aggression.json` builds gateway #1 @ 30, #2 @ 170, #3 @ 200, #4 @ 230. Any deviation from the chosen trajectory pays the per-gateway weight cost in edit-distance scoring; deviation FROM the rule's hard global threshold no longer pays a penalty when the trajectory itself doesn't require it. No new row in `4-gate-aggression.json`; no row in `robo-colossus.json` either (intentional). |

### NOT migrated (2 of 7) — left `active: true` in `reward_rules.json`

| rule id | rationale |
|---|---|
| `no-upgrades-late` | Predicate is `upgrade_count == 0` AND `game_time_seconds >= 360`. The trajectory schema (`_schema.json`) targets `(action, target, time_seconds, weight)` triples where action ∈ {build, train, research} — it does NOT support an "absence of N upgrades" target. Migrating would require either inventing a `research`-target-as-absence-predicate (not the schema shape) or shipping a fixture upgrade name. Per plan §D.4 "Rules that don't fit cleanly (no `requires` block, or non-structure predicate) go into a notes section." D.1's Borderline note already flagged this for D.4. Stays in `rewards.py`. |
| `defensive-batteries` | Predicate is `battery_count >= 1` AND `game_time_seconds >= 240`. While shieldbattery IS a structure (so the predicate technically fits), neither existing trajectory targets it, and the per-step recommendation in the D.4 spec was option (a) — "skip migration, leave rule active in rewards.py" — because defensive infrastructure is a meta-game decision (cheese-defense reaction) not a build-order milestone. Adding shieldbattery to `robo-colossus.json` would force trajectory-following bots to build a battery when they may not need one. Stays in `rewards.py`. |

### Trajectory file modifications

`bots/v13/data/build_orders/robo-colossus.json`:
- `tolerance_seconds`: 30 → 60 (per plan §D.4 "tolerance_seconds widened to cover the spread" for the collapsed tech-progress rules; 60s admits the 210s robo target as a match for executed robos finishing between 150-270s, which spans the 5:00 / 6:00 rule thresholds with margin).
- Added row 9 (after `warp_gate_research@230`): `{"action": "build", "target": "forge", "time_seconds": 240, "weight": 0.83}`.
- Added row 14 (new last row): `{"action": "build", "target": "roboticsfacility", "time_seconds": 420, "weight": 2.0}`.
- Row count: 12 → 14.

`bots/v13/data/build_orders/4-gate-aggression.json`: unchanged. The `too-few-gateways` rule is already covered by the four existing gateway rows.

### Pre-audit of `tests/test_rewards.py`

Per plan §D.4 Done-when: scanned `tests/test_rewards.py` for assertions whose
reward magnitude depends on a now-`active: false` (a) rule.

| test | hit | resolution |
|---|---|---|
| `TestMilitaryRewards.test_tech_progress_fires` | YES — sets `robo_count=1, game_time_seconds=300.0` and asserts `reward > base`. With `tech-progress` set `active: false` and `tech-progress-tight`/`-strong` also inactive, the delta vanishes; assertion would fail. | Updated to use a matched-state-delta comparison against the BACKUP rules file (loaded into a second `RewardCalculator` instance). The new shape: assert that under the BACKUP file, `delta > 0` (proves the rule WAS migrated cleanly); under the current file, `delta == 0` (proves it's no longer in the per-step reward path). Per memory `feedback_reward_test_baseline_drift`, this is a real regression catcher rather than a silenced assertion — if a future change re-enables `tech-progress` without intent, the "should not fire" half of the test fails immediately. |

All other tests in `tests/test_rewards.py` either reference (b)/(c)/(d) rules
(unaffected) or test the `RewardCalculator` mechanism itself (unaffected).
`_state()` does include `robo_count=0` and `forge_count=0` as defaults but no
assertion magnitude relies on them.

