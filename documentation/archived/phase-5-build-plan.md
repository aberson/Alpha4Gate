# Phase 5 Build Plan — Sandbox enforcement + skill integration

**Parent plan:** [alpha4gate-master-plan.md](alpha4gate-master-plan.md) — Phase 5 (line numbers stale after 2026-04-19 refactor; search "Phase 5 — Sandbox enforcement")
**Parent issue:** #105 (umbrella)
**Branch:** `master-plan/5/sandbox-enforcement` (cut from master at `070f267`)
**Baseline tag:** `master-plan/5/baseline`
**Target tag on gate pass:** `master-plan/5/final`
**Effort estimate:** 3–5 h.

## 1. What this feature does

Adds a pre-commit sandbox hook that prevents autonomous `/improve-bot-advised`
commits (tagged `[advised-auto]`) from touching anything outside `bots/current/**`.
This is the safety net that lets the autonomous improvement loop run unattended
without corrupting the versioning substrate, orchestrator, tests, or frontend.

Additionally wires cross-version Elo validation (`check_promotion()` +
`snapshot_current()`) into the `/improve-bot-advised` skill so that successful
iterations are automatically snapshotted and Elo-validated before committing.

## 2. Existing context (for fresh-context models)

- **`bots/current/`** — thin pointer package. `__init__.py` has a
  `_CurrentAliasFinder` (MetaPathFinder) that redirects `bots.current.*`
  imports to the target version (currently `v0`). `current.txt` contains
  the version name. **Do NOT import `bots.current` from `src/orchestrator/`**
  — triggers MetaPathFinder loop.
- **`src/orchestrator/snapshot.py`** — `snapshot_current(name?) -> Path`:
  copies `bots/current/` → `bots/<name>/` (auto-increments to `v{max+1}`
  if no name given), writes fresh manifest inheriting parent Elo, updates
  `bots/current/current.txt`. Returns new version dir path.
- **`src/orchestrator/ladder.py`** — `check_promotion(candidate, parent,
  games, map_name, *, elo_threshold=10.0) -> PromotionResult`: runs
  head-to-head self-play, computes Elo delta, calls `snapshot_current()`
  on success. Returns `PromotionResult` with `promoted` bool and `reason`.
- **`src/orchestrator/contracts.py`** — `PromotionResult` frozen dataclass
  with `candidate`, `parent`, `elo_delta`, `games_played`, `wr_vs_sc2`,
  `promoted`, `reason`.
- **`/improve-bot-advised` skill** (`.claude/skills/improve-bot-advised/SKILL.md`,
  766 lines) — autonomous outer loop: observe → analyze → pick improvement →
  execute → validate → commit. Phase 6 commits with `[advised-auto]` tag.
  Dashboard bridge: `data/advised_run_state.json`, `data/advised_run_control.json`.
  `--self-improve-code` gates all code changes.
- **`bots/v0/data/hyperparams.json`** — PPO hyperparams. No promotion
  thresholds yet (hardcoded in `ladder.py` and `promotion.py`).
- **No `.pre-commit-config.yaml` exists** — must be created.
- **1011 pytest tests**, 129 vitest, mypy strict (62 files), ruff clean.
- **Windows 11**, Python 3.12, `uv` package manager.

**Critical constraints:**
- Git on Windows returns forward-slash paths in `git diff --cached --name-only`.
  The hook must normalize all paths to forward slashes before prefix matching.
- `pre-commit` with `repo: local` + `language: system` uses the active Python,
  avoiding virtualenv conflicts with `uv`.
- `snapshot_current()` updates `bots/current/current.txt` as a side effect —
  after promotion, "current" IS the new version. Callers that resolve "current"
  must do so BEFORE calling `check_promotion`.

## 3. Scope

**In scope:**

- `scripts/check_sandbox.py` — sandbox enforcement hook.
- `.pre-commit-config.yaml` — wire the hook as a pre-commit hook via
  `repo: local`.
