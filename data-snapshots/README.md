# data-snapshots/

Operator-manual and skill-auto snapshots of `/data/`. **Gitignored.**
Established 2026-04-19 to keep the project root clean.

## Naming conventions

| Pattern | Created by | Meaning |
|---------|-----------|---------|
| `data-pre-soak-YYYY-MM-DD/` | Operator (manual `cp -r`) | `/data/` snapshot taken before a soak run |
| `data-post-soak-YYYY-MM-DD/` | Operator | `/data/` snapshot taken after a soak run |
| `data-pre-improve-YYYY-MM-DD/` | Operator | `/data/` snapshot before an `/improve-bot` run |
| `data.bak-<unix-ts>/` | `improve-bot` skill (training/dev/hybrid + `--fresh`) | `mv data data-snapshots/data.bak-$TS && mkdir data` |
| `data.demo-snapshot-<unix-ts>/` | `improve-bot` skill (demo flavor) | `cp -r data data-snapshots/data.demo-snapshot-$TS` — diff contract |

The skill convention lives at
`.claude/skills/improve-bot/SKILL.md` (search "data-snapshots").

## Lifecycle

Snapshots are operational artifacts, not source. They:

- Are **safe to delete** once the corresponding run record (in
  `documentation/soak-test-runs/`) is closed and you don't expect to
  roll back.
- Are NOT versioned. The whole `/data-snapshots/` dir is in
  `.gitignore`.
- Pair 1:1 with run records — find the related run via the date in the
  snapshot name, then look in `documentation/soak-test-runs/` for the
  matching `soak-YYYY-MM-DD.md` or `improve-YYYY-MM-DD.md`.

## What lives in here today

After the 2026-04-19 cleanup pass: snapshots from 2026-04-14 onward
were preserved (they pair with active soak/improve runs). Snapshots
from 2026-04-10 through 2026-04-13 were deleted as no longer needed.

Historical run records in `documentation/soak-test-runs/` may reference
deleted snapshot dirs by name — those references are correct as
historical artifacts even though the snapshot itself is gone.
