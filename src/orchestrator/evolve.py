"""Sibling-tournament (evolve) self-play round primitive.

Implements the one-round primitive for the evolve loop described in the master
plan Phase 9 / improve-bot-evolve build doc:

1. Take the current parent version.
2. Snapshot two candidates from it and apply a distinct :class:`Improvement`
   to each (``training`` -> data-file patch; ``dev`` -> sub-agent via injected
   callback).
3. Play *ab_games* between the two candidates. If tied, discard both.
4. Otherwise play *gate_games* between the AB winner and the parent. Promote
   iff the candidate wins a strict majority of the full gate_games batch —
   ties and crashes in the gate count against the candidate.

Public surface:

- :class:`Improvement` — frozen dataclass describing a proposed change.
- :class:`RoundResult` — frozen dataclass describing the outcome of one round.
- :func:`apply_improvement` — apply one :class:`Improvement` to a version dir.
- :func:`run_round` — execute one full evolve round (snapshot + apply + AB +
  parent gate + cleanup).

Design notes
------------

* ``snapshot_current`` updates ``bots/current/current.txt`` as a side effect.
  :func:`run_round` restores the pointer to *parent* between the two snapshots
  so the second candidate is snapshotted from the parent, not from candidate A.
* The round is always idempotent on ``current.txt``: at exit it either still
  points at *parent* (discard or gate failure) or at the newly-promoted
  candidate (gate pass).
* ``dev``-type improvements are dispatched to a caller-supplied
  ``dev_apply_fn``; production will inject a sub-agent spawner in a later
  step. Without one, ``dev`` imps raise :class:`NotImplementedError` so the
  caller cannot silently lose information.
* Cleanup failures (``shutil.rmtree`` errors) are logged at WARNING and do
  NOT mask the round result — the caller gets the real outcome string.
"""

from __future__ import annotations

import dataclasses
import json
import logging
import shutil
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from orchestrator.contracts import SelfPlayRecord
from orchestrator.registry import (
    _repo_root,
    current_version,
    list_versions,
)

_log = logging.getLogger(__name__)

__all__ = [
    "Improvement",
    "RoundResult",
    "apply_improvement",
    "run_round",
]


ImprovementType = Literal["training", "dev"]


@dataclass(frozen=True)
class Improvement:
    """A proposed change to try in one sibling-tournament round.

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
        For ``training`` imps, a JSON-encoded patch with shape::

            {"file": "reward_rules.json", "patch": {"some_key": new_value}}

        where ``file`` is a filename inside ``<version_dir>/data/`` and
        ``patch`` is a flat dict whose top-level keys replace matching keys
        in the target JSON. Nested patches are NOT supported — pass an
        entire nested sub-object as the value instead.

        For ``dev`` imps this is a free-text instruction forwarded to the
        ``dev_apply_fn``.
    """

    rank: int
    title: str
    type: ImprovementType
    description: str
    principle_ids: list[str]
    expected_impact: str
    concrete_change: str

    def to_json(self) -> str:
        return json.dumps(dataclasses.asdict(self))

    @classmethod
    def from_json(cls, data: str | bytes) -> Improvement:
        payload = json.loads(data)
        return cls(**payload)


@dataclass(frozen=True)
class RoundResult:
    """Outcome of one :func:`run_round` call.

    ``candidate_a`` / ``candidate_b`` are version NAMES (strings); callers
    derive paths via :func:`orchestrator.registry.get_version_dir`.
    ``winner`` is the name of the surviving candidate when
    ``promoted=True``; ``None`` otherwise (tie or gate failure).
    """

    parent: str
    candidate_a: str
    candidate_b: str
    imp_a: Improvement
    imp_b: Improvement
    ab_record: list[SelfPlayRecord]
    gate_record: list[SelfPlayRecord] | None
    winner: str | None
    promoted: bool
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

    For ``imp.type == "training"`` the ``concrete_change`` field is parsed as
    a JSON patch (see :func:`_patch_training_file`) and applied in place to
    the named data file under ``version_dir/data/``.

    For ``imp.type == "dev"`` the call is dispatched to *dev_apply_fn*
    which receives ``(version_dir, imp)`` and is responsible for whatever
    code mutation the improvement describes. Unit tests can inject a mock
    here; production will plug in a sub-agent spawner.

    Raises
    ------
    NotImplementedError
        If ``imp.type == "dev"`` and ``dev_apply_fn`` is ``None`` — the
        caller forgot to wire a handler.
    ValueError
        If a training ``concrete_change`` is malformed.
    FileNotFoundError
        If a training patch targets a file that does not exist under
        ``version_dir/data/``.
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
# run_round
# ---------------------------------------------------------------------------


