"""Self-play viewer — pygame container that frames two SC2 panes.

Step 1 (this commit) ships only the windowed container skeleton: layout
math, background loader, and a `demo.py` entry that renders grey
placeholder rectangles where SC2 panes will eventually live. Win32
reparenting (Step 2) and `run_batch` integration (Step 4) come later.

`run_with_viewer` is a placeholder for a future Step-4 helper.
"""

from __future__ import annotations

from selfplay_viewer.container import SelfPlayViewer

__all__ = ["SelfPlayViewer"]
