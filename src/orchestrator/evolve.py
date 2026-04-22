"""Individual-vs-parent fitness + full-stack composition + regression primitives.

Replaces the older (1+λ)-ES sibling-tournament round. Design rationale is
captured in ``documentation/investigations/evolve-algorithm-redesign-investigation.md``;
the short version:

- One imp at a time vs the parent (fitness phase, default 5 games).
- ``>=3/5`` → winner candidate; ``2/5`` → close-loss (resurrection-eligible,
  ``retry_count`` capped at 2); ``0-1/5`` → evicted.
- All winner candidates are stacked onto one snapshot and tested vs parent
  for 5 games (composition phase). Pass → promote the whole stack as one
  ``[evo-auto]`` commit. Fail → fall back to top-1 imp re-composition.
- New parent plays prior parent for 5 games (regression phase). Rollback
  on regression.

Public surface:

- :class:`Improvement` — frozen dataclass describing a proposed change
  (now carries optional ``files_touched`` for orthogonality checks).
- :class:`FitnessResult`, :class:`CompositionResult`, :class:`RegressionResult`
  — phase-specific result types replacing the old ``RoundResult``.
- :func:`apply_improvement` — apply one :class:`Improvement` to a version dir.
- :func:`generate_pool` — run mirror games, prompt Claude for ``pool_size``
  orthogonal improvements (re-prompts once on conflict).
- :func:`run_fitness_eval` — one-imp-vs-parent evaluation primitive.
- :func:`run_composition_eval` — full-stack promotion primitive (also used
  for the top-1 fallback path with a single-element list).
- :func:`run_regression_eval` — new-parent vs prior-parent regression gate.

Design notes
------------

* ``snapshot_current`` updates ``bots/current/current.txt`` as a side
  effect. Each primitive restores the pointer on every outcome path so the
  caller never sees a dangling scratch pointer.
* Scratch ``cand_*`` directories are discarded on every outcome EXCEPT a
  composition-pass, which promotes the scratch into a permanent ``vN`` via
  a second ``snapshot_current`` and then deletes the scratch.
* ``dev``-type improvements are dispatched to a caller-supplied
  ``dev_apply_fn``. Without one, ``dev`` imps raise
  :class:`NotImplementedError`.
* Cleanup failures (``shutil.rmtree`` errors) are logged at WARNING and
  do NOT mask the real outcome.
"""

from __future__ import annotations

import dataclasses
import json
import logging
import os
import re
import shutil
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, cast

from orchestrator.contracts import SelfPlayRecord
from orchestrator.registry import (
    _repo_root,
    current_version,
    list_versions,
)

_log = logging.getLogger(__name__)

__all__ = [
    "CompositionResult",
    "FitnessResult",
    "Improvement",
    "RegressionResult",
    "apply_improvement",
    "generate_pool",
    "run_composition_eval",
    "run_fitness_eval",
    "run_regression_eval",
]


ImprovementType = Literal["training", "dev"]


@dataclass(frozen=True)
class Improvement:
    """A proposed change to try in one evolve generation.

    Fields
    ------
    rank:
        Ordering hint emitted by the advisor (1 = best).
    title:
        Short human-readable label.
    type:
        ``"training"`` for data-file patches (reward rules, hyperparams);
        ``"dev"`` for code changes applied by a sub-agent.
    description:
        Long-form rationale.
    principle_ids:
        Opaque IDs referencing the principles / lessons cited by the advisor.
    expected_impact:
        Free-text prediction of effect (human-readable).
    concrete_change:
        For ``training`` imps, a JSON-encoded patch. For ``dev`` imps, a
        free-text instruction forwarded to the ``dev_apply_fn``.
    files_touched:
        Optional list of repo-relative file paths the imp will modify.
        Used by :func:`_orthogonality_conflicts` to detect proposals that
        would step on each other during the composition phase. Advisory
        only — missing values fall back to regex extraction from
        ``concrete_change``. Defaults to an empty list.
    """

    rank: int
    title: str
    type: ImprovementType
    description: str
    principle_ids: list[str]
    expected_impact: str
    concrete_change: str
    files_touched: list[str] = field(default_factory=list)

    def to_json(self) -> str:
        return json.dumps(dataclasses.asdict(self))

    @classmethod
    def from_json(cls, data: str | bytes) -> Improvement:
        payload = json.loads(data)
        payload.setdefault("files_touched", [])
        return cls(**payload)


# ---------------------------------------------------------------------------
# Phase-result dataclasses
# ---------------------------------------------------------------------------


FitnessBucket = Literal["pass", "close", "fail"]


@dataclass(frozen=True)
class FitnessResult:
    """Outcome of :func:`run_fitness_eval` — one imp vs parent.

    ``bucket`` classifies the outcome for the caller's pool-state update:
    ``pass`` (promotion-eligible), ``close`` (resurrection-eligible),
    ``fail`` (evict).
    """

    parent: str
    candidate: str
    imp: Improvement
    record: list[SelfPlayRecord]
    wins_candidate: int
    wins_parent: int
    games: int
    bucket: FitnessBucket
    reason: str


@dataclass(frozen=True)
class CompositionResult:
    """Outcome of :func:`run_composition_eval` — stacked imps vs parent.

    ``stacked_imps`` is the ordered list of imps that were applied to the
    single scratch candidate. A one-element list is the top-1 fallback
    after a full-stack composition fail.

    On ``promoted=True`` the scratch dir has been snapshotted into
    ``promoted_version`` (``vN``) and the scratch is rmtree'd. The pointer
    at ``bots/current/current.txt`` has been updated to ``vN``.

    On ``promoted=False`` the scratch dir is rmtree'd and the pointer is
    restored to ``parent``.
    """

    parent: str
    candidate: str
    stacked_imps: list[Improvement]
    record: list[SelfPlayRecord]
    wins_candidate: int
    wins_parent: int
    games: int
    promoted: bool
    promoted_version: str | None
    reason: str


