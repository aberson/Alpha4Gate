"""Repo hygiene: no operator home identity in the tracked tree.

Shape-based, not secret-based. A guard that grepped for the literal account name
would itself contain that name and fail its own assertion. Instead this asserts that
no home path in the tracked tree names a real account -- only the placeholder ``x``
or a shell variable. Consequence: it also catches a FUTURE account name.

THREE regexes, because this repo leaked in three shapes that no single one covers:

  * Windows drive paths (``C:\\Users\\<name>``, either separator, either case).
  * POSIX / WSL paths (``/home/<name>``, ``/mnt/c/Users/<name>``, ``/c/Users/<name>``).
    A Windows-only pattern misses 39 of the 80 occurrences scrubbed here.
  * The Claude project-dir slug ``c--Users-<name>-Alpha4Gate``, which carries the
    account name with NO path separator and so matches no path-shaped pattern at all.

``$env:USERPROFILE`` and ``$HOME`` never match: they carry no drive letter and no
``/home/`` prefix. That is deliberate -- the scrub preferred a runnable shell
variable wherever the line was an executable command, and a placeholder only where
the line is prose.

Enumeration is ``git ls-files``, deliberately NOT a filesystem walk: a walk reaches
``.venv`` and ``logs/``, where vendored packages and captured run output legitimately
contain absolute home paths that were never in git.

This file is part of the tree it walks and is NOT path-exempted -- an exemption goes
stale on rename -- so the negative fixtures build their account token at RUNTIME and
no literal home segment appears in this source.

DECLARED GAPS: percent-encoded, double-escaped and UNC (``\\\\wsl$``) forms are not
detected; none is present today. It cannot see git history, and a clean HEAD says
nothing about pushed commits.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]

#: The one sanctioned placeholder home segment.
ALLOWED_HOME_TOKENS = frozenset({"x"})

#: Windows home paths: any drive letter, either separator, any case of "users".
WIN_RE = re.compile(r"[A-Za-z]:[\\/]+(?i:users)[\\/]+(?!x(?:[\\/]|$))([A-Za-z0-9._$-]+)")

#: POSIX and WSL home paths, including the /mnt/c and MSYS /c mount forms.
POSIX_RE = re.compile(
    r"(?:/home/|/mnt/[a-z]/(?i:users)/|(?<!/mnt)/[a-z]/(?i:users)/)"
    r"(?!x(?:/|$))([A-Za-z0-9._-]+)"
)

#: Claude project-dir slugs: the drive-dash-dash-Users-dash form.
SLUG_RE = re.compile(r"[A-Za-z]--(?i:users)-(?!x-)([A-Za-z0-9._$]+)")

_ALL = (WIN_RE, POSIX_RE, SLUG_RE)


def _tracked_files() -> list[str]:
    git = shutil.which("git")
    if git is None:  # pragma: no cover - git absent
        pytest.skip("git is unavailable; cannot enumerate the tracked tree")
    proc = subprocess.run(
        [git, "ls-files", "-z"],
        cwd=PROJECT_ROOT,
        capture_output=True,
        check=False,
    )
    if proc.returncode != 0:  # pragma: no cover - not a work tree
        pytest.skip("git ls-files failed; cannot enumerate the tracked tree")
    return [p for p in proc.stdout.decode("utf-8").split("\0") if p]


def _violations_in(text: str, rel: str) -> list[str]:
    found: list[str] = []
    for lineno, line in enumerate(text.splitlines(), start=1):
        for rx in _ALL:
            for match in rx.finditer(line):
                if match.group(1).lower() not in ALLOWED_HOME_TOKENS:
                    found.append(f"{rel}:{lineno}: {match.group(0)!r}")
    return found


def test_no_operator_home_identity_in_tracked_tree() -> None:
    """No tracked file names a real account in any home path or project slug."""
    violations: list[str] = []
    for rel in _tracked_files():
        try:
            raw = (PROJECT_ROOT / rel).read_bytes()
        except OSError:
            continue
        if b"\0" in raw:  # binary
            continue
        violations.extend(_violations_in(raw.decode("utf-8", errors="ignore"), rel))
    assert not violations, (
        "operator home identity in the tracked tree. In an executable command use a "
        "shell variable ($env:USERPROFILE on PowerShell, $HOME on bash); in prose use "
        "the placeholder 'x':\n" + "\n".join(violations)
    )


def test_guard_detects_a_planted_violation() -> None:
    """Red-on-garbage anchor: the patterns must actually fire.

    Without this, a typo that made the regexes match nothing would leave
    ``test_no_operator_home_identity_in_tracked_tree`` permanently, silently green.
    The account token is assembled at runtime so this file does not violate its own
    guard, which does not exempt itself.
    """
    who = "some" + "one"
    planted = [
        rf"C:\Users\{who}\dev\Alpha4Gate",
        f"c:/Users/{who}/dev/Alpha4Gate",
        f"C:/USERS/{who}",
        f"/home/{who}/StarCraftII",
        f"/mnt/c/Users/{who}/dev/Alpha4Gate",
        f"/c/Users/{who}/dev/Alpha4Gate",
        f"c--Users-{who}-dev-Alpha4Gate",
    ]
    for line in planted:
        assert _violations_in(line, "planted"), line

    allowed = [
        r"$env:USERPROFILE\dev\Alpha4Gate",
        '"$HOME/StarCraftII"',
        "/home/x/StarCraftII",
        "/mnt/c/Users/x/dev/Alpha4Gate",
        "c--Users-x-dev-Alpha4Gate",
        r"C:\Users\x\dev",
        "https://github.com/aberson/Alpha4Gate",
        "documentation/wiki/operator-commands.md",
    ]
    for line in allowed:
        assert not _violations_in(line, "allowed"), line
