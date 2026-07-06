"""Tests for ``scripts/curate_evolve_favorites.py`` (Phase EJ Step EJ.1).

Covers the ``--exclude-promoted`` correlation (stack-apply-pass +
regression-pass, joined by generation index) and the cumulative
``--merge-existing`` fold. The module is loaded via importlib because
``scripts/`` is not on the default test path.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from orchestrator.evolve import normalize_prior_title as _evolve_normalize

_REPO_ROOT = Path(__file__).resolve().parent.parent


def _load_curate_module() -> ModuleType:
    """Import ``scripts/curate_evolve_favorites.py`` as ``curate_favorites``."""
    if "curate_favorites" in sys.modules:
        return sys.modules["curate_favorites"]
    spec = importlib.util.spec_from_file_location(
        "curate_favorites",
        str(_REPO_ROOT / "scripts" / "curate_evolve_favorites.py"),
    )
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules["curate_favorites"] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def curate() -> ModuleType:
    return _load_curate_module()


# ---------------------------------------------------------------------------
# Results-row builders (match the real evolve_results.jsonl schema)
# ---------------------------------------------------------------------------


def _imp_dict(title: str) -> dict[str, Any]:
    return {
        "title": title,
        "type": "dev",
        "description": f"desc for {title}",
        "principle_ids": ["§1"],
        "expected_impact": "impact",
        "concrete_change": f"change {title}",
        "files_touched": ["bots/v0/bot.py"],
    }


def _fitness_row(
    gen: int, title: str, *, wins_cand: int = 4, wins_parent: int = 1
) -> dict[str, Any]:
    return {
        "phase": "fitness",
        "generation": gen,
        "parent": "v0",
        "imp": _imp_dict(title),
        "candidate": f"cand_{title}",
        "wins_cand": wins_cand,
        "wins_parent": wins_parent,
        "outcome": "fitness-pass",
    }


def _stack_row(gen: int, outcome: str, titles: list[str]) -> dict[str, Any]:
    return {
        "phase": "stack_apply",
        "generation": gen,
        "parent": "v0",
        "new_version": f"v{gen}",
        "stacked_imps": [_imp_dict(t) for t in titles],
        "stacked_titles": list(titles),
        "outcome": outcome,
        "reason": "",
    }


def _regression_row(gen: int, outcome: str) -> dict[str, Any]:
    return {
        "phase": "regression",
        "generation": gen,
        "new_parent": f"v{gen}",
        "prior_parent": "v0",
        "wins_new": 3,
        "wins_prior": 2,
        "outcome": outcome,
        "rolled_back": outcome == "regression-rollback",
        "reason": "",
    }


def _fixture_rows() -> list[dict[str, Any]]:
    """A results log with a promote gen, a rollback gen, an import-fail gen.

    - gen 1: Alpha promoted AND survived regression   -> excludable
    - gen 2: Beta promoted but regression rolled back  -> retained
    - gen 3: Gamma stacked but import-failed           -> retained
    - gen 4: Delta fitness-passed, never stacked       -> retained
    """
    return [
        _fitness_row(1, "Alpha"),
        _stack_row(1, "stack-apply-pass", ["Alpha"]),
        _regression_row(1, "regression-pass"),
        _fitness_row(2, "Beta"),
        _stack_row(2, "stack-apply-pass", ["Beta"]),
        _regression_row(2, "regression-rollback"),
        _fitness_row(3, "Gamma"),
        _stack_row(3, "stack-apply-import-fail", ["Gamma"]),
        _fitness_row(4, "Delta"),
    ]


# ---------------------------------------------------------------------------
# One source of truth
# ---------------------------------------------------------------------------


def test_normalization_is_single_source_of_truth(curate: ModuleType) -> None:
    """The curator reuses the SAME normalization object as the priors filter.

    Identity, not equality — a future re-implementation in the curator
    would rebind the name to a different object and fail CI.
    """
    assert curate.normalize_prior_title is _evolve_normalize


# ---------------------------------------------------------------------------
# Exact-generation join (--exclude-promoted correlation)
# ---------------------------------------------------------------------------


def test_promoted_survivor_titles_exact_generation_join(
    curate: ModuleType,
) -> None:
    """Only the promote-and-survived generation's title is returned."""
    survivors = curate._promoted_survivor_titles(_fixture_rows())
    assert survivors == {_evolve_normalize("Alpha")}
    # Rolled-back (Beta) and import-failed (Gamma) are absent.
    assert _evolve_normalize("Beta") not in survivors
    assert _evolve_normalize("Gamma") not in survivors


