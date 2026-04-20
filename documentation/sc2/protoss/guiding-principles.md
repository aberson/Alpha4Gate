# StarCraft II Protoss Agent Guide

## 0. Purpose

This document defines high-level guiding principles for a Protoss-playing AI agent in StarCraft II.

Scope:
- Race: Protoss only
- Goal: maximize win probability through stable macro, safe scouting, correct reactions, efficient army control, and low-unforced-error play
- Audience: AI agent, not human beginner

Primary design principle:
- Prefer consistent, repeatable, low-risk play over flashy tactics unless the current game state strongly favors aggression.

---

## 1. Core Strategic Objective

Protoss wins by doing the following better than the opponent:
1. Spend resources efficiently
2. Reach useful technology on time
3. Preserve expensive units
4. Take and defend expansions on schedule
5. Build the correct army for observed threats
6. Avoid dying to hidden tech, timing attacks, or greed punishment

Operational interpretation:
- Idle resources are failure unless intentionally banked for a near-term power spike.
- Unspent Chrono Boost is wasted value.
- Losing workers, tech structures, or high-value units for low return is usually worse than giving up map control temporarily.
- Protoss armies are expensive and synergy-dependent. Avoid fragmented fights.

---

## 2. Global Priorities

At almost all times, prioritize in this order:
1. Do not die immediately
2. Maintain worker production until appropriate saturation
3. Avoid supply block
4. Spend money efficiently
5. Scout and update opponent model
6. Build the army that defeats the observed opponent plan
7. Expand when safe and economically justified
8. Deny enemy scouting and preserve own information advantage
9. Seek favorable fights, not random fights
10. Transition tech before current composition becomes obsolete

If priorities conflict:
- Survival > economy
- Economy > greed
- Scouting information > blind optimization
- Army preservation > low-value harassment

---

## 3. Protoss Identity Constraints

Protoss-specific truths:
- Units are individually strong and individually expensive.
- Warp Gate allows reinforcement flexibility, but only where power or Warp Prism positioning permits.
- Many Protoss units scale with support: Sentries, High Templar, Colossi, Disruptors, Immortals, Archons, Observers, Warp Prism.
- Protoss loses badly when caught out of position, split, unsieged, or fighting without spell/support layers.
- Protoss often has strong timing windows tied to tech completions, upgrades, and Warp Gate cycles.
- Defensive structure placement and choke control are more valuable than for some other races.

Implications:
- Preserve core tech and spellcasters.
- Fight with batteries/cannons when defending key bases if possible.
- Avoid low-value trades unless you are clearly ahead economically or setting up a killing blow.
- Keep production and reinforcement paths coherent.

---

## 4. Economic Rules

### 4.1 Worker production
- Build Probes continuously until target saturation is reached or immediate survival requires cutting workers.
- Default saturation goals:
  - 1 base: mineral saturation + limited gas as build requires
  - 2 base: strong probe production until both mineral lines are well saturated
  - 3 base: aim for full healthy economy unless executing committed all-in
- Do not stop probe production for small harass unless a decisive combat timing is underway.
- Resume probe production after defense unless the build is explicitly all-in.

### 4.2 Resource spending
- Minerals too high usually means insufficient gateways, nexuses, pylons, or static defense.
- Gas too high usually means missing tech transitions, robo/stargate/fleet production, upgrades, or templar/tech spending.
- Floating both minerals and gas means the production system is broken or supply blocked.

### 4.3 Expansion timing principles
- Take the next Nexus when:
  - current bases are nearing saturation,
  - the map state is not immediately dangerous,
  - or the opponent is expanding and not threatening an all-in.
- Delay an expansion if scouting strongly suggests a committed attack and your army/defense is inadequate.
- Do not take a greedy expansion without the production or scouting to survive the counterattack.

### 4.4 Chrono Boost allocation
Default Chrono priority by game phase:
1. Early game: worker production, Warp Gate, critical tech
2. Mid game: upgrades, key tech units, workers if under-saturated
3. Combat windows: upgrades or critical unit production that unlocks timing
4. Late game: upgrades, tech transitions, fleet/templar production as needed

Never allow Chrono energy to cap on Nexuses without reason.

---

## 5. Supply and Production Rules

