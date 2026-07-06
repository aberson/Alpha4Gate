"""Mine ``data/evolve_results.jsonl`` for high-performing improvements.

Writes ``data/evolve_favorites.json`` with every imp that achieved at
least one ``fitness-pass`` outcome, deduplicated by title, annotated
with each observation's score + which generation/candidate produced it.

Idempotent: re-run after a fresh evolve soak to refresh the favorites
list. The output is gitignored (``data/`` is per-user state) — this is
a curation aid, not a tracked artifact.

Flags (all opt-in; running bare produces byte-identical output to before):

``--exclude-promoted``
    Drop any favorite whose imp was stacked in a ``stack-apply-pass``
    generation whose ``regression`` phase in the SAME generation ALSO
    passed — i.e. the promotion survived the regression gate and is now
    baked into a promoted parent. A fitness-passer that was never promoted
    (or whose promotion rolled back) STAYS: resurrecting a non-promoted
    idea is legitimate.

``--merge-existing``
    Cumulatively merge track records with the EXISTING favorites file.
    ``evolve_results.jsonl`` is truncated per fresh run, so this-run mining
    would otherwise lose all prior-run observations; this folds them back
    in, preserving (not clobbering) prior fitness / stack-apply records.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parents[1]
_RESULTS_PATH = _REPO_ROOT / "data" / "evolve_results.jsonl"
_OUT_PATH = _REPO_ROOT / "data" / "evolve_favorites.json"

# Make ``orchestrator`` importable when run as a bare script (mirrors the
# bootstrap in scripts/evolve.py). The title-normalization used by the
# --exclude-promoted match is the SAME helper the evolve priors filter
# uses — one source of truth, imported, never re-implemented here.
if str(_REPO_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT / "src"))

from orchestrator.evolve import normalize_prior_title  # noqa: E402

_IMP_FIELDS = (
    "title",
    "type",
    "description",
    "principle_ids",
    "expected_impact",
    "concrete_change",
    "files_touched",
)


def _load_rows(path: Path) -> list[dict[str, Any]]:
    with path.open(encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def _imp_key(imp: dict[str, Any]) -> str:
    return imp["title"]


def _mine_favorites(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Build the title-keyed favorites map from results rows.

    First pass collects every ``fitness-pass`` imp (dedup by title,
    accumulating fitness observations); second pass annotates any that
    also reached ``stack_apply``. Insertion order follows first-seen
    fitness-pass order — the sort in :func:`main` is stable over it.
    """
    favorites: dict[str, dict[str, Any]] = {}

    for row in rows:
        phase = row.get("phase")
        if phase == "fitness" and row.get("outcome") == "fitness-pass":
            imp = row["imp"]
            key = _imp_key(imp)
            entry = favorites.setdefault(
                key,
                {f: imp.get(f) for f in _IMP_FIELDS}
                | {
                    "track_record": {
                        "fitness_observations": [],
                        "stack_apply_observations": [],
                    }
                },
            )
            entry["track_record"]["fitness_observations"].append(
                {
                    "generation": row.get("generation"),
                    "score": f"{row.get('wins_cand', 0)}-{row.get('wins_parent', 0)}",
                    "candidate": row.get("candidate"),
                    "parent": row.get("parent"),
                }
            )

    # Second pass — annotate any favorite that also reached stack-apply.
    for row in rows:
        if row.get("phase") != "stack_apply":
            continue
        for imp in row.get("stacked_imps") or []:
            key = _imp_key(imp)
            if key not in favorites:
                continue
            favorites[key]["track_record"]["stack_apply_observations"].append(
                {
                    "generation": row.get("generation"),
                    "outcome": row.get("outcome"),
                    "parent": row.get("parent"),
                }
            )

    return favorites


def _row_stacked_titles(row: dict[str, Any]) -> list[str]:
    """Titles stacked in a ``stack_apply`` row (prefer ``stacked_titles``)."""
    titles = row.get("stacked_titles")
    if isinstance(titles, list):
        return [t for t in titles if isinstance(t, str)]
    return [
        imp["title"]
        for imp in (row.get("stacked_imps") or [])
        if isinstance(imp, dict) and isinstance(imp.get("title"), str)
    ]


def _promoted_survivor_titles(rows: list[dict[str, Any]]) -> set[str]:
    """Normalized titles of imps whose promotion SURVIVED regression.

    An imp counts iff it was stacked in a ``stack-apply-pass`` generation
    whose ``regression`` row in the SAME generation is ``regression-pass``
    (i.e. not rolled back). Correlation is by ``generation`` index against
    the real results-row schema:

    - ``stack_apply`` rows: ``generation`` + ``outcome`` + ``stacked_titles``
      (fallback: titles from ``stacked_imps``).
    - ``regression`` rows: ``generation`` + ``outcome``.

    A promotion that rolled back (``regression-rollback``), or a stack-apply
    that had no passing regression row (crash / skipped), is NOT counted —
    so a rolled-back or never-promoted idea remains resurrection-eligible.
    """
    survived_gens: set[Any] = {
        row["generation"]
        for row in rows
        if row.get("phase") == "regression"
        and row.get("outcome") == "regression-pass"
        and row.get("generation") is not None
    }
    titles: set[str] = set()
    for row in rows:
        if row.get("phase") != "stack_apply":
            continue
        if row.get("outcome") != "stack-apply-pass":
            continue
        if row.get("generation") not in survived_gens:
            continue
        for title in _row_stacked_titles(row):
            titles.add(normalize_prior_title(title))
    return titles


