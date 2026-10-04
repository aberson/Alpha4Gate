"""Tests for the lineage-registry CLI (``scripts/lineage.py``, Phase EH Step EH.1).

Drives ``main(argv)`` end-to-end against a tmp registry file (via ``--path``)
so no real ``data/`` is touched. The ``list_versions`` seam in
``orchestrator.lineages`` is monkeypatched so ``add`` validation does not
depend on the live version tree.

Mirrors ``tests/test_baseline_cli.py``: load the script module via importlib,
then exercise ``main`` with ``capsys`` and assert exit codes + stdout/stderr.

The final test closes the producer -> consumer loop: it drives the CLI at its
DEFAULT path (``<repo_root>/data/lineages.json``, with ``_repo_root``
redirected at ``tmp_path`` per ``tests/test_evolve_cli.py``) and then asserts
that ``scripts/evolve.py``'s ``_load_lineage_registry_if_engaged`` — the real
consumer — sees the registry appear and disappear. Per
``.claude/rules/code-quality.md``, the bug would live in that relationship,
not in either endpoint.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType

import pytest

from orchestrator import lineages as lineages_mod
from orchestrator.lineages import (
    DEFAULT_LINEAGE_ID,
    Lineage,
    load_lineages,
    write_lineages,
)

_REPO_ROOT = Path(__file__).resolve().parent.parent


def _exec_module(name: str, script: Path) -> ModuleType:
    """Load *script* as module *name*, registering it before ``exec_module``.

    Pre-registration is required so Python 3.14's ``@dataclass`` can resolve
    ``cls.__module__`` during exec (see ``tests/test_evolve_cli.py``). The
    cache entry is dropped again if exec fails, so a genuine import error is
    reported once instead of being masked by a cached half-built module for
    every later test in the file.
    """
    spec = importlib.util.spec_from_file_location(name, str(script))
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    try:
        spec.loader.exec_module(mod)
    except BaseException:
        sys.modules.pop(name, None)
        raise
    return mod


def _load_cli_module() -> ModuleType:
    if "lineage_cli" in sys.modules:
        return sys.modules["lineage_cli"]
    return _exec_module("lineage_cli", _REPO_ROOT / "scripts" / "lineage.py")


def _load_evolve_cli_module() -> ModuleType:
    """Import ``scripts/evolve.py`` as module ``evolve_cli``.

    Same name and job as ``tests/test_evolve.py:_load_evolve_cli_module`` and
    ``tests/test_evolve_cli.py:_load_cli_module``.
    """
    if "evolve_cli" in sys.modules:
        return sys.modules["evolve_cli"]
    return _exec_module("evolve_cli", _REPO_ROOT / "scripts" / "evolve.py")


@pytest.fixture
def cli() -> ModuleType:
    return _load_cli_module()


@pytest.fixture(autouse=True)
def _known_versions(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pin the version-validation seam so 'add' does not hit the live repo."""
    monkeypatch.setattr(
        lineages_mod, "list_versions", lambda: ["v0", "v9", "v10"]
    )


