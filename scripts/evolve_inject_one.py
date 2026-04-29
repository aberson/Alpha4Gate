"""Inject a single named imp from ``data/evolve_favorites.json`` directly
into stack-apply + regression, bypassing the fitness phase.

A debug aid for iterating on the stack-apply pipeline without burning
fitness eval time. Selects the first favorite whose title contains the
provided substring (case-insensitive), wraps it in an ``Improvement``,
and runs the same primitives the full evolve loop uses:

1. ``_stack_apply_and_promote`` — snapshot parent → apply imp →
   import-check → ``[evo-auto]`` commit (skipped under ``--no-commit``).
2. ``run_regression_eval`` — new vs prior parent (default 5 games).
3. On regression rollback: ``git_revert_evo_auto`` produces an
   ``[evo-auto]`` revert commit so the working tree is clean again.

Usage::

    python scripts/evolve_inject_one.py --title "DEFEND/FORTIFY"
    python scripts/evolve_inject_one.py --title "Observer escort" --no-commit
    python scripts/evolve_inject_one.py --title "Gas-dump" --games-per-eval 3

The matching is by ``str.lower() in str.lower()`` against the favorite's
``title`` field. If multiple favorites match, the script exits and lists
them so the operator can pick a more specific substring.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import logging
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT / "src"))

# Load scripts/evolve.py via importlib because `scripts/` is not a
# package (no __init__.py). All the helpers we need
# (_stack_apply_and_promote, the EVO_AUTO commit/revert, the pre-flight
# checks) are defined module-level there; loading once at import time
# lets us reuse them without copy-pasting their bodies.
_evolve_spec = importlib.util.spec_from_file_location(
    "_evolve_inject_runner",
    _REPO_ROOT / "scripts" / "evolve.py",
)
assert _evolve_spec is not None and _evolve_spec.loader is not None
_evolve_mod = importlib.util.module_from_spec(_evolve_spec)
# Register before exec so @dataclass / typing introspection that looks up
# `sys.modules[cls.__module__]` finds the module — without this Python
# 3.14's dataclasses raises AttributeError on the first decorator.
sys.modules["_evolve_inject_runner"] = _evolve_mod
_evolve_spec.loader.exec_module(_evolve_mod)

_stack_apply_and_promote = _evolve_mod._stack_apply_and_promote
git_commit_evo_auto = _evolve_mod.git_commit_evo_auto
git_revert_evo_auto = _evolve_mod.git_revert_evo_auto
check_git_clean = _evolve_mod.check_git_clean
check_no_phantom_promote = _evolve_mod.check_no_phantom_promote
check_sc2_installed = _evolve_mod.check_sc2_installed
_restore_current_pointer = _evolve_mod._restore_current_pointer

from orchestrator.evolve import Improvement, run_regression_eval  # noqa: E402
from orchestrator.evolve_dev_apply import spawn_dev_subagent  # noqa: E402
from orchestrator.registry import current_version  # noqa: E402

_log = logging.getLogger("evolve_inject_one")


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python scripts/evolve_inject_one.py",
        description=(
            "Inject one imp from data/evolve_favorites.json into "
            "stack-apply + regression, skipping fitness."
        ),
    )
    parser.add_argument(
        "--title",
        required=True,
        help=(
            "Case-insensitive substring of the favorite's title. "
            "Matched against `data/evolve_favorites.json` `favorites[].title`."
        ),
    )
    parser.add_argument(
        "--favorites-path",
        type=Path,
        default=_REPO_ROOT / "data" / "evolve_favorites.json",
        help="Favorites JSON file (default: data/evolve_favorites.json).",
    )
    parser.add_argument(
        "--games-per-eval",
        type=int,
        default=5,
        help="Regression games (default: 5; same threshold as full evolve).",
    )
    parser.add_argument(
        "--map",
        default="Simple64",
        help="SC2 map (default: Simple64).",
    )
    parser.add_argument(
        "--game-time-limit",
        type=int,
        default=1800,
    )
    parser.add_argument(
        "--hard-timeout",
        type=float,
        default=2700.0,
    )
    parser.add_argument(
        "--no-commit",
        action="store_true",
        help="Skip the auto-commit on promote (dry-run; leaves snapshot on disk).",
    )
    parser.add_argument(
        "--skip-regression",
        action="store_true",
        help=(
            "Stop after stack-apply+commit; do not run regression. Useful "
            "for fast pipeline-only sanity checks."
        ),
    )
    return parser


def _load_favorite(favorites_path: Path, title_query: str) -> Improvement:
    """Find the matching favorite and turn it into an ``Improvement``.

    Exits the process on no-match or ambiguous-match — the operator
    needs to disambiguate before we burn an eval cycle.
    """
    if not favorites_path.is_file():
        print(
            f"inject-one: favorites file not found at {favorites_path}",
            file=sys.stderr,
        )
        sys.exit(1)

    payload = json.loads(favorites_path.read_text(encoding="utf-8"))
    favorites = payload.get("favorites", [])
    needle = title_query.lower()
    matches = [f for f in favorites if needle in f.get("title", "").lower()]

    if not matches:
        print(
            f"inject-one: no favorite matches {title_query!r}. Available "
            f"titles:",
            file=sys.stderr,
        )
        for f in favorites:
            print(f"  - {f.get('title')}", file=sys.stderr)
        sys.exit(1)
    if len(matches) > 1:
        print(
            f"inject-one: {len(matches)} favorites match {title_query!r}; "
            "narrow the substring:",
            file=sys.stderr,
        )
        for f in matches:
            print(f"  - {f.get('title')}", file=sys.stderr)
        sys.exit(1)

    fav = matches[0]
    return Improvement(
        rank=1,
        title=fav["title"],
        type=fav.get("type", "dev"),
        description=fav.get("description", ""),
        principle_ids=list(fav.get("principle_ids") or []),
        expected_impact=fav.get("expected_impact", ""),
        concrete_change=fav.get("concrete_change", ""),
        files_touched=list(fav.get("files_touched") or []),
    )


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )
    args = _build_parser().parse_args(argv)

    # --- Pre-flight ---
    check_git_clean()
    if not check_sc2_installed():
        print("inject-one: SC2 not installed; aborting.", file=sys.stderr)
        return 1
    phantom_ok, head_v, disk_v = check_no_phantom_promote()
    if not phantom_ok:
        print(
            "inject-one: phantom-promote state — "
            f"disk={disk_v!r} HEAD={head_v!r}. Recover before injecting.",
            file=sys.stderr,
        )
        return 1

    parent = current_version()
    imp = _load_favorite(args.favorites_path, args.title)

    _log.info("inject-one: parent=%s, imp=%r", parent, imp.title)
    _log.info(
        "inject-one: stack-apply phase (commit=%s)",
        "no" if args.no_commit else "yes",
    )

    commit_fn = None if args.no_commit else git_commit_evo_auto
    outcome = _stack_apply_and_promote(
        parent=parent,
        winning_imps=[imp],
        dev_apply_fn=spawn_dev_subagent,
        generation=0,
        commit_fn=commit_fn,
    )

    print()
    print("=" * 72)
    print(f"STACK-APPLY OUTCOME: {outcome.outcome}")
    print(f"reason: {outcome.reason}")
    print("=" * 72)

    if not outcome.promoted:
        return 2

    new_version = outcome.new_version
    promote_sha = outcome.promote_sha
    assert new_version is not None  # promoted=True invariant

    if args.skip_regression:
        print(
            f"inject-one: skipping regression. New parent on disk: "
            f"{new_version} (promote sha={promote_sha})."
        )
        return 0

    # --- Regression ---
    _log.info(
        "inject-one: regression %s vs %s (%d games)",
        new_version,
        parent,
        args.games_per_eval,
    )
    reg = run_regression_eval(
        new_parent=new_version,
        prior_parent=parent,
        games=args.games_per_eval,
        map_name=args.map,
        game_time_limit=args.game_time_limit,
        hard_timeout=args.hard_timeout,
    )

    print()
    print("=" * 72)
    print(
        f"REGRESSION OUTCOME: "
        f"{new_version} {reg.wins_new}-{reg.wins_prior} {parent} "
        f"({'rolled-back' if reg.rolled_back else 'PASS'})"
    )
    print(f"reason: {reg.reason}")
    print("=" * 72)

    if not reg.rolled_back:
        sha_short = promote_sha[:12] if promote_sha else "n/a"
        revert_target = promote_sha[:12] if promote_sha else "<sha>"
        print(
            f"\ninject-one: SUCCESS — {new_version} promoted "
            f"(commit {sha_short}). Roll back manually with "
            f"`git revert {revert_target}` if undesired."
        )
        return 0

    # Regression rolled back — revert the promote commit so the tree is
    # clean again.
    if promote_sha is None:
        print(
            "inject-one: regression rolled back but no promote sha to revert "
            "(likely --no-commit). Restoring pointer manually.",
            file=sys.stderr,
        )
        _restore_current_pointer(parent)
        return 3

    _log.info("inject-one: reverting promote %s", promote_sha)
    revert_ok = git_revert_evo_auto(
        promote_sha=promote_sha,
        generation=0,
        reason=reg.reason,
    )
    if not revert_ok:
        print(
            "inject-one: regression rolled back but `git revert` FAILED. "
            f"Reconcile manually: `git revert {promote_sha}`.",
            file=sys.stderr,
        )
        return 4

    print(
        f"\ninject-one: regression rolled back; promote {promote_sha[:12]} "
        "reverted. Tree is clean."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
