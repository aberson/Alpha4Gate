"""Single-match runner and CLI for Jev policy packages (plan D6).

``python -m bots.jev.v1`` is a thin entry that calls :func:`main` with its
packaged policy loader. Defaults match the plan's launch command::

    --map Simple64 --opponent-race Terran --difficulty 1 --seed 1
    --max-game-seconds 900 --max-wall-seconds 1800

plus ``--realtime``, and ``--validate-policy`` (with optional ``--policy-file``)
to validate and exit without SC2.

A match is one built-in-AI game: no daemon, no automatic restart. The SC2 install
and map are resolved before anything launches (the repository's SC2 path
resolver, then burnysc2's own map lookup); if either is missing, burnysc2 cannot
be imported, or the match fails before the bot attaches to a running game (SC2
cannot launch, connect or create it, or burnysc2 exits with a nonzero status),
the run fails with ``sc2_unavailable``. Ctrl+C asks the bot to leave cleanly and
the run records ``stopped`` (during SC2's launch, burnysc2 cleans up the SC2 it
started and exits; that is recorded as ``stopped`` too); SC2 processes are never
blanket-killed. A game-time or wall-clock limit makes the bot leave and records
result ``timeout`` -- status ``finished`` for the game clock (the match ran its
allotted game time), ``failed`` for the wall clock (the host could not play it in
time); the wall-clock limit counts from the first game step.

Exit codes: :data:`EXIT_OK` for a finished win/loss/draw and a valid policy;
:data:`EXIT_FAILURE` for an invalid policy, unavailable SC2, a crash
(``match_crashed``, even when burnysc2 reported the crash as a Defeat or exited
with a nonzero status after the bot attached) or a match that ended without a result;
:data:`EXIT_USAGE` for command-line errors;
:data:`EXIT_TIMEOUT` when a game-time or wall-clock limit ended the match;
:data:`EXIT_STOPPED` after Ctrl+C.

The validation path imports only the standard library and ``jev``: burnysc2 is
imported lazily, after the SC2 preflight passed.
"""

from __future__ import annotations

import argparse
import io
import itertools
import os
import re
import signal
import sys
import threading
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Final, NoReturn, Protocol

from jev.contracts import (
    MAX_MESSAGE_CHARS,
    JevError,
    RunResult,
    RunStatus,
    full_match,
    render_lines,
    render_text,
    safe_exception_text,
    safe_repr,
)
from jev.policy import PolicyBundle, PolicyError, describe_bundle

if TYPE_CHECKING:
    from jev.bot import JevController, MatchResult

__all__ = [
    "DEFAULT_DIFFICULTY",
    "DEFAULT_MAP",
    "DEFAULT_MAX_GAME_SECONDS",
    "DEFAULT_MAX_WALL_SECONDS",
    "DEFAULT_OPPONENT_RACE",
    "DEFAULT_SEED",
    "EXIT_FAILURE",
    "EXIT_OK",
    "EXIT_STOPPED",
    "EXIT_TIMEOUT",
    "EXIT_USAGE",
    "MAP_NAME_RE",
    "MAX_DIFFICULTY",
    "MAX_LIMIT_SECONDS",
    "MAX_SEED",
    "MatchLauncher",
    "MatchLimits",
    "MatchOptions",
    "MatchOutcome",
    "OPPONENT_RACES",
    "Sc2Launcher",
    "Sc2Setup",
    "Sc2Unavailable",
    "TerminalSafeArgumentParser",
    "build_parser",
    "main",
    "run_match",
]

