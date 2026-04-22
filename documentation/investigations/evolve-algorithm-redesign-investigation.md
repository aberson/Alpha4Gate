# Evolve Algorithm Redesign — Investigation

**Date:** 2026-04-21
**Context:** `/improve-bot-evolve` (Phase 9) currently uses (1+λ)-ES sibling tournaments. Discussion identified a structural flaw and worked through alternatives.
**Status:** Proposal. No code written. Current evolve skill (`scripts/evolve.py`, `.claude/skills/improve-bot-evolve/SKILL.md`) is unchanged.
**Decision:** Replace A/B siblings with individual-vs-parent evaluation, add full-stack composition, games-per-eval=5, conditional resurrection on close losses.

---

## 1. The flaw we started from

In the current algorithm, each round of the pool tournament picks two imps A and B, plays them against each other, and promotes the winner if it passes a parent-safety gate. Both siblings descend from the same parent, so the pairing asks only one question: **"is A better than B right now?"**

### Within-round discard

The loser is consumed immediately and never retested. Concretely, from the board the user was looking at:

```
#1  Raise attack supply threshold   CONSUMED-WON
#6  Add Blink micro for Stalkers    CONSUMED-WON   ← both won THEIR pairings
#8  Add Sentry for Guardian Shield  CONSUMED-LOST  ← dead forever
```

The problem: `#8` lost to some specific opponent on some specific parent. It might have been **additively fine** on top of `#1` or `#6`'s lineage. We never find out. A/B is a horizontal comparison; promotion is a vertical claim. The two are not equivalent.

This isn't a bug. It's a design consequence of (1+λ)-ES with sibling tournaments: pool imps are treated as competitors, not as a set of potentially-compatible deltas.

---

## 2. Three initial patches considered

Options ordered by implementation cost, each a targeted fix to the A/B structure:

