"""Phase D.3 build-order reward primitive.

Edit-distance scoring between the bot's executed action sequence and a
time-gated target trajectory (loaded from JSON files governed by the D.2
schema at ``bots/<v>/data/build_orders/_schema.json``).

The module exposes:

* :class:`BuildOrderStepTarget` -- one (action, target, time, weight) row.
* :class:`BuildOrderTrajectory` -- a named, ordered list of targets plus a
  ``tolerance_seconds`` half-width used for timing matches.
* :func:`load_build_order` -- read + validate a trajectory JSON file. Defaults
  to ``bots/<current>/data/build_orders/<label>.json`` via
  :func:`orchestrator.registry.resolve_data_path`.
* :func:`compute_progress` -- weighted Levenshtein-style edit distance between
  an executed action list and a trajectory. Lower-is-better (0 == perfect).
* :func:`step_reward` -- delta-based per-step reward. Sign-flipped from the
  raw distance so reward is *positive* when the bot closes the gap to the
  trajectory and *negative* when it diverges.

This module is the primitive. D.6 wires :func:`step_reward` into
``bots/v13/learning/rewards.py::RewardCalculator`` behind the
``use_build_order_reward`` flag; nothing in this file imports or mutates that
calculator.

Target-casing normalization
---------------------------

Trajectory JSONs use the canonical lowercase form documented in
``_schema.json``: ``pylon``, ``gateway``, ``cyberneticscore``,
``roboticsfacility``, plus snake_case ids for upgrades like
``warp_gate_research``. The bot's runtime ``BuildStep.target`` is CamelCase
(``Pylon``, ``Gateway``, ``CyberneticsCore``, ``RoboticsFacility``).

To match across that boundary :func:`compute_progress` lowercases both sides
of the ``(action, target)`` tuple before comparing. Underscores are NOT
stripped -- doing so would alias ``warp_gate_research`` to ``warpgateresearch``
and break upgrade matching. ``CyberneticsCore`` already lowercases to
``cyberneticscore`` (which is what the trajectory files store), so case-only
normalization is sufficient.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import jsonschema

__all__ = [
    "BuildOrderStepTarget",
    "BuildOrderTrajectory",
    "compute_progress",
    "load_build_order",
    "step_reward",
]


@dataclass
class BuildOrderStepTarget:
    """One target row in a build-order trajectory.

    Mirrors the ``Target`` subschema in
    ``bots/<v>/data/build_orders/_schema.json``.
    """

    action: str  # "build" | "train" | "research"
    target: str  # canonical lowercase unit/structure/upgrade name
    time_seconds: int
    weight: float = 1.0


@dataclass
class BuildOrderTrajectory:
    """A named, ordered list of build-order targets.

    Construct via :func:`load_build_order` for the validated-from-JSON path;
    direct construction is supported (and used by tests) when synthesizing
    trajectories in code.
    """

    name: str
    targets: list[BuildOrderStepTarget] = field(default_factory=list)
    tolerance_seconds: int = 30


def _schema_path() -> Path:
    """Return the absolute path to the trajectory schema for the active version."""
    # Local import avoids a hard dependency on ``orchestrator`` when this
    # module is imported in isolation (e.g. by a unit test that only exercises
    # :func:`compute_progress`).
    from orchestrator.registry import resolve_data_path

    return resolve_data_path("build_orders/_schema.json")


def _default_data_dir() -> Path:
    """Return ``bots/<current>/data/build_orders/`` for the active version."""
    return _schema_path().parent


def load_build_order(label: str, *, data_dir: Path | None = None) -> BuildOrderTrajectory:
    """Load and validate a trajectory JSON file by label.

    ``<data_dir>/<label>.json`` is read, validated against ``_schema.json``
    in the same directory, and parsed into a :class:`BuildOrderTrajectory`.

    Args:
        label: filename stem (e.g. ``"4-gate-aggression"``).
        data_dir: directory containing the trajectory files. Defaults to
            ``bots/<current>/data/build_orders/`` via
            :func:`orchestrator.registry.resolve_data_path`.

    Raises:
        FileNotFoundError: if either ``<label>.json`` or ``_schema.json`` is
            missing from ``data_dir``.
        jsonschema.ValidationError: if the trajectory violates the schema.
    """
    directory = _default_data_dir() if data_dir is None else data_dir
    trajectory_path = directory / f"{label}.json"
    if not trajectory_path.is_file():
        raise FileNotFoundError(
            f"build-order trajectory {label!r} not found at {trajectory_path}"
        )
    schema_path = directory / "_schema.json"
    if not schema_path.is_file():
        raise FileNotFoundError(
            f"build-order schema not found at {schema_path}"
        )

    instance: Any = json.loads(trajectory_path.read_text(encoding="utf-8"))
    schema: Any = json.loads(schema_path.read_text(encoding="utf-8"))
    jsonschema.validate(instance=instance, schema=schema)

    targets = [
        BuildOrderStepTarget(
            action=t["action"],
            target=t["target"],
            time_seconds=int(t["time_seconds"]),
            weight=float(t.get("weight", 1.0)),
        )
        for t in instance["targets"]
    ]
    return BuildOrderTrajectory(
        name=instance["name"],
        targets=targets,
        tolerance_seconds=int(instance.get("tolerance_seconds", 30)),
    )


def _matches(
    executed: tuple[str, str, int],
    target: BuildOrderStepTarget,
    tolerance_seconds: int,
) -> bool:
    """Return True iff ``executed`` matches ``target``.

    Match condition: ``(action, target)`` tuples equal under ``.lower()``
    AND ``|t_exec - t_target| <= tolerance_seconds``.
    """
    exec_action, exec_target, exec_time = executed
    if exec_action.lower() != target.action.lower():
        return False
    if exec_target.lower() != target.target.lower():
        return False
    return abs(exec_time - target.time_seconds) <= tolerance_seconds


def compute_progress(
    executed_actions: list[tuple[str, str, int]],
    trajectory: BuildOrderTrajectory,
) -> float:
    """Weighted Levenshtein-style edit distance between executed and trajectory.

    Returns a non-negative float. ``0.0`` means the executed sequence matches
    the trajectory perfectly (under case-insensitive action+target equality
    and a ``tolerance_seconds`` timing window). Higher values mean greater
    divergence. This is a *distance* -- :func:`step_reward` is the
    sign-flipped per-step quantity used as a learning signal.

    Edit operations:

    * **Match**: ``(action, target)`` equal (case-insensitively) and the
      execution time is within ``trajectory.tolerance_seconds`` of the
      target's ``time_seconds`` -- cost ``0``.
    * **Substitution**: cost = ``target.weight`` (the target was reached at
      the wrong time, or a different action/target was performed in its
      slot).
    * **Deletion** (target missing from executed): cost = ``target.weight``.
    * **Insertion** (executed extra not in trajectory): cost = ``1.0``.

    Edge cases:

    * Empty ``trajectory.targets`` returns ``0.0`` (no targets to score).
    * Empty ``executed_actions`` returns the sum of all target weights
      (every target counts as missing).
    """
    targets = trajectory.targets
    n = len(executed_actions)
    m = len(targets)

    if m == 0:
        return 0.0
    if n == 0:
        return float(sum(t.weight for t in targets))

    # dp[i][j] = min edit-distance between executed_actions[:i] and targets[:j].
    # Row i indexes executed; column j indexes trajectory targets.
    dp: list[list[float]] = [[0.0] * (m + 1) for _ in range(n + 1)]

    # Boundary: matching against an empty trajectory costs 1.0 per executed
    # extra (insertion); matching an empty executed list costs the cumulative
    # target weights (each target is a deletion).
    for i in range(1, n + 1):
        dp[i][0] = dp[i - 1][0] + 1.0
    cumulative_weight = 0.0
    for j in range(1, m + 1):
        cumulative_weight += targets[j - 1].weight
        dp[0][j] = cumulative_weight

    for i in range(1, n + 1):
        for j in range(1, m + 1):
            target = targets[j - 1]
            if _matches(executed_actions[i - 1], target, trajectory.tolerance_seconds):
                match_cost = dp[i - 1][j - 1]
            else:
                match_cost = dp[i - 1][j - 1] + target.weight
            deletion_cost = dp[i - 1][j] + 1.0  # executed extra
            insertion_cost = dp[i][j - 1] + target.weight  # missing target
            dp[i][j] = min(match_cost, deletion_cost, insertion_cost)

    return dp[n][m]


def step_reward(prev_progress: float, curr_progress: float, alpha: float = 1.0) -> float:
    """Per-step reward derived from the change in edit-distance.

    ``progress`` here is the edit-distance from :func:`compute_progress`
    (lower-is-better). The reward is sign-flipped so the learning signal is
    *positive* when the bot improves (distance shrinks) and *negative* when
    it diverges (distance grows).

    Args:
        prev_progress: distance computed at the previous environment step.
        curr_progress: distance computed at the current environment step.
        alpha: scaling coefficient (defaults to ``1.0``).

    Returns:
        ``-alpha * (curr_progress - prev_progress)``.
    """
    return -alpha * (curr_progress - prev_progress)
