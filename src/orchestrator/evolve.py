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
- :func:`generate_pool` — run mirror games and ask Claude to propose a pool
  of candidate improvements to try next.
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
import os
import shutil
import uuid
from collections.abc import Callable
from dataclasses import dataclass
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
    "Improvement",
    "RoundResult",
    "apply_improvement",
    "generate_pool",
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

# Exactly the set of allowed keys on each improvement object. Claude
# sometimes decorates responses with extra explanatory fields (e.g.
# ``files_touched``, ``notes``); we reject rather than silently drop so the
# advisor-side drift is surfaced early.
_ALLOWED_IMP_FIELDS: frozenset[str] = frozenset(_REQUIRED_IMP_FIELDS)

# Depth cap for the source-tree listing pasted into the prompt. Anything
# deeper is collapsed so the prompt doesn't balloon on unusual layouts.
_SOURCE_TREE_MAX_DEPTH = 5

# Directory names to exclude from the source-tree listing (noise).
_SOURCE_TREE_EXCLUDE: frozenset[str] = frozenset(
    {"__pycache__", ".pytest_cache", "data", ".mypy_cache", ".ruff_cache"}
)


def _strip_markdown_fences(raw: str) -> str:
    """Remove triple-backtick fences from a Claude response, if present.

    Mirrors the behaviour in ``bots/v0/commands/interpreter.py`` — Claude
    sometimes wraps JSON in ``` or ```json fences even when asked not to.
    Returns the inner JSON-looking payload, or the stripped input if no
    fences are present.
    """
    cleaned = raw.strip()
    if "```" not in cleaned:
        return cleaned
    parts = cleaned.split("```")
    for part in parts:
        candidate = part.strip()
        if candidate.startswith("json"):
            candidate = candidate[4:].strip()
        if candidate.startswith("[") or candidate.startswith("{"):
            return candidate
    # Fences were present but no JSON-looking segment found — fall back.
    return cleaned


