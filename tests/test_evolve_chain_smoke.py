"""Producer -> consumer chain smoke for the evolve lineage registry (EH.9).

Every other lineage test seeds ``data/lineages.json`` either by calling
``write_lineages`` directly or by hand-writing JSON, and asserts one hop at a
time. This file wires the real components end to end, in one test:

    scripts/lineage.py main()            (seed, in-process)
      -> data/lineages.json              (one tmp file)
      -> scripts/evolve.py run_loop      (--lineages 3, two generations)
      -> data/lineages.json              (generation-boundary persist hop)
      -> GET /api/evolve/lineages        (bots.v13.api via TestClient)

Coverage this deliberately does NOT repeat: the CLI -> loader engagement
round trip (``tests/test_lineage_cli.py``
``test_cli_engages_and_disengages_the_evolve_consumer``) and the
two-lineage write-back re-read (``tests/test_evolve_cli.py``
``test_lineage_write_back_persists_advanced_head_and_logs``).

What is new here is the run_loop -> disk -> API half and the UNSCHEDULED
third record: a generation is one lineage's turn, so three lineages over two
generations leaves one lineage unscheduled by construction. Asserting that it
survives on disk with its seed ``head_version`` and seed ``created_at`` is
the only check in the phase that catches a persist hop writing only the
lineages that took a turn.

Isolation: ``orchestrator.lineages._repo_root`` is pointed at ``tmp_path``
(``_load_lineage_registry_if_engaged`` and the persist hop both resolve
``default_lineages_path()`` with no path parameter), the seed is also given
``--path`` to that same file, every run_loop state file lives under
``tmp_path`` (including the EH.7 run-state lock), and the API is configured
with ``evolve_dir=tmp_path / "data"``. No SC2, no git commit, no network.
"""

from __future__ import annotations

import importlib.util
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
from bots.v13.api import app, configure
from fastapi.testclient import TestClient

import test_evolve_cli as harness
from orchestrator import lineages as lineages_mod
from orchestrator.evolve import Improvement
from orchestrator.lineages import DEFAULT_LINEAGE_ID, Lineage, load_lineages

_REPO_ROOT = Path(__file__).resolve().parent.parent

# Pinned into ``created_at`` at seed time so a persist hop that rebuilds a
# record instead of merging it (which restamps ``created_at`` to "now")
# cannot pass by landing in the same second as the seed.
_SEED_INSTANT = datetime(2001, 2, 3, 4, 5, 6, tzinfo=UTC)
_SEED_CREATED_AT = _SEED_INSTANT.isoformat()

# The smoke's own wall-clock ceiling (completion condition 2).
_SMOKE_BUDGET_S = 60.0


def _load_lineage_cli() -> ModuleType:
    """Import ``scripts/lineage.py`` as ``lineage_cli`` (same name and
    registration-before-exec dance as ``tests/test_lineage_cli.py``)."""
    if "lineage_cli" in sys.modules:
        return sys.modules["lineage_cli"]
    spec = importlib.util.spec_from_file_location(
        "lineage_cli", str(_REPO_ROOT / "scripts" / "lineage.py")
    )
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules["lineage_cli"] = mod
    try:
        spec.loader.exec_module(mod)
    except BaseException:
        sys.modules.pop("lineage_cli", None)
        raise
    return mod


class _FrozenSeedClock:
    """Stand-in for ``orchestrator.lineages.datetime`` during seeding only.

    ``Lineage.created_at``'s default factory is ``_now_iso``, which reads the
    module-global ``datetime`` at call time, so swapping that global pins the
    seed timestamp without touching production code.
    """

    @staticmethod
    def now(tz: Any = None) -> datetime:
        return _SEED_INSTANT