### Option 1 — Loser re-enters pool
Only the winner is consumed per round. Loser returns with a `losses` counter; evicted only after N losses against **different** parents.
- **Cost:** ~50 lines in pool bookkeeping.
- **Solves:** additivity (probabilistically — loser eventually paired against winner's descendants).
- **Also solves:** context-dependence generally (imps are good or bad relative to some parent state).
- **Cost side:** pool drain takes 3-5x more rounds → slower Claude-idea refresh.

### Option 2 — Sequential (1+1)-ES
Scrap siblings. One imp at a time vs parent; promote if >5/10. Cleanest semantics, worst throughput (2x slower per imp).

### Option 3 — Additive re-audition
After round N promotes imp_a, immediately slot loser imp_b as imp_a in round N+1 against fresh pool. One retry per loser, on the updated parent. Keeps concurrency, adds one degree of memory. Narrower than Option 1.

### Initial pick: Option 1

Reasoning at the time:
- Promotes more imps per pool (extracts additivity that old system discarded).
- Cheapest implementation.
- Preserves parallelism.
- Losses counter generalizes beyond the specific additivity question.

---

## 3. Pushback: "evolution would be very slow"

The user flagged slowness. Honest accounting:

**Unchanged:** Promotion rate per round. Both systems promote ≤1 imp per A/B. Parent grows at the same speed either way.

**Actually slower:** Pool drain. Pool=10 with N=3 losses:
- Current: ~5 rounds to exhaust.
- Option 1: ~15-25 rounds to drain (losers retested 1-2x each).

That means **3-5x slower refresh of Claude-generated ideas**. In an 8-hour soak (~24 rounds), the old system tries ~50 fresh imps; Option 1 tries ~15 fresh imps + retries.

- **Lose:** exploration breadth.
- **Gain:** exploitation depth.

This was the moment the framing needed to change. Patching the A/B structure with resurrection is defending the existing shape. The right question is whether the A/B shape is correct at all.

---

## 4. Reframe from first principles

**Expensive:** games (SC2 wall-clock, minutes each).
**Cheap:** imps (Claude call, seconds).
**Goal:** most promotable signal per game played.

The real question isn't "how do tournaments work" — it's "**which pairings are worth spending games on?**"

### Three question types a pairing can answer

1. "Is imp_X better than the current parent?" (fitness)
2. "Does imp_X still beat parent after parent_Y absorbed?" (regression / re-audition)
3. "Is imp_A + imp_B + imp_C additively better than any single one?" (composition)

The current system answers only #1 and does it indirectly — A/B filters within-pool, not against-parent. That's the weakest link: games get spent ranking two random Claude proposals against each other when the only benchmark that actually matters is the parent.

### The four orthogonal sliders

| Slider | Options | What it trades |
|---|---|---|
| **Evaluation structure** | H2H siblings / individual vs parent | Within-pool filtering vs absolute signal + parallelism |
| **Composition discovery** | none / full-stack / pairwise bracket | Whether additivity is sought at all, and how thoroughly |
| **Resurrection** | never / conditional (close losses only) / always | How much you trust one bad matchup as a death verdict |
| **Pool refresh** | drain-then-generate / rolling / continuous background | Idea throughput vs pool coherence |

---

## 5. Three pre-packaged points on the slider

### Exploitation-heavy (minimize waste)
Individual vs parent @ 3 games, full-stack composition, never resurrect, drain-then-regen.
- ~30 games → ~3-4 promotions per generation.
- Risk: throws away contextually-good imps. Innovation narrows to whatever Claude proposes first.

### Balanced
Individual vs parent @ 5 games, full-stack composition, conditional resurrection (close losses only, N=2), rolling refresh.
- ~50 games → ~4-5 promotions + additivity signal.
- Risk: composition test might mask "good A + bad B" — still needs rollback.

### Exploration-heavy (maximize innovation)
Individual vs parent @ 5 games, pairwise composition bracket, always-resurrect with N=2, continuous refresh.
- ~80 games → ~5-7 promotions + full additivity map.
- Risk: more games on re-auditions and pairwise tests. Throughput from Claude proposals stays high but total game cost balloons.

---

## 6. Chosen design

Parameters the user picked after seeing the three packages:

- **Games per eval: 5** (statistically sane, matches current `--gate-games` default).
- **Resurrection: conditional** (middle option — close losses retry, blowouts die).
- **Evaluation structure:** individual vs parent. H2H siblings dropped.
- **Composition discovery:** full-stack test on all winners simultaneously.
- **Pool refresh:** rolling (top off when pool drops below a threshold rather than drain-and-regen).

### Algorithm sketch

Per generation:

1. **Generate pool:** Claude proposes 10 imps targeting the current parent.
2. **Fitness phase:** each imp plays parent for **5 games** standalone. Imps go into one of three buckets by win count:
   - `>=3/5`: **winner candidate**
   - `2/5`: **close loss → resurrection-eligible** (retry against next parent, N=2 cap)
   - `0-1/5`: **evicted** (blowout, dead)
3. **Composition phase:** apply **all** winner candidates on top of parent simultaneously → `stacked_parent`. Play `stacked_parent` vs `parent` for 5 games.
   - `>=3/5`: **promote the whole stack** as one commit.
   - `<3/5`: **fallback** — promote only the single highest-win-count imp; log the stack failure as a composition conflict for later pairwise investigation.
4. **Regression check:** new parent plays prior parent for 5 games. If new regresses (`<3/5`), roll back the promotion.
5. **Pool refresh:** after promotions/evictions, top the pool back up to 10 active imps (Claude generates replacements for the delta). Resurrection-eligible losers stay in the active count and get re-paired against the new parent next generation.

### Game-cost budget (pool=10)

| Phase | Games |
|---|---|
| Fitness (10 imps × 5) | 50 |
| Composition (1 stacked pairing × 5) | 5 |
| Regression (1 pairing × 5) | 5 |
| **Total per generation** | **~60** |

Compare to current system: pool=10 with 10 AB-games + 5 gate-games per promotion ≈ 75-150 games per full pool drain, yielding 2-3 promotions. New design: 60 games per generation, yielding 1-3 promotions (3-5 additive when full-stack succeeds).

### What this design explicitly gives up

- **Within-pool filtering.** Two bad imps can both pass fitness if both beat parent ≥3/5. OK — both get promoted, regression check catches the dud.
- **Pairwise additivity detail.** Full-stack tells you "all-together works" or "all-together doesn't." It does not tell you which pair is toxic. The exploration-heavy variant would.
- **Serialized promotion order.** Current system promotes one imp at a time; new design can promote 3-5 in one commit. This makes the promotion-commit log coarser but faster.

---

## 7. Why this is strictly better than the initial "Option 1 with resurrection"

The trap in the original thread was treating sibling-A/B as the fixed frame and asking "how do we make it waste less." The right question turned out to be "is sibling-A/B the right frame." It isn't, because:

1. Siblings are ranked against each other, not the thing that matters (parent).
2. A single head-to-head matchup is a high-variance signal for "is this imp generally good."
3. Parallel individual-vs-parent runs are naturally concurrent without the sibling framing.

Conditional resurrection is preserved from Option 1 because the core intuition was right — close-loss imps deserve a second look on an updated parent. What changes is that the "close loss" signal is now measured against parent directly (3/5 or 2/5), not against a random sibling.

---

## 8. Open questions for implementation

- **Claude-generation coherence.** If 10 imps are proposed independently, composition conflicts will be common (two imps both edit the same function). Need a proposal-level dedup / conflict-detector, or a Claude prompt that explicitly says "these 10 should be orthogonal."
- **Resurrection context rules.** An imp that closely-lost against parent_v3 — does its retry happen on parent_v4 (the next promoted), or only on a parent with a specific kind of change? Simplest: any new parent, N=2 cap.
- **Full-stack rollback.** When `stacked_parent` loses to `parent`, do we fall back to the top-1 imp (simpler) or to top-K-minus-one (more expensive, more informative)? Proposal: top-1 for v1, upgrade later if conflicts are common.
- **Pool-refresh timing.** Between fitness and composition (fresh imps immediately)? After regression (full generation boundary)? Proposal: after regression, to keep a generation's imps coherent.
- **Dashboard status vocabulary.** Pool tab currently shows `CONSUMED-WON / CONSUMED-TIE / CONSUMED-LOST / ACTIVE`. New states need: `FITNESS-PASS`, `FITNESS-CLOSE` (resurrectable), `EVICTED`, `PROMOTED-STACK`, `PROMOTED-SINGLE`, `REGRESSION-ROLLBACK`, `ACTIVE`.

---

## 9. What was NOT changed by this discussion

- `scripts/evolve.py` pool-tournament runner — untouched.
- `.claude/skills/improve-bot-evolve/SKILL.md` — untouched.
- Dashboard Pool tab rendering — untouched.
- Phase 9 Step 10 / Step 11 plan docs — untouched.

This investigation is the **design rationale**. Implementation is a separate build step.
