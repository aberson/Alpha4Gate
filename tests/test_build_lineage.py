"""Tests for ``scripts/build_lineage.py`` (Models tab Step 2).

Coverage:

* End-to-end DAG build against the real ``bots/v*/`` checkpoint (11
  versions today). Asserts every promoted version produces both a
  node and an edge to its parent.
* Single-version state → 1 node, 0 edges.
* Two-version state where v1.parent=v0 → 1 edge.
* Atomic-replace race: ``os.replace`` fails with ``PermissionError``
  twice then succeeds; the build still completes (matches the
  retry-with-backoff recipe in
  ``feedback_evolve_windows_atomic_replace_race.md``).
* Idempotency: two runs back-to-back produce byte-identical output
  (stable JSON ordering inside ``_atomic_write_json``).
"""

from __future__ import annotations

import importlib.util
import json
import os
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

# ``scripts/build_lineage.py`` isn't part of an installed package, so
# import it via spec-loader. This matches what ``tests/test_evolve_cli.py``
# does for its sibling script.
_SPEC = importlib.util.spec_from_file_location(
    "build_lineage",
    Path(__file__).resolve().parent.parent / "scripts" / "build_lineage.py",
)
assert _SPEC is not None and _SPEC.loader is not None
build_lineage_module = importlib.util.module_from_spec(_SPEC)
sys.modules["build_lineage"] = build_lineage_module
_SPEC.loader.exec_module(build_lineage_module)


def _make_manifest(
    repo: Path,
    version: str,
    *,
    parent: str | None,
    git_sha: str = "deadbeef",
    timestamp: str | None = None,
) -> None:
    """Stage ``bots/<version>/manifest.json`` for tests."""
    vdir = repo / "bots" / version
    vdir.mkdir(parents=True, exist_ok=True)
    payload = {
        "version": version,
        "parent": parent,
        "git_sha": git_sha,
        "timestamp": timestamp or "2026-01-01T00:00:00+00:00",
        "best": version,
        "elo": 0.0,
        "previous_best": None,
        "fingerprint": {},
        "extra": {},
    }
    (vdir / "manifest.json").write_text(json.dumps(payload), encoding="utf-8")


@pytest.fixture()
def staged_repo(tmp_path: Path) -> Iterator[Path]:
    """A fresh repo root with empty ``bots/`` and ``data/`` dirs."""
    (tmp_path / "bots").mkdir()
    (tmp_path / "data").mkdir()
    yield tmp_path


