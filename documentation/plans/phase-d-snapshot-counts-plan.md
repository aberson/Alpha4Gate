# Phase D Snapshot Counts — Add missing GameSnapshot count fields

**Parent plan:** [alpha4gate-master-plan.md](alpha4gate-master-plan.md) — Phase D follow-up
**Track:** Capability (Phase D completion)
**Prerequisites:** Phase D automated section (D.1–D.6.5) shipped 2026-05-20 on master @ `18b20a7`. This feature is a pure additive extension; no preflight required beyond a clean tree.
**Effort estimate:** ~3 hours code + review + 1 build step. ~½ day end-to-end.
**Status:** Drafted 2026-05-20.

## 0. Path convention (read first)

The plan refers to `bots/current/...` as the edit target. `bots/current/` is a Python MetaPathFinder alias to whichever version `bots/current/current.txt` names (currently `v13`). When a step says "edit `bots/current/decision_engine.py`", the build agent must:

1. Read `bots/current/current.txt` (one line, e.g. `v13`).
2. Resolve to `bots/v13/decision_engine.py`.
3. Edit there.

The MetaPathFinder makes `from bots.current.* import ...` resolve transparently to the active version, so new tests track future promotions automatically. Evolve snapshots from `bots/current` into the next promoted `vN`, so changes flow forward.

## 1. What this feature does

Phase D's flag-on `compute_progress` is meant to score the bot's executed `(action, target)` sequence against time-gated trajectory targets defined in `bots/current/data/build_orders/*.json`. The producer side — `RewardCalculator._build_order_summand` derives executed actions from positive deltas in `GameSnapshot` count fields, mapping each delta to `(action, target)` tuples via `_COUNT_FIELD_TO_ACTION` at [rewards.py](../../bots/v13/learning/rewards.py).

`GameSnapshot` has count fields for gateway, robo, forge, cannon, battery, base (nexus), and 15 unit-train counts. It does **not** have count fields for:

| Trajectory target | Appears in trajectory file(s) |
|---|---|
| `pylon` | 4-gate-aggression (×2), robo-colossus (×1) |
| `assimilator` | 4-gate-aggression (×1), robo-colossus (×2) |
| `cyberneticscore` | 4-gate-aggression (×1), robo-colossus (×1) |
| `roboticsbay` | robo-colossus (×1) |
| `warp_gate_research` | 4-gate-aggression (×1), robo-colossus (×1) |

Without count fields, these 5 targets never appear in `executed_actions`, so `compute_progress` treats them as permanently missed — applying a constant negative offset whenever the flag is on. ~5/11 4-gate-aggression and ~5/12 robo-colossus targets unreachable.

This feature adds the 5 missing fields, populates them in `_build_snapshot`, extends `_COUNT_FIELD_TO_ACTION` with the new mappings, and extends the Phase D smoke gate to verify the full chain. Phase D's M1 operator step can then measure variance against the full signal.

## 2. Existing context