def _default_candidate_namer() -> tuple[str, str]:
    """Generate two UUID-based candidate names.

    Uses a single shared prefix so the pair reads as siblings in ``ls bots/``
    output.
    """
    prefix = uuid.uuid4().hex[:8]
    return f"cand_{prefix}_a", f"cand_{prefix}_b"


def _restore_pointer(parent_name: str) -> None:
    """Write ``bots/current/current.txt`` back to *parent_name*.

    Used between the two ``snapshot_current`` calls and on any abort path.
    """
    pointer = _repo_root() / "bots" / "current" / "current.txt"
    pointer.write_text(parent_name, encoding="utf-8")


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
    """Delete *path* recursively, logging at WARNING on failure.

    Cleanup failures must NOT mask the real round result — the caller
    always gets the outcome it computed, even if a stale directory lingers.
    """
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


def _resolve_candidate_names(
    namer: Callable[[], tuple[str, str]],
) -> tuple[str, str]:
    """Pick two distinct candidate names that don't collide with registry.

    Retries once on collision; raises :class:`RuntimeError` on a second
    collision so a stuck namer cannot loop forever.

    Raises
    ------
    ValueError
        If the namer returns two identical names.
    RuntimeError
        If two successive attempts both collide with existing registry
        entries.
    """
    for attempt in (1, 2):
        name_a, name_b = namer()
        if name_a == name_b:
            raise ValueError(
                f"candidate_namer returned identical names: {name_a!r}"
            )
        existing = set(list_versions())
        if name_a in existing or name_b in existing:
            _log.info(
                "candidate name collision (attempt %d): %s or %s already "
                "in registry; retrying",
                attempt,
                name_a,
                name_b,
            )
            continue
        return name_a, name_b
    raise RuntimeError(
        f"candidate_namer produced colliding names twice; last attempt was "
        f"({name_a!r}, {name_b!r}). Registry already contains one or both."
    )


