"""Compute per-checkpoint weight dynamics for one or all versions.

Models tab Step 9 deliverable. Walks ``bots/v{N}/data/checkpoints/*.zip``,
loads each Stable-Baselines3 PPO checkpoint, and emits one row per
checkpoint to ``data/weight_dynamics.jsonl``::

    Success: {"version": "v3", "checkpoint": "v3.zip", "ts": "...",
              "l2_per_layer": {"...": 12.3, ...},
              "kl_from_parent": 0.087, "hash": "...",
              "canary_source": "transitions_sample", "error": null}

    Failure: {"version": "v3", "checkpoint": "v3.zip", "ts": "...",
              "l2_per_layer": null, "kl_from_parent": null,
              "hash": "...", "canary_source": null,
              "error": "RuntimeError: <message>"}

Idempotent + resumable: a successful row keyed by ``(version, checkpoint,
hash)`` is skipped on subsequent runs; a failure row with the same key is
treated as a retry candidate. Append-only writes are serialised through
the advisory lockfile ``data/.weight_dynamics.lock`` (``fcntl.flock`` on
POSIX, ``msvcrt.locking`` on Windows). Per-checkpoint exceptions never
crash the run — they emit a failure row and the script moves on.

Canary obs vectors: when ``bots/v{N}/data/diagnostic_states.json`` exists
it is used directly; otherwise we deterministically sample 100 transitions
from the version's ``training.db`` (seed = the version string) and
reconstruct obs from the 40 base feature columns. Vectors are then
zero-padded or truncated to the loaded model's expected ``obs_dim`` so
the same canary works across both legacy 24-dim and current 47-dim
checkpoints. KL divergence between parent and child is only computed
when both models share the same ``obs_dim`` AND ``act_dim``; mismatched
shapes produce ``kl_from_parent = null`` (logged at INFO).

The action space is verified to be ``Discrete`` for the checkpoint
families this repo currently produces (`Discrete(NUM_ACTIONS)`); KL is
computed over the categorical distribution returned by
``model.policy.get_distribution(obs)``. Any other action-space class
falls back to ``kl_from_parent = null`` rather than guess at the math.

Usage::

    python scripts/compute_weight_dynamics.py --version v3
    python scripts/compute_weight_dynamics.py --all
    python scripts/compute_weight_dynamics.py --all --canary-source diagnostic_states
"""

from __future__ import annotations

import argparse
import contextlib
import datetime
import hashlib
import json
import logging
import os
import re
import sqlite3
import sys
import time
from collections.abc import Iterator
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # pragma: no cover — import-time only for type checkers.
    from collections.abc import Sequence

_log = logging.getLogger("compute_weight_dynamics")

_VERSION_RE: re.Pattern[str] = re.compile(r"^v\d+$")

# Number of base game-state features stored per transition row in
# ``training.db``. Mirrors ``bots.v10.learning.features.BASE_GAME_FEATURE_DIM``
# but duplicated here so the script stays importable in unit tests
# without dragging the bot's runtime stack in.
_BASE_GAME_FEATURE_DIM = 40

# Number of canary obs vectors sampled when the diagnostic_states.json
# fallback path is taken. 100 is the size called out in §6.7 of the
# Models tab plan.
_CANARY_SAMPLE_SIZE = 100

_CANARY_SOURCE_DIAGNOSTIC = "diagnostic_states"
_CANARY_SOURCE_TRANSITIONS = "transitions_sample"
_CANARY_SOURCE_AUTO = "auto"


def _repo_root() -> Path:
    """Resolve the repo root from this script's location.

    ``scripts/compute_weight_dynamics.py`` lives one level under the
    repo root, so ``parent.parent`` lands at the repo root.
    """
    return Path(__file__).resolve().parent.parent


def _validate_version(version: str) -> str:
    """Reject version strings that don't match ``^v\\d+$``.

    Mirrors the validator in ``bots/v0/learning/post_promotion_hooks.py``
    so a bogus argument is refused before any heavy import (notably
    ``torch`` / ``stable_baselines3``) executes.
    """
    if not isinstance(version, str) or not _VERSION_RE.match(version):
        msg = f"Invalid version: {version!r} (expected ^v\\d+$)"
        raise ValueError(msg)
    return version