DEFAULT_MAP: Final = "Simple64"
DEFAULT_OPPONENT_RACE: Final = "Terran"
DEFAULT_DIFFICULTY: Final = 1
DEFAULT_SEED: Final = 1
DEFAULT_MAX_GAME_SECONDS: Final = 900
DEFAULT_MAX_WALL_SECONDS: Final = 1800
#: burnysc2 ``Race`` member names a built-in opponent may play.
OPPONENT_RACES: Final = ("Terran", "Zerg", "Protoss", "Random")
#: burnysc2 ``Difficulty`` values run from VeryEasy (1) to CheatInsane (10).
MAX_DIFFICULTY: Final = 10
#: SC2's game seed is an unsigned 32-bit integer.
MAX_SEED: Final = 2**32 - 1
#: Largest game-time / wall-clock limit a match accepts, in seconds (one day).
MAX_LIMIT_SECONDS: Final = 86_400
#: A map is a file stem looked up under the SC2 Maps folder: no path separators.
MAP_NAME_RE: Final = re.compile(r"\A[A-Za-z0-9][A-Za-z0-9_-]{0,63}\Z")

EXIT_OK: Final = 0
EXIT_FAILURE: Final = 1
EXIT_USAGE: Final = 2
EXIT_TIMEOUT: Final = 3
EXIT_STOPPED: Final = 130

#: Directory entries scanned per folder while resolving the install and map.
_MAX_SCANNED_ENTRIES: Final = 4096


class Sc2Unavailable(Exception):
    """SC2 or the requested map cannot be used; stable error code ``sc2_unavailable``.

    ``message`` is built from rendered fragments and is terminal-safe.
    """

    code: Final = "sc2_unavailable"

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


def _is_int_in(value: object, low: int, high: int) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and low <= value <= high


@dataclass(frozen=True)
class MatchLimits:
    """Hard limits for one match (plan D6): game seconds and wall-clock seconds."""

    max_game_seconds: int
    max_wall_seconds: int

    def __post_init__(self) -> None:
        for name in ("max_game_seconds", "max_wall_seconds"):
            value = getattr(self, name)
            if not _is_int_in(value, 1, MAX_LIMIT_SECONDS):
                raise ValueError(
                    f"{name} must be an integer in 1..{MAX_LIMIT_SECONDS}, got {safe_repr(value)}"
                )


@dataclass(frozen=True)
class MatchOptions:
    """One match's settings (plan D6 defaults); invalid values raise ValueError."""

    map_name: str = DEFAULT_MAP
    opponent_race: str = DEFAULT_OPPONENT_RACE
    difficulty: int = DEFAULT_DIFFICULTY
    seed: int = DEFAULT_SEED
    max_game_seconds: int = DEFAULT_MAX_GAME_SECONDS
    max_wall_seconds: int = DEFAULT_MAX_WALL_SECONDS
    realtime: bool = False

    def __post_init__(self) -> None:
        if not full_match(MAP_NAME_RE, self.map_name):
            raise ValueError(f"map_name is not a map name: {safe_repr(self.map_name)}")
        if self.opponent_race not in OPPONENT_RACES:
            raise ValueError(
                f"opponent_race must be one of {OPPONENT_RACES}, "
                f"got {safe_repr(self.opponent_race)}"
            )
        if not _is_int_in(self.difficulty, 1, MAX_DIFFICULTY):
            raise ValueError(
                f"difficulty must be in 1..{MAX_DIFFICULTY}, got {safe_repr(self.difficulty)}"
            )
        if not _is_int_in(self.seed, 0, MAX_SEED):
            raise ValueError(f"seed must be in 0..{MAX_SEED}, got {safe_repr(self.seed)}")
        if not isinstance(self.realtime, bool):
            raise ValueError(f"realtime must be a bool, got {safe_repr(self.realtime)}")
        self.limits()  # validates both limits

    def limits(self) -> MatchLimits:
        return MatchLimits(self.max_game_seconds, self.max_wall_seconds)


@dataclass(frozen=True)
class MatchOutcome:
    """How a match run ended. ``message`` is rendered, terminal-safe text."""

    run_id: str
    status: RunStatus
    result: RunResult | None
    error: JevError | None
    message: str
    game_seconds: float
    commands_accepted: int
    commands_rejected: int
    exit_code: int


