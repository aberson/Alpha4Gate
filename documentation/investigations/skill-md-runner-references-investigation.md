# SKILL.md `alpha4gate.runner` references — investigation

**Date:** 2026-05-02
**Surfaced by:** Models tab Step 11 (smoke gate) reviewer pass on 2026-05-02 — fixed `scripts/start-dev.sh`'s use of `alpha4gate.runner` and ripgrepped for siblings; the SKILL.md cluster fell out of scope of that build-step.
**Status:** open. Cleanup not yet executed.
**Effort estimate:** 30–60 minutes (mechanical replace + manual verification).

## TL;DR

Phase 1 (`bots-v0-migration`, completed 2026-04-13) deleted the `src/alpha4gate/` package and replaced it with `bots/v0/` + the `bots/current/` MetaPathFinder alias. Three operator-facing skill files in `.claude/skills/` were never updated and still instruct Claude to invoke `python -m alpha4gate.runner`. Any operator running `/improve-bot`, `/improve-bot-advised`, or `/a4g-dashboard-check` today and following those instructions verbatim hits `ModuleNotFoundError: No module named 'alpha4gate'` and the skill aborts. The runtime fix (`bots.current.runner`) was already proven by the Models tab Step 11 fix to `scripts/start-dev.sh`, `scripts/live-test.sh`, `scripts/dev-serve.ps1`, and `frontend/src/components/ProcessMonitor.tsx` — same mechanical replacement applies.

**Recommendation: ship a single cleanup PR replacing all 7 SKILL.md call sites with `bots.current.runner`. Leave plan-doc references (phase-1-build-plan.md, phase-b-build-plan.md) alone — those are frozen-in-time records of how completed phases were executed, and editing them would rewrite history.**

---

## 1. Affected files (verified 2026-05-02)

`grep -rn "alpha4gate\.runner" .claude/ documentation/` against `master` HEAD `4a3a29a`:

### Operator-facing skill files (need fixing)

| File | Line | Context | Fix |
|---|---|---|---|
| `.claude/skills/a4g-dashboard-check/SKILL.md` | 23 | "Backend must be running on port 8765 (`uv run python -m alpha4gate.runner --serve`)" | swap module path |
| `.claude/skills/improve-bot/SKILL.md` | 205 | "Launch the backend **without** `--daemon` (e.g. `… uv run python -m alpha4gate.runner --serve …`)" | swap module path |
| `.claude/skills/improve-bot-advised/SKILL.md` | 141 | `DEBUG_ENDPOINTS=1 PYTHONUNBUFFERED=1 uv run python -m alpha4gate.runner --serve 2>&1 &` | swap module path |
| `.claude/skills/improve-bot-advised/SKILL.md` | 186 | `uv run python -m alpha4gate.runner --map Simple64 --difficulty $DIFFICULTY 2>&1 …` | swap module path |
| `.claude/skills/improve-bot-advised/SKILL.md` | 194 | `uv run python -m alpha4gate.runner --map Simple64 --difficulty $DIFFICULTY --realtime …` | swap module path |
| `.claude/skills/improve-bot-advised/SKILL.md` | 499 | `DEBUG_ENDPOINTS=1 PYTHONUNBUFFERED=1 uv run python -m alpha4gate.runner --serve --daemon …` | swap module path |
| `.claude/skills/improve-bot-advised/SKILL.md` | 537 | `DEBUG_ENDPOINTS=1 PYTHONUNBUFFERED=1 uv run python -m alpha4gate.runner --serve …` | swap module path |

**7 references across 3 files.** All are runnable shell commands that would crash today.

### Plan docs (LEAVE ALONE)

| File | Line | Why leave |
|---|---|---|
| `documentation/plans/phase-1-build-plan.md` | 158 | Describes the historical search-and-replace done DURING Phase 1 — editing it would corrupt the historical record |
| `documentation/plans/phase-1-build-plan.md` | 194 | Pre-migration verification checklist; the failing `alpha4gate.runner` invocation is exactly what was supposed to STILL work at that checkpoint |
| `documentation/plans/phase-b-build-plan.md` | 189 | Phase B operator step text frozen at the time the phase ran (memory `feedback_verify_plan_cli_commands.md` confirms this plan-doc text already cost 3 correction rounds during Phase B execution; no value in rewriting now) |

These references are historical. Fixing them would obscure what the project actually did at the time and would not improve any operator experience today.

### Out-of-scope grep (already clean)

`grep -rn "alpha4gate\.runner" bots/ src/ scripts/ frontend/src 2>&1` returns 0 matches at HEAD — Step 11 already cleaned the live code paths (`scripts/start-dev.sh`, `scripts/live-test.sh`, `scripts/dev-serve.ps1`, `frontend/src/components/ProcessMonitor.tsx`).

## 2. Why this slipped through Phase 1

Phase 1 (`documentation/plans/phase-1-build-plan.md`) was the bots-v0-migration that physically moved `src/alpha4gate/*` → `bots/v0/*` and deleted the source package. Step 1.10 of that plan was the destructive deletion. The plan's Impact Analysis (file-by-file change table) tracked Python import sites, the `pyproject.toml` package layout, the runner subprocess in `bots/v0/api.py:1433`'s `/restart` endpoint — but **did not enumerate Markdown skill files**. SKILL.md content is invisible to a `mypy strict` pass and is not exercised by any automated test, so the broken instructions sat dormant.

Until Models tab Step 11 forced the issue (`scripts/start-dev.sh` referenced `alpha4gate.runner` → `--ui` build-steps were uniformly broken), nothing failed loudly enough to notice. That step's reviewer ran a wider ripgrep and surfaced the SKILL.md cluster but explicitly scoped its fix to "3 active code paths" so Step 11's diff stayed tight.

