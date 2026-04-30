# Cloud deployment — building and running the Alpha4Gate worker

Phase 8 Step 9 ships a multi-stage `Dockerfile` at the repo root that
packages SC2 4.10 (Linux headless) + Python 3.12 + the Alpha4Gate code
into a single image. This page is the operator runbook for building
that image, running it locally, and (optionally) running it on AWS spot
instances for parallel evolve soaks.

## License caveat

The Dockerfile downloads the Blizzard SC2 4.10 headless package at build
time per Blizzard's AI/ML license. The resulting image is **not
redistributable**:

- Build locally on each host that needs it.
- **Do NOT push to public registries.** Private registries (ECR with
  IAM-locked access, GHCR private repos) are acceptable for internal
  use, but inspect access policies before pushing.
- The license also requires that the image be used only for AI/ML
  research, not commercial gameplay.

## 1. Build

### Prerequisites

- **Docker** ≥ 20.10 with BuildKit enabled (BuildKit is default since
  Docker Desktop 4.0 / Engine 23). On Windows: Docker Desktop with the
  WSL2 backend.
- **~20 GB free disk space** on the Docker storage volume — SC2 zip is
  ~3.83 GB compressed, ~8.6 GB extracted; the final image weighs ~12 GB
  including the Python venv and dependencies.
- **Internet access** to:
  - `https://blzdistsc2-a.akamaihd.net/` (SC2 zip)
  - `docker.io` and `ghcr.io` (base + uv images)

### Standard build

```powershell
# From the repo root
docker build -t alpha4gate-worker .
```

First build takes **10–20 minutes**, dominated by the SC2 zip download
(CDN throughput is the bottleneck). Subsequent builds reuse cache:

- Code-only changes invalidate only the final `COPY` layer (~10 s).
- `pyproject.toml` / `uv.lock` changes invalidate the `uv sync` layer
  (~2–3 min).
- Python or uv version changes invalidate the `python-base` layer.
- SC2 version changes invalidate everything.

### Offline / pre-cached SC2 zip

If the Blizzard CDN ever takes the 4.10 zip down (low-but-nonzero risk
per the Phase 8 investigation §9.2) or you want to avoid re-downloading
on every fresh host, archive the zip and pass it via a build-arg:

```powershell
# One-time: archive the zip to a host-local path
docker build --build-arg SC2_PACKAGE_URL=file:///path/to/SC2.4.10.zip `
             -t alpha4gate-worker .
```

The `file://` URL is interpreted by `curl` inside the build, which
supports the `file` scheme natively.

### Verifying the build

```powershell
# Image size (~12 GB expected)
docker images alpha4gate-worker

# Inspect the layer breakdown
docker history alpha4gate-worker
```

## 2. Run locally

### Smoke test (default CMD)

```powershell
docker run --rm alpha4gate-worker
```

Plays one solo game vs VeryEasy on Simple64 in `--decision-mode rules`
mode. Expected outcome: `Result.Victory` within ~70 seconds wall-clock.

### Selfplay

```powershell
# 2 games of v0 vs v0 on Simple64
docker run --rm alpha4gate-worker `
    scripts/selfplay.py --p1 v0 --p2 v0 --games 2 --map Simple64
```

### Evolve soak

```powershell
# 4-hour evolve cycle, results captured to a host-mounted volume
docker run --rm `
    -v ${PWD}/data-snapshots:/app/data-snapshots `
    -v ${PWD}/logs:/app/logs `
    -e EVO_AUTO=1 `
    alpha4gate-worker `
    scripts/evolve.py --hours 4 --games-per-eval 5 --pool-size 4
```

Volume mounts:
- `data-snapshots/` — per-version DB snapshots and pool state.
- `logs/` — JSONL game logs and run-state files.

The `EVO_AUTO=1` env var unlocks the auto-commit path (`git_commit_evo_auto`
in `scripts/evolve.py`). Inside a container, this only works if the
`/app` directory is itself a writable git checkout — for soak runs you
typically don't want auto-commits inside the container; use a host-side
runner instead and treat the container as an isolated playing environment.

### Overriding ENTRYPOINT

The `ENTRYPOINT` is `["uv", "run", "python"]`. To run anything else (a
shell, a different binary, etc.):

```powershell
docker run --rm -it --entrypoint /bin/bash alpha4gate-worker
```

This drops you in `/app` as the non-root `alpha4gate` user with the
venv on PATH (via `uv run` indirection — call `uv run pytest`, etc.).

## 3. Run on AWS spot

The Phase 8 plan includes a conditional Step 12 (cloud cost dry-run)
that's only triggered if the Linux unlock measured in Step 11 is
≥ 2× faster than the Windows baseline. The runbook below is the
recommended approach for that future spike — Phase 8 itself ships
without any AWS code, just the Dockerfile + this documentation.

### Recommended instance type

