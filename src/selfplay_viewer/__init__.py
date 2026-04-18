"""Self-play viewer — pygame container that frames two SC2 panes.

Step 4 surface: layout math + background loader (Step 1) + Win32
reparent primitive (Step 2) + ``attach_pane`` / ``detach_pane`` (Step 3)
+ live ``run_with_batch`` hand-off so the viewer can drive a
multi-game :func:`orchestrator.selfplay.run_batch` and slot each
game's SC2 pair into the two panes as it launches.

Public API:

* :class:`SelfPlayViewer` — the container. ``attach_pane`` /
  ``detach_pane`` for direct Win32 control; ``on_game_start`` /
  ``on_game_end`` as thread-safe callbacks for
  :func:`orchestrator.selfplay.run_batch`; ``run_with_batch`` to
  drive a live batch from the viewer's pygame loop.
* :class:`AttachedPane` — per-slot bookkeeping record.
"""

from __future__ import annotations

from selfplay_viewer.container import AttachedPane, SelfPlayViewer

__all__ = ["AttachedPane", "SelfPlayViewer"]
