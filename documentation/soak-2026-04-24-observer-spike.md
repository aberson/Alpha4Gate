# Observer-support spike — soak findings

**Date:** 2026-04-24
**Issue:** #195
**Plan:** `documentation/plans/selfplay-viewer-plan.md` §7 Step 1
**Result:** **BLOCKED** — SC2 server refuses 3-player multi-client games

## Summary

The spike (`scripts/spike_observer.py`) attempted to spawn a 3-player
`GameMatch` with `[Bot, Bot, Observer()]` via burnysc2's
`a_run_multiple_games`, with a local copy of the dispatch patch
(`_install_observer_dispatch_patch`) installed. The goal was to verify
checkpoints (a-e) from the plan:

| # | Checkpoint | Outcome |
|---|---|---|
| a | 3× SC2_x64.exe spawn | **PASS** — `maintain_SCII_count` correctly spawned 3 processes; 3× `Status.launched` logged |
| b | Observer window renders full map | **N/A** — game never started |
| c | Game completes normally | **FAIL** — `create_game` rejected before game start |
| d | `client._game_result` populated on observer | **N/A** — game never started |
| e | RAM peak recorded | **N/A** — game never ran |
| f | No orphan SC2 processes | **PASS** — `KillSwitch.kill_all` cleaned up all 3 on exception |
| g | Soak doc produced | **PASS** — this file |

## The blocker

After `maintain_SCII_count(3, ...)` successfully spawned three SC2 clients
and `_setup_host_game` called `create_game` with the 3-player
`player_setup`, the server returned:

```
CreateGameError.InvalidPlayerSetup: Only 1v1 is supported when using
multiple agents
```

Full traceback:

```
File ".venv/Lib/site-packages/sc2/main.py", line 591, in run_match
    await _setup_host_game(controllers[0], **match.host_game_kwargs)
File ".venv/Lib/site-packages/sc2/main.py", line 332, in _setup_host_game
    raise RuntimeError(err)
RuntimeError: Could not create game: CreateGameError.InvalidPlayerSetup:
Only 1v1 is supported when using multiple agents
```

## Root cause

The SC2 API server (Blizzard's C++ client) enforces a limit independent of
burnysc2: when **more than one API client** connects to the same game
("multiple agents"), the game's `player_setup` must be strict 1v1 (two
Participant slots). Adding an Observer slot — even though the proto at
`controller.py:34-40` accepts it — causes the server to refuse
`RequestCreateGame` at runtime.

The investigation at
`documentation/investigations/observer-player-viewer-investigation.md` was
entirely code-reading: it confirmed the data types, proto messages, and
`Client.join_game` observer path all exist. It did **not** run a live
3-player `create_game`. The runtime restriction is at the SC2 server layer,
which cannot be read from burnysc2's source.

## Implications for the plan

- **Path A (third-observer-process)** — as scoped in the current plan — is
  **infeasible** without a workaround for the server-side restriction. The
  `_play_game` dispatch patch works (installs idempotently, intercepts the
  Observer instance correctly), but nothing downstream can reach the
  observer coroutine because `create_game` rejects the player setup.
- **D5 of the plan** ("Hard commit to observer; no `disable_fog` fallback")
  needs to be reopened. The investigation's recommended **Path B
  (`disable_fog=True`)** is once again the cheapest viable option —
  provided it's gated as a dev-only viewer flag (not used during real
  self-play training, which D5 correctly rejected because fog-of-war
  visibility influences bot decisions).
- Plan §7 Steps 2–6 all presuppose Path A works. They must be reworked or
  the plan must be reframed around a different viewer architecture.

## Paths forward (not decided yet)

1. **`disable_fog=True` as dev-only viewer flag** — accept the
   investigation's original recommendation. Observer-class refactor is
   shelved. Requires a bot-code audit for `is_visible` / visibility
   branching (investigation §5 step 4) and a clear warning in the CLI that
   this flag changes bot decisions and should not be used during rated
   training runs.
2. **Speculative workaround: post-create observer join** — try spawning a
   2-player 1v1 game, then have a 3rd client call `join_game` with
   `observed_player_id` *after* the game has started. Untested; burnysc2
   has no API for this pattern and the server may still refuse. Would
   require another spike.
3. **Replay-viewer mode** — watch completed games after the fact via
   `run_replay` + `ObserverAI`. Removes "live" from the feature but is
   known-working in burnysc2. Useful for post-hoc analysis; doesn't
   deliver the "watch a live training game" experience.
4. **Revert to 2-pane viewer** — the existing shipped viewer
   (`src/selfplay_viewer/`, 2026-04-18) works. We'd accept "each pane shows
   one bot's fog-of-war camera" as the compromise that motivated this
   whole refactor.
5. **Hybrid: 2-pane + `disable_fog`** — keep the shipped 2-pane viewer but
   add `disable_fog` as a dev-only flag. Each pane now shows full-map
   vision from that bot's camera. Still two panes but each is full-map.

## Evidence

