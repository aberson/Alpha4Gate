"""Mine ``data/evolve_results.jsonl`` for high-performing improvements.

Writes ``data/evolve_favorites.json`` with every imp that achieved at
least one ``fitness-pass`` outcome, deduplicated by title, annotated
with each observation's score + which generation/candidate produced it.

Idempotent: re-run after a fresh evolve soak to refresh the favorites
list. The output is gitignored (``data/`` is per-user state) — this is
a curation aid, not a tracked artifact.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parents[1]
_RESULTS_PATH = _REPO_ROOT / "data" / "evolve_results.jsonl"
_OUT_PATH = _REPO_ROOT / "data" / "evolve_favorites.json"

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


def main() -> int:
    if not _RESULTS_PATH.exists():
        print(f"no results file at {_RESULTS_PATH}")
        return 1

    rows = _load_rows(_RESULTS_PATH)

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

    sorted_favs = sorted(
        favorites.values(),
        key=lambda f: -max(
            int(obs["score"].split("-")[0])
            for obs in f["track_record"]["fitness_observations"]
        ),
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
        best = max(
            int(obs["score"].split("-")[0])
            for obs in fav["track_record"]["fitness_observations"]
        )
        n_obs = len(fav["track_record"]["fitness_observations"])
        n_stack = len(fav["track_record"]["stack_apply_observations"])
        marker = f"{best}/5"
        if n_obs > 1:
            marker += f" (×{n_obs})"
        if n_stack:
            marker += f" SA×{n_stack}"
        print(f"  {marker:<10}  {fav['title']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
