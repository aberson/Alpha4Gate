# Observer-restriction workarounds — second-pass investigation

**Date:** 2026-04-24
**Author:** session continuation after Step 1 spike failure
**Predecessor:** [`observer-player-viewer-investigation.md`](observer-player-viewer-investigation.md) (2026-04-24, code-only feasibility study)
**Triggers for this doc:**

- Both spikes from `documentation/soak-2026-04-24-observer-spike.md` failed:
  - Option 1 (3-player `create_game`): server returned
    `CreateGameError.InvalidPlayerSetup: Only 1v1 is supported when using
    multiple agents`
  - Option 2 (post-create observer join): observer's `join_game` hung 14
    minutes (854s) before `KillSwitch` tore it down with
    `ConnectionAlreadyClosedError`; the bots' 1v1 was also corrupted —
    ended in 8s with neither bot reaching its attack iteration
- User pushback (verbatim): *"Path A is now decisively dead — both spikes
  confirm Blizzard's server refuses any 3rd live API client alongside a
  1v1. There could possibly be a way around this. The only constraint is
  getting the computers to play each other. ... Professional starcraft
  games are show using an observer so there must be something there."*

The user is right that "the pros do it" is strong evidence that an
observer path exists somewhere. The question this doc answers is whether
that path is reachable from our local-LAN API stack.

---

## 1. Restating what we actually proved

We must be precise here — saying "Path A is decisively dead" overstates
what the spikes ruled out. What was ruled out:

| Path | Status | Evidence |
|---|---|---|
| 3-player `RequestCreateGame` with `[Bot, Bot, Observer]` via burnysc2's `a_run_multiple_games` on Windows | DEAD | Spike 1 console error (soak doc §"The blocker"); refused with `InvalidPlayerSetup: Only 1v1 is supported when using multiple agents` |
| Post-create observer `RequestJoinGame(race=None, observed_player_id=1)` against a 1v1 game on Windows | DEAD | Spike 2 — `join_game` hung indefinitely; bots' 1v1 also corrupted |
| Any path involving 3 LAN-port-multiplexed API clients on Windows | LIKELY DEAD | Both spikes hit the same underlying server cap |

What was **not** ruled out:

- Single-instance multi-agent paths (where one SC2 process runs multiple
  bots internally, not 1 process per bot)
- Replay-based viewing (live or near-live via in-memory replay streaming)
- Custom rendering on top of `RequestObservation` data (no extra SC2
  process; we render the map ourselves)
- Camera-controlled embed of one of the existing 2 bot windows (full
  map + chosen camera position via raw camera-move API)
