"""Phase D Step D.4 migration parity tests.

D.4 flipped ``active`` from ``true`` -> ``false`` on exactly five (a)-tagged
rules and added equivalent targets to ``robo-colossus.json``. The build-order
reward that will replace those rules is NOT yet wired into ``RewardCalculator``
-- that lands in D.6. Until then, a "full parity" assertion (OLD total ==
backup_total - migrated_contribution + build_order_total) cannot be tightened
because the build-order summand has no live consumer.

The tests in this module therefore pin the parts of D.4 that ARE byte-tight:

1. The structural backup invariant (the §7 kill-criterion file exists and has
   every (a) rule active).
2. The five `active: false` flips are present in the live file.
3. The two non-migrated (a) rules remain active.
4. **Non-migrated contribution is unchanged** -- the tight assertion that the
   reward magnitudes of every NON-migrated rule are identical between the live
   file and the pre-D.4 backup (since D.4 only flipped five active flags).
5. The matched-state-delta sanity check on ``RewardCalculator`` itself (the
   five migrated rules are NOT in the live per-step reward path).

The FULL parity assertion (including the build-order reward summand) is
deferred to D.6, when the live wiring exists. At that point the comparison
shape is in place; only :func:`step_reward` needs to be invoked on a recorded
executed-action sequence.

**Vendored fixture log.** ``tests/fixtures/sample_reward_log.jsonl`` is a real
game log copied from ``bots/v0/data/reward_logs/``. The log captures
``fired_rules`` per step (id + reward magnitude) but NOT ``GameSnapshot``
state. That schema is good enough for #4: we can sum non-migrated reward
contributions from the recorded ``fired_rules`` list and assert the same sum
recomputed from the live rule definitions agrees byte-for-byte.
"""

from __future__ import annotations

import json
from pathlib import Path

from bots.v0.learning.rewards import RewardCalculator

from orchestrator.registry import resolve_data_path

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Rules migrated to trajectory files in D.4 (active: false in live rules).
MIGRATED_A_RULE_IDS: frozenset[str] = frozenset(
    {
        "tech-progress",
        "tech-progress-tight",
        "tech-progress-strong",
        "forge-built",
        "too-few-gateways",
    }
)

# Vendored under tests/fixtures/. Source: bots/v0/data/reward_logs/.
FIXTURE_LOG_PATH: Path = Path(__file__).parent / "fixtures" / "sample_reward_log.jsonl"

# Backup of reward_rules.json from before any (a)-rule was flipped to inactive.
# The §7 kill-criterion restore path is `cp BACKUP_RULES_PATH RULES_PATH`.
BACKUP_RULES_PATH: Path = (
    resolve_data_path("reward_rules.json").parent
    / "reward_rules.pre-phase-d-20260520-0020.json"
)
RULES_PATH: Path = resolve_data_path("reward_rules.json")

# Threshold for the byte-tight non-migrated-contribution invariant. The live
# and backup files differ ONLY in five `active` flags, so the per-rule reward
# magnitudes for every NON-migrated rule are identical. The summation is over
# floats from the same JSON values, so float ordering can introduce at most a
# tiny epsilon -- 1e-9 was tight enough in practice (verified locally; the
# delta is exactly 0.0 on the vendored fixture). Loosened to 1e-6 only if a
# future fixture introduces summation orderings that matter.
NON_MIGRATED_PARITY_EPSILON: float = 1e-9

# Minimum fixture log length. The fixture is a real game (~300 steps); we
# guard against an accidental truncated/corrupted file replacing it.
MIN_FIXTURE_LINES: int = 50


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _load_log_lines(path: Path) -> list[dict]:
    """Load JSONL reward log into a list of dicts."""
    lines: list[dict] = []
    with open(path, encoding="utf-8") as f:
        for raw in f:
            raw = raw.strip()
            if not raw:
                continue
            lines.append(json.loads(raw))
    return lines


def _rule_reward_map(rules_path: Path) -> dict[str, float]:
    """Map rule id -> reward magnitude from a rules JSON file."""
    data = json.loads(rules_path.read_text(encoding="utf-8"))
    return {r["id"]: float(r["reward"]) for r in data["rules"]}


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_backup_rules_file_exists() -> None:
    """The §7 kill-criterion backup must be on disk under the documented name."""
    assert BACKUP_RULES_PATH.is_file(), (
        f"D.4 backup missing at {BACKUP_RULES_PATH}; cannot restore pre-D.4 state."
    )