# ---------------------------------------------------------------------------
# Advisory lockfile
# ---------------------------------------------------------------------------
#
# Wrap the platform-specific lock primitive in a context manager so tests
# can stub the public ``acquire_lock`` function without monkeypatching the
# fcntl/msvcrt modules directly. The lockfile lives at
# ``data/.weight_dynamics.lock``.


@contextlib.contextmanager
def acquire_lock(lock_path: Path) -> Iterator[None]:
    """Acquire an exclusive advisory lock on ``lock_path``.

    Uses ``fcntl.flock(LOCK_EX)`` on POSIX and ``msvcrt.locking(LK_LOCK)``
    on Windows. Both calls block until the lock is granted. The lockfile
    is created if it doesn't exist; it is *not* deleted on release —
    leaving the file behind is harmless (the next acquire opens the
    existing file) and avoids a delete-after-close race with another
    process trying to acquire concurrently.
    """
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    # Open in append mode so the file is created if missing without
    # truncating any existing content (the file's contents are unused —
    # the lock is purely advisory — but we don't want a stray write to
    # happen on the wrong process either).
    fd = os.open(str(lock_path), os.O_RDWR | os.O_CREAT, 0o644)
    try:
        if sys.platform == "win32":
            import msvcrt

            # ``LK_LOCK`` blocks (with internal retry) for up to 10
            # seconds per call; loop until acquired so concurrent
            # writers eventually serialise.
            while True:
                try:
                    msvcrt.locking(fd, msvcrt.LK_LOCK, 1)
                    break
                except OSError:
                    time.sleep(0.05)
        else:
            import fcntl

            fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        try:
            if sys.platform == "win32":
                import msvcrt

                # Seek back to position 0 because LK_UNLCK unlocks
                # ``nbytes`` bytes starting at the current file position.
                os.lseek(fd, 0, os.SEEK_SET)
                with contextlib.suppress(OSError):
                    msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                with contextlib.suppress(OSError):
                    fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)


# ---------------------------------------------------------------------------
# JSONL read / index / append
# ---------------------------------------------------------------------------


def _read_existing_rows(jsonl_path: Path) -> list[dict[str, Any]]:
    """Read all rows from ``data/weight_dynamics.jsonl``.

    Returns an empty list if the file is missing. Malformed lines are
    skipped with a warning — the script never crashes on a partially
    corrupted JSONL.
    """
    if not jsonl_path.is_file():
        return []
    rows: list[dict[str, Any]] = []
    try:
        text = jsonl_path.read_text(encoding="utf-8")
    except OSError as exc:
        _log.warning("Could not read %s: %s", jsonl_path, exc)
        return []
    for index, line in enumerate(text.splitlines()):
        stripped = line.strip()
        if not stripped:
            continue
        try:
            row = json.loads(stripped)
        except json.JSONDecodeError as exc:
            _log.warning(
                "Skipping malformed weight_dynamics.jsonl line %d: %s",
                index,
                exc,
            )
            continue
        if isinstance(row, dict):
            rows.append(row)
    return rows


def _index_rows(
    rows: list[dict[str, Any]],
) -> dict[tuple[str, str, str], dict[str, Any]]:
    """Index rows by ``(version, checkpoint, hash)``.

    The latest entry for each key wins (file order is write order, so
    the freshest write overwrites earlier failure rows in-memory). The
    on-disk file keeps both for audit; this index is used only for
    "should we recompute?" decisions.
    """
    index: dict[tuple[str, str, str], dict[str, Any]] = {}
    for row in rows:
        v = row.get("version")
        cp = row.get("checkpoint")
        h = row.get("hash")
        if not (isinstance(v, str) and isinstance(cp, str) and isinstance(h, str)):
            continue
        index[(v, cp, h)] = row
    return index


