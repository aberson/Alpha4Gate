"""Reproducible Jev benchmarks through the production runner (plan D1/D6, Step 212).

A thin command over :mod:`jev.benchmark`; see that module for the panels, frozen
source snapshots, limits, model pinning, storage, scoring and exit codes, and
``documentation/operator/jev-v2-validation.md`` for the operator procedure.

Usage, from the repository root (PowerShell)::

    uv run python scripts/benchmark_jev.py --panel baseline --dry-run
    uv run python scripts/benchmark_jev.py --panel staging --max-game-seconds 120
    uv run python scripts/benchmark_jev.py --resume <batch id>
    uv run python scripts/benchmark_jev.py --report <batch id>
    uv run python scripts/benchmark_jev.py --calibrate-run <absolute run dir> `
        --expect-race Terran --expect-difficulty 1 --expect-seed 1

``--dry-run`` resolves and prints the exact entrypoint, policy hash, source
fingerprint, model and options of every case; it makes no service call, launches
nothing, opens no dashboard and writes nothing.

A real ``--panel`` or ``--resume`` run is dashboard-first (plan D7, Step 224): it
starts or reuses the dashboard, opens ``http://localhost:3000/?tab=jev&launch=<id>``
once, and each game starts only after that page rendered its exact run; the same
tab follows every case. A dashboard that cannot be used stops the run
(``dashboard_unavailable``); it never falls back to headless. ``--no-dashboard``
is the explicit headless mode for automation.
"""

from __future__ import annotations

import sys
from pathlib import Path

# Ensure the repository's ``src`` is on sys.path so ``jev`` is importable (and is
# this checkout's) when the script is invoked directly. The ``jev`` import is
# deferred past this setup (E402 waiver).
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT / "src"))

from jev.benchmark import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
