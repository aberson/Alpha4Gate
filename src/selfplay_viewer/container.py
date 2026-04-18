"""``SelfPlayViewer`` — pygame container window for two SC2 panes.

Step 1 surface: opens a themed pygame window sized per the
``(bar, size)`` layout, blits a background PNG, and paints grey
placeholder rectangles where the SC2 panes will eventually be
reparented (Step 2). No SC2, no Win32 yet.

pygame is imported lazily inside methods so that
``from selfplay_viewer import SelfPlayViewer`` succeeds on Linux CI
where pygame is not installed (the ``[viewer]`` extra is Windows-only
in practice — see ``pyproject.toml``).
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Final

from selfplay_viewer.backgrounds import pick_background
from selfplay_viewer.overlay import (
    CONTAINER_SIZES,
    OVERLAY_RECTS,
    PANE_RECTS,
)

if TYPE_CHECKING:
    import pygame  # For types only — real import is inside .run()

_VALID_BARS: Final[frozenset[str]] = frozenset({"top", "side"})
_VALID_SIZES: Final[frozenset[str]] = frozenset({"large", "small"})

#: Grey placeholder colour for the SC2 panes (RGB).
PLACEHOLDER_COLOR: Final[tuple[int, int, int]] = (0xAA, 0xAA, 0xAA)

#: Border colour for the overlay stub (RGB).
OVERLAY_BORDER_COLOR: Final[tuple[int, int, int]] = (0xFF, 0xFF, 0xFF)

#: Semi-transparent dark fill for the overlay stub (RGBA).
OVERLAY_FILL_COLOR: Final[tuple[int, int, int, int]] = (0x00, 0x00, 0x00, 0x80)

#: Border thickness for the overlay stub (px).
OVERLAY_BORDER_PX: Final[int] = 4

#: Target frame rate for the demo loop.
TARGET_FPS: Final[int] = 30


class SelfPlayViewer:
    """Themed pygame container for two SC2 panes.

    Parameters
    ----------
    bar:
        Where the stats overlay sits — ``"top"`` for a banner, ``"side"``
        for a vertical right-edge bar.
    size:
        SC2 pane preset — ``"large"`` is 1024x768, ``"small"`` is
        960x720.
    background:
        Either ``"random"`` (default) or a derived key from
        ``selfplay_viewer.backgrounds.list_backgrounds``.
    """

    def __init__(
        self,
        bar: str = "top",
        size: str = "large",
        background: str = "random",
    ) -> None:
        if bar not in _VALID_BARS:
            raise ValueError(
                f"bar must be one of {sorted(_VALID_BARS)}, got {bar!r}"
            )
        if size not in _VALID_SIZES:
            raise ValueError(
                f"size must be one of {sorted(_VALID_SIZES)}, got {size!r}"
            )
        self.bar: str = bar
        self.size: str = size
        self.background: str = background

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def run(self) -> None:
        """Open the window, paint the demo scene, and pump events.

        Blocks until the user closes the window. pygame is imported here
        so that callers on Linux can import ``SelfPlayViewer`` without
        the ``[viewer]`` extras installed (e.g. for type-only inspection
        or tests that never call ``run``).
        """
        # Lazy import — pygame may not be installed on non-Windows.
        import pygame

        bg_path = self._resolve_background_path()

        pygame.init()
        try:
            width, height = CONTAINER_SIZES[(self.bar, self.size)]
            screen = pygame.display.set_mode((width, height))
            pygame.display.set_caption("Alpha4Gate self-play viewer")

            background_surface = self._load_background(bg_path, (width, height))

            clock = pygame.time.Clock()
            running = True
            while running:
                for event in pygame.event.get():
                    if event.type == pygame.QUIT:
                        running = False
                    elif event.type == pygame.KEYDOWN and event.key == pygame.K_ESCAPE:
                        running = False

                self._paint_frame(screen, background_surface)
                pygame.display.flip()
                clock.tick(TARGET_FPS)
        finally:
            pygame.quit()

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _resolve_background_path(self) -> Path:
        """Resolve the configured background key to an on-disk PNG path."""
        return pick_background(self.background)

    @staticmethod
    def _load_background(
        path: Path,
        target_size: tuple[int, int],
    ) -> pygame.Surface:
        """Load + smoothscale a PNG to the container size.

        pygame is imported lazily inside the function body so this helper
        does not force a module-level pygame dependency. Returns a
        ``pygame.Surface`` scaled to ``target_size``.
        """
        import pygame

        surface = pygame.image.load(str(path)).convert()
        return pygame.transform.smoothscale(surface, target_size)

    def _paint_frame(
        self,
        screen: pygame.Surface,
        background_surface: pygame.Surface,
    ) -> None:
        """Paint background + placeholder panes + overlay stub."""
        import pygame

        screen.blit(background_surface, (0, 0))

        p1_rect, p2_rect = PANE_RECTS[(self.bar, self.size)]
        for rect in (p1_rect, p2_rect):
            pygame.draw.rect(screen, PLACEHOLDER_COLOR, pygame.Rect(*rect))

        overlay_rect = OVERLAY_RECTS[(self.bar, self.size)]
        ox, oy, ow, oh = overlay_rect
        # Semi-transparent dark fill via a per-pixel-alpha overlay surface.
        overlay_surface = pygame.Surface((ow, oh), pygame.SRCALPHA)
        overlay_surface.fill(OVERLAY_FILL_COLOR)
        screen.blit(overlay_surface, (ox, oy))
        # Visible border on top.
        pygame.draw.rect(
            screen,
            OVERLAY_BORDER_COLOR,
            pygame.Rect(ox, oy, ow, oh),
            OVERLAY_BORDER_PX,
        )


__all__ = ["SelfPlayViewer"]
