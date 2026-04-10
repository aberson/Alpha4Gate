# Phase 4.5 backlog — soak run #1 triage

This file is the Phase 4.5 Step 4 ([#64](https://github.com/aberson/Alpha4Gate/issues/64))
deliverable for the **non-blocker, non-alert-tuning** findings from soak run #1
(2026-04-10). It documents the Dashboard Polish, Daemon Tuning, and Documentation
Gaps buckets — items that should be addressed but are not Step 5 entry conditions.

Source of truth for the findings themselves:
[`documentation/soak-test-runs/soak-2026-04-10.md`](../soak-test-runs/soak-2026-04-10.md).

Blocker findings (#6, #7, #10, #17) and the alert tuning finding (#14) are tracked
as separate GitHub issues and are NOT in this file:

- [#66](https://github.com/aberson/Alpha4Gate/issues/66) — SQLite thread-safety + 30% loss + cycle uniformity (consolidates findings #6, #10, #17)
- [#67](https://github.com/aberson/Alpha4Gate/issues/67) — Silent eval-game crash recovery (finding #7)
- [#68](https://github.com/aberson/Alpha4Gate/issues/68) — Alerts pipeline fired zero alerts (finding #14)

Phase 5 inputs (findings #11 and #12) are recorded in
[`always-up-plan.md` Phase 5 section](always-up-plan.md), not here.

---

## Bucket: Dashboard polish

| Finding | Sev | Description | Suggested fix |
|---|---|---|---|
| #2 | minor | At T0 the Loop tab `Next Check` field displays the backend startup time `1:17:43 PM` and has not advanced — the check-loop timer appears paused while training is in progress. Possibly expected (the timer can't advance while the daemon is mid-cycle), but operator visibility is poor. | Verify whether the timer is intentionally paused. If yes, replace the value with a string like `paused (training in progress)`. If no, fix the polling refresh. |
| #8 (partial) | major | Daemon `state` field is too coarse: `state=training` covers both PPO update phase AND 20-game promotion eval phase. From the dashboard there is no way for the operator to tell which phase the daemon is in. We spent ~57 min thinking the daemon was still in training cycles when it was actually in eval. | Split `state` into distinct values: `training_cycle`, `evaluating`, `promoting`, `idle`. Update the Loop tab to render the new values. The other half of this finding (daemon-side state machine change) is in the Daemon tuning bucket below. |

## Bucket: Daemon tuning

| Finding | Sev | Description | Suggested fix |
|---|---|---|---|
| #3 | major | `save_checkpoint()` at [`src/alpha4gate/learning/checkpoints.py:63`](../../src/alpha4gate/learning/checkpoints.py#L63) blindly appends to `manifest["checkpoints"]` without deduping by name. After this session's first cycle, `manifest.json` contains two entries both named `v1` with different metadata (smoke 4's `v1` and the current session's first `v1`). Manifest will keep growing duplicate rows on every restart that overwrites an existing checkpoint name. | Dedupe by `name` on insert. If a checkpoint with the same name already exists, replace it (latest mtime wins) instead of appending. Add a regression test in `tests/test_checkpoints.py`. |
| #8 (partial) | major | Daemon-side state machine needs `state` split (see Dashboard polish row above for the user-visible half). | Add `DaemonState` enum with `IDLE`, `TRAINING_CYCLE`, `EVALUATING`, `PROMOTING`. Update `TrainingDaemon` and the `/api/training/daemon` endpoint to surface the new field. Coordinate with the dashboard fix. |
| #9 | minor | Curriculum range mismatch: [`environment.py:209-219`](../../src/alpha4gate/learning/environment.py#L209-L219) maps `current_difficulty` 1-10 to SC2 `Difficulty` enum but `9 → CheatInsane` and `10 → CheatInsane` are both the same opponent. The curriculum supports 10 levels but only 9 are distinct. | Tighten `max_difficulty` default to 9 in `DaemonConfig`. Document that 10 was a placeholder. |
| #12 (partial) | major | Daemon deadlocks on idle: no transitions arrive while no games run. After run #1 completed, daemon went `state=idle, transitions_since_last=0`. The transition trigger (`min_transitions=500`) can never fire while idle because nothing produces transitions. The only escape is the time trigger (`min_hours_since_last=1.0`). For a 4-hour soak running cleanly, that's a hard ceiling of ~3-4 cycles maximum. | Two options, decide before Step 5: (a) the daemon runs a low-rate background self-play stream while idle to keep transitions flowing; (b) the time trigger becomes the primary trigger and the transition trigger is a "minimum batch" floor instead of a primary gate. The Phase 5 input half of this finding (whether the abstraction needs to model this) is in `always-up-plan.md`. |
| #15 | minor | Game-timeout spam loop: backend log lines 6522-9210 contain ~200 `Game timeout at NNNs ▒ sending terminal observation` entries spanning ~3.5 wall-clock minutes during a single game. The warning re-fires every game step once the game crosses its timeout threshold instead of firing once. | Add a `_timeout_warned: bool` flag in the env wrapper. Log once on transition from `not_timed_out` to `timed_out`. Search for the log line in `src/alpha4gate/learning/environment.py`. |

## Bucket: Documentation gaps

| Finding | Sev | Description | Suggested fix |
|---|---|---|---|
| #5 | minor | The soak procedure (`documentation/soak-test.md` Section 2.1) implies that deleting `data/daemon_config.json` before a soak resets daemon state to dataclass defaults. In practice the daemon recreates the file via `save_daemon_config()` after the first curriculum advancement, so deleting it doesn't actually pin the run to the defaults — it just defers the persisted state by one cycle. | Update Section 2.1 to clarify that deleting the file gives a clean *initial* state, but the daemon will persist its first decision back to the file within minutes. To pin a known final state, the operator must also pre-populate `daemon_config.json` with the desired values. |
| #13 | minor | `soak_poll.py` hits 6 endpoints every 60s; uvicorn logs each as a separate `INFO: 127.0.0.1:NNNNN - "GET /api/... HTTP/1.1" 200 OK` line. Of the 10,044 lines in this run's backend log, the vast majority are these access-log lines, drowning out real findings. | Document the grep filter (`grep -v 'INFO:.*127.0.0.1:'`) in `soak-test.md` Section 5.2. Optional code change: configure uvicorn `--log-config` to silence the access log on a specific path prefix, OR have the poller hit a single combined `/api/training/snapshot` endpoint. |
| #16 | major | Backend log captured by scrollback copy lost the first 51 minutes. Backend launched 13:17:43 but `documentation/log-soaktests-backend.txt` started at 14:08:16 — the entire training-cycles phase and first ~14 eval games are missing. We cannot now tell whether the SQLite bug (#66) affects training games or only eval games — a meaningful gap for triage. **Step 5 entry condition.** | Strengthen `soak-test.md` Section 1 (Prerequisites) and Section 3.2 (Launch): the `tee` command in Section 3.2 must be set up FROM THE START. Add a hard-required pre-flight check that verifies the tee target file exists and is being appended to within 30 seconds of backend launch. The current `documentation/soak-test.md` Section 5.2 already mentions tee but allows scrollback as a fallback — remove the fallback. The new requirement is also captured in feedback memory `feedback_tee_from_start_for_evidence.md`. |

---

## Resolved during analysis (no action)

- **#1** — was "current_difficulty: 6 despite deleted config". Actual story: dataclass-default difficulty 1, daemon advanced curriculum 5 cycles to difficulty 6, then persisted via `save_daemon_config()`. Not a bug. See run log for full reasoning.
- **#4** — was "50 games completed in <20 min". Actually mistook eval phase for training phase. Resolved into the new findings #6/#7/#8.

---

## Step 5 entry conditions (consolidated)

For convenience, the full list of items that must be true before Phase 4.5 Step 5
([#65](https://github.com/aberson/Alpha4Gate/issues/65)) re-soak can begin:

1. **#66 closed** — SQLite thread-safety fixed AND write serialization story shipped.
2. **#67 closed** — Silent eval-game crash recovery fixed; eval surfaces crash count.
3. **#68 closed** — Alerts pipeline wired up to backend ERROR-level events; pre-flight verification added to `soak-test.md`.
4. **Manifest pre-seed** — `data/checkpoints/manifest.json` must have `best` populated before launch so the promotion gate's comparison code path actually executes (Finding #11; tracked in #65 pre-flight checklist).
5. **`tee` from start** — `soak-test.md` Section 3.2 updated to make tee mandatory; operator must verify the tee file is being written to within 30s of backend launch (Finding #16; same checklist).
6. **Synthetic alert pre-flight** — operator triggers a synthetic backend ERROR at T0 and confirms the Alerts tab shows it within one poll cycle. If it doesn't, abort the run (added by #68 acceptance criteria).

Items 1-3 are blocker fixes. Items 4-6 are procedural pre-flight checks added to
[#65](https://github.com/aberson/Alpha4Gate/issues/65).