@dataclass(frozen=True)
class RegressionResult:
    """Outcome of :func:`run_regression_eval` — new parent vs prior parent.

    ``rolled_back=True`` means the new parent failed the majority gate.
    The primitive itself only flips ``bots/current/current.txt`` back to
    ``prior_parent``; the caller (``scripts/evolve.py``) is responsible
    for the ``git revert`` of the promote commit.
    """

    new_parent: str
    prior_parent: str
    record: list[SelfPlayRecord]
    wins_new: int
    wins_prior: int
    games: int
    rolled_back: bool
    reason: str


# ---------------------------------------------------------------------------
# apply_improvement
# ---------------------------------------------------------------------------


def _patch_training_file(version_dir: Path, concrete_change: str) -> None:
    """Apply a training-type ``concrete_change`` patch to a version data file.

    ``concrete_change`` must be valid JSON shaped like::

        {"file": "reward_rules.json", "patch": {"key": value, ...}}

    The target file must already exist under ``version_dir/data/``. Top-level
    keys in ``patch`` overwrite matching keys in the target JSON; non-listed
    keys are preserved.
    """
    try:
        payload = json.loads(concrete_change)
    except json.JSONDecodeError as exc:
        raise ValueError(
            f"training imp concrete_change is not valid JSON: {exc}"
        ) from exc

    if not isinstance(payload, dict):
        raise ValueError(
            "training imp concrete_change must decode to a JSON object with "
            "'file' and 'patch' keys"
        )

    filename = payload.get("file")
    patch = payload.get("patch")
    if not isinstance(filename, str) or not filename:
        raise ValueError(
            "training imp concrete_change missing/invalid 'file' key "
            "(expected non-empty string)"
        )
    if not isinstance(patch, dict):
        raise ValueError(
            "training imp concrete_change missing/invalid 'patch' key "
            "(expected JSON object)"
        )

    target = version_dir / "data" / filename
    if not target.is_file():
        raise FileNotFoundError(
            f"target data file does not exist: {target}"
        )

    existing: dict[str, Any] = json.loads(target.read_text(encoding="utf-8"))
    if not isinstance(existing, dict):
        raise ValueError(
            f"target data file {target} must contain a JSON object at the top "
            "level; got a non-object value"
        )

    for key, value in patch.items():
        existing[key] = value

    target.write_text(
        json.dumps(existing, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def apply_improvement(
    version_dir: Path,
    imp: Improvement,
    *,
    dev_apply_fn: Callable[[Path, Improvement], None] | None = None,
) -> None:
    """Apply one :class:`Improvement` to a snapshotted version directory.

    For ``imp.type == "training"`` the ``concrete_change`` field is parsed
    as a JSON patch (see :func:`_patch_training_file`) and applied in place
    to the named data file under ``version_dir/data/``.

    For ``imp.type == "dev"`` the call is dispatched to *dev_apply_fn*
    which receives ``(version_dir, imp)``. Unit tests inject a mock here;
    production wires in the sub-agent spawner from
    :mod:`orchestrator.evolve_dev_apply`.
    """
    if imp.type == "training":
        _patch_training_file(version_dir, imp.concrete_change)
        return

    if imp.type == "dev":
        if dev_apply_fn is None:
            raise NotImplementedError(
                "dev-type improvements require a dev_apply_fn; none was "
                "provided. Inject a sub-agent spawner (or a mock in tests)."
            )
        dev_apply_fn(version_dir, imp)
        return

    raise ValueError(f"unknown improvement type: {imp.type!r}")


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _default_candidate_namer() -> str:
    """Generate a UUID-based candidate name, e.g. ``cand_ab12cd34``."""
    return f"cand_{uuid.uuid4().hex[:8]}"


def _resolve_candidate_name(
    namer: Callable[[], str],
) -> str:
    """Pick a candidate name that doesn't collide with registry entries.

    Retries once on collision; raises :class:`RuntimeError` on a second
    collision so a stuck namer cannot loop forever.
    """
    last_name = ""
    for attempt in (1, 2):
        last_name = namer()
        if last_name not in set(list_versions()):
            return last_name
        _log.info(
            "candidate name collision (attempt %d): %s already in registry; "
            "retrying",
            attempt,
            last_name,
        )
    raise RuntimeError(
        f"candidate_namer produced colliding names twice; last attempt was "
        f"{last_name!r}. Registry already contains it."
    )


def _restore_pointer(parent_name: str) -> None:
    """Write ``bots/current/current.txt`` back to *parent_name*."""
    pointer = _repo_root() / "bots" / "current" / "current.txt"
    pointer.write_text(parent_name, encoding="utf-8")


def _rewrite_manifest_parent(version_dir: Path, parent_name: str) -> None:
    """Rewrite the ``parent`` field in ``version_dir/manifest.json``.

    ``snapshot_current`` records the immediate source — in the promote
    flow that is the scratch ``cand_*`` dir, not the real parent. We
    overwrite post-snapshot so lineage is readable after the scratch is
    rmtree'd. Missing/malformed manifests are logged and left alone.
    """
    manifest_path = version_dir / "manifest.json"
    try:
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        _log.warning(
            "could not read %s after promotion; skipping parent rewrite",
            manifest_path,
            exc_info=True,
        )
        return
    payload["parent"] = parent_name
    try:
        manifest_path.write_text(
            json.dumps(payload, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    except OSError:
        _log.warning(
            "could not write %s after promotion; lineage may be stale",
            manifest_path,
            exc_info=True,
        )


def _count_wins(records: list[SelfPlayRecord], a: str, b: str) -> tuple[int, int]:
    """Count wins for *a* and *b* across *records*.

    Records with ``winner=None`` (draws / crashes) are excluded from both
    tallies.
    """
    wins_a = 0
    wins_b = 0
    for rec in records:
        if rec.winner == a:
            wins_a += 1
        elif rec.winner == b:
            wins_b += 1
    return wins_a, wins_b


def _safe_rmtree(path: Path) -> None:
    """Delete *path* recursively, logging at WARNING on failure."""
    if not path.exists():
        return
    try:
        shutil.rmtree(path, ignore_errors=False)
    except OSError:
        _log.warning(
            "failed to clean up candidate directory %s; leaving on disk",
            path,
            exc_info=True,
        )


def _safe_emit(
    cb: Callable[[dict[str, Any]], None], event: dict[str, Any]
) -> None:
    """Invoke *cb* with *event*, swallowing any exception it raises.

    Progress callbacks are strictly cosmetic — a broken dashboard writer
    must never abort a phase primitive.
    """
    try:
        cb(event)
    except Exception:
        _log.warning(
            "evolve progress callback raised (%s); continuing",
            event.get("type"),
            exc_info=True,
        )


def _fitness_bucket(wins: int, games: int) -> FitnessBucket:
    """Classify a fitness outcome into pass/close/fail.

    - ``pass``: strict majority (``wins >= games // 2 + 1``).
    - ``close``: one win short of majority (``wins == games // 2``).
    - ``fail``: anything lower.

    For the design's default ``games=5`` this yields pass@>=3, close@2,
    fail@<=1.
    """
    pass_threshold = games // 2 + 1
    if wins >= pass_threshold:
        return "pass"
    if wins == pass_threshold - 1:
        return "close"
    return "fail"


# ---------------------------------------------------------------------------
# run_fitness_eval
# ---------------------------------------------------------------------------


def run_fitness_eval(
    parent: str,
    imp: Improvement,
    *,
    games: int = 5,
    map_name: str = "Simple64",
    game_time_limit: int = 1800,
    hard_timeout: float = 2700.0,
    run_batch_fn: Callable[..., list[SelfPlayRecord]] | None = None,
    dev_apply_fn: Callable[[Path, Improvement], None] | None = None,
    candidate_namer: Callable[[], str] | None = None,
    on_event: Callable[[dict[str, Any]], None] | None = None,
) -> FitnessResult:
    """Run one imp-vs-parent fitness evaluation.

    1. Snapshot the parent to a scratch ``cand_*`` dir.
    2. Apply *imp* to the scratch dir.
    3. Play ``games`` candidate-vs-parent games.
    4. Classify the win count into pass / close / fail.
    5. Rmtree the scratch dir (always — the composition phase re-snapshots
       from parent so nothing downstream needs this candidate on disk).
    6. Restore ``bots/current/current.txt`` to *parent*.

    Progress events emitted on *on_event* (if provided):
      - ``{"type": "fitness_start", "candidate", "imp_title", "total"}``
      - ``{"type": "fitness_game_end", "wins_cand", "wins_parent", "games_played"}``
    """
    if run_batch_fn is None:
        from orchestrator import selfplay

        run_batch_fn = selfplay.run_batch

    if candidate_namer is None:
        candidate_namer = _default_candidate_namer

    live_parent = current_version()
    if live_parent != parent:
        raise ValueError(
            f"parent={parent!r} does not match current_version()={live_parent!r}; "
            "refusing to snapshot from a pointer that drifted since the caller "
            "decided."
        )

    cand_name = _resolve_candidate_name(candidate_namer)

    from orchestrator import snapshot as _snapshot_mod

    cand_dir: Path | None = None
    try:
        cand_dir = _snapshot_mod.snapshot_current(cand_name)
        _restore_pointer(parent)
        apply_improvement(cand_dir, imp, dev_apply_fn=dev_apply_fn)

        _log.info(
            "fitness: %s (%s) vs parent %s (%d games)",
            cand_name,
            imp.title,
            parent,
            games,
        )
        if on_event is not None:
            _safe_emit(
                on_event,
                {
                    "type": "fitness_start",
                    "candidate": cand_name,
                    "imp_title": imp.title,
                    "parent": parent,
                    "total": games,
                },
            )

        batch_kwargs: dict[str, Any] = {
            "game_time_limit": game_time_limit,
            "hard_timeout": hard_timeout,
        }
        if on_event is not None:
            live = [0, 0]  # [wins_cand, wins_parent]

            def _on_game_end(record: SelfPlayRecord) -> None:
                if record.winner == cand_name:
                    live[0] += 1
                elif record.winner == parent:
                    live[1] += 1
                _safe_emit(
                    on_event,
                    {
                        "type": "fitness_game_end",
                        "wins_cand": live[0],
                        "wins_parent": live[1],
                    },
                )

            batch_kwargs["on_game_end"] = _on_game_end

        record = run_batch_fn(
            cand_name,
            parent,
            games,
            map_name,
            **batch_kwargs,
        )
        wins_cand, wins_parent = _count_wins(record, cand_name, parent)
        bucket = _fitness_bucket(wins_cand, games)
        reason = (
            f"fitness {bucket}: {cand_name} ({imp.title!r}) "
            f"{wins_cand}-{wins_parent} vs parent {parent} over {games} games"
        )
        _log.info("fitness outcome: %s", reason)
        return FitnessResult(
            parent=parent,
            candidate=cand_name,
            imp=imp,
            record=record,
            wins_candidate=wins_cand,
            wins_parent=wins_parent,
            games=games,
            bucket=bucket,
            reason=reason,
        )
    finally:
        # Scratch is always discarded. Composition re-snapshots from parent.
        if cand_dir is not None and cand_dir.exists():
            _safe_rmtree(cand_dir)
        _restore_pointer(parent)


# ---------------------------------------------------------------------------
# run_composition_eval
# ---------------------------------------------------------------------------


def run_composition_eval(
    parent: str,
    imps: list[Improvement],
    *,
    games: int = 5,
    map_name: str = "Simple64",
    game_time_limit: int = 1800,
    hard_timeout: float = 2700.0,
    run_batch_fn: Callable[..., list[SelfPlayRecord]] | None = None,
    dev_apply_fn: Callable[[Path, Improvement], None] | None = None,
    candidate_namer: Callable[[], str] | None = None,
    on_event: Callable[[dict[str, Any]], None] | None = None,
) -> CompositionResult:
    """Stack *imps* onto one scratch snapshot and test vs parent.

    Used for both the full-stack phase (all fitness-pass imps) AND the
    top-1 fallback (single-element list) after a full-stack composition
    fails. Pass threshold is strict-majority of *games*.

    On pass:
        - Pointer is flipped to the scratch candidate.
        - ``snapshot_current()`` creates a permanent ``vN+1``.
        - Manifest's parent field is rewritten to *parent* (not the scratch).
        - Scratch dir is rmtree'd.
        - Pointer now points at the new ``vN+1``.

    On fail:
        - Scratch dir is rmtree'd.
        - Pointer is restored to *parent*.

    Progress events:
      - ``{"type": "composition_start", "candidate", "stacked_titles", "total"}``
      - ``{"type": "composition_game_end", "wins_cand", "wins_parent", "games_played"}``
    """
    if not imps:
        raise ValueError("run_composition_eval requires at least one improvement")

    if run_batch_fn is None:
        from orchestrator import selfplay

        run_batch_fn = selfplay.run_batch

    if candidate_namer is None:
        candidate_namer = _default_candidate_namer

    live_parent = current_version()
    if live_parent != parent:
        raise ValueError(
            f"parent={parent!r} does not match current_version()={live_parent!r}; "
            "refusing to snapshot from a pointer that drifted since the caller "
            "decided."
        )

    cand_name = _resolve_candidate_name(candidate_namer)

    from orchestrator import snapshot as _snapshot_mod

    cand_dir: Path | None = None
    try:
        cand_dir = _snapshot_mod.snapshot_current(cand_name)
        _restore_pointer(parent)
        for imp in imps:
            apply_improvement(cand_dir, imp, dev_apply_fn=dev_apply_fn)

        stacked_titles = [imp.title for imp in imps]
        _log.info(
            "composition: %s (%d stacked imps: %s) vs parent %s (%d games)",
            cand_name,
            len(imps),
            stacked_titles,
            parent,
            games,
        )
        if on_event is not None:
            _safe_emit(
                on_event,
                {
                    "type": "composition_start",
                    "candidate": cand_name,
                    "stacked_titles": stacked_titles,
                    "parent": parent,
                    "total": games,
                },
            )

        batch_kwargs: dict[str, Any] = {
            "game_time_limit": game_time_limit,
            "hard_timeout": hard_timeout,
        }
        if on_event is not None:
            live = [0, 0]  # [wins_cand, wins_parent]

            def _on_game_end(record: SelfPlayRecord) -> None:
                if record.winner == cand_name:
                    live[0] += 1
                elif record.winner == parent:
                    live[1] += 1
                _safe_emit(
                    on_event,
                    {
                        "type": "composition_game_end",
                        "wins_cand": live[0],
                        "wins_parent": live[1],
                    },
                )

            batch_kwargs["on_game_end"] = _on_game_end

        record = run_batch_fn(
            cand_name,
            parent,
            games,
            map_name,
            **batch_kwargs,
        )
        wins_cand, wins_parent = _count_wins(record, cand_name, parent)
        needed = games // 2 + 1

        if wins_cand >= needed:
            # Promote: flip pointer to scratch, snapshot to vN+1, rewrite
            # manifest.parent, rmtree scratch.
            _restore_pointer(cand_name)
            new_version_dir = _snapshot_mod.snapshot_current()
            new_version = new_version_dir.name
            _rewrite_manifest_parent(new_version_dir, parent)
            _safe_rmtree(cand_dir)
            reason = (
                f"composition pass: stacked_parent ({new_version}, was "
                f"{cand_name}; {len(imps)} imps) beat parent {parent} "
                f"{wins_cand}-{wins_parent}"
            )
            _log.info("composition outcome: %s", reason)
            return CompositionResult(
                parent=parent,
                candidate=cand_name,
                stacked_imps=list(imps),
                record=record,
                wins_candidate=wins_cand,
                wins_parent=wins_parent,
                games=games,
                promoted=True,
                promoted_version=new_version,
                reason=reason,
            )

        _safe_rmtree(cand_dir)
        _restore_pointer(parent)
        reason = (
            f"composition fail: stacked_parent ({cand_name}; {len(imps)} "
            f"imps) lost to parent {parent} {wins_cand}-{wins_parent}"
        )
        _log.info("composition outcome: %s", reason)
        return CompositionResult(
            parent=parent,
            candidate=cand_name,
            stacked_imps=list(imps),
            record=record,
            wins_candidate=wins_cand,
            wins_parent=wins_parent,
            games=games,
            promoted=False,
            promoted_version=None,
            reason=reason,
        )
    except BaseException:
        # Defensive cleanup on apply / run_batch / snapshot failures.
        if cand_dir is not None and cand_dir.exists():
            _safe_rmtree(cand_dir)
        _restore_pointer(parent)
        raise


# ---------------------------------------------------------------------------
# run_regression_eval
# ---------------------------------------------------------------------------


def run_regression_eval(
    new_parent: str,
    prior_parent: str,
    *,
    games: int = 5,
    map_name: str = "Simple64",
    game_time_limit: int = 1800,
    hard_timeout: float = 2700.0,
    run_batch_fn: Callable[..., list[SelfPlayRecord]] | None = None,
    on_event: Callable[[dict[str, Any]], None] | None = None,
) -> RegressionResult:
    """Play *new_parent* vs *prior_parent* for *games* games.

    No snapshots — both versions are already on disk. On regression (new
    parent fails the strict-majority gate), the pointer is restored to
    *prior_parent* and ``rolled_back=True`` is returned; the caller is
    responsible for the git revert of the promote commit.

    Progress events:
      - ``{"type": "regression_start", "new_parent", "prior_parent", "total"}``
      - ``{"type": "regression_game_end", "wins_new", "wins_prior", "games_played"}``
    """
    if run_batch_fn is None:
        from orchestrator import selfplay

        run_batch_fn = selfplay.run_batch

    if new_parent == prior_parent:
        raise ValueError(
            f"regression check requires distinct new/prior parents; got "
            f"{new_parent!r} for both."
        )

    _log.info(
        "regression: %s (new) vs %s (prior) (%d games)",
        new_parent,
        prior_parent,
        games,
    )
    if on_event is not None:
        _safe_emit(
            on_event,
            {
                "type": "regression_start",
                "new_parent": new_parent,
                "prior_parent": prior_parent,
                "total": games,
            },
        )

    batch_kwargs: dict[str, Any] = {
        "game_time_limit": game_time_limit,
        "hard_timeout": hard_timeout,
    }
    if on_event is not None:
        live = [0, 0]

        def _on_game_end(record: SelfPlayRecord) -> None:
            if record.winner == new_parent:
                live[0] += 1
            elif record.winner == prior_parent:
                live[1] += 1
            _safe_emit(
                on_event,
                {
                    "type": "regression_game_end",
                    "wins_new": live[0],
                    "wins_prior": live[1],
                },
            )

        batch_kwargs["on_game_end"] = _on_game_end

    record = run_batch_fn(
        new_parent,
        prior_parent,
        games,
        map_name,
        **batch_kwargs,
    )
    wins_new, wins_prior = _count_wins(record, new_parent, prior_parent)
    needed = games // 2 + 1
    rolled_back = wins_new < needed

    if rolled_back:
        _restore_pointer(prior_parent)
        reason = (
            f"regression rollback: new {new_parent} {wins_new}-{wins_prior} "
            f"prior {prior_parent} (needed {needed}); pointer reset"
        )
    else:
        reason = (
            f"regression pass: new {new_parent} {wins_new}-{wins_prior} "
            f"prior {prior_parent}"
        )
    _log.info("regression outcome: %s", reason)
    return RegressionResult(
        new_parent=new_parent,
        prior_parent=prior_parent,
        record=record,
        wins_new=wins_new,
        wins_prior=wins_prior,
        games=games,
        rolled_back=rolled_back,
        reason=reason,
    )


# ---------------------------------------------------------------------------
# generate_pool
# ---------------------------------------------------------------------------


# Fields every Improvement item in the Claude JSON response MUST have.
_REQUIRED_IMP_FIELDS: tuple[str, ...] = (
    "rank",
    "title",
    "type",
    "description",
    "principle_ids",
    "expected_impact",
    "concrete_change",
)

# Optional fields — accepted if present, silently filled with defaults if not.
_OPTIONAL_IMP_FIELDS: frozenset[str] = frozenset({"files_touched"})

# Full set of allowed keys (required + optional). Anything else raises.
_ALLOWED_IMP_FIELDS: frozenset[str] = (
    frozenset(_REQUIRED_IMP_FIELDS) | _OPTIONAL_IMP_FIELDS
)

# Depth cap for the source-tree listing pasted into the prompt.
_SOURCE_TREE_MAX_DEPTH = 5

# Directory names to exclude from the source-tree listing (noise).
_SOURCE_TREE_EXCLUDE: frozenset[str] = frozenset(
    {"__pycache__", ".pytest_cache", "data", ".mypy_cache", ".ruff_cache"}
)


def _strip_markdown_fences(raw: str) -> str:
    """Remove triple-backtick fences AND prose preamble/postamble.

    Opus sometimes prefixes responses with a preamble even under strict
    instruction. Extract from the first ``[``/``{`` to the matching last
    ``]``/``}`` so the payload is feedable to ``json.loads`` directly.
    """
    cleaned = raw.strip()

    if "```" in cleaned:
        parts = cleaned.split("```")
        for part in parts:
            candidate = part.strip()
            if candidate.startswith("json"):
                candidate = candidate[4:].strip()
            if candidate.startswith("[") or candidate.startswith("{"):
                cleaned = candidate
                break

    if cleaned and cleaned[0] not in "[{":
        i_arr = cleaned.find("[")
        i_obj = cleaned.find("{")
        candidates = [p for p in (i_arr, i_obj) if p >= 0]
        if candidates:
            start = min(candidates)
            opener = cleaned[start]
            closer = "]" if opener == "[" else "}"
            end = cleaned.rfind(closer)
            if end > start:
                cleaned = cleaned[start : end + 1]
    return cleaned


def _validate_improvement_item(item: Any, index: int) -> Improvement:
    """Validate one raw Claude JSON item against the Improvement schema.

    Unknown keys (outside required+optional) raise ``ValueError``. Missing
    optional fields get default values.
    """
    if not isinstance(item, dict):
        raise ValueError(
            f"improvement[{index}] must be a JSON object; got {type(item).__name__}"
        )

    missing = [f for f in _REQUIRED_IMP_FIELDS if f not in item]
    if missing:
        raise ValueError(
            f"improvement[{index}] missing required field(s): {missing!r}"
        )

    unknown = sorted(set(item.keys()) - _ALLOWED_IMP_FIELDS)
    if unknown:
        raise ValueError(
            f"improvement[{index}] has unknown field(s): {unknown!r}; "
            f"allowed fields are {sorted(_ALLOWED_IMP_FIELDS)!r}"
        )

    rank = item["rank"]
    if not isinstance(rank, int) or isinstance(rank, bool) or rank < 1:
        raise ValueError(
            f"improvement[{index}].rank must be a positive int; got {rank!r}"
        )

    for fname in ("title", "description", "expected_impact"):
        val = item[fname]
        if not isinstance(val, str):
            raise ValueError(
                f"improvement[{index}].{fname} must be a string; got "
                f"{type(val).__name__}"
            )

    type_val = item["type"]
    if type_val not in ("training", "dev"):
        raise ValueError(
            f"improvement[{index}].type must be 'training' or 'dev'; got "
            f"{type_val!r}"
        )

    principle_ids = item["principle_ids"]
    if not isinstance(principle_ids, list) or not all(
        isinstance(p, str) for p in principle_ids
    ):
        raise ValueError(
            f"improvement[{index}].principle_ids must be a list of strings; "
            f"got {principle_ids!r}"
        )

    concrete = item["concrete_change"]
    if isinstance(concrete, dict):
        concrete = json.dumps(concrete)
    elif not isinstance(concrete, str):
        raise ValueError(
            f"improvement[{index}].concrete_change must be a string or JSON "
            f"object; got {type(concrete).__name__}"
        )

    files_touched_raw = item.get("files_touched", [])
    if not isinstance(files_touched_raw, list) or not all(
        isinstance(p, str) for p in files_touched_raw
    ):
        raise ValueError(
            f"improvement[{index}].files_touched must be a list of strings; "
            f"got {files_touched_raw!r}"
        )

    return Improvement(
        rank=rank,
        title=item["title"],
        type=cast(ImprovementType, type_val),
        description=item["description"],
        principle_ids=list(principle_ids),
        expected_impact=item["expected_impact"],
        concrete_change=concrete,
        files_touched=list(files_touched_raw),
    )


def _filter_dev_only(pool: list[Improvement]) -> list[Improvement]:
    """Drop any ``training``-type improvements from *pool*.

    Evolve's head-to-head games only measure behavioural differences.
    Training-type patches only affect PPO's reward signal during training
    and are irrelevant during a non-training game, so they're handled
    out-of-band by the post-evolve PPO step.
    """
    kept: list[Improvement] = []
    for imp in pool:
        if imp.type == "training":
            _log.info(
                "generate_pool: dropping training-type imp %r "
                "(rank=%d) — pool is dev-only",
                imp.title,
                imp.rank,
            )
            continue
        kept.append(imp)
    return kept


def _parse_claude_pool(raw: str) -> list[Improvement]:
    """Parse Claude's response string into a list of Improvement objects."""
    cleaned = _strip_markdown_fences(raw)
    try:
        parsed: Any = json.loads(cleaned)
    except json.JSONDecodeError as exc:
        snippet = cleaned[:500]
        raise ValueError(
            f"Claude response is not valid JSON: {exc.msg} at pos {exc.pos}. "
            f"Response snippet (first 500 chars): {snippet!r}"
        ) from exc

    if not isinstance(parsed, list):
        raise ValueError(
            f"Claude response must be a JSON array at top level; got "
            f"{type(parsed).__name__}"
        )

    return [_validate_improvement_item(item, i) for i, item in enumerate(parsed)]


# Regex to extract .py file paths from free-text concrete_change, used as a
# fallback when an imp has no files_touched field. Matches common forms
# like ``bots/v0/foo.py`` and ``foo.py``; intentionally permissive so
# short mentions still register.
_FILE_EXTRACT_REGEX = re.compile(r"\b([A-Za-z0-9_/\\.\-]+\.py)\b")


def _extract_files_from_text(text: str) -> list[str]:
    """Best-effort extraction of ``.py`` paths from free-text."""
    return _FILE_EXTRACT_REGEX.findall(text)


def _normalize_file_token(raw: str) -> str:
    """Normalise a file path for orthogonality comparison.

    Collapses backslashes to forward slashes and strips any leading
    ``./``. Does NOT attempt to canonicalise parent dirs — the advisor
    refers to files by repo-relative paths, so lexical dedup is enough.
    """
    t = raw.replace("\\", "/").lstrip("./")
    return t.strip()


def _orthogonality_conflicts(
    pool: list[Improvement],
) -> dict[str, list[int]]:
    """Return ``{file_path: [imp_index, ...]}`` for files shared by ≥2 imps.

    Empty result = pool is orthogonal by the files-touched criterion.
    For imps with no ``files_touched``, the regex-extracted file list from
    ``concrete_change`` is used instead.
    """
    file_to_imps: dict[str, list[int]] = {}
    for i, imp in enumerate(pool):
        sources: list[str]
        if imp.files_touched:
            sources = imp.files_touched
        else:
            sources = _extract_files_from_text(imp.concrete_change)
        seen: set[str] = set()
        for raw in sources:
            norm = _normalize_file_token(raw)
            if not norm or norm in seen:
                continue
            seen.add(norm)
            file_to_imps.setdefault(norm, []).append(i)
    return {f: idxs for f, idxs in file_to_imps.items() if len(idxs) >= 2}


def _format_conflict_retry_prefix(
    conflicts: dict[str, list[int]],
    pool: list[Improvement],
) -> str:
    """Build a retry-prompt prefix listing the specific file conflicts."""
    lines = [
        "The previous response violated the orthogonality rule. The following",
        "files are touched by MORE THAN ONE improvement, which would cause",
        "merge conflicts in the composition phase:",
        "",
    ]
    for fpath, idxs in sorted(conflicts.items()):
        titles = ", ".join(
            f"#{pool[i].rank} {pool[i].title!r}" for i in idxs
        )
        lines.append(f"  - {fpath}: {titles}")
    lines.extend(
        [
            "",
            "Regenerate the pool so each file is touched by AT MOST ONE",
            "improvement. If two ideas naturally target the same file, pick",
            "the stronger one and propose a different area for the other.",
            "Return the full schema (all items) with the orthogonality",
            "constraint satisfied. No prose, no markdown fences.",
            "",
        ]
    )
    return "\n".join(lines) + "\n"


def _list_source_tree(bots_dir: Path, parent: str) -> list[str]:
    """List ``.py`` files under ``bots/<parent>/`` as repo-relative paths."""
    version_root = bots_dir / parent
    if not version_root.is_dir():
        return []

    files: list[str] = []
    root_parts = len(version_root.parts)
    for dirpath, dirnames, filenames in os.walk(version_root):
        dirnames[:] = [d for d in dirnames if d not in _SOURCE_TREE_EXCLUDE]

        cur = Path(dirpath)
        depth = len(cur.parts) - root_parts
        if depth > _SOURCE_TREE_MAX_DEPTH:
            dirnames[:] = []
            continue
        if depth == _SOURCE_TREE_MAX_DEPTH:
            dirnames[:] = []

        for name in filenames:
            if not name.endswith(".py"):
                continue
            rel = (cur / name).relative_to(bots_dir.parent)
            files.append(rel.as_posix())

    files.sort()
    return files


def _read_log_tails(
    logs_dir: Path, parent: str, *, max_bytes: int = 4000
) -> str:
    """Read tails of matching ``selfplay_<parent>_*.log`` files."""
    if not logs_dir.is_dir():
        return "(no logs/ directory — fresh project or mocked run)"

    matches = sorted(logs_dir.glob(f"selfplay_{parent}_*.log"))
    if not matches:
        return f"(no selfplay_{parent}_*.log files in {logs_dir})"

    chunks: list[str] = []
    for log_path in matches:
        try:
            raw = log_path.read_bytes()
        except OSError as exc:
            _log.warning("failed to read %s: %s", log_path, exc)
            continue
        tail = raw[-max_bytes:].decode("utf-8", errors="replace")
        chunks.append(f"--- {log_path.name} (tail) ---\n{tail}")
    if not chunks:
        return f"(no readable selfplay logs for {parent})"
    return "\n\n".join(chunks)


def _summarize_records(
    records: list[SelfPlayRecord], parent: str
) -> dict[str, Any]:
    """Compute winner counts / durations / crash counts for the prompt."""
    wins_p1 = sum(1 for r in records if r.winner == parent and not r.seat_swap)
    wins_p2 = sum(1 for r in records if r.winner == parent and r.seat_swap)
    total_parent_wins = sum(1 for r in records if r.winner == parent)
    draws = sum(1 for r in records if r.winner is None and r.error is None)
    crashes = sum(1 for r in records if r.error is not None)
    durations = [r.duration_s for r in records if r.duration_s > 0]
    avg_duration = sum(durations) / len(durations) if durations else 0.0
    return {
        "games": len(records),
        "parent_wins_p1_seat": wins_p1,
        "parent_wins_p2_seat": wins_p2,
        "parent_wins_total": total_parent_wins,
        "draws": draws,
        "crashes": crashes,
        "avg_duration_s": round(avg_duration, 1),
    }


_PROMPT_HEADER = """You are the improvement advisor for Alpha4Gate, a Protoss \
StarCraft II bot. Your job: propose a pool of {pool_size} candidate \
improvements that the evolve loop will fitness-test individually and then \
STACK TOGETHER on top of the parent for a single composition test.

Parent version: {parent}
Map: {map_name}
Mirror games run (parent vs parent): {mirror_games}

## Mirror-game summary

{summary_json}

## Self-play log tails

{log_tails}

## Parent source tree (.py files only)

{source_tree}

## Guiding principles (full)

{principles}

## Output schema

Return a JSON array of EXACTLY {pool_size} improvement objects, ordered by \
expected_impact descending (rank 1 = most impactful). Each object MUST have \
these seven required fields AND one optional-but-strongly-encouraged field:

  - rank: positive int (1 is best)
  - title: short human label (string)
  - type: MUST be "dev". (Training-type reward-rule / hyperparameter \
improvements are handled by a separate post-evolve PPO training step, so \
they don't belong in the evolve pool.)
  - description: long-form rationale (string)
  - principle_ids: list of principle ID strings from the guiding principles \
above
  - expected_impact: free-text prediction (string)
  - concrete_change: free-text instruction for a sub-agent that edits \
Python source under bots/{parent}/. Name the file and the specific \
function or line the change targets. Keep it minimal and implementable.
  - files_touched (OPTIONAL but strongly encouraged): list of repo-relative \
file path strings that the improvement will modify \
(e.g. ["bots/{parent}/chrono_boost.py", "bots/{parent}/bot.py"]). Used \
to detect overlap between sibling improvements.

## Orthogonality constraint (hard requirement)

The {pool_size} improvements will be STACKED together on one snapshot for a \
composition test. They MUST be orthogonal: **no two items may list the same \
file in `files_touched`**. If two ideas naturally target the same file, \
pick the stronger one and propose a different area for the other slot. \
Include `files_touched` on every item so the caller can mechanically verify \
orthogonality before running games.

## Constraints

- type MUST be exactly "dev" on every item. Do NOT emit "training" items; \
they will be dropped by the caller and force a retry.
- Dev improvements edit Python source under bots/{parent}/ only. They do \
NOT touch data/ files (reward_rules.json, hyperparams.json) and they do \
NOT edit files outside bots/{parent}/.
- Every improvement must type-check under `mypy --strict` and lint-clean \
under `ruff check`. If a change would require loosening either (e.g. via \
`# type: ignore` or a new `Any` cast), propose a different change instead.
- Every principle_ids entry should cite a real section or rule from the \
guiding-principles document above.
- Return ONLY the JSON array. No prose, no markdown fences.
"""


_PROMPT_RETRY_PREFIX = (
    "The previous response returned {got} items but EXACTLY {want} are "
    "required. Return the same schema, but with exactly {want} items this "
    "time. No prose, no markdown fences.\n\n"
)


def _build_prompt(
    *,
    parent: str,
    pool_size: int,
    mirror_games: int,
    map_name: str,
    summary: dict[str, Any],
    log_tails: str,
    source_tree: list[str],
    principles: str,
) -> str:
    """Assemble the Claude prompt from the collected context blocks."""
    tree_text = (
        "\n".join(f"- {p}" for p in source_tree)
        if source_tree
        else f"(no .py files found under bots/{parent}/ — empty or missing)"
    )
    return _PROMPT_HEADER.format(
        parent=parent,
        pool_size=pool_size,
        mirror_games=mirror_games,
        map_name=map_name,
        summary_json=json.dumps(summary, indent=2),
        log_tails=log_tails,
        source_tree=tree_text,
        principles=principles,
    )


_CLAUDE_POOL_MODEL_DEFAULT = "opus"


def _default_claude_fn(prompt: str, *, model: str | None = None) -> str:
    """Production Claude invocation. Shells out to the ``claude`` CLI."""
    import subprocess  # noqa: PLC0415 — lazy-imported to match the pattern

    resolved_model = (
        model
        or os.getenv("EVOLVE_POOL_MODEL")
        or _CLAUDE_POOL_MODEL_DEFAULT
    )
    try:
        result = subprocess.run(
            [
                "claude",
                "-p",
                "--model",
                resolved_model,
                "--output-format",
                "text",
                "--no-session-persistence",
            ],
            input=prompt,
            capture_output=True,
            text=True,
            check=False,
            timeout=600,
        )
    except FileNotFoundError as e:
        raise RuntimeError(
            "claude CLI not found on PATH; install Claude Code or inject a "
            "claude_fn into generate_pool()."
        ) from e
    except subprocess.TimeoutExpired as e:
        raise RuntimeError(
            f"claude CLI timed out after {e.timeout}s generating the pool."
        ) from e
    if result.returncode != 0:
        raise RuntimeError(
            f"claude CLI failed (rc={result.returncode}): "
            f"{result.stderr.strip()[:500]}"
        )
    text = result.stdout.strip()
    if not text:
        raise RuntimeError("claude CLI returned empty output.")
    return text


def generate_pool(
    parent: str,
    *,
    mirror_games: int = 3,
    pool_size: int = 10,
    map_name: str = "Simple64",
    game_time_limit: int = 1800,
    hard_timeout: float = 2700.0,
    run_batch_fn: Callable[..., list[SelfPlayRecord]] | None = None,
    claude_fn: Callable[[str], str] | None = None,
    on_pool_gen_event: Callable[[dict[str, Any]], None] | None = None,
    skip_mirror: bool = False,
) -> list[Improvement]:
    """Generate a pool of improvements via mirror self-play + Claude advisor.

    Runs ``mirror_games`` parent-vs-parent games (unless ``skip_mirror`` is
    set — used for pool-refresh calls that already have a warm parent),
    then prompts Claude for exactly ``pool_size`` orthogonal improvements.

    If the first response is short (fewer than ``pool_size`` items after
    dev-only filtering), re-prompt once with an explicit "return exactly N"
    prefix. If the first response is full-size but contains file-overlap
    conflicts, re-prompt once with a conflict-list prefix. Only ONE retry
    total — on a second failure of either kind, return whatever Claude
    produced (truncate if over-delivered; raise if still short).

    Parameters
    ----------
    parent:
        Version name to mirror and target for improvements.
    skip_mirror:
        If True, skip the mirror-game phase and pass an empty summary to
        Claude. Used by the generation-boundary pool-refresh, where the
        mirror signal from a freshly-promoted parent is less informative
        than simply asking for more imps.
    """
    if run_batch_fn is None:
        from orchestrator import selfplay

        run_batch_fn = selfplay.run_batch
    if claude_fn is None:
        claude_fn = _default_claude_fn

    # 1. Mirror games (optional).
    records: list[SelfPlayRecord] = []
    if not skip_mirror:
        _log.info(
            "generate_pool: running %d mirror games for %s on %s",
            mirror_games,
            parent,
            map_name,
        )
        if on_pool_gen_event is not None:
            _safe_emit(
                on_pool_gen_event,
                {"type": "mirror_start", "total": mirror_games, "parent": parent},
            )
        mirror_batch_kwargs: dict[str, Any] = {
            "game_time_limit": game_time_limit,
            "hard_timeout": hard_timeout,
        }
        if on_pool_gen_event is not None:
            mirror_played = [0]

            def _on_mirror_game_end(record: SelfPlayRecord) -> None:
                mirror_played[0] += 1
                _safe_emit(
                    on_pool_gen_event,
                    {
                        "type": "mirror_game_end",
                        "games_played": mirror_played[0],
                        "total": mirror_games,
                    },
                )

            mirror_batch_kwargs["on_game_end"] = _on_mirror_game_end
        records = run_batch_fn(
            parent,
            parent,
            mirror_games,
            map_name,
            **mirror_batch_kwargs,
        )

    summary = _summarize_records(records, parent)

    repo_root = _repo_root()
    log_tails = _read_log_tails(repo_root / "logs", parent)
    source_tree = _list_source_tree(repo_root / "bots", parent)

    principles_path = (
        repo_root / "documentation" / "sc2" / "protoss" / "guiding-principles.md"
    )
    try:
        principles = principles_path.read_text(encoding="utf-8")
    except FileNotFoundError:
        principles = (
            f"(guiding-principles.md not found at {principles_path} — "
            "advisor must fall back to general SC2 Protoss knowledge)"
        )

    prompt = _build_prompt(
        parent=parent,
        pool_size=pool_size,
        mirror_games=mirror_games if not skip_mirror else 0,
        map_name=map_name,
        summary=summary,
        log_tails=log_tails,
        source_tree=source_tree,
        principles=principles,
    )
    _log.info(
        "generate_pool: calling claude_fn (prompt=%d chars) for pool of %d",
        len(prompt),
        pool_size,
    )
    if on_pool_gen_event is not None:
        _safe_emit(
            on_pool_gen_event,
            {"type": "claude_start", "pool_size": pool_size},
        )
    raw = claude_fn(prompt)
    pool = _filter_dev_only(_parse_claude_pool(raw))

    # Decide whether we need one retry — either for short-pool OR orthogonality
    # conflicts. Only the FIRST attempt can trigger a retry; a second pass is
    # best-effort (accepts whatever comes back, truncating on over-delivery
    # and raising on persistent short-delivery).
    retried = False
    if len(pool) < pool_size:
        _log.warning(
            "generate_pool: got %d dev items, expected %d; retrying once",
            len(pool),
            pool_size,
        )
        retry_prompt = (
            _PROMPT_RETRY_PREFIX.format(got=len(pool), want=pool_size) + prompt
        )
        raw_retry = claude_fn(retry_prompt)
        pool = _filter_dev_only(_parse_claude_pool(raw_retry))
        retried = True
        if len(pool) < pool_size:
            raise ValueError(
                f"Claude returned {len(pool)} dev improvements on retry; "
                f"need exactly {pool_size}. Aborting."
            )

    if not retried:
        conflicts = _orthogonality_conflicts(pool[:pool_size])
        if conflicts:
            _log.warning(
                "generate_pool: %d file conflict(s) in initial pool; "
                "retrying once with conflict list",
                len(conflicts),
            )
            retry_prompt = (
                _format_conflict_retry_prefix(conflicts, pool[:pool_size])
                + prompt
            )
            raw_retry = claude_fn(retry_prompt)
            pool = _filter_dev_only(_parse_claude_pool(raw_retry))
            if len(pool) < pool_size:
                # Second attempt regressed on count — accept what we have
                # only if it meets the count bar; otherwise raise.
                raise ValueError(
                    f"Claude returned {len(pool)} dev improvements on "
                    f"orthogonality retry; need exactly {pool_size}. "
                    "Aborting."
                )
            remaining = _orthogonality_conflicts(pool[:pool_size])
            if remaining:
                _log.warning(
                    "generate_pool: %d file conflict(s) STILL present after "
                    "retry; accepting pool anyway — composition phase will "
                    "surface the merge failure empirically",
                    len(remaining),
                )

    final_pool = pool[:pool_size]
    if on_pool_gen_event is not None:
        _safe_emit(
            on_pool_gen_event,
            {"type": "pool_ready", "pool_size": len(final_pool)},
        )
    return final_pool