def test_lineage_chain_cli_seed_to_run_loop_to_disk_to_api(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    started = time.perf_counter()

    evolve = harness._load_cli_module()
    lineage_cli = _load_lineage_cli()

    # --- Isolation: every registry path resolves under tmp_path. ----------
    monkeypatch.setattr(lineages_mod, "_repo_root", lambda: tmp_path)
    registry_path = tmp_path / "data" / "lineages.json"
    assert lineages_mod.default_lineages_path() == registry_path
    assert registry_path.resolve().is_relative_to(tmp_path.resolve())

    # --- Producer 1: seed three lineages through the real CLI. ------------
    # The version-validation seam is pinned so ``add`` never reads the live
    # bots/ tree; the seed clock is pinned so ``created_at`` is distinctive.
    monkeypatch.setattr(lineages_mod, "list_versions", lambda: ["v0", "v9", "v10"])
    monkeypatch.setattr(lineages_mod, "datetime", _FrozenSeedClock)
    seeds = {DEFAULT_LINEAGE_ID: "v0", "line-2": "v9", "line-3": "v10"}
    for lineage_id, head in seeds.items():
        rc = lineage_cli.main(["--path", str(registry_path), "add", lineage_id, head])
        assert rc == 0, lineage_id
    # Restore the real clock: from here on a restamped created_at differs.
    monkeypatch.setattr(lineages_mod, "datetime", datetime)

    seeded = load_lineages(registry_path)
    assert {lid: lin.head_version for lid, lin in seeded.items()} == seeds
    assert {lin.created_at for lin in seeded.values()} == {_SEED_CREATED_AT}

    # --- Spy on the real loader (unpacks the (registry, from_disk) tuple). -
    real_loader = evolve._load_lineage_registry_if_engaged
    loader_returns: list[tuple[dict[str, Lineage], bool]] = []

    def spying_loader(lineages_arg: int) -> tuple[dict[str, Lineage], bool]:
        registry, from_disk = real_loader(lineages_arg)
        # Snapshot before run_loop's status partition can mutate the dict.
        loader_returns.append((dict(registry), from_disk))
        return registry, from_disk

    monkeypatch.setattr(evolve, "_load_lineage_registry_if_engaged", spying_loader)

    # --- Producer 2: drive the real run_loop, games stubbed. --------------
    monkeypatch.setattr(evolve, "check_sc2_installed", lambda: True)
    # Pointer flips would write bots/current/current.txt in the real repo.
    pointer_flips: list[str] = []
    monkeypatch.setattr(evolve, "_primitive_restore_pointer", pointer_flips.append)
    # The promotion path runs post-promotion hooks; keep real bot code out.
    monkeypatch.setattr(
        "bots.v0.learning.post_promotion_hooks.run_post_promotion_hooks",
        lambda _v: None,
    )

    # All run_loop state (results, pool, EH.7 run-state lock, round, crash
    # log, run log) lives under tmp_path; no_commit=True keeps git out.
    args = harness._build_args(tmp_path, pool_size=3, generations=2)
    args.lineages = 3
    for attr in (
        "results_path",
        "pool_path",
        "state_path",
        "current_round_path",
        "crash_log_path",
        "run_log",
    ):
        path = getattr(args, attr)
        assert Path(path).resolve().is_relative_to(tmp_path.resolve()), attr
    assert args.no_commit is True

    pool = harness._make_pool(3)

    def refresh(*a: Any, **k: Any) -> list[Improvement]:
        if k.get("skip_mirror"):
            return []
        return pool

    # The loaded registry iterates alphabetically (write_lineages uses
    # sort_keys=True), so the round-robin is line-2, line-3, main:
    #   gen 1: line-2 @ v9  -> rank-1 passes -> promote v9  -> v11
    #   gen 2: line-3 @ v10 -> rank-2 passes -> promote v10 -> v12
    #   main is never scheduled.
    def scripted_fitness(parent: str, imp: Improvement, **k: Any) -> Any:
        if (parent, imp.rank) in {("v9", 1), ("v10", 2)}:
            return harness._fitness(imp, bucket="pass", parent=parent)
        return harness._fitness(imp, bucket="close", parent=parent)

    stack_apply = harness._ScriptedStackApply([(True, "v11"), (True, "v12")])
    regression = harness._ScriptedRegression([False, False])

    rc = evolve.run_loop(
        args,
        generate_pool_fn=refresh,
        run_fitness_fn=scripted_fitness,
        stack_apply_fn=stack_apply,
        run_regression_fn=regression,
        current_version_fn=lambda: "v0",
    )
    assert rc == 0
    assert [c["parent"] for c in stack_apply.calls] == ["v9", "v10"]
    assert [c["new_parent"] for c in regression.calls] == ["v11", "v12"]

    # The scheduler read the three-record on-disk registry, not the single
    # synthesized implicit ``main``.
    assert len(loader_returns) == 1
    loaded_registry, from_disk = loader_returns[0]
    assert len(loaded_registry) == 3, sorted(loaded_registry)
    assert set(loaded_registry) == set(seeds)
    assert from_disk is True

    # --- Disk: re-read through the production reader. ---------------------
    persisted = load_lineages(registry_path)
    assert set(persisted) == set(seeds), sorted(persisted)
    advanced = {"line-2": "v11", "line-3": "v12"}
    for lineage_id, new_head in advanced.items():
        assert persisted[lineage_id].head_version != seeds[lineage_id]
        assert persisted[lineage_id].head_version == new_head
        assert persisted[lineage_id].created_at == _SEED_CREATED_AT
    # The UNSCHEDULED third record survived the whole-file replace intact.
    survivor = persisted[DEFAULT_LINEAGE_ID]
    assert survivor.head_version == seeds[DEFAULT_LINEAGE_ID]
    assert survivor.created_at == _SEED_CREATED_AT
    assert survivor.status == "active"

    # --- Consumer: the dashboard endpoint against that same directory. ----
    data_dir = tmp_path / "data"
    log_dir = tmp_path / "logs"
    replay_dir = tmp_path / "replays"
    log_dir.mkdir()
    replay_dir.mkdir()
    configure(data_dir, log_dir, replay_dir, evolve_dir=data_dir)
    with TestClient(app) as client:
        response = client.get("/api/evolve/lineages")
    assert response.status_code == 200
    body = response.json()
    assert isinstance(body["lineages"], list) and body["lineages"]
    served = {row["lineage_id"]: row["head_version"] for row in body["lineages"]}
    assert served == {**seeds, **advanced}, served

    elapsed = time.perf_counter() - started
    print(f"chain smoke elapsed: {elapsed:.2f}s")
    assert elapsed < _SMOKE_BUDGET_S, f"chain smoke took {elapsed:.1f}s"