### 5.1 Supply block avoidance
- Supply blocks are strategic errors because Protoss production is timing-sensitive.
- Always maintain future pylons before hitting the cap.
- Higher risk windows requiring extra pylon attention:
  - before Warp Gate completion
  - before multi-gateway warp-ins
  - during transition to high gateway counts
  - before carrier/tempest max-out phases

### 5.2 Production scaling
- Add production when resources cannot be spent on time.
- Add gateways mainly to convert mineral income into army.
- Add Robo/Stargate when composition requires higher-tech production throughput.
- Gateway count should rise with number of bases and gateway-unit reliance.

### 5.3 Warp-in logic
Warp in units based on tactical need, not habit:
- immediate defense: units that counter the incoming threat fastest
- map pressure: mobile units with reinforcement value
- sustained frontal fights: composition completion units, not random filler
- Prism aggression: high-impact units at the battle location

Do not warp in units that dilute the required counter-composition.

---

## 6. Information Model and Scouting

The agent should maintain a live model of the opponent in these categories:
- current base count
- worker count estimate
- gas count / tech likelihood
- production type
- current army composition
- possible hidden tech
- likely timing windows
- expansion timing
- air vs ground commitment
- harassment capability

### 6.1 What to scout for
Always try to answer:
1. Is the opponent expanding greedily, playing standard, or all-in?
2. Is the opponent on air tech, bio, mech, gateway pressure, roach/ravager, muta, etc.?
3. Is there a hidden tech structure or proxy?
4. How soon can the opponent attack with meaningful force?
5. Which units are mandatory in response?

### 6.2 Scouting tools
Use all available Protoss scouting tools efficiently:
- Probe scout early for structure timing and proxies
- Hallucinated Phoenix for mid-game information
- Observer for safe persistent vision
- Oracle for mobile scouting and worker threat
- Adept shades when available and safe
- Zealot / spotter units on key attack paths
- Pylon or unit vision near likely expansions if safe

### 6.3 Reaction rule
Do not keep executing the original plan if new scouting clearly invalidates it.

Examples:
- If air tech is identified, accelerate anti-air and detection.
- If a 1-base or low-econ all-in is identified, cut greed and hold.
- If the opponent is very greedy, accelerate expansion or pressure.
- If the opponent is turtling hard, secure economy and transition instead of forcing low-value attacks.

---

## 7. Opening Principles

The opening should accomplish these goals:
1. Avoid immediate death to cheese or proxy play
2. Establish a coherent economy
3. Get early tech that matches the intended style
4. Preserve flexibility until enough information is gathered

Opening heuristics:
- Wall or structure-position to reduce early run-bys and improve defendability.
- Scout early enough to identify abnormal play.
- Default to safe, standard openings unless the strategy specifically requires deviation.
- Early units should cover the most dangerous blind spots in the matchup.

Do not commit to a fragile opener if the agent cannot reliably interpret and react to scouting signals.

---

## 8. Midgame Principles

Midgame is where most games are decided by incorrect reactions or inefficient spending.

Key midgame goals:
- reach 3-base economy when safe
- establish correct production mix
- secure detection
- get important upgrades rolling
- set up map vision and attack-path warnings
- identify whether the game should be:
  - pressure-oriented,
  - defensive into tech,
  - or expansion-focused

Midgame failures to avoid:
- too many tech paths with insufficient production
- too much gateway army into splash-heavy enemy compositions
- no detection against cloaked/burrowed threats
- moving out with slow or unsupported expensive units into poor terrain
- remaining on obsolete units too long after scouting enemy counters

---

## 9. Late Game Principles

Late game Protoss depends heavily on composition integrity, spell usage, and pre-positioning.

Late game priorities:
1. Keep key tech alive
2. Preserve ultimate-tech units and spellcasters
3. Protect expansions with vision and static defense where efficient
4. Secure remax pathways
5. Fight with full support layers, not just raw supply
6. Replace losses with the correct tech mix, not panic units

Late game army quality matters more than raw supply.
A worse composition at equal supply often loses badly.

---

## 10. Combat Rules

### 10.1 General engagement logic
Take fights when one or more of the following is true:
- your army is positioned better
- your splash/support is ready
- your reinforcements are close
- the opponent is on a timing window you must deflect
- you can isolate part of the enemy army
- you can exploit battery/cannon/terrain advantage
- the trade meaningfully protects or secures economy/tech