@dataclass(frozen=True)
class Sc2Setup:
    """A resolved SC2 install and map file (from :meth:`Sc2Launcher.prepare`)."""

    sc2_path: Path
    map_file: Path


class MatchLauncher(Protocol):
    """Prepares and plays one match for a controller (burnysc2 in production)."""

    def prepare(self, options: MatchOptions) -> object:
        """Resolve everything needed to play; raise :class:`Sc2Unavailable` if not."""
        ...

    def play(
        self, controller: JevController, setup: object, options: MatchOptions
    ) -> MatchResult | None:
        """Play the match to its end; return the SC2 result (None if undecided)."""
        ...


def _entries(folder: Path) -> list[Path]:
    try:
        return list(itertools.islice(folder.iterdir(), _MAX_SCANNED_ENTRIES))
    except OSError as exc:
        detail = safe_exception_text(exc)
        raise Sc2Unavailable(f"cannot read {safe_repr(str(folder))}: {detail}") from exc


def _find_map(maps_dir: Path, name: str) -> Path | None:
    """burnysc2's lookup: ``<name>.SC2Map`` directly in Maps or one folder down."""
    for entry in _entries(maps_dir):
        if entry.is_dir():
            for child in _entries(entry):
                if child.is_file() and child.suffix == ".SC2Map" and child.stem == name:
                    return child
        elif entry.is_file() and entry.suffix == ".SC2Map" and entry.stem == name:
            return entry
    return None


class Sc2Launcher:
    """The production launcher: a real SC2 match through burnysc2."""

    def prepare(self, options: MatchOptions) -> Sc2Setup:
        """Resolve the SC2 install and map before anything launches."""
        from orchestrator.paths import resolve_sc2_path

        try:
            sc2_path = resolve_sc2_path()
        except RuntimeError as exc:
            raise Sc2Unavailable(safe_exception_text(exc)) from exc
        shown = safe_repr(str(sc2_path))
        versions = sc2_path / "Versions"
        if not versions.is_dir():
            raise Sc2Unavailable(f"no StarCraft II install at {shown} (set SC2PATH)")
        if not any(e.is_dir() and e.name.startswith("Base") for e in _entries(versions)):
            raise Sc2Unavailable(f"no StarCraft II executable under {shown}")
        maps_dir = sc2_path / "maps" if (sc2_path / "maps").is_dir() else sc2_path / "Maps"
        if not maps_dir.is_dir():
            raise Sc2Unavailable(f"no Maps folder under {shown}")
        map_file = _find_map(maps_dir, options.map_name)
        if map_file is None:
            raise Sc2Unavailable(
                f"map {safe_repr(options.map_name)} not found under {safe_repr(str(maps_dir))}"
            )
        return Sc2Setup(sc2_path=sc2_path, map_file=map_file)

    def play(
        self, controller: JevController, setup: object, options: MatchOptions
    ) -> MatchResult | None:
        """One built-in-AI match; burnysc2 owns SC2's process and signal lifecycle."""
        from sc2 import maps
        from sc2.data import Difficulty, Race
        from sc2.main import run_game
        from sc2.paths import Paths
        from sc2.player import Bot, Computer

        from jev.bot import JevBot, match_result

        if not isinstance(setup, Sc2Setup):
            raise TypeError(f"setup must come from Sc2Launcher.prepare, got {safe_repr(setup)}")
        os.environ["SC2PATH"] = str(setup.sc2_path)  # burnysc2 resolves the same install
        try:
            # burnysc2 resolves its paths lazily and calls sys.exit() without an install.
            executable = Path(Paths.EXECUTABLE)
            map_settings = maps.get(options.map_name)
        except (KeyError, OSError, ValueError, TypeError, SystemExit) as exc:
            raise Sc2Unavailable(f"burnysc2 setup failed: {safe_exception_text(exc)}") from exc
        if not executable.is_file():
            shown = safe_repr(str(executable))
            raise Sc2Unavailable(f"burnysc2 found no SC2 executable at {shown}")
        players = [
            Bot(Race.Protoss, JevBot(controller)),
            Computer(Race[options.opponent_race], Difficulty(options.difficulty)),
        ]
        main_thread = threading.current_thread() is threading.main_thread()
        previous = signal.getsignal(signal.SIGINT) if main_thread else None
        try:
            # A failure before the bot attaches (SC2 could not launch, connect or
            # create the game) is classified sc2_unavailable by run_match.
            result = run_game(
                map_settings, players, realtime=options.realtime, random_seed=options.seed
            )
        finally:
            if previous is not None:
                # burnysc2 leaves SIGINT at SIG_DFL after a match; restore Ctrl+C.
                signal.signal(signal.SIGINT, previous)
        return match_result(result)


