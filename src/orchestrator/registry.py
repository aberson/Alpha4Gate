"""Version discovery + per-version data-path resolution.

Phase 1.7 fills in the real implementation. Until then, this stub exists so
downstream code can import `orchestrator.registry` and pick up the real API
when it lands without a second rename pass.

Planned public surface:

- `current_version() -> str` — reads `bots/current.txt`.
- `get_version_dir(name: str) -> pathlib.Path` — returns `bots/<name>`.
- `resolve_data_path(filename: str, version: str | None = None) -> pathlib.Path`
  — returns `bots/<v>/data/<filename>` if that file exists, else falls back to
  the legacy repo-root `data/<filename>`.
- `get_manifest(version: str) -> contracts.Manifest` — loads and validates
  `bots/<v>/manifest.json`.
"""
