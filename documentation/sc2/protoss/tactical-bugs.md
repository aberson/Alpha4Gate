# Tactical bugs backlog (T.1–T.12)

Tactical bugs surfaced during Phase B Step 5 eval games — about commands,
micro, target priority, and structure placement. Not observation-spec
work (which is what Phase B itself is); these are tactical fixes that
block clean Phase B win-rate measurement because games drag out due to
passivity / bad placement / idle armies.

Handle via `/improve-bot --self-improve-code` or standalone `/build-step`
fixes — these are not formal master-plan phases.

**Provenance:** This block was extracted from the
`alpha4gate-master-plan.md` Phase B section on 2026-04-19 as part of the
plan/build-doc cleanup. The items remain numbered T.1–T.12 to keep
external references stable.

## Resolved

### T.1 — Soften max-supply ATTACK override (PARTIAL)

**Status:** Hard override shipped as commit `0bc2f90` (#134).
`MAX_SUPPLY_ATTACK_THRESHOLD=180` forces ATTACK regardless of
DEFEND/FORTIFY/EXPAND/OPENING state. **Deliberately heavy-handed for now —
get it working, tune later.**

**Problem with the current fix:** At higher difficulties (4-5), a legitimate
defensive stand (e.g., enemy doom-drop into main while own army is across
the map at 180+ supply) would be incorrectly preempted into ATTACK, losing
the defender's advantage. The 180-supply check fires even when defending
is the correct play.

**Softer solution candidates (pick one when tuning):**
- Require `army_supply >= enemy_army_supply_visible * k` before ATTACK
  fires, so max-supply doesn't override when outgunned in the engagement
  area.
- Add a cooldown: override fires only if `supply_used >= 180` has held
  for N seconds, preventing flip-flop with a visible enemy raid.
- Scale the threshold by difficulty (180 at diff 3, 190 at diff 4, 195
  at diff 5).
- Replace with a "production saturation" signal: override when no new
  units can be produced (all warp-gates on cooldown + all production
  full) AND supply at cap — captures the actual "waste" condition
  without false-firing.

**When to tune:** After re-eval at diff 3 confirms win-rate hold, before
pushing to diff 4-5. The hard fix is safe for diff 1-3 where opponents
rarely doom-drop.

### T.2 — Low-ground bleeding (rally below ramps) ✅

**Status:** SHIPPED as commit `c5bd90d` (#138).

Reactive detection instead of elevation awareness: when army centroid
moves < 2.0 tiles AND HP drops for >= 3.0 seconds, force attack-move on
enemy main. Same primitive as `FINISHER_SUPPLY` override. Reuses
`_should_reissue_attack_to_position` from T.7. `_bleeding_since` timer
resets unconditionally after commit fires.

Elevation-aware rally-point selection (proactive version) deferred — the
reactive fix addresses the observable bad behavior directly.

### T.5 — Attack-walking regression still live ✅

**Status:** SHIPPED as commit `e0ae944` (#135). `_run_micro` now
unconditionally attack-moves in all combat states since it's only
dispatched when state is ATTACK/DEFEND/FORTIFY/LATE_GAME.

Original symptom: units moved past enemies instead of attack-moving
(known bug from `feedback_attack_walking_vs_moving.md`). Re-observed
2026-04-17 during Phase B eval. When T.4 (target priority) lands, the
issued command must remain `.attack()` not `.move()`, closing the gap.

### T.6 — Hybrid DEFEND override too aggressive (ROOT CAUSE of passivity) ✅

**Status:** SHIPPED as commit `65d854c` (#136).

The `neural_engine.py::predict` hybrid-mode override fired
unconditionally whenever `enemy_army_near_base=True`, forcing DEFEND
even when PPO was 97.8% confident on ATTACK with overwhelming supply.
This was THE root cause of the max-supply passivity bug — the earlier
180-supply override (T.1) was a band-aid that only kicked in above 180.

New logic: override only fires when:
- Enemy near base AND
- Not a trivial raid (enemy_vis >= 8 or hidden near base) AND
- Can't safely counterattack (army < 12 OR army < enemy_vis * 1.5)

Otherwise trust PPO. Constants imported from `DecisionEngine` to prevent
drift. "Suppressed" log moved to DEBUG to prevent per-tick INFO spam.

**Found via:** log-reading during eval, not code review —
`feedback_check_logs_during_debug.md`.

### T.12 — Re-validate Archon morph fix in hybrid mode (DEBUG ONLY)

**Status:** Debug instrumentation shipped 2026-04-18 at
`bot.py::_produce_army` morph branch. First diagnostic run was done in
**rules** mode at diff 3 (simpler, removes the policy variable while
isolating the engine-level morph dispatch).

Once the root cause is identified and the fix lands, the morph behaviour
must be re-validated in **hybrid mode** (`--decision-mode hybrid
--model-path bots/v0/data/checkpoints/v3.zip --no-claude`) to confirm
the PPO policy doesn't suppress HT warp-ins or starve the gas economy
enough to skip the morph branch in realistic deployment. Archon count
must be non-zero across a 5-game hybrid sample before we consider the
fix shipped.

## Open

### T.3 — Tech structures placed on low ground

**Observed (2026-04-17):** Twilight Council and Forge placed on low
ground outside the main base ramp, with no expansion or defensive
structures nearby. Creates free-kill targets for enemy raids — losing a
Twilight Council resets the tech path and is game-ending at higher
difficulties.

**Rule of thumb the bot should follow:**
- **Pylons** — low ground is fine (cheap, power grid coverage, vision).
- **Tech structures** (Twilight, Robotics Bay, Cybernetics Core, Forge,
  Templar Archives, Fleet Beacon) — place inside the main base perimeter
  OR adjacent to an established expansion with cannons. Never alone on
  low ground.
- **Gateways / Robotics / Stargates** — generally inside main,
  occasionally at natural for warp-in proxy, but only when defended.

**Candidate fixes:**
- Add a `structure_placement_priority` map: each building type gets a
  preferred placement zone (main / natural / proxy / any-powered).
  Default placement logic respects the zone.
- Placement query: before committing to a low-ground pylon-powered spot
  for a tech structure, check if any townhall is within N tiles. Reject
  if not.
- Look at `build_manager.py` / wherever `build_structure` or
  pylon-adjacent placement search lives.

**Related observation:** Repeated "red placement location" error
messages in the log suggest the placement search is retrying invalid
spots. May be a symptom of the same bug (trying to place tech on an
already-taken low-ground spot) or a separate retry-storm issue worth
grepping for.

**Re-observed (2026-04-18, 6:23 game time):** Screenshot shows entire
production complex (multiple Gateways + CyberCore + Robo) built on the
low ground *below* the ramp. User note: "the main buildings you want to
protect on the high ground or next to the natural." This confirms T.3 is
still live post T.2/T.5/T.6/T.7 fixes. Three stacked "Can't find
placement location" errors visible → retry-storm likely firing again.

### T.4 — Target priority: engage producers, not their spawns

**Observed (2026-04-17):** Against Broodlord composition, bot units
engage the ground-spawned Broodlings instead of the flying Broodlords
that produce them. Broodlings respawn continuously as long as the
Broodlord lives; killing the Broodlord collapses the entire threat.
Wasting DPS on Broodlings is a positive-feedback loss (more time spent
on Broodlings = more Broodlings spawn).

**General principle:** For "producer + spawn" enemy unit pairs, target
the producer. Cases:
- Broodlord (air) → produces Broodlings (ground). Kill Broodlord.
- Carrier → produces Interceptors. Kill Carrier.
- Swarm Host (burrowed) → spawns Locusts. Kill Swarm Host.
- Warp Prism (air) + Zealots (warped in) → kill Warp Prism first.

**Candidate fixes:**
- Target priority table in `micro_controller.py` or `tactics/`:
  `{UnitTypeId.BROODLORD: priority_high, UnitTypeId.BROODLING: priority_low}`,
  `{UnitTypeId.CARRIER: priority_high, UnitTypeId.INTERCEPTOR: priority_low}`.
- When selecting attack targets, filter the enemy list by
  `priority_high` first; fall back to closest-enemy only if no
  high-priority targets are in range.
- Unit capability constraint: ground-only units (Zealot, Stalker without
  blink, Immortal) can still hit Broodlord since it's low-ground air —
  but ensure anti-air units (Stalker, Phoenix, Void Ray) prioritize
  flying producers.

### T.7 — Units in melee don't deal damage proportional to count

**Observed (2026-04-17, game time 13:16):** Large Protoss army
intermixed with Zerg in full melee. Units visibly engaged but the
engagement drags — bot loses what should be winning fights. User
described it as "move, attack move, don't really attack."

**Candidate root causes (need log evidence to diagnose):**
1. **Target priority (duplicate of T.4):** units engage nearest trash
   (Roaches, Zerglings, Banelings) instead of
   Broodlords/Ravagers/priority targets.
2. **Command churn:** `_run_micro` runs every tick, issues attack-move
   every tick, interrupting the unit's current attack animation before
   damage lands. Check: does DispatchGuard (shipped 2026-04-14) apply to
   unit commands or only build commands?
3. **Target switching:** units chase closer enemies mid-attack, never
   finishing a kill.

**Next step:** grab log snippet around game time 13:16 from an eval run
to see which code path is firing. Likely needs T.4 fix + possibly
command-rate-limit on attack commands.

### T.8 — Production structures placed inside the mineral line

**Observed (2026-04-18, ~6:10 game time):** Screenshot shows a Robotics
Facility, Stargate, Forge, and Gateway warp-gated directly on top of
mineral patches. Probes are trying to path through the buildings to
mine. A "Can't find placement location" error is visible. Nexus is
hidden behind the production complex, and the main Robotics Facility
(selected) is sitting where Probe pathing expects crystals.

**Impact:** worker mining throttled, pylon/production placement futures
constrained, cannons/batteries have no room when they're actually
needed, worker rally confused.

**Rule:** only defensive structures belong in the mineral line —
**PhotonCannon** and **ShieldBattery**. Everything else (Gateway,
CyberneticsCore, Forge, Robotics Facility, Stargate, Nexus, Pylon except
worker-rally Pylons) must be placed in the back of the main or at
natural choke points / behind the Nexus.

**Candidate root causes:**
1. `bot._build_structure()` falls back to "any powered Pylon radius"
   when primary placement fails — includes the Nexus-adjacent Pylon
   that covers the mineral line.
2. No keep-out zone around mineral patches / vespene geysers for
   non-defensive structures.

**Next step:** find the placement routine, add a filter that excludes
build positions within N tiles of any `self.mineral_field` or
`self.vespene_geyser` for structures ∉ {PhotonCannon, ShieldBattery,
Pylon-for-expansion}. Probably in `bot.py._build_structure` or
`macro_manager` → `MacroDecision` execution path.

### T.9 — Natural expansion undefended; fall-back point softer than main

**Observed (2026-04-18, ~5:48 game time):** Screenshot shows main with a
cluster of production structures (acceptable fortification given the
stacked gateways/robo/stargate near the Nexus), while the natural
(bottom-right Nexus) has only probes mining. No cannons, no shield
batteries, no units parked. If the bot is pushed off the main or needs
a pull-back, the natural is the logical fall-back — but it's softer
than the main.

**Impact:** a timing attack that forces a retreat has nowhere safe to
retreat to. User called out that the natural is "usually a better
fall-back point than the main" — Protoss tradition is to fortify the
natural because the main ramp funnels but the natural is where the army
lives.

**Rule:** once the natural Nexus is up, queue **at least one**
ShieldBattery next to it, and add a PhotonCannon covering the mineral
line / rally path when Forge is ready. Don't touch the main's existing
fortification logic — per the user, it's "sort of fine for now." Scope
is purely: seed a minimum defense at base #2.

**Candidate touch points:**
1. `fortification.py` — if it runs per-base, extend to include natural
   with a lower target count than main.
2. `macro_manager._check_shield_batteries` — if it only considers main,
   widen to include any Nexus owned ≥ 45 seconds.

**Next step:** read `fortification.py` +
`macro_manager._check_shield_batteries` to see whether the current
logic is base-indexed or hard-coded to main. Simplest fix: add a
"natural defense" check that fires once the 2nd Nexus has been standing
for ~45s and no ShieldBattery is within 10 tiles of it.

### T.10 — Army idles inside enemy base instead of finishing the game

**Observed (2026-04-18, 12:42 game time):** Screenshot shows a full
Protoss army (Stalkers + Zealots + at least 2 Archons visible) standing
dispersed among enemy Zerg structures in the Zerg main base. Supply
85/86, 4270 minerals / 478 gas floating. Three "Can't find placement
location" errors stacked on the left — bot is still trying to place
buildings (probably a Pylon to break supply cap) instead of ordering
the army to clean up.

User note: "It could win in a few seconds if it would attack walk
around."

**Relationship to T.5:** T.5 (`e0ae944`) made `_run_micro` always
attack-move in combat states. This screenshot suggests the problem is
not "move vs attack" — the units are simply **not being issued any
order this tick**. They are inside attack range of buildings/units but
stand idle. Either:
1. `_run_micro` is not running (strategic_state not in
   ATTACK/DEFEND/FORTIFY/LATE_GAME despite visually being in combat),
   or
2. `_run_micro` runs but targets are filtered out (e.g., T.4-style
   priority picks an out-of-range target), or
3. DispatchGuard is rate-limiting attack re-issues from T.7 too
   aggressively and no new command arrives when the current target
   dies — the unit goes idle.

**Test path:** pull the game log around 12:42 and grep for
`Hybrid override:`, `strategic_state`, and the unit-command dispatch
lines. Compare to a winning game's log at the same phase.

**Rule:** if units are in vision of enemy structures AND have not
received a command in the last N ticks AND are idle, issue attack-move
on the nearest enemy structure — regardless of strategic_state.

**Next step:** identify which of the 3 root causes above fires, fix
accordingly. Candidate is (3) — DispatchGuard window may be too wide
when the held target has just died.

### T.11 — Split army: engaged force + still-rallying force in parallel

**Observed (2026-04-18, 7:52 game time):** Screenshot shows a Protoss
force (Stalkers + Sentries) engaging Zerg Roaches at the top-right,
while a second group (Zealots) is standing on a rally point
lower-middle, not moving. Game shows 14 in F2 (probably the engaged
squad). Two forces fight at partial strength against the same enemy, so
trades are worse than they need to be.

**Rule (guiding principles §12 coherence, §10 engagement):** when any
owned army unit is in combat, subsequent unit production from Gateway /
WarpGate / Robo must **join the engagement**, not continue to a stale
rally point. Rally targets should be invalidated by a combat event.

**Candidate touch points:**
1. `army_coherence.py` — probably already computes a center-of-mass;
   see whether rally points derive from it.
2. `commands/executor.py` — when a new unit warps in during combat, is
   it issued an attack-move toward the fight, or just inherits a stale
   rally?
3. `bot.py._produce_army` after `wg.warp_in(...)` — maybe issue an
   immediate `newest_unit.attack(engagement_target)` before returning.

**Next step:** read `army_coherence.py` and the warp-in path; determine
whether stragglers get an explicit "move to fight" order or rely on the
WarpGate rally. If the latter, add a post-warp hook that routes new
units to the nearest ally-in-combat.