- **`bots/v13/decision_engine.py::GameSnapshot`** — dataclass; existing count fields use `int = 0` defaults so all current constructors remain green when new fields are added.
- **`bots/v13/bot.py::_build_snapshot`** ([bot.py:354](../../bots/v13/bot.py#L354)) — populates GameSnapshot fields each frame. Existing structure counts use `len(self.structures(UnitTypeId.X))` (in-progress + completed). Upgrade counts use `self.state.upgrades` (set of `UpgradeId`, populated when research completes).
- **`bots/v13/learning/rewards.py::_COUNT_FIELD_TO_ACTION`** — module-level dict (21 entries) mapping GameSnapshot count field names to `(action, target)` tuples in lowercase-compressed convention. `_build_order_summand` iterates positive deltas vs `_prev_counts` and appends `(action, target, game_time)` events to `_executed_actions`.
- **`tests/test_phase_d_smoke.py`** — D.6.5 smoke gate (9 tests, 1.7s runtime). `TestRewardCalculatorChain.test_compute_step_reward_returns_finite_with_build_order` reaches `_build_order_summand` and verifies `_active_build_order` populates. The natural extension point for new mappings.
- **Trajectory naming convention** — D.2's `bots/v13/data/build_orders/_schema.json` documents lowercase-compressed convention (e.g. `pylon`, `cyberneticscore`, `roboticsbay`). Upgrade ids keep underscores (`warp_gate_research`). D.3's `compute_progress` normalizes via `.lower()` only.

## 3. Scope (build steps)

ONE step. The change is small (~30-50 LOC) and tightly coupled; splitting just adds review overhead.

| Step | Issue | Type | Description |
|------|-------|------|-------------|
| 1 | (blank) | code | Add 5 fields + populate + extend mapping + smoke |

---

### Step 1: Add missing GameSnapshot count fields + extend reward mapping + smoke

**Problem:** Add `pylon_count`, `assimilator_count`, `cyberneticscore_count`, `roboticsbay_count`, `warp_gate_research_count` (all `int = 0`) to `GameSnapshot`; populate in `_build_snapshot` via `len(self.structures(UnitTypeId.X))` for structures and `int(UpgradeId.WARPGATERESEARCH in self.state.upgrades)` for the upgrade; add 5 entries to `_COUNT_FIELD_TO_ACTION` mapping these to `(action, target)` tuples matching the lowercase-compressed trajectory convention; extend `tests/test_phase_d_smoke.py` to drive a delta on each new field through `RewardCalculator` and assert the build-order summand picks them up. Update the rewards.py docstring note that previously said pylon/assimilator/etc. weren't tracked.

**Type:** code
**Issue:** #271
**Flags:** `--reviewers code --isolation worktree`
**Status:** DONE (2026-05-28)

**What to build.**

1. **`bots/current/decision_engine.py::GameSnapshot`** — add 5 fields (place them with other count fields):

   ```python
   pylon_count: int = 0
   assimilator_count: int = 0
   cyberneticscore_count: int = 0
   roboticsbay_count: int = 0
   warp_gate_research_count: int = 0  # 0 or 1: present in self.state.upgrades
   ```

2. **`bots/current/bot.py::_build_snapshot`** — add 5 lines (place adjacent to existing structure-count lines around [bot.py:354-360](../../bots/v13/bot.py#L354)):

   ```python
   pylon_count=len(self.structures(UnitTypeId.PYLON)),
   assimilator_count=len(self.structures(UnitTypeId.ASSIMILATOR)),
   cyberneticscore_count=len(self.structures(UnitTypeId.CYBERNETICSCORE)),
   roboticsbay_count=len(self.structures(UnitTypeId.ROBOTICSBAY)),
   warp_gate_research_count=int(UpgradeId.WARPGATERESEARCH in self.state.upgrades),
   ```

   `UnitTypeId` is already imported. `UpgradeId.WARPGATERESEARCH` is already imported (used at [bot.py:789](../../bots/v13/bot.py#L789)).

3. **`bots/current/learning/rewards.py::_COUNT_FIELD_TO_ACTION`** — add 5 entries matching trajectory-target convention:

   ```python
   "pylon_count": ("build", "pylon"),
   "assimilator_count": ("build", "assimilator"),
   "cyberneticscore_count": ("build", "cyberneticscore"),
   "roboticsbay_count": ("build", "roboticsbay"),
   "warp_gate_research_count": ("research", "warp_gate_research"),
   ```

   Then update the rewards.py module docstring / inline comment that previously noted these 5 targets weren't trackable (introduced in D.6 around the `_COUNT_FIELD_TO_ACTION` definition). The new wording should make clear all 5 are now tracked; D.3's `compute_progress` normalization (`.lower()` only, preserves underscores for `warp_gate_research`) makes the matching work.

4. **`tests/test_phase_d_smoke.py`** — extend the smoke gate:
   - Update `_smoke_snapshot` so the 5 new fields default to 0 (they will via dataclass defaults; nothing to do unless overrides are added).
   - Add a new test (suggested name: `TestRewardCalculatorChain::test_all_new_count_fields_register_events`):
     - Construct a `RewardCalculator` with flag ON.
     - Call `compute_step_reward` twice with snapshots that differ on each of the 5 new fields one-at-a-time (or all at once). The second call should produce executed_actions including each new `(action, target)` tuple — verify by inspecting `calc._executed_actions` post-call.
     - Assert all 5 new tuples appear: `("build", "pylon")`, `("build", "assimilator")`, `("build", "cyberneticscore")`, `("build", "roboticsbay")`, `("research", "warp_gate_research")`.
     - This is the producer-consumer assertion: GameSnapshot field → `_COUNT_FIELD_TO_ACTION` lookup → `_executed_actions` event.
   - Optionally extend `TestUnifiedFlow::test_unified_flow_with_build_order` to set non-zero values on the new fields, demonstrating the full chain end-to-end with the new mappings active.

**Existing context.**
- D.6's `_build_order_summand` is the consumer side; D.3's `compute_progress` is the matching algorithm. Neither needs changes — only the producer side (GameSnapshot + mapping) is incomplete.
- Trajectory tolerance handles the timing difference between structure-construction-start (when `self.structures()` count rises) and trajectory target time. `robo-colossus.json` has `tolerance_seconds=60` after D.4 widened it; `4-gate-aggression.json` has `tolerance_seconds=30`. Both should absorb the natural variance between probe-start and the trajectory's target time.

**Files to modify/create.**
- `bots/current/decision_engine.py` — 5 new fields on GameSnapshot.
- `bots/current/bot.py` — 5 lines in `_build_snapshot`.
- `bots/current/learning/rewards.py` — 5 entries in `_COUNT_FIELD_TO_ACTION` + docstring update.
- `tests/test_phase_d_smoke.py` — extend smoke (1 new test minimum; optional extension to existing unified-flow test).

**Done when.**
- `uv run pytest tests/test_phase_d_smoke.py -x` runs in <3 seconds and includes ≥1 new test exercising all 5 new field→action mappings.
- `assert len(_COUNT_FIELD_TO_ACTION) == 26` (was 21).
- Existing 1677 tests still pass — `uv run pytest -x` ≥1678 (likely 1678 with one new smoke test).
- `uv run ruff check .` clean.
- `uv run mypy src bots --strict` clean (802 files).
- The rewards.py docstring/comment that previously called out the 5 untracked targets is updated.

**Depends on.** none. (Phase D shipped; this is pure additive.)

**Produces.** 5 new GameSnapshot fields, 5 new bot.py populator lines, 5 new `_COUNT_FIELD_TO_ACTION` entries, ≥1 new smoke test, updated rewards.py docstring.

---

## 4. Tests (full list)

- `tests/test_phase_d_smoke.py` (UPDATE) — add ≥1 test exercising all 5 new mappings end-to-end through `RewardCalculator._build_order_summand`.

No other test files require updates — existing tests construct `GameSnapshot` directly and use defaults for the new fields.

## 5. Validation

Step 1's `Done when` items are the validation. After the step ships, Phase D's M1 operator step measures variance with the full signal. M1 success criteria unchanged from Phase D plan: early-game (≤5 min) reward std-dev drops ≥30% AND diff-3 WR holds.

## 6. Gate

Step 1 lands and the full test suite is green. Then M1 is run; M1's gate is the real go/no-go for Phase D.

## 7. Kill criterion

If M1 still shows no variance drop after this feature ships, Phase D's premise (build-order target matching reduces variance) is genuinely wrong — kill per Phase D plan §7 (restore `reward_rules.pre-phase-d-20260520-0020.json` backup).

This feature itself has no kill criterion — it's a pure additive extension of an existing producer-consumer chain. Worst case: M1 still reveals no variance drop and these 5 fields end up unused in the OFF flag path (no behavior change).

## 8. Rollback

Single commit revert undoes the entire change:
- Revert the GameSnapshot field additions (existing constructors will pass since the 5 fields are optional).
- Revert the bot.py populator lines.
- Revert the `_COUNT_FIELD_TO_ACTION` extensions.
- Revert the test additions.

## 9. Risks and open questions

| Item | Risk | Mitigation |
|---|---|---|
| `warp_gate_research` set-check timing | `UpgradeId.WARPGATERESEARCH in self.state.upgrades` flips at completion (~t+155s after research start). Trajectory's `warp_gate_research@230` was scaled to research-start; possible ~30s offset. | Trajectory `tolerance_seconds=60` (robo-colossus) absorbs ±30s. If M1 reveals mismatch, fall back to `max(set-check, int(self.already_pending_upgrade(UpgradeId.WARPGATERESEARCH) > 0))` — one-line change. |
| `_build_snapshot` real-game test | New populator lines only run with a live SC2 client. Unit tests construct GameSnapshot directly. | Same API the existing 6 structure-count fields use; if those work these will too. Smoke gate exercises the consumer side (mapping → events) end-to-end. |
| `_COUNT_FIELD_TO_ACTION` mapping typo | Silent miss if a key typo means the field never gets looked up | Smoke test asserts all 5 expected tuples appear in `_executed_actions`; a typo on either side fails the smoke. |

## 10. `/build-phase` invocation

After `/repo-sync` mints the issue:

```powershell
uv run claude /build-phase --plan documentation/plans/phase-d-snapshot-counts-plan.md
```

`/build-phase` walks Step 1 as a single code step. No operator handoff at the end (no manual sub-section).
