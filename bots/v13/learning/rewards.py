"""Reward shaping engine: configurable reward rules loaded from JSON.

Phase D Step D.6 adds two hyperparameter flags read from the sibling
``hyperparams.json`` next to the rules file (or from a passed-in dict):

* ``use_build_order_reward`` (default ``False``) -- when ``True``, the
  per-step reward total is augmented by
  :func:`bots.v13.learning.build_order_reward.step_reward(prev_progress,
  curr_progress, alpha=build_order_reward_alpha)` IFF the caller's state dict
  includes a non-``None`` ``current_build_order`` field. The trajectory file
  is lazily loaded by name on first use, and ``executed_actions`` are derived
  from positive deltas in known structure/unit count fields of the state dict
  (e.g. ``gateway_count`` 1->2 emits ``("build", "gateway", game_time)``).

* ``build_order_reward_alpha`` (default ``1.0``) -- scaling coefficient for
  the summand. Doubling alpha doubles the summand magnitude.

**Cardinal invariant.** When ``use_build_order_reward`` is ``False`` (the
default), per-step rewards AND per-step JSONL log lines are byte-identical to
the pre-D.6 baseline. The new ``build_order_reward`` log field is OMITTED
entirely when the flag is off (no ``null``, no ``0.0``).
"""

from __future__ import annotations

import json
import logging
import operator as op_module
from pathlib import Path
from typing import Any

from bots.v13.learning.build_order_reward import (
    BuildOrderTrajectory,
    compute_progress,
    load_build_order,
    step_reward,
)

_log = logging.getLogger(__name__)

# Base rewards applied at game end.
#
# Phase 4.8 Fix C (#89): terminal rewards scaled 10x up. The prior values
# (+10/-10) were dwarfed by cumulative shaping rewards (~+58 per 192-step
# game), causing PPO to learn passive play (maximize shaping bonuses by
# surviving longer) instead of winning. With +100/-100, a loss yields net
# negative total_reward even with maximum shaping accumulation.
BASE_WIN_REWARD: float = 100.0
BASE_LOSS_REWARD: float = -100.0
BASE_STEP_REWARD: float = 0.001  # survival bonus per step
BASE_TIMEOUT_REWARD: float = -50.0  # base timeout penalty (adjusted by army gradient)

# Supported comparison operators
_OPS: dict[str, Any] = {
    "<": op_module.lt,
    ">": op_module.gt,
    "<=": op_module.le,
    ">=": op_module.ge,
    "==": op_module.eq,
    "!=": op_module.ne,
}

# Phase D.6: mapping from GameSnapshot count-field name -> (action, target)
# tuple used to derive executed-action events from positive count deltas.
# Targets use the lowercase / snake_case canonical form documented in
# ``bots/<v>/data/build_orders/_schema.json`` (so case-insensitive equality
# in :func:`bots.v13.learning.build_order_reward.compute_progress` matches).
# Pylons, assimilators, cybernetics-cores, etc. lack dedicated count fields
# on ``GameSnapshot``; they intentionally don't appear here and just don't
# contribute to the derived sequence -- the edit-distance scorer handles
# missing-target deletions via per-target weights.
_COUNT_FIELD_TO_ACTION: dict[str, tuple[str, str]] = {
    "gateway_count": ("build", "gateway"),
    "robo_count": ("build", "roboticsfacility"),
    "forge_count": ("build", "forge"),
    "cannon_count": ("build", "photoncannon"),
    "battery_count": ("build", "shieldbattery"),
    "base_count": ("build", "nexus"),
    "zealot_count": ("train", "zealot"),
    "stalker_count": ("train", "stalker"),
    "sentry_count": ("train", "sentry"),
    "immortal_count": ("train", "immortal"),
    "colossus_count": ("train", "colossus"),
    "archon_count": ("train", "archon"),
    "high_templar_count": ("train", "hightemplar"),
    "dark_templar_count": ("train", "darktemplar"),
    "phoenix_count": ("train", "phoenix"),
    "void_ray_count": ("train", "voidray"),
    "carrier_count": ("train", "carrier"),
    "tempest_count": ("train", "tempest"),
    "disruptor_count": ("train", "disruptor"),
    "warp_prism_count": ("train", "warpprism"),
    "observer_count": ("train", "observer"),
}


