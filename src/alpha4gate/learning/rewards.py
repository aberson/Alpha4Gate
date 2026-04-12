"""Reward shaping engine: configurable reward rules loaded from JSON."""

from __future__ import annotations

import json
import logging
import operator as op_module
from pathlib import Path
from typing import Any

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
BASE_TIMEOUT_REWARD: float = -30.0  # milder than loss: bot survived but didn't close

# Supported comparison operators
_OPS: dict[str, Any] = {
    "<": op_module.lt,
    ">": op_module.gt,
    "<=": op_module.le,
    ">=": op_module.ge,
    "==": op_module.eq,
    "!=": op_module.ne,
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
    """

    def __init__(
        self,
        rules_path: str | Path | None = None,
        log_path: str | Path | None = None,
        log_dir: str | Path | None = None,
    ) -> None:
        self._rules: list[RewardRule] = []
        self._log_file: Any | None = None
        self._log_dir: Path | None = None
        if rules_path is not None:
            self.load_rules(rules_path)
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
        """
        self.close_game_log()
        if self._log_dir is not None:
            path = self._log_dir / f"game_{game_id}.jsonl"
            self._log_file = open(path, "a")  # noqa: SIM115

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
                total += BASE_TIMEOUT_REWARD
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

        # Log per-step reward breakdown if logging is enabled
        if self._log_file is not None:
            entry = {
                "game_time": state.get("game_time_seconds", 0.0),
                "total_reward": total,
                "fired_rules": fired_rules,
                "is_terminal": is_terminal,
                "result": result,
            }
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
