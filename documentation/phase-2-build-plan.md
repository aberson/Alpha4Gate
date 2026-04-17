# Phase 2 Build Plan — Registry + full-stack snapshot tool

**Parent plan:** [alpha4gate-master-plan.md](plans/alpha4gate-master-plan.md) Phase 2 (lines 457–503)
**Parent issue:** #108
**Branch:** `master-plan/2/registry-snapshot` (cut from master at `531b1b2`)
**Baseline tag:** `master-plan/2/baseline`
**Target tag on gate pass:** `master-plan/2/final`
**Effort estimate:** 3-4 h.

## 1. What this feature does

Completes the version registry with enumeration (`list_versions()`) and adds a
full-stack snapshot tool that copies `bots/current/` to `bots/vN+1/`, writes a
fresh `manifest.json` with parent lineage, git SHA, timestamp, Elo snapshot,
and feature/action-space fingerprints. Also adds CLI entry points for both the
registry (`python -m orchestrator.registry list/show`) and the snapshot tool
(`scripts/snapshot_bot.py`). After this phase, a developer can snapshot the
working bot to create a new frozen version that can independently boot and play.

## 2. Existing context (for fresh-context models)

- **`src/orchestrator/registry.py`** already has `current_version()`,
  `get_version_dir()`, `resolve_data_path()`, `get_data_dir()`, `get_manifest()`.
  Missing: `list_versions()` (enumerate `bots/v*/` dirs).
- **`src/orchestrator/contracts.py`** has frozen dataclasses: `Manifest`,
  `VersionFingerprint`, `BotSpawnArgs`, `MatchResult`. All have `to_json()`/
  `from_json()` round-trip methods.
- **`src/orchestrator/snapshot.py`** is a stub (docstring only, no code).
- **`bots/v0/`** is the full bot stack (46 modules). Has `VERSION` (="v0"),
  `manifest.json` (seeded), `data/` with training.db, checkpoints, etc.
- **`bots/current/`** is a MetaPathFinder pointer package. Reads
  `bots/current/current.txt` (="v0") and aliases `bots.current` to `bots.v0`.
- **`tests/test_registry.py`** covers `current_version`, `get_version_dir`,
  `resolve_data_path`, `get_data_dir`, `get_manifest` (18 tests).
- **916 unit tests passing**, mypy strict (61 files), ruff clean on master.
- **Important constraint:** `registry.py` does NOT import `bots.current` or
  `bots.<version>` — doing so triggers the MetaPathFinder loop. All version
  discovery is via pathlib.
- **`bots/current/` semantics after this phase:** working copy. Snapshot
  promotes current to the next `vN+1` and re-forks current off of it (by
  updating `current.txt`).

## 3. Scope

**In scope:**

- Add `list_versions()` to `src/orchestrator/registry.py`.
- Implement `src/orchestrator/snapshot.py` with `snapshot_current()`.
- Create `scripts/snapshot_bot.py` CLI.
- Add `src/orchestrator/__main__.py` for `python -m orchestrator.registry list/show`.
- Tests for new functionality.

**Out of scope:**

- Self-play runner (Phase 3).
- Elo ladder / cross-version promotion (Phase 4).
- Sandbox enforcement (Phase 5).
- Any changes to `bots/v0/` code.
- Running SC2 games (gate verification only).

## 4. Impact analysis

| Area | Files | Nature of change |
|------|-------|------------------|
| `src/orchestrator/registry.py` | 1 file | **Add** `list_versions()` |
| `src/orchestrator/snapshot.py` | 1 file | **Fill** stub with full implementation |
| `src/orchestrator/__main__.py` | 1 file | **Create** CLI entry point |
| `scripts/snapshot_bot.py` | 1 file | **Create** CLI script |
| `tests/test_registry.py` | 1 file | **Extend** with `list_versions` tests |
| `tests/test_snapshot.py` | 1 file | **Create** snapshot round-trip tests |

## 5. Design decisions

### 5.1 `list_versions()` scans `bots/v*/` directories

Uses `pathlib.Path.glob("v*")` on the `bots/` directory. Only includes
directories that contain a `VERSION` file (not `current/` or stray dirs).
Returns sorted list of version strings.

### 5.2 Snapshot copies the full tree

`snapshot_current()` does a `shutil.copytree` of the *target version dir*
(resolved from `current.txt`), not the `bots/current/` pointer package
itself. This avoids copying the MetaPathFinder machinery. The copy gets a
new `VERSION` file, a fresh `manifest.json` inheriting from the parent,
and `current.txt` is updated to point at the new version.

### 5.3 Next version name is auto-incremented

`_next_version_name()` scans existing `bots/v*/` dirs and returns `vN+1`
where N is the highest existing version number. Explicit `--name` override
available for non-numeric naming.

### 5.4 Manifest inherits from parent

The new manifest gets `parent` set to the source version, `elo` copied from
parent (starting point), `git_sha` from current HEAD, fresh `timestamp`,
and fingerprint copied from parent's manifest.

### 5.5 Checkpoint data is included in the copy