def test_promoted_survivor_requires_same_generation_regression(
    curate: ModuleType,
) -> None:
    """A stack-apply-pass with NO passing regression row is not a survivor.

    Cross-generation mismatch guard: a regression-pass in gen 2 must not
    license a stack-apply-pass in gen 1 whose own regression rolled back.
    """
    rows = [
        _stack_row(1, "stack-apply-pass", ["Alpha"]),
        _regression_row(1, "regression-rollback"),
        _stack_row(2, "stack-apply-pass", ["Beta"]),
        _regression_row(2, "regression-pass"),
    ]
    survivors = curate._promoted_survivor_titles(rows)
    assert survivors == {_evolve_normalize("Beta")}


def test_survivor_join_falls_back_to_stacked_imps(curate: ModuleType) -> None:
    """When ``stacked_titles`` is absent, titles come from ``stacked_imps``."""
    stack = _stack_row(1, "stack-apply-pass", ["Alpha"])
    del stack["stacked_titles"]
    rows = [stack, _regression_row(1, "regression-pass")]
    assert curate._promoted_survivor_titles(rows) == {_evolve_normalize("Alpha")}


# ---------------------------------------------------------------------------
# --exclude-promoted drop behavior
# ---------------------------------------------------------------------------


def test_exclude_promoted_drops_survivor_keeps_others(
    curate: ModuleType,
) -> None:
    rows = _fixture_rows()
    favorites = curate._mine_favorites(rows)
    assert set(favorites) == {"Alpha", "Beta", "Gamma", "Delta"}

    filtered = curate._exclude_promoted(favorites, curate._promoted_survivor_titles(rows))
    # Promoted-and-survived Alpha dropped.
    assert "Alpha" not in filtered
    # Rolled-back Beta, import-failed Gamma, never-stacked Delta all stay.
    assert {"Beta", "Gamma", "Delta"} == set(filtered)


def test_exclude_promoted_matches_normalized_title(curate: ModuleType) -> None:
    """Case/punctuation drift between favorite + stacked title still matches."""
    rows = [
        _fitness_row(1, "Shield-Aware Focus-Fire"),
        _stack_row(1, "stack-apply-pass", ["shield aware focus fire"]),
        _regression_row(1, "regression-pass"),
    ]
    favorites = curate._mine_favorites(rows)
    assert "Shield-Aware Focus-Fire" in favorites
    filtered = curate._exclude_promoted(favorites, curate._promoted_survivor_titles(rows))
    assert filtered == {}