class TestBuildLineageFunction:
    """``build_lineage(repo_root)`` — pure function tests."""

    def test_empty_repo_returns_empty(self, staged_repo: Path) -> None:
        result = build_lineage_module.build_lineage(staged_repo)
        assert result == {"nodes": [], "edges": []}

    def test_single_version_one_node_no_edges(
        self, staged_repo: Path
    ) -> None:
        _make_manifest(staged_repo, "v0", parent=None)
        result = build_lineage_module.build_lineage(staged_repo)
        assert len(result["nodes"]) == 1
        assert result["nodes"][0]["id"] == "v0"
        assert result["nodes"][0]["parent"] is None
        assert result["edges"] == []

    def test_two_versions_one_edge(self, staged_repo: Path) -> None:
        _make_manifest(staged_repo, "v0", parent=None)
        _make_manifest(
            staged_repo,
            "v1",
            parent="v0",
            timestamp="2026-02-01T00:00:00+00:00",
        )
        result = build_lineage_module.build_lineage(staged_repo)
        assert len(result["nodes"]) == 2
        assert len(result["edges"]) == 1
        edge = result["edges"][0]
        assert edge["from"] == "v0"
        assert edge["to"] == "v1"
        assert edge["outcome"] == "promoted"
        # No advised/evolve log → manual harness for v1.
        assert edge["harness"] == "manual"
        assert edge["improvement_title"] == "manual"

    def test_advised_harness_resolves_title_from_files_changed(
        self, staged_repo: Path
    ) -> None:
        _make_manifest(staged_repo, "v0", parent=None)
        _make_manifest(staged_repo, "v1", parent="v0")
        improvement_log = {
            "improvements": [
                {
                    "id": "advised-iter1",
                    "title": "Stronger mineral floats",
                    "files_changed": ["bots/v1/some_file.py"],
                }
            ]
        }
        (staged_repo / "data" / "improvement_log.json").write_text(
            json.dumps(improvement_log), encoding="utf-8"
        )
        result = build_lineage_module.build_lineage(staged_repo)
        edge = next(e for e in result["edges"] if e["to"] == "v1")
        assert edge["harness"] == "advised"
        assert edge["improvement_title"] == "Stronger mineral floats"

    def test_evolve_harness_resolves_title_from_stack_apply_row(
        self, staged_repo: Path
    ) -> None:
        _make_manifest(staged_repo, "v0", parent=None)
        _make_manifest(staged_repo, "v1", parent="v0")
        rows = [
            {
                "phase": "stack_apply",
                "new_version": "v1",
                "outcome": "stack-apply-pass",
                "stacked_titles": ["Splash readiness", "Auto Battery"],
            }
        ]
        (staged_repo / "data" / "evolve_results.jsonl").write_text(
            "\n".join(json.dumps(r) for r in rows) + "\n",
            encoding="utf-8",
        )
        result = build_lineage_module.build_lineage(staged_repo)
        edge = next(e for e in result["edges"] if e["to"] == "v1")
        assert edge["harness"] == "evolve"
        assert edge["improvement_title"] == "Splash readiness + Auto Battery"

    def test_manifest_extra_attribution_preferred_over_jsonl(
        self, staged_repo: Path
    ) -> None:
        """#269: when ``manifest.extra.harness_origin`` /
        ``manifest.extra.improvement_title`` are populated, lineage uses
        them — even if ``data/evolve_results.jsonl`` is empty (the
        fresh-run-truncation case).

        This is the primary regression test for the attribution-loss
        defect: stamping the manifest at promotion time means lineage
        survives any subsequent JSONL wipe.
        """
        _make_manifest(staged_repo, "v0", parent=None)
        # v1 manifest carries the attribution in extra (what
        # ``_rewrite_manifest_parent`` writes at promote time).
        vdir = staged_repo / "bots" / "v1"
        vdir.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": "v1",
            "parent": "v0",
            "git_sha": "deadbeef",
            "timestamp": "2026-02-01T00:00:00+00:00",
            "best": "v1",
            "elo": 0.0,
            "previous_best": None,
            "fingerprint": {},
            "extra": {
                "harness_origin": "evolve",
                "improvement_title": "Manifest-stamped title (post-#269)",
            },
        }
        (vdir / "manifest.json").write_text(
            json.dumps(payload), encoding="utf-8"
        )
        # No data/evolve_results.jsonl on disk — simulates the
        # fresh-run-truncation state where the JSONL has been wiped.
        result = build_lineage_module.build_lineage(staged_repo)
        edge = next(e for e in result["edges"] if e["to"] == "v1")
        assert edge["harness"] == "evolve"
        assert edge["improvement_title"] == "Manifest-stamped title (post-#269)"

    def test_manifest_extra_falls_through_when_harness_unrecognized(
        self, staged_repo: Path
    ) -> None:
        """Defense in depth: a malformed ``extra.harness_origin`` (not
        one of the four recognized values) falls through to the
        existing JSONL-derived attribution path. Keeps legacy manifests
        with stray extras from corrupting lineage output.
        """
        _make_manifest(staged_repo, "v0", parent=None)
        vdir = staged_repo / "bots" / "v1"
        vdir.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": "v1",
            "parent": "v0",
            "git_sha": "deadbeef",
            "timestamp": "2026-02-01T00:00:00+00:00",
            "best": "v1",
            "elo": 0.0,
            "previous_best": None,
            "fingerprint": {},
            "extra": {"harness_origin": "garbage-value"},
        }
        (vdir / "manifest.json").write_text(
            json.dumps(payload), encoding="utf-8"
        )
        result = build_lineage_module.build_lineage(staged_repo)
        edge = next(e for e in result["edges"] if e["to"] == "v1")
        # Falls through to the no-log default → manual.
        assert edge["harness"] == "manual"
        assert edge["improvement_title"] == "manual"

    def test_skip_orphan_when_parent_missing(
        self, staged_repo: Path
    ) -> None:
        # v0 with parent=null + v1 with parent=null (orphan): no edges.
        _make_manifest(staged_repo, "v0", parent=None)
        _make_manifest(staged_repo, "v1", parent=None)
        result = build_lineage_module.build_lineage(staged_repo)
        assert len(result["nodes"]) == 2
        assert result["edges"] == []

    def test_nodes_sorted_by_version_number(self, staged_repo: Path) -> None:
        # Stage v10 BEFORE v2 to confirm we sort by integer N (not by
        # lexical string order, which would put v10 before v2).
        _make_manifest(staged_repo, "v10", parent="v9")
        _make_manifest(staged_repo, "v9", parent="v0")
        _make_manifest(staged_repo, "v2", parent="v0")
        _make_manifest(staged_repo, "v0", parent=None)
        result = build_lineage_module.build_lineage(staged_repo)
        node_ids = [n["id"] for n in result["nodes"]]
        assert node_ids == ["v0", "v2", "v9", "v10"]


