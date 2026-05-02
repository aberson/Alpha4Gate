# Models Tab

The Models tab is the dashboard's **Utility** half — everything an operator
needs to understand the trained-model substrate (lineage, runs, weights,
forensics, and head-to-head comparisons). Its sibling **Observable** tab is
the exhibition / replay-stream surface and is intentionally decoupled so
perception-affecting rendering never bleeds into rated play. See
`project_two_stack_split.md` for the architectural principle.

---

## What this tab is for — five questions answered

The Models tab consolidates five questions that used to be scattered across
half a dozen tabs and ad-hoc CLI scripts:

1. **Where did this version come from?** (Lineage)
2. **What's running right now against it?** (Live Runs)
3. **What does this version look like internally?** (Inspector)
4. **How do two versions differ?** (Compare)
5. **Why did this game go the way it did?** (Forensics)

Each sub-view answers one question. The shell at the top (version dropdown,
race filter, harness chips, manual refresh) is shared state that propagates
to every sub-view so an operator can pick a version once and see it
reflected wherever they navigate.

---

## Sub-view guide

### Lineage

A DAG of every promoted version, edges colored by the harness that
promoted them (advised / evolve / manual / self-play). Use this when you
want to see the genealogy at a glance — which generation a given version
came from, which improvements landed in which line, and where rollbacks
broke a chain. Subsumes the old standalone Improvements tab; clicking an
edge surfaces the improvement that drove that promotion.

### Live Runs

A grid of every active subprocess that's currently producing data — open
SC2 games, in-flight evolve generations, advised iterations, headless
Linux soaks. Use this when you've kicked off a long-running command and
want to see the progress without tailing logs. Pulls from
`/api/runs/active`.

### Version Inspector

A single-version drill-down: hyperparams, reward rules, training-history
rolling windows, recent actions, per-version improvements timeline, and
weight-dynamics charts. Use this when you want to know what makes vN
different from its parent — the "what changed" view. The "Compare with
parent" button hands the current selection off to the Compare sub-view
with both slots pre-filled.

### Compare

Side-by-side view of two versions: head-to-head Elo, win-rate delta,
config diff, weight-norm delta, and (when available) reward-rule diff.
Use this when you want to know whether vN is actually better than vN-1
beyond a single noisy training number. Pulls from `/api/ladder` for the
head-to-head surface.

### Forensics

Per-game replay panel: trajectory, give-up firing, expert-dispatch trace,
and — eventually — the Phase O scripted-Hydra controller's mode
transitions. Use this when a single game went sideways and you need to
know **which step** the bot's decision quality cratered. Pulls from
`/api/versions/{v}/forensics/{game_id}`.

---

## How lineage is computed

The `/api/lineage` endpoint stitches three sources into a single DAG:

- **`bots/vN/manifest.json`** — every promoted version writes a manifest
  with its parent name, harness origin, and timestamp. This gives the
  spine (nodes + parent edges).
- **`improvement_log.json`** — advised-loop commits append entries here.
  Provides the human-readable improvement title that decorates each
  advised edge.
- **`evolve_results.jsonl`** — evolve-loop generations append here. Each
  promoted generation contributes the improvement title that decorates
  each evolve edge.

Edges in the rendered tree are **promotions** — a node-to-parent edge
exists for every version whose manifest names a parent. Edge metadata
(harness, improvement title, timestamp, outcome) comes from the join.
Nodes that exist on disk but never reached promotion (failed evolve
generations, advised iterations that didn't pass the regression gate)
are NOT part of the DAG; they only show up in `/api/runs/active` while
they're alive.

The endpoint self-builds on first request and caches in
`data/lineage.json`. To force a rebuild, see Recovery procedures below.

---

## What "Weight Dynamics" measures

Two charts on the Inspector tell you whether a model's parameters are
moving in a healthy way:

- **Layer L2 norms** answer "is the model's parameter magnitude growing
  or shrinking?" Each tracked layer (policy head, value head, LSTM cells,
  imitation head) reports its L2 norm at every snapshot. A sudden jump
  often means an over-aggressive learning rate; a flatline often means
  the layer isn't training.

- **KL divergence** answers "do the policy outputs diverge over a fixed
  canary?" The canary is a stored set of game states; KL is computed
  between the current model's action distribution and the previous
  snapshot's distribution on those same states. Sustained large KL
  between adjacent snapshots means the policy is moving fast (good early,
  worrying late); near-zero KL means training has effectively stopped.