def _exclude_promoted(
    favorites: dict[str, dict[str, Any]],
    promoted_norm: set[str],
) -> dict[str, dict[str, Any]]:
    """Drop favorites whose normalized title is a surviving-promotion."""
    if not promoted_norm:
        return favorites
    return {
        key: fav
        for key, fav in favorites.items()
        if normalize_prior_title(_imp_key(fav)) not in promoted_norm
    }


def _merge_existing(
    new_favs: dict[str, dict[str, Any]],
    existing_path: Path,
) -> dict[str, dict[str, Any]]:
    """Fold prior-run observations from ``existing_path`` into ``new_favs``.

    For a title present in both, prior-run fitness / stack-apply
    observations are prepended and this-run's appended (de-duplicated), so
    nothing is clobbered. This-run imp fields win (freshest description /
    files_touched). Titles present only in the existing file are carried
    through unchanged. A missing / malformed existing file is a no-op.
    """
    try:
        payload = json.loads(existing_path.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        return new_favs

    merged: dict[str, dict[str, Any]] = dict(new_favs)
    for prior in payload.get("favorites") or []:
        key = _imp_key(prior)
        prior_track = prior.get("track_record") or {}
        if key not in merged:
            merged[key] = prior
            continue
        track = merged[key].setdefault(
            "track_record",
            {"fitness_observations": [], "stack_apply_observations": []},
        )
        for field in ("fitness_observations", "stack_apply_observations"):
            prior_obs = list(prior_track.get(field) or [])
            this_run = list(track.get(field) or [])
            # prior-run observations first, then this-run's (dedup).
            track[field] = prior_obs + [obs for obs in this_run if obs not in prior_obs]
    return merged


def _fitness_observations(fav: dict[str, Any]) -> list[Any]:
    """Fitness observations for a favorite, tolerant of schema drift."""
    track = fav.get("track_record")
    if not isinstance(track, dict):
        return []
    obs = track.get("fitness_observations")
    return obs if isinstance(obs, list) else []


def _stack_apply_observations(fav: dict[str, Any]) -> list[Any]:
    """Stack-apply observations for a favorite, tolerant of schema drift."""
    track = fav.get("track_record")
    if not isinstance(track, dict):
        return []
    obs = track.get("stack_apply_observations")
    return obs if isinstance(obs, list) else []


def _best_fitness_score(fav: dict[str, Any]) -> int:
    """Max candidate-win count across a favorite's fitness observations.

    Returns 0 when the favorite carries no usable ``score`` — missing
    ``track_record``, empty ``fitness_observations``, or a score that is
    not an ``N-M`` string. This keeps a schema-drifted prior-only favorite
    (carried through from an older, differently-shaped favorites file by
    ``--merge-existing``) from crashing the sort key / summary loop; it
    simply sorts last. For well-formed observations the value is identical
    to ``max(int(score.split('-')[0]) ...)``, so the default (no-flag)
    output stays byte-identical.
    """
    best = 0
    for obs in _fitness_observations(fav):
        if not isinstance(obs, dict):
            continue
        score = obs.get("score")
        if not isinstance(score, str) or "-" not in score:
            continue
        try:
            best = max(best, int(score.split("-")[0]))
        except ValueError:
            continue
    return best


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Mine evolve_results.jsonl into data/evolve_favorites.json.",
    )
    parser.add_argument(
        "--exclude-promoted",
        action="store_true",
        help=(
            "Drop favorites whose imp was promoted AND survived the "
            "regression gate this run (baked into a promoted parent). "
            "Rolled-back or never-promoted fitness-passers stay."
        ),
    )
    parser.add_argument(
        "--merge-existing",
        action="store_true",
        help=(
            "Cumulatively merge track records with the existing favorites "
            "file, preserving prior-run observations (evolve_results.jsonl "
            "is truncated per fresh run)."
        ),
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)

    if not _RESULTS_PATH.exists():
        print(f"no results file at {_RESULTS_PATH}")
        return 1

    rows = _load_rows(_RESULTS_PATH)

    favorites = _mine_favorites(rows)

    if args.merge_existing:
        favorites = _merge_existing(favorites, _OUT_PATH)

    if args.exclude_promoted:
        favorites = _exclude_promoted(favorites, _promoted_survivor_titles(rows))

    sorted_favs = sorted(
        favorites.values(),
        key=lambda f: -_best_fitness_score(f),
    )

    payload = {
        "generated_at": datetime.now(UTC).isoformat(),
        "source": str(_RESULTS_PATH.relative_to(_REPO_ROOT)),
        "criteria": "imps with >=1 fitness-pass observation in evolve_results.jsonl",
        "count": len(sorted_favs),
        "favorites": sorted_favs,
    }

    _OUT_PATH.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    print(f"wrote {len(sorted_favs)} favorites to {_OUT_PATH.relative_to(_REPO_ROOT)}")
    print()
    print(f"{'best':>5}  title")
    print(f"{'----':>5}  -----")
    for fav in sorted_favs:
        best = _best_fitness_score(fav)
        n_obs = len(_fitness_observations(fav))
        n_stack = len(_stack_apply_observations(fav))
        marker = f"{best}/5"
        if n_obs > 1:
            marker += f" (×{n_obs})"
        if n_stack:
            marker += f" SA×{n_stack}"
        print(f"  {marker:<10}  {fav['title']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
