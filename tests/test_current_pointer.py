"""Tests for the ``bots/current/`` thin pointer package (Phase 1, Step 1.6).

The aliasing is a one-shot ``sys.modules`` replacement + meta-path finder
install, which is sticky for the lifetime of the process. To keep the tests
independent of collection order and of any other test that may have already
imported ``bots.current`` or ``bots.v0``, all behavioral assertions run in a
**fresh subprocess** — the lifecycle questions we're answering (does the alias
install correctly, does the error path surface a clear ImportError, does
``python -m bots.current`` delegate to ``python -m bots.v0``) are ones the
real interpreter asks once at startup, so a subprocess is the honest fixture.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest


def _run_python(code: str) -> subprocess.CompletedProcess[str]:
    """Run an inline ``-c`` snippet and return the completed process.

    ``check=False`` so the caller can assert on both happy and error paths.
    """
    return subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        check=False,
    )


def test_import_aliases_to_current_version() -> None:
    """``import bots.current`` resolves to ``bots.<current>`` (whatever current.txt says).

    Reads the committed pointer dynamically rather than hardcoding ``v0``, so
    the invariant under test is "the alias tracks the pointer" — which is what
    the meta-path finder actually guarantees and the evolve pipeline relies on.
    """
    pointer = Path(__file__).resolve().parent.parent / "bots" / "current" / "current.txt"
    current = pointer.read_text(encoding="utf-8").strip()
    result = _run_python(
        "import bots.current; print(bots.current.__name__)"
    )
    assert result.returncode == 0, f"stderr: {result.stderr}"
    assert result.stdout.strip() == f"bots.{current}"


def test_submodule_resolves_to_same_class_via_current() -> None:
    """``bots.current.X`` and ``bots.<current>.X`` yield the SAME class object.

    Identity (not just equality) is what matters — any downstream code that
    keys on class identity (isinstance checks, registries) relies on the meta
    path finder collapsing the two import paths to one module. The concrete
    target version is read from the committed pointer at test entry so this
    stays correct after evolve promotions.
    """
    pointer = Path(__file__).resolve().parent.parent / "bots" / "current" / "current.txt"
    current = pointer.read_text(encoding="utf-8").strip()
    result = _run_python(
        "from bots.current.learning.database import TrainingDB as A; "
        f"from bots.{current}.learning.database import TrainingDB as B; "
        "print(A is B); print(A.__module__)"
    )
    assert result.returncode == 0, f"stderr: {result.stderr}"
    lines = result.stdout.strip().splitlines()
    assert lines[0] == "True"
    assert lines[1] == f"bots.{current}.learning.database"


def test_help_delegates_to_bots_v0() -> None:
    """``python -m bots.current --help`` exits 0 and prints the v0 argparse."""
    result = subprocess.run(
        [sys.executable, "-m", "bots.current", "--help"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, f"stderr: {result.stderr}"
    # The usage line names the delegated module (``prog="python -m bots.v0"``
    # is hard-coded in bots.v0's parser), which is the simplest "it really
    # delegated" signal.
    assert "python -m bots.v0" in result.stdout
    # Sanity-check the role choices the ladder/orchestrator depend on.
    assert "--role" in result.stdout
    for choice in ("p1", "p2", "solo"):
        assert choice in result.stdout


def test_current_txt_points_to_registered_version() -> None:
    """The committed ``bots/current/current.txt`` names an existing version dir.

    Directly reads the file — no import-side effects. Instead of hardcoding
    ``v0`` (which goes stale every time evolve promotes a new version), this
    asserts the weaker-but-true invariant: the pointer names a directory that
    exists on disk AND is listed by the registry. That's the contract callers
    actually depend on.
    """
    repo_root = Path(__file__).resolve().parent.parent
    pointer = repo_root / "bots" / "current" / "current.txt"
    assert pointer.is_file()
    current = pointer.read_text(encoding="utf-8").strip()
    assert current, "current.txt is empty"
    assert (repo_root / "bots" / current).is_dir(), (
        f"current.txt points at {current!r} but bots/{current}/ does not exist"
    )
    # Lazy import to avoid pulling in the orchestrator package at module load
    # (keeps this file's other subprocess tests free of import-order concerns).
    from orchestrator import registry

    assert current in registry.list_versions()


def test_invalid_version_in_current_txt_raises(tmp_path: Path) -> None:
    """Pointing ``current.txt`` at a non-existent version raises ImportError.

    We can't safely mutate the committed ``bots/current/current.txt`` from a
    test, so we copy the whole project-relevant tree (bots/ + src/) into a
    tmpdir, overwrite the pointer to a bogus value, and import from there in
    a subprocess with ``PYTHONPATH`` redirected. Asserting on the exact error
    class (``ImportError``) and a substring of the message guards against
    future regressions that silently downgrade to ``ModuleNotFoundError`` or
    swallow the bad-pointer value.
    """
    repo_root = Path(__file__).resolve().parent.parent
    sandbox = tmp_path / "sandbox"
    sandbox.mkdir()
    # Copy just what we need: the bots/ tree (so bots.v0 still resolves when
    # referenced by name) and the src/ tree (for bots.v0/orchestrator
    # packages that ``bots.v0.__init__`` might transitively import).
    shutil.copytree(repo_root / "bots", sandbox / "bots")
    shutil.copytree(repo_root / "src", sandbox / "src")
    (sandbox / "bots" / "current" / "current.txt").write_text(
        "v-does-not-exist\n", encoding="utf-8"
    )

    # Run in the sandbox with PYTHONPATH set so the copied packages shadow the
    # real ones. Capture both the exit code and stderr so the assertion can be
    # specific about the failure surface.
    env = {
        **__import__("os").environ,
        "PYTHONPATH": f"{sandbox}{__import__('os').pathsep}{sandbox / 'src'}",
    }
    result = subprocess.run(
        [sys.executable, "-c", "import bots.current"],
        capture_output=True,
        text=True,
        check=False,
        cwd=str(sandbox),
        env=env,
    )
    assert result.returncode != 0
    # The stack trace should name ImportError (not FileNotFoundError etc.),
    # and the message should identify the offending version name verbatim so
    # an operator can grep their logs for it.
    assert "ImportError" in result.stderr
    assert "v-does-not-exist" in result.stderr


@pytest.mark.parametrize(
    "bad_content,expected_substring",
    [
        ("", "is empty"),
        ("   \n\t  ", "is empty"),
    ],
)
def test_empty_current_txt_raises(
    tmp_path: Path, bad_content: str, expected_substring: str
) -> None:
    """Blank / whitespace-only ``current.txt`` surfaces as ``ImportError``.

    Uses the same sandbox trick as the invalid-version test. We keep this
    split out (rather than lumping it into the invalid-version test) because
    the two failure surfaces have different messages, and an operator
    debugging a broken pointer needs that distinction.
    """
    repo_root = Path(__file__).resolve().parent.parent
    sandbox = tmp_path / "sandbox"
    sandbox.mkdir()
    shutil.copytree(repo_root / "bots", sandbox / "bots")
    shutil.copytree(repo_root / "src", sandbox / "src")
    (sandbox / "bots" / "current" / "current.txt").write_text(
        bad_content, encoding="utf-8"
    )

    env = {
        **__import__("os").environ,
        "PYTHONPATH": f"{sandbox}{__import__('os').pathsep}{sandbox / 'src'}",
    }
    result = subprocess.run(
        [sys.executable, "-c", "import bots.current"],
        capture_output=True,
        text=True,
        check=False,
        cwd=str(sandbox),
        env=env,
    )
    assert result.returncode != 0
    assert "ImportError" in result.stderr
    assert expected_substring in result.stderr