def test_add_roundtrips_through_load_lineages(
    cli: ModuleType, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Done-when (1): ``add`` writes a file ``load_lineages`` parses back.

    Two phases.

    *Create* asserts full dataclass equality against
    ``{"alt": Lineage(lineage_id="alt", head_version="v10")}`` — pinning only
    ``created_at`` from the loaded record, since it is a wall-clock default.
    That equality is what proves the unspecified fields landed at their
    defaults (``pool_path=""``, ``parent_chain=[]``, ``status="active"``).

    *Update* is the other half of add-or-update, and the half that carries a
    data-loss risk: this CLI expresses 2 of ``Lineage``'s 6 fields, so a
    re-``add`` that reconstructed the record would silently zero
    ``pool_path`` / ``parent_chain`` and restamp ``created_at``.
    ``register_lineage`` merges via ``dataclasses.replace`` instead, which is
    the primitive EH.2's write-back is specified to use and whose Done-when
    clause (2) asserts ``created_at`` survival as proof of exactly this.
    """
    path = tmp_path / "lineages.json"
    rc = cli.main(["--path", str(path), "add", "alt", "v10"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "alt" in out
    assert "v10" in out

    loaded = load_lineages(path)
    assert set(loaded) == {"alt"}
    assert loaded["alt"].created_at  # stamped, non-empty
    expected = Lineage(
        lineage_id="alt",
        head_version="v10",
        created_at=loaded["alt"].created_at,
    )
    assert loaded == {"alt": expected}

    # On-disk shape is a JSON object keyed by lineage_id.
    raw = json.loads(path.read_text(encoding="utf-8"))
    assert raw["alt"]["head_version"] == "v10"
    assert raw["alt"]["status"] == "active"

    # Update half: seed the three fields the CLI cannot express, re-add
    # validly, and assert only head_version moved.
    seeded = Lineage(
        lineage_id="alt",
        head_version="v10",
        pool_path="data/pool-alt.json",
        parent_chain=["v0", "v9"],
        created_at="2026-09-01T12:00:00+00:00",
        status="exhausted",
    )
    write_lineages(path, {"alt": seeded})

    assert cli.main(["--path", str(path), "add", "alt", "v9"]) == 0
    capsys.readouterr()  # drain the add output
    reloaded = load_lineages(path)
    assert set(reloaded) == {"alt"}  # updated in place, not appended
    assert reloaded["alt"].head_version == "v9"
    assert reloaded["alt"].pool_path == "data/pool-alt.json"
    assert reloaded["alt"].parent_chain == ["v0", "v9"]
    assert reloaded["alt"].created_at == "2026-09-01T12:00:00+00:00"
    assert reloaded["alt"].status == "exhausted"


def test_add_unknown_version_rc1_writes_nothing(
    cli: ModuleType, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Done-when (2): unregistered head exits 1 and creates no file."""
    path = tmp_path / "lineages.json"
    rc = cli.main(["--path", str(path), "add", "ghost", "v99"])
    assert rc == 1
    err = capsys.readouterr().err
    assert "not a registered version" in err
    assert not path.exists()


@pytest.mark.parametrize(
    "bad_id",
    ["", " ", "line—2", "has\ttab", "two\nrows", "main\n", "Main"],
    ids=[
        "empty",
        "space",
        "em-dash",
        "tab",
        "interior-newline",
        "trailing-newline",
        "uppercase",
    ],
)
def test_add_rejects_non_slug_lineage_id(
    cli: ModuleType,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    bad_id: str,
) -> None:
    """A non-slug ``lineage_id`` exits 1 before any write.

    ``Lineage`` documents ``lineage_id`` as a kebab/slug identifier
    (``lineages.py``, Fields block); ``register_lineage`` enforces it. Three
    concrete failures this closes: a tab or a newline in a key corrupts
    ``list``'s tab-separated rows, and a non-ASCII character in a key makes
    every later ``print`` of it raise ``UnicodeEncodeError`` under a
    redirected (``cp1252``) Windows stdout — which, for ``add``, would land
    *after* the registry write and report failure for a change that engaged
    multi-lineage scheduling for good.

    The two newline cases are not redundant. An *interior* newline is
    rejected by any anchored pattern; a *trailing* one is not, because
    Python's ``$`` matches immediately before a final newline -- so
    ``re.match`` admitted a valid slug plus a trailing newline. That key
    persists, keeps the registry non-empty, and can never be named again by
    any shell-typeable ``remove`` argument, latching multi-lineage
    scheduling on with no operator undo. ``register_lineage``'s
    ``fullmatch`` is what closes it, and this case is what keeps it closed.
    """
    path = tmp_path / "lineages.json"
    rc = cli.main(["--path", str(path), "add", bad_id, "v10"])
    assert rc == 1
    err = capsys.readouterr().err
    assert err.startswith("error: register_lineage:")
    assert "Traceback" not in err
    assert not path.exists()


def test_add_unknown_version_leaves_existing_registry_untouched(
    cli: ModuleType, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Done-when (2), pre-existing-file case: a failed add must not truncate.

    The validation happens before ``load_lineages``/``write_lineages``, so a
    typo on a second ``add`` cannot damage the registry an operator already
    built. Asserted on raw bytes.
    """
    path = tmp_path / "lineages.json"
    assert cli.main(["--path", str(path), "add", "alt", "v10"]) == 0
    capsys.readouterr()  # drain the add output
    before = path.read_bytes()

    rc = cli.main(["--path", str(path), "add", "alt", "v99"])
    assert rc == 1
    assert "not a registered version" in capsys.readouterr().err
    assert path.read_bytes() == before


def test_malformed_registry_is_a_clean_error_not_a_traceback(
    cli: ModuleType, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Every subcommand degrades to ``error: ...`` + rc 1 on a bad registry.

    ``operator-gate-runbook.md`` documents hand-authoring the registry as the
    pre-CLI workaround, so the registry most likely to be malformed is
    exactly the one this CLI exists to retire — and ``remove`` is the
    documented teardown hatch, so it has to work on a corrupt file rather
    than tracebacking. ``load_lineages`` raises three distinct classes
    (``JSONDecodeError`` for bad JSON, ``ValueError`` for a non-object
    payload/entry, ``KeyError`` for a missing required field); all three are
    covered here because a partial catch would leave the hatch unusable.
    """
    path = tmp_path / "lineages.json"
    payloads = {
        "bad-json": "{not json",
        "not-an-object": "[]",
        "entry-not-an-object": '{"alt": "v10"}',
        "missing-head-version": '{"alt": {"status": "active"}}',
    }
    argvs = [
        ["add", "alt", "v10"],
        ["list"],
        ["remove", "alt"],
    ]
    for label, payload in payloads.items():
        path.write_text(payload, encoding="utf-8")
        for argv in argvs:
            rc = cli.main(["--path", str(path), *argv])
            captured = capsys.readouterr()
            where = f"{label} / {argv[0]}"
            assert rc == 1, where
            assert captured.err.startswith("error: "), where
            assert "Traceback" not in captured.err, where
            assert captured.out == "", where
            # The unreadable registry is left exactly as the operator left
            # it, so it can still be inspected or moved aside by hand.
            assert path.read_text(encoding="utf-8") == payload, where


def test_list_missing_file_rc0_empty_output(
    cli: ModuleType, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Done-when (3): ``list`` on a missing file exits 0 with empty output.

    stdout is empty so a caller can parse it unconditionally; the
    operator-facing note goes to stderr. The absent registry is also not
    created as a side effect of reading it.
    """
    path = tmp_path / "lineages.json"
    rc = cli.main(["--path", str(path), "list"])
    assert rc == 0
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "no lineages registered" in captured.err
    assert not path.exists()


def test_list_shows_registered_lineages(
    cli: ModuleType, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """``list`` prints one tab-separated row per lineage, in on-disk order.

    The row order is derived from ``load_lineages`` rather than hardcoded:
    the claim under test is that ``list`` renders exactly what the consumer
    loads, in that order, not what any particular JSON-serializer flag in
    another module happens to produce.
    """
    path = tmp_path / "lineages.json"
    assert cli.main(["--path", str(path), "add", "main", "v0"]) == 0
    assert cli.main(["--path", str(path), "add", "line-2", "v9"]) == 0
    capsys.readouterr()  # drain the add output

    rc = cli.main(["--path", str(path), "list"])
    assert rc == 0
    rows = capsys.readouterr().out.strip().splitlines()
    assert len(rows) == 2
    assert [r.split("\t")[0] for r in rows] == list(load_lineages(path))
    assert {tuple(r.split("\t")[:3]) for r in rows} == {
        ("main", "v0", "active"),
        ("line-2", "v9", "active"),
    }


def test_remove_is_idempotent_and_last_removal_loads_empty(
    cli: ModuleType, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Done-when (4): ``remove`` is idempotent; last removal loads empty.

    Two claims:

    1. ``remove`` on an absent id exits 0 and changes nothing — it is the
       documented teardown escape hatch, so a repeat call is a success.
    2. Removing the LAST lineage leaves a registry that ``load_lineages``
       returns **empty** for. That empty-return, not merely "the file
       changed", is what makes the scheduler disengage.
    """
    path = tmp_path / "lineages.json"
    assert cli.main(["--path", str(path), "add", "alt", "v10"]) == 0
    capsys.readouterr()
    assert set(load_lineages(path)) == {"alt"}

    # First remove: the last remaining lineage.
    assert cli.main(["--path", str(path), "remove", "alt"]) == 0
    assert "removed alt" in capsys.readouterr().out
    assert load_lineages(path) == {}

    # Second remove: idempotent — rc 0, state unchanged.
    before = path.read_bytes()
    assert cli.main(["--path", str(path), "remove", "alt"]) == 0
    assert "no lineage named" in capsys.readouterr().err
    assert path.read_bytes() == before
    assert load_lineages(path) == {}


def test_cli_engages_and_disengages_the_evolve_consumer(
    cli: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Producer -> consumer round trip through the real evolve entry point.

    Done-when (4)'s consumer half: after the last ``remove``,
    ``_load_lineage_registry_if_engaged(1)`` must take its ``return {}``
    branch — the documented ``--lineages`` off-switch. Driven at the CLI's
    DEFAULT path, so this also pins that the script writes the registry the
    scheduler actually reads (``data/lineages.json``, **plural**).

    The first ``add`` at the default path is also where the one-way-flip
    note fires: creating that file engages multi-lineage scheduling for
    every later bare invocation, launcher included, so the warning is
    asserted here rather than in a test of its own -- on **stderr**, with
    stdout pinned at the single parseable ``registered ...`` line.

    The first id is ``DEFAULT_LINEAGE_ID`` itself, referenced through the
    constant rather than the literal ``"main"``, so a rename of the default
    lineage cannot silently unpin that the slug guard accepts it.
    """
    evolve = _load_evolve_cli_module()
    monkeypatch.setattr(lineages_mod, "_repo_root", lambda: tmp_path)
    default_path = tmp_path / "data" / "lineages.json"

    # Baseline: nothing on disk, --lineages 1 -> single-lineage path.
    assert evolve._load_lineage_registry_if_engaged(1) == {}

    # Producer: add two lineages at the default path (no --path override).
    assert cli.main(["add", DEFAULT_LINEAGE_ID, "v0"]) == 0
    first = capsys.readouterr()
    assert "engages multi-lineage scheduling" in first.err
    # The advisory is on stderr, so ``add``'s stdout is one parseable line
    # whether the registry was dormant or already engaged.
    assert len(first.out.strip().splitlines()) == 1
    assert cli.main(["add", "line-2", "v9"]) == 0
    # The note is for the dormant -> engaged transition only.
    second = capsys.readouterr()
    assert "engages multi-lineage scheduling" not in second.err
    assert len(second.out.strip().splitlines()) == 1
    assert default_path.is_file()

    # Consumer now sees the scheduler engaged even at --lineages 1.
    engaged = evolve._load_lineage_registry_if_engaged(1)
    assert set(engaged) == {DEFAULT_LINEAGE_ID, "line-2"}
    assert engaged[DEFAULT_LINEAGE_ID].head_version == "v0"
    assert engaged["line-2"].head_version == "v9"

    # Teardown: removing every lineage disengages it again.
    assert cli.main(["remove", DEFAULT_LINEAGE_ID]) == 0
    assert cli.main(["remove", "line-2"]) == 0
    assert load_lineages(default_path) == {}
    assert evolve._load_lineage_registry_if_engaged(1) == {}