class RewardRule:
    """A single configurable reward rule."""

    def __init__(
        self,
        rule_id: str,
        description: str,
        condition: dict[str, Any],
        requires: dict[str, Any] | None,
        reward: float,
        active: bool = True,
    ) -> None:
        self.rule_id = rule_id
        self.description = description
        self.condition = condition
        self.requires = requires
        self.reward = reward
        self.active = active


class RewardCalculator:
    """Computes shaped rewards by evaluating rules against game state.

    Supports per-game JSONL logging via a log directory. Use ``log_dir`` to
    enable always-on reward logging. Call :meth:`open_game_log` before each
    game and :meth:`close_game_log` after to write per-game files.

    Can also be used as a context manager::

        with RewardCalculator(rules_path, log_dir=some_dir) as calc:
            calc.open_game_log("game_abc")
            ...

    Phase D.6 introduces the build-order reward summand (gated behind
    ``use_build_order_reward`` from the sibling ``hyperparams.json`` or a
    passed-in ``hyperparams`` dict). See module docstring for details.
    """

    def __init__(
        self,
        rules_path: str | Path | None = None,
        log_path: str | Path | None = None,
        log_dir: str | Path | None = None,
        hyperparams: dict[str, Any] | None = None,
    ) -> None:
        self._rules: list[RewardRule] = []
        self._log_file: Any | None = None
        self._log_dir: Path | None = None
        if rules_path is not None:
            self.load_rules(rules_path)
        self.episode_total: float = 0.0

        # Phase D.6: load + apply the two build-order-reward flags.
        #
        # Precedence: explicit ``hyperparams`` kwarg wins over the
        # sibling-file load. Both are optional -- flags default to off / 1.0
        # so the cardinal invariant (byte-identical baseline) holds when no
        # hyperparams source is supplied.
        resolved: dict[str, Any] = {}
        if hyperparams is not None:
            resolved = hyperparams
        elif rules_path is not None:
            sibling = Path(rules_path).parent / "hyperparams.json"
            if sibling.is_file():
                try:
                    with open(sibling, encoding="utf-8") as f:
                        resolved = json.load(f)
                except (OSError, json.JSONDecodeError):
                    # Defensive: a malformed sibling must not block reward
                    # computation. The flag silently falls back to off.
                    resolved = {}
        self._use_build_order_reward: bool = bool(
            resolved.get("use_build_order_reward", False)
        )
        self._build_order_reward_alpha: float = float(
            resolved.get("build_order_reward_alpha", 1.0)
        )

        # Per-game build-order state. Reset in :meth:`open_game_log` AND in
        # :meth:`_reset_build_order_state` so an explicit reset is available
        # for code paths that don't go through ``open_game_log`` (e.g. unit
        # tests, the bot.py path that constructs the calc once and reuses it
        # across games via runner-level reset).
        self._reset_build_order_state()

        if log_dir is not None:
            self._log_dir = Path(log_dir)
            self._log_dir.mkdir(parents=True, exist_ok=True)
        elif log_path is not None:
            # Legacy single-file mode
            self._log_file = open(log_path, "a")  # noqa: SIM115

    # -- context manager --------------------------------------------------

    def __enter__(self) -> RewardCalculator:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # -- per-game log lifecycle -------------------------------------------

    def open_game_log(self, game_id: str) -> None:
        """Open a per-game JSONL log file inside *log_dir*.

        Closes any previously open game log first.
        Requires that ``log_dir`` was set at construction time.

        Also resets per-game build-order reward state (Phase D.6).
        """
        self.close_game_log()
        self.episode_total = 0.0
        self._reset_build_order_state()
        if self._log_dir is not None:
            path = self._log_dir / f"game_{game_id}.jsonl"
            self._log_file = open(path, "a")  # noqa: SIM115

    def _reset_build_order_state(self) -> None:
        """Reset per-game build-order reward state (Phase D.6)."""
        self._prev_progress: float = 0.0
        self._active_build_order: BuildOrderTrajectory | None = None
        self._executed_actions: list[tuple[str, str, int]] = []
        self._prev_counts: dict[str, int] = {}

    def close_game_log(self) -> None:
        """Flush and close the current per-game log file (if any)."""
        if self._log_file is not None and self._log_dir is not None:
            self._log_file.flush()
            self._log_file.close()
            self._log_file = None

    def close(self) -> None:
        """Flush and close any open log file handle."""
        if self._log_file is not None:
            self._log_file.flush()
            self._log_file.close()
            self._log_file = None

    @property
    def rules(self) -> list[RewardRule]:
        """All loaded rules."""
        return self._rules

    def load_rules(self, path: str | Path) -> None:
        """Load reward rules from a JSON file."""
        with open(path) as f:
            data = json.load(f)
        self._rules = []
        for r in data.get("rules", []):
            self._rules.append(
                RewardRule(
                    rule_id=r["id"],
                    description=r.get("description", ""),
                    condition=r["condition"],
                    requires=r.get("requires"),
                    reward=r["reward"],
                    active=r.get("active", True),
                )
            )

    @staticmethod
    def _timeout_reward(state: dict[str, Any]) -> float:
        """Gradient timeout penalty: punish harder when the bot had a big army
        but never attacked (passive play), softer when army is small (macro
        failure is a different problem).

        Range: -80 (big idle army) to -20 (no army at all).
        Base is -50, adjusted ±30 by army_supply ratio.
        """
        army = state.get("army_supply", 0)
        enemy = state.get("enemy_army_supply_visible", 0)
        # Ratio: how much bigger our army is vs enemy (capped 0–1).
        # High ratio = we had a strong army but didn't attack = punish more.
        if army + enemy == 0:
            ratio = 0.0
        else:
            ratio = min(army / max(army + enemy, 1), 1.0)
        # ratio ~0.5 → balanced, ratio ~1.0 → we have army, enemy doesn't
        # Shift so 0.5 maps to 0 adjustment, 1.0 maps to -30, 0.0 maps to +30
        adjustment = -60.0 * (ratio - 0.5)  # range: +30 to -30
        return BASE_TIMEOUT_REWARD + adjustment

    def _build_order_summand(self, state: dict[str, Any]) -> float | None:
        """Return the build-order reward summand for this step, or ``None``.

        Returns ``None`` (no contribution, no log field) when:

        * the ``use_build_order_reward`` flag is off, OR
        * ``state["current_build_order"]`` is missing or ``None``.

        Otherwise lazily loads the named trajectory, updates the
        ``executed_actions`` list from positive count-field deltas, computes
        the current edit-distance progress, and returns the sign-flipped
        delta scaled by ``build_order_reward_alpha``.

        Side effect: advances ``self._prev_progress``, ``self._executed_actions``,
        and ``self._prev_counts``. Reads ``self._active_build_order``.
        """
        if not self._use_build_order_reward:
            return None

        label = state.get("current_build_order")
        if label is None:
            return None

        # Lazy-load (or reload if the active label changed mid-game).
        if (
            self._active_build_order is None
            or self._active_build_order.name != label
        ):
            try:
                self._active_build_order = load_build_order(label)
            except (FileNotFoundError, Exception) as exc:
                _log.warning(
                    "build-order reward: failed to load trajectory %r: %s",
                    label,
                    exc,
                )
                self._active_build_order = None
                return None
            # When the trajectory changes mid-game, restart from a clean
            # slate so edit-distance is computed against the new target.
            self._executed_actions = []
            self._prev_progress = 0.0
            # _prev_counts stays as-is: we only want NEW events from this
            # point onward, not retroactive ones.

        trajectory = self._active_build_order

        # Derive executed-action events from positive count deltas.
        game_time = int(state.get("game_time_seconds", 0.0))
        for field, (action, target) in _COUNT_FIELD_TO_ACTION.items():
            curr = int(state.get(field, 0))
            prev = self._prev_counts.get(field, curr)
            if curr > prev:
                for _ in range(curr - prev):
                    self._executed_actions.append((action, target, game_time))
            self._prev_counts[field] = curr

        curr_progress = compute_progress(self._executed_actions, trajectory)
        summand = step_reward(
            self._prev_progress, curr_progress, alpha=self._build_order_reward_alpha
        )
        self._prev_progress = curr_progress
        return summand

    def compute_step_reward(
        self,
        state: dict[str, Any],
        is_terminal: bool = False,
        result: str | None = None,
    ) -> float:
        """Compute the total shaped reward for a single step.

        Args:
            state: Dict of game state fields (GameSnapshot fields + derived fields).
            is_terminal: Whether this is the last step of the game.
            result: "win" or "loss" (only used when is_terminal=True).

        Returns:
            Total reward for this step.
        """
        # Add derived fields
        state = self._add_derived_fields(state)

        total = BASE_STEP_REWARD

        # Apply terminal reward
        if is_terminal and result is not None:
            if result == "win":
                total += BASE_WIN_REWARD
            elif result == "timeout":
                total += self._timeout_reward(state)
            elif result == "loss":
                total += BASE_LOSS_REWARD

        # Evaluate each active rule
        fired_rules: list[dict[str, Any]] = []
        for rule in self._rules:
            if not rule.active:
                continue
            if self._check_clause(rule.condition, state) and self._check_clause(
                rule.requires, state
            ):
                total += rule.reward
                fired_rules.append({"id": rule.rule_id, "reward": rule.reward})

        # Phase D.6: build-order summand. ``None`` means no contribution (flag
        # off or no active build order). When the flag is on but no build
        # order is set, this returns ``None`` -- per-step total matches the
        # flag-off case for the same state.
        bo_summand = self._build_order_summand(state)
        if bo_summand is not None:
            total += bo_summand

        self.episode_total += total

        # Log per-step reward breakdown if logging is enabled
        if self._log_file is not None:
            entry: dict[str, Any] = {
                "game_time": state.get("game_time_seconds", 0.0),
                "total_reward": total,
                "fired_rules": fired_rules,
                "is_terminal": is_terminal,
                "result": result,
            }
            # Cardinal-invariant guard: only include the new key when the
            # flag is on AND a summand was actually computed. When the flag
            # is off (default) the log line is byte-identical to baseline.
            if bo_summand is not None:
                entry["build_order_reward"] = bo_summand
            self._log_file.write(json.dumps(entry) + "\n")

        return total

    def _check_clause(
        self, clause: dict[str, Any] | None, state: dict[str, Any]
    ) -> bool:
        """Evaluate a single condition/requires clause against the state."""
        if clause is None:
            return True

        field = clause.get("field", "")
        op_str = clause.get("op", "==")
        op_fn = _OPS.get(op_str)
        if op_fn is None:
            _log.warning("Unknown operator: %s", op_str)
            return False

        left = state.get(field)
        if left is None:
            return False

        # Compare against another field or a constant
        if "value_field" in clause:
            right = state.get(clause["value_field"])
            if right is None:
                return False
        else:
            right = clause.get("value")

        try:
            return bool(op_fn(left, right))
        except TypeError:
            return False

    def _add_derived_fields(self, state: dict[str, Any]) -> dict[str, Any]:
        """Compute derived fields and add them to a copy of the state dict."""
        state = dict(state)  # shallow copy

        # has_scouted: true if ScoutManager has assigned a scout this game
        # (Caller should set this from ScoutManager state; default False)
        state.setdefault("has_scouted", False)

        # enemy_structure_near_base_early: enemy structures within proximity AND game_time < 300s
        enemy_structs = state.get("enemy_structure_count", 0)
        game_time = state.get("game_time_seconds", 0.0)
        enemy_near = state.get("enemy_army_near_base", False)
        state.setdefault(
            "enemy_structure_near_base_early",
            enemy_structs > 0 and game_time < 300.0 and enemy_near,
        )

        # Economy derived fields
        minerals = state.get("minerals", 0)
        state.setdefault("is_mineral_floating", minerals > 1000)

        # Military derived fields
        army_supply = state.get("army_supply", 0)
        enemy_supply = state.get("enemy_army_supply_visible", 0)
        state.setdefault("army_stronger_than_enemy", army_supply > enemy_supply)

        # Scouting derived fields
        current_state = state.get("current_state", "")
        state.setdefault(
            "is_defending_rush",
            bool(enemy_near) and current_state == "defend",
        )

        return state
