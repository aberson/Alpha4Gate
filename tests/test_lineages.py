"""Tests for ``orchestrator.lineages`` — parallel-lineage registry + scheduler.

Covers the registry round-trip, the round-robin scheduler's wrap + None /
unknown handling, the implicit-``main`` back-compat fallback, and the atomic
write helper. The ``_repo_root`` / ``current_version`` seams are monkeypatched
at a tmp tree so no test touches the real ``data/`` dir.
"""

from __future__ import annotations

import json
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
