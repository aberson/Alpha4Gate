"""``python -m selfplay_viewer.demo`` — visual smoke-test entry point.

Opens the themed pygame container with placeholder grey rectangles
where the SC2 panes will eventually live. Use this during Step 1
development to sanity-check the layout math without spawning real
SC2 processes.

Examples
--------
::

    python -m selfplay_viewer.demo
    python -m selfplay_viewer.demo --bar side --size small
    python -m selfplay_viewer.demo --background brazil
"""

from __future__ import annotations

import argparse

from selfplay_viewer.container import SelfPlayViewer


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m selfplay_viewer.demo",
        description=(
            "Open the self-play viewer container with placeholder panes "
            "(no SC2). Useful for layout / background smoke-testing."
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
    return parser


def main(argv: list[str] | None = None) -> None:
    args = _build_parser().parse_args(argv)
    viewer = SelfPlayViewer(
        bar=args.bar,
        size=args.size,
        background=args.background,
    )
    viewer.run()


if __name__ == "__main__":
    main()