def _append_row(
    jsonl_path: Path,
    lock_path: Path,
    row: dict[str, Any],
    *,
    success_dedup_key: tuple[str, str, str] | None = None,
) -> None:
    """Append a single row to ``jsonl_path`` under ``lock_path`` lock.

    Creates the parent dir + lockfile if missing. The append is a
    single ``write`` call so partial-write corruption is bounded by
    the OS atomicity guarantee for small writes (single line ≪ PIPE_BUF
    on every platform we support).

    When ``success_dedup_key`` is supplied, after acquiring the lock the
    function re-reads the JSONL and skips the append if a SUCCESS row
    (``error is None``) matching that ``(version, checkpoint, hash)``
    key already exists. This closes the TOCTOU race where two parallel
    invocations both read the index outside the lock, both see "no row",
    and both append duplicate rows. Failure rows are NEVER deduped here
    — retry semantics rely on each attempt appending a fresh row.
    """
    jsonl_path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(row, sort_keys=True) + "\n"
    with acquire_lock(lock_path):
        if success_dedup_key is not None and jsonl_path.is_file():
            # Re-scan inside the lock so a concurrent writer that
            # appended between the caller's index build and our acquire
            # is detected. Cheaper than holding the lock across the
            # whole compute (which would serialise all checkpoints).
            existing_rows = _read_existing_rows(jsonl_path)
            for existing in existing_rows:
                if (
                    existing.get("version") == success_dedup_key[0]
                    and existing.get("checkpoint") == success_dedup_key[1]
                    and existing.get("hash") == success_dedup_key[2]
                    and existing.get("error") is None
                ):
                    _log.info(
                        "Skipping duplicate success row for %s/%s "
                        "(hash=%s) — concurrent writer beat us to it",
                        success_dedup_key[0],
                        success_dedup_key[1],
                        success_dedup_key[2],
                    )
                    return
        with jsonl_path.open("a", encoding="utf-8", newline="\n") as fh:
            fh.write(payload)
            fh.flush()


# ---------------------------------------------------------------------------
# Canary obs reconstruction
# ---------------------------------------------------------------------------


def _load_diagnostic_states(version_data_dir: Path) -> list[list[float]] | None:
    """Load ``diagnostic_states.json`` if present; return ``None`` if absent.

    The file is expected to be a JSON list of obs vectors (each a list
    of floats). Anything malformed → warning + ``None`` so the caller
    falls back to the transitions sampler.
    """
    path = version_data_dir / "diagnostic_states.json"
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        _log.warning(
            "diagnostic_states.json present but unreadable at %s: %s",
            path,
            exc,
        )
        return None
    if not isinstance(payload, list):
        _log.warning(
            "diagnostic_states.json at %s is not a JSON list; ignoring",
            path,
        )
        return None
    out: list[list[float]] = []
    for item in payload:
        if not isinstance(item, list):
            continue
        try:
            out.append([float(x) for x in item])
        except (TypeError, ValueError):
            continue
    return out if out else None


def _sample_transitions(
    version_data_dir: Path,
    *,
    version: str,
    sample_size: int = _CANARY_SAMPLE_SIZE,
) -> list[list[float]] | None:
    """Sample obs vectors deterministically from the version's training.db.

    Uses the lazily-imported ``_coerce_action`` helper from
    ``bots.v10.learning.database`` on the action column so any future
    BLOB-encoded rows decode the same way the production reader does.
    The action is read but discarded — only the obs vector is used as a
    canary input. The seed is the version string so the same sample is
    used on re-runs and across promotion hooks.

    Returns ``None`` when the DB doesn't exist OR has no transitions.
    """
    db_path = version_data_dir / "training.db"
    if not db_path.is_file():
        return None

    try:
        from bots.v10.learning.database import _coerce_action  # noqa: PLC0415
    except ImportError:  # pragma: no cover — package always present in repo.
        _log.warning(
            "bots.v10.learning.database not importable; skipping action-coerce",
        )
        _coerce_action = int  # type: ignore[assignment]

    # Build the column list out-of-band so the SELECT is a single
    # parameterised query (defense in depth even though the columns are
    # static literals).
    state_cols = [
        "supply_used", "supply_cap", "minerals", "vespene", "army_supply",
        "worker_count", "base_count", "enemy_near", "enemy_supply",
        "game_time_secs", "gateway_count", "robo_count", "forge_count",
        "upgrade_count", "enemy_structure_count", "cannon_count",
        "battery_count", "zealot_count", "stalker_count", "sentry_count",
        "immortal_count", "colossus_count", "archon_count",
        "high_templar_count", "dark_templar_count", "phoenix_count",
        "void_ray_count", "carrier_count", "tempest_count",
        "disruptor_count", "warp_prism_count", "observer_count",
        "enemy_light_count", "enemy_armored_count", "enemy_siege_count",
        "enemy_support_count", "enemy_air_harass_count",
        "enemy_heavy_count", "enemy_capital_count", "enemy_cloak_count",
    ]
    assert len(state_cols) == _BASE_GAME_FEATURE_DIM
    select_cols = ", ".join(state_cols)

    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    except sqlite3.OperationalError as exc:
        _log.warning("Cannot open %s read-only: %s", db_path, exc)
        return None
    try:
        try:
            total_row = conn.execute(
                "SELECT COUNT(*) FROM transitions"
            ).fetchone()
        except sqlite3.OperationalError as exc:
            _log.warning("transitions table missing in %s: %s", db_path, exc)
            return None
        total = int(total_row[0]) if total_row else 0
        if total == 0:
            return None

        # Deterministic sample: rather than ``ORDER BY RANDOM()``
        # (non-deterministic across runs), seed ``random.Random`` with
        # the version string and ``sample`` ``sample_size`` distinct
        # row offsets without replacement, then sort so we read in
        # ascending order. Same seed → same offsets across runs.
        import random  # noqa: PLC0415

        rng = random.Random(version)
        offsets: list[int] = sorted(rng.sample(range(total), min(sample_size, total)))

        # Pull rows by id position. Using ``OFFSET`` repeatedly is O(N²);
        # instead, fetch all rows in a single read (transitions is at
        # most a few hundred MB) but skip-by-index in Python.
        rows = conn.execute(
            f"SELECT {select_cols}, action FROM transitions"
        ).fetchall()
        out: list[list[float]] = []
        for off in offsets:
            if off >= len(rows):
                continue
            row = rows[off]
            obs = [float(x) for x in row[:_BASE_GAME_FEATURE_DIM]]
            # Touch the action column so ``_coerce_action`` is exercised
            # in the canary read path (per Step 9 spec). Discarded after.
            _ = _coerce_action(row[_BASE_GAME_FEATURE_DIM])
            out.append(obs)
        return out if out else None
    finally:
        conn.close()


