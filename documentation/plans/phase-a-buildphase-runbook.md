# Phase A — /build-phase runbook

This is the `/build-phase`-compatible re-expression of **Phase A** from
`alphastar-upgrade-plan.md`. The main plan is human-led; this runbook carves
out just Phase A's validation steps into the `Type:` format the orchestrator
understands.

All SC2 training / soak steps are `operator` type — you run the PowerShell,
observe, and report PASS / BLOCKED / SKIP back to build-phase. The final 20-game
soak is `wait` type — build-phase halts after kicking it off.

Refer to `alphastar-upgrade-plan.md` Phase A for the diagnostic table, kill
criteria, and rollback procedure. This runbook is a dispatcher, not a
replacement.

## Phase A

### Step 1: Checkout feat/lstm-kl-imitation and confirm baseline tests
- **Problem:** You have uncommitted changes on master (reward_rules.json, new data dirs). Stash or commit them, then `git checkout feat/lstm-kl-imitation`, run `uv run pytest` and confirm ~834 unit tests pass. Do NOT start Phase A validation on a dirty working tree. Report PASS with test count, or BLOCKED if tests regress on the branch.
- **Type:** operator
- **Issue:** #100
- **Status:** DONE (2026-04-15) — 864 tests passing on `68a27f7` (master merged into branch, pushed to origin)

### Step 2: A.1 no-op regression
- **Problem:** On branch feat/lstm-kl-imitation with all new flags at shipped defaults (`use_imitation_init: false`, `kl_rules_coef: 0.0`, `policy_type: MlpPolicy`), run `uv run python -m alpha4gate.runner --train rl --cycles 1 --games-per-cycle 3 --difficulty 3`. Pass = cycle completes, checkpoint saves, win-rate logged, no import/shape/class-mismatch error from `_init_or_resume_model`. Capture the wall-clock of this cycle — Step 4 (A.3) needs it as a baseline for the ≤1.5× overhead check. Report PASS with wall-clock seconds, or BLOCKED with the error.
- **Type:** operator
- **Issue:** #100
- **Status:** DONE (2026-04-15) — start 16:12:50, end 16:17:45, wall-clock **295 sec** for 3 games at diff 3. Checkpoint `v1` saved, WR=0/3 (expected, no-op regression), failed_games=0. Step 4 KL-overhead gate (≤1.5×): **≤442 sec**.

