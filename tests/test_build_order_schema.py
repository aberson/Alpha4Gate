"""Tests for Phase D.2 build-order trajectory schema and example files."""

from __future__ import annotations

import json
from pathlib import Path

import jsonschema
import pytest

from orchestrator.registry import resolve_data_path

# Resolve via registry so the test follows the per-version data layout
# (bots/<v>/data/build_orders/) without hard-coding the active version.
# resolve_data_path falls back to per-version when the file doesn't exist
# at the legacy ``data/`` root, which suits our brand-new directory.
_SCHEMA_PATH: Path = resolve_data_path("build_orders/_schema.json")
BUILD_ORDERS_DIR: Path = _SCHEMA_PATH.parent


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _trajectory_files() -> list[Path]:
    """All trajectory JSONs in the build_orders dir, excluding the schema itself."""
    return sorted(
        p for p in BUILD_ORDERS_DIR.glob("*.json") if p.name != "_schema.json"
    )


def test_build_orders_dir_exists() -> None:
    assert BUILD_ORDERS_DIR.is_dir(), f"missing build_orders dir: {BUILD_ORDERS_DIR}"


def test_schema_loads() -> None:
    schema = _load_json(_SCHEMA_PATH)
    # Sanity: the validator must accept the schema itself as a valid schema.
    jsonschema.Draft202012Validator.check_schema(schema)
    assert schema["title"] == "BuildOrderTrajectory"


def test_at_least_two_example_trajectories() -> None:
    files = _trajectory_files()
    names = {p.name for p in files}
    assert "4-gate-aggression.json" in names
    assert "robo-colossus.json" in names


@pytest.mark.parametrize("trajectory_path", _trajectory_files(), ids=lambda p: p.name)
def test_trajectory_validates_against_schema(trajectory_path: Path) -> None:
    schema = _load_json(_SCHEMA_PATH)
    instance = _load_json(trajectory_path)
    jsonschema.validate(instance=instance, schema=schema)


@pytest.mark.parametrize("trajectory_path", _trajectory_files(), ids=lambda p: p.name)
def test_trajectory_name_matches_filename_stem(trajectory_path: Path) -> None:
    """D.5's registry will key off the filename stem, so name must match."""
    instance = _load_json(trajectory_path)
    assert instance["name"] == trajectory_path.stem, (
        f"{trajectory_path.name}: name field {instance['name']!r} "
        f"does not match filename stem {trajectory_path.stem!r}"
    )


def test_schema_rejects_invalid_action() -> None:
    """Negative-case: an action outside the enum must fail schema validation."""
    schema = _load_json(_SCHEMA_PATH)
    bad_instance = {
        "name": "x",
        "targets": [
            {"action": "smelt", "target": "iron", "time_seconds": 0},
        ],
    }
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(instance=bad_instance, schema=schema)


def test_trajectory_timings_monotonic_nondecreasing() -> None:
    """Examples should be ordered by game time so the D.3 edit-distance is meaningful."""
    for trajectory_path in _trajectory_files():
        instance = _load_json(trajectory_path)
        times = [t["time_seconds"] for t in instance["targets"]]
        assert times == sorted(times), (
            f"{trajectory_path.name} targets not ordered by time_seconds: {times}"
        )