- `pre-commit` added as dev dependency in `pyproject.toml`.
- `tests/test_sandbox_hook.py` — comprehensive tests.
- `/improve-bot-advised` SKILL.md updates: run-start banner, `ADVISED_AUTO=1`
  env var before commits, `check_promotion()` wiring in Phase 5→6 transition.

**Out of scope:**

- Server-side hook on the remote (fallback if Windows pre-commit is unreliable).
- Daemon integration (wiring promotion into daemon idle loop).
- Moving promotion thresholds into `hyperparams.json`.
- PFSP rewire.
- Changes to `check_promotion()` itself — it already works correctly.

## 4. Impact analysis

| Area | Files | Nature of change |
|------|-------|------------------|
| `scripts/check_sandbox.py` | 1 file | **Create** sandbox enforcement hook |
| `.pre-commit-config.yaml` | 1 file | **Create** pre-commit config |
| `pyproject.toml` | 1 file | **Extend** with `pre-commit` dev dependency |
| `tests/test_sandbox_hook.py` | 1 file | **Create** hook tests |
| `.claude/skills/improve-bot-advised/SKILL.md` | 1 file | **Modify** banner + promotion wiring |

## 5. Design decisions

### 5.1 Pre-commit framework with `repo: local`

Using the `pre-commit` framework (not a raw `.git/hooks/` script) because:
- The master plan explicitly references `.pre-commit-config.yaml`.
- `repo: local` + `language: system` avoids extra virtualenvs and works with
  `uv`'s active environment.
- Standard, well-known pattern. Easy to disable (remove the entry).
- Alternative considered: standalone `.git/hooks/pre-commit` symlink. Rejected
  because it's less portable and harder to version-control.

### 5.2 Environment variable for `[advised-auto]` detection

The hook checks `ADVISED_AUTO=1` env var to decide whether to enforce the sandbox.
If the env var is unset or not `"1"`, the hook exits 0 immediately (human commit
passthrough). This is simpler and more reliable than parsing commit messages at
pre-commit time (before the message is finalized).

`/improve-bot-advised` Phase 6 sets `ADVISED_AUTO=1` before running `git commit`
and includes `[advised-auto]` in the commit message. The tag in the commit
message is for human readability and git-log filtering — the hook does not
depend on it (it checks the env var only). **Note:** The current SKILL.md does
NOT yet include the `[advised-auto]` tag or `ADVISED_AUTO` env var — both must
be added in step 5.4.

### 5.3 Path matching logic

Staged file paths from `git diff --cached --name-only` are normalized to
forward slashes (Git on Windows already does this, but we normalize explicitly
for safety). A file is **allowed** if its path starts with `bots/current/`.
Everything else is **forbidden** when `ADVISED_AUTO=1`.