def _limit_seconds(reason: str, limit: int) -> str:
    return f"{reason} limit of {limit} seconds reached"


def run_match(
    options: MatchOptions,
    bundle: PolicyBundle,
    *,
    launcher: MatchLauncher | None = None,
    run_id: str | None = None,
    clock: Callable[[], float] = time.monotonic,
) -> MatchOutcome:
    """Prepare and play one match; never raises for match-level failures.

    ``launcher`` defaults to :class:`Sc2Launcher`. Infrastructure failure, a
    crash, a time limit and a stop all come back as a :class:`MatchOutcome` with
    a nonzero exit code; a win, loss or draw exits zero. A failure before the bot
    attached to a running game -- burnysc2 failing to import, any exception, or
    a nonzero ``SystemExit`` -- is ``sc2_unavailable``; after it, ``match_crashed``
    unless the controller had already ended the match (a stop or time limit stays
    one, e.g. when :class:`~jev.bot.LeaveFailed` ended burnysc2's loop).
    """
    launcher = Sc2Launcher() if launcher is None else launcher
    run_id = uuid.uuid4().hex if run_id is None else run_id
    try:
        setup = launcher.prepare(options)
    except Sc2Unavailable as exc:
        return _unavailable(run_id, exc)

    try:
        from jev.bot import JevController  # imports burnysc2: only after the preflight
    except (Exception, SystemExit) as exc:  # a broken burnysc2 is infrastructure, not a crash
        return _unavailable(run_id, _never_started("burnysc2 could not be imported", exc))

    controller = JevController(bundle, run_id=run_id, limits=options.limits(), clock=clock)
    try:
        result = launcher.play(controller, setup, options)
    except Sc2Unavailable as exc:
        return _unavailable(run_id, exc)
    except KeyboardInterrupt:
        controller.request_stop()  # Ctrl+C before the bot could leave on its own
        result = None
    except SystemExit as exc:
        # burnysc2 exits instead of raising: sys.exit() (status 0) once its own Ctrl+C
        # handler has cleaned up the SC2 it launched, sys.exit(2) on a broken exchange.
        if exc.code is None or exc.code == 0:
            controller.request_stop()
        elif not controller.attached:
            return _unavailable(run_id, _never_started("SC2 exited before the match began", exc))
        elif controller.terminal is None:
            controller.record_crash(exc)
        result = None
    except Exception as exc:
        if not controller.attached:
            return _unavailable(run_id, _never_started("SC2 could not start the match", exc))
        if controller.terminal is None and not controller.stop_requested:
            controller.record_crash(exc)
        result = None  # SC2's own result, if any, never hides a recorded terminal reason
    terminal = controller.terminal
    if terminal == "crashed":
        crash = controller.crash
        message = "match crashed" if crash is None else f"match crashed: {crash.message}"
        return _outcome(controller, "failed", None, crash, message, EXIT_FAILURE)
    if terminal == "stopped" or (terminal is None and controller.stop_requested):
        return _outcome(controller, "stopped", None, None, "stopped on request", EXIT_STOPPED)
    if terminal == "game_timeout":
        message = _limit_seconds("game-time", options.max_game_seconds)
        error = JevError("game_timeout", message)
        return _outcome(controller, "finished", "timeout", error, message, EXIT_TIMEOUT)
    if terminal == "wall_timeout":
        message = _limit_seconds("wall-clock", options.max_wall_seconds)
        error = JevError("wall_timeout", message)
        return _outcome(controller, "failed", "timeout", error, message, EXIT_TIMEOUT)
    if result is None:
        message = "the match ended without a result"
        return _outcome(controller, "failed", None, None, message, EXIT_FAILURE)
    return _outcome(controller, "finished", result, None, f"result {result}", EXIT_OK)


