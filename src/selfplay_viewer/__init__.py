"""Self-play viewer — pygame container that frames two SC2 panes.

Step 3 surface: layout math + background loader (Step 1) + Win32
reparent primitive (Step 2) wired together so external callers can
host two SC2 client windows inside one themed pygame container via
``SelfPlayViewer.attach_pane`` / ``detach_pane``. ``run_batch``
integration (Step 4) comes next.

`run_with_viewer` is a placeholder for a future Step-4 helper.
"""

from __future__ import annotations

from selfplay_viewer.container import AttachedPane, SelfPlayViewer

__all__ = ["AttachedPane", "SelfPlayViewer"]