- **`c5.2xlarge`** (8 vCPU, 16 GB RAM, ~$0.10/hr spot) — fits 4 parallel
  evolve workers comfortably given the Linux memory unlock (~600 MB per
  SC2 process vs ~1.5 GB on Windows).

### Pre-flight on the issuing host

```bash
# Configure AWS CLI on the host that requests the instance.
# Credentials live in ~/.aws/credentials — never bake into the image
# or pass via environment variables.
aws configure
```

### Provisioning

For Phase 8 / Step 12 the recommendation is to provision spot via the
AWS Console (no Terraform/CDK scaffolding) — one-off runs don't
justify infrastructure-as-code overhead. Steps:

1. Launch a spot request for `c5.2xlarge` with Ubuntu 22.04 AMI (no GPU
   needed; SC2 4.10 headless runs CPU-only).
2. SSH to the instance, install Docker:
   ```bash
   curl -fsSL https://get.docker.com | sh
   sudo usermod -aG docker $USER
   ```
3. Copy the Alpha4Gate repo to the instance (rsync or `git clone` if
   the repo is in a private GitHub).
4. Build the worker image:
   ```bash
   docker build -t alpha4gate-worker .
   ```
5. Run the soak with output volume mounts pointing at instance-local
   storage that gets rsync'd back before termination.

### Cost expectation

- ~$0.10/hr × 24 hr ≈ **$2.40 per 24-hour soak** at spot prices.
- Add ~$0.10–0.50 for storage + data transfer.

### Retrieving results before termination

Spot instances can be reclaimed with 2 minutes notice. Two options:

1. **Continuous rsync to S3** — cron job pushes `data-snapshots/` and
   `logs/` to S3 every N minutes. Most resilient.
2. **Final rsync on shutdown** — systemd unit triggered by the spot
   reclaim notice. Brittle but simpler.

The detailed AWS runbook is deferred to a future plan triggered if
Phase 8 Step 11 confirms the throughput unlock justifies cloud overhead.

## 4. Troubleshooting

### `Cannot connect to the Docker daemon`

Docker Desktop isn't running. Start it from the Windows tray, wait for
the whale icon to stop animating, retry.

### Build fails at SC2 zip download

- Check internet connectivity to `blzdistsc2-a.akamaihd.net`.
- The Blizzard CDN occasionally serves 503s under load — retry.
- If the CDN has the zip permanently down, archive a previous build's
  zip via `--build-arg SC2_PACKAGE_URL=file:///path/to/local.zip`.

### Build fails at `uv sync --frozen`

`uv.lock` doesn't match `pyproject.toml`. On the host:

```powershell
uv lock
git diff uv.lock
```

If the diff is intentional, commit it; rebuild.

### Container exits with `SC2 install not found`

The `SC2PATH` env var inside the container is set by the Dockerfile to
`/opt/StarCraftII`. If the resolver complains anyway, the SC2 zip
likely failed to extract — check `docker run --rm -it
--entrypoint /bin/bash alpha4gate-worker` and `ls /opt/StarCraftII`.

### Container dies with `permission denied` writing replays/logs

The non-root `alpha4gate` user owns `/app` but not `/opt/StarCraftII`.
If burnysc2 is configured to write replays inside the SC2 dir
(non-default), either override `--save-replay` to a path under `/app`
or chown `/opt/StarCraftII` to alpha4gate (rebuild required).

### `bots.current` resolution surprise

`python -m bots.current` resolves to whatever version is recorded in
`bots/current/current.txt` at the time the image was BUILT. To re-pin
to a different version after a promotion, rebuild the image. To run a
specific version explicitly, override CMD:

```powershell
docker run --rm alpha4gate-worker -m bots.v4 --role solo
```

### `uv sync` re-runs on code-only rebuild (~190 s wasted)

Code-only rebuilds correctly cache `sc2-base` and `python-base`
(saves the 8.6 GB SC2 zip from re-downloading) but `uv sync
--no-install-project` re-runs even though its inputs
(`pyproject.toml` + `uv.lock`) are byte-identical. Validated against
Docker BuildKit during Step 9 done-when (5). Suspect: a uv-cache
mtime sensitivity under BuildKit. Workaround: none yet — the layer
re-runs from a populated wheel cache so the actual download skips,
but the install-and-bytecode-compile re-runs. Investigation deferred;
not a blocker for normal use.

## See also

- `documentation/plans/phase-8-build-plan.md` — the full Phase 8 plan,
  including the SC2PATH resolver, Linux CI, and the conditional Step 12
  cloud dry-run.
- `documentation/wiki/linux-dev-environment.md` — WSL2 dev setup
  (alternative to Docker for local development).
- `Dockerfile` — the canonical image spec; layer ordering and ENV
  rationale is commented inline.
- `.dockerignore` — what's excluded from the build context and why.