- `scripts/spike_observer.py` (committed at spike time) — the spike script
- Full console log captured 2026-04-24 ~16:27 UTC on commit `5a284d3`,
  branch `master`, venv `.venv-py312` (Py3.12), burnysc2 7.1.3, SC2 client
  running from `C:\Program Files (x86)\StarCraft II\`.

## Option 2 attempt: post-create observer join (also failed)

**Approach.** `scripts/spike_observer_postjoin.py` — manually spawn 3
SC2Processes, call `create_game` with only the 2 bots (1v1, known-good),
and have the 3rd client call `join_game(race=None, observed_player_id=1,
portconfig=Portconfig(guests=2))` in parallel with the 2 bot joins.

**Outcome.** Three independent failure modes:

1. **Observer's `join_game` hangs indefinitely.** The SC2 server never
   sends a `JoinGameResponse`. The request blocked for 14 minutes (854s)
   until `KillSwitch.kill_all` forcibly tore down all 3 SC2 processes,
   which raised `ConnectionAlreadyClosedError` on the observer's
   client side.

2. **The pending observer join corrupts the 2-bot game.** Both bots
   reached `Status.in_game` at 17:06:30 but the game ended 8 seconds
   later at 17:06:38 — Result.Victory for P2, Result.Defeat for P1.
   With `realtime=False` and `game_step=8`, 8 seconds wall-clock ≈ 22
   bot iterations; AggroBot's attack-move logic doesn't fire until
   iter > 200. Neither bot did anything substantive yet the game ended
   with a deterministic winner. The shared `Portconfig(guests=2)`
   reserves a third port pair the server expects to be filled, and the
   observer's pending (never-completed) request appears to leave the
   1v1 in an unrecoverable state.

3. **No graceful fallback.** Even if we dropped the observer's frame
   loop entirely and accepted that `join_game` would never return, the
   bots' 1v1 itself is corrupted by the additional client's pending
   join. We cannot run the observer "best-effort, ignore if it fails"
   because the failure mode is "the bots' game also breaks."

**Verdict on Option 2:** infeasible. Late observer joins are not a
viable workaround.

## Final conclusion

Both Option 2 spikes confirm that **burnysc2 + the local SC2 API server
cannot run a 3rd live SC2 client alongside a 1v1 game**. The proto-level
support the investigation found at `controller.py:34-40` and
`client.py:89-95` is real, but the server enforces a strict 2-API-client
cap that no Python-layer workaround can bypass.

The observer-based single-window viewer (Path A from the plan) is
**not buildable** without either:
- A patch to Blizzard's SC2 client (out of scope; we don't have source)
- A Linux-only multi-instance pattern that may have different rules
  (untested; we're Windows-only per CLAUDE.md)
- Replay-mode viewing (post-hoc, not live)

## Reporting to build-phase

**Step 1: BLOCKED.** Plan must pivot away from Path A. Options 1, 3, 4,
5 from §"Paths forward" above remain — Option 2 is now also crossed off.

User decided 2026-04-24 to try Option 2 first; that path is now
exhausted.

**Evidence:**
- `scripts/spike_observer.py` — Option 1 attempt (3-player create_game)
- `scripts/spike_observer_postjoin.py` — Option 2 attempt (post-create observer join)
- Console logs from both runs captured 2026-04-24 in this document and in
  conversation history.

## Spike A: does `debug_show_map()` affect bot perception?

After both observer-join paths failed, the second-pass investigation
(`documentation/investigations/observer-restriction-workarounds-investigation.md`)
identified path 4.1(b) — embed one of the two playing bots' windows
with `show_map` enabled — as the cheapest viable alternative IF
`debug_show_map()` is rendering-only. Spike A
(`scripts/spike_show_map.py`) tests that.

**Setup.** Bot vs Computer.Easy on Simple64. Bot trains probes and
never scouts (no probe contact with enemy). Toggle `show_map` ON at
iter 100, OFF at iter 200, sample `enemy_units.amount` and
`enemy_structures.amount` each iteration, summarize.

**Result.**

| phase | avg enemy_units | avg enemy_structures |
|---|---:|---:|
| baseline (iter 0-99, OFF) | 0.00 | 0.00 |
| show_map ON (iter 100-199) | 13.46 | 1.86 |
| show_map OFF (iter 200-299) | 0.14 | 2.00 |

`enemy_units` jumps from **0 → 13.46** the iteration after
`debug_show_map()` is called and drops back to **0** when toggled off.
`enemy_structures` stays at 2 after toggle-off because of SC2's normal
"discovered structures stay dimly visible" memory — that is fog-of-war
working as intended, not a confound.

**Verdict.** `debug_show_map()` is **PERCEPTION-AFFECTING**. It changes
what the SC2 server returns via `RequestObservation`, not just what
gets rendered to the client window. **Path 4.1(b) is training-unsafe.**
A bot with show_map on would learn to act on enemy information it
shouldn't have access to.

**Implications for the viewer.** With 4.1(b) ruled out, the remaining
viable paths from the workarounds investigation §4 are:

- **4.1(a) Sacrificed viewer-bot** — ~50 LOC, but means watching one
  real bot vs an idle bot, NOT real self-play. Significant downgrade.
- **4.2 Replay-stream-as-live** — ~150-200 LOC, ~4hr spike. The only
  remaining path that delivers ALL of: both bots playing normally,
  single wide window, full-map vision, training-safe. ~1-2s lag is the
  cost.
- **4.5 Custom renderer** — 2-3 days. Heavy, but full visual control.
- Revert to shipped 2-pane viewer (fog-limited).
- Hybrid: 2-pane + `disable_fog` (training-unsafe).

**Decision (deferred to a later session).** Pausing the viewer thread.
The headless Linux training opportunity discovered while investigating
4.7 (see
`documentation/investigations/headless-linux-training-investigation.md`)
is strategically more valuable than the viewer right now and may
change viewer requirements (if training moves to Linux servers, the
viewer becomes a Windows-host dev tool with different constraints).
