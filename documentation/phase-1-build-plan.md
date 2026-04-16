# Phase 1 Build Plan — Move full stack to `bots/v0/` + scaffold orchestrator

**Parent plan:** [alpha4gate-master-plan.md](plans/alpha4gate-master-plan.md) Phase 1 (lines 331–446)
**Parent issue:** #107
**Branch:** `master-plan/1/bots-v0-migration` (cut from master at `0b67b30`)
**Baseline tag:** `master-plan/1/baseline`
**Target tag on gate pass:** `master-plan/1/final`
**Effort estimate:** 12–16 h across multiple sessions.

## 1. What this feature does

Packages the current full-stack bot as a self-contained `bots/v0/` directory — bot code, learning pipeline, API, reward engine, and per-version runtime state — so future versions (`bots/v1/`, `bots/v2/`, …) are drop-in replacements playable against each other via the Phase 0 subprocess self-play mechanism. In parallel, scaffolds `src/orchestrator/` with frozen contracts and a data-path registry so per-version state can be resolved without hardcoded `data/` paths. Also fixes always-up Finding #11 (unconditional bootstrap promotion in `PromotionManager`) as a folded-in cleanup so the first post-merge promotion exercises the real comparison path.

## 2. Existing context (for fresh-context models)

- **Current layout:** `src/alpha4gate/` with ~52 Python modules (25 top-level + 7 under `commands/` + 18 under `learning/`), ~864 tests in `tests/` (50 test files), 126 frontend tests, React+Vite frontend under `frontend/`.
- **Data dir:** `data/` holds runtime state (training.db, checkpoints/, reward_rules.json, hyperparams.json, reward_logs/, daemon_config.json, promotion_history.json, advised_run_state.json, advised_run_control.json, stats.json) intermixed with 30+ backup snapshots (`*.bak`, `*.pre-advised-*`, `*.pre-run6-*`). Entire directory is gitignored per CLAUDE.md.
- **Frontend has NO direct `data/` path coupling** — zero references to `advised_run_state` / `advised_run_control` / `/data/` under `frontend/src/`. All fetches go via API endpoints. Registry redirect can be confined to Python.
- **Phase 0 already shipped** (tag `master-plan/0/final`, commit `0b67b30`): `scripts/spike_subprocess_selfplay.py` + `scripts/spike_bot_stub.py` + `documentation/wiki/subprocess-selfplay.md` prove subprocess self-play works via `BotProcess` + `Proxy`. Phase 1 must preserve that mechanism unchanged.
- **Finding #11** (from always-up Phase 4.5 soak): `PromotionManager` short-circuits to "promote" when `manifest.best` is null, so the comparison path was never exercised in any soak. Phase 1 pre-seeds `bots/v0/manifest.json` with real `best`/`previous_best` and adds a test that rejects null-manifests.

## 3. Scope

**In scope:**

- Scaffold `src/orchestrator/` (registry, contracts, snapshot, selfplay, ladder stubs).
- Move `src/alpha4gate/` → `bots/v0/` wholesale; rewrite ~200 internal import sites.
- Introduce `bots/current/` as a **thin pointer package** (not a symlink, not a copy) that aliases `sys.modules["bots.current"]` to `bots.<name>` based on `bots/current.txt`.
- Migrate HOT data files (~10) from `data/` → `bots/v0/data/`. Leave backups at repo root.
- Pre-seed `bots/v0/manifest.json` with `best` / `previous_best` so Finding #11's comparison path runs on first promotion.
- Add `bots/v0/__main__.py` implementing the ladder CLI contract from Phase 0 (`--role`, `--map`, `--GamePort`, `--LadderServer`, `--StartPort`, `--result-out`).
- Update `pyproject.toml` to package `bots/v0` (plus any `bots/vN/` pattern the snapshot tool will follow in Phase 2).
- Rewrite all test imports from `alpha4gate.*` to `bots.v0.*`.
- Verify dashboard + daemon + Phase 0 spike all still work from the new paths.

**Out of scope:**

