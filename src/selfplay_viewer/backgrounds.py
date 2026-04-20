"""Background discovery for the self-play viewer.

Pure-Python module — does NOT import pygame. Enumerates image files
(``*.webp`` and ``*.png``) under ``src/selfplay_viewer/assets/`` and
derives a short key from each filename so the CLI accepts
``--background brazil`` instead of the full
``protoss_themed_sf2_brazil_background`` stem.

Conventions
-----------
Filenames matching ``protoss_themed_sf2_<key>_background.{webp,png}`` are
mapped to ``<key>``. Anything else falls back to the bare filename
stem (so ``tokyo.webp`` works with ``--background tokyo``). New drops
that follow either convention are picked up automatically — no code
change required.
"""

from __future__ import annotations

import random
from pathlib import Path

#: Default location of the background assets. Resolved from this file's
#: location: ``src/selfplay_viewer/backgrounds.py`` -> ``./assets``.
_DEFAULT_BACKGROUND_DIR: Path = Path(__file__).parent / "assets"

_PREFIX = "protoss_themed_sf2_"
_SUFFIX = "_background"


def _derive_key(stem: str) -> str:
    """Strip the project's filename boilerplate to get a short CLI key."""
    if stem.startswith(_PREFIX) and stem.endswith(_SUFFIX):
        return stem[len(_PREFIX) : -len(_SUFFIX)]
    return stem


def list_backgrounds(backgrounds_dir: Path | None = None) -> dict[str, Path]:
    """Enumerate image files and derive ``{key: path}`` from each filename.

    Parameters
    ----------
    backgrounds_dir:
        Directory to scan. Defaults to ``src/selfplay_viewer/assets``.

    Returns
    -------
    Dictionary mapping the derived short key to the absolute path. If
    the directory is missing or empty the dictionary is empty (callers
    handle the empty case — usually ``pick_background`` raising).
    """
    base = _DEFAULT_BACKGROUND_DIR if backgrounds_dir is None else backgrounds_dir
    if not base.exists():
        return {}
    out: dict[str, Path] = {}
    collisions: dict[str, list[Path]] = {}
    candidates = sorted([*base.glob("*.webp"), *base.glob("*.png")])
    for path in candidates:
        key = _derive_key(path.stem)
        if key in out:
            collisions.setdefault(key, [out[key]]).append(path)
        else:
            out[key] = path
    if collisions:
        lines = []
        for key, paths in sorted(collisions.items()):
            joined = ", ".join(str(p) for p in paths)
            lines.append(f"{key!r} collides: {joined}")
        detail = "; ".join(lines)
        raise ValueError(
            f"Background key collision detected in {base}: {detail}"
        )
    return out


def pick_background(
    key: str,
    rng: random.Random | None = None,
    backgrounds_dir: Path | None = None,
) -> Path:
    """Select a background path by key.

    ``key == "random"`` picks uniformly from the discovered pool. Any
    other key is looked up directly. Unknown keys raise ``KeyError``
    with a message listing the available keys (sorted) so the CLI can
    surface a useful hint.

    Parameters
    ----------
    key:
        Either ``"random"`` or the derived short key for a specific
        background.
    rng:
        Optional ``random.Random`` instance for deterministic random
        selection (used by tests and the eventual ``--seed`` CLI flag).
        When ``None`` the module-level ``random`` is used.
    backgrounds_dir:
        Directory to scan. Defaults to ``src/selfplay_viewer/assets``.
    """
    available = list_backgrounds(backgrounds_dir=backgrounds_dir)
    if not available:
        raise KeyError(
            f"No backgrounds found in "
            f"{_DEFAULT_BACKGROUND_DIR if backgrounds_dir is None else backgrounds_dir}"
        )
    if key == "random":
        keys_sorted = sorted(available)
        chooser = rng if rng is not None else random
        chosen = chooser.choice(keys_sorted)
        return available[chosen]
    if key not in available:
        keys_sorted = sorted(available)
        raise KeyError(
            f"Unknown background {key!r}. Available keys: {keys_sorted}"
        )
    return available[key]


__all__ = ["list_backgrounds", "pick_background"]