**Diagnostic-states fallback.** When the canary file is missing (e.g.
on a freshly snapshotted vN that hasn't built its diagnostic-states yet),
the KL panel falls back to a synthetic random-state batch and labels the
chart as "approximate". Treat the absolute KL number as approximate in
that mode but the trend across snapshots is still meaningful.

---

## First-run / refresh

If the **Weight Dynamics** charts say "Pending" or are empty, the
weight-dynamics file hasn't been built yet for that version. Run:

```powershell
uv run python scripts/compute_weight_dynamics.py --all
```

This walks every version with a `data/checkpoints/` directory and writes
per-version L2-norm and KL series to `data/weight_dynamics.jsonl`. The
Inspector picks up the new data on the next refresh.

If the **Lineage** tree is empty, click the manual **Refresh** button at
the top of the tab. The DAG endpoint self-builds on first request — an
empty render usually means the cache wasn't yet written, not that the
data is missing.

---

## Recovery procedures

### Rebuild `data/weight_dynamics.jsonl`

If the file is corrupted (truncated, mid-line JSON error, or out-of-date
after a manual checkpoint rewrite):

```powershell
Remove-Item data\weight_dynamics.jsonl -ErrorAction SilentlyContinue
uv run python scripts/compute_weight_dynamics.py --all
```

The script is idempotent — it writes one JSON object per (version,
snapshot) pair and skips already-written rows. Deleting the file forces
a full rebuild from scratch.

### Force-rebuild `data/lineage.json`

If lineage edges look stale (e.g. you just promoted a version but it
doesn't show up):

```powershell
Remove-Item data\lineage.json -ErrorAction SilentlyContinue
```

Then click **Refresh** at the top of the tab. The next request to
`/api/lineage` will rebuild from the three source files
(`bots/vN/manifest.json`, `improvement_log.json`, `evolve_results.jsonl`)
and write a fresh cache.

---

## Phase L / Phase O / Phase G placeholders

Several panels in the Models tab and the sibling Observable tab carry
"awaits Phase X" placeholders. They mark surfaces that are wired up in
the shell but waiting for the corresponding plan phase to land:

- **Phase L (Observable tab — Exhibition mode).** Currently a placeholder
  card that says "Exhibition mode awaits Phase L (replay-stream-as-live)".
  Phase L will turn the two version dropdowns into a live replay-stream
  comparison renderer that shows the same game from both versions'
  vantage points without affecting rated play. See the master plan's
  Phase L section.

- **Phase O (Inspector → Forensics expert-dispatch).** The Forensics
  sub-view always renders a `forensics-expert-dispatch` panel. Today it
  reports "no expert dispatch trace" for every game; once Phase O ships
  the scripted-Hydra controller, this panel will show the mode
  transitions (Macro / Harass / Defend / Tech) with timestamps and the
  trigger that fired each switch. See `project_hydra_scripted_first.md`.

- **Phase G (race filter).** The race-filter dropdown auto-hides today
  because every promoted version coerces to `protoss`. Phase G will
  introduce multi-race versions (e.g. `v_zerg_0`); when the registry
  contains more than one race the dropdown unhides itself and filters
  every sub-view's queries accordingly. No code change needed when Phase
  G data lands — the filter is data-driven.

---

## Operator commands cheatsheet

The Models tab visualizes data produced by these commands. Run them from
the project root.

| Command | What it produces |
|---|---|
| `uv run python -m claude_code_sdk improve-bot-advised` (or `/improve-bot-advised` from the harness) | New advised-iteration entries in `improvement_log.json`; promoted versions write `bots/vN/manifest.json`. |
| `/improve-bot-evolve` | New rows in `evolve_results.jsonl`; promoted generations write `bots/vN/manifest.json`. |
| `uv run python scripts/snapshot_bot.py --from <version>` | Hand-promote: copies `bots/<version>/` to a fresh `bots/vN+1/` and writes its manifest. Use to fold dormant work back into `bots/current`. |
| `uv run python scripts/compute_weight_dynamics.py --all` | Writes / refreshes `data/weight_dynamics.jsonl` (Inspector "Weight Dynamics" charts). |

For a full operator cheat sheet (launching evolve, watching tasks,
debugging recipes), see [operator-commands.md](operator-commands.md).
