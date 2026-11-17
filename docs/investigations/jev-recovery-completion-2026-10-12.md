# Recovery completion and Observer Rush follow mode

## Implemented

Observer Rush now calls its commentary panel **Archon Auto Caster**. Its new
**Follow decisions** control selects an endpoint of newly lit recorded paths,
choosing randomly among simultaneous endpoints. Unchanged polls do not reshuffle;
manual node selection pauses following. Stale/ended runs stop advancing. Caster
commentary highlights, graph rotation, and inspector following remain independent.

Hosted Jev v2 recovery now has observable completion. With at least the existing
policy's launch/regroup minimum (four units), at least 80% of the ready combat army
inside the existing home defense radius (20), no visible base threat, and no new
losses or damage for five game seconds, the recovery override completes. The
five-second and 80% values are initial development hypotheses, not tuned optima.
Visible dangerous air near either base also prevents completion.

Repeated recover answers share readiness progress. Once completed, recover is
removed from executable strategic choices until departure, loss/damage, a base
threat, or an undersized army creates another recovery need. In-flight replies
are revalidated against those options. Completing recovery releases the override;
it never directly orders an attack. Local defense, the few-units recovery guard,
and tactical army intent remain responsible for actions. No v1 policy changed.

The hosted strategy context, decision trace and Observer Rush current-decisions
panel report recovery status, elapsed/settled game seconds and readiness counts.

## Preserved offline before/after

Same 140-game-second fixture: 16 ready Zealots home, no visible enemies, repeated
strategy recover and tactical attack replies, zero network requests.

| Source | Continuous recovery span | Final tactical intent | Final strategic override |
|---|---:|---|---|
| Before | 137.5 game seconds | attack | recover |
| After | 5.0 game seconds | attack | none; recovery completed |

Before fingerprint: `ebdb5891f3f5e631d58fe53aefb40ace03aac6ce37fda7bbd9402322a26ef356`.
After fingerprint: `ebb70227a4eff45b7100a2fe942a64ab76ac5e185d983c165643f7a258dfde8c`.
Both source captures were verified after the offline probe. The identical fixture
hash is recorded with numeric results in
[jev-recovery-comparison-2026-10-12.json](jev-recovery-comparison-2026-10-12.json).

Reproduce separately for each source:

```powershell
uv run python scripts/probes/jev_recovery_comparison.py --source <snapshot-directory>
```

This deliberately isolates arbitration; it does not simulate movement, prove the
army would win, or show that all inactivity has been removed.

## Validation

335 Python tests passed across recovery, hosted decisions, plans, graph runtime,
and caster; 38 frontend tests passed across Observer Rush and the graph. Frontend
build and focused ESLint passed; Ruff and strict mypy passed for changed runtime
code. The frontend build retains the existing large-bundle warning.

## Live evidence

One bounded hosted 4x-cap Observer Rush case completed naturally with a win:
`b451217895fa499d899ebae4783c4751`, Medium Terran, Simple64, seed11,
900 game seconds / 1200 gameplay wall seconds / 450 requests maximum. Camera and
caster on, recording off. Candidate snapshot above; no source edits during play. Post-run hashes verified.

| Metric | Candidate live result |
|---|---:|
| Game duration | 500.714 seconds |
| Gameplay wall time | 164.027 seconds |
| Achieved speed | 3.0526x |
| Hosted requests / returned answers | 61 / 60 (one pending at end) |
| Accepted / stale / low-confidence answers | 42 / 16 / 2 |
| Service failures | 0 |
| Median response | 573.8 ms |
| Hosted army-source time after first attack | 35.64% |
| First attack | 150 game seconds |
| Army attack / defense branch time after first attack | 306.071 / 23.929 seconds |

The live run did not select hosted recovery, so it validates integration rather
than live recovery completion. Source-time fractions are coordinator attribution,
not proof of control of each unit command. Full summary:
[jev-recovery-live-2026-10-12.json](jev-recovery-live-2026-10-12.json).

Live screenshots verified native SC2 fit, the renamed panel and Follow decisions
control. The inspector visibly advanced from army.defend.mode to
army.defend.engage between captures. Local screenshots remain under
`.build-step/jev-speed/recovery-live*.png`; no recording requested.

The earlier baseline 4x game won in 715.357 game / 264.304 wall seconds (2.7066x),
with 80/104 answers accepted and no hosted recovery selected. Thus that single
baseline match cannot establish a live recovery improvement. Use the offline
mechanism proof and additional matched seeds before claiming stronger play.


## Next isolated gameplay experiment

The useful v13 reference is `bots/v13/army_coherence.py`: centroid-based grouping,
a staging location before engagement, and different retreat/re-engagement
thresholds to prevent oscillation. Adapt the grouping/readiness observations into
Jev's own graph/model context first; keep Jev as the strategy chooser. Do not copy
v13's automatic attack when no enemy is visible, its randomized thresholds, or its
whole strategic controller. Preserve this candidate before the next behavior
change and measure repeated matched seeds. Choice flattening remains deferred.