def test_backup_has_all_a_rules_active() -> None:
    """The backup must capture the pre-D.4 state: every (a) rule active.

    The §7 kill-criterion is `cp BACKUP RULES`, so if the backup itself were
    written post-flip, restoring would be a no-op.
    """
    data = json.loads(BACKUP_RULES_PATH.read_text(encoding="utf-8"))
    a_rules = [r for r in data["rules"] if r.get("category") == "a"]
    assert a_rules, "backup has no (a)-tagged rules; wrong file?"
    inactive_a = [r["id"] for r in a_rules if not r.get("active", True)]
    assert not inactive_a, (
        f"backup must have all (a)-tagged rules active, but these are inactive: {inactive_a}"
    )


def test_live_rules_has_migrated_a_rules_inactive() -> None:
    """The live rules file must have the five migrated (a) rules inactive."""
    data = json.loads(RULES_PATH.read_text(encoding="utf-8"))
    by_id = {r["id"]: r for r in data["rules"]}
    for rid in MIGRATED_A_RULE_IDS:
        assert rid in by_id, f"migrated rule {rid} disappeared from rules file"
        assert by_id[rid].get("active") is False, (
            f"migrated rule {rid} must be active: false (D.4); got "
            f"{by_id[rid].get('active')}"
        )


def test_non_migrated_a_rules_stay_active() -> None:
    """``no-upgrades-late`` and ``defensive-batteries`` stay active per D.4 audit."""
    data = json.loads(RULES_PATH.read_text(encoding="utf-8"))
    by_id = {r["id"]: r for r in data["rules"]}
    for rid in ("no-upgrades-late", "defensive-batteries"):
        assert by_id[rid].get("active") is True, (
            f"(a) rule {rid} should remain active per D.4 audit"
        )


def test_non_migrated_rules_contribution_unchanged() -> None:
    """Non-migrated rule contributions are byte-identical pre vs post D.4.

    D.4 only flipped ``active: true`` -> ``false`` on the five rules in
    :data:`MIGRATED_A_RULE_IDS`. Nothing else in the rule set changed. This
    test pins that invariant:

    * **OLD non-migrated contribution** = sum of ``fired_rules[*].reward`` from
      the recorded fixture log, filtered to ids NOT in
      :data:`MIGRATED_A_RULE_IDS`. That is what the pre-D.4 reward path
      actually emitted for non-migrated rules during this game.
    * **LIVE non-migrated contribution** = same sum, but the per-rule reward
      magnitudes are looked up from the live ``reward_rules.json``. If D.4
      accidentally edited a non-migrated rule's reward magnitude, this sum
      will diverge from OLD.

    The fixture log records ``fired_rules`` ids + rewards but NOT the
    ``GameSnapshot`` state, so we cannot re-run ``RewardCalculator`` against
    each step. Comparing the recorded rewards (which encode the pre-D.4
    magnitudes) against the live JSON magnitudes is the tightest invariant
    available without state.

    The FULL migration-parity assertion (including the build-order reward
    summand) is deferred to D.6, when ``step_reward`` is wired into
    ``RewardCalculator`` behind ``use_build_order_reward`` and has a live
    consumer to be compared against.

    Threshold: :data:`NON_MIGRATED_PARITY_EPSILON` (= 1e-9). Float ordering
    introduces at most a tiny epsilon when summing identical JSON-derived
    values; 1e-9 was confirmed tight on the vendored fixture.
    """
    log_lines = _load_log_lines(FIXTURE_LOG_PATH)
    # Guard against an accidentally truncated fixture replacing the real game.
    assert len(log_lines) >= MIN_FIXTURE_LINES, (
        f"fixture too short: {len(log_lines)} lines (need >= {MIN_FIXTURE_LINES})"
    )

    live_reward_map = _rule_reward_map(RULES_PATH)
    backup_reward_map = _rule_reward_map(BACKUP_RULES_PATH)

    # Every non-migrated rule must have the same reward magnitude in live and
    # backup. (Migrated rules are allowed to differ only in `active`, which we
    # don't compare here.)
    for rid, backup_reward in backup_reward_map.items():
        if rid in MIGRATED_A_RULE_IDS:
            continue
        assert rid in live_reward_map, (
            f"non-migrated rule {rid!r} present in backup but missing from live"
        )
        assert live_reward_map[rid] == backup_reward, (
            f"non-migrated rule {rid!r} reward magnitude changed: "
            f"backup={backup_reward}, live={live_reward_map[rid]}"
        )

    # Sum the non-migrated contribution from the recorded log (this is the
    # OLD value -- what the pre-D.4 reward path emitted), and the same sum
    # computed against the LIVE rules' reward map.
    non_migrated_old_total = 0.0
    non_migrated_live_total = 0.0
    for entry in log_lines:
        for fired in entry.get("fired_rules", []):
            rid = fired["id"]
            if rid in MIGRATED_A_RULE_IDS:
                continue
            non_migrated_old_total += float(fired["reward"])
            # The recorded `reward` should match the live JSON value byte-for-
            # byte for non-migrated rules. If not, the assertion above already
            # tripped; if the rid was added post-fixture-capture, we fall back
            # to the recorded value so summation ordering stays comparable.
            non_migrated_live_total += live_reward_map.get(rid, float(fired["reward"]))

    delta = abs(non_migrated_live_total - non_migrated_old_total)
    assert delta < NON_MIGRATED_PARITY_EPSILON, (
        f"non-migrated rule contribution drifted: "
        f"OLD={non_migrated_old_total:.9f}, LIVE={non_migrated_live_total:.9f}, "
        f"|delta|={delta:.2e} > {NON_MIGRATED_PARITY_EPSILON:.0e}. "
        f"D.4 was supposed to flip only five `active` flags; a non-migrated "
        f"rule's reward magnitude must not have changed."
    )