- Linux/WSL paths (some burnysc2 quirks differ on Linux per
  `main.py:694-697`'s explicit Linux branch in `maintain_SCII_count`)
- `pysc2` (Google's library; different architecture than burnysc2;
  AlphaStar used it)

So "Path A as scoped in the original plan" is dead. "Any path that gets
us a wide observer-style view of bot self-play" is not.

---

## 2. How professional SC2 broadcasts actually work

Pro broadcasts use a fundamentally different stack than our local LAN
API. This is an important calibration: the existence of pro observer
streams does **not** prove the local LAN API supports it.

### The pro-tournament path (Battle.net + spectate)

Tournament players queue on **Battle.net** (Blizzard's online
matchmaking). The server-side game runs on Blizzard's infrastructure; the
players' clients connect to that server. **Casters/observers are
provisioned an additional client connection through Battle.net's
spectator system** — a separate piece of infrastructure with its own
authentication, match-list APIs, and access controls.

Critically:

- The local SC2 client's `RequestCreateGame` / `RequestJoinGame` API we
  use is the **LAN/AI** stack. It is intentionally limited because it's
  not the production multiplayer path.
- The Battle.net spectator path is **server-side**, not client-side. The
  observer client doesn't peer-to-peer with the player clients; it
  connects to Blizzard's relay/spectate server.
- This Battle.net spectate API is not exposed to the local AI client.
  burnysc2's `client.py` and `controller.py` have no Battle.net/spectate
  endpoints.

### What this means for us

Pro broadcasts work because Blizzard runs a server-side spectate relay.
We run our self-play locally on one machine, with no Battle.net
involvement. Replicating the pro setup would require either:
- Running our self-play on Battle.net (not possible — only humans can use
  Battle.net for live AI vs AI; AI matches go through the LAN API), or
- Standing up our own spectate relay server (massive undertaking; likely
  needs Blizzard cooperation)

**Pro path is therefore unreachable from our stack.** But — and this is
important — it doesn't matter, because we don't need a Battle.net-class
spectator. We need any viable single-process omniscient view. There are
several local paths we haven't tried.

---

## 3. The error message, decoded

> `CreateGameError.InvalidPlayerSetup: Only 1v1 is supported when using
> multiple agents`

Parsed against the proto:
- `CreateGameError.InvalidPlayerSetup` enum value at
  [`sc2api_pb2.pyi:160-164`](../../.venv/Lib/site-packages/s2clientprotocol/sc2api_pb2.pyi#L160-L164).
- "Multiple agents" in Blizzard parlance ≈ multiple **API client
  processes** connecting to the same game over LAN ports. (This is
  inferred from Blizzard's own [s2client-proto issues] — verifiable by
  looking at Blizzard's error_messages source if/when we have access.)
- "Only 1v1 supported" = exactly 2 participant slots; no observer slots
  permitted in the player_setup.

So the restriction is specifically:
- **>1 LAN-API client process** + **player_setup containing ≥3 slots** →
  refused.

By contrast:
- **>1 LAN-API client process** + **exactly 2 participant slots** → OK
  (this is our normal 2-bot self-play).
- **1 LAN-API client process** + **any player_setup** → OK (this is
  Bot vs Computer, where Computer is in-game AI hosted in the bot's SC2
  instance, no separate API connection).
- **1 LAN-API client process** + **observer-only join via
  `RequestStartReplay`** → OK (this is replay observation; the API path
  is well-trodden).

This decomposition is what lets us see paths the predecessor
investigation missed.

---

## 4. Untested paths, ranked

### 4.1 STRONG: Camera-controlled embed of one bot window with `show_map`

**The shortest useful path.** Re-read the predecessor investigation §2:
the original recommendation was `disable_fog=True` + embed one bot's
window. The user pushed back that the resulting view is "small," but
that's not actually a constraint — we control the pygame container size,
and we control the embedded SC2 window's render size.

What's new since the predecessor:

- `RequestDebug.game_state = DebugGameState.show_map`
  ([`debug_pb2.pyi:85-97`](../../.venv/Lib/site-packages/s2clientprotocol/debug_pb2.pyi#L85-L97))
  toggles fog of war off **at runtime, per-client**, without setting it
  at create-game time. This means we don't have to decide between
  "training-safe" and "viewable" globally — the embedded bot can have
  show_map on while the other bot keeps it off.
- `ActionRaw.camera_move` (`ActionRawCameraMove` at
  [`raw_pb2.pyi:265`](../../.venv/Lib/site-packages/s2clientprotocol/raw_pb2.pyi#L265))
  programmatically positions a participant client's camera. Combined
  with show_map, the bot's window can frame the entire map from any
  point we choose, just like an observer would.

**Architecture:**
- 2 SC2 processes (current self-play, no new processes)
- One bot client runs our normal `BotAI` (training-safe; show_map off)
- The other bot client either:
  - **(a) Runs a special "viewer-mode" `BotAI`** that issues
    `DebugCommand.show_map` on every step + camera_move to a fixed
    position. Plays minimally (loses every game) — but since it's a
    **viewer-only mode for dev observation**, we don't care about its
    win rate.
  - **(b) Runs the same `BotAI` but with show_map + camera_move
    overlaid as side effects** — the bot still plays normally for
    training, but its rendered window has full vision and our chosen
    camera. **Critical question: does show_map change `self.is_visible`
    for this bot?** If yes, this path is training-unsafe and we fall
    back to (a). If no, it's training-safe.

**Spike to settle (a) vs (b):** small Python script that turns on
show_map on a participant bot mid-game and inspects whether
`self.enemy_units` is now full-map (training affecting) or unchanged
(window-rendering only). 30 minutes of work.

**Risk:** if `show_map` and `disable_fog=True` have identical semantic
effects on bot perception, then (b) is dead and we'd use (a). (a) is
still a viable single-window full-vision viewer — we just give up on the
"both bots play normally" idea by sacrificing one bot's game agency for
viewer purposes. That's a real downgrade from the observer path
(observer never "plays" — it's a watcher), but it's still a working
single-window full-map view.

**Why this is "wider than disable_fog small":** the user's "small"
concern was that the embedded SC2 window in a 1620×910 layout looks
cropped. With show_map + camera_move + render-size set to 1920×1080,
the embedded window can be exactly the same dimensions as the original
plan's observer-window. Wide. Not small. Same visual budget.

**Cost:** ~30 LOC (camera_move + show_map dispatch on one bot's
on_step). Plus a small "viewer-mode" `BotAI` if we go (a).

### 4.2 STRONG: Replay-stream-as-live (in-memory, low-latency)

`RequestSaveReplay` returns the replay as **raw bytes**
([`sc2api_pb2.pyi:398-403`](../../.venv/Lib/site-packages/s2clientprotocol/sc2api_pb2.pyi#L398-L403))
— it does not require writing to disk. Combined with
`RequestStartReplay(replay_data=bytes)`
([`sc2api_pb2.pyi:229-248`](../../.venv/Lib/site-packages/s2clientprotocol/sc2api_pb2.pyi#L229-L248))
which **accepts** raw bytes, we can:

1. Run the 2-bot self-play game normally (2 SC2 processes).
2. Spawn a **3rd SC2 process** that's never involved in the live game.
3. Periodically (every K seconds), call `save_replay` on the host bot's
   client to grab the current replay-so-far as bytes.
4. Pipe those bytes into the 3rd process via `start_replay`. The 3rd
   process plays the replay back from t=0 each refresh. With
   `realtime=False` and a fast step, it can fast-forward to "now" within
   a fraction of a second.

**The "Only 1v1 supported when using multiple agents" restriction does
NOT apply here** because:
- The 3rd process is not joining the live game.
- It's running a **replay**, which is a separate API path.
- From the live game's perspective, only 2 LAN-API agents exist.

**Trade-offs:**
- **Latency:** depends on how often we refresh. Refreshing every second
  → 1s latency on the observer view. Probably acceptable for watching.
- **Compute cost:** the observer process re-plays from t=0 each refresh.
  At fast-forward speed (game_step=1000 or so) a 5-minute game replays
  in ~10 seconds. Wasteful but workable.
- **Server load:** the host bot calls `save_replay` once per second.
  This is cheap (it's already serializing internally) but we should
  verify it doesn't stall the game loop.
- **Memory:** replays are small (~MB scale).

**Risk:** the latency might be noticeable. If we refresh every 5 seconds
the view is 5s behind, which is fine for watching but not for tight
training feedback.

**Cost:** ~150-200 LOC. Architecturally this is a NEW VIEWER MODE
distinct from anything we've built. Potentially more code than 4.1.

### 4.3 MEDIUM-STRONG: pysc2 (Google's SC2 library, different architecture)

[pysc2](https://github.com/google-deepmind/pysc2) is Google's
StarCraft II Python library, used by DeepMind for AlphaStar.

**Why it might be different:**
- pysc2's `LanSC2Env` is a multi-agent environment that's been used by
  DeepMind for live multi-agent training. Their published research
  scaled to 5v5 League games with observers.
- pysc2 has explicit "observe" support that's different from burnysc2's
  Observer class.
- pysc2 may use a different API mode (the "Feature Layer" API) that
  doesn't hit the same restrictions.

**Open questions (that this investigation doesn't answer):**
- Does pysc2's multi-agent environment hit the same
  `InvalidPlayerSetup` server-side error?
- Can pysc2 and burnysc2 coexist in the same project, or do they
  conflict on `sc2_x64.exe` lifecycle?
- Is pysc2 still maintained? (Last commit on GitHub matters.)

**Cost to spike:** medium — need to install pysc2, port a minimal
`pysc2`-based "AggroBot" + observer setup, see what the server says.
Probably 4 hours.

**Risk:** pysc2 may have the same restriction (Blizzard's server is the
same server regardless of which Python wrapper you use). The
SC2 client itself enforces the cap, not burnysc2.

**Recommendation:** worth a 4-hour spike. If pysc2 hits the same error,
we have stronger evidence that the cap is in the SC2 client itself. If
pysc2 succeeds, we pivot the entire stack to pysc2 for the viewer mode.

### 4.4 MEDIUM: Single-process multi-agent (1 SC2 instance, 2 bots, 1 observer)

The error said "multiple agents." What if we configure only **one** SC2
process to run **both** bots (and the observer too)?

burnysc2 supports `BotProcess` objects (see
[`main.py:597-612`](../../.venv/Lib/site-packages/sc2/main.py#L597-L612))
which are bot subprocesses connected via a Proxy pattern. This is the
"ladder bot" mode used by SC2 ladders:
- One SC2 process hosts the game.
- Bots run as separate Python subprocesses on the same machine.
- They connect via a proxy (one TCP connection multiplexes through SC2's
  single websocket).

**The hypothesis:** if we use `BotProcess` for the two bots and `Observer`
for the third, only ONE SC2 process exists, only ONE `multiple agents`
LAN connection exists, and the server's restriction may not trigger
because there isn't actually multi-instance contention.

**Open questions:**
- Does `BotProcess` compose with `Observer`? burnysc2's `run_match`
  iterates `players_that_need_sc2`, and an Observer is one of those.
  Untested combination.
- Can ONE SC2 process render BOTH bots' perspectives + observer's view?
  (Probably not — each SC2 process renders one player's view.)
- If the SC2 process can only render one perspective at a time, this
  doesn't actually solve our viewing problem.

**This path is a long shot** — the SC2 server probably enforces the cap
at the game-creation layer, not at the API-client-count layer. But the
distinction between "1 process running 2 bots via BotProcess + 1
observer" vs "3 processes" is potentially meaningful and worth a quick
test.

**Cost:** ~3 hours to spike with `BotProcess`.

### 4.5 MEDIUM: Custom renderer on `RequestObservation` data

The `Observation` proto returned by `RequestObservation` includes raw
unit positions, terrain, fog of war, abilities — everything an observer
would render. We could:

1. Run 2-bot self-play normally.
2. Periodically read `Observation` from one of the bot clients (raw
   data, not the rendered SC2 window).
3. Render our own minimap-style top-down view in pygame using that
   data — units as colored dots, structures as squares, terrain as a
   pre-rendered map background.

**Trade-offs:**
- **Pro:** zero extra SC2 processes, zero API workarounds, total
  rendering control.
- **Pro:** can be much wider/larger than any embedded SC2 window.
- **Con:** we'd be re-implementing SC2's renderer. At minimum we need
  unit sprites, terrain backgrounds, building outlines, fog-of-war
  shading. That's a lot of art and a lot of code.
- **Con:** the user's "wide observer view" is partially about visual
  fidelity — actual SC2 graphics. Our renderer wouldn't have that.

**Effective form:** "minimap-on-steroids." Useful for strategic overview
but not a substitute for actual SC2 visuals.

**Cost:** 2-3 days for a minimal version. Could grow indefinitely.

### 4.6 MEDIUM: "Viewer-mode" bot with show_map + camera_move (variant of 4.1)

Same idea as 4.1(a), called out separately because it's the path of
**least risk:**

- 2 bots play (training-safe): bot1 runs our real `Alpha4Bot`; bot2
  runs a `ViewerBot` that surrenders immediately and lets bot1 win.
- bot2's SC2 window has show_map on + camera_move set to map center.
- Embed bot2's window in pygame container at large size.
- Result: a watching view of bot1 playing against a passive opponent.

**Limitation:** bot2 doesn't actually play, so we're watching bot1 vs
nothing. **Not useful for self-play observation** — the whole point is
watching two bots fight.

So 4.6 is **strictly worse** than 4.1(b), which would let us watch two
real bots. We list it for completeness; cross it off if 4.1(b) works.

### 4.7 WEAK: Linux multi-instance differences

burnysc2 has Linux-specific code at
[`main.py:694-697`](../../.venv/Lib/site-packages/sc2/main.py#L694-L697)
that serializes SC2 client startup ("Works on linux: start one client
after the other"). This implies Linux behaves differently than Windows
in some multi-instance scenarios. **Whether the
"InvalidPlayerSetup" cap behaves differently on Linux is unverified.**

To test we'd need WSL or a Linux dev box with SC2 installed. SC2 on
Linux is community-supported (Wine wrappers) and may have different
edge cases. Probably not worth the setup overhead unless 4.1, 4.2, and
4.3 all fail.

### 4.8 DEAD: Approaches the spikes already ruled out

- 3-player `RequestCreateGame` with `[Bot, Bot, Observer]` (Spike 1)
- Post-create observer `RequestJoinGame` (Spike 2)

These don't need re-investigation.

---

## 5. Comparison of the live workarounds

| # | Approach | Wide view? | Live? | Training-safe? | New SC2 procs | Cost | Verdict |
|---|---|---|---|---|---|---|---|
| 4.1(b) | Embed bot1 window with show_map + camera_move | Yes (any size) | Yes | **Maybe** — needs spike | 0 | ~30 LOC + 30-min spike | **Try first** |
| 4.1(a) | Embed bot2's "viewer-mode" window | Yes | Yes | Yes (bot2 sacrificed) | 0 | ~50 LOC | Solid fallback |
| 4.2 | Replay-stream-as-live (3rd process plays replay) | Yes | ~1-5s lag | Yes | 1 (no live join) | ~150-200 LOC | Strong if 4.1 fails |
| 4.3 | pysc2 multi-agent + observer | Yes | Yes | Unknown | Unknown | 4-hour spike | Worth checking |
| 4.4 | Single-process multi-agent + observer | Probably no | Yes | Unknown | -1 | 3-hour spike | Long shot |
| 4.5 | Custom renderer on Observation data | Yes (custom) | Yes | Yes | 0 | 2-3 days | Heavy |
| 4.7 | Linux multi-instance | Unknown | Unknown | Unknown | 3 | High setup cost | Defer |

The key insight: **we have at least one viable live path that doesn't
require any of the dead approaches.** 4.1(b) is the cheapest spike, and
if it confirms show_map is rendering-only (doesn't change
`self.enemy_units`), we have our viewer.

---

## 6. Recommended spike sequence

Time-boxed; halt and reassess at each step.

### Spike A: show_map effect on bot perception (30 min)

**Question:** Does `RequestDebug.game_state = DebugGameState.show_map`
on a participant client change `self.enemy_units` /
`self.enemy_structures` for that bot?

**Test:** small `BotAI` subclass:
- Iter 0-100: assert `self.enemy_units` is empty / fog-of-war-limited.
  Print sizes.
- Iter 100: send `DebugCommand` with `game_state=DebugGameState.show_map`.
- Iter 101-200: assert `self.enemy_units` is now full-map (or unchanged,
  depending on result). Print sizes.

**Outcomes:**
- `enemy_units` count jumps after iter 100 → show_map IS perception-affecting →
  4.1(b) is training-unsafe → fall back to 4.1(a).
- `enemy_units` count unchanged after iter 100 → show_map is rendering-only →
  **4.1(b) is the viable path** → build the viewer.

**Cost:** 30 min. No new architecture; reuses our existing
2-bot self-play.

### Spike B (only if Spike A says show_map is perception-affecting): camera_move on a sacrificed bot (1 hour)

**Question:** Can we embed bot2's window with full vision + good camera
position, where bot2 is a ViewerBot that surrenders immediately?

**Test:**
- Run 2-bot game with bot1 = our real `Alpha4Bot`, bot2 = `ViewerBot`.
- bot2 sends camera_move + show_map on iter 0, then surrenders via
  `RequestDebug(end_game=DebugEndGame.Surrender)` only when we choose
  (e.g., on Esc keypress in pygame). Until surrender, it idles.
- Verify bot2's window renders the full map with our camera position.
- Verify the bots' game does NOT end in 8 seconds (bot1 should be able
  to play normally for several minutes against an idle bot2).

**Outcomes:**
- Works → 4.1(a) is the viewer; ship it.
- Doesn't work → drop to 4.2 (replay streaming).

**Cost:** 1 hour.

### Spike C (only if both A and B fail): replay-stream-as-live (4 hours)

**Question:** Can a 3rd SC2 process play back an in-memory replay that
the host bot client refreshes every K seconds, achieving a usable
live-ish observer view?

**Test:**
- 3rd SC2Process spawned at startup (no `join_game`; only `start_replay`).
- Background task in `_run_single_game`: every 1s, call `save_replay`
  on host client → bytes → push into 3rd process via `start_replay`.
- Verify the 3rd process renders a coherent view that lags behind the
  live game by ~1-2s.
- Verify `save_replay` doesn't stall the host bot's step loop.

**Outcomes:**
- Works → 4.2 is the viewer.
- Doesn't work → 4.5 (custom renderer) or revert to 2-pane.

**Cost:** 4 hours.

### Spike D (only if A, B, C all fail): pysc2 (4 hours)

**Question:** Does pysc2's multi-agent + observer environment hit the
same server cap as burnysc2?

**Test:**
- Install pysc2 in a side venv (don't pollute main project).
- Minimal pysc2 game: 2 random-action agents + observer slot.
- See what error (if any) the server returns.

**Outcomes:**
- pysc2 hits the same `InvalidPlayerSetup` → strong evidence the cap is
  in the SC2 client itself; observer path is dead.
- pysc2 succeeds → pivot the viewer to pysc2 (significant rework).

**Cost:** 4 hours.

---

## 7. Recommendation

**Run Spike A first** (30 min). It's the smallest information-bearing
test in the entire decision tree, and its outcome immediately tells us
whether 4.1(b) is the viewer (best case: ~2 hours total to ship a
working wide single-window viewer) or whether we need to cascade into B
or C.

The user's "small" objection to the predecessor investigation's Option
1 is **resolved** by 4.1: the embedded SC2 window can be exactly the
same dimensions (1920×1080) as the original plan's observer-window. The
visual real-estate budget is identical to the dead Path A. We were
solving the wrong constraint — "small" wasn't about the actual pixel
size, it was about the camera being stuck on one bot's start position.
camera_move solves that without an observer client.

**Order to run:**

1. Spike A (show_map perception test) — 30 min — DECISIVE
2. Spike B (camera_move + sacrificed viewer-bot) — 1 hour — if A says no
3. Replan around 4.1 (whichever variant survived A & B)
4. (Defer C & D unless 4.1 path also fails)

If 4.1 ships, the original `selfplay-viewer-plan.md` is salvaged with
modest revisions: 7 steps → ~5 steps, drop Step 1 spike (we already
spiked), drop `_play_observer` coroutine and dispatch patch, replace
with show_map + camera_move dispatch on one bot client. Container
refactor to single-pane stays. End-to-end and soak steps stay.

---

## 8. What this doc deliberately does not do

- It does not run any spikes. Spike A is described, not executed. The
  decision of which path to invest in is the user's.
- It does not propose code for any path. All snippets are sketches.
- It does not declare any of 4.1–4.4 "the answer." They are ranked
  candidates; only spikes settle the question.
- It does not revisit the dead approaches (4.8) — the spikes that ruled
  them out are documented in the soak doc.

The user's pushback is valid: "Path A is dead" was correct only for the
narrow definition of "Path A" used in the original plan (3-process live
observer). The broader goal — single-window full-map view of bot
self-play — is **still reachable**, probably via 4.1.
