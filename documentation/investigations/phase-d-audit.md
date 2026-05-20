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
