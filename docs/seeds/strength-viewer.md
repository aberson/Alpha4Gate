# Seed: strength viewer — spot-check candidates and flag issues

**Status:** SEED, 2026-11-11. Owner step: VW.0 in [master_plan.md](../plans/master_plan.md).
Decision D10.

**Operator framing:** watching games is how issues get spotted and flagged. Refine the
viewer without spending much time on it. Jev's viewer was good; something like it for
candidate bots, or a two-screen version when two bots play.

**Purpose:** watch a candidate play, see why it did what it did, and flag a moment as an
issue that feeds the improvement loop.

**Update (2026-11-11, D16):** evolution now runs headless on WSL, and live evolution
(`evolve --viewer`) is deprecated (master plan ES.6). The viewer runs separate live games
on Windows for spot checks and demos, either one bot against the built-in AI or two bots,
rather than showing evolve runs. Older versions crash on Windows build 98310 without
`orchestrator.sc2_compat`, so the dual layout's launcher must install the shim for them; a
small wrapper before `bots.<v>` in `src/orchestrator/selfplay.py:277-293` does this without
editing frozen trees. Flags go through `scripts/scoreboard.py flag` (ES.10).

## What to reuse from Jev's Observer Rush

| Feature | Source |
|---|---|
| One window: native SC2 beside a decision panel, recent events and current decisions | `frontend/src/components/JevBattleView.tsx` (`?battle=1`), `scripts/probes/jev_battle_view.py` (native hosting) |
| Dashboard-first launch: the viewer opens on the exact run before SC2 starts | `src/jev/launch.py`, `scripts/launch-jev.ps1` |
| Automatic observer camera that follows the action, with manual unlock | `src/jev/camera.py` |
| Follow decisions: inspect newly active paths; clicking pauses following | JevBattleView |
| Optional local commentary (Archon Auto Caster, Ollama, no gameplay authority) | `src/jev/caster.py` |
| Graceful owned stop; optional recording | `scripts/launch-jev-battle.ps1`; `docs/operator/jev-battle-view.md` |

## What the main bot shows in place of Jev's graph

v13 has no policy graph, but it has decision "lanes" that serve the same purpose:

- the strategic-state timeline (six states)
- `data/decision_audit.json` (currently advisor responses, not rules/PPO decisions)
- the `/ws/game`, `/ws/decisions` and `/ws/commands` WebSockets (their queues are
  process-local; a separate candidate worker needs an explicit telemetry bridge)
- the build backlog, army targets and win probability (Phase N)
- in hybrid mode, the PPO action and its probabilities

## Two layouts

- **Single, for candidate vs built-in AI:** one SC2 client plus the decision-lanes panel,
  laid out like Observer Rush.
- **Dual, for separate live bot-vs-bot spot checks:** SC2 allows only two API clients and both
  players use them, so there is no observer client. Show each player's own client side by
  side, with a decision strip per side. This extends the existing `src/selfplay_viewer`
  container, which already hosts two SC2 clients with a stats bar and a win-loss overlay.

## Flag this moment (the reason the viewer exists)

- A button or hotkey records the game time, run/match ID, versions, a short note and the
  replay path to a findings file (for example `data/findings.jsonl`).
- Phase ES reads it for tactical revalidation (ES-G) and for proposal evidence (ES-H).

## Scope cap

- Two or three build steps. Reuse Jev's components.
- No new 3D graph work. Recording and commentary are optional.
- Stopping an evolve run stays "close the evolve console": D7 dropped the control-file
  reader. Closing the viewer only detaches it. A graceful stop is in scope for single
  candidate matches only.

## Removed steps folded in here (D10, 2026-11-11)

- **EV.4 / EV.5** (`evolve --viewer` smoke and soak) become the dual layout's acceptance.
- **SV.1–SV.6** become known issues to verify in the dual layout:
  - detach and orphan cleanup
  - threaded-viewer interrupt ownership (Ctrl+C can orphan SC2)
  - sizing on the operator display
  - surrender-label leak
  - non-blocking window discovery
  - legacy acceptance leftovers
- **UI.7 / UI.8** (historical acceptance provenance) are closed by supersession.
- **The dashboard's unwired Stop Run button** (D7): remove it, or replace it with the real
  instruction ("close the evolve console window").

## Open questions

1. Which lanes to show first. Proposed: strategic state, army target and win probability.
2. The dual layout's size on the operator display.

**Draft (2026-11-16):** [VW.0 viewer plan](../investigations/viewer-plan-2026-11-16.md)
proposes three build slices and a separate manual acceptance bundle. It is HELD for
shared-interface/ownership agreement and downstream review, not implementation approval.
The draft distinguishes the current evolve detach behavior from self-play's boundary-stop
behavior and identifies telemetry fields that are not yet produced.

**Plan (2026-11-16, lane evolve-demo):** [VW.1–VW.3 build steps](../investigations/viewer-vw-steps-2026-11-16.md)
supersedes that draft and is sent to main for the master plan. Operator decisions the same day:
one Observer Rush-style window for both layouts (the old pygame container is left for legacy
callers), and flags go through ES.10's `flag` command unchanged, so VW.3 waits for ES.10.
The measured display is 3440×1440 (work area 3440×1392), which answers open question 2.