def _never_started(what: str, exc: BaseException) -> Sc2Unavailable:
    """An ``sc2_unavailable`` error for a failure before the bot attached (rendered)."""
    name = safe_repr(type(exc).__name__)
    if isinstance(exc, SystemExit):
        detail = f"exit status {safe_repr(exc.code)}"
    else:
        detail = safe_exception_text(exc)
    return Sc2Unavailable(f"{what} ({name}: {detail})")


def _unavailable(run_id: str, exc: Sc2Unavailable) -> MatchOutcome:
    message = render_text(exc.message)[:MAX_MESSAGE_CHARS]
    return MatchOutcome(
        run_id=run_id,
        status="failed",
        result=None,
        error=JevError("sc2_unavailable", message),
        message=message,
        game_seconds=0.0,
        commands_accepted=0,
        commands_rejected=0,
        exit_code=EXIT_FAILURE,
    )


def _outcome(
    controller: JevController,
    status: RunStatus,
    result: RunResult | None,
    error: JevError | None,
    message: str,
    exit_code: int,
) -> MatchOutcome:
    if controller.leave_failure is not None:
        message = f"{message}; leaving the game failed: {controller.leave_failure}"
    return MatchOutcome(
        run_id=controller.runtime.run_id,
        status=status,
        result=result,
        error=error,
        message=render_text(message),
        game_seconds=controller.game_seconds,
        commands_accepted=controller.adapter.accepted_count,
        commands_rejected=controller.adapter.rejected_count,
        exit_code=exit_code,
    )


# ---------------------------------------------------------------------------
# Command line
# ---------------------------------------------------------------------------


class TerminalSafeArgumentParser(argparse.ArgumentParser):
    """ArgumentParser whose every emitted message is terminal-safe and capped.

    argparse echoes argv in its errors (unrecognized arguments, invalid values),
    so ``error`` escapes the message on one line (at most MAX_MESSAGE_CHARS) and
    ``exit`` renders whatever it prints. The usage-error exit code (2) is kept.
    """

    def error(self, message: str) -> NoReturn:
        self.print_usage(sys.stderr)
        text = render_text(message)
        if len(text) > MAX_MESSAGE_CHARS:
            text = f"{text[:MAX_MESSAGE_CHARS]}... (+{len(text) - MAX_MESSAGE_CHARS} chars)"
        self.exit(EXIT_USAGE, f"{self.prog}: error: {text}\n")

    def exit(self, status: int = 0, message: str | None = None) -> NoReturn:
        if message:
            sys.stderr.write(render_lines(message))
        raise SystemExit(status)


def _bounded_int(low: int, high: int) -> Callable[[str], int]:
    def parse(text: str) -> int:
        try:
            value = int(text)
        except ValueError:
            value = None
        if value is None or not low <= value <= high:
            raise argparse.ArgumentTypeError(
                f"expected an integer in {low}..{high}, got {safe_repr(text)}"
            )
        return value

    return parse


def _map_name(text: str) -> str:
    if not full_match(MAP_NAME_RE, text):
        raise argparse.ArgumentTypeError(
            f"expected a map name (letters, digits, '_' or '-'), got {safe_repr(text)}"
        )
    return text


