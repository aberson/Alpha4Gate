"""Publish current Alpha4Gate soak state as a GitHub Gist for remote monitoring.

First run creates a secret gist and writes the ID to ``.soak-monitor-gist.id``
(gitignored). Subsequent runs ``PATCH`` the existing gist atomically. Cloud
agents (or anyone with the URL) can fetch the gist contents without auth —
secret gists are accessible by URL alone, just not enumerable.

Requires the ``gh`` CLI to be on PATH and authenticated with at least the
``gist`` scope. ``gh auth refresh -s gist`` if missing.

Usage::

    python scripts/publish_soak_state.py            # publish (create or update)
    python scripts/publish_soak_state.py --url      # print existing gist URL
"""

from __future__ import annotations

import argparse
import datetime
import json
import pathlib
import subprocess
import sys
from typing import Any

_REPO = pathlib.Path(__file__).resolve().parent.parent
_GIST_ID_FILE = _REPO / ".soak-monitor-gist.id"
_MAX_FILE_BYTES = 200_000  # gist API allows ~1 MB; staying conservative


def _run(cmd: list[str], **kw: Any) -> subprocess.CompletedProcess[str]:
    # ``encoding="utf-8"`` is required on Windows where the default locale is
    # cp1252 — ``gh`` emits UTF-8 (gist content can include non-ASCII like the
    # em-dashes in our docs) and the subprocess reader thread crashes mid-read
    # without an explicit encoding override.
    return subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        check=True,
        encoding="utf-8",
        **kw,
    )


def _read_tail(path: pathlib.Path, *, tail_lines: int | None = None) -> str | None:
    """Return file content (optionally tailed). Returns None for missing or
    empty files — gist API rejects empty file content with HTTP 422."""
    if not path.is_file():
        return None
    text = path.read_text(encoding="utf-8", errors="replace")
    if tail_lines is not None:
        text = "\n".join(text.splitlines()[-tail_lines:])
    if len(text) > _MAX_FILE_BYTES:
        text = "...[truncated to last 200KB]\n" + text[-_MAX_FILE_BYTES:]
    if not text.strip():
        return None
    return text


def _collect_state() -> dict[str, str]:
    files: dict[str, str] = {}

    state = _read_tail(_REPO / "data" / "evolve_run_state.json")
    if state is not None:
        files["01-run_state.json"] = state

    logs = sorted(
        (_REPO / "logs").glob("evolve-linux-8h-*.log"),
        key=lambda p: p.stat().st_mtime,
    )
    latest_log_name = "(none)"
    if logs:
        latest = logs[-1]
        latest_log_name = latest.name
        tail = _read_tail(latest, tail_lines=300)
        if tail is not None:
            files["02-log_tail.txt"] = f"# Source: {latest.name}\n\n{tail}"

    results = _read_tail(_REPO / "data" / "evolve_results.jsonl", tail_lines=80)
    if results is not None:
        files["03-results_tail.jsonl"] = results

    try:
        recent_commits = _run(
            ["git", "log", "--oneline", "--since=12 hours ago", "-50"],
            cwd=_REPO,
        ).stdout
    except subprocess.CalledProcessError as exc:
        recent_commits = f"(git log failed: {exc})"
    files["04-recent_commits.txt"] = recent_commits.strip() or "(no commits in last 12h)"

    pool = _read_tail(_REPO / "data" / "evolve_pool.json")
    if pool is not None:
        files["05-pool.json"] = pool

    summary_lines = [
        "# Alpha4Gate soak monitor",
        f"Generated:  {datetime.datetime.now(datetime.UTC).isoformat()}",
        f"Repo HEAD:  {_run(['git', 'log', '--oneline', '-1'], cwd=_REPO).stdout.strip()}",
        f"Latest log: {latest_log_name}",
    ]
    if state is not None:
        try:
            s = json.loads(state)
            summary_lines += [
                "",
                "## Soak run state",
                f"- status:                {s.get('status')}",
                f"- started_at:            {s.get('started_at')}",
                f"- generation_index:      {s.get('generation_index')}",
                f"- generations_completed: {s.get('generations_completed')}",
                f"- generations_promoted:  {s.get('generations_promoted')}",
                f"- evictions:             {s.get('evictions')}",
                f"- parent_current:        {s.get('parent_current')}",
                f"- parent_start:          {s.get('parent_start')}",
                f"- wall_budget_hours:     {s.get('wall_budget_hours')}",
            ]
        except json.JSONDecodeError:
            pass
    files["00-summary.md"] = "\n".join(summary_lines) + "\n"

    return files


def _create_gist(files: dict[str, str], description: str) -> str:
    payload = {
        "description": description,
        "public": False,
        "files": {name: {"content": content} for name, content in files.items()},
    }
    result = _run(
        ["gh", "api", "gists", "-X", "POST", "--input", "-"],
        input=json.dumps(payload),
    )
    return json.loads(result.stdout)["id"]


def _update_gist(gist_id: str, files: dict[str, str]) -> None:
    payload = {
        "files": {name: {"content": content} for name, content in files.items()},
    }
    _run(
        ["gh", "api", f"gists/{gist_id}", "-X", "PATCH", "--input", "-"],
        input=json.dumps(payload),
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--url",
        action="store_true",
        help="Print existing gist URL and exit (no upload).",
    )
    args = parser.parse_args()

    if args.url:
        if not _GIST_ID_FILE.exists():
            print("No gist exists yet. Run without --url to create one.", file=sys.stderr)
            return 1
        print(f"https://gist.github.com/{_GIST_ID_FILE.read_text().strip()}")
        return 0

    files = _collect_state()
    if not files:
        print("No state files to publish.", file=sys.stderr)
        return 1

    if _GIST_ID_FILE.exists():
        gist_id = _GIST_ID_FILE.read_text().strip()
        _update_gist(gist_id, files)
        print(f"Updated gist ({len(files)} files): https://gist.github.com/{gist_id}")
    else:
        gist_id = _create_gist(files, "Alpha4Gate soak monitor")
        _GIST_ID_FILE.write_text(gist_id)
        print(f"Created gist ({len(files)} files): https://gist.github.com/{gist_id}")
        print(f"Saved gist ID to {_GIST_ID_FILE.relative_to(_REPO)}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
