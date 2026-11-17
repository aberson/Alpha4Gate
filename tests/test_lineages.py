"""Tests for ``orchestrator.lineages`` — parallel-lineage registry + scheduler.

Covers the registry round-trip, the round-robin scheduler's wrap + None /
unknown handling, the implicit-``main`` back-compat fallback, and the atomic
write helper. The ``_repo_root`` / ``current_version`` seams are monkeypatched
at a tmp tree so no test touches the real ``data/`` dir.
"""

from __future__ import annotations

import dataclasses
import json
import logging
import os
from pathlib import Path

import pytest

from orchestrator import lineages
from orchestrator.lineages import (
    DEFAULT_LINEAGE_ID,
    Lineage,
    default_lineages_path,
    load_lineages,
    load_or_default_lineages,
    next_lineage,
    write_lineages,
)


def _make_lineage(
    lineage_id: str,
    head_version: str,
    *,
    parent_chain: list[str] | None = None,
) -> Lineage:
    return Lineage(
        lineage_id=lineage_id,
        head_version=head_version,
        pool_path=f"data/evolve_pool_{lineage_id}.json",
        parent_chain=parent_chain if parent_chain is not None else [],
        created_at="2026-06-19T00:00:00+00:00",
        status="active",
    )


# ---------------------------------------------------------------------------
# Dataclass json helpers
# ---------------------------------------------------------------------------


def test_lineage_json_round_trip() -> None:
    lin = _make_lineage("line-2", "v13", parent_chain=["v0", "v7"])
    restored = Lineage.from_json(lin.to_json())
    assert restored == lin


def test_lineage_from_dict_fills_optional_defaults() -> None:
    # Only the two required fields present; the rest fall back to defaults.
    lin = Lineage.from_dict({"lineage_id": "main", "head_version": "v0"})
    assert lin.lineage_id == "main"
    assert lin.head_version == "v0"
    assert lin.pool_path == ""
    assert lin.parent_chain == []
    assert lin.status == "active"
    assert lin.created_at  # default factory stamped a timestamp


# ---------------------------------------------------------------------------
# Registry round-trip
# ---------------------------------------------------------------------------


def test_registry_round_trip(tmp_path: Path) -> None:
    path = tmp_path / "lineages.json"
    registry = {
        "main": _make_lineage("main", "v13"),
        "line-2": _make_lineage("line-2", "v9", parent_chain=["v0"]),
    }
    write_lineages(path, registry)
    loaded = load_lineages(path)
    assert loaded == registry


def test_load_lineages_missing_file_returns_empty(tmp_path: Path) -> None:
    assert load_lineages(tmp_path / "nope.json") == {}


def test_load_lineages_empty_file_returns_empty(tmp_path: Path) -> None:
    path = tmp_path / "lineages.json"
    path.write_text("   \n", encoding="utf-8")
    assert load_lineages(path) == {}


def test_load_lineages_uses_key_as_authoritative_id(tmp_path: Path) -> None:
    # The registry key is authoritative even if the value omits lineage_id.
    path = tmp_path / "lineages.json"
    path.write_text(
        json.dumps({"main": {"head_version": "v3"}}),
        encoding="utf-8",
    )
    loaded = load_lineages(path)
    assert loaded["main"].lineage_id == "main"
    assert loaded["main"].head_version == "v3"


def test_load_lineages_non_object_top_level_raises(tmp_path: Path) -> None:
    path = tmp_path / "lineages.json"
    path.write_text(json.dumps([1, 2, 3]), encoding="utf-8")
    with pytest.raises(ValueError, match="must be a JSON object"):
        load_lineages(path)


# ---------------------------------------------------------------------------
# Atomic write
# ---------------------------------------------------------------------------


def test_write_lineages_creates_readable_file(tmp_path: Path) -> None:
    path = tmp_path / "nested" / "lineages.json"
    registry = {"main": _make_lineage("main", "v0")}
    write_lineages(path, registry)
    assert path.is_file()
    # Re-readable as JSON keyed by lineage_id.
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert set(payload.keys()) == {"main"}
    assert payload["main"]["head_version"] == "v0"
    # And re-loadable into equal Lineage objects.
    assert load_lineages(path) == registry