- Per-version venvs (deferred decision; shared repo deps).
- Automated cross-version promotion (Phase 4).
- `scripts/snapshot_bot.py` + registry CLI (Phase 2).
- Moving `scripts/` or `frontend/` (stay at repo root; those are shared tooling).
- Deleting backup snapshots at repo root (they're audit trail, already gitignored).

## 4. Impact analysis

| Area | Files | Nature of change |
|------|-------|------------------|
| `src/alpha4gate/` (25 top-level .py) | `api.py`, `bot.py`, `config.py`, `connection.py`, `runner.py`, … | **Move** to `bots/v0/` + rename imports |
| `src/alpha4gate/commands/` (7 files) | `dispatch_guard.py`, `executor.py`, `interpreter.py`, … | **Move** + rename |
| `src/alpha4gate/learning/` (18 files) | `daemon.py`, `trainer.py`, `promotion.py`, … | **Move** + rename |
| Data-path sites | 15 modules hardcode `data/`: `config.py`, `api.py`, `runner.py`, `audit_log.py`, `batch_runner.py`, `claude_advisor.py`, `process_registry.py`, `learning/{trainer,database,promotion,evaluator,environment,daemon,rollback,reward_aggregator}.py` | **Refactor** to use `orchestrator.registry.resolve_data_path` |
| `PromotionManager` (Finding #11) | `src/alpha4gate/learning/promotion.py` | **Refactor** — reject null-manifest bootstrap |
| `tests/` (50 files) | All `from alpha4gate...` imports | **Rewrite** imports to `from bots.v0...` |
| `pyproject.toml` | `[tool.hatch.build.targets.wheel]`, `[tool.mypy]`, `[tool.ruff]` | **Update** package paths |
| `pyproject.toml` entry points | (none currently) | **Add** `bots/v0` + `src/orchestrator` packages |
| `data/` | Hot files (10) | **Move** to `bots/v0/data/`; backups stay at root |
| `frontend/` | — | **None expected.** API endpoints change internally; frontend fetches unchanged. Verify in smoke gate. |
| `scripts/` | `spike_bot_stub.py`, `spike_subprocess_selfplay.py`, `start-dev.sh`, etc. | **Audit** for hardcoded paths; update any that break. |
| `.claude/skills/` | `improve-bot/`, `improve-bot-advised/`, etc. | **Audit** for hardcoded `src/alpha4gate/` paths. |
| `CLAUDE.md`, `AGENTS.md`, `documentation/wiki/` | `architecture.md`, `training-pipeline.md`, `frontend.md`, etc. | **Update** path references post-merge (separate doc pass) |

## 5. New components

| Path | Purpose |
|------|---------|
| `src/orchestrator/__init__.py` | Package marker. |
| `src/orchestrator/registry.py` | `current_version()`, `get_version_dir(name)`, `resolve_data_path(filename)`, `get_manifest(version)`. Filled in this phase. |
| `src/orchestrator/contracts.py` | Frozen dataclasses: `BotSpawnArgs`, `MatchResult`, `Manifest`, `VersionFingerprint`. |
| `src/orchestrator/snapshot.py` | Stub with docstrings. Phase 2 fills it. |
| `src/orchestrator/selfplay.py` | Stub — will absorb the Phase 0 port-collision patch + run match wrapper in Phase 3. |
| `src/orchestrator/ladder.py` | Stub. Phase 4 fills it. |
| `bots/__init__.py` | Namespace package marker. |
| `bots/v0/` | Copy of `src/alpha4gate/` with internal imports rewritten to `bots.v0.*`. |
| `bots/v0/__main__.py` | Ladder CLI entry point matching Phase 0's contract. Reuses `Alpha4GateBot` and the ladder argparse from `spike_bot_stub.py`. |
| `bots/v0/VERSION` | Literal `v0`. |
| `bots/v0/manifest.json` | `{best, previous_best, parent, git_sha, timestamp, elo, feature_dim, action_space}` — pre-seeded with current best checkpoint so Finding #11's comparison path runs on first promotion. |
| `bots/v0/data/` | Hot runtime state moved from `data/`. |
| `bots/current/__init__.py` | Reads `bots/current.txt` and sets `sys.modules["bots.current"] = bots.<name>` so `from bots.current.learning.database import TrainingDB` works. |
| `bots/current/__main__.py` | Reads `bots/current.txt`, runs `bots.<name>` as main. Enables `python -m bots.current --map Simple64`. |
| `bots/current.txt` | Literal `v0`. Phase 2's snapshot tool rewrites this atomically. |
| `tests/test_contracts.py` | Round-trip typed dataclasses through JSON. |
| `tests/test_current_pointer.py` | Verify `bots.current` aliases to `bots.v0` on import; submodule imports resolve. |
| `tests/test_registry.py` | `resolve_data_path` returns `bots/v0/data/<f>` when file exists there, else `data/<f>` fallback. |
| `tests/test_bootstrap_promotion.py` | `PromotionManager` refuses to promote when manifest `best` is null; accepts when pre-seeded. |

## 6. Design decisions

### 6.1 Checkpointed sub-commits over one massive commit

The master plan recommends a single big commit to avoid interim broken state. We're taking the opposite approach: **each step ends on a green `uv run pytest` and is its own commit.** Rationale: the memory entry `feedback_improve-bot_wall_clock_leak.md` warns against long undifferentiated phases; a green-between-steps history enables `git bisect` if Phase 1 introduces a regression later, and each commit is individually reviewable. The only step that cannot be incrementally green is Step 1.4 (the copy + internal-rename), but that step ends with BOTH `src/alpha4gate/*` and `bots/v0/*` importable and tests still running against the old tree — so it's green despite being large.

### 6.2 `bots/current/` as a thin pointer package, not a symlink

Windows symlinks require admin or dev-mode. Full copy bloats the repo and duplicates runtime state. Instead, `bots/current/__init__.py` reads `bots/current.txt` and does `sys.modules[__name__] = importlib.import_module(f"bots.{name}")`. This makes `bots.current` AND every `bots.current.<submodule>` alias transparently to `bots.<name>`. `python -m bots.current` works via a parallel `__main__.py`. Phase 2's snapshot tool becomes a two-line change: copy `bots/vN/` → `bots/vN+1/`, rewrite `bots/current.txt`.

### 6.3 Registry data-path resolution with fallback

`src/orchestrator/registry.py` exposes `resolve_data_path(filename: str, version: str | None = None) -> Path`. If `version` is None, reads `bots/current.txt`. Returns `bots/<version>/data/<filename>` if that file exists, else falls back to `data/<filename>`. This lets us refactor the 15 data-path sites BEFORE moving the data files — during the transitional window (Steps 1.7 vs 1.8) tests still pass with data at the old location. After Step 1.8 moves the hot files, the same code finds them at the new location automatically.

### 6.4 Hot-only data migration

Move only the ~10 active runtime files. Leave all `*.bak`, `*.pre-advised-*`, `*.pre-run6-*`, `checkpoints.pre-lstm/`, `checkpoints.pre-run6-backup/`, `training.pre-reset-*.db`, `training.pre-run6-backup.db` at repo-root `data/` as audit trail. They're already gitignored. Alternative considered: moving all under `bots/v0/data/archive/`; rejected because the backups span multiple cycles of improvement and don't belong to one version.

### 6.5 `phase0_spike/` stays at repo root

Phase 0's `data/phase0_spike/` artifacts are orchestrator evidence, not per-version state. They belong alongside the future `data/selfplay_results.jsonl` and `data/bot_ladder.json`. No move in this phase.

### 6.6 Finding #11 fix shipped test-first

Following TDD: write the failing test first (`test_bootstrap_promotion.py` — asserts null-manifest bootstrap is rejected), then change `PromotionManager` to enforce it. Pre-seed `bots/v0/manifest.json` with real `best`/`previous_best` values sourced from `data/promotion_history.json` so the first real promotion hits the comparison path. Use `/build-step-tdd` flag (`--tdd`) for this step.

### 6.7 Smoke gate before delete

Step 1.9 is a dedicated smoke-gate step (Type: operator) that runs a real 1-minute SC2 game via `python -m bots.current --map Simple64 --difficulty 1` AND re-runs the Phase 0 spike AND verifies the dashboard renders all 9 tabs. Only after this passes does Step 1.10 delete `src/alpha4gate/` and rewrite test imports. This catches producer/consumer drift between the registry, the env, and the DB that unit tests miss.

## 7. Build steps

### Step 1.1: Scaffold `src/orchestrator/` with empty stubs
- **Status:** DONE (2026-04-15)
- **Problem:** Create `src/orchestrator/` as a new Python package with empty-but-documented stubs for `registry.py`, `contracts.py`, `snapshot.py`, `selfplay.py`, `ladder.py`. Add `src/orchestrator/__init__.py`. Update `pyproject.toml` so hatch packages, mypy, and ruff all see the new package. Do NOT implement any behavior; docstrings only. Expected outcome: `uv run pytest` still green (864 tests), `uv run mypy src` green, `uv run ruff check .` green. No new tests.
- **Issue:** #107 (Phase 1 umbrella; this step posts a comment, not its own issue)
- **Flags:** `--reviewers auto`
- **Produces:** `src/orchestrator/{__init__.py,registry.py,contracts.py,snapshot.py,selfplay.py,ladder.py}`, updated `pyproject.toml`.
- **Done when:** All 864 tests pass, mypy strict green, ruff green, `python -c "import orchestrator.registry"` succeeds.
- **Depends on:** none.

### Step 1.2: Define frozen contracts in `src/orchestrator/contracts.py`
- **Problem:** Fill `contracts.py` with typed dataclasses that freeze the cross-version interfaces. `BotSpawnArgs` (role, map, sc2_connect, result_out, seed). `MatchResult` (version, match_id, outcome, duration_s, error). `Manifest` (version, best, previous_best, parent, git_sha, timestamp, elo, feature_dim, action_space). `VersionFingerprint` (feature_dim, action_space_size, obs_spec_hash). Include `to_json` / `from_json` roundtrip methods. Add `tests/test_contracts.py` verifying every field type, required-vs-optional, and roundtrip fidelity.
- **Issue:** #107
- **Flags:** `--reviewers auto`
- **Produces:** filled `src/orchestrator/contracts.py`, new `tests/test_contracts.py`.
- **Done when:** new tests pass, existing 864 still pass, mypy strict green.
- **Depends on:** 1.1.

### Step 1.3: Finding #11 — PromotionManager rejects null-manifest bootstrap (TDD)
- **Problem:** Fix always-up Finding #11. First write `tests/test_bootstrap_promotion.py` with two cases: (a) `PromotionManager` receives a manifest with `best=None` and raises `ValueError("manifest not seeded — refusing to bootstrap-promote")`; (b) receives a seeded manifest and runs the real WR-delta comparison path end-to-end on a temp SQLite. The test MUST fail against current code. Then edit `src/alpha4gate/learning/promotion.py` to enforce the seeded-manifest invariant. Do NOT pre-seed `bots/v0/manifest.json` in this step (that happens in 1.8 when v0 exists) — the test creates its own fixture manifests.
- **Issue:** #107
- **Flags:** `--tdd`
- **Produces:** new `tests/test_bootstrap_promotion.py`, modified `src/alpha4gate/learning/promotion.py`.
- **Done when:** new test red → green, existing 864 still pass, mypy strict green.
- **Depends on:** none (orthogonal to 1.1/1.2, but sequenced here because it's the last pre-migration additive step).

### Step 1.4: Copy `src/alpha4gate/` → `bots/v0/` with internal rename
- **Problem:** Create `bots/__init__.py` (namespace marker) and copy every file under `src/alpha4gate/` to `bots/v0/` preserving directory structure. Rewrite all `from alpha4gate.X import Y` and `from alpha4gate import X` inside the COPY (bots/v0/) to `from bots.v0.X import Y`. Do NOT touch `src/alpha4gate/` — it keeps running tests. Update `pyproject.toml` `[tool.hatch.build.targets.wheel]` packages to include both `src/alpha4gate` AND `bots/v0` so both import paths resolve. Update mypy to also see `bots/`. Verify: both `python -c "from alpha4gate.bot import Alpha4GateBot"` and `python -c "from bots.v0.bot import Alpha4GateBot"` succeed. 864 existing tests keep running against `src/alpha4gate.*`. No test changes yet.
- **Issue:** #107
- **Flags:** `--reviewers code --isolation worktree`
- **Produces:** `bots/__init__.py`, `bots/v0/**` (mirror of `src/alpha4gate/` with internal imports rewritten), `bots/v0/VERSION` (="v0"), updated `pyproject.toml`.
- **Done when:** both import paths work, 864 tests pass, mypy strict green for BOTH trees, ruff green.
- **Depends on:** 1.1, 1.2, 1.3.

### Step 1.5: Add `bots/v0/__main__.py` ladder entry point
- **Problem:** Implement `python -m bots.v0 --role {p1|p2|solo} --map NAME --GamePort N --LadderServer H --StartPort N --result-out PATH --seed N` matching the contract Phase 0 proved out. For `--role solo`, it runs the current `runner.py --map X --difficulty N` behavior. For `--role p1` or `--role p2`, it connects to an already-hosted match via `play_from_websocket` using the Phase 0 portconfig reconstruction logic. Keep the full Alpha4GateBot stack (not a stub). Add `tests/test_bots_v0_main.py` with argparse-level tests (happy paths + required-arg validation) and one `@pytest.mark.sc2` smoke test.
- **Issue:** #107
- **Flags:** `--reviewers auto`
- **Produces:** `bots/v0/__main__.py`, `tests/test_bots_v0_main.py`.
- **Done when:** argparse tests pass, 864+previous tests still pass, `python -m bots.v0 --help` exits 0.
- **Depends on:** 1.4.

### Step 1.6: Add `bots/current/` thin pointer package
- **Problem:** Create `bots/current.txt` (content: `v0`). Create `bots/current/__init__.py` that reads `current.txt` and does `sys.modules[__name__] = importlib.import_module(f"bots.{name}")`. Create `bots/current/__main__.py` that reads `current.txt` and `runpy.run_module` the target version with `run_name="__main__"`. Add `tests/test_current_pointer.py` verifying: (a) `import bots.current` aliases to `bots.v0`; (b) `from bots.current.learning.database import TrainingDB` resolves; (c) swapping `current.txt` to a non-existent version raises a clear error.
- **Issue:** #107
- **Flags:** `--reviewers auto`
- **Produces:** `bots/current.txt`, `bots/current/{__init__.py,__main__.py}`, `tests/test_current_pointer.py`.
- **Done when:** new tests pass, `python -m bots.current --help` delegates to `python -m bots.v0 --help`.
- **Depends on:** 1.4, 1.5.

### Step 1.7: Fill `src/orchestrator/registry.py` with data-path resolver
- **Problem:** Implement `current_version() -> str` (reads `bots/current.txt`), `get_version_dir(v) -> Path` (returns `bots/<v>`), `resolve_data_path(filename: str, version: str | None = None) -> Path` (returns `bots/<v>/data/<filename>` if the file exists there, else `data/<filename>` as fallback), `get_manifest(v) -> Manifest` (loads + validates via `contracts.Manifest`). Add `tests/test_registry.py` covering: happy path, fallback when per-version file absent, error when version dir doesn't exist, manifest load validates required fields.
- **Issue:** #107
- **Flags:** `--reviewers auto`
- **Produces:** filled `src/orchestrator/registry.py`, new `tests/test_registry.py`.
- **Done when:** new tests pass, 864+previous still pass, mypy strict green.
- **Depends on:** 1.1, 1.2, 1.6.

### Step 1.8: Migrate 15 data-path sites to use registry + move hot data files + pre-seed manifest
- **Problem:** Sweep the 15 modules that hardcode `data/` paths (`config.py`, `api.py`, `runner.py`, `audit_log.py`, `batch_runner.py`, `claude_advisor.py`, `process_registry.py`, `learning/{trainer,database,promotion,evaluator,environment,daemon,rollback,reward_aggregator}.py`) and rewrite each to use `orchestrator.registry.resolve_data_path(...)`. Apply the rewrite in BOTH `src/alpha4gate/` and `bots/v0/`. With the registry's fallback behavior, this step alone doesn't break anything. THEN move the hot data files: `training.db`, `checkpoints/`, `reward_rules.json`, `hyperparams.json`, `reward_logs/`, `daemon_config.json`, `promotion_history.json`, `advised_run_state.json`, `advised_run_control.json`, `stats.json` → `bots/v0/data/`. THEN pre-seed `bots/v0/manifest.json` with `best` = the current best checkpoint name from `promotion_history.json`, `previous_best` = the prior entry if one exists else same-as-best, `feature_dim` = 24, `action_space` = "Discrete(6)", `git_sha` = current HEAD. Leave all backup snapshots (`*.bak`, `*.pre-*`) at repo root. This step is split into three git commits: (a) registry refactor, (b) file move, (c) manifest seed.
- **Issue:** #107
- **Flags:** `--reviewers code --isolation worktree`
- **Produces:** 15 modified files across both trees, moved files under `bots/v0/data/`, seeded `bots/v0/manifest.json`.
- **Done when:** 864+previous tests still pass, mypy strict green, `ls bots/v0/data/training.db` resolves.
- **Depends on:** 1.7.

### Step 1.9: Smoke gate — full system against new layout
- **Type:** operator
- **Problem:** Human-run end-to-end verification that the registry + migrated data + bots.current pointer all compose correctly, BEFORE the destructive Step 1.10 runs. Checklist: (a) `uv run python -m bots.current --map Simple64 --difficulty 1` plays a game to completion; (b) `uv run python -m alpha4gate.runner --serve` starts the API from repo root; (c) dashboard loads all 9 tabs; (d) Daemon start → one training cycle → one eval → one promotion candidate persists to `bots/v0/data/training.db`; (e) Phase 0 spike (`uv run python scripts/spike_subprocess_selfplay.py`) still PASSES; (f) `/a4g-dashboard-check` skill captures screenshots and reports no regressions. Any failure = stop and triage; do NOT proceed to 1.10.
- **Issue:** #107
- **Produces:** `documentation/phase-1-smoke-gate.md` with human-signed checklist + screenshots.
- **Done when:** all six checklist items pass, no orphan SC2 processes, user signs off in writing.
- **Depends on:** 1.8.

### Step 1.10: Delete `src/alpha4gate/`; rewrite all test imports
- **Problem:** Remove `src/alpha4gate/` entirely (`git rm -r src/alpha4gate`). Rewrite every `from alpha4gate...` or `import alpha4gate` in `tests/` to `from bots.v0...` / `import bots.v0`. Update `pyproject.toml` `[tool.hatch.build.targets.wheel]` packages to drop `src/alpha4gate` — only `bots/v0` + `src/orchestrator` remain. Update `[tool.mypy]` packages similarly. Update `[tool.ruff] src` to `["src", "bots", "tests"]`. Audit `scripts/` and `.claude/skills/` for any hardcoded `alpha4gate` import paths; update or leave a migration note.
- **Issue:** #107
- **Flags:** `--reviewers code --isolation worktree`
- **Produces:** `src/alpha4gate/` removed, 50+ test files with rewritten imports, updated `pyproject.toml`.
- **Done when:** `uv run pytest` passes all 864+new tests, `uv run mypy src bots` green, `uv run ruff check .` green, grep for `alpha4gate` in `src/` / `bots/` / `tests/` returns zero hits.
- **Depends on:** 1.9.

### Step 1.11: Final full-stack verification + docs update
- **Type:** operator
- **Problem:** Repeat the Step 1.9 checklist on the deleted-old-tree state. Update `CLAUDE.md`, `AGENTS.md`, and the affected wiki pages (`architecture.md`, `training-pipeline.md`, `frontend.md`, `index.md`) to reflect the new layout. Update master plan's Baseline section to note Phase 1 complete. Tag `master-plan/1/final`. Close issue #107 with summary comment. Update umbrella #105 to check off Phase 1.
- **Issue:** #107
- **Produces:** updated docs, tag `master-plan/1/final`, closed issue, checked umbrella.
- **Done when:** checklist passes, docs merged, tag pushed, #107 closed.
- **Depends on:** 1.10.

## 8. Risks and open questions

| Item | Risk | Mitigation |
|------|------|-----------|
| `bots.current` import hook | `sys.modules` aliasing is unusual; may confuse mypy / ruff / IDE navigation | Cover with explicit test (Step 1.6); if mypy complains, add `# type: ignore[misc]` at the alias line and document the pattern in `bots/current/__init__.py` |
| Circular imports after rename | Internal rewrites (Step 1.4) could introduce cycles via `bots.v0` name difference | Step 1.4 keeps imports relative where possible (`from .bot import ...`); if absolute, use `bots.v0.*` consistently |
| Windows file locks on data move | Backend server or daemon holding `training.db` open when Step 1.8 tries to move it | Prompt user to stop backend + daemon before 1.8 runs; smoke gate in 1.9 re-verifies |
| Test-time working directory | Tests sometimes assume CWD = repo root and read `data/...` directly (see `feedback_backend_wrong_cwd_silent.md` memory) | Registry's `resolve_data_path` uses repo-relative path (computed from `__file__`), not CWD-relative |
| `.claude/skills/` hardcoded paths | Skills (`improve-bot`, `improve-bot-advised`, `a4g-dashboard-check`) may reference `src/alpha4gate/` | Step 1.10 grep audit + update; skills are project-local so a single pass covers them |
| Phase 0 spike regression | `scripts/spike_subprocess_selfplay.py` must keep working across the move | Smoke gate 1.9 re-runs the spike; if it fails, the orchestrator contract drift is the root cause |
| Manifest seed source of truth | If `promotion_history.json` is empty or corrupt, Step 1.8's pre-seed has no data | Fall back to `bots/v0/data/checkpoints/best.zip` discovery; if that's also missing, leave manifest with `best=null` and raise on first promotion (Finding #11 fix catches it) |
| Hot-data move misses something | A file we missed in the "hot" list gets orphaned at repo root | Step 1.8 diff-checks the 10-item list against `data/` glob and reports anything left over |

## 9. Testing strategy

**New tests added (per step):**
- `tests/test_contracts.py` (1.2) — dataclass roundtrip, JSON schema fidelity.
- `tests/test_bootstrap_promotion.py` (1.3) — manifest-seeded invariant.
- `tests/test_bots_v0_main.py` (1.5) — argparse + one `@pytest.mark.sc2` smoke test.
- `tests/test_current_pointer.py` (1.6) — `bots.current` aliasing, submodule resolution.
- `tests/test_registry.py` (1.7) — `resolve_data_path` fallback, manifest load/validate.

**Existing tests that might break:**
- `test_promotion.py` — Step 1.3's stricter invariant may invalidate fixtures that use null manifests. Update those fixtures.
- `test_database.py`, `test_database_threadsafety.py` — Step 1.8 changes `data/training.db` path resolution. Fixtures using `Path("data/training.db")` directly must route via `resolve_data_path`.
- All 50 test files — Step 1.10 rewrites imports. Verified by full test run post-rewrite.

**End-to-end verification (Steps 1.9 + 1.11):**
- Full SC2 game via `bots.current`.
- Backend boot from repo root, dashboard 9 tabs render.
- Daemon training cycle + eval + promotion attempt.
- Phase 0 spike re-run.
- Playwright-driven dashboard screenshot audit (`/a4g-dashboard-check`).

**Regression signal:**
- 864 (session start) + 5 new tests from this phase = target 869+ on green.
- Frontend: 126 tests unchanged (no frontend code touched).

## 10. Execution guidance

- **Tonight:** `/build-phase --plan documentation/phase-1-build-plan.md --stop-after 1.3` — runs Steps 1.1, 1.2, 1.3. All additive, each ends green. Low risk overnight.
- **Fresh session (tomorrow+):** Steps 1.4 → 1.6 in one session (copy + pointer + entry-point). 1.7 + 1.8 in a third session (registry + migration). 1.9 smoke gate is synchronous with user. 1.10 + 1.11 in a fourth session.
- **Kill criterion:** if any step's test run goes red and can't be fixed within 1 h, revert the step commit, reopen the design decision, and update this plan before proceeding.
- **Rollback:** `git revert` per step commit. For 1.4/1.8/1.10 (the large ones), having them as single commits makes revert atomic.