def _resolve_canary(
    version_data_dir: Path,
    *,
    version: str,
    canary_source: str,
) -> tuple[list[list[float]] | None, str | None]:
    """Pick the canary obs set + report which source produced it.

    Returns ``(vectors, source_label)``. ``vectors=None`` means no
    canary set could be assembled; the caller should emit a failure
    row with ``canary_source = "transitions_sample"`` (the last source
    we tried) and an explanatory ``error`` message.
    """
    if canary_source in (_CANARY_SOURCE_DIAGNOSTIC, _CANARY_SOURCE_AUTO):
        diag = _load_diagnostic_states(version_data_dir)
        if diag is not None:
            return diag, _CANARY_SOURCE_DIAGNOSTIC
        if canary_source == _CANARY_SOURCE_DIAGNOSTIC:
            # User explicitly asked for diagnostic-states-only and the
            # file is missing — surface that as a None-canary failure.
            return None, _CANARY_SOURCE_DIAGNOSTIC

    samples = _sample_transitions(version_data_dir, version=version)
    if samples is not None:
        return samples, _CANARY_SOURCE_TRANSITIONS
    return None, _CANARY_SOURCE_TRANSITIONS


def _canary_signature(vectors: Sequence[Sequence[float]]) -> str:
    """Deterministic SHA-256 hex of the canary obs bytes.

    Used as a stable component of the row hash so that two runs over
    the same checkpoint with the same canary produce the same row hash
    (and the second run is therefore skipped via the dedup index).
    """
    h = hashlib.sha256()
    for vec in vectors:
        for x in vec:
            # ``repr(float)`` round-trips exactly — that's what we want
            # for a deterministic signature across Python versions.
            h.update(repr(float(x)).encode("ascii"))
        h.update(b"|")
    return h.hexdigest()


def _row_hash(
    *,
    version: str,
    checkpoint: str,
    canary_signature: str,
) -> str:
    """First 16 hex chars of the row hash; see plan §5 schema."""
    h = hashlib.sha256()
    h.update(f"{version}:{checkpoint}:{canary_signature}".encode())
    return h.hexdigest()[:16]


# ---------------------------------------------------------------------------
# Per-checkpoint compute
# ---------------------------------------------------------------------------