### Step 3: A.2 imitation-init alone
- **Problem:** Flip `use_imitation_init: true` in `data/hyperparams.json` (leave kl_rules_coef=0.0 and policy_type=MlpPolicy). Run `uv run python -m alpha4gate.runner --train rl --cycles 1 --games-per-cycle 3 --difficulty 3 --ensure-pretrain`. Pass = log shows `--ensure-pretrain: running imitation training` → `agreement=X.XXX` → `Loading imitation-pretrained checkpoint v0_pretrain` before cycle 1, AND `Test-Path data/checkpoints/v0_pretrain.zip` is True. Then re-run without `--ensure-pretrain` to verify idempotence (log shows `Loading imitation-pretrained checkpoint v0_pretrain` without re-running imitation). Report PASS with imitation agreement score, or BLOCKED with the error (likely suspect: empty training.db or skewed action distribution — see plan's diagnostic table).
- **Type:** operator
- **Issue:** #100
- **Status:** DONE (2026-04-15) — imitation **agreement=0.915** on 94,650 transitions (100 epochs, loss=0.2827). `v0_pretrain.zip` saved. Idempotence confirmed: second run with `use_imitation_init: true` logs `Loading imitation-pretrained checkpoint v0_pretrain (class=PPO)` and skips re-training. Blob-decoder fix required to unblock (commit `d37fdfe`): historical training.db had ~40K rows where `action` was stored as `np.int64.tobytes()` BLOBs instead of integers.

### Step 4: A.3 KL-to-rules alone
- **Problem:** Revert `use_imitation_init` to false, set `kl_rules_coef: 0.1`, keep `policy_type: MlpPolicy`. Run `uv run python -m alpha4gate.runner --train rl --cycles 2 --games-per-cycle 3 --difficulty 3`. Pass = no NaN / crash AND cycle wall-clock ≤ 1.5× the Step 2 baseline (extra-pass overhead bounded). Optional bonus: check `data/training_diagnostics.json` probabilities on diagnostic states drift toward rule-engine choices across cycles. Report PASS with wall-clock ratio, or BLOCKED with symptom (NaN → drop coef to 0.05 per plan).
- **Type:** operator
- **Issue:** #100
- **Status:** DONE (2026-04-15) — 2 cycles in 502 sec total (~251 sec/cycle), **0.85× the Step 2 baseline** of 295 sec/cycle. Gate ≤1.5× (442 sec) cleared. No NaN, both checkpoints saved (v1, v2), failed_games=0. `kl_rules_coef=0.1` adds no measurable overhead.

### Step 5: A.4 LSTM alone
- **Problem:** Revert `kl_rules_coef` to 0.0, set `policy_type: MlpLstmPolicy`. LSTM checkpoints are incompatible with prior MlpPolicy checkpoints, so first `Move-Item data/checkpoints data/checkpoints.bak-pre-lstm`. Then run `uv run python -m alpha4gate.runner --train rl --cycles 2 --games-per-cycle 3 --difficulty 3`. Pass = env loop runs, hidden state threads through, cycles complete. Known failure: if `net_arch: [128, 128]` flat-list is invalid for MlpLstmPolicy, model construction crashes — fix is to change net_arch in hyperparams to `{"pi": [128], "vf": [128]}`. Log any net_arch fix in the plan's history section. Report PASS or BLOCKED with root cause.
- **Type:** operator
- **Issue:** #100
- **Status:** DONE (2026-04-15) — 2 cycles in 543 sec (~271 sec/cycle). Both checkpoints saved, failed_games=0, no NaN. **Unexpected result:** `RecurrentPPO` accepted `net_arch: [128, 128]` flat list without crash — the predicted failure mode did not materialize; no dict-form rewrite needed.

### Step 6: A.5 all three together
- **Problem:** Set `use_imitation_init: true`, `kl_rules_coef: 0.1`, keep `policy_type: MlpLstmPolicy`. Run `uv run python -m alpha4gate.runner --train rl --cycles 3 --games-per-cycle 3 --difficulty 3 --ensure-pretrain`. Pass = 3 cycles complete without crash, imitation pretrain loads cleanly into LSTM policy, no NaN in KL pass. Report PASS with final win rate across 9 games, or BLOCKED.
- **Type:** operator
- **Issue:** #100
- **Status:** DONE (2026-04-15) — 3 cycles completed, checkpoints v1/v2/v3 saved, failed_games=0, no NaN. Per-cycle WR: 0.0, 0.167, 0.167 (sliding 6-game window). `RecurrentPPOWithKL` loaded the `RecurrentPPO`-saved `v0_pretrain.zip` cleanly — the imitation+KL+LSTM class-compatibility concern did not materialize.

### Step 7: A.6 validation soak — 20 games at difficulty 3 hybrid
- **Problem:** With flags from Step 6 (full stack on), run `uv run python -m alpha4gate.runner --batch 20 --difficulty 3 --decision-mode hybrid --model-path data/checkpoints/best.zip`. This is a ~2–4 hour SC2 wall-clock run. Capture final win rate. Gate: at least one of the configs from Steps 3–6 must hit win rate ≥ 75% baseline over 20 games at difficulty 3. Deliverable is the batch log + the comparison write-up in the issue comment when you resume.
- **Type:** wait
- **Issue:** #100
- **Status:** DONE (2026-04-15) — **19/20 wins = 95% WR at difficulty 3 hybrid**. Used `data/checkpoints/v3.zip` (full-stack LSTM+KL+imitation, 9-game training from Step 6). Added `--no-claude` to bypass a CancelledError in claude_advisor batch-mode path (separate bug, not Phase A). Final 18 of 20 games won consecutively. Gate ≥75%: **PASSED**.

### Step 8: Phase A gate decision + merge
- **Problem:** Based on Step 7 soak results and Steps 2–6 outcomes, make the gate call per the plan: `(combo_passed & no_crashes & tests_green) → merge branch to master`. If gate passes: `git checkout master && git merge feat/lstm-kl-imitation`, tag `alphastar/A/final`, push tags, close issue #100. If gate fails: leave branch unmerged, append outcome to the plan's "Plan history" section (line 687+), and decide whether to investigate per the kill-criterion candidates (stateless rule-teacher lossiness, padding distribution shift). Report DONE when merged or when failure is logged.
- **Type:** operator
- **Issue:** #100
- **Status:** DONE (2026-04-15) — Gate PASSED. Merged to master (fast-forward `567f542..cfeeb99`), tagged `alphastar/A/final`, pushed, issue #100 closed. Full-stack hyperparams stashed for future use.
