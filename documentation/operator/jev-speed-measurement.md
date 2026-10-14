# Jev speed measurement

Measure hosted gameplay before changing recovery, combat or economy behavior.
These are development experiments, separate from the frozen-v1 and held-out J2
panels. A time-limited or interrupted run is never counted as a win.

## Preserve first

`scripts/measure_jev_speed.py` captures the v2 runtime, policy and dependency
configuration with the benchmark module's content-addressed snapshot mechanism.
It does not finalize a v2 candidate or replace the existing v1 baseline.
`--snapshot` reuses an existing capture after checking its hashes. Each case has
a unique directory and refuses to overwrite previous evidence.

The original pre-pacing capture from 2026-10-11 is
`v2-4b7bfd1cc77f349e`, fingerprint
`4b7bfd1cc77f349e45b253bd57253d2d737eb4c052239214b43d6604f537ca15`.
Local experiment sources and manifests live under
`data/jev/experiments/speed-2026-10-11/`; the run archives remain in
`data/jev/runs/<run_id>/` so the dashboard can display them.

## Run one case

Use the current preview checkout with SC2, Simple64, the dashboard dependencies
and `TYPESAFE_API_KEY` configured in the launching terminal. Close any previous
game normally before starting another. This command makes paid Typesafe calls.

```powershell
uv run python scripts/measure_jev_speed.py --experiment-root data/jev/experiments/my-speed-study --label native-1x --speed native
uv run python scripts/measure_jev_speed.py --experiment-root data/jev/experiments/my-speed-study --label paced-2x --speed 2
uv run python scripts/measure_jev_speed.py --experiment-root data/jev/experiments/my-speed-study --label paced-4x --speed 4
```

Run these sequentially. For strict comparisons, pass the same verified pacing-capable
`--snapshot <directory>` to every case. `--speed 1` supplies a paced 1x control:
native real-time scheduling and paced non-real-time scheduling are different modes.
A pre-pacing source snapshot only supports `--speed native`.

Each case opens **Observer Rush**, the combined native SC2/graph/caster viewer,
before SC2 and waits for the exact run to render there. Defaults: Medium Terran,
Simple64, seed 11, `jev-1.13.0`, at most
450 requests, 900 game seconds and 1200 wall seconds. `--seed` and `--difficulty`
allow further matched development cases. The wall limit counts gameplay, not SC2
startup or the viewer readiness barrier. Use **Stop battle** in Observer Rush
for a normal early shutdown; do not force-kill SC2.

## What speed means

The runner's `--game-speed 1|2|4|8|16` caps **game seconds per wall second** in
non-real-time mode. It does not change SC2's simulation step, policy tick interval,
question cadence, reply confidence threshold, freshness checks or request budget.
`--realtime` and `--game-speed` are mutually exclusive. Omitting both preserves
the existing unrestricted non-real-time mode. The measurement wrapper deliberately
offers only native, 1x, 2x and 4x for this first experiment.

Pacing yields to asynchronous model requests and checks stop/wall-limit conditions
at most every 50 milliseconds of requested sleep. Slow frames do not accumulate
credit for a catch-up burst. Actual throughput can be below the requested cap;
read `metrics.timing.game_seconds_per_wall_second` rather than assuming the label
was achieved. That measurement excludes startup, dashboard wait and replay saving.
The old pre-pacing baseline has no timing accumulator; do not invent an exact
measured throughput for that archive.

The standard desktop Observer Rush launcher remains real-time by default;
`-GameSpeed 2` or `-GameSpeed 4` selects paced acceleration. The experiment wrapper
uses Observer Rush automatically, including its camera and caster workload.
Keep viewer and recording settings consistent across comparisons. Earlier
separate-window runs are labeled as preliminary, not matched viewer baselines.

## Read the evidence

```powershell
uv run python scripts/report_jev_speed.py data/jev/experiments/my-speed-study
```

The read-only report includes the fraction of post-first-attack game time for
which the army coordinator records a Typesafe choice in force. It integrates
source transitions, not request counts. Incomplete traces are labeled unavailable;
sequence gaps from normal trace suppression are allowed. This is an attribution
measure, not proof that the model caused each unit action or a win. Strategic
posture, emergency defense and persistent plans can still override army intent.

Each case saves `case.json`, the host process log, source fingerprint, run ID,
viewer source hashes, full options, outcome and metrics. A post-run hash check verifies
that the source snapshot stayed unchanged. Each run retains its policy, decision
trace, diagnostics and replay. Raw data is local/gitignored; checked-in reports
must contain the identifiers, settings and numeric summaries needed to interpret it.

Compare model response latency, accepted/stale/low-confidence answers, service
failures and request count alongside actual throughput. Use time with a hosted
choice in force when available; a request count alone does not establish model
influence. Also compare first attack, supply blocks, idle-Gateway seconds, resource
bank, losses and outcome. Gateway idle time sums across eligible buildings, so it
can exceed elapsed match time. State sampling gaps are reported explicitly.

Start with one case per speed to find timing failures, not to declare a win-rate
improvement. Different match lengths and model nondeterminism complicate comparisons
even at the same seed. A successful faster run is provisional until repeated with
matched settings and additional development seeds. Keep the existing held-out
panels untouched. The existing formal benchmark scorer does not accept the extra
speed option as one of its fixed-panel settings; use the development case evidence.

## Improvement order

1. Preserve the current player and gather baseline data.
2. Establish usable hosted acceleration with unchanged gameplay rules.
3. Add recovery progress/completion and reconcile recovery with attack intent.
4. Adapt selected v13 grouping/re-engagement, then combat/economy behavior one
   change at a time; avoid copying its whole strategic controller or thresholds.
5. Compare against the preserved baseline at the same measured speed.

Choice flattening remains a separate later experiment with its own before/after
source, policy and trace evidence.
