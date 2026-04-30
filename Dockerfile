# syntax=docker/dockerfile:1.7
#
# Phase 8 Step 9 — Alpha4Gate headless Linux training worker.
#
# Multi-stage to maximize layer-cache reuse:
#   sc2-base    — Ubuntu 22.04 + libstdc++6 + Blizzard SC2 4.10 headless
#                 package (~3.83 GB zip → ~8.6 GB extracted). Rebuilds
#                 only when the SC2 version pin changes — typically never
#                 within a phase.
#   python-base — adds Python 3.12 (deadsnakes PPA) + uv (copied from
#                 the official uv image). Rebuilds on Python or uv bump.
#   app         — copies pyproject.toml + uv.lock first, runs
#                 `uv sync --frozen --no-dev`, THEN copies the rest of
#                 the repo. Code-only changes only invalidate the final
#                 COPY layer.
#
# Build:    docker build -t alpha4gate-worker .
# Run:      docker run --rm alpha4gate-worker
#               # default: bots.current solo vs VeryEasy difficulty 1
# Override: docker run --rm alpha4gate-worker scripts/selfplay.py \
#               --p1 v0 --p2 v0 --games 2 --map Simple64
#           docker run --rm alpha4gate-worker -m bots.current \
#               --role solo --difficulty 3
#
# License: the SC2 4.10 zip is downloaded at build time per Blizzard's
# AI/ML headless-Linux license. The image is NOT redistributable —
# must be built locally on each host, MUST NOT be pushed to public
# registries.

# ---------------------------------------------------------------------------
# Stage 1: SC2 base — Ubuntu 22.04 + Blizzard SC2 4.10
# ---------------------------------------------------------------------------
FROM ubuntu:22.04 AS sc2-base

ARG SC2_PACKAGE_URL=https://blzdistsc2-a.akamaihd.net/Linux/SC2.4.10.zip
ARG DEBIAN_FRONTEND=noninteractive

