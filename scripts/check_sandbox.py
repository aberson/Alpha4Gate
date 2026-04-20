#!/usr/bin/env python
"""Pre-commit sandbox enforcement hook.

Two autonomous-commit modes are supported, mutually exclusive:

* ``ADVISED_AUTO=1``: only files under ``bots/current/`` may be committed.
* ``EVO_AUTO=1``: files under ``bots/`` (any version dir) may be committed.

Human commits (neither env var set to "1") pass through unconditionally.
Setting both env vars to "1" is a conflict and fails loudly.
"""

from __future__ import annotations

import os
import posixpath
import subprocess
import sys


def main() -> int:
    advised = os.environ.get("ADVISED_AUTO") == "1"
    evo = os.environ.get("EVO_AUTO") == "1"

    # Human commit passthrough: skip unless an automated mode is active.
    if not advised and not evo:
        return 0

    # Conflict: both modes set simultaneously represent different skills
    # with different scope philosophies. Fail loudly rather than pick one.
    if advised and evo:
        print("=" * 60, file=sys.stderr)
        print("SANDBOX CONFLICT — commit blocked", file=sys.stderr)
        print("=" * 60, file=sys.stderr)
        print(file=sys.stderr)
        print(
            "Both ADVISED_AUTO=1 and EVO_AUTO=1 are set. These modes have",
            file=sys.stderr,
        )
        print(
            "different scopes (bots/current/ vs bots/) and must not be combined.",
            file=sys.stderr,
        )
        print("Unset one of them and retry.", file=sys.stderr)
        print("=" * 60, file=sys.stderr)
        return 1

    result = subprocess.run(
        ["git", "diff", "--cached", "--name-only"],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        print("check_sandbox: failed to run git diff --cached --name-only", file=sys.stderr)
        print(result.stderr, file=sys.stderr)
        return 1

    staged = [line for line in result.stdout.splitlines() if line.strip()]
    if not staged:
        return 0

    if advised:
        allowed_prefix = "bots/current/"
        allowed_dir = "bots/current"
        mode_name = "ADVISED_AUTO"
        scope_desc = "only files under bots/current/ are allowed"
    else:
        # evo mode
        allowed_prefix = "bots/"
        allowed_dir = "bots"
        mode_name = "EVO_AUTO"
        scope_desc = "only files under bots/ (any version dir) are allowed"

    forbidden: list[str] = []

    for path in staged:
        normalized = posixpath.normpath(path.replace("\\", "/"))
        # normpath strips trailing slash, so "bots/current/foo.py" stays as-is.
        # We need startswith check against the allowed prefix — include the
        # bare directory name as a special case for the (unlikely) dir itself.
        if not (normalized == allowed_dir or normalized.startswith(allowed_prefix)):
            forbidden.append(normalized)

    if forbidden:
        print("=" * 60, file=sys.stderr)
        print("SANDBOX VIOLATION — commit blocked", file=sys.stderr)
        print("=" * 60, file=sys.stderr)
        print(file=sys.stderr)
        print(
            f"{mode_name}=1 is set, so {scope_desc}.",
            file=sys.stderr,
        )
        print(file=sys.stderr)
        print("Forbidden paths:", file=sys.stderr)
        for p in forbidden:
            print(f"  - {p}", file=sys.stderr)
        print(file=sys.stderr)
        print(
            f"If this is a human commit, unset {mode_name} or set it to something other than '1'.",
            file=sys.stderr,
        )
        print("=" * 60, file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
