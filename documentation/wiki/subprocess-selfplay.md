# Subprocess Self-Play (Phase 0 Spike)

## Status

**Phase 0 gate: PASS** — 2026-04-15, commit `master-plan/0/baseline` + spike commit on branch `master-plan/0/subprocess-spike`.

The spike proved that two independent Python subprocesses, each hosting a burnysc2 Bot, can play a 1v1 match on Simple64 with no in-process sharing. This unblocks the versioning track (Phases 1–6) of the master plan.

## Result

```json
{
  "elapsed_seconds": 22.42,
  "match_result": {"Spike-p1": "Victory", "Spike-p2": "Defeat"},
  "per_bot": {
    "p1": {"role": "p1", "result": "Victory",      "steps": 200, "actions_issued": 4},
    "p2": {"role": "p2", "result": "Surrendering", "steps": 200, "actions_issued": 4}
  }
}
```

Both subprocesses spawned, connected to their respective SC2 clients, issued actions, and the match terminated decisively with no orphaned SC2 processes.

## Architecture

burnysc2 7.1.3 supports subprocess self-play via `BotProcess` + `Proxy` + `run_match`. For a 1v1 match where both players are `BotProcess`:

```
+----------------+         +----------------+
| SC2 instance A |<------->| SC2 instance B |   ← LAN networked via Portconfig
+----------------+         +----------------+
        ^                          ^
        |   s2client-proto         |   s2client-proto
        |   (protobuf over WS)     |
+----------------+         +----------------+
| Proxy (WS svr) |         | Proxy (WS svr) |   ← localhost:pport_a / pport_b
+----------------+         +----------------+
        ^                          ^
        |                          |
+----------------+         +----------------+
| Bot subprocess |         | Bot subprocess |   ← python scripts/spike_bot_stub.py
| (p1)           |         | (p2)           |
+----------------+         +----------------+
```

Key facts:

- **Two SC2 processes**, one per `BotProcess` player. `GameMatch.needed_sc2_count == 2`.
- **Two Proxy WebSocket servers**, one per subprocess bot, running on localhost on portpicker-assigned ports.
- **Two bot subprocesses**, each a `subprocess.Popen` spawned by its Proxy. Each bot connects to `ws://127.0.0.1:<pport>/sc2api`.
- **LAN handshake** between the two SC2s uses a `Portconfig` derived from a shared `--StartPort` passed to both bots on their command lines.

## Bot-subprocess CLI contract

Each bot subprocess MUST accept the standard ladder ports (matches sc2ai / aiarena):

| Flag | Meaning |
|------|---------|
| `--GamePort <int>` | WebSocket port exposed by the bot's Proxy (NOT a direct SC2 port) |
| `--LadderServer <host>` | Usually `127.0.0.1` |
| `--StartPort <int>` | Base port for deriving the shared `Portconfig` |
| `--RealTime` | Optional flag for realtime mode |

The bot reconstructs the shared `Portconfig` from `--StartPort N` as:

```python
Portconfig(
    server_ports=[N + 2, N + 3],
    player_ports=[[N + 4, N + 5]],
)
```

This matches the offset convention used by `burnysc2.main.run_match`:

```python
portconfig = Portconfig.contiguous_ports()
startport = portconfig.server[0] - 2   # passed to bots as --StartPort
```

The bot joins the already-hosted game with:

```python
await play_from_websocket(
    f"ws://{args.LadderServer}:{args.GamePort}/sc2api",
    Bot(Race.Protoss, bot_instance, name=...),
    realtime=args.RealTime,
    portconfig=Portconfig(server_ports=[N+2, N+3], player_ports=[[N+4, N+5]]),
)
```

## Orchestrator API

See [scripts/spike_subprocess_selfplay.py](../../scripts/spike_subprocess_selfplay.py). Minimal shape:

```python
from sc2 import maps
from sc2.main import GameMatch, a_run_multiple_games
from sc2.player import BotProcess
from sc2.data import Race

match = GameMatch(
    map_sc2=maps.get("Simple64"),
    players=[
        BotProcess(path=repo_root, launch_list=[python, stub_path, "--role", "p1", ...], race=Race.Protoss),
        BotProcess(path=repo_root, launch_list=[python, stub_path, "--role", "p2", ...], race=Race.Protoss),
    ],
    realtime=False,
    random_seed=42,
    game_time_limit=300,
)
results = asyncio.run(a_run_multiple_games([match]))
# results[0] -> {BotProcess("Spike-p1"): Result.Victory, BotProcess("Spike-p2"): Result.Defeat}
```

## Known issues and workarounds

### 1. Port-collision bug in `Portconfig.contiguous_ports`

`Portconfig.contiguous_ports()` uses `portpicker.pick_unused_port()` only for the FIRST (`start`) port. The remaining 4 contiguous LAN ports (`start+1` through `start+4`) are verified free via `is_port_free` but NOT reserved in portpicker's `_owned_ports`. When `run_match` subsequently picks WebSocket Proxy ports with `portpicker.pick_unused_port()`, it can return those same ports. SC2 then deadlocks because its LAN listener cannot bind ports the Proxy WS already holds.

**Symptom:** Both SC2 windows stay on a black loading screen indefinitely. Log shows `Starting bot with command ...` and then no further progress. The match never reaches `Status.in_game`.

**Workaround** (in the orchestrator): monkey-patch `Portconfig.contiguous_ports` to push all picked ports into a blocklist, and wrap `portpicker.pick_unused_port` to retry whenever it returns a blocked port. See the `_BLOCKED_PORTS` / `_pick_avoiding_blocked` patch block at the top of [scripts/spike_subprocess_selfplay.py](../../scripts/spike_subprocess_selfplay.py).

**Upstream status:** unfixed in burnysc2 7.1.3. The orchestrator (Phase 2+) will own this patch as part of `src/orchestrator/selfplay.py`.

### 2. Bot `on_end` does not always fire

When a bot calls `await self.client.leave()` to surrender, the WebSocket can close before the `on_end` callback runs. Symptom: the per-bot result JSON stays un-updated. The spike bot works around this by writing a heartbeat file every 50 steps AND again immediately before calling `leave()`. Production bots should do the same, or persist game state on `on_step` rather than relying on `on_end`.

### 3. Python 3.14 `asyncio.get_event_loop`

`burnysc2.main.run_multiple_games` (the sync wrapper) calls the deprecated `asyncio.get_event_loop()`, which raises on Python 3.14 if no loop is currently running. Use the async version directly: `asyncio.run(a_run_multiple_games([match]))`.

## Failure modes verified as NOT occurring

- **Orphan SC2 processes:** burnysc2's `KillSwitch.kill_all()` cleans up spawned clients on normal exit. The user's main-menu SC2 client (spawned separately) is never touched.
- **Hung LAN handshake:** with the port-collision fix, the two SC2 clients complete the handshake and enter `Status.in_game` within ~10 seconds of spawning.
- **Result reporting:** both players' results are available via `a_run_multiple_games` return value AND per-bot result files.

## What this does NOT yet prove

- **Crash hygiene under subprocess failure.** If a bot crashes mid-game, Phase 3 needs to verify the orchestrator doesn't leak SC2. The spike only tested a clean surrender.
- **Batch self-play across multiple games.** Phase 3 will add a `--games N` loop with alternating seats and PFSP-lite sampling.
- **Full-stack bot import.** The spike used a minimal BotAI stub, not the full Alpha4Gate bot stack. Phase 1 moves the real bot into `bots/v0/` and adds a `python -m bots.v0 --role {p1|p2|solo}` entry-point that accepts the same ladder CLI contract.

## References

- burnysc2 source: `.venv/Lib/site-packages/sc2/main.py` (`run_match`, `a_run_multiple_games`), `sc2/proxy.py`, `sc2/player.py` (`BotProcess`), `sc2/portconfig.py`.
- Master plan: [documentation/plans/alpha4gate-master-plan.md](../plans/alpha4gate-master-plan.md) Phase 0.
- Spike artifacts: `data/phase0_spike/spike_summary.json`, per-bot result JSON, stdout logs.