Avoid fights when:
- army is split
- key splash/spell units are absent or out of energy/cooldown
- reinforcements are far away
- fight area is enemy-favored terrain with no compensating benefit
- the attack serves no strategic purpose

### 10.2 Concave and surface area
- Ranged Protoss armies want good formation and protected backline units.
- Melee-heavy Zealot armies want effective surround angles and warp-in support.
- Chokes can help or hurt depending on whether your splash is ready and whether the opponent gains better surface area.

### 10.3 Retreat logic
Retreat if:
- the initial spell or volley failed and the fight is now unfavorable
- key support units are threatened for low-value gain
- you achieved the objective already (kill workers/tech/army fragment)
- the opponent has stronger reinforcements about to arrive

Retreat in order, not as a route collapse if possible.
Preserve Immortals, Colossi, Disruptors, High Templar, Warp Prism, and detection.

---

## 11. Spellcaster and Support Unit Rules

### 11.1 Sentry principles
Use Sentries for:
- Force Fields to split or delay
- Guardian Shield against ranged projectile pressure
- Hallucination for scouting when combat value is low

Do not waste Sentry energy randomly. A single good Force Field can decide a defense.

### 11.2 High Templar principles
Use High Templar for:
- Psionic Storm against dense light/biological or clumped armies
- Feedback against high-value energy units
- morph into Archons when energy is spent or frontline bulk is needed

Preserve Templar positioning. They are high-impact and fragile.

### 11.3 Disruptor principles
- Disruptors create zone denial and burst threat.
- One good shot can win a fight; one bad exposure can lose a Disruptor instantly.
- Fire novas where enemy movement is constrained, committed, or distracted.
- Do not over-chase after landing one good shot unless the follow-up fight is clearly winning.

### 11.4 Observer principles
- Keep detection with the army against cloak risk.
- Keep backup detection if possible.
- Use Observers for advance warning on attack paths and siege positions.
- Do not lose all detection to carelessness.

### 11.5 Warp Prism principles
- Warp Prism multiplies mobility, reinforcement speed, and pickup micro.
- Preserve the Prism unless a decisive trade is available.
- Prism enables harass, counterpressure, and rescue of expensive units.

---

## 12. Composition Principles by Unit Role

The agent should reason in terms of unit roles, not just unit names.

### 12.1 Frontline soak / engage
Common units:
- Zealot
- Archon
- sometimes Immortal in limited contexts

Rules:
- Frontline exists to buy time for damage and spell units.
- Too little frontline causes support units to die early.
- Too much frontline without damage/support loses efficiency.

### 12.2 Core sustained damage
Common units:
- Stalker
- Adept (early/situational)
- Carrier (late sustained air value)
- Tempest (siege/range role)

Rules:
- Sustained damage units need correct target access.
- Pure Stalker compositions fall off against many efficient mid/late-game armies unless paired with tempo, mobility, or support.

### 12.3 Anti-armor / anti-large
Common units:
- Immortal
- Void Ray in limited contexts
- Tempest in long-range/siege contexts

Rules:
- Build these when enemy unit tags and engagement patterns justify them.
- Do not overbuild single-purpose units into mixed armies without support.

### 12.4 Splash / area denial
Common units:
- Colossus
- Disruptor
- High Templar / Storm
- Archon in some anti-light contexts

Rules:
- Splash units are often the difference-maker versus mass bio, ling/bane, hydra, gateway clumps, and other dense armies.
- Splash units require protection and vision.

### 12.5 Utility / detection / scouting
Common units:
- Observer
- Oracle
- Phoenix (situational control/scouting)
- Sentry
- Warp Prism

Rules:
- Utility units are mandatory for correct information and fight execution.
- Do not cut all utility to gain short-term supply.

---

## 13. Matchup Framework: Protoss vs Terran (PvT)

### 13.1 Main Terran threat categories
- Marine/Marauder/Medivac bio timings
- Widow Mine pressure
- Tank pushes
- Liberator zoning
- Cloaked Banshee or Ghost tech
- Mech transitions
- multi-prong drops

### 13.2 PvT general principles
- Respect Terran timing attacks more than Terran greed unless proven otherwise.
- Vision on drop paths is very valuable.
- Detection is mandatory because Terran commonly leverages Mines, Banshees, Ghosts.
- Gateway-only armies often need splash/support to trade efficiently versus bio.
- Do not attack blindly into sieged Tanks without a positional or mobility advantage.

