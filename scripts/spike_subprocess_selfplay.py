"""Phase 0 spike — subprocess self-play orchestrator.

Proves that two independent Python subprocesses, each hosting a burnysc2 Bot,
can play a single 1v1 match on Simple64 with no in-process sharing.

Architecture (resolved from burnysc2 7.1.3 sources):
  - `run_multiple_games` spins up TWO SC2 clients via `maintain_SCII_count`.
  - For each `BotProcess`, a `Proxy` runs a local WebSocket server and spawns
    the bot subprocess via `subprocess.Popen`.
  - The bot subprocess connects to `ws://127.0.0.1:<proxy_port>/sc2api`
    and plays via `play_from_websocket`.
  - Both bots receive the same `--StartPort` so they derive identical
    Portconfigs and SC2s network via the LAN protocol.

Pass criteria (Phase 0 gate):
  1. Both subprocesses spawn and connect.
  2. Each bot issues at least one action (proves action path works).
  3. The game terminates with a decisive Victory/Defeat (not hang).
  4. `data/phase0_spike/spike_summary.json` is well-formed JSON.

Run from repo root:
  uv run python scripts/spike_subprocess_selfplay.py
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT / "src"))

from orchestrator.paths import resolve_sc2_path  # noqa: E402

os.environ.setdefault("SC2PATH", str(resolve_sc2_path()))

import asyncio

import portpicker
from sc2 import maps
from sc2.data import Race
from sc2.main import GameMatch, a_run_multiple_games
from sc2.player import BotProcess
from sc2.portconfig import Portconfig

# burnysc2 7.1.3 bug: Portconfig.contiguous_ports reserves only `start` with
# portpicker. The 4 adjacent ports it picks for server/player LAN traffic are
# not registered as owned, so the subsequent Proxy `pick_unused_port()` calls
# can hand back those same ports. SC2 then deadlocks because its LAN listener
# can't bind ports the Proxy WS already holds. We monkey-patch both sides:
# contiguous_ports registers all picked ports with a shared blocklist, and
# pick_unused_port retries whenever it returns a blocked port.
_BLOCKED_PORTS: set[int] = set()
_ORIG_PICK_UNUSED_PORT = portpicker.pick_unused_port
_ORIG_CONTIGUOUS_PORTS = Portconfig.contiguous_ports


def _pick_avoiding_blocked(*args, **kwargs):
    for _ in range(64):
        port = _ORIG_PICK_UNUSED_PORT(*args, **kwargs)
        if port not in _BLOCKED_PORTS:
            return port
    raise portpicker.NoFreePortFoundError()


@classmethod  # type: ignore[misc]
def _contiguous_ports_with_blocklist(cls, guests: int = 1, attempts: int = 40):
    pc = _ORIG_CONTIGUOUS_PORTS(guests=guests, attempts=attempts)
    _BLOCKED_PORTS.update(pc.server)
    for pair in pc.players:
        _BLOCKED_PORTS.update(pair)
    return pc


portpicker.pick_unused_port = _pick_avoiding_blocked
Portconfig.contiguous_ports = _contiguous_ports_with_blocklist


REPO_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = REPO_ROOT / "data" / "phase0_spike"
STUB = REPO_ROOT / "scripts" / "spike_bot_stub.py"


def _bot_process(role: str) -> BotProcess:
    return BotProcess(
        path=str(REPO_ROOT),
        launch_list=[
            sys.executable,
            str(STUB),
            "--role",
            role,
            "--result-out",
            str(DATA_DIR / f"{role}_result.json"),
        ],
        race=Race.Protoss,
        name=f"Spike-{role}",
        stdout=str(DATA_DIR / f"{role}_stdout.log"),
    )


def _build_match() -> GameMatch:
    return GameMatch(
        map_sc2=maps.get("Simple64"),
        players=[_bot_process("p1"), _bot_process("p2")],
        realtime=False,
        random_seed=42,
        game_time_limit=300,
    )


def _load_bot_result(role: str) -> dict | None:
    path = DATA_DIR / f"{role}_result.json"
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text())
    except json.JSONDecodeError:
        return None


def main() -> int:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    for role in ("p1", "p2"):
        stale = DATA_DIR / f"{role}_result.json"
        if stale.exists():
            stale.unlink()

    match = _build_match()
    print(f"Starting spike match: {match}")
    t0 = time.monotonic()
    raw_results = asyncio.run(a_run_multiple_games([match]))
    elapsed = time.monotonic() - t0

    match_result = raw_results[0] if raw_results else None
    summary: dict = {
        "elapsed_seconds": round(elapsed, 2),
        "match_result": None,
        "per_bot": {},
    }
    if match_result:
        summary["match_result"] = {
            player.name: (result.name if result is not None else None)
            for player, result in match_result.items()
        }
    for role in ("p1", "p2"):
        summary["per_bot"][role] = _load_bot_result(role)

    (DATA_DIR / "spike_summary.json").write_text(json.dumps(summary, indent=2))
    print(json.dumps(summary, indent=2))

    p1 = summary["per_bot"].get("p1") or {}
    p2 = summary["per_bot"].get("p2") or {}
    both_acted = p1.get("actions_issued", 0) > 0 and p2.get("actions_issued", 0) > 0
    decisive = False
    if summary["match_result"]:
        outcomes = set(summary["match_result"].values())
        decisive = {"Victory", "Defeat"}.issubset(outcomes)

    ok = bool(match_result) and both_acted and decisive
    print(f"\nPhase 0 gate: {'PASS' if ok else 'FAIL'}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