def run_round(
    parent: str,
    imp_a: Improvement,
    imp_b: Improvement,
    *,
    ab_games: int = 10,
    gate_games: int = 5,
    map_name: str = "Simple64",
    run_batch_fn: Callable[..., list[SelfPlayRecord]] | None = None,
    dev_apply_fn: Callable[[Path, Improvement], None] | None = None,
    candidate_namer: Callable[[], tuple[str, str]] | None = None,
) -> RoundResult:
    """Execute one sibling-tournament evolve round.

    See the module docstring for the full round-mechanics description. The
    returned :class:`RoundResult` captures the full audit trail (AB record,
    gate record if any, outcome string).

    Parameters
    ----------
    parent:
        Version string the two candidates are snapshotted from. Must match
        :func:`orchestrator.registry.current_version` at call time — if the
        live pointer drifts we refuse to run rather than silently snapshot
        from the wrong place.
    imp_a, imp_b:
        Improvements to apply to candidate A / candidate B respectively.
    ab_games:
        Number of games in the A-vs-B fight. Ties (including all-crash)
        discard both candidates without a parent gate.
    gate_games:
        Number of games in the winner-vs-parent gate. Winner must win a
        strict majority of the full gate_games batch (> ``gate_games // 2``)
        — ties and crashes in the gate count against the candidate.
    map_name:
        SC2 map name passed through to ``run_batch_fn``.
    run_batch_fn:
        Injected in tests. Defaults to
        :func:`orchestrator.selfplay.run_batch`. Called as
        ``run_batch_fn(p1, p2, games, map_name)``.
    dev_apply_fn:
        Forwarded to :func:`apply_improvement` for ``dev``-type imps.
    candidate_namer:
        Callable returning ``(name_a, name_b)``. Defaults to a UUID-based
        scheme. Retried once if the generated names collide with existing
        versions.

    Returns
    -------
    RoundResult
        Always returned (no raises for the round-logic itself); exceptions
        from snapshot / apply / run_batch_fn propagate to the caller.

    Raises
    ------
    ValueError
        If the supplied *parent* does not match ``current_version()``.
    """
    if run_batch_fn is None:
        # Lazy-import to match the ladder pattern and keep tests that mock
        # selfplay off the burnysc2 import path.
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

    cand_a, cand_b = _resolve_candidate_names(candidate_namer)

    # Lazy-import snapshot so tests that monkey-patch it win deterministically.
    from orchestrator import snapshot as _snapshot_mod

    cand_a_dir: Path | None = None
    cand_b_dir: Path | None = None
    try:
        cand_a_dir = _snapshot_mod.snapshot_current(cand_a)
        _restore_pointer(parent)
        apply_improvement(cand_a_dir, imp_a, dev_apply_fn=dev_apply_fn)

        cand_b_dir = _snapshot_mod.snapshot_current(cand_b)
        _restore_pointer(parent)
        apply_improvement(cand_b_dir, imp_b, dev_apply_fn=dev_apply_fn)

        _log.info(
            "evolve round: %s vs %s (%d games, parent=%s)",
            cand_a,
            cand_b,
            ab_games,
            parent,
        )
        ab_record = run_batch_fn(cand_a, cand_b, ab_games, map_name)
        ab_wins_a, ab_wins_b = _count_wins(ab_record, cand_a, cand_b)

        if ab_wins_a == ab_wins_b:
            # Tie (including 0-0 all-crash). Discard both.
            if ab_wins_a == 0 and all(r.winner is None for r in ab_record):
                reason = (
                    f"discarded: all {ab_games} A/B games crashed/drew; both "
                    "improvements consumed"
                )
            else:
                reason = (
                    f"discarded: A/B was {ab_wins_a}-{ab_wins_b} tie; both "
                    "improvements consumed"
                )
            _safe_rmtree(cand_a_dir)
            _safe_rmtree(cand_b_dir)
            _restore_pointer(parent)
            _log.info("evolve round outcome: %s", reason)
            return RoundResult(
                parent=parent,
                candidate_a=cand_a,
                candidate_b=cand_b,
                imp_a=imp_a,
                imp_b=imp_b,
                ab_record=ab_record,
                gate_record=None,
                winner=None,
                promoted=False,
                reason=reason,
            )

        ab_winner = cand_a if ab_wins_a > ab_wins_b else cand_b
        ab_loser = cand_b if ab_winner == cand_a else cand_a
        ab_loser_dir = cand_b_dir if ab_loser == cand_b else cand_a_dir

        _log.info(
            "evolve round: %s vs parent %s (%d games)",
            ab_winner,
            parent,
            gate_games,
        )
        gate_record = run_batch_fn(ab_winner, parent, gate_games, map_name)
        gate_wins_cand, gate_wins_parent = _count_wins(
            gate_record, ab_winner, parent
        )

        # Strict majority of the full game budget — ties do not count for the
        # candidate.
        needed = gate_games // 2 + 1
        if gate_wins_cand >= needed:
            # Promote the winner: replace the pointer (the version dir already
            # exists from the earlier snapshot). Clean up the loser.
            _restore_pointer(ab_winner)
            _safe_rmtree(ab_loser_dir)
            reason = (
                f"promoted: {ab_winner} beat {ab_loser} "
                f"{max(ab_wins_a, ab_wins_b)}-{min(ab_wins_a, ab_wins_b)}, "
                f"then beat parent {parent} "
                f"{gate_wins_cand}-{gate_wins_parent}"
            )
            _log.info("evolve round outcome: %s", reason)
            return RoundResult(
                parent=parent,
                candidate_a=cand_a,
                candidate_b=cand_b,
                imp_a=imp_a,
                imp_b=imp_b,
                ab_record=ab_record,
                gate_record=gate_record,
                winner=ab_winner,
                promoted=True,
                reason=reason,
            )

        # Gate failed: discard BOTH candidates, pointer stays at parent.
        _safe_rmtree(cand_a_dir)
        _safe_rmtree(cand_b_dir)
        _restore_pointer(parent)
        reason = (
            f"discarded: {ab_winner} beat {ab_loser} "
            f"{max(ab_wins_a, ab_wins_b)}-{min(ab_wins_a, ab_wins_b)}, "
            f"lost to parent {parent} {gate_wins_cand}-{gate_wins_parent}"
        )
        _log.info("evolve round outcome: %s", reason)
        return RoundResult(
            parent=parent,
            candidate_a=cand_a,
            candidate_b=cand_b,
            imp_a=imp_a,
            imp_b=imp_b,
            ab_record=ab_record,
            gate_record=gate_record,
            winner=None,
            promoted=False,
            reason=reason,
        )
    except BaseException:
        # Defensive cleanup on any unhandled exception so snapshot + apply +
        # run_batch crashes don't leak candidate dirs or leave current.txt
        # pointing at a half-built candidate. Re-raise — caller still sees
        # the real error.
        if cand_a_dir is not None and cand_a_dir.exists():
            _safe_rmtree(cand_a_dir)
        if cand_b_dir is not None and cand_b_dir.exists():
            _safe_rmtree(cand_b_dir)
        _restore_pointer(parent)
        raise