def build_parser(prog: str) -> argparse.ArgumentParser:
    parser = TerminalSafeArgumentParser(
        prog=prog,
        description="Jev decision-graph player: one match against the built-in AI.",
    )
    parser.add_argument(
        "--map", type=_map_name, default=DEFAULT_MAP, help="SC2 map (default: %(default)s)"
    )
    parser.add_argument(
        "--opponent-race",
        choices=OPPONENT_RACES,
        default=DEFAULT_OPPONENT_RACE,
        help="built-in opponent race (default: %(default)s)",
    )
    parser.add_argument(
        "--difficulty",
        type=_bounded_int(1, MAX_DIFFICULTY),
        default=DEFAULT_DIFFICULTY,
        help="built-in AI difficulty 1-10 (default: %(default)s)",
    )
    parser.add_argument(
        "--seed",
        type=_bounded_int(0, MAX_SEED),
        default=DEFAULT_SEED,
        help="SC2 game seed (default: %(default)s)",
    )
    parser.add_argument(
        "--max-game-seconds",
        type=_bounded_int(1, MAX_LIMIT_SECONDS),
        default=DEFAULT_MAX_GAME_SECONDS,
        help="end the match at this game time (default: %(default)s)",
    )
    parser.add_argument(
        "--max-wall-seconds",
        type=_bounded_int(1, MAX_LIMIT_SECONDS),
        default=DEFAULT_MAX_WALL_SECONDS,
        help=(
            "end the match after this much wall-clock time, counted from the first game "
            "step; SC2's launch is bounded by burnysc2's own connect timeout "
            "(default: %(default)s)"
        ),
    )
    parser.add_argument("--realtime", action="store_true", help="play at real-time speed")
    parser.add_argument(
        "--validate-policy",
        action="store_true",
        help="validate the packaged policy and manifest, print the policy hash, and exit",
    )
    parser.add_argument(
        "--policy-file",
        type=Path,
        default=None,
        help="with --validate-policy: validate this candidate policy document instead",
    )
    return parser


def _make_streams_encoding_safe() -> None:
    """Never let an unencodable character (e.g. a CJK path on cp1252) crash output."""
    for stream in (sys.stdout, sys.stderr):
        if isinstance(stream, io.TextIOWrapper):
            stream.reconfigure(errors="backslashreplace")


def _report(outcome: MatchOutcome) -> None:
    summary = (
        f"jev match {outcome.status}: result={outcome.result} run_id={outcome.run_id} "
        f"game_seconds={outcome.game_seconds:.1f} commands_accepted={outcome.commands_accepted} "
        f"commands_rejected={outcome.commands_rejected}"
    )
    print(render_text(summary))
    if outcome.exit_code != EXIT_OK:
        code = outcome.error.code if outcome.error is not None else outcome.status
        print(render_text(f"jev: {code}: {outcome.message}"), file=sys.stderr)


def main(
    argv: list[str] | None = None,
    *,
    load_policy: Callable[[Path | None], PolicyBundle],
    prog: str,
    launcher: MatchLauncher | None = None,
) -> int:
    """Parse ``argv``, then validate the policy or play one match; return the exit code.

    ``load_policy`` loads the calling package's policy (``bots.jev.vN``).
    """
    _make_streams_encoding_safe()
    parser = build_parser(prog)
    args = parser.parse_args(argv)
    if args.policy_file is not None and not args.validate_policy:
        parser.error("--policy-file requires --validate-policy")
    try:
        bundle = load_policy(args.policy_file)
    except PolicyError as exc:
        print(render_lines(f"jev: {exc}"), file=sys.stderr)
        return EXIT_FAILURE
    if args.validate_policy:
        print(render_text(describe_bundle(bundle)))
        return EXIT_OK
    options = MatchOptions(
        map_name=args.map,
        opponent_race=args.opponent_race,
        difficulty=args.difficulty,
        seed=args.seed,
        max_game_seconds=args.max_game_seconds,
        max_wall_seconds=args.max_wall_seconds,
        realtime=args.realtime,
    )
    outcome = run_match(options, bundle, launcher=launcher)
    _report(outcome)
    return outcome.exit_code