## 3. Symptoms today (if not fixed)

Running any of the three skills:

```
$ uv run python -m alpha4gate.runner --serve
ModuleNotFoundError: No module named 'alpha4gate'
```

Specifically:

- `/improve-bot-advised` (line 141 in its SKILL.md) is the FIRST command in the skill's main loop. The skill aborts before any improvement work begins. Memory `project_improve_bot_advised.md` records improve-bot-advised as "0%→75%+ win rate at diff 3 via 6 code iterations" — that workflow is currently inaccessible.
- `/improve-bot` (line 205) is similarly broken on its backend-launch step.
- `/a4g-dashboard-check` (line 23) is a pre-flight check that was already advisory, but the instruction is wrong.

**Effective blast radius:** every operator invocation of `/improve-bot*` skills since the Phase 1 merge (2026-04-13) has either silently failed at the backend step OR succeeded only because Claude noticed the failure and improvised a fix in-context. Recent successful runs (e.g. v4→v7 promoted on the 2026-04-30 8h soak per memory `project_evolve_v4_to_v7_soak.md`) presumably went via `/improve-bot-evolve` which uses different invocation paths — confirms the dormant-defect hypothesis.

## 4. Fix

```bash
# From repo root, on a fresh branch:
git grep -l 'alpha4gate\.runner' .claude/skills/ | xargs sed -i 's/alpha4gate\.runner/bots.current.runner/g'

# Verify:
git diff --stat .claude/skills/
grep -rn 'alpha4gate\.runner' .claude/skills/ && echo "FAIL — references remain" || echo "OK"

# Manual smoke (optional — confirms the bots.current alias works for each invocation form):
uv run python -m bots.current.runner --serve &     # used in 4 of the 7 sites
uv run python -m bots.current.runner --map Simple64 --difficulty 1   # used in 2 of the 7 sites
uv run python -m bots.current.runner --map Simple64 --difficulty 1 --realtime    # used in 1 of the 7 sites
```

`bots.current.runner` resolves to `bots/v10/runner.py` today via the MetaPathFinder alias defined in `bots/current/__init__.py`. Verified 2026-05-02 during Models tab Step 11: `python -c "import bots.current.runner; print(bots.current.runner.__file__)"` → `bots/v10/runner.py`. The alias auto-tracks future evolve promotions, so the SKILL.md fix is durable across version bumps — never needs to be re-edited as v11, v12, … land.

## 5. Verification (suggested PR done-when)

- [ ] All 7 `alpha4gate.runner` references in `.claude/skills/{a4g-dashboard-check,improve-bot,improve-bot-advised}/SKILL.md` replaced with `bots.current.runner`.
- [ ] `grep -rn 'alpha4gate\.runner' .claude/skills/` returns no matches.
- [ ] `grep -rn 'alpha4gate\.runner' bots/ src/ scripts/ frontend/src/` still returns no matches (Step 11 baseline preserved).
- [ ] Each of the three skill bodies still parses cleanly (no markdown corruption from sed).
- [ ] One end-to-end manual run of `/improve-bot-advised --self-improve-code --max-runs 1` proceeds past the backend-launch step (proves the actual operator path works; this is the spirit of the SKILL.md verification carve-out from Models tab Step 11).
- [ ] Plan-doc references in `documentation/plans/phase-1-build-plan.md` and `documentation/plans/phase-b-build-plan.md` deliberately UNCHANGED.

## 6. Risk if not fixed

**Low ongoing user impact** because operators have learned to work around it (or pivoted to `/improve-bot-evolve`). **Medium silent-defect risk** because the SKILL.md instructions are followed literally by Claude in fresh contexts that don't have the workaround memorized — a new contributor or a stale-cache session would hit the wall.

The fix is mechanical, low-risk, and unblocks recorded historical workflows that have value today.

## 7. Out of scope

- Any change to plan-doc historical references (see §1).
- Any change to `bots/current/` alias mechanism — already shipped and working.
- Adding automated lint/CI to catch future renames of this kind. Worth its own consideration but not bundled here; the cost-benefit isn't obvious for a 7-reference one-off after a 3-week silent gap.
- Removing `bots/v0/` or any other historical version — orthogonal.

## 8. Sources

- Memory `project_phase_1_status.md` — Phase 1 COMPLETE 2026-04-13, src/alpha4gate/ deleted, all code in bots/v0/.
- Memory `project_improve_bot_advised.md` — improve-bot-advised reaches 75%+ WR at diff 3 with --self-improve-code.
- Memory `project_evolve_v4_to_v7_soak.md` — recent productive operator workflow uses `/improve-bot-evolve`, not `/improve-bot-advised` directly.
- Memory `feedback_verify_plan_cli_commands.md` — plan-doc CLI text drift has historically cost correction rounds; argues for not editing frozen plan docs.
- `documentation/plans/phase-1-build-plan.md` — Phase 1 Impact Analysis (the artifact that should have caught these but didn't enumerate `.claude/skills/`).
- Models tab Step 11 PR / commit `4a3a29a` (master, 2026-05-02) — the Step 11 dev's iter-2 report explicitly flagged this cluster as out of scope: *"these are operator/agent-facing instructions and would crash if followed today, BUT the reviewer's MEDIUM-2 explicitly scoped the fix to '3 active code paths' and called out only `live-test.sh`, `dev-serve.ps1`, `ProcessMonitor.tsx`. SKILL.md updates were not in scope. Worth noting for a future cleanup pass."*