def _shape_canary_for_model(
    canary: Sequence[Sequence[float]],
    *,
    obs_dim: int,
) -> Any:
    """Zero-pad or truncate canary vectors to ``obs_dim``; return ndarray.

    Different checkpoint families in this repo were trained against
    different feature dims (legacy 24-dim vs current 47-dim — see plan
    §6.7 fallback). Rather than emit a failure row for every legacy
    checkpoint, we trim/pad the canary to fit the model's expected
    obs_dim. This is implementation-defined per the Step 9 spec; the
    chosen behaviour is documented here AND in the script docstring.
    """
    import numpy as np  # noqa: PLC0415 — lazy-import per module docstring.

    arr = np.zeros((len(canary), obs_dim), dtype=np.float32)
    for i, vec in enumerate(canary):
        n = min(len(vec), obs_dim)
        arr[i, :n] = np.asarray(vec[:n], dtype=np.float32)
    return arr


def _compute_l2_per_layer(model: Any) -> dict[str, float]:
    """Return ``{param_name: l2_norm}`` for every named policy parameter.

    Computed on CPU. Detached so torch's autograd graph is not held
    across iterations (each PPO model defines its own params; we don't
    care about gradients for the L2 snapshot).
    """
    import torch  # noqa: PLC0415

    out: dict[str, float] = {}
    for name, param in model.policy.named_parameters():
        out[name] = float(torch.linalg.norm(param.detach()).item())
    return out


def _compute_kl_from_parent(
    *,
    child_model: Any,
    parent_model: Any,
    canary_obs: Any,
) -> float | None:
    """KL(parent || child) averaged over canary states.

    Assumes both models accept ``canary_obs`` (caller is responsible
    for shape compatibility — if the parent's ``obs_dim`` differs from
    the child's, return None and let the caller record null KL).

    For Discrete action spaces (the only kind this repo currently
    produces) the policy distribution is a Categorical whose
    ``log_prob`` / ``probs`` are well-defined. For any other action
    space we return None — the script docstring documents that
    conservative fallback.
    """
    import numpy as np  # noqa: PLC0415
    import torch  # noqa: PLC0415

    # Verify shape compatibility before touching the policies.
    if (
        parent_model.observation_space.shape
        != child_model.observation_space.shape
    ):
        _log.info(
            "KL skipped: parent obs_dim %s != child obs_dim %s",
            parent_model.observation_space.shape,
            child_model.observation_space.shape,
        )
        return None

    try:
        child_n = int(child_model.action_space.n)
        parent_n = int(parent_model.action_space.n)
    except AttributeError:
        _log.info(
            "KL skipped: action space lacks ``.n`` (not Discrete) "
            "(child=%r parent=%r)",
            type(child_model.action_space).__name__,
            type(parent_model.action_space).__name__,
        )
        return None
    if child_n != parent_n:
        _log.info(
            "KL skipped: action_space.n mismatch parent=%d child=%d",
            parent_n,
            child_n,
        )
        return None

    obs_t = torch.from_numpy(np.asarray(canary_obs, dtype=np.float32))

    with torch.no_grad():
        # ``get_distribution`` returns SB3's distribution wrapper whose
        # ``.distribution`` attribute is a torch ``Categorical`` for
        # Discrete action spaces. The wrapper exposes ``.log_prob`` /
        # ``.probs`` consistently across SB3 minor versions so we go
        # through the inner torch object directly.
        child_dist = child_model.policy.get_distribution(obs_t).distribution
        parent_dist = parent_model.policy.get_distribution(obs_t).distribution

        # KL(parent || child) = sum_a p(a) * (log p(a) - log q(a)).
        # Read via ``.probs`` / ``log()`` to keep the math identical
        # to the formula in the spec; SB3's torch Categorical exposes
        # ``probs`` shape (B, A).
        p = parent_dist.probs.float()
        q = child_dist.probs.float()
        # Numerical floor — log(0) blows up; clamp to a tiny value
        # consistent across both distributions.
        eps = torch.finfo(p.dtype).eps
        p = torch.clamp(p, min=eps)
        q = torch.clamp(q, min=eps)
        kl_per_state = (p * (p.log() - q.log())).sum(dim=-1)
        kl_mean = float(kl_per_state.mean().item())

    return kl_mean


def _checkpoint_records(
    repo_root: Path,
    version: str,
) -> list[Path]:
    """List the ``.zip`` checkpoint files for ``version`` in name order.

    Returns ``[]`` when the version's checkpoints dir is missing — the
    caller will silently skip the version (no rows emitted).
    """
    cps_dir = repo_root / "bots" / version / "data" / "checkpoints"
    if not cps_dir.is_dir():
        return []
    return sorted(p for p in cps_dir.glob("*.zip") if p.is_file())