### 13.3 PvT composition guidance
Common successful ingredients:
- Blink Stalker for mobility and drop defense
- Colossus or Storm versus bio
- Immortals when armored units/tanks are relevant
- Disruptors if controlled well and terrain allows
- Archons/Zealots for midgame bulk and run-by potential

### 13.4 PvT behavioral rules
- Deny or punish exposed Terran thirds if feasible.
- Defend drops without overreacting with the full army unless necessary.
- If Terran is turtling mech, expand, scout, and transition deliberately rather than forcing choke fights.
- If Terran is bio-heavy, prioritize splash and good defensive setup before taking open-field fights.

---

## 14. Matchup Framework: Protoss vs Zerg (PvZ)

### 14.1 Main Zerg threat categories
- Ling flood / early aggression
- Roach/Ravager pressure
- Mutalisk transitions
- Hydra/Lurker tech
- Ling/Bane/Hydra or Ling/Bane/Muta map pressure
- macro overwhelm from over-greeded Zerg economy

### 14.2 PvZ general principles
- Scout Zerg tech and drone count aggressively; Zerg can punish blind greed or blind tech.
- Preserve wall integrity and choke control in earlier stages when ling run-bys matter.
- Splash and area control are critical against Zerg swarms.
- Harassment is valuable because Zerg benefits disproportionately from uninterrupted drone cycles.
- Static defense has high value versus multi-prong ling/bane and muta pressure when placed efficiently.

### 14.3 PvZ composition guidance
Common successful ingredients:
- Adept/Oracle openings for information and pressure
- Immortals versus roach-based armies
- Archons, Storm, Colossus, or Disruptors depending on enemy composition and control confidence
- Phoenix or Stargate elements if air control or scouting is needed
- Carriers/Tempests only when transition timing is safe and anti-air response is understood

### 14.4 PvZ behavioral rules
- If Zerg is over-droning, pressure or expand greedily with defense ready.
- If Zerg is on low drone count and many units, prepare for attack rather than tech greed.
- Against lurkers, value detection, range, and positional patience.
- Against mutas, do not rely on one anti-air source only.
- Avoid chasing ling/bane carelessly into bad terrain.

---

## 15. Matchup Framework: Protoss vs Protoss (PvP)

### 15.1 PvP nature
PvP is volatile because:
- units kill each other quickly
- small mistakes snowball hard
- hidden tech and proxy play are common
- defender’s advantage depends heavily on positioning and tech readiness

### 15.2 PvP main threat categories
- proxy Gateways / proxy Robo / proxy Stargate
- Blink pressure
- Oracle openings
- Immortal/Sentry all-ins
- Disruptor or Colossus tech spikes
- greedy expansions punished by sharp timings

### 15.3 PvP general principles
- Scout more aggressively than in other matchups.
- Hidden information is especially dangerous.
- Keep units together unless a split is clearly safe and purposeful.
- Detection and anti-air can be mandatory very early depending on tech.
- One bad fight often decides the game; avoid coin-flip engages.

### 15.4 PvP behavioral rules
- Respect proxied tech until ruled out.
- High ground vision and Observer control matter greatly.
- Blink and Prism mobility can change fights instantly; account for both.
- Do not expand greedily without confirmed safety.
- Tech and unit-count parity matter; update threat model constantly.

---

## 16. Static Defense Principles

Static defense is efficient when it protects:
- a vulnerable expansion
- a key choke
- a mineral line against repeated harassment
- a staging area for important fights

Protoss static tools:
- Shield Battery: best as force multiplier near army/production/base chokes
- Photon Cannon: detection + durable anti-light defense + anti-harass

Rules:
- Build static defense where it changes opponent incentives or saves multitasking load.
- Do not overspend on static if it delays essential army/tech against a mobile opponent.
- Batteries are strongest when the fight happens near them.
- Cannons are especially valuable against invisible or repeated harass threats.

---

## 17. Harassment and Counterpressure

Harassment is good when it:
- kills workers
- forces inefficient unit positioning
- reveals tech
- delays an expansion
- buys time for your tech/economy
- pulls the opponent home during your vulnerable phase

Do harassment only if the opportunity cost is acceptable.

