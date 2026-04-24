# Observer-player single-window viewer — feasibility investigation

**Date:** 2026-04-24
**Context:** Alpha4Gate Phase 6 self-play viewer. The currently-planned viewer
(`documentation/plans/selfplay-viewer-plan.md`) reparents **two** SC2 windows
side-by-side into a pygame container — each window showing one bot's
fog-of-war-limited perspective. The user asked whether we could instead spawn a
**third SC2 client as an "observer" player** with full map vision (no fog) and
only embed that one window — giving a single, larger, strategically complete
view.

This doc answers: is that feasible with burnysc2 v7.1.3, what would it take,
and how does it compare to the two-screen plan. All claims cite primary source.

---

## 1. What exists in burnysc2 today

### Observer is a first-class player type in the proto and in burnysc2 classes

- [`.venv/Lib/site-packages/sc2/player.py:89-94`](../../.venv/Lib/site-packages/sc2/player.py#L89-L94)
  defines `class Observer(AbstractPlayer)` with a no-arg constructor that sets
  `p_type=PlayerType.Observer` (and no race / no difficulty).
- [`player.py:38-41`](../../.venv/Lib/site-packages/sc2/player.py#L38-L41)
  — `AbstractPlayer.__init__` has an explicit `elif p_type == PlayerType.Observer`
  branch that asserts `race is None`, `difficulty is None`, `ai_build is None`.
- [`player.py:48-50`](../../.venv/Lib/site-packages/sc2/player.py#L48-L50)
  — `needs_sc2` returns `not isinstance(self, Computer)`, so **Observer DOES
  need its own SC2 process** (this is critical — an observer is not free;
  it's a third `SC2_x64.exe`).
- [`controller.py:34-40`](../../.venv/Lib/site-packages/sc2/controller.py#L34-L40)
  — `Controller.create_game` iterates `players` and adds `p.type =
  player.type.value` to `RequestCreateGame.player_setup` for each. Observer
  slots are accepted at proto level with no special handling.
- [`client.py:57-95`](../../.venv/Lib/site-packages/sc2/client.py#L57-L95)
  — `Client.join_game` has an explicit observer path: when `race is None`, it
  asserts `observed_player_id` is an int and emits
  `sc_pb.RequestJoinGame(observed_player_id=observed_player_id, options=ifopts)`.
- `ObserverAI` exists at [`observer_ai.py:18`](../../.venv/Lib/site-packages/sc2/observer_ai.py#L18)
  and is documented as "very experimental" — it's the base class for
  replay-watching bots, used by `run_replay`.

So **the data types, proto messages, and base class all exist**. The SC2
client itself supports joining as an observer — this part is production-ready.

### But the live-game dispatcher does NOT wire observers through

This is the key finding the subagent's initial sweep missed. The standard
self-play path is `run_match` →
`play_from_websocket` → `_play_game`, and `_play_game` breaks on Observer:

- [`main.py:207-232`](../../.venv/Lib/site-packages/sc2/main.py#L207-L232)
  — `_play_game` calls `await client.join_game(player.name, player.race,
  portconfig=portconfig, ...)` at line 217-219. Accessing `player.race` on
  an Observer raises `AttributeError` — [`player.py:28-29`](../../.venv/Lib/site-packages/sc2/player.py#L28-L29)
  only assigns `self.race` when `race is not None`.
- Even if `race` were `None`, `_play_game` never passes `observed_player_id`,
  so `client.join_game`'s own assertion at [`client.py:90`](../../.venv/Lib/site-packages/sc2/client.py#L90)
  would fail: `assert isinstance(observed_player_id, int)`.
- [`main.py:224-225`](../../.venv/Lib/site-packages/sc2/main.py#L224-L225)
  then calls `_play_game_ai(client, player_id, player.ai, ...)`. Observer
  has no `.ai` attribute. Another `AttributeError`.
- `ObserverAI` is **only** wired into `_host_replay` / `run_replay` — see
  [`main.py:452-458`](../../.venv/Lib/site-packages/sc2/main.py#L452-L458)
  and [`main.py:539`](../../.venv/Lib/site-packages/sc2/main.py#L539). It
  is not reachable through `a_run_multiple_games`.

**Conclusion for this section:** `players.append(Observer())` on a
`GameMatch` will fail at runtime in live self-play. It is *not* a one-line
change. The plumbing exists in the lower layers (proto, Controller, Client)
but `run_match` / `_play_game` needs patching to teach the dispatcher how
to drive an observer coroutine.

---

## 2. The alternative hiding in plain sight: `disable_fog=True`

Before going deeper into observer-coroutine work, there's a much simpler
lever already in burnysc2 and already plumbed through our code:

- [`main.py:53`](../../.venv/Lib/site-packages/sc2/main.py#L53)
  — `GameMatch` has a `disable_fog: bool | None = None` field.
- [`main.py:82`](../../.venv/Lib/site-packages/sc2/main.py#L82)
  — `host_game_kwargs` forwards it.
- [`controller.py:27-29`](../../.venv/Lib/site-packages/sc2/controller.py#L27-L29)
  — `create_game` sets `disable_fog=disable_fog` on `RequestCreateGame`.
  This is the SC2 proto's **global fog-of-war disable**: when true, **every
  client in the match** renders the whole map with no fog.

Our own `_run_single_game` in [`src/orchestrator/selfplay.py:326-331`](../../src/orchestrator/selfplay.py#L326-L331)
builds the `GameMatch` but does not pass `disable_fog`. Adding it as a
kwarg is a ~5-line change.

**What this buys us:** with `disable_fog=True`, the two existing bot clients
each already see the whole map. To get a "single full-vision window," we
just embed **one** of the two bot windows into the pygame container instead
of both. The other bot's window still spawns (SC2 on Windows can't be
headless) but we don't reparent it; we let it run minimized or
off-screen. Same resource budget as today, zero burnysc2 patching, and the
viewer complexity drops to a single-child reparent.

Caveat: because the camera in each bot client starts anchored on that bot's
start location and follows that bot's selections, the view is "full vision
from bot N's camera." That's not a true omniscient centered view, but with
fog off it gives the strategic overview we actually want. The pygame
overlay can add a "viewing: P1 / P2 / auto-swap" hotkey to flip between
the two windows mid-match.

---

## 3. If we genuinely want a third dedicated observer window

There are two viable construction paths. Both require code outside our repo
OR a modest burnysc2 patch.

### Path A: custom parallel coroutine + monkey-patch (cleanest, ~100 LOC)

Write our own `_play_observer(client, observed_player_id, realtime)`
coroutine in `src/orchestrator/selfplay.py` alongside `run_batch`. Then
monkey-patch `sc2.main.run_match` (or fork it locally as
`_run_match_with_observer`) so that when it iterates
`players_that_need_sc2`, any `Observer` instance is routed to our
coroutine instead of `play_from_websocket`.

The observer coroutine:
1. Receives its own `Controller` from `maintain_SCII_count(3, ...)`.
2. Calls `await client.join_game(name="Observer", race=None,
   observed_player_id=1, portconfig=portconfig)` directly (bypassing
   `_play_game`).
3. Loops on `await client.observation()` at the same `game_step` as the
   bots, ignoring the returned state (purely for keeping the websocket
   alive). Exits when `client._game_result` is populated.
4. `await client.leave()` on game end.

Cost on top of Phase 3's already-patched self-play stack: ~60 lines of
coroutine + ~30 lines of `_run_match` fork (we already monkey-patch
`Portconfig.contiguous_ports` in [`selfplay.py:104-117`](../../src/orchestrator/selfplay.py#L104-L117),
so adding another surgical patch is in-keeping).

### Path B: upstream the fix

`_play_game` is a natural place to add an `isinstance(player, Observer)`
branch that does what Path A does inline. A PR to BurnySc2's main repo
would unblock this for everyone but slows us down by weeks of review.
Pragmatically: Path A now, upstream if it works.

### Shared costs and constraints of any third-process observer approach

- **Memory:** 3× `SC2_x64.exe` at ~2 GB each → ~6 GB per live match
  (today: ~4 GB). Relevant on 16 GB dev boxes that already run the dashboard,
  Python, and Chrome.
- **Port pressure:** burnysc2 7.1.3's port-collision bug ([`selfplay.py:76-118`](../../src/orchestrator/selfplay.py#L76-L118))
  gets worse with 3 clients. Our existing blocklist patch already handles
  this, but we'd need a 10-game soak to confirm.
- **Camera:** observer clients open with camera centered at map origin
  (0,0), not on either bot. To get a useful default framing we'd probably
  send a `RequestDebug` camera-move right after join. Not hard.
- **Game_loop sync:** the observer must call `client.step()` at the same
  cadence as the two bots or the bot step loop stalls waiting on a quorum.
  `run_match`'s `asyncio.gather` over all three coroutines already enforces
  this; a naive observer loop using only `observation()` without `step()`
  would desync. Our coroutine must include the `await client.step()` call
  every iteration.

---

## 4. Comparison table

| Dimension | Current plan: **2-screen reparent** | **`disable_fog=True` + embed one window** | **Third Observer process** |
|---|---|---|---|
| Screens embedded | 2 | 1 | 1 |
| Full-map vision | No (each window shows one bot's fog) | **Yes** (both clients render whole map) | **Yes** (dedicated omniscient view) |
| SC2 processes | 2 | 2 | **3** (+~2 GB RAM / match) |
| burnysc2 patching required | None | None | ~100 LOC custom coroutine + dispatch patch |
| Code change to `selfplay.py` | None (pygame side only) | ~5 lines (add `disable_fog=True`) | ~100 lines + 3-PID wiring |
| Viewer code change | As planned (2-child reparent) | **Simpler** (1-child reparent) | Similar complexity (1-child reparent, 3-PID discovery) |
| Risk profile | Win32 reparenting is the main risk | Lowest — additive flag | Highest — untested path in burnysc2 for live games |
| Camera framing | Each bot's own camera (follows its selections) | One bot's camera, but with no fog | Neutral observer camera (we set it on join) |
| Affects training? | No | **Possibly** — some bots' decision logic reads `is_visible`; need to audit | No |
| Time to first watchable match | As planned | Day or two | Week-ish (patch + soak) |

---

## 5. Recommendation

**Build the `disable_fog=True` variant first.** It delivers the user's
stated goal — "one screen, full map vision, fits better than two screens" —
with the least code and no new failure modes. Concretely:

1. Add a `disable_fog: bool = False` kwarg to `run_batch` in
   [`src/orchestrator/selfplay.py`](../../src/orchestrator/selfplay.py),
   threaded through to `_run_single_game`'s `GameMatch` construction at
   [line 326](../../src/orchestrator/selfplay.py#L326).
2. Expose it as `--viewer-full-vision` on
   [`scripts/selfplay.py`](../../scripts/selfplay.py) (default `False` to
   keep training unaffected — see audit note below).
3. Modify the selfplay-viewer plan: replace "reparent 2 windows
   side-by-side" with "reparent 1 window, show placeholder for the
   off-screen bot, allow `v` hotkey to flip which bot's camera we're
   watching."
4. **Audit step before enabling in training:** grep `bots/v0/` for
   `is_visible`, `visibility`, `enemy_structures`, `enemy.visible` — any
   bot logic that branches on fog-of-war should be surveyed. If the bots
   read visibility, then `disable_fog=True` changes their decisions and
   **cannot be used during real self-play training runs**; it would become
   a "spectator mode" flag for dashboard viewing only. Most `burnysc2`
   bots access enemy info through `self.enemy_units` / `self.enemy_structures`,
   which in a no-fog game will include everything, so yes this almost
   certainly affects decisions. **Treat `disable_fog=True` as a
   debug/spectate flag, not a training-time flag.**
5. If, after building this, the user specifically wants a neutral
   omniscient camera (rather than a bot's camera with fog disabled),
   **then** invest the week in Path A from §3.

**Why not jump straight to the observer process:** we'd pay 50% more RAM
per match, add untested code, and risk destabilizing the already-working
Phase 3 self-play pipeline — all for an outcome the `disable_fog` variant
mostly covers. A third process is only worth it if the one-bot-camera
framing turns out to be confusing in practice.

**Why not keep the two-screen plan as-is:** the user's intuition is
correct. Two SC2 windows cramped into one container each showing only
half the map is worse than one window showing the whole map. The planned
viewer's value was "watchable at all"; a single full-vision window is
strictly better watching.

---

## 6. Open questions for the user

1. Is "full map vision from one bot's perspective" acceptable, or do you
   specifically want a neutral omniscient camera (which requires the
   third-process path)?
2. Is this viewer strictly for **observation during dev** (where changing
   `disable_fog` is fine) or do you want it running during **rated
   self-play training runs** (where it cannot change bot behavior —
   implying the third-process path is the only option)?
3. Do you want to keep the two-screen plan's themed SF2-style backgrounds
   and W-L overlay, or simplify the whole pygame container to a
   minimal bezel now that it only hosts one window?

Answers to these determine which of the three paths in §4 to actually build.