def _resolve_parent_checkpoint(
    repo_root: Path,
    version: str,
) -> Path | None:
    """Look up the parent checkpoint via ``bots/v{N}/manifest.json``.

    Returns the parent's ``best``-named ``.zip`` if it exists, else
    falls back to the parent's ``v{parent}.zip``. Returns ``None``
    when no manifest, no parent field, or no resolvable file.
    """
    manifest_path = repo_root / "bots" / version / "manifest.json"
    if not manifest_path.is_file():
        return None
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(manifest, dict):
        return None
    parent = manifest.get("parent")
    if not isinstance(parent, str) or not _VERSION_RE.match(parent):
        return None
    parent_dir = repo_root / "bots" / parent / "data" / "checkpoints"
    if not parent_dir.is_dir():
        return None

    parent_manifest = parent_dir / "manifest.json"
    best: str | None = None
    if parent_manifest.is_file():
        try:
            pm = json.loads(parent_manifest.read_text(encoding="utf-8"))
            if isinstance(pm, dict):
                b = pm.get("best")
                if isinstance(b, str) and b:
                    best = b
        except (OSError, json.JSONDecodeError):
            best = None

    candidates: list[Path] = []
    if best is not None:
        candidates.append(parent_dir / f"{best}.zip")
    candidates.append(parent_dir / f"{parent}.zip")
    # Intentionally NO alphabetical-glob fallback: picking an arbitrary
    # .zip would compute KL against an unrelated checkpoint and produce
    # plausible-looking but meaningless numbers. If neither the
    # parent-manifest's `best` zip nor `{parent}.zip` exists, return
    # None so the row records `kl_from_parent: null` honestly.
    for cand in candidates:
        if cand.is_file():
            return cand
    return None


def _utc_now_iso() -> str:
    """ISO-8601 UTC timestamp with millisecond precision + trailing ``Z``.

    Millisecond precision lets two same-second appends still differ on
    ``ts`` — relied on by the failure-then-success retry path so an
    operator (or a test) can prove a fresh compute happened rather than
    an echo of an earlier row.
    """
    now = datetime.datetime.now(datetime.UTC)
    # ``%f`` is microseconds; truncate to milliseconds (3 digits) to
    # keep the field human-readable.
    return now.strftime("%Y-%m-%dT%H:%M:%S.") + f"{now.microsecond // 1000:03d}Z"


