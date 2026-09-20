# Phase EH — Evolve operational hardening

**Track:** 10 (Statistical robustness / evolve substrate). **Status:** Planned 2026-09-04.
**Prerequisites:** Phase EL shipped (#273–#278) — its three registries (`data/{lineages,baselines,fingerprints}.json`)
are this phase's subject. Phase EV shipped (#291–#293) — its launcher (`scripts/launch-evolve.ps1`) and viewer
path are what this phase hardens. Phase EI ([`evolve-evidence-layer-plan.md`](evolve-evidence-layer-plan.md), cited below as "EI plan :N") is *planned but unbuilt*; it does not gate this phase, but the two
share `scripts/evolve.py` and must be sequenced (§6 D-8). No other phase gates it.

> Slots into the master plan as **Phase EH** on Track 10, alongside Phases EJ, EI and R. Step IDs are
> `EH.1 … EH.10` (letter.number form, matching Phases B/D/E/G/EL/EJ/EV/EI) so they never collide with
> numeric-track step numbering. The letter `EH` was checked free across `documentation/` and `CLAUDE.md`
> on 2026-09-04 (`grep -rn "EH\.[0-9]\|Phase EH"` → zero text hits; two binary false positives in
> `documentation/images/`).
>
> **Provenance.** Every defect below was verified read-only against HEAD `9ccf4b8` on branch
> `master-plan/phase-ev` by a 19-agent verification run on 2026-09-04 (4 ground readers + 8 adversarial
> skeptics + 7 context readers, 0 errors). **All four target defects survived refutation 8/8**
> (`refuted=false` from every skeptic), and the skeptics corrected two of the four *stated causes* — those
> corrections are folded in below and called out in §6 D-1 and D-3. Primary in-repo sources, all
> authoritative: [`../operator-gate-runbook.md`](../operator-gate-runbook.md) (29 documented defects; its
> closing shortlist at :510-512 names three of this plan's targets), [`../master_plan.md`](../master_plan.md)
> :1080-1096 ("Known gaps (carried, not fixed by this phase) … Queued for the evolve operational-hardening
> plan"), and [`evolve-evidence-layer-plan.md`](evolve-evidence-layer-plan.md):130, which explicitly hands
> this plan its first two items. Where the operator seed
> [`../../docs/seeds/evolve-restructure-operator-notes.md`](../../docs/seeds/evolve-restructure-operator-notes.md)
> and the round-2 investigation ([`../investigations/evolve-restructure-round2.md`](../investigations/evolve-restructure-round2.md))
> disagree on a `scripts/evolve.py` line number, **round 2 §3 is the one owner
> of file:line truth** (that file's `## 3. Anchor corrections`, `:158`) — the seed's own round-1 anchors are
> known-wrong in 19 places and uncorrected.

---

## 1. What This Feature Does

Phase EH closes the **population-state write-back seam** in the evolve run, and repairs the three
operator-facing controls that lie about it.

An evolve run durably persists exactly the state it *owns* — pool, results, run state, crash log, git
commits, run-log markdown. It persists **none** of the state that describes the *population*. Every registry
module built for that purpose (`lineages.py`, `baselines.py`, `fingerprint.py`) is, from `run_loop`'s
perspective, a read-only input. The root cause is visible in three consecutive declarations:
`run_loop` holds `_lineage_heads` (`scripts/evolve.py:4173`), `_lineage_fitness` (`:4206`) and
`_lineage_fingerprints` (`:4207`) as **local dicts**, then at the generation boundary writes the advanced
head back to memory at `:5158` under a comment that says *"Persist this lineage's (possibly advanced) head"*.
There is no persist hop anywhere in the loop.

That one missing hop produces the whole symptom set: lineage heads and extinctions evaporate at process
exit; a resumed run re-branches from stale heads and revives extinct lineages; and a shipped, disk-backed
dashboard endpoint (`GET /api/evolve/lineages`, `bots/v13/api.py:1641`) returns an empty-but-successful
HTTP 200 during a live multi-lineage soak, so the operator cannot distinguish *"scheduling is off"* from
*"running fine, nothing persisted."*

Alongside it, three controls are wired only halfway: the dashboard **Stop Run** button drives a five-hop
write path that terminates in a file no runner opens; `scripts/launch-evolve.ps1` never forwards
`--generations`, so every launcher-started *N-hour soak* silently stops after **one** generation; and a
second evolve run started mid-soak truncates the first run's `evolve_results.jsonl` with nothing but a
fail-open Windows process probe in the way.

**Why now.** Roughly 28–30 hours of queued operator-gate evidence (EL.7 #279, EJ.7 #288, EJ.8 #289, EV.5
#295, Phase 7 Step 6 #280, and Phase EI's own EI.14) runs on this substrate. The runbook's own impact table
(`operator-gate-runbook.md:40-45`) rates the generation-cap defect **fatal to EV.5, EJ.8 and EL.7**. Every
one of those gates is currently capable of burning its full wall-clock budget and producing a green-looking
record for a feature set that never executed.

## 2. Existing Context

**The evolve run.** `scripts/evolve.py` is a ~5,400-line single-file orchestrator. `main()` (`:5363`) parses
args and calls `run_loop` (`:3759`), whose `while True:` generation loop (`:4223`) has exactly three exits —
wall-clock (`:4224`), generation cap (`:4229`) and pool-exhausted (`:4241`). Fitness runs either serially
(`if concurrency <= 1:` at `:4378`) or through `_run_fitness_phase_parallel` (`:2583`). **The serial path is
the default operator path**: `scripts/launch-evolve.ps1:72` spawns `--viewer`, which is mutually exclusive
with `concurrency > 1`.

**The three Phase EL registries**, all at repo-root `data/` (gitignored; `git ls-files data/` is empty):

| Registry | Writer | Wired? |
|---|---|---|
| `data/baselines.json` | `write_baselines` / `register_baseline` (`src/orchestrator/baselines.py:212`/`:226`) | **Yes** — production CLI `scripts/baseline.py:78`/`:109` |
| `data/fingerprints.json` | `save_fingerprint` (`src/orchestrator/fingerprint.py:347`) | **Yes** — one in-loop call site `scripts/evolve.py:4762`, behind three conjoined gates (`:4708-4711`, `:4743`) |
| `data/lineages.json` | `write_lineages` (`src/orchestrator/lineages.py:215`) | **NO** — zero production callers anywhere |

The gating is a chain: an empty `baselines.json` makes `--fitness-mode baseline|both` degrade to `parent`
with a warning only (`scripts/evolve.py:3924-3930`) and `--panel-floor` INERT (`:3938-3950`); no gauntlet
means the fingerprint gate never opens; no fingerprints means the diversity matrix is empty; and
`decide_extinctions` (`population.py:204`) — which *is* called at `:5185` — applies its verdict only to
in-memory dicts (`:5203-5204`).

**The control plane** is the same seam inverted. `PUT /api/evolve/control` (`bots/v13/api.py:1460`)
atomically writes `data/evolve_run_control.json` with keys `{stop_run, pause_after_round}` (`:1425-1432`);
`scripts/launch-evolve.ps1:31` carries a comment stating that nothing in `scripts/evolve.py` or
`src/orchestrator/` reads it — mechanically true, and the only hit for the filename across all of `scripts/`.
The sibling `/improve-bot-advised` control file **is** consumed (`bots/v13/process_registry.py:249`), so an
operator who learned that Stop works will reasonably assume this one does too.

**Live version pointer.** `bots/current/current.txt` reads `v13`; `bots/v0`–`v12` are frozen historical
trees whose `api.py` copies must not be touched.

**Conventions this phase inherits.** All new flags default OFF / preserve a byte-identical bare invocation
(the EJ cross-cutting rule, seed :62). No new gate may replace or weaken fitness / stack-apply import gate /
regression (`.claude/skills/improve-bot-evolve/SKILL.md:574`, the rail that survives every operator answer).

## 3. Scope

### In scope

- The generation-boundary write-back for lineage heads and extinctions, plus a CLI that can create a
  registry in the first place (EH.1, EH.2).
- Converting the silent gauntlet degradations into a loud, opt-in-strict failure (EH.3).
- A runner-side reader for `data/evolve_run_control.json` acting on **`stop_run` only**, in both
  fitness-path shapes, plus the frontend honesty fix (EH.4, EH.5). `pause_after_round` is cut by
  operator decision **P-1** and its checkbox is disabled rather than left lying (§ Decision Inventory).
- Launcher `--generations` passthrough, its false header comment, the second independent caller in
  `improve-bot-evolve/SKILL.md`, and a contradiction warning (EH.6).
- Two unattended-run data-safety guards: a single-run lock, and path-scoping the `[evo-auto]` commit
  (EH.7, EH.8).
- A real producer→consumer smoke gate and a short operator verification (EH.9, EH.10). Those are the
  phase's only two end-to-end tiers: **no multi-hour, unattended-observation step is in scope**, per
  operator decision **P-2 as overridden** — see the Explicitly-out row below and § Decision Inventory.

### Explicitly out

| Item | Why |
|---|---|
| Registering the frozen baselines themselves | A one-time operator action, not code: `uv run python scripts/baseline.py add v10 v10 --note "frozen anchor"` per `../operator-gate-runbook.md`:85-92, promoted to Gate 0 at :245-248. The CLI already exists and works. EH.3 hardens the *code-side* fail-open only. |
| Creating or repopulating lineages at runtime | Separable defect (`../operator-gate-runbook.md`:124): `population.py`'s `repopulate` list is always empty and `scripts/evolve.py` never reads it. Wiring `write_lineages` makes an operator-seeded registry durable; it does not make `--lineages 4` produce 4 lineages. Decided out; the `repopulate` list stays dead code. |
| The `--lineages` one-way default | Once a non-empty registry exists, multi-lineage scheduling engages for every later bare invocation with no off-switch (`../operator-gate-runbook.md`:147). EH.1 makes this permanent rather than accidental, so it is carried as a **Risk**, not fixed here. |
| Flipping `--generations`' argparse default | `default=1` is a documented quick-test contract (`scripts/evolve.py:30-31`; `../wiki/operator-commands.md`:66-67). Flipping it turns every bare `python scripts/evolve.py` into an unbounded auto-committing run — a strictly worse failure mode. EH.6 fixes the callers. |
| Anything Phase EI (`evolve-evidence-layer-plan.md`) owns | EI claims `scripts/evolve.py` at :125, :827-838, :928-935, :957-968, :4559-4562, :4609, :4676 and the whole `logs/traces/` retention surface. EI also adds six flags to the same `build_parser` argparse block (EI `:149`, `:197-202`) and a generation-boundary call site in `run_loop`. See §6 D-8 for the collision points and the one hard prohibition. |
| The Elo ladder writers | `src/orchestrator/ladder.py`'s six unwired writers are a genuinely separate Phase-4-era subsystem with its own CLI; nothing in `scripts/evolve.py` imports `orchestrator.ladder` at all. Wiring it would create a second promotion path competing with `_stack_apply_and_promote`. Its own decision, not this plan's. |
| `logs/selfplay_tmp/game{i}_p*.json` collision | Real sibling defect, but `--result-out` is a dead contract with zero consumers (EI plan :131), and the fix lands in `src/orchestrator/selfplay.py`, which EI.1 and EI.5 both modify. Leave to whichever plan touches that file first. |
| Any unattended multi-hour observation of this phase's changes — a soak slot of its own **or** a co-tenant `Type: wait` step on someone else's | Operator decision **P-2, overridden 2026-09-04** (D2 = ship without). **This phase closes with no unattended-observation step at all.** The draft carried one — EH.11, a co-tenant on EL.7 (#279) in the EI.14 pattern — and the operator's final call removed it: soaks serialize machine-wide (EI plan :512), 28–30 hours of gates are already queued, and the long observation rides EL.7 / EI.14 **as originally written** — opportunistically, owed by no step in this plan and imposed on no sibling plan (P-7 forbids editing them). EH.10, bounded at ~45–60 minutes, is the phase's terminal verification. **What that gives up, stated plainly:** EH.2's per-generation persist hop is never watched across a six-hour run, and EH.8's `[evo-auto]` commit scoping is never observed against a real unattended auto-committing loop — both ship on test and short-run evidence only. Accepted consequence, not an oversight. See §9. |
| Wiring `pause_after_round` | Operator decision **P-1**: the second flag on the same dead control file is **cut**, not half-wired. Specifying it would need a new `paused: bool` run-state field (never `status="paused"` — `EvolutionTab.tsx:1382` gates both controls on `status === "running"`, and EH.7's lock keys on it), a generation-boundary-only wait, and bounded re-checks of `_budget_exceeded`/`stop_run`. That is a design increment for a control with no evidence of use; precedent for folding one away is `../master_plan.md`:2095 ("Operator reported not using the Alerts tab"). EH.5 **disables the checkbox and labels it not-implemented** so the UI stops lying — cutting the work must not leave the lie. |
| Editing sibling plan documents | Operator decision **P-7**: EH.4 falsifies five "Stop button is not wired" claims in `evolve-viewer-plan.md` (`:657, :665, :684, :803, :936`), but that plan has two pending operator gates (EV.4 #294, EV.5 #295) and editing a plan mid-gate is how records drift. Tracked as sibling issue **#302** instead — which also carries the sequencing fact that **EV.5 must run *after* EH.6**, since a pre-EH.6 EV.5 measures a single generation. |

## 4. Impact Analysis

Per [`code-quality.md`](../../../.claude/rules/code-quality.md) § "Grep all downstream consumers when
changing a key/id shape", the `Verified` column records the literal search and its result.

| File | Change Type | Reason | Verified |
|---|---|---|---|
| `src/orchestrator/lineages.py` | extend | `next_lineage` must filter `status == "active"` once extinct records are persisted (EH.2) | `grep -rn "write_lineages"` → def `:215`, `__all__` `:70`, docstring `:41`; cross-ref docstrings only at `fingerprint.py:337`, `baselines.py:216`; **callers exclusively in `tests/test_lineages.py` (3) and `tests/test_evolve_cli.py` (6)**. `next_lineage` `:272` is `ids = list(registry.keys())` — takes every key, no status filter. |
| `scripts/lineage.py` | **new** | First production caller for `write_lineages` (EH.1) | `git log --all --diff-filter=A -- scripts/lineage.py` → empty across 15 local branches + origin. No such file has ever existed. |
| `scripts/evolve.py` | modify | Generation-boundary persist hop (EH.2); strict-baseline gate (EH.3); control-file reader + startup clear + two poll shapes (EH.4); contradiction warning (EH.6); lock acquire/release (EH.7); path-scoped commit (EH.8) | Write site `:5158` + cull pops `:5203-5204`; persist model `:4762`; degrade warnings `:3924-3930`, `:3938-3950`; loop head `:4223-4246`; serial fitness `:4378-4380`; parallel dispatcher `:2785-2793`; `_clear_fresh_run_state` `:1888-1899`; run-state write `:4005`. `mypy src bots --strict` does **not** cover `scripts/` (verified `pyproject.toml:97`). |
| `scripts/launch-evolve.ps1` | modify | `-Generations` param + spawn-line passthrough + header-comment repair (EH.6); the stale "nothing reads it" comment at `:31` (EH.4, comment only) — **two steps edit this file** | Only parameter is `[double]$Hours = 4` (`:45`); spawn line `:72` forwards `--hours` and `--viewer` only; false claim at `:27-29`. **Pinned ASCII/no-BOM by `tests/test_evolve_cli.py:4362-4367`** — edit accordingly. |
| `bots/v13/api.py` | none (read-only audit) | Consumer of both registries | `:1641` `GET /api/evolve/lineages` reads `:1668` `_read_json_file(_evolve_dir / _EVOLVE_LINEAGES_FILE) or {}`; reads `entry.get("head_version")`/`.get("status")` at `:1689-1694` — both emitted by `dataclasses.asdict(Lineage)`. Path agreement confirmed: `runner.py:292` `evolve_dir=_repo_root()/"data"` == `lineages.py:175` `default_lineages_path()`. **Verdict OK, no edit.** `bots/v0`–`v12` copies frozen — do not touch (`bots/current/current.txt` == `v13`). |
| `frontend/src/hooks/useEvolveRun.ts` | modify | `res.ok` is never checked, so a 400/500 still fires a success toast (EH.5) | `:381-391` — the `fetch` Response is not bound to a variable. `interface EvolveLineage` `:270` already carries `lineage_id`/`head_version`/`status` — **no shape change**. `generations_target?: number \| null` `:108-111` documents `0 = unbounded` — **already the contract EH.6 relies on**. |
| `frontend/src/components/EvolutionTab.tsx` | modify | Optimistic toast → pending badge; empty-state copy; **pause checkbox disabled + labelled not-implemented** (all EH.5, the last per P-1) | Toast `:1401-1407`; dialog promise `:1648`; button `:1620-1623`; `pause_after_round` checkbox `:1634-1641` (**second silent no-op on the same file**); empty state `:1330` blames the pre-EL loop; `PoolStatusBadge` `:183` falls back to grey for an unknown status, so `"extinct"` renders safely. |
| `.claude/skills/improve-bot-evolve/SKILL.md` | modify | Second independent `--generations` caller (EH.6); three stale "nothing reads this file" claims (EH.4) | Canonical invocation `:216-222` omits `--generations`; flag table `:34-53` has no row for it. Stop-button claims at `:63`, `:359`, `:462`. **Ordering constraint:** `tests/test_evolve_cli.py:4337-4352` parses `stop_reason` values out of this file and asserts each appears in `evolve.py` source — **ship the reader first, the doc second.** |
| `documentation/wiki/operator-commands.md` | modify | `:335` "Not wired to this runner" becomes false after EH.4 | Single line; its soak examples at `:61-63`/`:294` already pass `--generations 0` correctly — no change there. |
| `tests/test_evolve_cli.py` | extend | New/extended tests from seven steps (EH.2, EH.3, EH.4, EH.6, EH.7, EH.8, EH.9; EH.1 ships `tests/test_lineage_cli.py` instead) | `_build_args` `:171-211` sets no `control_path`/`lock` attr → **~40 existing tests stay green only if reads use `getattr(args, …, None)`**. `:457` `assert args.generations == 1` is the tripwire that distinguishes the launcher fix from an argparse flip — **leave it untouched**. Lineage harness to copy: `:2133-2156` (`monkeypatch.setattr(lineages_mod, "_repo_root", lambda: tmp_path)`). |
| `tests/test_lineages.py`, `tests/test_api_evolve_lineages.py` | extend | Round-trip + end-to-end closure | `test_lineages.py:77,:119,:225` are pure unit round-trips — green, and prove nothing about reachability. `test_api_evolve_lineages.py:84,:171,:198,:251,:292` hand-write JSON, bypassing `write_lineages`; `:114` `assert body["lineages"] == []` pins empty-for-missing-file and **stays correct**. |
| `data/lineage.json` (**singular**) | **DO NOT TOUCH** | Different artifact | The version DAG written by `scripts/build_lineage.py:436`. `../operator-gate-runbook.md`:127-129 — "a completely different file. Do not confuse them." |

## 5. New Components

- **`scripts/lineage.py`** (~120 LOC) — a structural mirror of `scripts/baseline.py`: same `_REPO_ROOT`
  `sys.path` preamble, same argparse sub-parser shape, subcommands `add <lineage_id> <head_version>` /
  `list` / `remove <lineage_id>`, `--path` override for tests. Imports `default_lineages_path`,
  `load_lineages`, `write_lineages` from `orchestrator.lineages` and validates `head_version` against
  `orchestrator.registry.list_versions` exactly as `register_baseline` does (`baselines.py:250-254`), so a
  typo fails loudly. **This is the only new operator-facing surface in the phase.**
- **`read_run_control(path) -> dict`** in `scripts/evolve.py`, beside `write_run_state` (~`:1412`).
  Returns `{"stop_run": False, "pause_after_round": False}` on missing file, `OSError`,
  `JSONDecodeError`, **a non-dict top-level payload, or a non-bool value for either key** — a corrupt
  control file must never abort a soak, and must never stop one either. Implement as a narrow
  `except (OSError, json.JSONDecodeError)` plus `if not isinstance(payload, dict): return defaults` and a
  per-key `isinstance(v, bool)` coercion, mirroring `bots/v13/api.py:1115-1132`'s `_read_json_file` (which
  pairs the narrow catch with an `isinstance(payload, dict)` check at `:1129-1130`) and matching the API's
  bool-only contract at `api.py:1485-1490`. Do **not** use a blanket `except Exception`: this is a two-line
  `json.loads` where that would mask genuine bugs, and it would not catch the truthy-non-bool case anyway. Must **not** require the
  `updated_at` field `SKILL.md:452-460` documents; the API rejects any key outside
  `{stop_run, pause_after_round}` with HTTP 400 (`bots/v13/api.py:1479-1490`), so that field never exists.
- **`--control-path`** on `scripts/evolve.py`, alongside `--state-path` (`:265-273`), defaulting to
  `_REPO_ROOT / "data" / "evolve_run_control.json"` so it matches `bots/v13/runner.py:242` by construction.
- **`--require-baselines`** (default OFF) on `scripts/evolve.py` — turns today's warn-and-proceed
  degradation into a two-second non-zero exit. Default OFF preserves the byte-identical bare invocation.
- **A single-run lock**, implemented as a pid + start-timestamp stamped into `data/evolve_run_state.json`
  and checked at startup. Not a new file: the state file already exists, is already written at `:4005`, and
  a pid in it *is* the lock — which also reconciles the write-only staleness defect
  (`../operator-gate-runbook.md`:172, :182-186) in the same change. Liveness is `psutil.pid_exists(pid)`
  plus a `psutil.Process(pid).create_time()` reuse check, **never** `os.kill(pid, 0)` (see EH.7); `psutil`
  is a core transitive dependency via `portpicker`, so no fallback branch is permitted.
- **`--force`** on `scripts/evolve.py` — the manual override for the EH.7 lock, for the genuine recovery
  case where the operator knows the recorded pid is not a live evolve run.
- **`-Generations` parameter** on `scripts/launch-evolve.ps1` (`[int]$Generations = 0`).
- **New stop reason `"dashboard-stop"`**, joining `wall-clock` / `generations-reached` / `pool-exhausted`.

One new run-state field, and it follows an established additive pattern: EH.7 adds `pid` to
`data/evolve_run_state.json` via a new keyword-only param on `write_run_state` (`scripts/evolve.py:1412`,
default `None`) plus the `_write_state` wrapper the `:4005` initial write calls — exactly how `run_id`,
`concurrency`, `cli_argv`, `gen_durations_seconds` and `generations_target` were each added. `started_at`
already exists; only `pid` is new. `GET /api/evolve/state` returns the parsed file verbatim
(`bots/v13/api.py:1445-1448`, untyped `dict[str, Any]`, no response model) and `EvolveRunState`
(`frontend/src/hooks/useEvolveRun.ts:79-112`) takes post-`last_result` additions as optional fields, so no
consumer edit is required and `bots/v13/api.py` stays read-only. Nothing else is new: `Lineage`
(`lineages.py:115-141`), the control-file key set, and `generations_target = 0` are all pre-existing wire
contracts.

## 6. Design Decisions

**D-1 — Frame the phase as one seam, not four bugs; and correct the tasking's stated cause for the
registry defect.** The scouting brief framed defect 4 as *"the frozen-baseline registry is never written
because no CLI writes it."* **That is false for two of the three registries it conflates**, and both
skeptics confirmed the refutation: `scripts/baseline.py` *is* a production CLI writer (docstring `:1-3`,
`register_baseline` `:78`, `write_baselines` `:109`), and `save_fingerprint` *is* wired into the production
loop at `scripts/evolve.py:4762` — empirically proven to fire, since EL.6's PASS record
(`../soak-test-runs/evolution-lines-smoke-20260620.md`:35, commit `7137e7c`) shows
`data/fingerprints.json` populated. The runbook sentence the brief quoted (`:126` *"And no CLI writes the
registry"*) is scoped to the **lineage** registry in its section 3, not to the baseline registry of its
section 2. What the evidence actually supports: *the EL.7 soak is vacuous because three data preconditions
are unmet — one of which (`lineages.json`) has no production writer at all, while the other two are gated
behind an unperformed operator step that cascades into suppressing fingerprint writes.* Consequence for
this plan: the lineage half of that defect **is** the `write_lineages` defect, diagnosed in the same runbook
paragraph (`:124-129`), so it is fixed **once** (EH.1 + EH.2), not twice; the baseline half is Gate 0
(operator) plus EH.3's fail-loud complement. Sequence accordingly: **baseline registration is sub-step zero
and everything downstream is gated on a non-empty `data/baselines.json`.**

**D-2 — Persist by merging live heads onto loaded records, never by writing `_lineages_registry` naively.**
`_lineages_registry` holds the *loaded* records with stale `head_version`, while live heads sit in
`_lineage_heads`. Writing the former persists seed values and fixes nothing. Merge with
`dataclasses.replace(_lineages_registry[lid], head_version=head)` so operator-authored `pool_path`,
`parent_chain` and `created_at` survive. Guard the hop on the registry having been **loaded from disk**, not merely on
`_lineage_heads` being non-empty: `_load_lineage_registry_if_engaged` (`:3697-3751`) synthesizes an implicit
`main` at `:3745-3751` whenever `--lineages > 1` meets an absent or malformed file, so `if _lineage_heads:`
is truthy there and would write that synthetic record to disk. Return `(registry, from_disk)` from that
function, unpack at `:4176`, and gate on `if (_lineage_heads or _extinct_lineages) and _registry_from_disk:`
— a default `--lineages 1` run then stays byte-identical and creates no file, and a registry-less
multi-lineage run creates none either. The `dataclasses.replace(...)` formula above applies only to ids
still in `_lineage_heads`; culled ids come from the pop-site snapshot (D-3). **Whole-file semantics:**
`write_lineages` (`lineages.py:215-223`) rebuilds the entire payload and `_lineages_registry` is loaded
once at `:4176` and never re-read inside the loop, so the hop is a last-writer-wins full replace — a
`scripts/lineage.py add/remove` run mid-soak is reverted at the next generation boundary. EH.1's `--help`
and module docstring must say "do not mutate the registry while an evolve run is active", and §8's
`--lineages` mitigation row means `remove` **after the run exits**. Wrap in `try/except` + `_log.exception`
mirroring `:4762`, so a disk failure never aborts a soak — the in-memory dict stays authoritative.

**D-3 — Persist extinctions as `status="extinct"`, and teach `next_lineage` to filter.** `:5203-5204` pop
culled lineages from two dicts, so a naive merge would silently delete them from disk. Retaining
`dataclasses.replace(lin, status="extinct")` is the conservative path — the API already surfaces `status`
and `EvolutionTab.tsx:1150` already renders it, falling back to grey for unknown values (`:183`). **The
trap:** `next_lineage` (`lineages.py:272`) takes every registry key with no status filter, so writing an
extinct record back would *resurrect* it into the round-robin on the next run. The filter is therefore not
optional — it ships in the same step. Note this is a real semantic change: today a restart revives culled
lineages (a bug); after EH.2 extinction is permanent across restarts. That is the intended behaviour, and
`--lineages`' one-way default (§8) is its cost.

**D-4 — Clear the control file at run start, and treat that as load-bearing rather than hygiene.**
`data/evolve_run_control.json` on disk at HEAD is `{"pause_after_round": false, "stop_run": true}` — a real
operator press that has had no effect. **Any reader added without a startup clear aborts the very next run
at generation 0.** The clear belongs at the initial "running" state write (`:4005`). **Resolved by operator decision P-6
(§ Decision Inventory): the clear is unconditional, including on `--resume`** — an explicit resume is
fresher operator intent than the stale stop, and honouring the old stop would make resume appear to do
nothing. It must emit an INFO line naming the discarded pending stop, so a superseded request is visible
in the run log rather than silently dropped. EH.4 Done-when clause (5) pins both halves.

**D-5 — Poll in two shapes, because the cooperative-cancel machinery is parallel-path only.** `halt_state`
(`:2661`) and `_signal_handler` (`:2663`, installed `:2756`/`:2765`) live *inside*
`_run_fitness_phase_parallel` (`:2583`). The serial path (`:4378`) has no handler at all — and
`launch-evolve.ps1:72` spawns `--viewer`, which forces serial. **The default operator launch path is the one
without the handler.** So EH.4 adds a boundary check at the loop head (`:4223`, before the wall-clock check),
plus a serial-fitness check at `:4380`, plus reuse of `halt_state["stop_dispatching"]` in the parallel
dispatcher (`:2785-2793`). **Drain, never kill:** Decision D-5 of the parallel dispatcher (`:2787-2798`) and
the dialog copy (`:1648` *"In-progress games will complete first"*) both specify drain-in-flight, and the
SIGINT path (`:2663-2715`) already owns hard-kill escalation. The parallel dispatcher's inner `while` spins,
so its poll must be mtime- or time-throttled.

**D-6 — Fix the launcher, not the argparse default; and fix the second caller too.** Covered in §3
Explicitly out. The non-obvious half: `.claude/skills/improve-bot-evolve/SKILL.md:216-222` is a **second,
independent caller** with the identical omission, invisible to a launcher-only fix. Its own eval fixture
(`.claude/skills/improve-bot-evolve/evals/test_scenarios.json:9`) narrates *"evolve.py runs 4 generations … Wall-clock budget of 4h is reached
after gen4"* — impossible under the real defaults. The dev-observatory `run-evolution` verb was checked and
passes **no** arguments (`dev/.claude/observatory/registry.toml`), closing the last indirect-caller escape.
A startup warning (not a fatal) covers any future caller: `--hours > 0` with an unpassed `--generations`
default of 1 is a contradiction worth one loud line.

**D-7 — Route four steps to `--reviewers deep`, and mark them seal-gated.** EH.2 (persisted-state
semantics + resume behaviour), EH.4 (a runner-side reader for an operator-armed control file, two poll
shapes, and a load-bearing startup clear), EH.7 and EH.8 (both data-safety: one prevents truncation of a
live soak's results, one bounds what an unattended commit sweeps) match the high-stakes trigger classes per
`review-deep` SKILL.md's header. **`--reviewers deep` is frozen** until skill-mesh Phase RD Step 4/#181
publishes `PhaseRdActivationSealV1` (marker: `../../../.claude/task-state/freeze.json`), and so is the whole
build toolkit — so this routing costs nothing today and is correct the day the seal lands. Do not downgrade
it to get an earlier build.

**D-8 — Sequence against Phase EI on `scripts/evolve.py`; one prohibition is hard.** EI edits the same file
in nine steps (EI.1, EI.3, EI.5, EI.6, EI.7, EI.8, EI.9, EI.10, EI.11). **Most** of this plan's regions differ textually (`:3697-3751` lineage scheduling,
`:4223-4246` loop head, `:1888-1899` fresh-run state), but **two collide outright.** (a) The generation
boundary in `run_loop` (`:5226-5231`) is claimed by BOTH — EI.1(d)/EI.3 insert a `_prune_selfplay_logs`
call there (EI plan `:356`, `:360`, `:374`, `:378`, D-3 `:232`) and EH.2 inserts the lineage persist hop
there. (b) The `build_parser` argparse block — EI adds six flags (EI `:197-202`), EH adds three
(`--control-path` beside `--state-path` `:265-273`, `--require-baselines`, `--force`). Build one phase at a
time, and **whichever builds second must re-derive its pinned anchors** — EH.2's `:5226`/`:5231`, EH.4's
`:265-273` and `:4005`, EH.7's `:1888-1899` — from the post-merge file rather than from this plan. **HARD:** EI.1/D-3 (`:232`) forbids putting retention
in `_cleanup_stale_round_files` because its sole caller sits behind `if concurrency > 1` (`:3873`) and it is
a cross-run sweeper that unlinks unconditionally, so a `--resume` would delete the prior run's traces.
**EH must not extend that sweeper to `*.log` or `logs/traces/`** — doing so silently destroys EI.1/EI.3
output and invalidates EI.14's done-when. Two further couplings: EH's lineage persistence is a
**precondition-shaped input** to EI.14's soak (EI plan `:502-504`), and EH.5 lands in the same frontend
seam as EI.12 against the same `npm run test:run` baseline of 234.

**D-9 — Two data-safety steps are included beyond the four briefed defects, and they are the highest-value
items found.** The runbook documents a defect strictly more destructive than any of the four: **there is no
lock or pid file** (`:166-175`). A second run started mid-soak calls `_clear_fresh_run_state`
(`:1888-1899`), which **truncates the first run's `evolve_results.jsonl`**. The only guard is a Windows CIM
probe in `launch-evolve.ps1:54-75` that is launcher-scoped, WSL-blind, and explicitly fails open — and
**every gate command in the runbook is a direct `python scripts/evolve.py` that bypasses it.** Its sibling
(`:200`): `EVO_AUTO=1` commits sweep the **entire git index**, so an unattended 6-hour soak commits whatever
happened to be staged, to whatever branch is checked out. Both protect the same 28–30 hours of queued gate
evidence this phase exists to make trustworthy. They are EH.7 and EH.8. **This is a deliberate scope
expansion past the four briefed defects.** Operator decision **P-3** (§ Decision Inventory) kept both.
**Coupling to carry forward:** if EH.8 is ever cut, EH.6 must be cut or held with it (P-4).

## 7. Build Steps

**Quality gates — binding on every `Type: code` step below.**

- `uv run pytest` — full suite. Baseline **re-measured at HEAD `9ccf4b8` on 2026-09-04**: `uv run pytest
  --collect-only -q` reports **2,026 selected / 2,027 collected** (the 1 deselection is
  `addopts = "-m 'not sc2'"`, `pyproject.toml:76`) in the repo `.venv`, which has the viewer extra
  installed. The earlier **2,007 / 2,024** figures were stale and are retired. A `uv sync --extra dev`
  worktree collects fewer, so **EH.1 measures its own worktree's collected count before its first edit and
  records it in the step checkpoint; every later step gates on "≥ the EH.1-recorded baseline", never on a
  hard-coded literal.** The gate that flips a step DONE runs the full suite, not the subset the step
  iterated against.
- `uv run ruff check .` — clean.
- `uv run mypy src bots --strict` — **does NOT cover `scripts/`.** Verified at `pyproject.toml:97`.
- `uv run mypy scripts/evolve.py scripts/lineage.py --strict` — **added by this phase**, because seven of
  ten steps land in `scripts/`, which the standing gate cannot see. Declare the pre-existing error count on
  EH.1 and gate on **"no NEW errors"**, not zero.
- `cd frontend && npm run test:run` — for EH.5 only. Baseline **234** (228 passing, 6 skipped). **Not
  `npm run test`**, which is bare `vitest` in watch mode.

**Step-heading notation.** Steps are `### Step EH.N:` (letter.number), matching Phases B/D/E/G/EL/EJ/EV/EI.
A strict reading of `/plan-review` §25(a)'s `^#{3,4} Step \d+:` regex does not match that form, but it is
the established convention in this repo and `/build-phase` walks these headings in the sibling plans.

**Reviewer routing and the freeze.** Four steps carry `--reviewers deep` — EH.2, EH.4, EH.7 and EH.8, per §6 D-7.
`--reviewers deep`, `build-step`, `build-phase` and `build-queue` are **all frozen** until skill-mesh
Phase RD Step 4/#181 publishes `PhaseRdActivationSealV1`; the marker is
`../../../.claude/task-state/freeze.json`. **Do not dispatch a build from this plan until that file is gone.**

**Base branch.** Build this phase on **`master-plan/phase-ev`**, the de-facto mainline, NOT on `master`
(`master` is 21 commits behind — `git rev-list --count master..HEAD` at HEAD `9ccf4b8`, 2026-09-04 — and lacks `launch-a4g.ps1`). **Fresh-worktree note:** always
`uv venv --python 3.14 && uv sync --extra dev` before the first gate; a fresh worktree inherits no `.venv`.

### Step EH.1: `scripts/lineage.py` — lineage registry CLI
- **Problem:** `write_lineages` (`src/orchestrator/lineages.py:215`) has **zero production callers** — the only way a `data/lineages.json` can exist today is an operator hand-authoring JSON, which `../operator-gate-runbook.md`:131-145 documents as the workaround. Add `scripts/lineage.py` as a direct structural mirror of `scripts/baseline.py` (same `_REPO_ROOT` preamble, same sub-parser shape) with `add <lineage_id> <head_version>` / `list` / `remove <lineage_id>` and a `--path` override, validating `head_version` against `orchestrator.registry.list_versions` the way `register_baseline` does (`baselines.py:250-254`) so a typo fails loudly rather than seeding an unreachable head. This gives `write_lineages` its first production caller and makes the registry creatable without hand-editing JSON. Do **not** touch `data/lineage.json` (**singular**) — that is the unrelated version DAG from `scripts/build_lineage.py:436` (`../operator-gate-runbook.md`:127-129).
- **Type:** code
- **Issue:** #
- **Flags:** --reviewers code --isolation worktree
- **Produces:** new `scripts/lineage.py`; new `tests/test_lineage_cli.py`; declared baseline error count for the new `mypy scripts/…` gate
- **Done when:** full suite green at the pytest baseline **this worktree records** — run `uv run pytest --collect-only -q` before the first edit and write the collected count into the step checkpoint (2,026 selected at HEAD `9ccf4b8` with the viewer extra present; a `--extra dev`-only worktree collects fewer) — ruff clean; the new `mypy scripts/evolve.py scripts/lineage.py --strict` gate records its pre-existing error count in the step's checkpoint; and `tests/test_lineage_cli.py` covers (1) `add` writes a file that `load_lineages` parses back to `{"alt": Lineage(lineage_id="alt", head_version="v10")}`, (2) `add` with an unregistered version exits 1 **and writes nothing**, (3) `list` on a missing file exits 0 with empty output, (4) `remove` is idempotent, **and removing the last remaining lineage leaves a registry that `load_lineages` returns empty for**, so `_load_lineage_registry_if_engaged` takes its `return {}` branch — this is EH.10's teardown escape hatch and §8's documented `--lineages` off-switch.
- **Depends on:** none

### Step EH.2: Generation-boundary lineage write-back
- **Problem:** `run_loop` advances each lineage head into a **local dict** at `scripts/evolve.py:5158` — under a comment that literally reads *"Persist this lineage's (possibly advanced) head"* — and drops culled lineages from two more local dicts at `:5203-5204`. Nothing flushes any of it, so heads and extinctions evaporate at process exit, a resumed run re-branches from stale heads, extinct lineages revive, and the shipped disk-backed endpoint `GET /api/evolve/lineages` (`bots/v13/api.py:1641`) returns an empty HTTP 200 throughout a live multi-lineage soak. Insert **one** persist hop in `run_loop` immediately after `gen_durations_seconds.append(...)` (`:5227-5229`) and before `generations_completed += 1` (`:5231`) — the named window is not empty, and putting the hop ahead of the append charges the registry write's disk I/O to the reported generation duration the dashboard uses for its time-remaining range (`write_run_state` docstring `:1441-1443`) — merging live heads onto the loaded records per §6 D-2 (`dataclasses.replace`, never a naive `_lineages_registry` write), guarded so the hop fires **only when the registry was actually loaded from disk**. `_load_lineage_registry_if_engaged` (`:3697-3751`) returns a bare `dict[str, Lineage]` and cannot distinguish an on-disk registry from the implicit `main` it synthesizes at `:3745-3751`; change it to return `tuple[dict[str, Lineage], bool]` with the bool captured as `bool(on_disk)` at `:3739` (before any pop), unpack at its single call site `:4176` into `_lineages_registry, _registry_from_disk`, and gate the hop on `if (_lineage_heads or _extinct_lineages) and _registry_from_disk:`. `if _lineage_heads:` alone is **not** sufficient: with `--lineages 3` and an absent *or* malformed registry (`:3725-3733` swallows the parse error into `{}`), the synthesized single `main` makes that guard truthy, so the hop would CREATE or OVERWRITE `data/lineages.json` — destroying the hand-authored file the runbook documents as today's only workaround (`../operator-gate-runbook.md`:131-145), permanently engaging multi-lineage scheduling for every later bare invocation (§8), and leaving `:4268-4271` to flip `bots/current/current.txt` to a stale recorded head. Wrap the hop in `try/except` + `_log.exception` mirroring `:4762` so a disk failure never aborts a soak. **Emit one INFO line per successful persist** naming the generation index and each lineage id with its advanced head — without it the hop leaves no per-boundary trace at all (`write_lineages` is a whole-file replace, so a post-hoc artifact shows only the final payload and one mtime), and **Done-when clause (8) below — the only gate this phase still has that can catch a silently omitted persist trace — has nothing to assert on**. Persist culled lineages as `status="extinct"` rather than dropping them — which requires capturing the record **at the cull site**, because `:5204` pops the culled id out of `_lineages_registry` before the hop ever runs. Declare `_extinct_lineages: dict[str, Lineage] = {}` beside `_lineage_heads` (`:4173`, outside the generation loop so it accumulates across generations) and, immediately before the two pops at `:5203-5204`, capture `_extinct_lineages[_cull.lineage_id] = dataclasses.replace(_lineages_registry[_cull.lineage_id], head_version=_cull.head_version, status="extinct")` — `_cull.head_version`, **not** the registry's seed head, since `_lineages_registry` is loaded once at `:4176-4180` and never advanced, and a seed head would contradict the extinction event written at `:5213`. Leave both pops unchanged so in-run scheduling stays byte-identical. The hop's payload is then the **union** of the live records and `_extinct_lineages`: `write_lineages` (`lineages.py:215`) serializes the whole dict, so any key missing from the payload is deleted from disk. **And in the same step teach `next_lineage` (`lineages.py:272`, currently `ids = list(registry.keys())`) to filter `status == "active"`** — without that filter a persisted extinct record resurrects into the round-robin on the next run (§6 D-3). Filter semantics: `ids = [k for k, v in registry.items() if v.status == "active"]`; if that list is empty but the registry is not, fall back to all ids and emit a WARNING — `next_lineage` raises `ValueError` on an empty list (`lineages.py:273-274`) and its call site (`scripts/evolve.py:4264`) sits inside the generation loop with no guard, so an all-extinct on-disk registry would otherwise crash the next run outright.
- **Type:** code
- **Issue:** #
- **Flags:** --reviewers deep --isolation worktree
- **Produces:** modified `scripts/evolve.py` (persist hop); modified `src/orchestrator/lineages.py` (`next_lineage` status filter); extended `tests/test_evolve_cli.py`; extended `tests/test_lineages.py`
- **Done when:** full suite green at ≥ the EH.1-recorded pytest baseline (§7) and (1) a new integration test drives real `run_loop` for 2 generations with a 2-lineage seeded registry and asserts **on the file** via `load_lineages` that `persisted["main"].head_version != "v0"` — the load-bearing inequality, which fails at HEAD; (2) the same test asserts `persisted["main"].created_at` equals the seed value, proving `dataclasses.replace` preserved operator-authored fields; (3) a back-compat test with `args.lineages = 1` and no registry asserts `not (tmp_path/"data"/"lineages.json").exists()`; (4) an extinction test asserts `load_lineages(...)["line-2"].status == "extinct"` **and** that a subsequent `next_lineage` call never returns `"line-2"`; (5) `bots/v13/api.py` is confirmed unmodified — the reader already parses this exact shape; (6) with `args.lineages = 3`, (a) no registry file → the run completes and `data/lineages.json` is still **absent** afterwards, and (b) a registry file containing `{not json` → the run completes and the file's bytes are **unchanged** (byte-equality is the data-loss assertion; "the run completes" alone is not enough); (7) a unit test asserts `next_lineage` on a registry whose every record is `status="extinct"` returns the first id and warns rather than raising; and (8) a `caplog` assertion on clause (1)'s own 2-generation integration run proves **one INFO record per generation boundary**, each naming the generation index and every persisted lineage id with its advanced `head_version` — added because the deleted EH.11 was the only place that per-boundary trace was ever read (operator decision **P-2 as overridden**, § Decision Inventory), so without this clause the INFO-line requirement in the Problem above would ship unenforceable and every gate would still go green.
- **Depends on:** EH.1

### Step EH.3: Fail loudly when the gauntlet is inert
- **Problem:** With `data/baselines.json` absent, `--fitness-mode baseline|both` degrades to `parent` (`scripts/evolve.py:3924-3930`) and `--panel-floor` is INERT (`:3938-3950`) — each with a startup WARNING but **no error and no failed run**, which `../operator-gate-runbook.md`:72-80 notes is "easy to miss in a long log". The operator-visible cost is a six-hour soak that produces a green-looking record for capabilities that never executed. Add `--require-baselines` (default OFF, preserving the byte-identical bare invocation) which converts the degradation into a **two-second non-zero exit** naming the missing registry and the exact `scripts/baseline.py add` command from `../operator-gate-runbook.md`:85-92. Keep the existing warn-and-proceed as the default so no queued gate command changes behaviour unless it opts in.
- **Type:** code
- **Issue:** #
- **Flags:** --reviewers code --isolation worktree
- **Produces:** modified `scripts/evolve.py`; extended `tests/test_evolve_cli.py`
- **Done when:** full suite green at ≥ the EH.1-recorded pytest baseline (§7); a test asserts `--fitness-mode both --require-baselines` with an empty registry exits non-zero **before any game is dispatched** and the message names `scripts/baseline.py`; a paired negative test asserts the same invocation **without** the flag still warns and proceeds, pinning the default as byte-identical.
- **Depends on:** none

### Step EH.4: Runner-side control-file reader
- **Problem:** The dashboard's Stop Run button drives five hops — `EvolutionTab.tsx:1620` → `handleStopConfirm` `:1401` → `useEvolveRun.ts:383` `PUT /api/evolve/control` → `bots/v13/api.py:1460-1502` → atomic write of `data/evolve_run_control.json` — and terminates in a file **no runner opens**; grepping `scripts/` and `src/` yields exactly one hit, and it is a comment in `launch-evolve.ps1:31` stating that nothing reads it. `pause_after_round` (`EvolutionTab.tsx:1411`, checkbox `:1634-1641`) is a **second silent no-op on the identical file** — **cut from this step by operator decision P-1** (§3 Explicitly out): `read_run_control` still parses the key, because the API writes it and a reader must not choke on it, but nothing acts on it, and EH.5 disables the checkbox so the UI stops promising it. Add `--control-path` beside `--state-path` (`:265-273`) read via `getattr(args, "control_path", None)` so the ~40 tests built by `_build_args` stay green; add `read_run_control(path)` beside `write_run_state` (~`:1412`) returning both-False on missing/`OSError`/`JSONDecodeError`/**non-dict payload**/**non-bool key value** (§5) and **not** requiring the phantom `updated_at` field; **clear the file to both-False at the initial running-state write (`:4005`)** — load-bearing, because a `stop_run: true` is armed on disk right now and a reader without the clear would abort the next run at generation 0 (§6 D-4). Per operator decision **P-6** the clear is **unconditional, including on `--resume`** (an explicit resume is a fresher operator intent than the stale stop, and honouring the old stop would make resume appear to do nothing) — but it must **emit an INFO line naming the pending stop it discarded**, so a superseded stop is visible in the run log rather than silently dropped. Then poll at the loop head (`:4223`, before the wall-clock check) setting `stop_reason = "dashboard-stop"`, at the serial fitness loop (`:4380`), and via `halt_state["stop_dispatching"]` in the parallel dispatcher (`:2785-2793`) — **two shapes, because the cancel machinery is parallel-path-only while `--viewer` forces serial** (§6 D-5). **Drain in-flight games, never kill** the SC2 subprocess. Update the three stale `SKILL.md` claims (`:63`, `:359`, `:462`), `wiki/operator-commands.md:335` and the `launch-evolve.ps1:31` comment **in this step, after the reader lands** — `tests/test_evolve_cli.py:4337-4352` parses stop reasons out of SKILL.md and asserts each appears in evolve.py source, so the doc edit only passes once the code emits the string. **Do not touch `documentation/plans/evolve-viewer-plan.md`** — its five now-false "not wired" claims (`:657, :665, :684, :803, :936`) are tracked in a sibling issue per operator decision **P-7**, because that plan has two open operator gates.
- **Type:** code
- **Issue:** #
- **Flags:** --reviewers deep --isolation worktree
- **Produces:** modified `scripts/evolve.py`; modified `.claude/skills/improve-bot-evolve/SKILL.md`; modified `documentation/wiki/operator-commands.md`; modified `scripts/launch-evolve.ps1` (comment only, ASCII/no-BOM); extended `tests/test_evolve_cli.py`
- **Done when:** full suite green at ≥ the EH.1-recorded pytest baseline (§7) and (1) an integration test drives real `run_loop` with `hours=0.0` and **`generations=0` (unbounded)** — so the control file is the only thing that *could* stop the loop — where the fitness stub arms `stop_run` on its first call, asserting `rc == 0`, `state["status"] == "completed"`, `state["generations_completed"] == 1`, and `"- Stop reason: dashboard-stop"` in the run log; (2) a stale-flag test arms `stop_run: true` **before** `run_loop` and asserts at least one generation still executes, proving the startup clear fired; (3) a corrupt-file test parameterised over `"{not json"`, `"[]"`, `"null"` and `'"stop"'` asserts the run completes normally in all four cases, plus a non-bool test writing `'{"stop_run": "yes"}'` asserting the run does **not** stop early (non-bool coerces to False), pinning the coercion decision rather than a crash; (4) a cross-file pin asserts evolve.py's `--control-path` basename equals `bots/v13/api.py:1369`'s `_EVOLVE_CONTROL_FILE`; (5) a resume test arms `stop_run: true`, runs with `--resume`, and asserts the run **proceeds** and the run log carries the INFO line naming the discarded pending stop (P-6); (6) a test writing `'{"pause_after_round": true}'` asserts the run is **unaffected** — pinning P-1's cut, so a later re-wiring is a deliberate change rather than an accident.
- **Depends on:** none

### Step EH.5: Frontend stop honesty
- **Problem:** `useEvolveRun.ts:381-391` never binds the `fetch` Response, so `res.ok` genuinely cannot be checked and a 400/500 still produces a success toast; `EvolutionTab.tsx:1401-1407` fires `showMessage("Stop requested — run will end at the next generation boundary")` unconditionally after the PUT, with no try/catch and no error branch. Even after EH.4 makes the stop real, the UI has no way to show a request that failed to reach the API. Bind and check the Response; replace the fire-and-forget toast with a persistent "Stop requested — pending" badge driven by `control.data.stop_run` and cleared when the runner clears the flag at the next run start; and fix the `:1330` empty-state copy, which currently blames the pre-EL single-lineage loop for an empty lineage panel that was actually caused by the missing writer. **Disable the "Pause after current generation" checkbox (`:1634-1641`) and label it not-implemented** — operator decision **P-1** cuts the runtime work for `pause_after_round`, and cutting the work must not leave the control lying. Use `disabled` plus a short title/helper string; do not remove the markup, so re-arming it later is a one-line change.
- **Type:** code
- **Issue:** #
- **Flags:** --reviewers code --isolation worktree
- **Produces:** modified `frontend/src/hooks/useEvolveRun.ts`; modified `frontend/src/components/EvolutionTab.tsx`; extended `frontend/src/components/EvolutionTab.test.tsx`
- **Done when:** `cd frontend && npm run test:run` green at ≥234 (228 passing, 6 skipped baseline) with a new case asserting the pending badge renders from `control.stop_run` rather than from a toast, a case asserting a non-ok PUT surfaces an error instead of a success message, and a case asserting the pause checkbox renders **disabled** with its not-implemented label (P-1); `npm run build` clean; backend suite untouched at ≥ the EH.1-recorded pytest baseline (§7).
- **Depends on:** EH.4

### Step EH.6: Launcher `--generations` passthrough
- **Problem:** `scripts/evolve.py` declares `--generations` with `default=1` (`:217`) and the loop breaks with `stop_reason = "generations-reached"` as soon as the cap is met (`:4229-4236`). `scripts/launch-evolve.ps1` has exactly one parameter, `[double]$Hours = 4` (`:45`), and its spawn line (`:72`) forwards only `--hours` and `--viewer` — so the observatory `run-evolution` button and every `.\scripts\launch-evolve.ps1 -Hours N` promise an N-hour soak and deliver **one generation** (~20 min), then exit cleanly with nothing anywhere saying so. `../operator-gate-runbook.md`:40-45 rates this **fatal to EV.5, EJ.8 and EL.7**. Add `[int]$Generations = 0` at `:45` and `--generations $Generations` to `:72`; correct the false header comment at `:27-29` (which claims the run "keeps going headless to its `--hours` budget"); add `--generations 0` plus a flag-table row to the **second independent caller**, `.claude/skills/improve-bot-evolve/SKILL.md:216-222` and `:34-53`; and add a non-fatal startup WARNING in `scripts/evolve.py` when `--hours > 0` and `--generations` was left at its default of 1, naming the contradiction. **Do not flip the argparse default** (§3 Explicitly out) and leave `tests/test_evolve_cli.py:457` `assert args.generations == 1` untouched — it is the tripwire that forces the docstring and wiki to move together if a future change ever does flip it. `scripts/launch-evolve.ps1` is pinned ASCII-only with no BOM by `tests/test_evolve_cli.py:4362-4367`. Sequenced after EH.8 so the unattended multi-hour run this step unblocks cannot sweep the operator's staged index into an `[evo-auto]` commit (operator decision P-4, § Decision Inventory).
- **Type:** code
- **Issue:** #
- **Flags:** --reviewers code --isolation worktree
- **Produces:** modified `scripts/launch-evolve.ps1`; modified `scripts/evolve.py`; modified `.claude/skills/improve-bot-evolve/SKILL.md`; extended `tests/test_evolve_cli.py`
- **Done when:** full suite green at ≥ the EH.1-recorded pytest baseline (§7) and (1) `test_launch_evolve_ps1_pins_the_viewer_spawn_contract` (`:4355`) gains an assertion that `--generations` appears in the parsed `$evolveCmd` line, mirroring its existing `assert "--viewer" in spawn` at `:4387`, plus a separate assertion that the param block declares a default of 0; (2) a new `caplog` test asserts a WARNING naming both `--hours` and `--generations` fires for `hours=4.0, generations=1`; (3) a paired negative test with an explicitly-passed `--generations 1` and `hours=0.75` asserts **no** warning, so the guard cannot regress into noise; (4) the ASCII/no-BOM launcher test still passes.
- **Depends on:** EH.8

### Step EH.7: Single-run lock + stale run-state reconciliation
- **Problem:** *"One evolve run at a time, machine-wide"* is enforced **by convention, not code** (`../operator-gate-runbook.md`:166-175): "There is no lock file, no pid file." Two runs collide on `data/evolve_*.json`, both flip `bots/current/current.txt`, and — worst — a second run starting mid-soak calls `_clear_fresh_run_state` (`scripts/evolve.py:1888-1899`), which **truncates the first run's `evolve_results.jsonl`**, destroying the evidence of a soak in progress. The only guard is a Windows CIM probe in `launch-evolve.ps1:54-75` that is launcher-scoped, Windows-only, blind to a WSL run, and explicitly fails open — and **every gate command in the runbook is a direct `python scripts/evolve.py` that bypasses it entirely.** Stamp pid + start timestamp into the already-existing `data/evolve_run_state.json` at the `:4005` write and check it at startup. **Read the pre-existing file strictly before the `:4005` write** — that write is a whole-file `_atomic_write_json` (`:1472`) and would otherwise clobber the very record being checked; both precede `_clear_fresh_run_state` (`:4079`), which is what truncates `evolve_results.jsonl`. **Liveness is `psutil.pid_exists(pid)`, never `os.kill(pid, 0)`:** on Windows (the sole declared platform, `pyproject.toml:16`) `os.kill` resolves to `OpenProcess` + `TerminateProcess(handle, 0)`, so the POSIX idiom *kills* the soak this lock exists to protect — and would kill the pytest process in this step's own Done-when (1). `psutil` is guaranteed importable with no fallback branch: it is an **unconditional transitive dependency of the core `portpicker>=1.6`** (`pyproject.toml:34`; `uv.lock` portpicker block is `dependencies = [{ name = "psutil" }]`, distinct from the `viewer`-extra declaration at `uv.lock:171`), and `bots/v*/system_info.py:35` already imports it at module scope inside the current baseline — add no lazy import and no degrade-on-`ImportError` path, because a lock that fails open is not a lock. Guard pid reuse by comparing the stamped start timestamp against `psutil.Process(pid).create_time()`: a process younger than the stamp means the pid was recycled and the record is stale. Then: if the recorded pid is alive and the state is `running`, refuse to start with a non-zero exit naming the live pid; and — generalizing — refusal requires a **positively confirmed** live pid, so any `running` record from which liveness cannot be established (pid key absent, non-integer, or the probe itself raising — the pid-less shape is the ONLY shape on every pre-EH.7 machine, including the 17-key record on disk at HEAD) is treated as stale: reap the stale record and proceed, emitting an INFO line naming the reaped `run_id` and `started_at` so a mixed-version collision is diagnosable after the fact — which also fixes the write-only staleness defect (`:172`, `:182-186`), where the file has claimed `"status": "running"` since a 2026-08-28 launcher run that never completed a generation, making the dashboard render a phantom soak indefinitely. Provide `--force` to override for the genuine recovery case.
- **Type:** code
- **Issue:** #
- **Flags:** --reviewers deep --isolation worktree
- **Produces:** modified `scripts/evolve.py`; extended `tests/test_evolve_cli.py`
- **Done when:** full suite green at ≥ the EH.1-recorded pytest baseline (§7) and (1) a test with a state file naming **this** process's own live pid asserts a second `run_loop` refuses with a non-zero exit and the message names the pid, and asserts `evolve_results.jsonl` is **byte-unchanged** — the truncation is the actual harm; (2) a test with a state file naming a dead pid asserts the run proceeds and the stale record is reaped; (3) `--force` overrides (1); (4) a fresh checkout with no state file is unaffected; (5) a test seeded with the **real pre-EH.7 record shape** — `status: "running"`, a past `started_at`, and NO `pid` key, i.e. exactly what `write_run_state` (`:1412`) produces today — asserts the run **proceeds** (rc 0, not a refusal) and that the emitted INFO line names the reaped `run_id` and `started_at`; a sibling case with `"pid": "not-an-int"` takes the same path; (6) a recycled-pid test where the state file names a live pid whose `create_time()` is newer than the stamped start timestamp asserts the run proceeds and reaps; (7) a source-level pin asserts the lock code contains no `os.kill`, mirroring the cross-file pin at `tests/test_evolve_cli.py:4337-4352`. Clause (1) additionally asserts the probing process is **still alive** after the refusal — the canary that catches a `TerminateProcess`-shaped probe.
- **Depends on:** none

### Step EH.8: Path-scope the `[evo-auto]` commit
- **Problem:** `EVO_AUTO=1` commits **sweep the entire git index**, not just `bots/<v>/` (`../operator-gate-runbook.md`:200; `.claude/rules/evolve.md` § Pre-launch hygiene), so anything an operator happened to stage rides into the next `[evo-auto]` commit — on whatever branch is checked out, during an unattended six-hour soak. The only mitigation today is a pre-flight `git diff --staged --stat` the operator has to remember. **The `git add` is already path-scoped** — `scripts/evolve.py:1564` is `["git", "add", f"bots/{new_version}/", "bots/current/current.txt"]`, the only `git add` call site in the repo — so there is nothing to replace there, and it must be **kept**: a fresh `bots/<vN>/` is untracked, so the add is what makes those files committable at all. The sweep is the **bare commit** at `:1592`, `["git", "commit", "--no-verify", "-m", msg]`, which carries no pathspec and no `-a` and therefore commits everything already in the index — evolve.py's own docstrings say exactly this at `:1646` and `:1678-1681`. Fix **four** sites, not one: (a) make the promote commit partial — `git commit --no-verify --only -m msg -- bots/<new_version>/ bots/current/current.txt`; (b) immediately before it, read `git diff --cached --name-only` and log an **ERROR naming any staged path outside that set — log, never abort**, because a refusal produces no commit at all, fails this step's own Done-when (1), and would silently drop a promotion on every generation of an unattended soak; (c) `git_revert_evo_auto` carries the identical bare commit at `:1750` and is live via `scripts/evolve.py:3808` and `scripts/evolve_inject_one.py:287` — scope it too, but verify the shape in a scratch repo first, since `git revert --no-commit` leaves sequencer state and a partial commit there may be refused (fall back to `git revert --quit` after the scoped commit, or an isolated `GIT_INDEX_FILE`); (d) `_reset_staged_promote` (`:1657`) and `_reset_staged_revert` (`:1687`) both run `git reset HEAD -- .`, which unstages the operator's unrelated work on every commit-failure path — scope both resets to the same paths (the revert path derives its set from `git show --name-only --pretty= <promote_sha>`, since a pre-EH.8 promote commit may itself have swept foreign paths). Correct the stale rationale comment at `:1585-1586` in the same edit — `--no-verify` skips `scripts/check_sandbox.py` entirely, and per `.claude/rules/evolve.md`:25 that hook "blocks invalid paths but doesn't enforce ONLY these paths" — but keep `--no-verify` itself; the WSL shebang rationale below it is real. Note the partial-commit gotcha: `git commit -- <paths>` commits the WORKING-TREE state of those paths, identical to the staged state here only because the `git add` immediately precedes it. This is the workspace's standing rule for git-touching automation (`dev/.claude/rules/working-directory.md`: "Prefer path-scoped `git add <paths>`").
- **Type:** code
- **Issue:** #
- **Flags:** --reviewers deep --isolation worktree
- **Produces:** modified `scripts/evolve.py` (`git_commit_evo_auto`, `git_revert_evo_auto`, `_reset_staged_promote`, `_reset_staged_revert`); extended `tests/test_evolve_cli.py`
- **Done when:** full suite green at ≥ the EH.1-recorded pytest baseline (§7) and a test that stages an unrelated file in a temp git repo, runs the promotion commit path, and asserts (1) the resulting commit contains **only** the promotion paths and (2) the unrelated staged file is still staged and uncommitted afterwards; (3) the same two assertions hold for `git_revert_evo_auto` — an unrelated staged file survives the rollback commit still staged; and (4) a forced commit failure leaves the unrelated file staged, proving the `_reset_staged_*` cleanups no longer run `git reset HEAD -- .`.
- **Depends on:** none

<!-- autofix-applied: 2026-09-05 -->
### Step EH.9: Producer→consumer smoke gate
- **Problem:** Every existing lineage test either calls `write_lineages` directly as a fixture seed (`tests/test_evolve_cli.py:2156` and five siblings) or hand-writes `lineages.json` as JSON (`tests/test_api_evolve_lineages.py:84` and four siblings). **That is precisely why this defect survived a 2,026-test suite**: both endpoints are asserted, the relationship between them never is. Add one chain smoke that wires the real components: seed by calling `scripts/lineage.py`'s `main()` **in-process** under the same `monkeypatch.setattr(lineages_mod, "_repo_root", lambda: tmp_path)` harness §4 already designates (`tests/test_evolve_cli.py:2133-2156`), additionally passing `--path tmp_path / "data" / "lineages.json"`; the seeded path and the path `run_loop` reads MUST be the same file, because `_load_lineage_registry_if_engaged` hardcodes `default_lineages_path()` (`:3723`) with no path parameter and an unmonkeypatched seed would read — or write — the **real** repo-root registry. Drive real `run_loop` with `--lineages 3` for two generations, assert `_load_lineage_registry_if_engaged` returned `len(registry) == 3` (unpacking the `(registry, from_disk)` tuple EH.2 introduces, and asserting `from_disk is True`) rather than the single implicit `main` from `:3742-3751`, then assert the on-disk file, re-read via `load_lineages`, still contains **all three** records, that the **two** lineages which took a round-robin turn have `head_version` differing from their seed, and that the **unscheduled** third record is present with its seed `head_version` and seed `created_at` intact — a generation is one lineage's turn (`:4249-4272`), so three lineages over two generations leaves one unscheduled by construction, and that survivor assertion is the only one in the phase that catches a persist hop which writes only the live `_lineage_heads` dict, then serve `GET /api/evolve/lineages` against that same directory and assert the response body carries a non-empty `lineages` array with the **advanced** `head_version`. **Point the API at that directory explicitly** — `configure(tmp_path/"data", tmp_path/"logs", tmp_path/"replays", evolve_dir=tmp_path/"data")` before driving it through `fastapi.testclient.TestClient(app)`, reusing the `evolve_dir`/`client` fixture pair at `tests/test_api_evolve_lineages.py:26-41`. Monkeypatching `lineages_mod._repo_root` repoints the **writer only**; the endpoint resolves its directory from the module-level `_evolve_dir` that `bots.v13.api.configure(...)` sets, so without this call the consumer half of the chain reads the real repo-root registry and the smoke gate silently proves nothing. No SC2 process and no real games — the fitness function is stubbed; everything else is production code on its real import path.
- **Type:** code
- **Issue:** #
- **Flags:** --reviewers code --isolation worktree
- **Produces:** new chain-smoke test in `tests/test_evolve_cli.py` (or `tests/test_evolve_chain_smoke.py`); extended `tests/test_api_evolve_lineages.py`
- **Done when:** full suite green at ≥ the EH.1-recorded pytest baseline (§7); the smoke completes in under 60 seconds; it **fails** when run against the pre-EH.2 persist hop (verify by reverting the hop locally and observing red — record that red-first evidence in the step checkpoint); and it asserts the API response, not just the file.
- **Depends on:** EH.1, EH.2

### Step EH.10: Operator verification — Gate 0 plus one armed short run
- **Problem:** Six of the nine preceding steps change behaviour that only a real run can demonstrate, and three of them (EH.4, EH.6, EH.7) are deployment-seam changes — a launcher script, a control file the dashboard writes, and a startup guard — which unit tests structurally cannot exercise against the real environment. Perform the runbook's **Gate 0** first: `uv run python scripts/baseline.py add v10 v10 --note "frozen anchor"`, again for `v13`, then `list` (`../operator-gate-runbook.md`:85-92, :245-248). Then seed a 2-lineage registry with the new `scripts/lineage.py`, start a short run through the **launcher** (the real production entry point for the observatory button), and confirm on live substrate: the run does not stop after one generation; the dashboard's Lineages panel shows non-empty data with advancing heads; pressing **Stop Run** ends the run at the next generation boundary and the badge reflects a pending state until it does; and a second `python scripts/evolve.py` launched mid-run is refused rather than truncating `evolve_results.jsonl`.

  **Pre-flight, run from the repo root:**

  ```powershell
  cd $env:USERPROFILE\dev\Alpha4Gate
  git branch --show-current          # expect master-plan/phase-ev
  git diff --staged --stat           # MUST be empty - EH.8 has landed by now (EH.10 -> EH.6 -> EH.8), so this is belt-and-braces
  uv run python scripts/baseline.py list
  uv run python scripts/lineage.py list
  uv run python scripts/lineage.py add main v13
  uv run python scripts/lineage.py add line-2 v10
  uv run python scripts/lineage.py list      # expect 2 active records
  .\scripts\launch-evolve.ps1 -Hours 1 -Generations 0
  ```

  Do **not** run the launcher bare — post-EH.6 its defaults are `-Hours 4 -Generations 0`, an unbounded
  four-hour soak, which is exactly the long slot this step declines to consume. `-Hours 1` is a ceiling,
  not a plan: check (3), pressing **Stop Run**, is the intended terminator, pressed once the second
  generation boundary has been observed. Budget ~45-60 min of machine time at ~20 min/generation,
  serialized machine-wide against every other pending soak. The existing pid-less
  `data/evolve_run_state.json` is **expected** to be reaped by the first launch and must NOT be
  hand-deleted beforehand — deleting it converts this step's most valuable real-world check into a re-run
  of EH.7's Done-when (4).

  **Post-flight teardown, same shell:**

  ```powershell
  uv run python scripts/lineage.py list      # take the seeded ids from here, do not assume names
  uv run python scripts/lineage.py remove main
  uv run python scripts/lineage.py remove line-2
  uv run python scripts/lineage.py list      # expect empty
  git log --oneline -20                      # capture every [evo-auto] commit this run produced
  ```
- **Type:** operator
- **Issue:** #
- **Produces:** a verification record under `documentation/soak-test-runs/` with the pre-flight state, the observed outcome of each of the four checks, and the run log excerpt showing `Stop reason: dashboard-stop`
- **Done when:** all four checks are observed and recorded **from the live environment's own evidence** (dashboard screenshot, run log, `data/lineages.json` before/after) rather than inferred from tests; Gate 0's `baseline.py list` output is captured; **the seeded registry is torn down before the record is filed** — remove every lineage id the run's own `scripts/lineage.py list` reports (`main`/`line-2` are only the runbook's example at `../operator-gate-runbook.md`:140-143), then capture a post-flight `list` showing none; `remove` routes through `write_lineages`, so the file normally remains on disk as `{}`, which is the pass condition, not a failure. If the operator deliberately keeps the registry live to feed EL.7 (#279), the record must say so **in writing** — the default is teardown, because a surviving non-empty registry engages multi-lineage for every later bare invocation (`scripts/evolve.py:3739-3740`), including EJ.7 #288, EJ.8 #289, EV.5 #295, Phase 7 Step 6 #280 and EI.14. **Do not tear down `data/baselines.json`** — Gate 0's frozen anchors are meant to persist (§3) and EH.3 depends on them. Commit posture is also recorded: the launcher cannot pass `--no-commit`, so re-confirm `git diff --staged --stat` is empty and `git branch --show-current` is `master-plan/phase-ev` immediately before launching, then capture `git log --oneline` for every `[evo-auto]` commit the run produced — or record explicitly that none were — plus `git show --stat` for each, confirming only `bots/<vN>/` and `bots/current/current.txt` paths appear. **Claims survival and control-correctness only** — this is not a throughput or fitness verdict, and it deliberately does **not** consume a long soak slot. **No step in this phase owns the multi-hour tier** — operator decision **P-2 as overridden** cut the co-tenant `Type: wait` step the draft carried (§3), so EH.10 is the phase's terminal verification and there is nothing downstream to hand off to. EH.10 claims nothing about time-dependent behaviour; whatever EL.7 (#279) or EI.14 happens to show is opportunistic evidence this plan neither owes nor collects.
- **Depends on:** EH.1, EH.2, EH.4, EH.5, EH.6, EH.7, EH.9

## 8. Risks and Open Questions

| Item | Risk | Mitigation |
|---|---|---|
| Stale `stop_run` armed on disk | `data/evolve_run_control.json` currently holds `{"pause_after_round": false, "stop_run": true}`. A reader shipped without the startup clear aborts the very next run at generation 0 — and it would look exactly like the feature working | D-4: the clear at `:4005` is part of EH.4, and EH.4's done-when clause (2) is the test that pins it |
| Extinction becomes permanent | Persisting `status="extinct"` is a real semantic change: today a restart revives culled lineages (a bug), afterwards it does not | D-3, and the `next_lineage` status filter ships in the same step (EH.2) so a persisted extinct record cannot resurrect into the round-robin |
| `--lineages` has no off-switch | Once a non-empty registry exists on disk, multi-lineage scheduling engages for every later bare invocation (`../operator-gate-runbook.md`:147). EH.1 makes this reachable, so a gate-4 artifact silently changes behaviour for gates 5-8 | Explicitly out of scope; `scripts/lineage.py remove` is the documented escape, and the runbook's "move the file aside when you are done" stands. Revisit if EH.10 finds it painful |
| Parallel-dispatcher poll cost | The dispatcher's inner `while pending or in_flight` (`:2779`) spins; an unthrottled control-file read there would hammer the filesystem | EH.4 throttles by mtime or elapsed time. The loop-head and serial-fitness polls are once-per-generation and once-per-imp — negligible |
| `scripts/evolve.py` contention with Phase EI | EI edits the same file in nine steps; most regions differ, but the generation boundary (`:5226-5231`) and the `build_parser` argparse block collide outright | D-8: build one phase at a time. **Hard prohibition:** do not extend `_cleanup_stale_round_files` to `*.log` or `logs/traces/` — it would destroy EI.1/EI.3 evidence |
| `mypy` blind spot | `mypy src bots --strict` does not cover `scripts/`, where seven of ten steps land | A phase-added `mypy scripts/evolve.py scripts/lineage.py --strict` gate, baselined on EH.1 and gated on "no NEW errors" |
| `lineage.json` vs `lineages.json` | Singular is the version DAG from `scripts/build_lineage.py:436`; plural is the registry. Same directory, one character apart | Called out in §4 as DO-NOT-TOUCH and in EH.1's Problem. Any automation must use `default_lineages_path()`, never a literal |
| Pid reuse in the single-run lock | A recycled pid makes the lock refuse a legitimate run, and the operator sees a phantom soak exactly as today | EH.7 compares the stamped start timestamp to `psutil.Process(pid).create_time()` so a recycled pid reaps automatically; `--force` remains the manual escape |
| **No unattended evidence of this phase's own** | Operator decision **P-2 as overridden** (ship without) removed the only multi-hour observation step, so EH.2's per-generation persist hop is never watched across a real six-hour run and EH.8's `[evo-auto]` commit scoping is never observed against a live unattended auto-committing loop. Both ship on test and short-run evidence alone | Accepted, not mitigated — the operator took this trade knowingly. EH.9's chain smoke plus EH.10 (~45–60 min, armed) are the whole end-to-end tier; EH.2's new Done-when clause (8) at least gates the persist trace in test. Any EL.7 (#279) / EI.14 observation is opportunistic and owed by no step here. See §3 Explicitly out and §9 |
| **Decided — P-1 … P-9**, one later reversed | Nine operator decisions were taken 2026-09-04, closing every open question this plan carried except the one below. The former Open-1 (resume clears `stop_run`), Open-2 (keep EH.7 + EH.8), Open-3 (confirm EH.6) and Open-4 (`-Generations` parameter) are all resolved. **P-2 was subsequently overridden by operator final call the same day** (ship without the multi-hour observation step); the other eight stand as applied | See the **Decision Inventory** at the foot of this plan — the P-2 row carries its own `changed` stamp. Each decision names the steps it binds; the two couplings that survive are recorded there |
| **Open — 5, out of scope** | Extinction cannot fire at EL.7's own nominal settings even after this phase: `population.py:242` is `if len(lineages) <= cap`, and `--lineages 3 --population-cap 3` is keep-all (`../operator-gate-runbook.md`:101-104, Blocker A) | A **plan-text** fix to `evolution-lines-plan.md`, not code. Flagged here so EL.7 is not re-queued believing EH fixed it |

## 9. Testing Strategy

**The load-bearing negative finding.** The current 2,026-test suite goes **fully green** on the central
defect of this phase. Six tests in `tests/test_evolve_cli.py` (`:2156`, `:2240`, `:2321`, `:2372`, `:2439`,
`:2584`) drive real `run_loop` through the production entry point and are docstring'd as integration tests —
but every one of them calls `write_lineages` as a **fixture seed** and then asserts only on pointer flips
and scheduling order, never on the file afterward. Five more in `tests/test_api_evolve_lineages.py`
hand-write the JSON, bypassing the writer entirely. Producer and consumer are each asserted; the
relationship between them never is. That is exactly the mock-bounded blind spot
[`code-quality.md`](../../../.claude/rules/code-quality.md) § "New components require an integration test
through the production caller" describes, and it is why EH.9 exists.

**New tests, by class:**

- *CLI unit* — `tests/test_lineage_cli.py`, mirroring `tests/test_baselines.py` (EH.1).
- *Integration through the production caller* — the entry point differs per step and must be named
  explicitly: `run_loop` (`scripts/evolve.py:3759`, reached from `main()` at `:5363`) for EH.2, EH.3, EH.4,
  EH.7 and EH.8; **`scripts/launch-evolve.ps1` itself** for EH.6, which pytest reaches only through the
  file-parsing test at `:4355` — that test *is* the integration test for the launcher contract.
- *Negative / default-preservation* — every new flag gets a paired test proving the bare invocation is
  byte-identical: `--require-baselines` off still warns and proceeds (EH.3); `--lineages 1` writes no file
  (EH.2); an explicit `--generations 1` raises no warning (EH.6); no state file means no lock refusal
  (EH.7).
- *Trap tests* — the stale armed `stop_run` (EH.4), the corrupt control file (EH.4), the resurrection of a
  persisted extinct lineage (EH.2), and the unrelated staged file surviving a promotion commit (EH.8).
  Each of these encodes a failure mode that a naive implementation would ship.
- *Cross-file producer/consumer pins* — assert evolve.py's `--control-path` basename equals
  `bots/v13/api.py:1369`'s `_EVOLVE_CONTROL_FILE` (EH.4), in the style of the existing SKILL.md/evolve.py
  guard at `:4337-4352`, so a rename on either side fails a test instead of silently re-severing the link.
- *Frontend* — `EvolutionTab.test.tsx` gains a pending-badge case and a non-ok-response case (EH.5).

**Existing tests that will need attention.** `tests/test_evolve_cli.py:4337-4352` parses stop reasons out of
`improve-bot-evolve/SKILL.md` and asserts each appears in evolve.py source — it **blocks** updating SKILL.md
before the reader lands, which is the correct ordering and is written into EH.4. `:4362-4367` pins
`launch-evolve.ps1` as ASCII with no BOM — EH.4 and EH.6 both edit that file. `:457`
`assert args.generations == 1` must stay untouched (§6 D-6). The ~40 tests built by `_build_args`
(`:171-211`) set no `control_path` or lock attribute, so every new read must use
`getattr(args, …, None)`. `tests/test_api_evolve_lineages.py:114` `assert body["lineages"] == []` pins the
empty response for a **missing** file and remains correct.

**End-to-end verification — two tiers, each with an owner, and a third this phase deliberately declines.**
EH.9 is the 60-second producer→consumer smoke gate and must go green *before* EH.10. EH.10 is the
live-substrate check — the only place the launcher, the dashboard control file and the startup lock meet
the real environment — and it is bounded at ~45–60 minutes. It is also the **terminal** step: operator
decision **P-2, overridden 2026-09-04** (D2 = ship without), removed the draft's third tier, so this phase
ships **no multi-hour, unattended-observation step of any kind** — not a soak slot of its own, and not a
co-tenant `Type: wait` step on EL.7 (#279). The long observation rides EL.7 / EI.14 **as originally
written**: opportunistically, naming no host command and no EH-specific observation, owed by no step here
and imposed on no sibling plan (P-7 forbids editing them, and EI.14 accepts no EH obligation). Read that
honestly rather than as coverage — the phase closes with **zero hours** of its own unattended evidence,
which means EH.2's per-generation persist hop is never watched across a six-hour run and EH.8's
`[evo-auto]` commit scoping is never observed against a real unattended auto-committing loop. Both ship on
test and short-run evidence alone. That is the accepted cost of the override, recorded here so a later
reader does not mistake silence for a passing observation.

## Decision Inventory

Operator P/D pass taken **2026-09-04** against the post-`/plan-review` plan. IDs are append-only and
stable; a later reversal is recorded as `changed <date>`, never by renumbering or deleting a row.

| ID | P/D | Decision | Binds | Status |
|---|---|---|---|---|
| P-1 | P | **Cut `pause_after_round`.** Do not wire the second control flag; `read_run_control` still parses the key but nothing acts on it. **Amendment:** EH.5 disables the checkbox and labels it not-implemented — cutting the work must not leave the UI lying | §3, EH.4, EH.5 | applied 2026-09-04 |
| P-2 | P | **Add EH.11 as a co-tenant `Type: wait` step on EL.7 (#279)**, not a soak slot of its own, with a binding ≥2-lineage host precondition and five named observations. "Rides EL.7" alone owed nothing and is retired | §3, §8, §9, EH.2, EH.10, ~~EH.11~~ (deleted — see Status) | applied 2026-09-04 — **changed 2026-09-04: OVERRIDDEN by operator final call (D2 = ship without).** Step EH.11 is deleted, the plan returns to EH.1–EH.10, and the phase ships **no** unattended-observation step; "rides EL.7 / EI.14" is reinstated exactly as originally written, owed by no step here. §3, §8 and §9 amended to say so plainly, EH.10 made terminal, and **EH.2 gained Done-when clause (8)** — the deleted step's done-when was the only reader of EH.2's per-boundary persist INFO line, so the clause re-gates a requirement the override did not authorise cutting. Applied in the wrap window 2026-09-05, before `/plan-wrap`. Row kept in place per the append-only convention above |
| P-3 | P | **Keep both EH.7 and EH.8** — the two data-safety steps added beyond the four briefed defects. They protect the ~28–30h of queued gate evidence this phase exists to make trustworthy | EH.7, EH.8, §6 D-9 | applied 2026-09-04 |
| P-4 | P | **Confirm EH.6**, conditional on P-3 keeping EH.8. **Coupling:** if EH.8 is ever cut, EH.6 must be cut or held with it — EH.6 makes runs long and real, EH.8 bounds what they commit | EH.6, EH.8 | applied 2026-09-04 |
| P-5 | P | **Launcher gets a `-Generations` parameter defaulting to 0**, not a hardcoded `--generations 0` — satisfies `evolve-viewer-plan.md`:657 (D16, bare launcher) and keeps a smoke-run cap available | EH.6 | applied 2026-09-04 |
| P-6 | P | **A resumed run clears a pending `stop_run` unconditionally** — an explicit `--resume` is fresher operator intent than the stale stop. **Amendment:** emit an INFO line naming the discarded stop, so it is visible rather than silently dropped | EH.4, §6 D-4 | applied 2026-09-04 |
| P-7 | P | **EH.4 does not edit `evolve-viewer-plan.md`.** Its five now-false "not wired" claims become a sibling issue, because that plan has two open operator gates (EV.4 #294, EV.5 #295). The issue also carries the fact that **EV.5 must run after EH.6** | §3, EH.4 | applied 2026-09-04 — filed as **#302** |
| P-8 | P | **EH.9 depends on EH.1, EH.2 only** — the EH.4 edge was false and would have cost real serialization at build time | EH.9 | applied 2026-09-04 |
| P-9 | P | **Phase letter `EH` confirmed**; the master-plan narrative section, Track-structure line, decision-graph node and plan-history bullet land at `/plan-wrap` so they describe approved scope rather than draft scope | header, `../master_plan.md` | index row applied; rest due at wrap |

**Surviving couplings:** P-3 → P-4 (cutting EH.8 pulls EH.6 with it) and P-1 → EH.5 (the cut only fully
lands if the frontend step stops the checkbox promising).

## Next steps

`/plan-redline`'s P/D pass is **done** — recorded in the Decision Inventory above; re-run it only if scope
changes again. `/plan-wrap` passed 2026-09-19. Next: `/repo-sync` to mint issues, which back-fills the 10 bare
`**Issue:** #` fields. Issue numbering starts from **#303** (#302 is P-7's sibling issue, filed 2026-09-04) — verified that none of these
defects has an existing issue (the nearest, #74, is the *training* daemon stop surface: different API,
different runner).

**Do not dispatch a build.** `build-step`, `build-phase`, `build-queue`, `review-deep` and
`--reviewers deep` are frozen until the skill-mesh review-deep restoration seals; the marker is
`../../../.claude/task-state/freeze.json`. This plan's build is Backlog item **B5**.