# libstdc++6 + libc6 (already in base) are SC2's runtime ABI deps;
# ca-certificates + curl + unzip handle the download. After install we
# wipe apt lists to keep the layer tight.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates \
        curl \
        libstdc++6 \
        unzip \
    && rm -rf /var/lib/apt/lists/*

# Download + extract SC2.4.10. Password "iagreetotheeula" is Blizzard's
# documented EULA gate, not a secret. Unzipping with cwd=/opt produces
# /opt/StarCraftII/ directly (no doubled layout — that only happens
# when the user pre-creates a StarCraftII dir and cd's into it before
# extracting; see Phase 8 Spike 1 findings).
#
# Post-extraction tweaks per Spike 1 findings #3 + #7:
#   * copy bundled Simple64.SC2Map to flat Maps/ (SC2 expects it there).
#   * symlink lowercase maps -> Maps (SC2 4.10's map lookup is
#     case-sensitive on Linux).
RUN curl -LsSf -o /tmp/sc2.zip "${SC2_PACKAGE_URL}" \
    && cd /opt \
    && unzip -q -P iagreetotheeula /tmp/sc2.zip \
    && rm /tmp/sc2.zip \
    && cp /opt/StarCraftII/Maps/Melee/Simple64.SC2Map \
          /opt/StarCraftII/Maps/Simple64.SC2Map \
    && ln -s /opt/StarCraftII/Maps /opt/StarCraftII/maps \
    && chmod -R a+rX /opt/StarCraftII

# `chmod -R a+rX` ensures non-root users (the alpha4gate user we
# create in the app stage) can read SC2 binaries and traverse map
# dirs. The zip preserves Blizzard's original perms, some of which
# are 700 (e.g. Maps/Ladder2018Season4/) — caught the first build
# with a `PermissionError` from sc2.maps.iterdir(). Capital X only
# adds execute on items that already have it for someone, so file
# perms stay sensible (binaries 755, data files 644).

# ---------------------------------------------------------------------------
# Stage 2: Python + uv
# ---------------------------------------------------------------------------
FROM sc2-base AS python-base

ARG DEBIAN_FRONTEND=noninteractive

# Python 3.12 via deadsnakes PPA (Ubuntu 22.04 stock is 3.10).
# software-properties-common provides add-apt-repository; gnupg signs
# the deadsnakes archive.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        gnupg \
        software-properties-common \
    && add-apt-repository -y ppa:deadsnakes/ppa \
    && apt-get update \
    && apt-get install -y --no-install-recommends \
        python3.12 \
        python3.12-dev \
        python3.12-venv \
    && rm -rf /var/lib/apt/lists/*

# uv from the official Astral image — single static binary copy, no
# need to run a shell installer. `:latest` is acceptable for Phase 8
# since the project's lockfile is the source of truth for dep
# resolution; uv version bumps are independently visible in build logs.
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /usr/local/bin/

# ---------------------------------------------------------------------------
# Stage 3: app — sync deps + copy source as non-root alpha4gate user
# ---------------------------------------------------------------------------
FROM python-base AS app

# SC2PATH pins burnysc2 to the install dir we just laid down.
# SC2_WSL_DETECT=0 forces pure-Linux mode so burnysc2 doesn't try to
# auto-detect WSL2 and route through PowerShell — see memory
# feedback_burnysc2_wsl_autodetect_overrides_linux.md.
# UV_LINK_MODE=copy avoids hardlink errors across Docker layer
# boundaries. UV_COMPILE_BYTECODE=1 precompiles .pyc on install for
# faster startup. UV_PYTHON=python3.12 pins uv to the apt-installed
# interpreter so it doesn't try to download a different one.
ENV SC2PATH=/opt/StarCraftII \
    SC2_WSL_DETECT=0 \
    UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1 \
    UV_PROJECT_ENVIRONMENT=/app/.venv \
    UV_PYTHON=python3.12 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# Create the non-root user BEFORE uv sync so the venv is owned by
# alpha4gate from the start (avoids a multi-GB chown layer).
RUN useradd -m -s /bin/bash -u 1000 alpha4gate \
    && chown alpha4gate:alpha4gate /app

USER alpha4gate

# Copy lock files first so dep changes don't invalidate the source-
# code COPY below. `--no-install-project` installs all dependencies
# but skips installing the alpha4gate package itself — that requires
# README.md (per pyproject.toml's `readme = "README.md"`) which
# isn't copied until the next stage. Splitting like this preserves
# layer-cache efficiency: dep changes invalidate this RUN, but
# code-only changes only invalidate the final COPY + the cheap
# project-install step below. `--frozen` requires uv.lock to match
# pyproject.toml exactly (verify pre-build via `uv lock --check`).
# `--no-dev` skips pytest/ruff/mypy/pre-commit (runtime doesn't need them).
COPY --chown=alpha4gate:alpha4gate pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

# Source code. .dockerignore trims the context aggressively. After
# the COPY the project itself is installable, so finish the sync —
# this only does the editable install of alpha4gate, deps are cached.
COPY --chown=alpha4gate:alpha4gate . .
RUN uv sync --frozen --no-dev

# ENTRYPOINT is `uv run --no-sync python` so operators can override CMD
# with either `-m <module>` or a script path:
#   docker run image                                  # default smoke gate
#   docker run image -m bots.current --difficulty 3
#   docker run image scripts/selfplay.py --p1 v0 --p2 v0 --games 2 --map Simple64
#   docker run image scripts/evolve.py --hours 4
#
# `--no-sync` skips uv's pre-run consistency check; without it every
# `docker run` does a redundant ~1s "Uninstalled 1 package + Installed
# 1 package + Bytecode compiled 10055 files" cycle because uv detects
# the project at /app as out-of-sync vs /app/.venv. The image has the
# venv pre-built at build time; runtime sync is wasted work.
ENTRYPOINT ["uv", "run", "--no-sync", "python"]
CMD ["-m", "bots.current", "--role", "solo", "--map", "Simple64", "--difficulty", "1", "--decision-mode", "rules"]
