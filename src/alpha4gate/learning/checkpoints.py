"""Checkpoint management: save, load, prune, and track best model."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

_log = logging.getLogger(__name__)

_MANIFEST_NAME = "manifest.json"


def _default_manifest() -> dict[str, Any]:
    return {"checkpoints": [], "best": None}


def _load_manifest(checkpoint_dir: Path) -> dict[str, Any]:
    manifest_path = checkpoint_dir / _MANIFEST_NAME
    if manifest_path.exists():
        with open(manifest_path) as f:
            return json.load(f)  # type: ignore[no-any-return]
    return _default_manifest()


def _save_manifest(checkpoint_dir: Path, manifest: dict[str, Any]) -> None:
    manifest_path = checkpoint_dir / _MANIFEST_NAME
    with open(manifest_path, "w") as f:
        json.dump(manifest, f, indent=2)
        f.write("\n")


def save_checkpoint(
    model: Any,
    checkpoint_dir: str | Path,
    name: str,
    metadata: dict[str, Any] | None = None,
    is_best: bool = False,
) -> Path:
    """Save an SB3 model checkpoint and update the manifest.

    Args:
        model: SB3 model with a .save() method.
        checkpoint_dir: Directory to store checkpoints.
        name: Checkpoint name (e.g., "v0_pretrain", "v12").
        metadata: Optional dict of metrics to store in manifest.
        is_best: Whether this is the new best model.

    Returns:
        Path to the saved checkpoint file.
    """
    cp_dir = Path(checkpoint_dir)
    cp_dir.mkdir(parents=True, exist_ok=True)

    model.save(str(cp_dir / name))  # SB3 appends .zip automatically
    save_path = cp_dir / f"{name}.zip"

    manifest = _load_manifest(cp_dir)
    entry: dict[str, Any] = {"name": name, "file": f"{name}.zip"}
    if metadata:
        entry["metadata"] = metadata
    manifest["checkpoints"].append(entry)

    if is_best:
        manifest["best"] = name

    _save_manifest(cp_dir, manifest)
    _log.info("Saved checkpoint: %s (best=%s)", name, is_best)
    return save_path


def load_checkpoint(checkpoint_dir: str | Path, name: str | None = None) -> Any:
    """Load an SB3 model checkpoint.

    Args:
        checkpoint_dir: Directory containing checkpoints.
        name: Checkpoint name to load. If None, loads the best.

    Returns:
        Loaded SB3 PPO model.
    """
    from stable_baselines3 import PPO

    cp_dir = Path(checkpoint_dir)
    if name is None:
        manifest = _load_manifest(cp_dir)
        name = manifest.get("best")
        if name is None:
            msg = "No best checkpoint found in manifest"
            raise FileNotFoundError(msg)

    path = cp_dir / f"{name}.zip"
    if not path.exists():
        msg = f"Checkpoint not found: {path}"
        raise FileNotFoundError(msg)

    # Pass path without .zip — SB3's load() appends .zip automatically
    return PPO.load(str(cp_dir / name))


def promote_checkpoint(checkpoint_dir: str | Path, name: str) -> None:
    """Promote a checkpoint to best in the manifest.

    Args:
        checkpoint_dir: Directory containing checkpoints.
        name: Checkpoint name to promote.
    """
    cp_dir = Path(checkpoint_dir)
    manifest = _load_manifest(cp_dir)
    manifest["best"] = name
    _save_manifest(cp_dir, manifest)
    _log.info("Promoted checkpoint to best: %s", name)


def get_best_name(checkpoint_dir: str | Path) -> str | None:
    """Get the name of the best checkpoint, or None if no checkpoints exist."""
    manifest = _load_manifest(Path(checkpoint_dir))
    best = manifest.get("best")
    if isinstance(best, str):
        return best
    return None


def list_checkpoints(checkpoint_dir: str | Path) -> list[dict[str, Any]]:
    """List all checkpoints with their metadata."""
    manifest = _load_manifest(Path(checkpoint_dir))
    return manifest.get("checkpoints", [])  # type: ignore[no-any-return]


def prune_checkpoints(
    checkpoint_dir: str | Path,
    keep: int = 5,
) -> list[str]:
    """Remove old checkpoints, keeping the N most recent + the best.

    Returns:
        List of removed checkpoint names.
    """
    cp_dir = Path(checkpoint_dir)
    manifest = _load_manifest(cp_dir)
    checkpoints = manifest.get("checkpoints", [])
    best = manifest.get("best")

    if len(checkpoints) <= keep:
        return []

    # Keep the last `keep` entries and the best
    to_keep = checkpoints[-keep:]
    keep_names = {c["name"] for c in to_keep}
    if best:
        keep_names.add(best)

    removed: list[str] = []
    remaining: list[dict[str, Any]] = []
    for cp in checkpoints:
        if cp["name"] in keep_names:
            remaining.append(cp)
        else:
            # Delete the file
            path = cp_dir / cp["file"]
            if path.exists():
                path.unlink()
            removed.append(cp["name"])
            _log.info("Pruned checkpoint: %s", cp["name"])

    manifest["checkpoints"] = remaining
    _save_manifest(cp_dir, manifest)
    return removed