def _validate_improvement_item(item: Any, index: int) -> Improvement:
    """Validate one raw Claude JSON item against the Improvement schema.

    Raises ``ValueError`` with a message that names the offending field and
    includes the item index (so the caller can find it in the batch). The
    schema is STRICT: unknown keys (beyond the seven required fields) trigger
    ``ValueError`` rather than being silently dropped — Claude occasionally
    decorates responses with extra explanatory keys and we want that drift
    to surface immediately.
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

    for field in ("title", "description", "expected_impact"):
        val = item[field]
        if not isinstance(val, str):
            raise ValueError(
                f"improvement[{index}].{field} must be a string; got "
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
    # concrete_change is a str for training (JSON-encoded patch) and a str
    # for dev (free-text). If Claude emits a dict we coerce via json.dumps so
    # the Improvement dataclass (which declares concrete_change: str) stays
    # type-consistent.
    if isinstance(concrete, dict):
        concrete = json.dumps(concrete)
    elif not isinstance(concrete, str):
        raise ValueError(
            f"improvement[{index}].concrete_change must be a string or JSON "
            f"object; got {type(concrete).__name__}"
        )

    return Improvement(
        rank=rank,
        title=item["title"],
        type=cast(ImprovementType, type_val),
        description=item["description"],
        principle_ids=list(principle_ids),
        expected_impact=item["expected_impact"],
        concrete_change=concrete,
    )


def _parse_claude_pool(raw: str) -> list[Improvement]:
    """Parse Claude's response string into a list of Improvement objects.

    Expects a JSON array at the top level. Strips markdown fences first.
    Raises ``ValueError`` (with the first 500 chars of the offending payload)
    on decode failure or schema violations.
    """
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


def _list_source_tree(bots_dir: Path, parent: str) -> list[str]:
    """List .py files under ``bots/<parent>/`` as repo-relative paths.

    Excludes ``data/``, cache dirs, and anything past
    ``_SOURCE_TREE_MAX_DEPTH`` from the version root. Returned paths use
    forward slashes for cross-platform consistency in the prompt.
    """
    version_root = bots_dir / parent
    if not version_root.is_dir():
        return []

    files: list[str] = []
    root_parts = len(version_root.parts)
    for dirpath, dirnames, filenames in os.walk(version_root):
        # Prune excluded dirs in-place so os.walk skips them.
        dirnames[:] = [d for d in dirnames if d not in _SOURCE_TREE_EXCLUDE]

        cur = Path(dirpath)
        depth = len(cur.parts) - root_parts
        # Stop DESCENDING past the depth cap, but still collect .py files at
        # exactly the cap depth. Using ``>`` instead of ``>=`` here: files at
        # depth==_SOURCE_TREE_MAX_DEPTH are included, descent stops at
        # depth+1. Previously ``>=`` paired with ``continue`` silently hid
        # any .py file sitting at exactly the cap depth.
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
    """Read tails of matching ``selfplay_<parent>_*.log`` files.

    Missing ``logs/`` or no matches is not an error — returns a placeholder
    so the prompt is still well-formed. Each log is truncated to the last
    ``max_bytes`` bytes to keep the prompt bounded.
    """
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
improvements that a sibling-tournament loop will try in parallel.

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
these seven fields:

  - rank: positive int (1 is best)
  - title: short human label (string)
  - type: "training" or "dev" (exactly one of these two strings)
  - description: long-form rationale (string)
  - principle_ids: list of principle ID strings from the guiding principles \
above
  - expected_impact: free-text prediction (string)
  - concrete_change: string. For "training" imps it must be JSON-encoded with \
shape {{"file": "reward_rules.json" | "hyperparams.json", "patch": \
{{"<key>": <value>, ...}}}} — flat top-level keys only. For "dev" imps it is \
a free-text instruction for a sub-agent that edits Python source under \
bots/{parent}/ .

## Constraints

- type must be exactly "training" or "dev". No other values.
- training improvements edit reward_rules.json or hyperparams.json inside \
the version's data/ dir. They do NOT edit Python.
- dev improvements edit Python source under bots/{parent}/. They do NOT \
touch data files.
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


# Default model alias for the advisor. We shell out to the ``claude`` CLI
# (matches ``bots/v0/claude_advisor.py``), so this is a CLI alias
# (``opus`` / ``sonnet`` / ``haiku``), not a dated SDK model ID. Auth is
# handled by the CLI itself — OAuth subscription token OR ANTHROPIC_API_KEY,
# whichever the operator configured. Opus tier because pool generation is
# strategic reasoning where quality dominates latency/cost (per the
# "Prefer Opus for subagents" project memory note).
_CLAUDE_POOL_MODEL_DEFAULT = "opus"


def _default_claude_fn(prompt: str, *, model: str | None = None) -> str:
    """Production Claude invocation. Shells out to the ``claude`` CLI.

    Matches the pattern in :mod:`bots.v0.claude_advisor` — auth is
    handled by the CLI (OAuth subscription token OR ``ANTHROPIC_API_KEY``,
    whichever the operator configured). This avoids forcing subscription
    users to obtain an API key just to run the evolve pool generator.

    Sync (not async) because :func:`generate_pool` is called once at run
    start, not in a hot loop.

    Model resolution (first match wins):

    1. Explicit ``model`` argument if provided.
    2. ``EVOLVE_POOL_MODEL`` env var if set.
    3. :data:`_CLAUDE_POOL_MODEL_DEFAULT` (currently ``"opus"``).

    The CLI accepts family aliases (``opus`` / ``sonnet`` / ``haiku``)
    and dated model IDs. The default is a family alias so the CLI
    resolves to the latest version the installed ``claude`` binary knows.

    Raises ``RuntimeError`` if the CLI returns a non-zero exit code or
    empty output. This function is only called from production paths;
    tests inject a mock ``claude_fn`` into :func:`generate_pool`.
    """
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
                prompt,
                "--model",
                resolved_model,
                "--output-format",
                "text",
                "--no-session-persistence",
            ],
            capture_output=True,
            text=True,
            check=False,
            timeout=180,
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
    run_batch_fn: Callable[..., list[SelfPlayRecord]] | None = None,
    claude_fn: Callable[[str], str] | None = None,
) -> list[Improvement]:
    """Generate a pool of improvements via mirror self-play + Claude advisor.

    Runs ``mirror_games`` parent-vs-parent games, then prompts Claude with:

    * a summary of the mirror-game outcomes (winners, durations, crashes),
    * tail snippets of any matching ``logs/selfplay_<parent>_*.log`` files
      (optional — missing logs are not fatal),
    * a listing of ``.py`` files under ``bots/<parent>/`` (depth-limited),
    * the full text of ``documentation/sc2/protoss/guiding-principles.md``,
    * and the :class:`Improvement` JSON schema with a strict "return exactly
      ``pool_size`` items" instruction.

    The response (expected to be a JSON array) is parsed and validated
    against the :class:`Improvement` schema. If Claude returns fewer than
    ``pool_size`` items we retry ONCE with an explicit "return exactly N"
    prefix prepended. A second short response raises ``ValueError``.

    Parameters
    ----------
    parent:
        The version name to mirror and to target for improvements.
    mirror_games:
        Number of parent-vs-parent games to run. Defaults to 3.
    pool_size:
        Exact number of improvements to return. Defaults to 10.
    map_name:
        SC2 map passed to ``run_batch_fn``. Defaults to ``"Simple64"``.
    run_batch_fn:
        Injected in tests. Defaults to :func:`orchestrator.selfplay.run_batch`.
    claude_fn:
        Injected in tests. Defaults to :func:`_default_claude_fn` (real
        Anthropic SDK call to Opus).

    Returns
    -------
    list[Improvement]
        Exactly ``pool_size`` validated improvements.

    Raises
    ------
    ValueError
        If Claude's response is malformed, any item fails schema validation,
        or the retry still returns too few items.
    """
    if run_batch_fn is None:
        from orchestrator import selfplay

        run_batch_fn = selfplay.run_batch
    if claude_fn is None:
        claude_fn = _default_claude_fn

    # 1. Run parent-vs-parent mirror games.
    _log.info(
        "generate_pool: running %d mirror games for %s on %s",
        mirror_games,
        parent,
        map_name,
    )
    records = run_batch_fn(parent, parent, mirror_games, map_name)

    # 2. Summary stats for the prompt.
    summary = _summarize_records(records, parent)

    # 3. Log tails (optional; missing logs are fine).
    repo_root = _repo_root()
    log_tails = _read_log_tails(repo_root / "logs", parent)

    # 4. Source-tree listing under bots/<parent>/.
    source_tree = _list_source_tree(repo_root / "bots", parent)

    # 5. Guiding-principles doc.
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

    # 6. Build prompt + call Claude.
    prompt = _build_prompt(
        parent=parent,
        pool_size=pool_size,
        mirror_games=mirror_games,
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
    raw = claude_fn(prompt)
    pool = _parse_claude_pool(raw)

    # 7. Retry once if Claude under-delivered.
    if len(pool) < pool_size:
        _log.warning(
            "generate_pool: got %d items, expected %d; retrying once",
            len(pool),
            pool_size,
        )
        retry_prompt = (
            _PROMPT_RETRY_PREFIX.format(got=len(pool), want=pool_size) + prompt
        )
        raw_retry = claude_fn(retry_prompt)
        pool = _parse_claude_pool(raw_retry)
        if len(pool) < pool_size:
            raise ValueError(
                f"Claude returned {len(pool)} improvements on retry; need "
                f"exactly {pool_size}. Aborting."
            )

    # Truncate if Claude over-delivered so the caller always gets exactly
    # pool_size — ordering is preserved (highest-rank first).
    return pool[:pool_size]
