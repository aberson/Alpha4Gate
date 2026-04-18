"""Cross-platform tests for ``selfplay_viewer.reparent``.

These tests run on Linux CI and on Windows. They exercise the
cross-platform guard contract:

* ``import selfplay_viewer.reparent`` must succeed on Linux even
  though pywin32 is Windows-only.
* Every public function must raise ``RuntimeError("reparent.py
  requires Windows")`` when ``sys.platform != "win32"``.

We do NOT use a module-level ``pytestmark`` skip here (contrast
``tests/test_reparent.py``) — that's the whole point: the cross-
platform guard must be verifiable on any platform.
"""

from __future__ import annotations

import importlib
import sys

import pytest


def test_import_succeeds_without_pywin32_at_module_level() -> None:
    """Importing the module must not pull pywin32 names into its namespace.

    All pywin32 imports (win32gui, win32con, win32process, win32api,
    pywintypes) must be lazy — done inside function bodies or guarded
    by ``if TYPE_CHECKING`` — so the module imports cleanly on Linux
    where pywin32 is not installable.

    The check here inspects ``reparent.__dict__`` directly rather than
    ``sys.modules`` because a parallel test (or the user's shell) may
    have already imported pywin32 independently; what we care about is
    that *this module* does not bind those names at import time.
    """
    # Fresh import so we're measuring the module we're actually shipping.
    sys.modules.pop("selfplay_viewer.reparent", None)
    mod = importlib.import_module("selfplay_viewer.reparent")

    forbidden = {"win32gui", "win32con", "win32process", "win32api", "pywintypes"}
    leaked = forbidden & set(vars(mod))
    assert not leaked, (
        f"reparent.py leaked pywin32 names into its module namespace "
        f"at import time: {sorted(leaked)!r}. These imports must be "
        f"lazy (inside function bodies) or TYPE_CHECKING-only."
    )


@pytest.mark.parametrize(
    "fn_name",
    ["find_hwnd_for_pid", "attach_window", "move_window", "detach_window"],
)
def test_non_windows_call_raises_runtime_error(
    fn_name: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Every public function raises ``RuntimeError`` on non-win32 platforms.

    We monkeypatch ``sys.platform`` to ``"linux"`` so the test
    exercises the guard regardless of the host OS. The test runs on
    the main thread (pytest default) so the main-thread assertion
    doesn't fire first; if it did, the RuntimeError message would
    mention "main thread" rather than the Windows guard.
    """
    monkeypatch.setattr(sys, "platform", "linux")

    sys.modules.pop("selfplay_viewer.reparent", None)
    mod = importlib.import_module("selfplay_viewer.reparent")

    with pytest.raises(RuntimeError, match="reparent.py requires Windows"):
        if fn_name == "find_hwnd_for_pid":
            mod.find_hwnd_for_pid(pid=999999, timeout_s=0.1)
        elif fn_name == "attach_window":
            mod.attach_window(0, 0, (0, 0, 10, 10))
        elif fn_name == "move_window":
            mod.move_window(0, (0, 0, 10, 10))
        elif fn_name == "detach_window":
            mod.detach_window(0)
        else:  # pragma: no cover — parametrize guard
            raise AssertionError(f"unknown fn_name: {fn_name}")