def _process_one_checkpoint(
    *,
    repo_root: Path,
    version: str,
    checkpoint_path: Path,
    canary_vectors: list[list[float]] | None,
    canary_source: str | None,
    jsonl_path: Path,
    lock_path: Path,
    success_index: dict[tuple[str, str, str], dict[str, Any]],
) -> None:
    """Compute one row for ``checkpoint_path`` and append it to JSONL.

    Wraps every step in a try/except — any exception turns into a
    failure-row write and a warning log. The row is keyed by
    ``(version, checkpoint, hash)`` so re-running the script skips
    completed work but retries failures.
    """
    checkpoint = checkpoint_path.name

    # No canary → emit failure row with null canary_source.
    if canary_vectors is None:
        row_hash = _row_hash(
            version=version,
            checkpoint=checkpoint,
            canary_signature="no-canary",
        )
        if (version, checkpoint, row_hash) in success_index:
            existing_no_canary = success_index[(version, checkpoint, row_hash)]
            existing_error = existing_no_canary.get("error")
            if existing_error is None:
                _log.info(
                    "Skipping %s/%s — already computed with hash %s",
                    version,
                    checkpoint,
                    row_hash,
                )
                return
            # Idempotent retry: real per-checkpoint failures (RuntimeError
            # from torch, OSError, etc.) get a fresh attempt every run.
            # But the "no canary states available" failure is a permanent
            # absence (no diagnostic_states.json AND no training.db
            # transitions) and re-emitting the same row on every run
            # would grow the JSONL unbounded. Skip it once we've already
            # recorded it; the operator can drop the failure row when
            # they backfill a diagnostic_states.json.
            if isinstance(existing_error, str) and existing_error.startswith(
                "RuntimeError: no canary states available"
            ):
                _log.info(
                    "Skipping %s/%s — no-canary failure already recorded "
                    "(permanent absence; provide diagnostic_states.json "
                    "or seed training.db to retry)",
                    version,
                    checkpoint,
                )
                return
        _log.warning(
            "%s/%s: no canary states available "
            "(no diagnostic_states.json, no training.db transitions)",
            version,
            checkpoint,
        )
        _append_row(
            jsonl_path,
            lock_path,
            {
                "version": version,
                "checkpoint": checkpoint,
                "ts": _utc_now_iso(),
                "l2_per_layer": None,
                "kl_from_parent": None,
                "hash": row_hash,
                # Per plan §5: failure rows record canary_source as null.
                # The originating source label only appears on success rows.
                "canary_source": None,
                "error": (
                    "RuntimeError: no canary states available "
                    "(no diagnostic_states.json, no training.db transitions)"
                ),
            },
        )
        return

    canary_sig = _canary_signature(canary_vectors)
    row_hash = _row_hash(
        version=version,
        checkpoint=checkpoint,
        canary_signature=canary_sig,
    )

    # Skip already-computed success rows; failure rows are retried
    # because ``existing.get("error")`` will be non-None for them.
    existing = success_index.get((version, checkpoint, row_hash))
    if existing is not None and existing.get("error") is None:
        _log.info(
            "Skipping %s/%s — already computed with hash %s",
            version,
            checkpoint,
            row_hash,
        )
        return

    try:
        # Lazy-import inside the per-checkpoint loop so a failed
        # ``import torch`` only fails one row (we still get a partial
        # success log) and tests that don't load real models can
        # monkeypatch at the seam.
        from stable_baselines3 import PPO  # noqa: PLC0415

        child_model = PPO.load(str(checkpoint_path), device="cpu")
        child_obs_shape = child_model.observation_space.shape
        if child_obs_shape is None or len(child_obs_shape) == 0:
            msg = (
                f"observation_space.shape is None/empty for {checkpoint_path}"
            )
            raise RuntimeError(msg)
        obs_dim = int(child_obs_shape[0])
        canary_obs = _shape_canary_for_model(canary_vectors, obs_dim=obs_dim)

        l2_per_layer = _compute_l2_per_layer(child_model)

        kl: float | None = None
        parent_path = _resolve_parent_checkpoint(repo_root, version)
        if parent_path is not None:
            try:
                parent_model = PPO.load(str(parent_path), device="cpu")
                kl = _compute_kl_from_parent(
                    child_model=child_model,
                    parent_model=parent_model,
                    canary_obs=canary_obs,
                )
            except (
                FileNotFoundError,
                RuntimeError,
                KeyError,
                ValueError,
                OSError,
            ) as exc:
                _log.info(
                    "Parent KL compute failed for %s/%s vs %s: %s — "
                    "kl_from_parent=null",
                    version,
                    checkpoint,
                    parent_path,
                    exc,
                )
                kl = None

        _append_row(
            jsonl_path,
            lock_path,
            {
                "version": version,
                "checkpoint": checkpoint,
                "ts": _utc_now_iso(),
                "l2_per_layer": l2_per_layer,
                "kl_from_parent": kl,
                "hash": row_hash,
                "canary_source": canary_source,
                "error": None,
            },
            # Dedup inside the lock so two parallel runs can't both
            # write the same success row.
            success_dedup_key=(version, checkpoint, row_hash),
        )
    except Exception as exc:  # noqa: BLE001 — explicit broad catch per spec.
        # Any failure (FileNotFoundError, torch errors, OOM, KeyError,
        # RuntimeError, etc.) → emit failure row, log warning, continue.
        _log.warning(
            "%s/%s compute failed: %s: %s",
            version,
            checkpoint,
            type(exc).__name__,
            exc,
        )
        _append_row(
            jsonl_path,
            lock_path,
            {
                "version": version,
                "checkpoint": checkpoint,
                "ts": _utc_now_iso(),
                "l2_per_layer": None,
                "kl_from_parent": None,
                "hash": row_hash,
                # Per plan §5: failure rows record canary_source as null.
                "canary_source": None,
                "error": f"{type(exc).__name__}: {exc}",
            },
        )


# ---------------------------------------------------------------------------
# Per-version + main entry points
# ---------------------------------------------------------------------------