Rules:
- Oracle, Warp Prism, Zealot run-bys, Blink pressure, and small gateway warps can all be valid.
- Do not lose expensive harass units for low-value damage unless that trade enables a larger strategic goal.
- Harassment should not cause the main army to die to a counterattack.
- When ahead, harassment can widen the lead safely. When behind, harassment must create real damage or information, not distraction for its own sake.

---

## 18. Detection and Cloak Response

Always maintain a detection policy, not just a detector unit.

The policy should answer:
- what cloaked/burrowed threats are possible now?
- where could they hit?
- what detection is attached to army?
- what detection covers bases?
- what is the backup if the first detector dies?

Common punishments for bad detection:
- Widow Mine hits
- DT damage
- Banshee harassment
- Lurker fights without vision
- burrow traps / map-control loss

If cloak is plausible, detection is not optional.

---

## 19. Upgrade Principles

Upgrades are especially important when:
- your composition scales strongly with them
- you are approaching a timing attack
- the opponent is on a similar unit count and efficiency margins matter

Rules:
- Start upgrades with a purpose, not automatically.
- Avoid starting too many conflicting upgrade lines if they delay essential units or expansions.
- Sync move-out opportunities with meaningful completions when practical.
- If on air transition, prioritize the upgrades that matter for that path.

---

## 20. Positioning and Map Control

Map control is not only territorial ownership. It is knowledge + threat + movement freedom.

To improve map control:
- place spotters on attack routes
- secure vision near watchtowers and key lanes
- clear enemy forward vision when possible
- position the main army where it can respond to the most dangerous threats
- use Warp Prism, Recall, and warp-in points to compress response times

Rules:
- Do not station the full army where it cannot protect multiple bases unless forcing a timing.
- Do not overextend without reinforcement infrastructure.
- Owning the center is valuable only if it improves response or engagement quality.

---

## 21. Recall Rules

Recall is for preserving strategic value, not for panic use only.

Use Recall when it preserves:
- key tech army that would otherwise be trapped
- enough army to stop a counterattack
- a harass force after it already achieved value
- tempo after forcing enemy movement

Do not waste Recall casually if a larger attack or multi-prong threat is imminent.

---

## 22. All-In Detection and Defense

Indicators of all-in or heavy commitment include:
- low worker count
- delayed expansion
- unusually high gas taken early
- hidden or proxied production
- many combat units with weak economy
- no sign of expected tech/econ follow-up

Defense rules:
- cut greed rapidly when evidence is strong
- spend money on immediate defensive value
- use batteries, choke points, Force Fields, and safe warp-ins
- avoid moving out unnecessarily
- do not chase after holding unless the counterattack is clearly favorable

Holding an all-in usually creates a winning position if economy survives.

---

## 23. Greed Detection and Punishment

Indicators of greed include:
- fast expansion with insufficient defense
- low unit count relative to time
- tech or eco investment without map control
- exposed bases or thin production

Punishment options:
- sharp timing attack
- deny third/fourth base
- harassment on worker lines
- force static defense or defensive posture
- expand behind contained pressure if direct kill is unnecessary

Do not overcommit to punishment if the opponent already stabilized and your own economy is falling behind.

---

## 24. Common Protoss Failure Modes

The agent should explicitly avoid these patterns:
- long supply block before attack/defense window
- too few units because probe or tech greed continued too long
- floating resources with insufficient gateways or tech production
- no scouting update for several minutes
- dying with unused Chrono, Recall, or spell energy
- attacking into bad terrain with expensive splash units exposed
- building the wrong anti-unit package because scouting was ignored
- no detection when cloak is plausible
- warping in habit units instead of correct response units
- splitting army versus multi-prong when one concentrated defense was required
- overchasing small harassment while losing macro structure

---

## 25. Decision Heuristics for Army Composition

When choosing composition, answer these in order:
1. What can kill me soonest?
2. What enemy units produce the most resource-efficient trades against my current army?
3. Which support/detection units are mandatory?
4. Does my army need more frontline, anti-air, anti-armor, splash, mobility, or range?
5. Will this composition remain valid for the next 2-3 minutes, or is a transition already required?

Composition update rules:
- If enemy composition changes, your production priorities must also change.
- Avoid single-tech tunnel vision.
- Prefer coherent compositions with a clear purpose over a pile of unrelated good units.

---

## 26. Tactical Micro Principles

### 26.1 Stalker micro
- Blink to preserve low-health Stalkers or gain firing angles.
- Do not blink forward unless the kill value or positional swing justifies the risk.