The full `data/` subtree (including `checkpoints/`, `training.db`) is copied.
This makes each version fully self-contained and independently bootable.
Disk cost is acceptable at our scale (single bot, single machine).

## 6. Build steps

### Step 2.1: Add `list_versions()` to registry + tests
- **Status:** DONE (2026-04-16)
- **Problem:** Add `list_versions() -> list[str]` to `src/orchestrator/registry.py`. Scans `bots/` for directories containing a `VERSION` file. Returns sorted list of version strings (e.g. `["v0"]`). Does NOT include `current/` or dirs without `VERSION`. Add tests to `tests/test_registry.py`: happy path with 1 version, multiple versions, empty (no versions), ignores dirs without VERSION file.
- **Issue:** #108
- **Flags:** `--reviewers auto`
- **Produces:** updated `src/orchestrator/registry.py`, updated `tests/test_registry.py`.
- **Done when:** new tests pass, 916 existing tests still pass, mypy strict green, ruff green.

### Step 2.2: Implement `snapshot.py` — full-stack snapshot tool
- **Problem:** Fill `src/orchestrator/snapshot.py` with a `snapshot_current(name: str | None = None) -> Path` function. Reads `current.txt` to find the source version. Copies the source version dir (`bots/<source>/`) to `bots/<new_name>/` via `shutil.copytree`. If `name` is None, auto-increments from the highest existing `vN` to `vN+1`. Writes a new `VERSION` file with the new name. Creates a fresh `manifest.json`: `version` = new name, `parent` = source version, `git_sha` = current `git rev-parse HEAD`, `timestamp` = UTC ISO 8601, `elo` = parent's elo, `fingerprint` = parent's fingerprint, `best` = parent's best, `previous_best` = parent's previous_best. Updates `bots/current/current.txt` to point at the new version. Add `tests/test_snapshot.py` verifying: (a) snapshot produces a self-contained tree with correct VERSION, (b) manifest has correct parent + fingerprint, (c) current.txt updated to new version, (d) auto-increment naming works, (e) explicit name override works, (f) source version dir unchanged after snapshot, (g) error if current.txt points to nonexistent version.
- **Issue:** #108
- **Flags:** `--reviewers auto`
- **Produces:** filled `src/orchestrator/snapshot.py`, new `tests/test_snapshot.py`.
- **Done when:** new tests pass, 916+step1 tests still pass, mypy strict green, ruff green.
- **Depends on:** 2.1.

### Step 2.3: Add registry CLI (`python -m orchestrator.registry`)
- **Problem:** Create `src/orchestrator/__main__.py` with argparse supporting two subcommands: `list` (calls `list_versions()`, prints one version per line) and `show <version>` (calls `get_manifest(version)`, prints manifest JSON). Exit 0 on success, exit 1 with error message on failure (e.g. version not found). Add tests to `tests/test_registry.py` (or a new `tests/test_registry_cli.py`): verify `list` output format, `show v0` outputs valid JSON with expected fields, `show v99` exits with error.
- **Issue:** #108
- **Flags:** `--reviewers auto`
- **Produces:** new `src/orchestrator/__main__.py`, new/updated test file.
- **Done when:** `python -m orchestrator.registry list` prints `v0`, `python -m orchestrator.registry show v0` prints valid manifest JSON, tests pass, mypy strict green.
- **Depends on:** 2.1.

### Step 2.4: Create `scripts/snapshot_bot.py` CLI
- **Problem:** Create `scripts/snapshot_bot.py` as a thin CLI wrapper around `orchestrator.snapshot.snapshot_current()`. Argparse with `--name` (optional, override version name). Prints the path to the new version dir on success. Exit 0 on success, exit 1 with error on failure. Add a test in `tests/test_snapshot.py` (or new file) that verifies the script's argparse (not a full end-to-end — snapshot logic is already tested in step 2.2).
- **Issue:** #108
- **Flags:** `--reviewers auto`
- **Produces:** new `scripts/snapshot_bot.py`, updated test file.
- **Done when:** `uv run python scripts/snapshot_bot.py --help` exits 0, argparse test passes, all tests green.
- **Depends on:** 2.2.

### Step 2.5: Gate verification — snapshot round-trip
- **Type:** operator
- **Problem:** Human-run end-to-end verification. Checklist: (a) `uv run python scripts/snapshot_bot.py --name v1` creates `bots/v1/` with all expected files; (b) `bots/v1/VERSION` contains `v1`; (c) `bots/v1/manifest.json` has `parent: "v0"` and correct fingerprint; (d) `bots/current/current.txt` now says `v1`; (e) `uv run python -m orchestrator.registry list` shows both `v0` and `v1`; (f) `uv run python -m orchestrator.registry show v1` outputs valid manifest; (g) `uv run python -m bots.v1 --role solo --map Simple64 --difficulty 1` boots and plays a game (requires SC2); (h) `bots/v0/` is unchanged. After verification, delete `bots/v1/` and reset `current.txt` to `v0` (this was a test snapshot, not a real promotion).
- **Issue:** #108
- **Produces:** comment on #108 with checklist results.
- **Done when:** all checklist items pass, user signs off.
- **Depends on:** 2.1, 2.2, 2.3, 2.4.
