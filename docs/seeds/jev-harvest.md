# Seed: Jev harvest — general-purpose ideas from the closed Jev proof of concept

**Status:** SEED, 2026-11-11. Decision D14 closed Jev as a completed proof of concept.
Its unfinished steps (207, 208, 213, 222, 223, 225–240, J2.0, JV.9, JV.10) were removed from
the [master plan](../plans/master_plan.md); their original text remains in
`docs/archived-plans/jev-*.md` and `docs/archived-plans/master_plan.md`. This file keeps the
ideas worth carrying into the main bot.

**Operator note:** "Jev was a POC. We can incorporate it in our bot moving forward."

## Ideas and where they go

| ID | Idea | From | Destination |
|---|---|---|---|
| H1 | Emergency defense takes precedence over strategic intent; never "recover" with zero combat units; an attack with no eligible group or target falls back in the same tick | 232 | ES-G tactical work (T.1 max-supply attack override, T.9 undefended natural) |
| H2 | Prove outcomes by inspecting each unit's actual orders after the command layer, not the selected branch. Jev's audit found recovery reported success while eight earlier ATTACK orders survived. | 232 audit | ES-G checks for T.10 and T.11 (idle and split armies); test design |
| H3 | Record decision opportunities and separate accepted, effective and executed decisions; compare offline with one answer removed | 233 | Decision lanes in the [strength viewer](strength-viewer.md); PPO evaluation |
| H4 | Bounded, richer context: trends, fight history (≤1 sample per game-second, 31 samples), enemy sighting ages; vision loss is not death | 234 | PPO observation features; rules-engine threat memory |
| H5 | Macro choices (tech / expand / hold) separate from army intent, each with its own slot and expiry | 235 | [PPO direction](ppo-direction.md) question 1c; Phase E action space |
| H6 | Matched experiments: identical source in every arm, sealed seeds, intention-to-test scoring, paired discordant counts, Clopper-Pearson intervals, a scripted control arm, no result-driven reruns, new seeds after a source change | 236–240 | ES-B scoreboard; ES-F promotion gate |
| H7 | Freeze exact source bytes before evaluation; no second authoring on promotion; one assessment per candidate fingerprint | 225 | ES-E (promote the tested code) |
| H8 | Weighted references (parent 50 / original 25 / distinct 25); promotion panel of sealed seeds across three races and two difficulties; no lower race or difficulty result | 225, master §8 | ES-F |
| H9 | Nightly parent controls, one per race; a disagreement invalidates the window instead of being averaged away | master §8 | ES-A drift detection (the SC2 98310 incident) |
| H10 | Durable cost ledger: reserve worst-case cost before every paid call, in integer units | 226 | A cost cap for Claude calls in unattended evolve runs |
| H11 | Count only results bound to real process exits and run identity, not caller claims | 227 | Evolve result authenticity (ES-F) |
| H12 | One hypothesis per candidate in an isolated allowlisted worktree; evaluator, tests and budget protected from candidate edits; no-op or malformed proposals launch no games; correctness tests run before games | 230 | Evolve dev-apply sandbox (EJ.2's null-diff screen covers part of this) |
| H13 | Sequential controller: graceful stop after the current game, resume without duplicates, stale-lease handling | 227–229 | EH.7 run lock; ES scoreboard runner |
| H14 | Dashboard-first launch: the viewer is ready on the exact run before SC2 starts; never guess the newest run | 213, master §8 | Strength viewer |
| H15 | Observer Rush: combined SC2 and decision view, follow-decisions camera, caster, graceful stop, optional recording | JV.9 | Strength viewer |
| H16 | Failure classes: execution, information, composition, strategy, infrastructure | 213, 223 | ES-B loss labels; proposal evidence (ES-H) |
| H17 | Grouping from v13 (centroid, staging, hysteresis); avoid "unseen enemy means attack" and random thresholds | JV.10 | Already in v13. Keep as a review checklist for army-logic changes. |
| H18 | Unbuilt J2 facts: max health and shield, regroup at 40% durability, available abilities, mineral contents, worker saturation beyond 16, an assimilator at the natural | J2.0 | Candidate evolve proposals; PPO features |
| H19 | The decision graph owns legality and execution while a model chooses among legal branches | Jev architecture | PPO question 1f: action masking over graph-legal branches |
| H20 | Question families (strategy, army mode, expansion response, production mix) as distinct decision points | J2 | Candidate PPO heads (question 1) |

## Bringing Jev into the main bot

These are three routes, not commitments; each needs its own planning.

1. **Legality layer for PPO (H19 + H5):** a graph-style feasibility check turns PPO's
   choice into a masked choice among executable options.
2. **Inspectability (H3 + H15):** the strength viewer's decision lanes reuse Jev's
   explain-what-happened approach.
3. **Diverse sparring partner:** frozen Jev v1 (rush) and v2 policies as extra opponents
   for the ES panel (D13 diversification). Feasibility is unknown: Jev runs against built-in
   AI through its own runner, not the version self-play harness.

## Preserved, unmerged Jev work (not deleted)

| Branch / worktree | Commit | Content |
|---|---|---|
| `build-step-225-b0cc7700` (sibling worktree `worktree_build-step-225-b0cc7700`) | `5eb0367` | Original 225 attempt and its audit |
| `build-step-225-jdi-contracts` | `324d5f0` | Bounded 225 continuation (NEEDS-WORK review) |
| `build-step-232-jdi` | `00bc515` | 232 candidate after the third review |
| `fix/jev-order-repair` | `f027bc2` | Recovery-lifecycle repair (NEEDS-WORK review) |

Deleting these branches or the worktree is a separate operator decision.