# ---------------------------------------------------------------------------
# Round-robin scheduler
# ---------------------------------------------------------------------------


def test_next_lineage_none_returns_first() -> None:
    registry = {
        "main": _make_lineage("main", "v0"),
        "line-2": _make_lineage("line-2", "v1"),
    }
    assert next_lineage(registry, None) == "main"


def test_next_lineage_unknown_returns_first() -> None:
    registry = {
        "main": _make_lineage("main", "v0"),
        "line-2": _make_lineage("line-2", "v1"),
    }
    assert next_lineage(registry, "does-not-exist") == "main"


def test_next_lineage_wraps() -> None:
    registry = {
        "main": _make_lineage("main", "v0"),
        "line-2": _make_lineage("line-2", "v1"),
        "line-3": _make_lineage("line-3", "v2"),
    }
    assert next_lineage(registry, "main") == "line-2"
    assert next_lineage(registry, "line-2") == "line-3"
    # Wrap-around: successor of the last id is the first id.
    assert next_lineage(registry, "line-3") == "main"


def test_next_lineage_single_lineage_returns_itself() -> None:
    registry = {"main": _make_lineage("main", "v0")}
    assert next_lineage(registry, None) == "main"
    assert next_lineage(registry, "main") == "main"


def test_next_lineage_empty_registry_raises() -> None:
    with pytest.raises(ValueError, match="empty"):
        next_lineage({}, None)


def test_next_lineage_skips_extinct_records() -> None:
    """Phase EH.2: a persisted ``status="extinct"`` record is never scheduled.

    The generation-boundary write-back persists culled lineages rather than
    dropping them (plan §6 D-3), so without this filter a restart would
    resurrect an extinct lineage into the round-robin.
    """
    registry = {
        "main": _make_lineage("main", "v0"),
        "line-2": dataclasses.replace(
            _make_lineage("line-2", "v8"), status="extinct"
        ),
        "line-3": _make_lineage("line-3", "v9"),
    }
    assert next_lineage(registry, None) == "main"
    assert next_lineage(registry, "main") == "line-3"
    # Wrap skips the extinct record entirely.
    assert next_lineage(registry, "line-3") == "main"
    # An extinct id handed in as *last_id* is not a member of the active
    # ring, so the scheduler restarts at the first active lineage rather
    # than raising from ``list.index``.
    assert next_lineage(registry, "line-2") == "main"


