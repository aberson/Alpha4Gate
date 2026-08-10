# Phase EV — Themed viewer for evolution runs

**Track:** Observable. **Status:** Planned (authored 2026-08-09).
**Prerequisites:** Phase 3 (subprocess self-play runner + `src/selfplay_viewer/`),
Phase 9 (evolve loop). No prerequisite on Phase L.

---

## 0. Prerequisites before `/build-phase` — P-1 and P-2 RESOLVED 2026-08-09

<!-- autofix-applied: 2026-08-09 -->

> **Status: cleared.** P-1 and P-2 were resolved before `/repo-sync` ran.
> Phase EV builds on branch **`master-plan/phase-ev`**, cut from `onbrand-pilot`
> at commit **`bb2e79c`** (`feat(launch): launch-evolve.ps1 + -Tab param for
> launch-a4g.ps1`). Both launcher files are now tracked at HEAD; the unrelated
> frontend work-in-progress (`frontend/src/App.tsx`, `frontend/src/App.test.tsx`)
> was deliberately left uncommitted and is NOT part of this phase.
>
> **Why this branch and not `master`:** `master` still carries
> `viewer = ["pygame>=2.5", ...]` (mainline pygame — no cp314 wheel, fails to
> build on the Py3.14 venv) and has **no `scripts/launch-a4g.ps1` at all**. Both
> arrived in `844ae57`, which lives on `onbrand-pilot` and is not yet merged. So
> P-3's verified claim and EV.3's launcher edits are only true on this lineage.
> **Phase EV cannot be merged to `master` until `onbrand-pilot` lands first.**
> P-3 and P-4 below still apply and are unchanged.

**P-1 — RESOLVED (`bb2e79c`).** The two files EV.3 edits were not in a state a
build worktree could see. Retained below because it explains what EV.3 depends
on and why the guard must survive:

| File | State | Consequence in a `--isolation worktree` build |
|---|---|---|
| `scripts/launch-evolve.ps1` | **untracked** (`git status` → `?? scripts/launch-evolve.ps1`; `git ls-files --error-unmatch` errors) | Absent from the worktree entirely. The developer agent would author a *new* file from scratch and silently lose the already-running-evolve guard EV.3 is required to preserve. |
| `scripts/launch-a4g.ps1` | tracked, but the `-Tab` parameter is **uncommitted** (`git show HEAD:scripts/launch-a4g.ps1 \| grep Tab` returns nothing; the working copy has it at L23/L76) | The worktree gets a `launch-a4g.ps1` with no `-Tab` parameter, so EV.3's "delegation at L57 is untouched" check cannot be evaluated and the `-Tab evolution` call would look broken. |

Commit both (path-scoped — the tree also holds unrelated in-flight frontend work
at `frontend/src/App.tsx` and `frontend/src/App.test.tsx` that must NOT ride
along) before dispatching EV.3:

```
git add scripts/launch-evolve.ps1 scripts/launch-a4g.ps1
git commit -m "feat(launch): launch-evolve.ps1 + -Tab param for launch-a4g.ps1"
```

Exactly the two files EV.3 depends on — `docs/seeds/run-improvement-button.md`
is also modified in the tree but EV.3 has no dependency on it, so it stays out
of a prerequisite commit whose whole purpose is to be minimal.

Alternatively, run EV.3 with `--isolation none`. Committing is preferred —
the worktree isolation is worth keeping for the other steps.

These prerequisites are prose, and `/build-phase` does not enforce prose (D14) —
which is why P-1 and P-2 were satisfied by hand before `/repo-sync` rather than
relied on. P-3 and P-4 remain operator obligations at EV.4/EV.5 time.

**P-2 — RESOLVED.** Phase EV builds on `master-plan/phase-ev`. Per
`feedback_git_branch_drift_alpha4gate`, still verify
`git branch --show-current` returns `master-plan/phase-ev` before every EV
commit — this repo's IDE has flipped the active branch mid-session before, and
every EV worktree is cut from whatever is checked out.

**P-3 — Viewer extra is already installable on the main venv.** Verified on this
machine: `uv run --extra viewer python -c "import pygame"` reports
`pygame-ce 2.5.7 (SDL 2.32.10, Python 3.14.3)` and
`importlib.util.find_spec("pygame")` returns non-`None`. This **supersedes**
`feedback_py312_venv_recipe_for_soaks` (which says the main `.venv` is Py3.14
with no pygame wheels and prescribes a side `.venv-py312`) — commit `844ae57`
swapped mainline `pygame` for `pygame-ce`, which ships cp314 wheels. EV.4/EV.5
need no side venv; `uv sync --extra viewer` on the main environment is
sufficient.