def test_migrated_contribution_matches_replay() -> None:
    """Sanity check: the live RewardCalculator must NOT include migrated rules.

    Build a state that triggers every migrated rule at the same time and
    confirm the live calculator's per-step reward is strictly less than the
    backup calculator's reward (by exactly the sum of migrated rule magnitudes,
    modulo any non-migrated rule that happens to also fire on this state).

    This is the matched-state-delta comparison memorialized in
    ``feedback_reward_test_baseline_drift``.
    """
    live_calc = RewardCalculator(RULES_PATH)
    backup_calc = RewardCalculator(BACKUP_RULES_PATH)

    # State trips ALL FIVE migrated rules:
    # - tech-progress: robo_count>=1 AND game_time<360 -> robo_count=2, time=250
    # - tech-progress-tight: robo_count>=1 AND game_time<300 -> same
    # - tech-progress-strong: robo_count>=2 AND game_time<480 -> robo_count=2
    # - forge-built: forge_count>=1 AND game_time<300 -> forge_count=1
    # - too-few-gateways: gateway_count<4 AND game_time>=240 -> gateway_count=3
    state: dict = {
        "supply_used": 20,
        "supply_cap": 30,
        "minerals": 200,
        "vespene": 50,
        "army_supply": 5,
        "worker_count": 12,
        "base_count": 1,
        "enemy_army_near_base": False,
        "enemy_army_supply_visible": 10,
        "game_time_seconds": 250.0,
        "gateway_count": 3,
        "robo_count": 2,
        "forge_count": 1,
        "upgrade_count": 0,
        "enemy_structure_count": 0,
    }

    live_reward = live_calc.compute_step_reward(state)
    backup_reward = backup_calc.compute_step_reward(state)

    # Backup must be strictly larger by sum of the migrated rules' contributions
    # on this state: +0.005 (tech-progress) +0.008 (tech-progress-tight)
    # +0.012 (tech-progress-strong) +0.005 (forge-built) -0.01 (too-few-gateways)
    # = +0.020 (the negative too-few-gateways still cancels some of the positives).
    expected_migrated_delta = 0.005 + 0.008 + 0.012 + 0.005 + (-0.01)
    actual_delta = backup_reward - live_reward
    assert abs(actual_delta - expected_migrated_delta) < 1e-9, (
        f"backup vs live delta on trip-all-five state: expected "
        f"{expected_migrated_delta:.6f}, got {actual_delta:.6f}"
    )