def test_next_lineage_all_extinct_falls_back_and_warns(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """An all-extinct registry degrades to scheduling, never to a crash.

    Phase EH.2 clause (7). ``next_lineage``'s in-loop call site
    (``scripts/evolve.py``'s generation loop) reaches this helper with
    whatever the registry holds, so an all-extinct on-disk registry must
    fall back to every id and warn rather than abort the next run. (The
    call site gained a status partition upstream in EH.2 iteration 2; this
    fallback remains the defence-in-depth guarantee of the helper itself.)
    """
    registry = {
        "main": dataclasses.replace(
            _make_lineage("main", "v0"), status="extinct"
        ),
        "line-2": dataclasses.replace(
            _make_lineage("line-2", "v8"), status="extinct"
        ),
    }
    with caplog.at_level(logging.WARNING, logger="orchestrator.lineages"):
        first = next_lineage(registry, None)
    assert first == "main"
    warnings = [
        rec.getMessage()
        for rec in caplog.records
        if rec.levelno == logging.WARNING
    ]
    # Pin the ACTUAL text, not a hedged disjunction: a two-arm `or` whose
    # first arm can never match passes on the loose second arm and asserts
    # nothing about the warning contract.
    assert any(
        "none of the 2 registered lineage(s) is active" in msg
        for msg in warnings
    ), warnings
    # The warning must also name each id with its status so the operator
    # can see WHY nothing was schedulable.
    assert any(
        "main='extinct'" in msg and "line-2='extinct'" in msg
        for msg in warnings
    ), warnings
    # The fallback ring still round-robins across every id.
    assert next_lineage(registry, "main") == "line-2"
    assert next_lineage(registry, "line-2") == "main"


def test_next_lineage_preserves_insertion_order() -> None:
    # Insertion order (NOT sorted) drives the round-robin sequence.
    registry = {
        "zeta": _make_lineage("zeta", "v0"),
        "alpha": _make_lineage("alpha", "v1"),
    }
    assert next_lineage(registry, None) == "zeta"
    assert next_lineage(registry, "zeta") == "alpha"
    assert next_lineage(registry, "alpha") == "zeta"


# ---------------------------------------------------------------------------
# Implicit-main back-compat
# ---------------------------------------------------------------------------


def test_load_or_default_implicit_main_when_absent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(lineages, "current_version", lambda: "v42")
    path = tmp_path / "lineages.json"  # does not exist
    registry = load_or_default_lineages(path)
    assert set(registry.keys()) == {DEFAULT_LINEAGE_ID}
    main = registry[DEFAULT_LINEAGE_ID]
    assert main.lineage_id == "main"
    assert main.head_version == "v42"


def test_load_or_default_implicit_main_when_empty(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(lineages, "current_version", lambda: "v7")
    path = tmp_path / "lineages.json"
    path.write_text("", encoding="utf-8")
    registry = load_or_default_lineages(path)
    assert set(registry.keys()) == {DEFAULT_LINEAGE_ID}
    assert registry[DEFAULT_LINEAGE_ID].head_version == "v7"


def test_load_or_default_returns_on_disk_when_present(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # current_version must NOT be consulted when a registry exists.
    def _boom() -> str:
        raise AssertionError("current_version should not be called")

    monkeypatch.setattr(lineages, "current_version", _boom)
    path = tmp_path / "lineages.json"
    on_disk = {
        "main": _make_lineage("main", "v13"),
        "line-2": _make_lineage("line-2", "v9"),
    }
    write_lineages(path, on_disk)
    assert load_or_default_lineages(path) == on_disk


def test_default_lineages_path_under_repo_data(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(lineages, "_repo_root", lambda: tmp_path)
    assert default_lineages_path() == tmp_path / "data" / "lineages.json"


def test_atomic_write_scratch_file_is_per_process(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The ``.tmp`` scratch name carries the writer's pid.

    Two concurrent ``scripts/lineage.py`` invocations used to share one
    ``<path>.tmp``: the second writer overwrote the first's scratch, the
    first's ``replace`` consumed it, and the second's ``replace`` raised an
    unretried ``FileNotFoundError`` — so one process reported success for an
    entry that never landed. The pid in the name is what makes that
    impossible, and this test exists so "restore consistency with
    ``baselines``/``fingerprint``" cannot silently undo it.
    """
    seen: list[str] = []
    real_replace = Path.replace

    def _spy(self: Path, target: str | os.PathLike[str]) -> Path:
        seen.append(self.name)
        return real_replace(self, target)

    monkeypatch.setattr(Path, "replace", _spy)
    path = tmp_path / "lineages.json"
    write_lineages(path, {"main": _make_lineage("main", "v13")})

    assert len(seen) == 1
    # The property under test is collision-freedom, not the exact spelling:
    # any name distinct from the shared ``<path>.tmp`` the sibling copies
    # use keeps two concurrent writers off each other's scratch. Pinning
    # the literal would fail a refactor that preserved the property.
    assert seen[0] != f"{path.name}.tmp"
    assert load_lineages(path)["main"].head_version == "v13"
    # The scratch file is consumed by the replace, not left behind.
    assert list(tmp_path.glob("*.tmp")) == []


def test_atomic_write_cleans_scratch_when_replace_never_succeeds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A ``replace`` that never succeeds leaves no scratch file behind.

    The retry-backoff covers a ``--serve`` handle that clears within ~1.5s;
    one that never clears propagates ``PermissionError``. Without the
    ``finally`` cleanup the scratch survived that path, and per-pid naming
    turned the leak from a single reusable ``<path>.tmp`` into one
    accumulating file per invocation under ``data/``.
    """
    monkeypatch.setattr(lineages.time, "sleep", lambda _delay: None)

    def _always_busy(self: Path, target: str | os.PathLike[str]) -> Path:
        raise PermissionError(13, "in use")

    monkeypatch.setattr(Path, "replace", _always_busy)
    path = tmp_path / "lineages.json"
    with pytest.raises(PermissionError):
        write_lineages(path, {"main": _make_lineage("main", "v13")})

    assert list(tmp_path.glob("*.tmp")) == []
    assert not path.exists()