**P-4 — Serialize against the other pending evolve soaks.** EV.4 and EV.5 both
run `scripts/evolve.py` and write `data/evolve_*.json`. Four other soak steps
are already pending and use the same state files and the same `bots/current`
pointer: EJ.7 (#288), EJ.8 (#289), EL.7 (#279), and Phase 7 Step 6 (#280). Two
concurrent runs race on state, both flip `bots/current`, and both auto-commit
`[evo-auto]` to master. Only one evolve run at a time, machine-wide.

---

## 1. What This Feature Does

**Proposal:** <https://claude.ai/code/artifact/e037e409-119d-4585-8148-69ae1dcd794a>
(operator-facing view of this plan — decision IDs are defined in the Appendix and
are append-only; republishing keeps this URL).

`scripts/evolve.py` runs an unattended evolution loop that plays real SC2
self-play games (candidate-vs-parent fitness, regression gate, pool-generation
mirror calibration). Today those games render as raw, unmanaged SC2 windows that
pop up, steal focus, and disappear — the operator watching an evolve run sees OS
chrome, not a product.

`scripts/selfplay.py` already solves this for manual self-play: it hosts both SC2
clients inside a themed pygame container (`src/selfplay_viewer/`) with a
background, a stats bar, and live W-L overlay. Both tools drive the *same* game
engine (`orchestrator.selfplay.run_batch`), so the visual is reusable as-is.

Phase EV adds an opt-in `--viewer` flag to `scripts/evolve.py` that renders an
evolution run's games in that same themed container. Default stays headless, so
overnight soaks, `--concurrency > 1` fan-out, and the Linux/WSL evolve substrate
are byte-identical to today. The launcher `scripts/launch-evolve.ps1` (the
command behind the dev-observatory `run-evolution` button) then opts in, turning
"click the button" into a watchable, presentable run.

**Why now:** `scripts/launch-evolve.ps1` shipped in `844ae57` as the one-click
evolve entry point. It currently produces the raw-SC2-window experience. The
viewer is already built, already tested, and already wired to the identical
engine — this is a wiring feature, not a new capability.

### Operator acceptance condition (redline 2026-08-09, binds the whole phase)

> "As long as I am able to run a real evolve **visible match + UI** from the
> dev-observatory launcher — with no dev-observatory change."

This is the phase's definition of done, and every step serves it. Verified
against the registry at authoring time: the control plane's `run-evolution`
verb is

```toml
[project.launch.run-evolution]
verb = "run-evolution"
command = "scripts/launch-evolve.ps1"
confirm = true
```

(`dev/.claude/observatory/registry.toml:35-38`). Three consequences the build
must respect:

1. **The button already resolves to the script this phase edits.** EV.3's
   one-line change to `launch-evolve.ps1` is therefore sufficient on its own —
   nothing in the registry moves. The Phase EV out-of-scope boundary holds.
2. **The verb passes no arguments**, so `launch-evolve.ps1` uses its own
   `[double]$Hours = 4` default and, after EV.3, `--concurrency` is never
   supplied. D1's `--concurrency 1` restriction is therefore unreachable from
   the button — clicking it can never hit the mutual-exclusion error.
3. **"Visible match" is a real pass criterion, not "the window opened."** EV.4
   item (e) must observe an actual SC2 game rendered inside the container,
   launched from the button, alongside the dashboard on the Evolution tab.

---

## 2. Existing Context

A fresh-context model needs these five facts. Every line number below was read
directly from the file at authoring time (2026-08-09, `HEAD` = `844ae57`).

**(a) One engine, two callers.** `orchestrator.selfplay.run_batch`
([`src/orchestrator/selfplay.py:590`](../../src/orchestrator/selfplay.py#L590))
already accepts the three kwargs the viewer needs:

```python
def run_batch(
    p1: str, p2: str, games: int, map_name: str = "Simple64", *,
    game_time_limit: int = 300, hard_timeout: float = 600.0,
    seed: int | None = None, results_path: Path | None = None,
    on_game_start: OnGameStart | None = None,
    on_game_end: OnGameEnd | None = None,
    stop_event: threading.Event | None = None,
) -> list[SelfPlayRecord]:
```

Callback exceptions are caught and logged at WARNING
([`selfplay.py:545-551`](../../src/orchestrator/selfplay.py#L545-L551)), so a
viewer bug can never abort a batch. `stop_event` breaks the loop at the next
*inter-game* boundary ([`selfplay.py:671-679`](../../src/orchestrator/selfplay.py#L671-L679));
the in-flight game always finishes.

`SelfPlayRecord` — the payload every `on_game_end` receives, and the object
EV.2's tests must construct synthetically. Frozen dataclass, defined once in
[`src/orchestrator/contracts.py:121-137`](../../src/orchestrator/contracts.py#L121-L137);
one row of `data/selfplay_results.jsonl`:

| field | type | note |
|---|---|---|
| `match_id` | `str` | per-game identifier |
| `p1_version` | `str` | seat-1 version string (post-swap) |
| `p2_version` | `str` | seat-2 version string (post-swap) |
| `winner` | `str \| None` | winning version string; `None` for draw **or** crash |
| `map_name` | `str` | e.g. `Simple64` |
| `duration_s` | `float` | wall-clock game length |
| `seat_swap` | `bool` | `True` on odd-indexed games (spawn-side bias control) |
| `timestamp` | `str` | ISO timestamp |
| `error` | `str \| None` | defaults to `None`; non-`None` marks a crashed game |

The `winner`-is-`None`-for-both-draw-and-crash conflation is why
`run_fitness_eval`'s tally checks `record.winner == cand_name` rather than
counting non-losses — a viewer wrapper must not "helpfully" reinterpret it.

**(b) The viewer inverts control.** `SelfPlayViewer.run_with_batch`
([`container.py:499`](../../src/selfplay_viewer/container.py#L499)) must run on
the **main thread** (`_ensure_main_thread`,
[`container.py:68`](../../src/selfplay_viewer/container.py#L68) — Win32 HWND
manipulation is not thread-safe). pygame owns the main thread; the caller's
`batch_fn` runs on a daemon worker thread
([`container.py:574-578`](../../src/selfplay_viewer/container.py#L574-L578)).
`on_game_start`/`on_game_end` are thread-safe *enqueue-only* shims
([`container.py:431`](../../src/selfplay_viewer/container.py#L431),
[`:479`](../../src/selfplay_viewer/container.py#L479)) — they touch neither
pygame nor Win32, so they remain safe to call even after `pygame.quit()`.

**(c) `run_batch` is never called directly by `scripts/evolve.py`.** This
corrects a common assumption. There is **no** module-level
`from orchestrator.selfplay import run_batch` in `scripts/evolve.py`. Instead,
`run_loop` takes a dependency-injection seam
([`evolve.py:3550`](../../scripts/evolve.py#L3550)):

```python
run_batch_fn: Callable[..., list[SelfPlayRecord]] | None = None,
```

`None` means "callee defaults to `selfplay.run_batch`" (resolved in
`src/orchestrator/evolve.py` at
[`:563`](../../src/orchestrator/evolve.py#L563),
[`:788`](../../src/orchestrator/evolve.py#L788),
[`:972`](../../src/orchestrator/evolve.py#L972),
[`:1779`](../../src/orchestrator/evolve.py#L1779)). `run_loop` forwards it
verbatim to all five game-playing call sites: pool-gen mirror
([`3913`/`3918`](../../scripts/evolve.py#L3913)), fitness
([`4211`](../../scripts/evolve.py#L4211)), baseline gauntlet
([`4505`](../../scripts/evolve.py#L4505)), regression
([`4729`](../../scripts/evolve.py#L4729)), pool refresh
([`4867`](../../scripts/evolve.py#L4867) — `skip_mirror=True`, plays no games).
**This seam is the entire integration point.** `main()` currently calls
`run_loop(args)` with no injection
([`evolve.py:5151`](../../scripts/evolve.py#L5151)).

**(d) Callees build their OWN `on_game_end` and `stop_event`.** Both
`run_fitness_eval` ([`src/orchestrator/evolve.py:624-648`](../../src/orchestrator/evolve.py#L624-L648))
and `run_regression_eval` ([`:825-855`](../../src/orchestrator/evolve.py#L825-L855))
construct an early-stop `threading.Event` plus a tallying `_on_game_end`, and
pass both in `batch_kwargs`:

```python
        stop_event = threading.Event()
        def _on_game_end(record: SelfPlayRecord) -> None:
            ...
            if max(live) >= pass_threshold:
                stop_event.set()
        batch_kwargs: dict[str, Any] = {
            "game_time_limit": game_time_limit, "hard_timeout": hard_timeout,
            "on_game_end": _on_game_end, "stop_event": stop_event,
        }
```

That `stop_event` implements the `games // 2 + 1` early-stop that saves 2-3 games
per evaluation. A viewer wrapper that *overwrites* either kwarg silently destroys
the early-stop or the live tally. **The wrapper must chain, never replace.**
`run_baseline_gauntlet` ([`:1017`](../../src/orchestrator/evolve.py#L1017))
passes neither; `generate_pool`'s mirror path sets `on_game_end` conditionally
([`:1818`](../../src/orchestrator/evolve.py#L1818)). `on_game_start` is used
**nowhere** in the evolve path — it is a free slot.

**(e) Wiring precedent.** `scripts/selfplay.py::_run_pfsp_mode`
([`:223-297`](../../scripts/selfplay.py#L223-L297)) already reuses ONE viewer
instance across many one-game `run_batch` calls with idle gaps between them —
structurally identical to an evolve generation (the viewer idles during
`claude_prompt`, `stack_apply`, and `pool_refresh`). `_viewer_enabled`
([`:191`](../../scripts/selfplay.py#L191)) is the graceful-degradation template.
Note that selfplay's flag is `--no-viewer` (default **on**); evolve's must be
`--viewer` (default **off**).

---

## 3. Scope

### In scope

- A `--viewer` flag on `scripts/evolve.py`, default off.
- A degradation gate that turns `--viewer` into a WARNING + headless run on
  non-Windows or when the `[viewer]` extra is absent — never a crash.
- A mutual-exclusion check between `--viewer` and `--concurrency > 1`
  (rationale in §6, D-2).
- A `run_batch_fn` wrapper that injects `viewer.on_game_start` / `on_game_end`
  by **chaining** onto whatever the callee already passed, and never touches
  `stop_event`.
- `main()` restructured so the viewer owns the main thread and `run_loop` runs on
  the batch thread, with **detach-and-continue-headless** semantics on viewer
  close.
- `scripts/launch-evolve.ps1` opts in (`--extra viewer` + `--viewer`), keeping
  its existing already-running-evolve guard.
- Docs: `improve-bot-evolve` SKILL.md flag table, master-plan index entry.

### Explicitly out of scope

- **Any dev-observatory change.** The registry command already points at
  `scripts/launch-evolve.ps1`; only that script changes, and it lives in this
  repo.
- **Viewer appearance flags on evolve** (`--bar` / `--size` / `--layout` /
  `--background`). Evolve uses the horizontal defaults
  (`bar="top", size="large", layout="horizontal", background="random"`). Adding
  a parallel flag surface is scope creep; recorded as a follow-up in §8.
- **Showing parallel work.** At `--concurrency > 1` the fitness phase fans out to
  `scripts/evolve_worker.py` *subprocesses* — a 2-pane container cannot represent
  N concurrent pairs, and the callbacks never reach this process. `--viewer` is
  rejected at parse time rather than silently showing an empty window.
- **Fixing `scripts/selfplay.py::_viewer_enabled`'s missing pygame probe.** It
  has the same latent hole this plan closes for evolve (see §6, D-3), but it is a
  different default and a different CLI; recorded as a follow-up in §8.
- **Phase L (replay-stream-as-live single-pane refactor).** Independent; see §8
  for the collision note.
- Changing any evolve decision logic, fitness math, promotion behavior, or state
  file schema. Headless behavior must be byte-identical.

---

## 4. Impact Analysis

| File | Change Type | Reason | Verified |
|---|---|---|---|
| `scripts/evolve.py` | modify | `--viewer` flag; `_viewer_enabled()`; `_EvolveViewerSession` wrapper; `main()` viewer inversion | Read `build_parser` L145-472 (28 existing flags; `return parser` at L472 is the append point); `main()` L5141-5151 (currently `return run_loop(args)`); `run_loop` signature L3537-3554 with `run_batch_fn` at L3550. Grepped all 5 `run_batch_fn=` forwarding sites: L3913, L3918, L4211, L4505, L4729, L4867 — all inside `run_loop`, all forward verbatim. |
| `src/orchestrator/evolve.py` | **none (read-only)** | Confirms wrapper must chain, not replace | Grepped `batch_kwargs\|run_batch_fn\|on_game_end\|stop_event`: 4 signature sites (L537, L747, L948, L1734), 4 `None`→`selfplay.run_batch` defaults (L563, L788, L972, L1779), 2 caller-built `stop_event`s (L624, L825), 3 `on_game_end` injections (L646, L854, L1818). **No edit needed** — the seam already exists. |
| `src/orchestrator/selfplay.py` | **none (read-only)** | `run_batch` already accepts all three kwargs with exception isolation | Read L590-657 (signature + docstring) and L671-679 (`stop_event` inter-game check); L543-551 (`on_game_start` try/except → WARNING). |
| `src/selfplay_viewer/container.py` | **none (read-only)** | `run_with_batch` teardown contract drives design decision D-1 | Read L499-693. `finally` at L650: sets `stop_event` if given (L657-658), detaches panes, `pygame.quit()`, `reset_font_cache()`, then joins the batch thread for `1.0s` when `stop_event is None` (L679-682) and prints a stderr warning if still alive (L683-689). |
| `src/orchestrator/fingerprint.py` | **none** | Accepts `run_batch_fn` (L199, L229) but is not a game call site from evolve | Grepped `fingerprint` in `scripts/evolve.py`: only L4522-4546 importing `default_fingerprints_path` / `save_fingerprint`; the comment at L4517 confirms the fingerprint is built "from the gauntlet we just ran", not from a fresh batch. `compute_fingerprint` is never called by `run_loop`. |
| `scripts/launch-evolve.ps1` | modify | Spawn line opts into the extra + the flag | Read all 57 lines. Only L51 changes (`$evolveCmd`). The already-running guard (L40-49) and the `launch-a4g.ps1 -Tab evolution` delegation (L57) are untouched. |
| `tests/test_evolve_cli.py` | extend | Flag defaults, gating, wrapper chaining, `main()` integration | `test_default_flags` at L445-459 (`args = cli.build_parser().parse_args([])`); per-flag boolean template at L2927-2931 (`--panel-floor`); module loader at L41-57 caches into `sys.modules["evolve_cli"]`. |
| `tests/test_evolve_parallel.py` | extend | Sibling home for the wrapper-routing test | `test_make_parallel_run_batch_fn_diverts_mirror_passes_through_fitness` L2171 and `..._passthrough_when_concurrency_1` L2215 build the wrapper directly and assert routing — the exact shape the viewer wrapper test needs. |
| `.claude/skills/improve-bot-evolve/SKILL.md` | modify | Document `--viewer` in the flag table | Flag summary line L5; flag table L38-65 (`--concurrency` row at L38-39 must gain the mutual-exclusion note). |
| `documentation/master_plan.md` | modify | Add Phase EV to the plan index | `## Plan index` at L3; sibling sub-plans listed there (`evolve-judging-plan.md`, `evolution-lines-plan.md`). |
| `CLAUDE.md` (Alpha4Gate) | modify | Stale test count (`1799` → current) | Grepped `1799` in CLAUDE.md: three stale sites at L12 (Stack bullet), L20 (Commands block), L58 (Phase 7 note). Live count verified by `uv run pytest --collect-only -q` (see §9 baseline). Drive-by correction in EV.3. |

**No shape changes.** This feature adds no schema field, primary key, cache key,
filename format, or shared constant. The only signature touched is
`scripts/evolve.py::main()` (an entry point with one caller, `if __name__ ==
"__main__"` at L5154) and `build_parser()` (additive flag only). The
downstream-consumer grep required by
[`.claude/rules/code-quality.md`](../../.claude/rules/code-quality.md) was run
against `run_batch_fn` — the one value that crosses module boundaries — and is
recorded in rows 1, 2 and 5 above.

---

## 5. New Components

### `scripts/evolve.py::_viewer_enabled(args) -> bool`

Graceful-degradation gate, modeled on
[`scripts/selfplay.py:191`](../../scripts/selfplay.py#L191) but with one
addition that file lacks. Returns `False` (with a WARNING naming the reason)
when:

1. `--viewer` was not passed (silent — this is the default),
2. `sys.platform != "win32"`,
3. `importlib.util.find_spec("pygame") is None`.

Check 3 is **load-bearing and non-obvious**: `selfplay_viewer.container` imports
pygame lazily *inside methods* precisely so
`from selfplay_viewer import SelfPlayViewer` succeeds without the extra
([`container.py:9-14`](../../src/selfplay_viewer/container.py#L9-L14)). Probing
`selfplay_viewer` therefore succeeds on a machine with no pygame, and the real
`import pygame` at
[`container.py:558`](../../src/selfplay_viewer/container.py#L558) would raise
`ImportError` out of `run_with_batch` — killing the entire evolve run before a
single game. The probe must target `pygame`, not `selfplay_viewer`.

### `scripts/evolve.py::_EvolveViewerSession`

A small class owning the wrapper and the close latch. Two members:

- **`run_batch_fn(p1, p2, games, map_name="Simple64", **kwargs)`** — the callable
  injected into `run_loop(run_batch_fn=...)`. It:
  - returns `selfplay.run_batch(p1, p2, games, map_name, **kwargs)` **unchanged**
    when the latch is closed (post-viewer-close → byte-identical headless);
  - otherwise chains `on_game_start` and `on_game_end`: if the caller supplied
    one, the wrapper calls the caller's first, then the viewer's, each inside its
    own `try/except` so neither can break the other or the batch;
  - **never reads, writes, or forwards a `stop_event` of its own** — the caller's
    `stop_event` (the `games // 2 + 1` early-stop) passes through untouched.
- **`close()`** — sets the latch. Called once, on the main thread, immediately
  after `run_with_batch` returns.

The latch matters because `on_game_start`/`on_game_end` enqueue onto
`SelfPlayViewer._event_queue`, which nobody drains after the pygame loop exits.
Without the latch a multi-hour post-close run accumulates one `SelfPlayRecord`
per game in a queue that is never read.

### `scripts/evolve.py::main()` — viewer inversion

```python
def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(...)                      # unchanged
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.viewer and int(getattr(args, "concurrency", 1) or 1) > 1:
        parser.error("--viewer requires --concurrency 1 ...")

    if not _viewer_enabled(args):
        return run_loop(args)                     # today's path, unchanged

    try:
        from selfplay_viewer import SelfPlayViewer
        viewer = SelfPlayViewer()                 # horizontal defaults
    except Exception:
        _log.warning("viewer unavailable; continuing headless", exc_info=True)
        return run_loop(args)

    session = _EvolveViewerSession(viewer)
    done = threading.Event()
    rc_box: list[int] = []
    exc_box: list[BaseException] = []

    def _batch() -> None:
        try:
            rc_box.append(run_loop(args, run_batch_fn=session.run_batch_fn))
        except SystemExit as exc:                 # preserve main()'s int-return contract
            rc_box.append(exc.code if isinstance(exc.code, int) else 1)
        except Exception as exc:                  # noqa: BLE001 — re-raised on main thread
            exc_box.append(exc)
        finally:
            done.set()

    try:
        viewer.run_with_batch(_batch, stop_event=None)   # NOT the run's stop_event
    except Exception:
        _log.warning("viewer failed; evolve continues headless", exc_info=True)
    finally:
        session.close()

    if not done.is_set():
        # STDERR, not the logger: container.py:683-689 prints its misleading
        # orphan warning to stderr, so the correction must land on the same
        # stream or log routing/level filters can show the scare and hide the
        # fix. That is the never-kill-SC2 hazard in §8 row 2.
        print(
            "[evolve] viewer closed; the evolution run CONTINUES headless. "
            "Any preceding [selfplay_viewer] orphaned-SC2 warning does not apply.",
            file=sys.stderr,
            flush=True,
        )
    # Heartbeat rather than a bare done.wait(): a detached tail can run for
    # hours, and a silent console is indistinguishable from a hung one. The
    # 60s wake also keeps KeyboardInterrupt promptly deliverable on the main
    # thread.
    _started = time.monotonic()
    while not done.wait(60.0):
        _log.info(
            "evolve still running headless (viewer closed); %.0f min elapsed",
            (time.monotonic() - _started) / 60.0,
        )
    if exc_box:
        raise exc_box[0]
    return rc_box[0] if rc_box else 1
```

> **Correction (2026-08-10, found during the EV.2 build by `/review-deep`).** The
> block above has a defect: if `viewer.run_with_batch` raises **before** the
> container reaches `batch_thread.start()` ([`container.py:588`](../../src/selfplay_viewer/container.py#L588)),
> `_batch` never runs, so `done` is never set and `run_loop` is **never called** —
> yet control still falls into `while not done.wait(60.0)` and blocks the process
> forever, after printing "the evolution run CONTINUES headless" and then logging
> that same false claim every 60 s. The pre-start window is real and is not
> predictable from `_viewer_enabled`'s `find_spec` probe: `import pygame`
> (`container.py:558`), `_resolve_background_path` (`:560`), `pygame.init()`
> (`:580`), `pygame.display.set_mode()` (`:583`), `_load_background` (`:586`) all
> precede the thread start, so a headless/RDP desktop hits it. Four review lenses
> found it independently and three reproduced it by execution.
>
> **The shipped code deliberately deviates from this block:** `_batch` sets a
> `started` event as its first statement, and after the `try/except/finally`
> around `run_with_batch` an unset `started` means `return run_loop(args)` — the
> genuine fall-through to headless that D-5 promises. Do not "restore" the block
> above verbatim.

The `done` wait on the main thread is what makes detach-and-continue-headless
work: `run_loop`'s thread is a **daemon**
([`container.py:577`](../../src/selfplay_viewer/container.py#L577)), so it dies
the instant the main thread returns. Blocking on `done` keeps the interpreter
alive until the evolution run finishes on its own terms.

Three details in that block are deliberate and easy to get wrong:

- **`except SystemExit` is handled separately from `except Exception`.** A bare
  `except BaseException` would swallow `SystemExit` on the worker thread and
  re-raise it on the main thread after the wait, changing both the exit code and
  the traceback shape versus today's `return run_loop(args)`. Mapping its code
  into `rc_box` preserves `main()`'s `-> int` contract.
- **The counter-message goes to `sys.stderr`, not `_log`.** See the inline
  comment: same stream as the warning it corrects.
- **`time` and `sys` are already imported** in `scripts/evolve.py`
  (`run_loop`'s `time_fn` default is `time.monotonic`), so this adds no imports.

---

## 6. Design Decisions

### D-1 — Viewer close detaches; it does not stop the run

`run_with_batch`'s teardown calls `stop_event.set()` when a `stop_event` is
supplied ([`container.py:657-658`](../../src/selfplay_viewer/container.py#L657-L658)).
`scripts/selfplay.py` wants that: closing the window cancels the manual batch.
Evolve must not — an operator glancing at an overnight run and hitting Escape
should lose the *window*, not four hours of evolution.

**Decision:** pass `stop_event=None` to `run_with_batch`, and keep the main
thread alive on a separate `done` event until `run_loop` returns.

*Alternative considered — pass a dedicated event and ignore it.* Rejected: it
would still take the 30-second `BATCH_STOP_JOIN_TIMEOUT_SECONDS` join path
([`container.py:679-681`](../../src/selfplay_viewer/container.py#L679-L681)),
freezing the console for half a minute after close and then emitting the same
misleading warning anyway.

**Known cosmetic wart:** with `stop_event=None` the join budget is `1.0s`, and
because `run_loop` is (correctly) still running, `run_with_batch` prints
`batch thread did not exit within 1.0s of viewer close; orphaned SC2 processes
may remain` to stderr
([`container.py:683-689`](../../src/selfplay_viewer/container.py#L683-L689)).
For evolve that message is wrong. Mitigation is the explicit INFO line in
`main()` shown in §5 rather than editing shared viewer code that
`scripts/selfplay.py` depends on. Recorded in §8.

### D-2 — `--viewer` requires `--concurrency 1`, enforced at parse time

Three independently sufficient reasons, all verified against source:

1. **It would silently disable parallel mirror dispatch.**
   [`evolve.py:3910-3921`](../../scripts/evolve.py#L3910-L3921) reads
   `if run_batch_fn is not None: pool_kwargs["run_batch_fn"] = run_batch_fn`
   / `elif concurrency_int > 1: ... _make_parallel_run_batch_fn(...)`. Injecting
   a viewer wrapper takes the first branch, so the concurrency-`>1` mirror
   dispatcher never gets built. That is a real behavior regression, not a
   cosmetic one.
2. **The container would be empty during most of the run.** At
   `--concurrency > 1` the fitness phase — the bulk of wall-clock — dispatches to
   `scripts/evolve_worker.py` subprocesses
   ([`evolve.py:4312`](../../scripts/evolve.py#L4312)). Those are separate
   processes; the viewer's callbacks live in this one and never fire.
3. **Ctrl+C would orphan SC2 worker subprocesses.**
   `_run_fitness_phase_parallel` installs SIGINT/SIGTERM handlers
   ([`evolve.py:2534`, `:2543`](../../scripts/evolve.py#L2534)). Under the viewer,
   `run_loop` executes off the main thread, where `signal.signal()` raises
   `ValueError`. It does not crash — the code already catches it
   ([`:2536`, `:2545`](../../scripts/evolve.py#L2536)) — it degrades to a WARNING
   and *no handler*. Given the never-kill-SC2 rule
   ([`.claude/rules/bot-runtime.md`](../../.claude/rules/bot-runtime.md)),
   silently losing orphan cleanup is the worst of the three.

**Decision:** `parser.error()` on the combination. Fail loud in the first second
rather than degrade invisibly for four hours.

*Alternative considered — force `concurrency = 1` with a warning.* Rejected: it
silently changes a throughput parameter the operator explicitly set.
*Alternative considered — let mirror games run un-viewed.* Rejected: reason 1
makes it not merely un-viewed but behaviorally different.

The gate is free in practice: `scripts/launch-evolve.ps1` passes no
`--concurrency` (default 1), and `/improve-bot-evolve --concurrency 4` passes no
`--viewer`.

### D-3 — Degradation probes `pygame`, not `selfplay_viewer`

See §5. `selfplay_viewer` imports cleanly without the extra by design, so the
obvious probe is the wrong probe. `importlib.util.find_spec("pygame")` is the
correct one. `pygame-ce` (the actual dependency,
`pyproject.toml` `[project.optional-dependencies] viewer`) provides the `pygame`
module name, so one probe covers both editions.

### D-4 — Chain callbacks; never touch `stop_event`

Established by §2(d). The wrapper composes rather than replaces, and every
viewer-side invocation is individually `try/except`-wrapped. Combined with
`run_batch`'s own WARNING-level isolation
([`selfplay.py:545-551`](../../src/orchestrator/selfplay.py#L545-L551)) and
`_drain_event_queue`'s per-event isolation
([`container.py:721-726`](../../src/selfplay_viewer/container.py#L721-L726)),
this gives three independent layers between a viewer defect and the evolution
run. A viewer exception ends the viewer; it never ends the run.

### D-5 — Default off, and headless must be provably unchanged

`--viewer` is `store_true`, default `False`. When absent, `main()` reaches
`return run_loop(args)` on the same line shape it uses today — no wrapper
constructed, no import attempted, no thread created. EV.1's Done-when asserts
`run_batch_fn is None` reaches `run_loop` in the default case, mirroring the
identity-assertion pattern already used for `--screen-null-diff`
(`tests/test_evolve_worker.py:733`).

### D-6 — No new appearance flags

`SelfPlayViewer()` is constructed with its defaults
(`bar="top", size="large", layout="horizontal", background="random"` —
[`container.py:169-176`](../../src/selfplay_viewer/container.py#L169-L176)).
Labels come free: `run_batch` passes the version strings, so fitness games render
as `cand_<hash>` vs `v13`, and `_update_game_start_state`
([`container.py:747`](../../src/selfplay_viewer/container.py#L747)) resets the
W-L counters at each `game_index == 1` — i.e. per fitness evaluation, which is
the correct tally boundary.

### D-7 — Autonomous-behavior classification

This feature **does** extend always-on behavior: it adds a pygame loop and a
daemon thread that ride an entire multi-hour run. Per the plan-feature quality
bar that mandates a dedicated observation step, EV.5 (`Type: wait`) exists to
expose the time-dependent failures unit tests cannot see — queue growth after
close, pygame stability across dozens of attach/detach cycles, and whether
detach-and-continue-headless actually survives to a run's natural end.

---

## 7. Build Steps

<!-- autofix-applied: 2026-08-09 -->

**Quality gates — binding on every `Type: code` step below.** The gate that flips
a step DONE runs the FULL suite, per
[`CLAUDE.md` § Testing](../../CLAUDE.md), and names which suites ran:

- `uv run pytest` — full suite, all 1990 collected (1989 run; 1 deselected by
  `addopts = "-m 'not sc2'"`). Subsets are fine while iterating, never at the gate.
- `uv run ruff check .` — **applies to `scripts/`.** `[tool.ruff] line-length = 100`;
  `ruff check .` walks the whole tree regardless of the `src` setting.
- `uv run mypy src bots --strict` — **does NOT cover `scripts/`.** Verified:
  `[tool.mypy] packages = ["orchestrator", "bots.v0", "selfplay_viewer"]`
  (`pyproject.toml:97`). `scripts/evolve.py` is outside mypy's scope, so a type
  error there will not be caught by the typecheck gate. Do not spend a build
  iteration expecting it to be. Run mypy anyway to prove no regression in the
  packages that *are* covered — `selfplay_viewer` is one of them, so any change
  that reaches into the viewer's public surface is type-checked.

### Step EV.1: `--viewer` flag, degradation gate, and concurrency guard
- **Problem:** Add a `--viewer` store_true flag (default off) to `scripts/evolve.py::build_parser`, an `_viewer_enabled(args)` helper that returns False with a WARNING on non-Windows or when `importlib.util.find_spec("pygame") is None`, and a `parser.error()` in `main()` when `--viewer` is combined with `--concurrency > 1`. No viewer object is constructed in this step — `_viewer_enabled` returning True still falls through to the existing `return run_loop(args)`. This step's whole deliverable is "the flag exists, is documented in `--help`, and every path still runs headless and byte-identically." **That leaves `--viewer` inert on `master` until EV.2 merges**, so `_viewer_enabled` returning True in this step must emit `WARNING: --viewer accepted but not yet wired (lands in Phase EV.2); running headless`. An operator who tries the flag between the two merges gets told, rather than silently getting the old behaviour.
- **Type:** code
- **Issue:** #291
- **Flags:** --reviewers code --isolation worktree
- **Produces:** `--viewer` flag in `build_parser`; `_viewer_enabled()` in `scripts/evolve.py`; mutual-exclusion check in `main()`; tests in `tests/test_evolve_cli.py`
- **Done when:** `uv run pytest` full suite green at or above the 1990-collected baseline, with new tests asserting: (1) `cli.build_parser().parse_args([]).viewer is False` and `parse_args(["--viewer"]).viewer is True` (template: `tests/test_evolve_cli.py:2927`), plus a `viewer` line added to `test_default_flags` at `:445`; (2) `_viewer_enabled` returns False and logs a WARNING for each of non-win32 and absent-pygame, simulated with the `sys.meta_path` / `find_spec` stub pattern at `tests/test_selfplay_cli.py:202-209`; (3) `main(["--viewer", "--concurrency", "2"])` raises `SystemExit` with the guidance text; (4) an identity assertion that `main([])` reaches `run_loop` with `run_batch_fn` still `None` (template: `tests/test_evolve_worker.py:733`). The suite must collect cleanly **without** `--extra viewer` installed.
- **Depends on:** none
- **Status:** DONE (2026-08-10)

<!-- autofix-applied: 2026-08-09 -->
### Step EV.2: Viewer session wrapper + `main()` inversion
- **Problem:** Add `_EvolveViewerSession` (chaining `run_batch_fn` + `close()` latch, per §5) and restructure `main()` to run `viewer.run_with_batch(_batch, stop_event=None)` on the main thread with `run_loop(args, run_batch_fn=session.run_batch_fn)` on the batch thread, holding the main thread on a `done` event so viewer close detaches instead of cancelling. Chain — never replace — `on_game_start`/`on_game_end`; never pass or touch `stop_event`. Wrap viewer construction and `run_with_batch` so any viewer exception logs a WARNING and falls through to headless.
- **Type:** code
- **Issue:** #292
- **Flags:** --reviewers deep --isolation worktree
- **Produces:** `_EvolveViewerSession` + rewritten `main()` in `scripts/evolve.py`; wrapper tests in `tests/test_evolve_parallel.py`; `main()`-level integration test in `tests/test_evolve_cli.py`
- **Done when:** full `uv run pytest` green, with new tests asserting: (1) **chaining** — the wrapper is called with a caller-supplied `on_game_end` and a caller-supplied `stop_event`; assert the caller's `on_game_end` still fires, the viewer's also fires, and the `stop_event` object reaching `run_batch` `is` the caller's (identity, not equality); (2) **no stop_event injection** — with no caller `stop_event`, the wrapper forwards `stop_event` absent/`None`; (3) **exception isolation** — a viewer callback that raises does not prevent the caller's callback from running and does not propagate (template: `tests/test_selfplay_callbacks.py:176`); (4) **latch** — after `close()`, the kwargs reaching `run_batch` are identical to the un-wrapped call; (5) **integration through the production caller** (required by [`code-quality.md`](../../.claude/rules/code-quality.md)) — drive `main(["--viewer", ...])` with a fake `SelfPlayViewer` whose `run_with_batch` invokes `batch_fn()` inline and a fake `run_batch`, and assert the fake viewer's `on_game_start`/`on_game_end` were reached end-to-end; (6) **detach-and-continue** — a fake viewer whose `run_with_batch` returns *before* `batch_fn` completes; assert `main()` still blocks until the loop finishes and returns `run_loop`'s real return code. Viewer-object tests use `pytest.importorskip("selfplay_viewer")` + construct + drain `_event_queue` (template: `tests/test_selfplay_callbacks.py:467`) — never the real-window path in `tests/test_container_integration.py`.
- **Depends on:** EV.1
- **Status:** DONE (2026-08-10)

### Step EV.3: Launcher opt-in + documentation
- **Problem:** Change `scripts/launch-evolve.ps1` line 51 from `uv run python scripts/evolve.py --hours $Hours` to `uv run --extra viewer python scripts/evolve.py --hours $Hours --viewer`, preserving the already-running-evolve guard (L40-49) and the `launch-a4g.ps1 -Tab evolution` delegation (L57) exactly. Update the script's header comment to say the run renders in the themed container and that closing the container does not stop the run. Document `--viewer` in `.claude/skills/improve-bot-evolve/SKILL.md` (flag summary line 5 + flag table), including the `--concurrency 1` requirement as a note on the existing `--concurrency` row. Add a Phase EV entry to `documentation/master_plan.md`'s plan index. Correct the stale test count in `CLAUDE.md` (1799 → the post-EV.2 collected count).
- **Type:** code
- **Issue:** #293
- **Flags:** --reviewers code --isolation worktree
- **Produces:** modified `scripts/launch-evolve.ps1`, `.claude/skills/improve-bot-evolve/SKILL.md`, `documentation/master_plan.md`, `CLAUDE.md`
- **Done when:** `powershell -NoProfile -Command "& { . { $null = [ScriptBlock]::Create((Get-Content -Raw scripts/launch-evolve.ps1)) } }"` parses clean; the script is ASCII-only per [`.claude/rules/windows-shell.md`](../../.claude/rules/windows-shell.md) (no em-dashes — the file has no BOM); `grep -i "viewer" scripts/launch-evolve.ps1` shows both `--extra viewer` and `--viewer`; the already-running guard block is unchanged (verify with `git diff` showing only the `$evolveCmd` line plus header comments); SKILL.md flag table contains a `--viewer` row and the `--concurrency` row names the mutual exclusion; full `uv run pytest` still green.
- **Depends on:** EV.2
- **Status:** DONE (2026-08-10)

### Step EV.4: Real-SC2 smoke gate
- **Problem:** Prove the wiring works against real SC2 on Windows before any long run. On a Windows box with `uv sync --extra viewer` and SC2 installed, run a minimal evolve: `uv run --extra viewer python scripts/evolve.py --pool-size 2 --games-per-eval 3 --hours 0.5 --no-commit --viewer`. Confirm (a) the themed container opens with a background and stats bar; (b) during the fitness phase both SC2 clients reparent into the two panes and the overlay shows `cand_<hash>` vs the parent version; (c) the container idles gracefully (placeholders, no freeze) during the Claude pool-generation and stack-apply phases; (d) closing the container mid-run leaves the evolve process alive and the run continuing headlessly to its budget — verify via `data/evolve_results.jsonl` gaining rows *after* the window closed. Then run the same command **without** `--viewer` for 10 minutes and confirm behavior matches a pre-EV run (raw SC2 windows, identical row shapes in `evolve_results.jsonl`). Finally confirm `uv run python scripts/evolve.py --viewer --concurrency 2` exits immediately with the guidance message, and that on a machine without the extra (`uv run python ... --viewer`, no `--extra viewer`) the run starts headless with a WARNING rather than crashing. **(e) Exercise the operator acceptance condition end-to-end (§1).** This item is the phase's acceptance gate, not a spot-check. Launch from the dev-observatory `run-evolution` button itself if the control plane is up; otherwise run the identical command the registry holds, `.\scripts\launch-evolve.ps1` (bare — let it use its own `-Hours 4` default, exactly as the button invokes it). Confirm: (e1) a **real SC2 match renders inside the themed container** — not merely that the window opened; wait for it, because pool generation runs a Claude prompt before the first mirror-calibration game, so first paint can be several minutes out and a short `-Hours` override may end the run before a game ever starts; (e2) the dashboard opens on the Evolution tab and shows the run; (e3) the already-running-evolve guard fires on a second invocation instead of starting a competing run; (e4) closing the container leaves both the run and the dashboard alive. Stop the run once (e1)-(e4) are observed — the full four hours is EV.5's job, not this one.
- **Type:** operator
- **Issue:** #294
- **Produces:** smoke evidence appended to the run log under `documentation/soak-test-runs/`; pass/fail verdict per checklist item (a)-(e) plus the three negative cases
- **Done when:** all five positive observations confirmed, **with (e1) — a real match visibly rendered in the container from the launcher — as the hard gate**; the defaults-off run shows zero behavior change; both rejection/degradation cases behave as specified; and verdicts are recorded. A failure here blocks EV.5. If (e1) does not happen, the phase has not met its acceptance condition regardless of how many unit tests pass.
- **Depends on:** EV.3

### Step EV.5: Attended-then-detached observation run
- **Problem:** Time-dependent failures in a pygame loop + daemon thread riding a multi-hour run are invisible to EV.4's 30 minutes (D-7). Launch a standard 4-hour run via the production launcher (`.\scripts\launch-evolve.ps1 -Hours 4`), watch the first generation attended, then close the container and let the remainder run detached. Afterwards compare against the most recent pre-EV soak on: generations completed, promotions, games per generation, and crash rows in `data/evolve_crashes.jsonl`. Specifically look for viewer-attributable divergence — a lower generation count, HWND-attach stalls (the 15s `GAME_START_HWND_TIMEOUT_SECONDS` blocks the pygame thread per game, [`container.py:94`](../../src/selfplay_viewer/container.py#L94)), unbounded process memory growth, or any evidence the post-close latch failed.
- **Type:** wait
- **Issue:** #295
- **Produces:** observation report under `documentation/soak-test-runs/` with the survival verdict and, if a paired control was run, the throughput comparison
- **Done when:** the 4-hour run completes; the run continued past the container close; and the report records a **survival verdict** — no crash, no dead run, no unbounded memory growth, no evidence the post-close latch failed. **Throughput is NOT claimed from an unpaired comparison.** Comparing against "the most recent pre-EV soak" confounds viewer overhead with a different bot version, a different pool, and different machine load; per [`.claude/rules/measurement-validity.md`](../../.claude/rules/measurement-validity.md) (match measurement scope to decision scope), that comparison cannot answer "did `--viewer` cost throughput". To claim throughput, run the paired control: 2 hours `--viewer` then 2 hours headless resumed from the same state in the same session, and compare those. Absent the paired control, record generations/promotions as **context, explicitly not a verdict**.
- **Depends on:** EV.4

---

## 8. Risks and Open Questions

| Item | Risk | Mitigation |
|---|---|---|
| `run_loop` runs off the main thread | An unnoticed main-thread-only API inside 1600 lines of `run_loop` breaks under the viewer | Grepped `signal.signal` across `scripts/evolve.py` — only L2534/2543/3028/3035, all inside `_run_fitness_phase_parallel`, which D-2 excludes; `src/orchestrator/evolve.py` imports `signal` nowhere. EV.4 is the empirical check |
| Misleading `[selfplay_viewer]` orphan warning on close | Operator reads "orphaned SC2 processes may remain" and force-kills SC2, violating the never-kill rule | Explicit INFO line from `main()` immediately after (D-1). Follow-up: teach `run_with_batch` a `detach_only=True` mode so shared viewer code stops emitting it — deliberately deferred to avoid touching `scripts/selfplay.py`'s contract in this phase |
| `_event_queue` growth after viewer close | Multi-hour post-close run accumulates one record per game in an undrained queue | The `close()` latch (§5) makes post-close calls pass through unwrapped; EV.2 Done-when item (4) asserts it; EV.5 confirms empirically |
| Phase L refactors `src/selfplay_viewer/` to single-pane | Phase L's stated scope ("refactor `src/selfplay_viewer/` to single-pane", master plan L1207) would break evolve's 2-pane use | Advisory only — Phase L is Future/unscheduled. Recorded here so Phase L's plan inherits a second consumer. Evolve games are inherently 2-bot, so Phase L must keep a 2-pane mode or gate evolve's viewer behind it |
| `scripts/selfplay.py::_viewer_enabled` lacks the pygame probe | Same latent crash this plan fixes for evolve still exists for `scripts/selfplay.py` on a Windows box without the extra | Out of scope (different flag, different default). Recorded as a follow-up; the fix is the same three-line `find_spec` probe |
| Viewer appearance not configurable from evolve | Operators on 2560x1600 displays may want `layout="vertical"` | Deferred (§3). `SelfPlayViewer.__init__` already takes the parameters; adding `--viewer-layout` later is additive and needs no rework |
| Dashboard Stop button is not wired to the runner | EV.3 forbids Ctrl+C under `--viewer`, so the documented stop gestures must all actually work — but `bots/v0/api.py:698-742` writes `data/evolve_run_control.json` and **no reader exists**: `rg "evolve_run_control\|stop_run\|pause_after_round"` over `scripts/` and `src/orchestrator/` returns zero hits, and `scripts/evolve.py` only ever emits `stop_reason` of `wall-clock`/`pool-exhausted`/`generations-reached`. An operator told to "use the dashboard's stop control" on a 4-hour run that auto-commits to `master` would get nothing | EV.3 made the documentation true instead of implementing the poll (out of scope for a launcher-opt-in step): `launch-evolve.ps1`, `SKILL.md`'s Viewer note, and both pre-existing SKILL.md stop sections now state that closing the console window is the only working stop gesture. **Follow-up (not scheduled):** teach the runner to read the control file at the generation boundary in `scripts/evolve.py`'s loop, next to the wall-clock and `--generations` checks, and add a `dashboard-stop` `stop_reason` |
| `--viewer` + `--resume` untested together | A resumed run takes a different pool-gen path (L3816) | Both EV.4 and EV.5 exercise fresh runs only. `--resume` skips pool generation, which is a viewer-*idle* phase, so risk is low; note it as an untested combination rather than expanding scope |
| Windows-only feature, Linux CI | New code must import and test cleanly without pygame/pywin32 | Every new test collects without `--extra viewer`; viewer-object tests use `pytest.importorskip("selfplay_viewer")`; `_viewer_enabled` short-circuits on `sys.platform` before any viewer import |
| Launcher is a deployment seam that unit tests cannot reach | A malformed `uv run --extra viewer python ...` line in `launch-evolve.ps1` is invisible to `pytest` and would surface at the start of EV.5's four-hour window | EV.4 checklist item (e) runs the real launcher for 15 minutes before EV.5 commits to a long run. Satisfies the substrate-smoke requirement for deployment-seam changes |
| Uncommitted launcher work vs. worktree isolation | EV.3 edits two files a `--isolation worktree` build cannot see in their current state | §0 P-1 is a blocking prerequisite with a path-scoped commit command; the alternative (`--isolation none` for EV.3) is recorded there |
| Step headings use `EV.N`, not bare digits | A strict reading of plan-review §25(a)'s `^#{3,4} Step \d+:` regex does not match `### Step EV.1:` | Not a real defect: `/repo-sync` documents letter and sub-phase notation support (`SKILL-core.md` L114, L261) and Phase EJ shipped EJ.1–EJ.6 through this exact pipeline. Renumbering to bare digits would risk the cross-plan step-number collision that `feedback_repo_sync_step_collision` describes — keep `EV.N` |

**Open question (does not block the build):** should `--viewer` eventually
default *on* for interactive (TTY-attached) invocations while staying off for
`nohup`/CI? Deferred until EV.5 quantifies the overhead. Do not implement
TTY-sniffing in this phase.

---

## 9. Testing Strategy

**New unit tests** (all collect and pass without `--extra viewer` installed):

- `tests/test_evolve_cli.py` — flag default + flip (template `:2927`), a `viewer`
  assertion inside `test_default_flags` (`:445`), `_viewer_enabled` degradation
  on non-win32 and absent-pygame, the `--concurrency` mutual-exclusion
  `SystemExit`, and the `main()`-level integration test with a fake viewer.
- `tests/test_evolve_parallel.py` — wrapper behavior as a sibling of
  `test_make_parallel_run_batch_fn_*` (`:2171`, `:2215`): chaining, `stop_event`
  identity pass-through, exception isolation, post-`close()` transparency.

**Integration through the production caller** — mandated by
[`code-quality.md`](../../.claude/rules/code-quality.md) ("New components require
an integration test through the production caller"). EV.2's Done-when item (5)
drives real `main()` with a fake `SelfPlayViewer` and a fake `run_batch`,
asserting the viewer's callbacks are actually reached. A unit test of
`_EvolveViewerSession` alone would leave a silent-wiring failure invisible.

**Smoke gate before observation** — EV.4 is a real-components, no-mocks run that
must pass before EV.5's 4-hour observation. The producer/consumer coupling here
(evolve's `run_batch_fn` seam → `run_batch`'s callback contract → the viewer's
queue → the pygame drain) spans four modules and is exactly the shape that
mock-based unit tests cannot see.

**Existing tests that might break:**

- `tests/test_evolve_cli.py:430-442` asserts `--help` output contains/omits
  specific flag strings — adding `--viewer` may require updating that assertion.
- `tests/test_evolve_cli.py::test_default_flags` (`:445`) enumerates defaults and
  will need the new attribute.
- Any test that calls `cli.main(...)` directly: `main()` gains branches before
  `run_loop`, though the default path stays `return run_loop(args)`.
- `tests/test_evolve_cli.py`'s module loader caches into `sys.modules["evolve_cli"]`
  (`:41-57`), unlike `test_evolve_worker.py`'s fresh-module `worker` fixture
  (`:48-53`). Tests that monkeypatch module globals on `evolve_cli` must undo them
  or use `monkeypatch.setattr`, or state will leak between tests.

**End-to-end verification:** EV.4's checklist (a)-(e) plus the three negative
cases (concurrency rejection, missing-extra degradation, non-Windows
degradation). The load-bearing one is (d): rows appearing in
`data/evolve_results.jsonl` *after* the container was closed is the only direct
proof that detach-and-continue-headless works.

**Baseline:** `uv run pytest` collects **1990 / runs 1989** (1 deselected by
`addopts = "-m 'not sc2'"`) at `HEAD` = `844ae57`. Every step's gate runs the
full suite, not a subset.

---

## Appendix — Decision Inventory

Rendered for review as the proposal linked in §1. IDs are **append-only and
stable**: never renumber, never delete. A reversed decision keeps its ID and
gains `changed <date>` in Status.

Labels: **O** = open, blocks the build until you answer. **P** = you picked it
in the request. **D** = I defaulted it; every D has an axis you can move.
**R** = inherited from a standing workspace rule, not chosen for this plan
(listed so it is not mistaken for something you asked for).

| ID | P/D | Choice | Status |
|---|---|---|---|
| O1 | O | Commit the untracked `launch-evolve.ps1` + uncommitted `-Tab` param before EV.3 | RESOLVED 2026-08-09 — committed as `bb2e79c`; both files tracked at HEAD |
| O2 | O | Target branch for Phase EV | RESOLVED 2026-08-09 — `master-plan/phase-ev`, cut from `onbrand-pilot` (master lacks pygame-ce + launch-a4g.ps1). EV merges to master only after onbrand-pilot does. |
| P1 | P | Opt-in `--viewer` on `scripts/evolve.py` reusing the existing themed container | accepted |
| P2 | P | Default stays headless; soaks and the Linux/WSL substrate byte-identical | accepted |
| P3 | P | Closing the viewer detaches the window; the run continues | accepted |
| P4 | P | Non-Windows or missing `[viewer]` extra degrades to WARNING + headless, never a crash | accepted |
| P5 | P | Viewer is advisory: a viewer exception ends the viewer, not the run | accepted |
| P6 | P | No dev-observatory change of any kind | accepted |
| P7 | P | Launcher opts in via your exact spawn line, keeping the already-running guard | accepted |
| D1 | D | Of your two parallelism options, restrict `--viewer` to `--concurrency 1` rather than letting mirror games run un-viewed | accepted 2026-08-09, conditional — operator required that a real visible match + UI still run from the dev-observatory launcher with no observatory change. Verified reachable: the `run-evolution` verb passes no `--concurrency`, so the restriction is unreachable from the button. Gate is §1 acceptance condition + EV.4 item (e1). |
| D2 | D | Enforce D1 with `parser.error()` at parse time, not silent coercion to 1 | accepted 2026-08-09 |
| D3 | D | Integrate only through `run_loop`'s existing `run_batch_fn` seam; orchestrator, selfplay and container files stay read-only | accepted 2026-08-09 |
| D4 | D | Detach mechanism: `stop_event=None` + a main-thread `done` latch with a 60 s heartbeat; `SystemExit` mapped to the return code | accepted 2026-08-09 |
| D5 | D | Degradation probes `pygame` via `find_spec`, not `selfplay_viewer` (which imports fine without the extra) | accepted 2026-08-09 |
| D6 | D | `SelfPlayViewer()` at defaults; no evolve-side appearance flags and no evolve context (generation / phase) in the overlay | accepted 2026-08-09 |
| D7 | D | Accept the container's misleading orphan warning; counter it on **stderr** rather than patching shared viewer code | accepted 2026-08-09 |
| D8 | D | Five strictly linear steps: EV.1–EV.3 code, EV.4 operator, EV.5 wait | accepted 2026-08-09 |
| D9 | D | EV.2 reviewed at `--reviewers deep`; EV.1/EV.3 at `code`; all worktree-isolated | accepted 2026-08-09 |
| D10 | D | EV.3 flips the one-click launcher to viewer mode **before** EV.4/EV.5 produce evidence, with no rollback clause | accepted 2026-08-09 |
| D11 | D | EV.5 is ~20 min attended then ~3.5 h detached, so the attach/detach-cycle failure D-7 names is its least-covered mode | accepted 2026-08-09 |
| D12 | D | EV.5 claims **survival only**; throughput is not claimed without a paired 2 h/2 h control | accepted 2026-08-09 |
| D13 | D | The `--viewer`/`--concurrency` guard lives in `main()`, so `build_parser()` alone does not enforce it | accepted 2026-08-09 |
| D14 | D | §0's four prerequisites stay prose rather than a `Type: operator` Step EV.0, so `/build-phase` does not enforce them | accepted 2026-08-09 |
| D15 | D | EV.1 merges a flag that is inert until EV.2; mitigated by a "not yet wired" WARNING | accepted 2026-08-09 |
| D16 | D | EV.4's smoke uses `--no-commit` and 3 games/eval, so no promotion happens under the viewer and the `games//2+1` early-stop never fires | accepted 2026-08-09 |
| D17 | D | Non-Windows degradation is proved by unit test only; EV.4 has no WSL negative case | accepted 2026-08-09 |
| D18 | D | No test pins the `run_batch_fn`-vs-parallel-dispatch branch collision that D1 makes unreachable | accepted 2026-08-09 |
| D19 | D | New tests live in `test_evolve_cli.py` + `test_evolve_parallel.py`; the four files you named are used as pattern templates, not extended | accepted 2026-08-09 |
| R1 | R | Full-suite `uv run pytest` at the 1990 baseline is the gate that flips any code step DONE | rule-inherited |
| R2 | R | New components need an integration test through the production caller | rule-inherited |
| R3 | R | `launch-evolve.ps1` stays ASCII-only (file has no BOM) | rule-inherited |
| R4 | R | Verify `git branch --show-current` before every EV commit | rule-inherited |
| R5 | R | One evolve run machine-wide; serialize EV.4/EV.5 against the four pending soaks | rule-inherited |
| R6 | R | Main venv + `--extra viewer`; no side Py3.12 venv (verified `pygame-ce 2.5.7` on Py3.14) | rule-inherited |