Edge case: `bots/current/../orchestrator/foo.py` — the hook must resolve `..`
segments before matching. Use `posixpath.normpath()` which collapses `..`
segments purely on the string without touching the filesystem.
(`PurePosixPath` has no `resolve()` — that's only on concrete `Path`.)

### 5.4 Promotion wiring in `/improve-bot-advised`

After the skill's Phase 5 (validate — run games, compare win rate vs baseline),
if the iteration passed, Phase 6 now:

1. Resolves the current version name (the "prior best") BEFORE any mutation.
2. Calls `check_promotion(candidate="current", parent=<prior_best>,
   games=20, elo_threshold=10.0)` via the Python API.
3. If `PromotionResult.promoted` is True: `snapshot_current()` has already
   been called by `check_promotion()`. Proceed to commit with `ADVISED_AUTO=1`.
4. If promotion fails: log the reason, skip the commit, report as
   "iteration passed WR validation but failed Elo gate".

This ensures every `[advised-auto]` commit corresponds to an Elo-validated
version snapshot.

## 6. Build steps

### Step 5.1: Sandbox hook script
- **Issue:** #118
- **Status:** DONE (2026-04-17)
- **Problem:** Create `scripts/check_sandbox.py` that enforces the sandbox for
  `[advised-auto]` commits. When `ADVISED_AUTO=1` env var is set, inspect staged
  files via `git diff --cached --name-only`. Allow only files under
  `bots/current/`. Reject anything else with a clear error message listing the
  forbidden paths. Exit 0 if `ADVISED_AUTO` is unset (human commit passthrough).
  Normalize paths to forward slashes and collapse `..` segments via
  `posixpath.normpath()` before matching. Handle empty staging area
  (ADVISED_AUTO=1 but nothing staged) as vacuously allowed (exit 0).
  Include `#!/usr/bin/env python` shebang for cross-platform compatibility.
  Script must be runnable as `python scripts/check_sandbox.py` (exit code 0/1).
- **Flags:** `--reviewers code`
- **Produces:** `scripts/check_sandbox.py`
- **Done when:** `ADVISED_AUTO=1 python scripts/check_sandbox.py` exits 1 when
  forbidden files are staged; exits 0 when only `bots/current/**` files are
  staged; exits 0 when env var is unset regardless of staged files.
- **Depends on:** none

### Step 5.2: Sandbox hook tests
- **Issue:** #119
- **Status:** DONE (2026-04-17)
- **Problem:** Create `tests/test_sandbox_hook.py` with comprehensive tests for
  `scripts/check_sandbox.py`. Test cases: (a) env var unset → passthrough (exit 0),
  (b) env var set + only `bots/current/foo.py` staged → allowed (exit 0),
  (c) env var set + `src/orchestrator/ladder.py` staged → blocked (exit 1),
  (d) env var set + `pyproject.toml` staged → blocked (exit 1),
  (e) env var set + `bots/current/learning/trainer.py` + `tests/test_foo.py`
  staged → blocked (exit 1, mixed allowed + forbidden),
  (f) path normalization: `bots/current/../orchestrator/foo.py` → blocked,
  (g) nested `bots/current/subdir/deep/file.py` → allowed,
  (h) `bots/v0/foo.py` → blocked (only `bots/current/` is allowed, not other
  versions),
  (i) env var set + nothing staged → allowed (exit 0, vacuously true).
  Use subprocess to invoke the script (matching real pre-commit
  behavior) or import and mock `git diff` output.
- **Flags:** `--reviewers code`
- **Produces:** `tests/test_sandbox_hook.py`
- **Done when:** All tests pass. `uv run pytest tests/test_sandbox_hook.py -v`
  green.
- **Depends on:** 5.1

### Step 5.3: Pre-commit wiring
- **Issue:** #120
- **Status:** DONE (2026-04-17)
- **Problem:** Add `pre-commit` as a dev dependency in `pyproject.toml`
  under `[project.optional-dependencies]` `dev` extra (matching existing
  pattern — pytest, ruff, mypy, httpx are already there). Create
  `.pre-commit-config.yaml` with a `repo: local` entry that runs
  `python scripts/check_sandbox.py` as a pre-commit hook with
  `language: system`. Run `uv sync` then `uv run pre-commit install` to
  wire the hook into `.git/hooks/`. Verify with
  `uv run pre-commit run --all-files` (should pass since `ADVISED_AUTO` is
  unset). Then verify with `ADVISED_AUTO=1 uv run pre-commit run --all-files`
  against a staged forbidden file (should fail).
- **Produces:** `.pre-commit-config.yaml`, modified `pyproject.toml`,
  `.git/hooks/pre-commit` (via `pre-commit install`)
- **Done when:** `uv run pre-commit run --all-files` passes cleanly.
  Manual test with env var confirms blocking behavior. `pre-commit install`
  has been run so future `git commit` invocations trigger the hook.
- **Depends on:** 5.1

### Step 5.4: Skill updates — banner + promotion wiring
- **Issue:** #121
- **Status:** DONE (2026-04-17)
- **Problem:** Update `.claude/skills/improve-bot-advised/SKILL.md`:
  (a) In Phase 0 (bootstrap), add a run-start banner that prints:
  "I can edit: bots/current/**. I cannot edit: src/orchestrator/,
  pyproject.toml, tests/, frontend/, scripts/."
  (b) In Phase 6 (commit), add `[advised-auto]` to the commit message
  template (it is NOT there yet) and add instruction to set `ADVISED_AUTO=1`
  environment variable before running `git commit`.
  (c) In Phase 5→6 transition (after WR validation passes), add instruction
  to call `check_promotion()` against the prior best version. Resolve the
  current version name BEFORE any mutation. If `PromotionResult.promoted`
  is False, skip the commit and log "iteration passed WR but failed Elo gate".
  If True, `snapshot_current()` has already been called — proceed to commit.
  (d) Ensure the skill's `--self-improve-code` path only edits files under
  `bots/current/` (reinforcing the sandbox contract).
- **Produces:** Updated SKILL.md
- **Done when:** Skill spec review confirms all four additions are present
  and consistent with `check_sandbox.py` behavior.
- **Depends on:** none (can run in parallel with 5.1–5.3)

### Step 5.5: Gate verification
- **Issue:** #122
- **Type:** operator
- **Status:** DONE (2026-04-17)
- **Problem:** Verify the two gate criteria from the master plan:
  (a) Stage `pyproject.toml` for commit, set `ADVISED_AUTO=1`, run
  `uv run pre-commit run --all-files` → hook blocks with clear error.
  (b) Stage `bots/current/learning/trainer.py` (or create a dummy change),
  set `ADVISED_AUTO=1`, run `uv run pre-commit run --all-files` → hook allows.
  (c) Verify run-start banner text in SKILL.md matches the allowed/forbidden
  lists.
  (d) Run full test suite: `uv run pytest --tb=no -q` (expect 1011+ tests),
  `uv run mypy src bots --strict`, `uv run ruff check .`
- **Produces:** Verification log
- **Done when:** Both gate criteria pass. Full test suite green. mypy + ruff
  clean.
- **Depends on:** 5.1, 5.2, 5.3, 5.4

## 7. Risks and open questions

| Item | Risk | Mitigation |
|------|------|------------|
| Windows pre-commit reliability | Line endings, path separators, or shell quoting may cause false positives/negatives | Normalize all paths to forward slashes; test on Windows explicitly in step 5.2; kill criterion: fall back to server-side hook |
| `pre-commit` + `uv` interaction | `pre-commit` may not find the right Python | Use `language: system` so it uses the active env; verify in step 5.3 |
| Path traversal bypass | `bots/current/../orchestrator/` could bypass prefix check | Collapse `..` segments before matching; explicit test case in step 5.2 |
| `check_promotion()` requires SC2 | Elo validation runs self-play games which need SC2 installed | This is expected — `/improve-bot-advised` always runs on the dev machine with SC2. Document the requirement in the skill banner |
| Env var leak | If `ADVISED_AUTO=1` leaks to a human terminal session, their commits get sandboxed | Hook prints a clear message explaining why the commit was blocked and how to unset the var |
| `--no-verify` bypass | `git commit --no-verify` skips pre-commit hooks entirely | This is the intentional developer escape hatch. Claude Code must never use `--no-verify` unprompted (per project feedback). Document as known escape in SKILL.md |

## 8. Testing strategy

**New tests:**
- `tests/test_sandbox_hook.py` — 8+ test cases covering allowed paths,
  forbidden paths, passthrough, normalization, edge cases (see step 5.2).

**Existing tests that should NOT break:**
- All 1011 existing pytest tests are unaffected (no production code changes).
- All 129 vitest tests are unaffected (no frontend changes).
- mypy strict and ruff should remain clean.

**End-to-end verification:**
- Step 5.5 is a manual gate verification matching the master plan's gate criteria.
- No soak test or long-running observation needed — this feature is a static
  enforcement layer (pre-commit hook), not an autonomous/background behavior.
  The autonomous behavior it protects (`/improve-bot-advised`) is tested
  separately via that skill's own validation phases.