def test_default_no_flags_retains_promoted(
    curate: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Running bare (no --exclude-promoted) keeps the promoted favorite."""
    payload = _run_main(curate, tmp_path, monkeypatch, argv=[])
    titles = {f["title"] for f in payload["favorites"]}
    assert titles == {"Alpha", "Beta", "Gamma", "Delta"}


# ---------------------------------------------------------------------------
# --merge-existing cumulative fold
# ---------------------------------------------------------------------------


def test_merge_existing_preserves_prior_observations(curate: ModuleType, tmp_path: Path) -> None:
    """Prior-run observations survive a fresh (truncated-results) mining."""
    existing_path = tmp_path / "existing.json"
    existing_path.write_text(
        json.dumps(
            {
                "favorites": [
                    _imp_dict("Alpha")
                    | {
                        "track_record": {
                            "fitness_observations": [
                                {
                                    "generation": 5,
                                    "score": "5-0",
                                    "candidate": "cand_old",
                                    "parent": "v4",
                                }
                            ],
                            "stack_apply_observations": [
                                {
                                    "generation": 5,
                                    "outcome": "stack-apply-pass",
                                    "parent": "v4",
                                }
                            ],
                        }
                    },
                    # A title only in the prior file — must carry through.
                    _imp_dict("Legacy")
                    | {
                        "track_record": {
                            "fitness_observations": [{"generation": 3, "score": "4-1"}],
                            "stack_apply_observations": [],
                        }
                    },
                ]
            }
        ),
        encoding="utf-8",
    )

    # This run's truncated results only saw Alpha at gen 1.
    new_favs = curate._mine_favorites([_fitness_row(1, "Alpha")])
    merged = curate._merge_existing(new_favs, existing_path)

    alpha_fit = merged["Alpha"]["track_record"]["fitness_observations"]
    gens = {obs["generation"] for obs in alpha_fit}
    assert gens == {5, 1}  # prior gen 5 preserved AND this-run gen 1 added
    # Prior stack-apply observation preserved too.
    assert merged["Alpha"]["track_record"]["stack_apply_observations"]
    # Prior-only title carried through unchanged.
    assert "Legacy" in merged


def test_merge_missing_existing_file_is_noop(curate: ModuleType, tmp_path: Path) -> None:
    new_favs = curate._mine_favorites([_fitness_row(1, "Alpha")])
    merged = curate._merge_existing(new_favs, tmp_path / "nope.json")
    assert merged == new_favs


# ---------------------------------------------------------------------------
# main() end-to-end (flag wiring)
# ---------------------------------------------------------------------------


def _run_main(
    curate: ModuleType,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    argv: list[str],
    rows: list[dict[str, Any]] | None = None,
    existing_out: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Drive ``curate.main(argv)`` against tmp state; return the written payload.

    ``existing_out`` pre-seeds the output file (data/evolve_favorites.json)
    before the run — exercises the ``--merge-existing`` read path.
    """
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    results = data_dir / "evolve_results.jsonl"
    out = data_dir / "evolve_favorites.json"
    rows = rows if rows is not None else _fixture_rows()
    results.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
    if existing_out is not None:
        out.write_text(json.dumps(existing_out), encoding="utf-8")
    monkeypatch.setattr(curate, "_REPO_ROOT", tmp_path)
    monkeypatch.setattr(curate, "_RESULTS_PATH", results)
    monkeypatch.setattr(curate, "_OUT_PATH", out)
    rc = curate.main(argv)
    assert rc == 0
    return json.loads(out.read_text(encoding="utf-8"))


def test_main_exclude_promoted_end_to_end(
    curate: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    payload = _run_main(curate, tmp_path, monkeypatch, argv=["--exclude-promoted"])
    titles = {f["title"] for f in payload["favorites"]}
    assert "Alpha" not in titles
    assert {"Beta", "Gamma", "Delta"} == titles


def test_merge_existing_survives_schema_drifted_prior(
    curate: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """--merge-existing must not crash on a schema-drifted prior-only favorite.

    Regression for the non-defensive sort key / summary loop: an older
    favorites file carried through by the merge can have a favorite that is
    missing ``track_record``, has empty ``fitness_observations``, or an
    observation with no ``score``. The run must still exit 0 and keep those
    favorites (sorted last), not blow up with KeyError/ValueError.
    """
    existing = {
        "favorites": [
            # (1) no track_record at all
            {"title": "DriftNoTrack", "type": "dev"},
            # (2) empty fitness_observations
            {
                "title": "DriftEmptyObs",
                "type": "dev",
                "track_record": {
                    "fitness_observations": [],
                    "stack_apply_observations": [],
                },
            },
            # (3) an observation missing its "score"
            {
                "title": "DriftNoScore",
                "type": "dev",
                "track_record": {
                    "fitness_observations": [{"generation": 9}],
                    "stack_apply_observations": [],
                },
            },
        ]
    }
    payload = _run_main(
        curate,
        tmp_path,
        monkeypatch,
        argv=["--merge-existing"],
        rows=[_fitness_row(1, "Alpha")],
        existing_out=existing,
    )
    titles = {f["title"] for f in payload["favorites"]}
    # This-run Alpha plus all three schema-drifted priors survive.
    assert titles == {"Alpha", "DriftNoTrack", "DriftEmptyObs", "DriftNoScore"}
    # Well-scored Alpha sorts ahead of the score-less drift favorites.
    assert payload["favorites"][0]["title"] == "Alpha"
