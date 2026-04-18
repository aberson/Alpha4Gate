"""``python -m selfplay_viewer.demo`` — visual smoke-test entry point.

Opens the themed pygame container with placeholder grey rectangles
where the SC2 panes will eventually live. With ``--attach-pids A,B``
the two slots are wired to real Win32 child windows owned by the
given PIDs (Step 3 smoke-test for the reparent integration).

Examples
--------
::

    python -m selfplay_viewer.demo
    python -m selfplay_viewer.demo --bar side --size small
    python -m selfplay_viewer.demo --background brazil
    python -m selfplay_viewer.demo --attach-pids 1234,5678

Note
----
On Windows 11, spawn ``charmap.exe`` (or any classic Win32 app) when
smoke-testing ``--attach-pids`` rather than ``notepad.exe``: Win11
notepad is a UWP shim whose visible window is owned by a wrapper PID,
so ``find_hwnd_for_pid(notepad_pid)`` legitimately times out.
"""

from __future__ import annotations

import argparse

from selfplay_viewer.container import SelfPlayViewer


def _parse_attach_pids(raw: str) -> list[int]:
    """Parse the comma-separated ``--attach-pids`` argument into two positive ints.

    Raises ``argparse.ArgumentTypeError`` on any deviation so argparse
    surfaces a clean ``--attach-pids: invalid value`` message.
    """
    parts = [p.strip() for p in raw.split(",")]
    if len(parts) != 2:
        raise argparse.ArgumentTypeError(
            f"expected exactly 2 comma-separated PIDs, got {len(parts)}: {raw!r}"
        )
    pids: list[int] = []
    for part in parts:
        try:
            value = int(part)
        except ValueError as exc:
            raise argparse.ArgumentTypeError(
                f"PID {part!r} is not an integer"
            ) from exc
        if value <= 0:
            raise argparse.ArgumentTypeError(
                f"PID {value} must be positive"
            )
        pids.append(value)
    if pids[0] == pids[1]:
        raise argparse.ArgumentTypeError(
            f"PIDs must be distinct, got {pids[0]} twice"
        )
    return pids


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m selfplay_viewer.demo",
        description=(
            "Open the self-play viewer container with placeholder panes "
            "(no SC2). Use --attach-pids A,B to reparent two real Win32 "
            "windows into the pane slots. On Windows 11 use charmap.exe "
            "(or any classic Win32 app) for smoke-testing rather than "
            "notepad.exe -- Win11 notepad is a UWP shim whose window is "
            "owned by a wrapper PID."
        ),
    )
    parser.add_argument(
        "--bar",
        choices=("top", "side"),
        default="top",
        help="Where the stats overlay sits (default: top).",
    )
    parser.add_argument(
        "--size",
        choices=("large", "small"),
        default="large",
        help="SC2 pane size preset (default: large = 1024x768).",
    )
    parser.add_argument(
        "--background",
        default="random",
        help=(
            "Background key (e.g. 'brazil', 'china', or 'random' for a "
            "uniform pick). Default: random."
        ),
    )
    parser.add_argument(
        "--attach-pids",
        type=_parse_attach_pids,
        default=None,
        metavar="PID1,PID2",
        help=(
            "Comma-separated pair of process IDs to reparent into pane "
            "slots 0 and 1. Both must be positive integers. On Win11 "
            "use charmap.exe (NOT notepad.exe — UWP shim)."
        ),
    )
    return parser


def main(argv: list[str] | None = None) -> None:
    args = _build_parser().parse_args(argv)
    viewer = SelfPlayViewer(
        bar=args.bar,
        size=args.size,
        background=args.background,
    )
    viewer.run(attach_pids=args.attach_pids)


if __name__ == "__main__":
    main()