### 26.2 Zealot use
- Zealots are strongest when they connect to valuable targets or occupy space for backline units.
- They are poor when endlessly kited without support value.

### 26.3 Immortal use
- Immortals should hit armored/high-value ground units from protected positions.
- Do not leave Immortals exposed as the frontmost unit unless unavoidable.

### 26.4 Colossus use
- Keep Colossi behind the frontline with vision and anti-dive protection.
- Their value comes from repeated attack cycles, not one-time exposure.

### 26.5 Archon use
- Archons are strong as durable splashy bulk, especially versus light units and in tight fights.
- Use them to anchor midgame armies and defend against certain harassment patterns.

### 26.6 Carrier / Tempest use
- These units need time, protection, and support.
- Transition only if you can survive the vulnerable bridge period.
- Tempests excel at range siege and anti-capital / anti-static situations, not all-purpose brawling.

---

## 27. Build Selection Framework

The agent does not need one fixed build only. It needs a build selection policy.

Select builds based on:
- matchup
- map size and rush distances
- agent micro confidence
- ability to scout and respond
- opponent style if known

Preferred build classes for a robust agent:
- standard safe opener into macro
- pressure opener with flexible transition
- anti-cheese / scouting-heavy variant

Avoid build classes that require extreme precision unless the agent can execute them reliably.

Reliability > theoretical sharpness.

---

## 28. Win Condition Tracking

At all times, estimate your most likely win condition.

Common Protoss win conditions:
- superior economy with stable defense
- timing attack at upgrade/tech completion
- splash-tech superiority in a decisive fight
- superior air transition after securing ground safety
- repeated harassment causing economic collapse
- positional containment while taking more bases

Do not pursue actions unrelated to the current win condition.

Examples:
- If winning via superior economy, avoid random all-in fights.
- If winning via timing, synchronize upgrades/warp-ins and move decisively.
- If winning via late-game air, protect the transition and buy time.

---

## 29. Loss Condition Tracking

Also track the most likely way to lose.

Common Protoss loss conditions:
- dying to unscouted timing or proxy
- being out-expanded while inactive
- taking bad fights into enemy splash or siege
- lacking detection versus cloak/burrow
- losing expensive tech units to poor positioning
- failing to remax after one bad trade
- overcommitting to harassment and dying at home

The current action plan should reduce the highest-probability loss condition.

---

## 30. Practical Default Style for a Robust AI

Unless strong evidence suggests otherwise, the default Protoss AI style should be:
- safe standard opener
- continuous worker production
- regular scouting updates
- fast Warp Gate usage
- timely third base when safe
- strong detection discipline
- tech into composition with splash/support
- defend cleanly, then pressure with a coherent timing
- avoid reckless sacrifices
- transition methodically if the game goes long

This default style is preferable because it:
- reduces catastrophic losses to surprises
- exploits Protoss power spikes naturally
- supports adaptation across all matchups
- is less dependent on perfect cheese execution

---

## 31. Minimal Rule Set Summary

If the agent needs an ultra-short rule set, use this:

1. Never stop making Probes too early without a clear reason.
2. Never get supply blocked for avoidable reasons.
3. Spend Chrono and resources efficiently.
4. Scout continuously and update the opponent model.
5. Build the army that beats what the opponent is actually doing.
6. Always maintain detection when cloak is possible.
7. Fight with support, formation, and purpose.
8. Preserve expensive units and spellcasters.
9. Expand on time, but not blindly.
10. Prefer stable macro and coherent timings over random aggression.

---

## 32. Agent-Oriented Decision Loop

Repeat continuously:
1. Check immediate threats
2. Check worker production and saturation
3. Check supply and production capacity
4. Spend resources
5. Update scouting information
6. Re-evaluate opponent plan probabilities
7. Recompute required defenses and composition
8. Reposition army and vision
9. Decide whether to expand, pressure, defend, or transition
10. Preserve key units during execution

This loop should run faster during combat and when new scouting arrives.

---

## 33. Final Meta-Rule

Protoss is strongest when the army is coherent, the economy is healthy, the tech path is purposeful, and the fight is taken on favorable terms.

Therefore:
- avoid chaos you did not choose
- create structure in your economy and production
- force the opponent to fight into your timing, your tech, and your positioning whenever possible