def _list_all_versions(repo_root: Path) -> list[str]:
    """Return every ``vN`` subdir of ``bots/`` in version-number order."""
    bots_dir = repo_root / "bots"
    if not bots_dir.is_dir():
        return []
    out: list[str] = []
    for child in bots_dir.iterdir():
        if not child.is_dir():
            continue
        if _VERSION_RE.match(child.name):
            out.append(child.name)
    out.sort(key=lambda name: int(name[1:]))
    return out


def _process_version(
    repo_root: Path,
    *,
    version: str,
    canary_source: str,
    jsonl_path: Path,
    lock_path: Path,
    success_index: dict[tuple[str, str, str], dict[str, Any]],
) -> None:
    """Compute every checkpoint of ``version`` (skips silently if none)."""
    version = _validate_version(version)
    cps = _checkpoint_records(repo_root, version)
    if not cps:
        _log.info("%s: no checkpoints under data/checkpoints — skipping", version)
        return

    version_data_dir = repo_root / "bots" / version / "data"
    canary_vectors, source_label = _resolve_canary(
        version_data_dir,
        version=version,
        canary_source=canary_source,
    )

    _log.info(
        "%s: %d checkpoints; canary_source=%s; canary_count=%s",
        version,
        len(cps),
        source_label,
        len(canary_vectors) if canary_vectors is not None else "0",
    )

    for cp in cps:
        _log.info("%s: computing %s", version, cp.name)
        _process_one_checkpoint(
            repo_root=repo_root,
            version=version,
            checkpoint_path=cp,
            canary_vectors=canary_vectors,
            canary_source=source_label,
            jsonl_path=jsonl_path,
            lock_path=lock_path,
            success_index=success_index,
        )


def main(argv: list[str] | None = None) -> int:
    """CLI entry point."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )

    parser = argparse.ArgumentParser(
        description=(
            "Compute per-checkpoint weight dynamics + KL-from-parent and "
            "append rows to data/weight_dynamics.jsonl."
        ),
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument(
        "--version",
        help="Single version (e.g. v3). Mutually exclusive with --all.",
    )
    group.add_argument(
        "--all",
        action="store_true",
        help="Process every bots/v* version in order.",
    )
    parser.add_argument(
        "--canary-source",
        choices=[
            _CANARY_SOURCE_DIAGNOSTIC,
            _CANARY_SOURCE_TRANSITIONS,
            _CANARY_SOURCE_AUTO,
        ],
        default=_CANARY_SOURCE_AUTO,
        help=(
            "Canary obs source. 'auto' (default): diagnostic_states.json "
            "if present, else hashed sample from training.db. "
            "'diagnostic_states': require the JSON file. "
            "'transitions_sample': skip diagnostic_states even if present."
        ),
    )

    args = parser.parse_args(argv)

    if not args.all:
        # Validate BEFORE importing torch so a malformed input is
        # rejected cheaply.
        try:
            _validate_version(args.version)
        except ValueError as exc:
            print(f"compute_weight_dynamics: {exc}", file=sys.stderr)
            return 2

    repo_root = _repo_root()
    cross_dir = repo_root / "data"
    jsonl_path = cross_dir / "weight_dynamics.jsonl"
    lock_path = cross_dir / ".weight_dynamics.lock"

    existing_rows = _read_existing_rows(jsonl_path)
    success_index = _index_rows(existing_rows)

    if args.all:
        versions = _list_all_versions(repo_root)
    else:
        versions = [args.version]

    if not versions:
        _log.warning("No versions to process")
        return 0

    start = time.monotonic()
    _log.info("compute_weight_dynamics: starting %d version(s)", len(versions))

    for version in versions:
        try:
            _process_version(
                repo_root,
                version=version,
                canary_source=args.canary_source,
                jsonl_path=jsonl_path,
                lock_path=lock_path,
                success_index=success_index,
            )
        except Exception as exc:  # noqa: BLE001 — see comment below.
            # In ``--all`` mode one bad version (any failure mode: bad
            # version name, unreadable manifest, ImportError on a
            # corrupted bot package, OSError from a missing data dir,
            # etc.) must NOT kill the cross-version backfill. Log the
            # full traceback so the operator can diagnose, then move on.
            _log.exception("Skipping version %r due to error: %s", version, exc)
            continue

    elapsed = time.monotonic() - start
    _log.info(
        "compute_weight_dynamics: done in %.2fs (%d versions)",
        elapsed,
        len(versions),
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