class TestBuildLineageRealRepo:
    """End-to-end test against the real ``bots/v*/`` checkpoints."""

    def test_real_repo_lineage_well_formed(self) -> None:
        """Structural invariants on the real repo's lineage.

        Counts grow over time as evolve promotes new versions, so the
        assertion is on shape not magnitude: nodes == bots/v* dirs,
        edges == nodes minus the single root (v0 has parent=null), and
        every node/edge carries its required fields. Locking to a
        specific number was previously broken every time a promotion
        landed.
        """
        repo_root = Path(__file__).resolve().parent.parent
        result = build_lineage_module.build_lineage(repo_root)
        # Count actual on-disk version dirs to compare apples-to-apples.
        bots_dir = repo_root / "bots"
        on_disk_versions = sorted(
            p.name
            for p in bots_dir.iterdir()
            if p.is_dir()
            and p.name != "current"
            and build_lineage_module._VERSION_RE.match(p.name)
        )
        assert len(result["nodes"]) == len(on_disk_versions), (
            f"expected {len(on_disk_versions)} versions in lineage, "
            f"got {len(result['nodes'])}; on-disk: {on_disk_versions}"
        )
        assert len(result["nodes"]) >= 11, (
            "smoke floor: should always have at least the original "
            "11 versions (v0–v10) that shipped with the Models tab plan"
        )
        # Tree shape: edges = nodes - root. Even though `_skip_orphan`
        # tolerates parent=null on non-v0 manifests, the on-disk repo
        # has exactly one (v0).
        assert len(result["edges"]) == len(result["nodes"]) - 1
        # Every node has the required fields.
        for node in result["nodes"]:
            assert set(node.keys()) >= {
                "id",
                "version",
                "race",
                "harness_origin",
                "parent",
            }
            assert node["id"] == node["version"]
            assert node["race"] == "protoss"
        # Every edge has the required fields.
        for edge in result["edges"]:
            assert set(edge.keys()) >= {
                "from",
                "to",
                "harness",
                "improvement_title",
                "ts",
                "outcome",
            }
            assert edge["outcome"] == "promoted"


class TestAtomicWrite:
    """Atomic-replace race + idempotency tests."""

    def test_atomic_replace_retries_on_permission_error(
        self,
        staged_repo: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Two ``PermissionError``s then success — the build still completes.

        Mirrors ``tests/test_evolve_cli.py``'s race test for the
        ``_restore_pointer`` retry-with-backoff helper. The build
        helper retries up to 5 times so two transient failures in a
        row should not propagate.
        """
        out_path = staged_repo / "data" / "lineage.json"
        # Stage a single version so build_lineage has something to write.
        _make_manifest(staged_repo, "v0", parent=None)

        real_replace = os.replace
        call_count = {"n": 0}

        def fake_replace(src: Any, dst: Any) -> None:
            call_count["n"] += 1
            if call_count["n"] <= 2:
                raise PermissionError("simulated WinError 5")
            real_replace(src, dst)

        monkeypatch.setattr(build_lineage_module.os, "replace", fake_replace)
        # Drop sleep delays so the test stays quick.
        monkeypatch.setattr(
            build_lineage_module,
            "_ATOMIC_REPLACE_RETRY_DELAYS",
            (0.0, 0.0, 0.0, 0.0, 0.0),
        )

        payload = build_lineage_module.build_lineage(staged_repo)
        build_lineage_module._atomic_write_json(payload, out_path)

        assert call_count["n"] >= 3, (
            f"expected at least 3 os.replace calls, got {call_count['n']}"
        )
        assert out_path.is_file()
        loaded = json.loads(out_path.read_text(encoding="utf-8"))
        assert loaded == payload

    def test_idempotent_byte_identical(
        self, staged_repo: Path
    ) -> None:
        """Two runs produce byte-identical files."""
        _make_manifest(staged_repo, "v0", parent=None)
        _make_manifest(
            staged_repo,
            "v1",
            parent="v0",
            timestamp="2026-02-01T00:00:00+00:00",
        )
        out_path = staged_repo / "data" / "lineage.json"

        payload1 = build_lineage_module.build_lineage(staged_repo)
        build_lineage_module._atomic_write_json(payload1, out_path)
        first_bytes = out_path.read_bytes()

        payload2 = build_lineage_module.build_lineage(staged_repo)
        build_lineage_module._atomic_write_json(payload2, out_path)
        second_bytes = out_path.read_bytes()

        assert first_bytes == second_bytes, (
            "build_lineage output is not idempotent — repeated runs on "
            "identical state produced different bytes."
        )


class TestCli:
    """``main(argv)`` smoke tests."""

    def test_main_writes_to_explicit_out(self, staged_repo: Path) -> None:
        _make_manifest(staged_repo, "v0", parent=None)
        out = staged_repo / "lineage_out.json"
        # Override _repo_root() so the CLI walks the staged repo.
        original_repo_root = build_lineage_module._repo_root
        build_lineage_module._repo_root = lambda: staged_repo
        try:
            rc = build_lineage_module.main(["--out", str(out)])
        finally:
            build_lineage_module._repo_root = original_repo_root

        assert rc == 0
        assert out.is_file()
        loaded = json.loads(out.read_text(encoding="utf-8"))
        assert len(loaded["nodes"]) == 1
        assert loaded["edges"] == []
