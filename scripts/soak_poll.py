"""Periodic state poller for soak tests.

Hits a fixed set of training/daemon REST endpoints on a configurable interval and
appends one JSONL row per poll to an artifact file. Designed to run unattended in a
third terminal alongside the backend (terminal 1) and frontend (terminal 2) during a
soak test, capturing the structured state evidence the run-log timeline would
otherwise have to reconstruct from screenshots.

Run from the project root:

    uv run python scripts/soak_poll.py

Default output: ~/soak-artifacts/<YYYY-MM-DD>/daemon-state.jsonl
Default interval: 60 seconds
Default base URL: http://localhost:8765

Stop with Ctrl+C. The script flushes after every poll, so a kill mid-run loses at
most one row.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ENDPOINTS: dict[str, str] = {
    "daemon": "/api/training/daemon",
    "triggers": "/api/training/triggers",
    "curriculum": "/api/training/curriculum",
    "checkpoints": "/api/training/checkpoints",
    "promotions": "/api/training/promotions",
    "stats": "/api/stats",
}


def fetch_json(url: str, timeout: float = 5.0) -> dict[str, Any] | None:
    """GET a URL and return parsed JSON, or None on any failure (logged to stderr)."""
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            body = resp.read().decode("utf-8")
            return json.loads(body)
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, OSError) as exc:
        print(f"  ! fetch failed for {url}: {exc}", file=sys.stderr)
        return None
    except json.JSONDecodeError as exc:
        print(f"  ! json parse failed for {url}: {exc}", file=sys.stderr)
        return None


def poll_once(base_url: str) -> dict[str, Any]:
    """Hit every endpoint once and return a single row dict."""
    row: dict[str, Any] = {
        "ts_local": datetime.now().isoformat(timespec="seconds"),
        "ts_utc": datetime.now(UTC).isoformat(timespec="seconds"),
    }
    for key, path in ENDPOINTS.items():
        row[key] = fetch_json(base_url + path)
    return row


def summarize(row: dict[str, Any]) -> str:
    """Build a one-line human-readable summary of a poll row for terminal output."""
    daemon = row.get("daemon") or {}
    triggers = row.get("triggers") or {}
    curriculum = row.get("curriculum") or {}
    checkpoints = row.get("checkpoints") or {}
    promotions = row.get("promotions") or {}

    state = daemon.get("state", "?")
    runs = daemon.get("runs_completed", "?")
    transitions = triggers.get("transitions_since_last", "?")
    diff = curriculum.get("current_difficulty", "?")
    cp_count = len(checkpoints.get("checkpoints", []) or [])
    promo_count = len(promotions.get("promotions", []) or [])

    return (
        f"state={state} runs={runs} transitions={transitions} "
        f"difficulty={diff} checkpoints={cp_count} promotions={promo_count}"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--interval", type=float, default=60.0, help="Seconds between polls (default: 60)"
    )
    parser.add_argument(
        "--base-url",
        default="http://localhost:8765",
        help="Backend base URL (default: http://localhost:8765)",
    )
    parser.add_argument(
        "--output",
        default=None,
        help=(
            "Output JSONL path. Default: "
            "~/soak-artifacts/<YYYY-MM-DD>/daemon-state.jsonl"
        ),
    )
    args = parser.parse_args()

    today = datetime.now().strftime("%Y-%m-%d")
    output_path = Path(
        args.output
        if args.output is not None
        else f"~/soak-artifacts/{today}/daemon-state.jsonl"
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)

    print(f"soak_poll: writing to {output_path}", flush=True)
    print(f"soak_poll: interval={args.interval}s base_url={args.base_url}", flush=True)
    print("soak_poll: Ctrl+C to stop", flush=True)

    poll_count = 0
    try:
        with output_path.open("a", encoding="utf-8") as out:
            while True:
                row = poll_once(args.base_url)
                out.write(json.dumps(row) + "\n")
                out.flush()
                poll_count += 1
                print(f"[{row['ts_local']}] poll #{poll_count}: {summarize(row)}", flush=True)
                time.sleep(args.interval)
    except KeyboardInterrupt:
        print(f"\nsoak_poll: stopped after {poll_count} polls. Output: {output_path}", flush=True)
        return 0


if __name__ == "__main__":
    sys.exit(main())
