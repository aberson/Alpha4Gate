"""Reproducible, resumable Jev benchmarks through the production runner (plan D1/D6).

A benchmark **batch** is a fixed list of cases (one built-in-AI match each) played
strictly one after another. Every case runs the real Jev entrypoint
(``python -m bots.jev.vN``) as a child process, from a **frozen source snapshot**,
with an explicit run root and explicit match options. Nothing on this path plays a
match in-process or substitutes another version, the current checkout or a default
option for the ones the manifest names.

**Panels** (plan D6). ``baseline``: frozen v1 with Typesafe, each race at
difficulty 3 and 4, seed 11. ``heldout-a`` / ``heldout-b``: each race at difficulty
4 with seed 101 / 202, frozen v1 and frozen v2 with Typesafe in alternating order.
``attribution``: the six v2 held-out cases with the scripted provider. Each is
exactly six games. ``staging`` is a one-game, scripted, non-realtime substrate
check of the current v1 source (no service call) -- never performance evidence.
A panel needing a version that is not packaged (today ``bots.jev.v2``) or not
frozen fails resolution with the reason; it never falls back.

**Frozen source** (plan D1). The runtime source set of a version -- every module of
the ``jev`` package, the SC2 path resolver it imports, the ``bots``/``bots.jev``
package markers, every file of ``bots/jev/vN`` and the dependency configuration
(``pyproject.toml``, ``uv.lock``) -- is copied byte for byte into
``data/jev/benchmarks/baselines/<vN>-<fingerprint prefix>/`` with a
``snapshot.json`` record. The **source fingerprint** is a SHA-256 over the sorted
project-relative paths and the bytes of exactly those files; identity is never
inferred from the git HEAD (the commit is recorded for information only). Data,
caches and credentials are never part of it, and a file whose name looks like a
credential stops the capture. A child runs with that snapshot as its working
directory and first import path, and its archived diagnostics must name the
snapshot as the runtime it executed from; the snapshot is re-hashed before and
after every case. ``--panel baseline`` finalizes the v1 snapshot as *the* baseline
(``baselines/v1.baseline.json``, written once, never replaced) in its preflight;
later batches run v1 from it whatever the current source is.

**Limits** (plan D6), mandatory defaults that the command line may only lower:
per match 900 game seconds, 1200 wall seconds and 450 service requests; per
invocation 6 games, 7200 wall seconds and 2700 requests. The child's own
``--max-wall-seconds`` is the match bound minus :data:`LAUNCH_ALLOWANCE_SECONDS`
(SC2 launch, leaving and replay save), so the whole child process tree is bounded
by the match bound; a child still alive then has its own tree terminated (only
that tree, never every SC2 process). Before each match the parent checks the
invocation budget and never starts a match that could exceed it; a hosted match is
charged the requests its archive verifies, or its whole allowance when it crashed
or its evidence cannot be verified.

**Model pinning** (plan D6). Hosted cases request :data:`PINNED_MODEL`; before the
first hosted case of an invocation one tiny request through the production
Typesafe client must return exactly that model, and every answer a match records
must too. ``jev-latest`` is never substituted.

**Storage.** ``data/jev/benchmarks/<batch_id>/`` holds ``manifest.json`` (written
once at creation and verified by hash on every resume), ``results.json`` (every
case's status, replaced atomically before and after each match),
``attempts.jsonl`` (append-only attempt records), ``attempts/`` (each child's
stdout/stderr) and ``lock.json`` (one owning process; a lock whose process is
verifiably gone is taken over, any other is refused). Runs themselves go to the
dashboard's run root (``data/jev/runs``) so they can be inspected like any run.

**Scoring** compares the child's summary line and exit code, the terminal
RunState, the run metadata, the archived policy, the replay reference and the
run's diagnostic summary (``jev.runner.DIAGNOSTICS_FILE``). A case is
``complete`` (a result of win/loss/draw/timeout/error) or ``invalid`` with a stable
reason code; an invalid case is never a win, and ties, timeouts and crashes never
count as wins. The first infrastructure failure, authentication failure,
provenance mismatch, model drift, corrupt evidence, interruption or exhausted
budget stops the batch. Resuming skips complete cases and never overwrites them;
a case found ``running`` (its benchmark process ended) is labeled ``interrupted``
first, and interrupted cases -- and ``launch_failed`` ones, which never started SC2
-- are replayed only with ``--retry-interrupted`` (:data:`RETRYABLE_REASONS`).

**Calibration.** ``--calibrate-run`` scores an existing run archive against an
explicit expectation with the same logic; archives older than the diagnostic
summary are scored from their complete trace (``legacy_trace``).

**Dashboard first** (plan D7, Step 224). A real invocation opens the dashboard
before anything is probed, captured or played: :class:`DashboardObserver` starts or
reuses healthy servers, creates ONE launch session for the invocation
(:mod:`jev.launch`), opens ``http://localhost:3000/?tab=jev&launch=<session_id>``
once, and gives every case's child ``--launch-session <session_id>``. Each child
publishes its recorded run to the session and starts SC2 only after the page
rendered that exact run; the same tab follows the next case. A dashboard that is
not healthy, serves another data root, or never acknowledges a run stops the batch
(``dashboard_unavailable`` before any case, ``launch_failed`` for a case); it never
falls back to headless. ``--no-dashboard`` is the explicit headless mode;
``--dry-run`` opens nothing. The frozen v1 baseline is finalized only by a real
``--panel baseline`` preflight, so it carries the launch hook
(:data:`OFFICIAL_PANEL_PLAY_ENABLED`).
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import functools
import hashlib
import itertools
import json
import os
import re
import signal
import socket
import subprocess
import sys
import tempfile
import time
import uuid
from collections import Counter
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path, PurePosixPath
from typing import Any, Final, Literal, Protocol, get_args

from jev.contracts import (
    MAX_ABS_NUMBER,
    TERMINAL_RUN_STATUSES,
    JsonValue,
    PolicyError,
    RunState,
    full_match,
    is_valid_run_id,
    render_lines,
    render_text,
    safe_exception_text,
    safe_repr,
)
from jev.decision import (
    AUTH_FAILURE_REASONS,
    Answer,
    DecisionConfig,
    DecisionError,
    DecisionProvider,
    TypesafeProvider,
)
from jev.launch import (
    DASHBOARD_URL,
    LAUNCH_SESSION_FLAG,
    RUN_READY_SECONDS,
    DashboardLaunch,
    DashboardUnavailable,
    LaunchState,
)
from jev.policy import PolicyBundle, load_policy_bundle, parse_json_document
from jev.runner import (
    DIAGNOSTICS_FIELDS,
    DIAGNOSTICS_FILE,
    DIAGNOSTICS_KIND,
    DIAGNOSTICS_SCHEMA_VERSION,
    EXIT_FAILURE,
    EXIT_OK,
    EXIT_STOPPED,
    EXIT_TIMEOUT,
    EXIT_USAGE,
    MAX_DIAGNOSTICS_BYTES,
    OPPONENT_RACES,
    MatchOptions,
    TerminalSafeArgumentParser,
    absolute_path,
    make_streams_encoding_safe,
    match_options_record,
)
from jev.telemetry import (
    COMMIT_RE,
    MAX_TRACE_SEGMENTS,
    POLICY_HASH_RE,
    REPLAY_FILE,
    RUN_ROOT_PARTS,
    STATE_FILE,
    CorruptRun,
    EvidenceFiles,
    PolicyHashMismatch,
    default_run_root,
    read_policy_archive,
    read_run_metadata,
    read_run_state,
    read_source_commit,
    read_trace_segment,
    repository_root,
    utc_timestamp,
)

__all__ = [
    "ATTEMPTS_FILE",
    "ATTEMPT_LOGS_DIR",
    "BASELINES_DIR",
    "BASELINE_VERSION",
    "BENCHMARK_ROOT_PARTS",
    "BatchManifest",
    "BatchOutcome",
    "BatchStatus",
    "BatchStore",
    "BenchmarkError",
    "BenchmarkErrorCode",
    "BenchmarkLimits",
    "CASE_RESULTS",
    "CASE_STATUSES",
    "CONFIG_FILES",
    "CaseObserver",
    "CaseRecord",
    "CaseResult",
    "CaseSpec",
    "CaseStatus",
    "ChildEvidence",
    "ChildExit",
    "ChildLaunch",
    "ChildLauncher",
    "CorruptRecord",
    "DashboardFactory",
    "DashboardObserver",
    "EXPECTED_RETURNED_MODEL",
    "Evidence",
    "Finding",
    "KEY_ENV",
    "LAUNCH_ALLOWANCE_SECONDS",
    "LIMIT_CEILINGS",
    "LOCK_FILE",
    "LockHeld",
    "LockOwner",
    "MANIFEST_FILE",
    "MAP",
    "MIN_CHILD_WALL_SECONDS",
    "OFFICIAL_PANELS",
    "OFFICIAL_PANEL_PLAY_ENABLED",
    "PACKAGE_FILES",
    "PANELS",
    "PANEL_CLAIMS",
    "PINNED_MODEL",
    "PROBE_CHOICES",
    "PROBE_STATE",
    "PROBE_TIMEOUT_SECONDS",
    "PROVIDERS",
    "Panel",
    "ProbeResult",
    "ProcessProbe",
    "ProvenanceMismatch",
    "Provider",
    "ProviderFactory",
    "RACES",
    "REASON_PRIORITY",
    "RESULTS_FILE",
    "RESULTS_STATUSES",
    "RETRYABLE_REASONS",
    "RUNTIME_PACKAGE",
    "Reason",
    "ResolvedSource",
    "ResultsStatus",
    "RunExpectation",
    "SCHEMA_VERSION",
    "SHARED_SOURCE_FILES",
    "SNAPSHOT_RECORD",
    "SOURCE_STATES",
    "STOPPING_REASONS",
    "STOP_GRACE_SECONDS",
    "STOP_REASONS",
    "ScoredRun",
    "SourceFile",
    "SourceSnapshot",
    "SourceState",
    "StopReason",
    "SubprocessLauncher",
    "SystemProcessProbe",
    "VersionUnavailable",
    "build_manifest",
    "build_parser",
    "capture_source",
    "child_argv",
    "child_environment",
    "child_hard_wall_seconds",
    "default_benchmark_root",
    "dry_run_plan",
    "finalize_baseline",
    "fingerprint_source",
    "freeze_candidate",
    "label_interrupted",
    "main",
    "panel_cases",
    "panel_versions",
    "parse_child_summary",
    "probe_model",
    "read_finalized",
    "read_manifest",
    "read_results",
    "resolve_sources",
    "run_batch",
    "runtime_source_paths",
    "score_run",
    "scorecard",
    "source_fingerprint",
    "terminate_process_tree",
    "verify_snapshot",
]

SCHEMA_VERSION: Final = 1
#: Plan D6: the model every hosted case requests, and the one each answer must name.
PINNED_MODEL: Final = "jev-1.13.0"
EXPECTED_RETURNED_MODEL: Final = "jev-1.13.0"
#: ``data/jev/benchmarks`` under the repository (ignored by git).
BENCHMARK_ROOT_PARTS: Final = ("data", "jev", "benchmarks")
BASELINES_DIR: Final = "baselines"
MANIFEST_FILE: Final = "manifest.json"
RESULTS_FILE: Final = "results.json"
ATTEMPTS_FILE: Final = "attempts.jsonl"
ATTEMPT_LOGS_DIR: Final = "attempts"
LOCK_FILE: Final = "lock.json"
SNAPSHOT_RECORD: Final = "snapshot.json"
#: The key the hosted provider reads from the child's environment.
KEY_ENV: Final = "TYPESAFE_API_KEY"
#: Plan D6 per-match wall bound covers SC2's launch and teardown too: the child is
#: told to leave the game this much earlier.
LAUNCH_ALLOWANCE_SECONDS: Final = 120
#: Shortest in-game wall limit a lowered match bound may leave the child.
MIN_CHILD_WALL_SECONDS: Final = 60
#: After Ctrl+C, how long the child may take to leave cleanly before its tree ends.
STOP_GRACE_SECONDS: Final = 90.0
#: The parent waits for its child in slices this long (keeps Ctrl+C responsive).
_WAIT_SLICE_SECONDS: Final = 0.5
#: The availability probe's total deadline (one tiny request).
PROBE_TIMEOUT_SECONDS: Final = 10.0
#: Plan D1/D7: the frozen v1 baseline is finalized only once the dashboard-first
#: launch hook exists (Step 224), so the frozen source carries it. A build without
#: it (False) refuses to finalize or play an official panel (``launch_integration_pending``).
OFFICIAL_PANEL_PLAY_ENABLED: Final = True

MAP: Final = "Simple64"
#: The frozen comparison baseline (plan D1); every other version is a candidate.
BASELINE_VERSION: Final = "v1"
#: Plan D6's race order for the panels: every race the runner accepts except
#: ``Random`` (a regression test pins them to ``jev.runner.OPPONENT_RACES``).
RACES: Final = ("Terran", "Protoss", "Zerg")
Panel = Literal["baseline", "heldout-a", "heldout-b", "attribution", "staging"]
PANELS: Final[tuple[Panel, ...]] = get_args(Panel)
OFFICIAL_PANELS: Final[frozenset[str]] = frozenset(
    {"baseline", "heldout-a", "heldout-b", "attribution"}
)
Provider = Literal["scripted", "typesafe"]
PROVIDERS: Final[tuple[Provider, ...]] = get_args(Provider)
CaseStatus = Literal["pending", "running", "complete", "invalid"]
CASE_STATUSES: Final[tuple[CaseStatus, ...]] = get_args(CaseStatus)
CaseResult = Literal["win", "loss", "draw", "timeout", "error"]
CASE_RESULTS: Final[tuple[CaseResult, ...]] = get_args(CaseResult)
#: Stable reasons a case (or a batch) is invalid or stopped.
Reason = Literal[
    "interrupted",
    "infrastructure_failure",
    "launch_failed",
    "authentication_failed",
    "corrupt_evidence",
    "provenance_mismatch",
    "model_unavailable",
    "model_drift",
    "no_accepted_hosted_decision",
    "budget_exhausted",
    "missing_configuration",
]
#: Order in which a case's findings pick its reason (the first present wins).
REASON_PRIORITY: Final[tuple[Reason, ...]] = get_args(Reason)
#: Reasons that stop the batch (plan D6); the rest only invalidate the case.
STOPPING_REASONS: Final[frozenset[str]] = frozenset(REASON_PRIORITY) - {
    "no_accepted_hosted_decision"
}
#: Invalid cases a resume replays with ``--retry-interrupted`` (they stay labeled until
#: then): an interrupted case, and one whose dashboard never acknowledged its run (SC2
#: was never started and nothing was spent).
RETRYABLE_REASONS: Final[frozenset[str]] = frozenset({"interrupted", "launch_failed"})
#: Codes a :class:`BenchmarkError` carries besides the case :data:`Reason` codes.
BenchmarkErrorCode = (
    Reason
    | Literal[
        "usage",
        "lock_held",
        "dashboard_unavailable",
        "launch_integration_pending",
        "version_not_packaged",
        "candidate_not_frozen",
    ]
)
#: How a batch invocation ended (``results.json`` status once it has run).
BatchStatus = Literal["complete", "incomplete", "stopped", "interrupted", "budget_exhausted"]
#: ``results.json``'s status: an ending, or ``created``/``running`` before one.
ResultsStatus = Literal["created", "running", BatchStatus]
RESULTS_STATUSES: Final[tuple[ResultsStatus, ...]] = get_args(ResultsStatus)
#: Why a batch invocation stopped: a case reason, or ``incomplete`` (cases left over).
StopReason = Literal[Reason, "incomplete"]
STOP_REASONS: Final[tuple[StopReason, ...]] = get_args(StopReason)
#: What a case's scoring could rely on.
Evidence = Literal["diagnostics", "legacy_trace", "none"]
#: Where a version's executable source stands (see :class:`ResolvedSource`).
SourceState = Literal["finalized", "captured", "capture_pending"]
SOURCE_STATES: Final[tuple[SourceState, ...]] = get_args(SourceState)

PANEL_CLAIMS: Final[Mapping[str, str]] = {
    "baseline": (
        "Baseline screen: frozen v1 with Typesafe on Simple64 against Terran, Protoss and "
        "Zerg at difficulties 3 and 4, seed 11. Reveals failure modes; not a win-rate."
    ),
    "heldout-a": (
        "On Simple64 against MediumHard built-in opponents, v2 wins more of the specified "
        "held-out games than frozen v1, without a race-wide regression (seed 101 half)."
    ),
    "heldout-b": (
        "On Simple64 against MediumHard built-in opponents, v2 wins more of the specified "
        "held-out games than frozen v1, without a race-wide regression (seed 202 half)."
    ),
    "attribution": (
        "Attribution: the six v2 held-out cases with the scripted provider, separating "
        "whole-player improvement from evidence for hosted decisions."
    ),
    "staging": (
        "Staging substrate check: one scripted v1 match through the benchmark path. "
        "Not performance evidence."
    ),
}

_VERSION_RE: Final = re.compile(r"\Av[1-9][0-9]{0,3}\Z")
_FINGERPRINT_RE: Final = re.compile(r"\A[0-9a-f]{64}\Z")
_SNAPSHOT_NAME_RE: Final = re.compile(r"\Av[1-9][0-9]{0,3}-[0-9a-f]{16}\Z")
#: Names that look like credentials: never captured, and their presence stops a capture.
_SECRET_NAME_RE: Final = re.compile(
    r"(\A\.env)|secret|credential|password|apikey|api_key|\.dpapi\Z|\.pem\Z|\.key\Z|\.pfx\Z",
    re.IGNORECASE,
)
#: Files every version's runtime source set contains besides its package and ``jev``.
SHARED_SOURCE_FILES: Final = (
    "bots/__init__.py",
    "bots/jev/__init__.py",
    "src/orchestrator/__init__.py",
    "src/orchestrator/paths.py",
)
CONFIG_FILES: Final = ("pyproject.toml", "uv.lock")
RUNTIME_PACKAGE: Final = "src/jev"
#: The policy package files captured besides the policy file its manifest names
#: (an explicit allowlist: nothing else in the package is ever copied).
PACKAGE_FILES: Final = ("__init__.py", "__main__.py", "manifest.json")
_MAX_SOURCE_FILE_BYTES: Final = 16 * 1024 * 1024
_MAX_DIR_ENTRIES: Final = 512
#: Entries (files and folders) a snapshot may hold; more is rejected, not truncated.
_MAX_SNAPSHOT_ENTRIES: Final = 2048
_MAX_RECORD_BYTES: Final = 4 * 1024 * 1024
#: Largest list any benchmark record holds (cases, files, invocations).
_MAX_RECORD_ITEMS: Final = 10_000
#: The service spend a decision record carries (summed per case over attempts).
_SPEND_FIELDS: Final = ("calls", "input_tokens", "output_tokens")
_MAX_STDOUT_TAIL_BYTES: Final = 64 * 1024
_FINGERPRINT_DOMAIN: Final = b"jev-benchmark-source-v1\n"
_BATCH_FINGERPRINT_DOMAIN: Final = b"jev-benchmark-batch-v1\n"
#: The runner's one-line report (``jev.runner._report``).
_SUMMARY_RE: Final = re.compile(
    r"^jev match (?P<status>starting|running|finished|stopped|failed): "
    r"result=(?P<result>win|loss|draw|timeout|None) run_id=(?P<run_id>[0-9a-f]{32}) ",
    re.MULTILINE,
)
#: Environment entries a child never inherits: they could redirect its imports.
_DROPPED_ENV: Final = frozenset(
    {
        "PYTHONPATH",
        "PYTHONHOME",
        "PYTHONSTARTUP",
        "PYTHONSAFEPATH",
        "PYTHONINSPECT",
        "PYTHONUSERBASE",
        "PYTHONEXECUTABLE",
        "PYTHONPYCACHEPREFIX",
    }
)
#: The probe's question state: deliberately tiny (plan D6 "tiny availability probe").
PROBE_STATE: Final[Mapping[str, JsonValue]] = {
    "purpose": "benchmark model availability probe",
    "game_seconds": 0,
}
PROBE_CHOICES: Final = ("attack", "regroup")


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class BenchmarkError(Exception):
    """A benchmark cannot proceed; ``code`` is a stable :data:`BenchmarkErrorCode`.

    ``usage`` exits 2; every other code exits 1 (the operator guide lists them).
    """

    def __init__(self, code: BenchmarkErrorCode, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = render_text(message)


class VersionUnavailable(BenchmarkError):
    """A panel names a version that is not packaged (``version_not_packaged``), or
    not frozen when it must be (``candidate_not_frozen``)."""

    def __init__(
        self,
        message: str,
        code: Literal["version_not_packaged", "candidate_not_frozen"] = "version_not_packaged",
    ) -> None:
        super().__init__(code, message)


class ProvenanceMismatch(BenchmarkError):
    """Recorded source, model or options disagree with what they must be."""

    def __init__(self, message: str) -> None:
        super().__init__("provenance_mismatch", message)


class CorruptRecord(BenchmarkError):
    """A benchmark record on disk is malformed."""

    def __init__(self, message: str) -> None:
        super().__init__("corrupt_evidence", message)


class LockHeld(BenchmarkError):
    """Another process owns the batch (or its lock cannot be verified)."""

    def __init__(self, message: str) -> None:
        super().__init__("lock_held", message)


# ---------------------------------------------------------------------------
# Limits and cases
# ---------------------------------------------------------------------------

#: Plan D6 mandatory defaults; a run may lower each, never raise it.
LIMIT_CEILINGS: Final[Mapping[str, int]] = {
    "max_games": 6,
    "invocation_wall_seconds": 7200,
    "invocation_requests": 2700,
    "match_game_seconds": 900,
    "match_wall_seconds": 1200,
    "match_requests": 450,
}


@dataclass(frozen=True)
class BenchmarkLimits:
    """Per-invocation and per-match limits (plan D6); see :data:`LIMIT_CEILINGS`."""

    max_games: int = LIMIT_CEILINGS["max_games"]
    invocation_wall_seconds: int = LIMIT_CEILINGS["invocation_wall_seconds"]
    invocation_requests: int = LIMIT_CEILINGS["invocation_requests"]
    match_game_seconds: int = LIMIT_CEILINGS["match_game_seconds"]
    match_wall_seconds: int = LIMIT_CEILINGS["match_wall_seconds"]
    match_requests: int = LIMIT_CEILINGS["match_requests"]

    def __post_init__(self) -> None:
        for name, ceiling in LIMIT_CEILINGS.items():
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                raise ValueError(f"{name} must be a positive integer, got {safe_repr(value)}")
            if value > ceiling:
                raise ValueError(
                    f"{name} may only be lowered: {value} exceeds the mandatory limit {ceiling}"
                )
        floor = LAUNCH_ALLOWANCE_SECONDS + MIN_CHILD_WALL_SECONDS
        if self.match_wall_seconds < floor:
            raise ValueError(
                f"match_wall_seconds must be at least {floor} (SC2 launch allowance "
                f"{LAUNCH_ALLOWANCE_SECONDS} plus {MIN_CHILD_WALL_SECONDS} seconds of play)"
            )

    @property
    def child_wall_seconds(self) -> int:
        """The child's ``--max-wall-seconds``: the match bound minus the launch allowance."""
        return self.match_wall_seconds - LAUNCH_ALLOWANCE_SECONDS

    def to_dict(self) -> dict[str, JsonValue]:
        record: dict[str, JsonValue] = {name: getattr(self, name) for name in LIMIT_CEILINGS}
        record["child_max_wall_seconds"] = self.child_wall_seconds
        record["launch_allowance_seconds"] = LAUNCH_ALLOWANCE_SECONDS
        return record

    @classmethod
    def from_dict(cls, value: object) -> BenchmarkLimits:
        doc = _Doc("manifest limits", value, cls().to_dict())
        limits = cls(**{name: doc.integer(name) for name in LIMIT_CEILINGS})
        if doc.integer("child_max_wall_seconds") != limits.child_wall_seconds:
            raise CorruptRecord("manifest limits disagree with the launch allowance")
        return limits


@dataclass(frozen=True)
class CaseSpec:
    """One planned match: identity plus the options the manifest fixes for it."""

    version: str
    provider: Provider
    map_name: str
    race: str
    difficulty: int
    seed: int
    realtime: bool

    @property
    def case_id(self) -> str:
        """Plan section 5: ``version-provider-map-race-difficulty-seed`` (lowercase)."""
        parts = (self.version, self.provider, self.map_name, self.race, self.difficulty, self.seed)
        return "-".join(str(part) for part in parts).lower()

    @property
    def hosted(self) -> bool:
        return self.provider == "typesafe"

    @property
    def entrypoint(self) -> str:
        return f"bots.jev.{self.version}"

    def match_options(self, limits: BenchmarkLimits, model: str) -> MatchOptions:
        """The exact options the child receives (every one explicit on its command line)."""
        return MatchOptions(
            map_name=self.map_name,
            opponent_race=self.race,
            difficulty=self.difficulty,
            seed=self.seed,
            max_game_seconds=limits.match_game_seconds,
            max_wall_seconds=limits.child_wall_seconds,
            realtime=self.realtime,
            decision_provider=self.provider,
            decision_model=model,
            decision_max_requests=limits.match_requests,
        )

    def to_dict(self) -> dict[str, JsonValue]:
        return {
            "case_id": self.case_id,
            "version": self.version,
            "provider": self.provider,
            "map": self.map_name,
            "race": self.race,
            "difficulty": self.difficulty,
            "seed": self.seed,
            "realtime": self.realtime,
        }

    @classmethod
    def from_dict(cls, value: object, what: str) -> CaseSpec:
        doc = _Doc(what, value, _CASE_SPEC_KEYS)
        spec = cls(
            version=doc.text("version"),
            provider=doc.choice("provider", PROVIDERS),
            map_name=doc.text("map"),
            race=doc.text("race"),
            difficulty=doc.integer("difficulty"),
            seed=doc.integer("seed"),
            realtime=doc.flag("realtime"),
        )
        if doc.text("case_id") != spec.case_id:
            raise CorruptRecord(f"{what} case_id does not match its fields")
        return spec


_CASE_SPEC_KEYS: Final = tuple(CaseSpec("v1", "scripted", MAP, "Terran", 1, 1, False).to_dict())


def panel_cases(panel: str) -> tuple[CaseSpec, ...]:
    """The exact, ordered cases of ``panel`` (plan D6); ValueError for an unknown panel."""
    if panel == "baseline":
        return tuple(
            CaseSpec("v1", "typesafe", MAP, race, difficulty, 11, True)
            for difficulty in (3, 4)
            for race in RACES
        )
    if panel in ("heldout-a", "heldout-b"):
        seed, first = (101, 0) if panel == "heldout-a" else (202, 1)
        cases: list[CaseSpec] = []
        for index, race in enumerate(RACES):
            order = ("v1", "v2") if (index + first) % 2 == 0 else ("v2", "v1")
            cases.extend(CaseSpec(v, "typesafe", MAP, race, 4, seed, True) for v in order)
        return tuple(cases)
    if panel == "attribution":
        return tuple(
            CaseSpec("v2", "scripted", MAP, race, 4, seed, True)
            for seed in (101, 202)
            for race in RACES
        )
    if panel == "staging":
        return (CaseSpec("v1", "scripted", MAP, "Terran", 1, 1, False),)
    raise ValueError(f"unknown panel {safe_repr(panel)}; expected one of {', '.join(PANELS)}")


def panel_versions(cases: Sequence[CaseSpec]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(case.version for case in cases))


# ---------------------------------------------------------------------------
# Strict record access
# ---------------------------------------------------------------------------


class _Doc:
    """Typed access to one stored JSON object; every defect is a :class:`CorruptRecord`.

    It mirrors the strictness of the run-record reader in :mod:`jev.telemetry`
    (exact key sets, bounded counts and lists, Literal choices). That reader is
    private to the run-evidence boundary and raises the run's own ``corrupt_run``
    error, which the dashboard API maps to HTTP 503, so it is not reused for
    benchmark records.
    """

    def __init__(self, what: str, value: object, keys: Iterable[str] | None = None) -> None:
        if not isinstance(value, dict):
            raise CorruptRecord(f"{what} is not a JSON object")
        self.what = what
        self.value: dict[str, object] = value
        if keys is not None:
            self.exact(keys)

    def exact(self, keys: Iterable[str]) -> None:
        """The object must hold exactly ``keys`` (no missing, no unexpected field)."""
        expected = frozenset(keys)
        if self.value.keys() != expected:
            missing = sorted(expected - self.value.keys())[:5]
            extra = sorted(self.value.keys() - expected)[:5]
            raise CorruptRecord(
                f"{self.what} has missing or unexpected fields (missing: {missing}, "
                f"unexpected: {[safe_repr(k) for k in extra]})"
            )

    def _get(self, name: str) -> object:
        if name not in self.value:
            raise CorruptRecord(f"{self.what} is missing {name!r}")
        return self.value[name]

    def bad(self, name: str, expected: str) -> CorruptRecord:
        return CorruptRecord(f"{self.what} field {name!r} is not {expected}")

    def text(self, name: str) -> str:
        value = self._get(name)
        if not isinstance(value, str):
            raise self.bad(name, "a string")
        return value

    def optional_text(self, name: str) -> str | None:
        return None if self._get(name) is None else self.text(name)

    def integer(self, name: str) -> int:
        value = self._get(name)
        if isinstance(value, bool) or not isinstance(value, int):
            raise self.bad(name, "an integer")
        return value

    def count(self, name: str) -> int:
        value = self.integer(name)
        if not 0 <= value <= MAX_ABS_NUMBER:
            raise self.bad(name, "a non-negative integer")
        return value

    def choice[T: str](self, name: str, allowed: tuple[T, ...]) -> T:
        value = self._get(name)
        for option in allowed:
            if value == option:
                return option
        raise self.bad(name, f"one of {', '.join(allowed)}")

    def optional_choice[T: str](self, name: str, allowed: tuple[T, ...]) -> T | None:
        return None if self._get(name) is None else self.choice(name, allowed)

    def number(self, name: str) -> float:
        value = self._get(name)
        if isinstance(value, bool) or not isinstance(value, int | float):
            raise self.bad(name, "a number")
        return float(value)

    def flag(self, name: str) -> bool:
        value = self._get(name)
        if not isinstance(value, bool):
            raise self.bad(name, "a boolean")
        return value

    def mapping(self, name: str) -> dict[str, object]:
        value = self._get(name)
        if not isinstance(value, dict):
            raise self.bad(name, "a JSON object")
        return value

    def items(self, name: str, limit: int = _MAX_RECORD_ITEMS) -> list[object]:
        value = self._get(name)
        if not isinstance(value, list) or len(value) > limit:
            raise self.bad(name, f"a list of at most {limit} items")
        return value

    def matching(self, name: str, pattern: re.Pattern[str], expected: str) -> str:
        value = self.text(name)
        if not full_match(pattern, value):
            raise self.bad(name, expected)
        return value


def _encode(document: Mapping[str, JsonValue]) -> bytes:
    text = json.dumps(document, ensure_ascii=True, allow_nan=False, indent=2, sort_keys=False)
    return (text + "\n").encode("ascii")


def _read_document(path: Path, what: str, max_bytes: int = _MAX_RECORD_BYTES) -> dict[str, Any]:
    """A strict JSON object from ``path`` (a regular file, at most ``max_bytes``)."""
    try:
        if path.is_symlink() or not path.is_file():
            raise CorruptRecord(f"{what} is missing or not a regular file")
        with path.open("rb") as handle:
            data = handle.read(max_bytes + 1)
    except OSError as exc:
        raise CorruptRecord(f"{what} cannot be read: {safe_exception_text(exc)}") from exc
    if len(data) > max_bytes:
        raise CorruptRecord(f"{what} is larger than {max_bytes} bytes")
    try:
        return parse_json_document(data, what=what, max_bytes=max_bytes)
    except PolicyError as exc:
        raise CorruptRecord(f"{what} is not a strict JSON object") from exc


def _write_document(files: EvidenceFiles, path: Path, document: Mapping[str, JsonValue]) -> None:
    """Atomically replace ``path`` (same-directory temporary file, sharing retries)."""
    files.write(path, _encode(document))


def _exclusive_write(path: Path, data: bytes) -> bool:
    """Create ``path`` holding ``data``; False when it already exists (never replaced).

    The bytes are written to a temporary sibling and hard-linked into place, so the
    record appears complete or not at all. A file system without hard links falls
    back to an exclusive create (still never replacing an existing record).
    """
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    temporary.write_bytes(data)
    try:
        os.link(temporary, path)
    except FileExistsError:
        return False
    except OSError:  # no hard links here
        try:
            descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
        except FileExistsError:
            return False
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(data)
    finally:
        temporary.unlink(missing_ok=True)
    return True


def default_benchmark_root() -> Path:
    """``<repository>/data/jev/benchmarks`` (git-ignored, like the run root)."""
    return repository_root().joinpath(*BENCHMARK_ROOT_PARTS)


# ---------------------------------------------------------------------------
# Frozen source capture (plan D1)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SourceFile:
    path: str  # project-relative, POSIX separators
    sha256: str
    size: int

    def to_dict(self) -> dict[str, JsonValue]:
        return {"path": self.path, "sha256": self.sha256, "size": self.size}


@dataclass(frozen=True)
class SourceSnapshot:
    """A version's runtime source set and its fingerprint.

    ``directory`` is the snapshot directory, or None for a source set that has only
    been fingerprinted in place (a dry run never copies anything).
    """

    version: str
    fingerprint: str
    files: tuple[SourceFile, ...]
    directory: Path | None
    source_commit: str | None
    captured_at: str | None

    @property
    def name(self) -> str:
        """The snapshot directory name: ``<version>-<first 16 fingerprint digits>``."""
        return f"{self.version}-{self.fingerprint[:16]}"


def _check_version(version: str) -> None:
    if not full_match(_VERSION_RE, version):
        raise ValueError(f"not a Jev version name: {safe_repr(version)}")


def _safe_relative(path: str) -> bool:
    if not path or "\\" in path or path.startswith("/") or ":" in path:
        return False
    parts = PurePosixPath(path).parts
    return all(part not in ("", ".", "..") for part in parts)


def _listed(folder: Path) -> list[Path]:
    try:
        entries = list(itertools.islice(folder.iterdir(), _MAX_DIR_ENTRIES + 1))
    except OSError as exc:
        raise VersionUnavailable(f"cannot list {folder.name}: {safe_exception_text(exc)}") from exc
    if len(entries) > _MAX_DIR_ENTRIES:  # never capture a silently truncated listing
        raise ProvenanceMismatch(f"{folder.name} holds more than {_MAX_DIR_ENTRIES} entries")
    return sorted(entries)


def _package_dir(version: str) -> str:
    return f"bots/jev/{version}"


def runtime_source_paths(source_root: Path, version: str) -> tuple[str, ...]:
    """The sorted project-relative runtime source paths of ``version`` (module docstring).

    The set is an explicit allowlist: the ``jev`` runtime package's modules, the
    shared files in :data:`SHARED_SOURCE_FILES` and :data:`CONFIG_FILES`, and from
    the policy package only :data:`PACKAGE_FILES` plus the policy file its
    manifest names. Raises :class:`VersionUnavailable` when the version is not
    packaged here or a required file is missing, and :class:`ProvenanceMismatch`
    when a selected file is a link, its name looks like a credential, or the
    package's policy is invalid.
    """
    _check_version(version)
    package = source_root / _package_dir(version)
    if not (package / "manifest.json").is_file():
        raise VersionUnavailable(
            f"bots.jev.{version} is not packaged ({_package_dir(version)}/manifest.json is "
            "missing); a panel needing it cannot resolve, and no other version or the "
            "current runtime is substituted"
        )
    policy_file = _load_policy(source_root, version).manifest.policy_file
    if PurePosixPath(policy_file).name != policy_file or "\\" in policy_file:
        raise ProvenanceMismatch(f"bots.jev.{version} names an unsafe policy file")
    paths = set(SHARED_SOURCE_FILES) | set(CONFIG_FILES)
    for entry in _listed(source_root / RUNTIME_PACKAGE):
        if entry.suffix == ".py" and entry.is_file():
            paths.add(f"{RUNTIME_PACKAGE}/{entry.name}")
    paths.update(f"{_package_dir(version)}/{name}" for name in (*PACKAGE_FILES, policy_file))
    for relative in sorted(paths):
        path = source_root / relative
        if _SECRET_NAME_RE.search(PurePosixPath(relative).name):
            raise ProvenanceMismatch(f"refusing to capture {relative}: it looks like a credential")
        if path.is_symlink():
            raise ProvenanceMismatch(f"refusing to capture {relative}: it is a link")
        if not path.is_file():
            raise VersionUnavailable(f"runtime source file {relative} is missing")
    return tuple(sorted(paths))


def _read_source(path: Path, relative: str) -> bytes:
    with path.open("rb") as handle:
        data = handle.read(_MAX_SOURCE_FILE_BYTES + 1)
    if len(data) > _MAX_SOURCE_FILE_BYTES:
        raise ProvenanceMismatch(f"{relative} is larger than {_MAX_SOURCE_FILE_BYTES} bytes")
    return data


def source_fingerprint(entries: Sequence[tuple[str, bytes]]) -> tuple[str, tuple[SourceFile, ...]]:
    """THE source fingerprint: SHA-256 over sorted relative paths, sizes and file digests."""
    digest = hashlib.sha256(_FINGERPRINT_DOMAIN)
    files: list[SourceFile] = []
    for relative, data in sorted(entries):
        file_digest = hashlib.sha256(data).hexdigest()
        digest.update(f"{relative}\0{len(data)}\0{file_digest}\n".encode())
        files.append(SourceFile(relative, file_digest, len(data)))
    return digest.hexdigest(), tuple(files)


def _batch_fingerprint(sources: Mapping[str, str]) -> str:
    digest = hashlib.sha256(_BATCH_FINGERPRINT_DOMAIN)
    for version in sorted(sources):
        digest.update(f"{version}:{sources[version]}\n".encode("ascii"))
    return digest.hexdigest()


def fingerprint_source(source_root: Path, version: str) -> SourceSnapshot:
    """Fingerprint ``version``'s runtime source set in place (nothing is copied)."""
    paths = runtime_source_paths(source_root, version)
    entries = [(relative, _read_source(source_root / relative, relative)) for relative in paths]
    fingerprint, files = source_fingerprint(entries)
    return SourceSnapshot(
        version=version,
        fingerprint=fingerprint,
        files=files,
        directory=None,
        source_commit=read_source_commit(source_root),
        captured_at=None,
    )


def capture_source(
    source_root: Path, version: str, baselines_dir: Path, *, now: Callable[[], float] = time.time
) -> SourceSnapshot:
    """Copy ``version``'s exact runtime source set into ``baselines_dir`` and hash it.

    The copy is assembled in a temporary sibling directory and renamed into
    ``<version>-<fingerprint prefix>``; an existing snapshot of the same fingerprint
    is verified and reused (captures are content addressed). Returns the verified
    snapshot; raises :class:`ProvenanceMismatch` if the files changed while being
    copied or an existing snapshot does not verify.
    """
    paths = runtime_source_paths(source_root, version)
    entries = [(relative, _read_source(source_root / relative, relative)) for relative in paths]
    fingerprint, files = source_fingerprint(entries)
    final = baselines_dir / f"{version}-{fingerprint[:16]}"
    if final.exists():
        return _expect_fingerprint(verify_snapshot(final), fingerprint)
    baselines_dir.mkdir(parents=True, exist_ok=True)
    staging = baselines_dir / f".capture-{uuid.uuid4().hex}"
    try:
        for relative, data in entries:
            target = staging.joinpath(*PurePosixPath(relative).parts)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
        record: dict[str, JsonValue] = {
            "schema_version": SCHEMA_VERSION,
            "kind": _SNAPSHOT_KIND,
            "version": version,
            "entrypoint": f"bots.jev.{version}",
            "fingerprint": fingerprint,
            "files": [item.to_dict() for item in files],
            "source_commit": read_source_commit(source_root),
            "captured_at": utc_timestamp(now()),
        }
        assert tuple(record) == _SNAPSHOT_KEYS  # the reader requires exactly these
        (staging / SNAPSHOT_RECORD).write_bytes(_encode(record))
        try:
            staging.rename(final)
        except OSError:
            if not final.exists():
                raise
    finally:
        _remove_tree(staging)
    return _expect_fingerprint(verify_snapshot(final), fingerprint)


def _expect_fingerprint(snapshot: SourceSnapshot, fingerprint: str) -> SourceSnapshot:
    if snapshot.fingerprint != fingerprint:
        raise ProvenanceMismatch(
            f"snapshot {snapshot.name} has fingerprint {snapshot.fingerprint}, expected "
            f"{fingerprint}"
        )
    return snapshot


def _remove_tree(folder: Path) -> None:
    """Remove a capture's temporary directory (files first, then folders)."""
    if not folder.exists():
        return
    for path in sorted(folder.rglob("*"), key=lambda p: len(p.parts), reverse=True):
        with contextlib.suppress(OSError):
            path.unlink() if path.is_file() or path.is_symlink() else path.rmdir()
    with contextlib.suppress(OSError):
        folder.rmdir()


_SNAPSHOT_KIND: Final = "jev_source_snapshot"
#: ``snapshot.json``'s fields, in order (the capture writes exactly these).
_SNAPSHOT_KEYS: Final = (
    "schema_version",
    "kind",
    "version",
    "entrypoint",
    "fingerprint",
    "files",
    "source_commit",
    "captured_at",
)
_POINTER_KEYS: Final = ("schema_version", "version", "fingerprint", "snapshot", "finalized_at")


def _snapshot_tree(directory: Path) -> tuple[set[str], set[str]]:
    """(files, directories) under a snapshot, as relative POSIX paths, its record aside.

    Every entry is counted, and a tree holding more than
    :data:`_MAX_SNAPSHOT_ENTRIES` is rejected (never silently truncated). A link, a
    special file or a bytecode cache is rejected outright: children never write a
    cache (bytecode writing is off and caches go to a fresh per-attempt prefix), and
    CPython would execute a matching ``__pycache__`` entry instead of the hashed
    source.
    """
    files: set[str] = set()
    folders: set[str] = set()
    count = 0
    for root, dirnames, filenames in os.walk(directory, followlinks=False):
        for name, is_folder in [(d, True) for d in dirnames] + [(f, False) for f in filenames]:
            count += 1
            path = Path(root) / name
            relative = path.relative_to(directory).as_posix()
            if count > _MAX_SNAPSHOT_ENTRIES:
                raise ProvenanceMismatch(
                    f"snapshot {directory.name} holds more than {_MAX_SNAPSHOT_ENTRIES} "
                    f"entries: {relative}"
                )
            if "__pycache__" in relative.split("/"):
                raise ProvenanceMismatch(
                    f"snapshot {directory.name} contains a bytecode cache: {relative}"
                )
            if path.is_symlink():
                raise ProvenanceMismatch(f"snapshot {directory.name} contains a link: {relative}")
            if is_folder:
                folders.add(relative)
            elif not path.is_file():
                raise ProvenanceMismatch(
                    f"snapshot {directory.name} holds a special file: {relative}"
                )
            elif relative != SNAPSHOT_RECORD:
                files.add(relative)
    return files, folders


def verify_snapshot(directory: Path) -> SourceSnapshot:
    """Re-hash a snapshot directory against its record; :class:`ProvenanceMismatch` if not.

    The directory must hold exactly the recorded files and no bytecode cache, each
    with its recorded digest; the recomputed fingerprint must equal the recorded one
    and the directory name; and the set must be a complete runtime source set.
    """
    if not full_match(_SNAPSHOT_NAME_RE, directory.name) or not directory.is_dir():
        raise ProvenanceMismatch(f"{safe_repr(directory.name)} is not a source snapshot")
    try:
        doc = _Doc(
            SNAPSHOT_RECORD,
            _read_document(directory / SNAPSHOT_RECORD, SNAPSHOT_RECORD),
            _SNAPSHOT_KEYS,
        )
        if doc.integer("schema_version") != SCHEMA_VERSION or doc.text("kind") != _SNAPSHOT_KIND:
            raise CorruptRecord(f"{SNAPSHOT_RECORD} has an unsupported schema")
        version = doc.text("version")
        _check_version(version)
        fingerprint = doc.matching("fingerprint", _FINGERPRINT_RE, "a SHA-256 hex digest")
        listed: dict[str, tuple[str, int]] = {}
        for index, item in enumerate(doc.items("files")):
            entry = _Doc(f"{SNAPSHOT_RECORD} files[{index}]", item, ("path", "sha256", "size"))
            relative = entry.text("path")
            if not _safe_relative(relative) or relative in listed:
                raise CorruptRecord(f"{SNAPSHOT_RECORD} lists an unsafe or repeated path")
            listed[relative] = (
                entry.matching("sha256", _FINGERPRINT_RE, "a digest"),
                entry.count("size"),
            )
        commit = doc.optional_text("source_commit")
        if commit is not None and not full_match(COMMIT_RE, commit):
            raise CorruptRecord(f"{SNAPSHOT_RECORD} source_commit is not a commit")
        captured_at = doc.text("captured_at")
    except (CorruptRecord, ValueError) as exc:
        raise ProvenanceMismatch(f"snapshot {directory.name} record: {exc}") from exc
    if directory.name != f"{version}-{fingerprint[:16]}":
        raise ProvenanceMismatch(f"snapshot {directory.name} does not match its fingerprint")
    present, folders = _snapshot_tree(directory)
    if present != set(listed):
        extra = sorted(present - set(listed))[:5]
        missing = sorted(set(listed) - present)[:5]
        raise ProvenanceMismatch(
            f"snapshot {directory.name} files changed (extra: {extra}, missing: {missing})"
        )
    expected_folders = {
        "/".join(parts[:depth])
        for parts in (relative.split("/") for relative in listed)
        for depth in range(1, len(parts))
    }
    if folders != expected_folders:
        extra = sorted(folders - expected_folders)[:5]
        raise ProvenanceMismatch(f"snapshot {directory.name} has unexpected folders: {extra}")
    entries = []
    for relative, (expected, size) in listed.items():
        data = _read_source(directory.joinpath(*PurePosixPath(relative).parts), relative)
        if len(data) != size or hashlib.sha256(data).hexdigest() != expected:
            raise ProvenanceMismatch(f"snapshot {directory.name}: {relative} was modified")
        entries.append((relative, data))
    recomputed, files = source_fingerprint(entries)
    if recomputed != fingerprint:
        raise ProvenanceMismatch(f"snapshot {directory.name} fingerprint does not verify")
    required = {
        f"{RUNTIME_PACKAGE}/runner.py",
        f"{_package_dir(version)}/manifest.json",
        f"{_package_dir(version)}/__main__.py",
        *SHARED_SOURCE_FILES,
        *CONFIG_FILES,
    }
    if not required <= set(listed):
        raise ProvenanceMismatch(f"snapshot {directory.name} is not a complete runtime source")
    return SourceSnapshot(
        version=version,
        fingerprint=fingerprint,
        files=files,
        directory=directory,
        source_commit=commit,
        captured_at=captured_at,
    )


def _baseline_pointer(baselines_dir: Path, version: str) -> Path:
    return baselines_dir / f"{version}.baseline.json"


def read_finalized(baselines_dir: Path, version: str) -> str | None:
    """The fingerprint finalized as ``version``'s baseline, or None if none is."""
    _check_version(version)
    pointer = _baseline_pointer(baselines_dir, version)
    if not pointer.exists():
        return None
    doc = _Doc(pointer.name, _read_document(pointer, pointer.name), _POINTER_KEYS)
    if doc.integer("schema_version") != SCHEMA_VERSION or doc.text("version") != version:
        raise CorruptRecord(f"{pointer.name} does not describe {version}")
    fingerprint = doc.matching("fingerprint", _FINGERPRINT_RE, "a SHA-256 hex digest")
    if doc.text("snapshot") != f"{version}-{fingerprint[:16]}":
        raise CorruptRecord(f"{pointer.name} names another snapshot")
    return fingerprint


def finalize_baseline(
    baselines_dir: Path, snapshot: SourceSnapshot, *, now: Callable[[], float] = time.time
) -> None:
    """Record ``snapshot`` as its version's frozen baseline, once (plan D1).

    Idempotent for the same fingerprint; a different already-finalized baseline is
    a :class:`ProvenanceMismatch` -- completed baseline cases are never rebased.
    """
    if snapshot.directory is None:
        raise ValueError("only a captured snapshot can be finalized")
    record: dict[str, JsonValue] = {
        "schema_version": SCHEMA_VERSION,
        "version": snapshot.version,
        "fingerprint": snapshot.fingerprint,
        "snapshot": snapshot.name,
        "finalized_at": utc_timestamp(now()),
    }
    assert tuple(record) == _POINTER_KEYS  # the reader requires exactly these
    if _exclusive_write(_baseline_pointer(baselines_dir, snapshot.version), _encode(record)):
        return
    existing = read_finalized(baselines_dir, snapshot.version)
    if existing != snapshot.fingerprint:
        raise ProvenanceMismatch(
            f"{snapshot.version} baseline is already finalized with fingerprint {existing}; "
            "it is never replaced"
        )


@dataclass(frozen=True)
class ResolvedSource:
    """A version a batch plays, with the exact source it runs from.

    ``state``: ``finalized`` (the frozen baseline), ``captured`` (a snapshot of the
    current source, not yet finalized) or ``capture_pending`` (dry run: what a real
    run would capture; nothing was copied).
    """

    version: str
    entrypoint: str
    policy_hash: str
    snapshot: SourceSnapshot
    state: SourceState

    def to_dict(self, baselines_dir: Path) -> dict[str, JsonValue]:
        directory = self.snapshot.directory or baselines_dir / self.snapshot.name
        return {
            "version": self.version,
            "entrypoint": self.entrypoint,
            "policy_hash": self.policy_hash,
            "fingerprint": self.snapshot.fingerprint,
            "snapshot": self.snapshot.name,
            "snapshot_dir": str(directory),
            "state": self.state,
            "files": len(self.snapshot.files),
            "source_commit": self.snapshot.source_commit,
        }


def _load_policy(root: Path, version: str) -> PolicyBundle:
    try:
        return load_policy_bundle(
            root / _package_dir(version), expected_entrypoint=f"bots.jev.{version}"
        )
    except PolicyError as exc:
        raise ProvenanceMismatch(f"bots.jev.{version} policy is invalid: {exc}") from exc


def resolve_sources(
    panel: str,
    *,
    source_root: Path,
    baselines_dir: Path,
    capture: bool,
    now: Callable[[], float] = time.time,
) -> dict[str, ResolvedSource]:
    """Resolve every version ``panel`` plays to exact frozen source (module docstring).

    ``capture`` False (dry run) copies nothing: a source that would be captured is
    fingerprinted in place and reported ``capture_pending``. Raises
    :class:`VersionUnavailable` naming every version that cannot resolve.
    """
    resolved: dict[str, ResolvedSource] = {}
    problems: list[VersionUnavailable] = []
    for version in panel_versions(panel_cases(panel)):
        try:
            resolved[version] = _resolve_one(
                panel, version, source_root, baselines_dir, capture, now
            )
        except VersionUnavailable as exc:
            problems.append(exc)
    if problems:
        unpackaged = any(p.code == "version_not_packaged" for p in problems)
        raise VersionUnavailable(
            "; ".join(p.message for p in problems),
            "version_not_packaged" if unpackaged else "candidate_not_frozen",
        )
    return resolved


def _resolve_one(
    panel: str,
    version: str,
    source_root: Path,
    baselines_dir: Path,
    capture: bool,
    now: Callable[[], float],
) -> ResolvedSource:
    state: SourceState
    # A frozen source is used as frozen, whatever the live package now holds.
    finalized = None if panel == "staging" else read_finalized(baselines_dir, version)
    if finalized is None:
        runtime_source_paths(source_root, version)  # packaged here at all?
    if finalized is not None:
        snapshot = verify_snapshot(baselines_dir / f"{version}-{finalized[:16]}")
        _expect_fingerprint(snapshot, finalized)
        state = "finalized"
    elif panel in ("baseline", "staging"):  # staging always checks the current source
        if capture:
            snapshot = capture_source(source_root, version, baselines_dir, now=now)
            state = "captured"
        else:
            snapshot = fingerprint_source(source_root, version)
            state = "capture_pending"
    else:
        how = (
            "the --panel baseline preflight finalizes it"
            if version == BASELINE_VERSION
            else f"freeze it with --freeze-candidate {version}"
        )
        raise VersionUnavailable(
            f"bots.jev.{version} has no finalized frozen source; the {panel} panel only "
            f"plays frozen sources ({how})",
            "candidate_not_frozen",
        )
    root = snapshot.directory if snapshot.directory is not None else source_root
    bundle = _load_policy(root, version)
    return ResolvedSource(
        version=version,
        entrypoint=f"bots.jev.{version}",
        policy_hash=bundle.policy_hash,
        snapshot=snapshot,
        state=state,
    )


def freeze_candidate(
    source_root: Path, version: str, baselines_dir: Path, *, now: Callable[[], float] = time.time
) -> ResolvedSource:
    """Capture ``version``'s current source and finalize it as its frozen source (D1/D6).

    This is the candidate freeze the held-out and attribution panels require. The
    baseline version is finalized only by the ``--panel baseline`` preflight, never
    here. Finalizing is write-once: a different fingerprint already frozen for the
    version is a :class:`ProvenanceMismatch`.
    """
    _check_version(version)
    if version == BASELINE_VERSION:
        raise BenchmarkError(
            "usage", f"{version} is the baseline; the --panel baseline preflight freezes it"
        )
    snapshot = capture_source(source_root, version, baselines_dir, now=now)
    finalize_baseline(baselines_dir, snapshot, now=now)
    bundle = _load_policy(snapshot.directory or source_root, version)
    return ResolvedSource(version, f"bots.jev.{version}", bundle.policy_hash, snapshot, "finalized")


# ---------------------------------------------------------------------------
# Batch manifest, results, attempts and lock
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class BatchManifest:
    """The immutable batch definition (plan section 5 ``Benchmark manifest``)."""

    batch_id: str
    panel: Panel
    claim: str
    source_commit: str | None
    source_fingerprint: str
    sources: Mapping[str, Mapping[str, JsonValue]]
    policy_hashes: Mapping[str, str]
    requested_model: str
    expected_returned_model: str
    cases: tuple[CaseSpec, ...]
    limits: BenchmarkLimits
    created_at: str

    def to_dict(self) -> dict[str, JsonValue]:
        """The manifest document; its keys are exactly :data:`_MANIFEST_KEYS`."""
        return {
            "schema_version": SCHEMA_VERSION,
            "batch_id": self.batch_id,
            "panel": self.panel,
            "claim": self.claim,
            "source_commit": self.source_commit,
            "source_fingerprint": self.source_fingerprint,
            "sources": {v: dict(s) for v, s in sorted(self.sources.items())},
            "policy_hashes": dict(sorted(self.policy_hashes.items())),
            "requested_model": self.requested_model,
            "expected_returned_model": self.expected_returned_model,
            "cases": [case.to_dict() for case in self.cases],
            "limits": self.limits.to_dict(),
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, value: object) -> BatchManifest:
        doc = _Doc(MANIFEST_FILE, value, _MANIFEST_KEYS)
        if doc.integer("schema_version") != SCHEMA_VERSION:
            raise CorruptRecord(f"{MANIFEST_FILE} has an unsupported schema_version")
        batch_id = doc.text("batch_id")
        if not is_valid_run_id(batch_id):
            raise CorruptRecord(f"{MANIFEST_FILE} batch_id is not a UUID4 hex")
        sources: dict[str, Mapping[str, JsonValue]] = {}
        for version, source in doc.mapping("sources").items():
            entry = _Doc(f"{MANIFEST_FILE} sources.{version}", source, _SOURCE_ENTRY_KEYS)
            sources[version] = {
                "fingerprint": entry.matching("fingerprint", _FINGERPRINT_RE, "a digest"),
                "snapshot": entry.matching("snapshot", _SNAPSHOT_NAME_RE, "a snapshot name"),
                "entrypoint": entry.text("entrypoint"),
                "state": entry.choice("state", SOURCE_STATES),
                "files": entry.count("files"),
                "source_commit": entry.optional_text("source_commit"),
            }
        hashes = {
            version: _Doc(MANIFEST_FILE, {"h": h}).matching("h", POLICY_HASH_RE, "a hash")
            for version, h in doc.mapping("policy_hashes").items()
        }
        commit = doc.optional_text("source_commit")
        return cls(
            batch_id=batch_id,
            panel=doc.choice("panel", PANELS),
            claim=doc.text("claim"),
            source_commit=commit,
            source_fingerprint=doc.matching(
                "source_fingerprint", _FINGERPRINT_RE, "a SHA-256 hex digest"
            ),
            sources=sources,
            policy_hashes=hashes,
            requested_model=doc.text("requested_model"),
            expected_returned_model=doc.text("expected_returned_model"),
            cases=tuple(
                CaseSpec.from_dict(item, f"{MANIFEST_FILE} cases[{i}]")
                for i, item in enumerate(doc.items("cases"))
            ),
            limits=BenchmarkLimits.from_dict(doc.mapping("limits")),
            created_at=doc.text("created_at"),
        )


#: ``manifest.json``'s top-level fields and one ``sources`` entry's fields.
_MANIFEST_KEYS: Final = (
    "schema_version",
    "batch_id",
    "panel",
    "claim",
    "source_commit",
    "source_fingerprint",
    "sources",
    "policy_hashes",
    "requested_model",
    "expected_returned_model",
    "cases",
    "limits",
    "created_at",
)
_SOURCE_ENTRY_KEYS: Final = (
    "fingerprint",
    "snapshot",
    "entrypoint",
    "state",
    "files",
    "source_commit",
)


def build_manifest(
    panel: Panel,
    sources: Mapping[str, ResolvedSource],
    limits: BenchmarkLimits,
    *,
    batch_id: str,
    source_commit: str | None,
    created_at: str,
) -> BatchManifest:
    cases = panel_cases(panel)
    return BatchManifest(
        batch_id=batch_id,
        panel=panel,
        claim=PANEL_CLAIMS[panel],
        source_commit=source_commit,
        source_fingerprint=_batch_fingerprint(
            {v: s.snapshot.fingerprint for v, s in sources.items()}
        ),
        sources={
            v: {
                "fingerprint": s.snapshot.fingerprint,
                "snapshot": s.snapshot.name,
                "entrypoint": s.entrypoint,
                "state": s.state,
                "files": len(s.snapshot.files),
                "source_commit": s.snapshot.source_commit,
            }
            for v, s in sources.items()
        },
        policy_hashes={v: s.policy_hash for v, s in sources.items()},
        requested_model=PINNED_MODEL,
        expected_returned_model=EXPECTED_RETURNED_MODEL,
        cases=cases,
        limits=limits,
        created_at=created_at,
    )


@dataclass(frozen=True)
class CaseRecord:
    """One case's live record (plan section 5 ``Benchmark case``) in ``results.json``."""

    spec: CaseSpec
    status: CaseStatus = "pending"
    run_id: str | None = None
    result: CaseResult | None = None
    reason: Reason | None = None
    detail: str | None = None
    attempts: int = 0
    metrics: Mapping[str, JsonValue] = field(default_factory=dict)
    #: Service spend summed over every attempt with a decision record (a retry adds
    #: to it, never resets it), and the hosted attempts whose spend is unknown.
    spent: Mapping[str, int] = field(default_factory=lambda: dict.fromkeys(_SPEND_FIELDS, 0))
    unrecorded_attempts: int = 0
    #: The latest attempt's child process (pid and start time), once it started:
    #: a resume refuses to replay a case whose match may still be playing.
    child_pid: int | None = None
    child_started: float | None = None

    @property
    def counted_as_win(self) -> bool:
        return self.status == "complete" and self.result == "win"

    def to_dict(self) -> dict[str, JsonValue]:
        record = self.spec.to_dict()
        record.update(
            {
                "status": self.status,
                "run_id": self.run_id,
                "result": self.result,
                "reason": self.reason,
                "detail": self.detail,
                "attempts": self.attempts,
                "metrics": dict(self.metrics),
                "spent": dict(self.spent),
                "unrecorded_attempts": self.unrecorded_attempts,
                "child_pid": self.child_pid,
                "child_started": self.child_started,
            }
        )
        return record

    @classmethod
    def from_dict(cls, value: object, spec: CaseSpec, what: str) -> CaseRecord:
        doc = _Doc(what, value, CaseRecord(spec).to_dict())
        if CaseSpec.from_dict({k: doc.value[k] for k in _CASE_SPEC_KEYS}, what) != spec:
            raise CorruptRecord(f"{what} is not case {spec.case_id}")
        run_id = doc.optional_text("run_id")
        if run_id is not None and not is_valid_run_id(run_id):
            raise CorruptRecord(f"{what} run_id is not a UUID4 hex")
        started = doc.value["child_started"]
        return cls(
            spec=spec,
            status=doc.choice("status", CASE_STATUSES),
            run_id=run_id,
            result=doc.optional_choice("result", CASE_RESULTS),
            reason=doc.optional_choice("reason", REASON_PRIORITY),
            detail=doc.optional_text("detail"),
            attempts=doc.count("attempts"),
            metrics=_json_mapping(doc.mapping("metrics")),
            spent=_spend_from(_Doc(f"{what} spent", doc.mapping("spent"), _SPEND_FIELDS)),
            unrecorded_attempts=doc.count("unrecorded_attempts"),
            child_pid=None if doc.value["child_pid"] is None else doc.count("child_pid"),
            child_started=None if started is None else doc.number("child_started"),
        )


def _spend_from(doc: _Doc) -> dict[str, int]:
    return {name: doc.count(name) for name in _SPEND_FIELDS}


def _attempt_spend(metrics: Mapping[str, JsonValue]) -> dict[str, int] | None:
    """One attempt's recorded calls and tokens; None when it has no decision record."""
    decisions = metrics.get("decisions")
    if not isinstance(decisions, dict) or decisions.get("status") != "measured":
        return None
    spend: dict[str, int] = {}
    for name in _SPEND_FIELDS:
        value = decisions.get(name)
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            return None
        spend[name] = value
    return spend


def _json_mapping(value: Mapping[str, object]) -> dict[str, JsonValue]:
    """Re-type a parsed JSON object (``parse_json_document`` yields JSON data only)."""
    return {key: _json_value(item) for key, item in value.items()}


def _json_value(value: object) -> JsonValue:
    if value is None or isinstance(value, str | int | float | bool):
        return value
    if isinstance(value, list):
        return [_json_value(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _json_value(item) for key, item in value.items()}
    raise CorruptRecord("record holds a value that is not JSON data")


@dataclass(frozen=True)
class LockOwner:
    pid: int
    started: float | None
    host: str
    invocation_id: str
    created_at: str

    def to_dict(self) -> dict[str, JsonValue]:
        return {
            "schema_version": SCHEMA_VERSION,
            "pid": self.pid,
            "process_started": self.started,
            "host": self.host,
            "invocation_id": self.invocation_id,
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, value: object) -> LockOwner:
        doc = _Doc(LOCK_FILE, value, _LOCK_KEYS)
        started = doc.value["process_started"]
        return cls(
            pid=doc.count("pid"),
            started=None if started is None else doc.number("process_started"),
            host=doc.text("host"),
            invocation_id=doc.text("invocation_id"),
            created_at=doc.text("created_at"),
        )


_LOCK_KEYS: Final = tuple(LockOwner(0, None, "", "", "").to_dict())


class ProcessProbe(Protocol):
    """Process identity for the batch lock (injectable for tests)."""

    def current(self) -> tuple[int, float | None]:
        """This process: its pid and start time (None when unknown)."""
        ...

    def alive(self, pid: int, started: float | None) -> bool:
        """Whether that process is still running (a reused pid with another start is not)."""
        ...


if sys.platform == "win32":
    import ctypes
    from ctypes import wintypes

    _PROCESS_QUERY_LIMITED_INFORMATION: Final = 0x1000
    _STILL_ACTIVE: Final = 259
    _ERROR_ACCESS_DENIED: Final = 5

    def _process_state(pid: int) -> tuple[bool, float | None]:
        """(exists, start time in seconds since 1601) via the Win32 process API.

        Never signals the process (``os.kill`` on Windows terminates it).
        """
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel32.OpenProcess.restype = wintypes.HANDLE
        handle = kernel32.OpenProcess(_PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not handle:
            return ctypes.get_last_error() == _ERROR_ACCESS_DENIED, None
        try:
            code = wintypes.DWORD()
            if not kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
                return True, None
            if code.value != _STILL_ACTIVE:
                return False, None
            times = [wintypes.FILETIME() for _ in range(4)]
            if not kernel32.GetProcessTimes(handle, *(ctypes.byref(t) for t in times)):
                return True, None
            created = times[0]
            ticks = (int(created.dwHighDateTime) << 32) | int(created.dwLowDateTime)
            return True, ticks / 10_000_000
        finally:
            kernel32.CloseHandle(handle)

else:

    def _process_state(pid: int) -> tuple[bool, float | None]:
        """(exists, start time in clock ticks since boot when /proc has it)."""
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False, None
        except PermissionError:
            return True, None
        try:
            stat = Path(f"/proc/{pid}/stat").read_text(encoding="ascii", errors="replace")
            fields = stat.rsplit(")", 1)[1].split()
            return True, float(fields[19])
        except (OSError, IndexError, ValueError):
            return True, None


class SystemProcessProbe:
    """The production :class:`ProcessProbe` (Win32 API on Windows, signal 0 elsewhere)."""

    def current(self) -> tuple[int, float | None]:
        pid = os.getpid()
        return pid, _process_state(pid)[1]

    def alive(self, pid: int, started: float | None) -> bool:
        if isinstance(pid, bool) or not isinstance(pid, int) or pid <= 0:
            return False
        exists, actual = _process_state(pid)
        if not exists:
            return False
        return not (started is not None and actual is not None and abs(actual - started) > 1.0)


@dataclass
class _InvocationRecord:
    invocation_id: str
    started_at: str
    ended_at: str | None = None
    games: int = 0
    requests_charged: int = 0
    probe_requests: int = 0
    status: ResultsStatus = "running"
    stop_reason: StopReason | None = None
    detail: str | None = None
    #: The availability probe's answer (model, latency, tokens), when one was sent.
    probe: dict[str, JsonValue] | None = None

    def to_dict(self) -> dict[str, JsonValue]:
        return {
            "invocation_id": self.invocation_id,
            "started_at": self.started_at,
            "ended_at": self.ended_at,
            "games": self.games,
            "requests_charged": self.requests_charged,
            "probe_requests": self.probe_requests,
            "status": self.status,
            "stop_reason": self.stop_reason,
            "detail": self.detail,
            "probe": self.probe,
        }


#: ``results.json``'s top-level fields and one ``invocations`` entry's fields.
_RESULTS_KEYS: Final = (
    "schema_version",
    "batch_id",
    "panel",
    "manifest_sha256",
    "status",
    "stop_reason",
    "updated_at",
    "invocations",
    "cases",
    "scorecard",
)
_INVOCATION_KEYS: Final = tuple(_InvocationRecord("", "").to_dict())


def read_results(batch_dir: Path, manifest: BatchManifest, digest: str) -> _Doc:
    """``results.json``, checked against its batch's manifest (and that manifest's hash).

    A manifest edited after the batch started no longer matches the hash recorded
    when it was created: :class:`ProvenanceMismatch`.
    """
    doc = _Doc(RESULTS_FILE, _read_document(batch_dir / RESULTS_FILE, RESULTS_FILE), _RESULTS_KEYS)
    if doc.text("manifest_sha256") != digest:
        raise ProvenanceMismatch(f"{MANIFEST_FILE} changed after the batch started")
    if doc.text("batch_id") != manifest.batch_id or manifest.batch_id != batch_dir.name:
        raise CorruptRecord(f"{RESULTS_FILE} or {MANIFEST_FILE} belongs to another batch")
    doc.choice("status", RESULTS_STATUSES)
    doc.optional_choice("stop_reason", STOP_REASONS)
    for index, item in enumerate(doc.items("invocations")):
        entry = _Doc(f"{RESULTS_FILE} invocations[{index}]", item, _INVOCATION_KEYS)
        entry.choice("status", RESULTS_STATUSES)
        entry.optional_choice("stop_reason", STOP_REASONS)
    return doc


def _cases_from(doc: _Doc, manifest: BatchManifest) -> list[CaseRecord]:
    items = doc.items("cases")
    if len(items) != len(manifest.cases):
        raise CorruptRecord(f"{RESULTS_FILE} does not list every planned case")
    return [
        CaseRecord.from_dict(item, spec, f"{RESULTS_FILE} cases[{i}]")
        for i, (item, spec) in enumerate(zip(items, manifest.cases, strict=True))
    ]


class BatchStore:
    """One batch directory, owned by this process for its lifetime (module docstring)."""

    def __init__(
        self,
        batch_dir: Path,
        manifest: BatchManifest,
        manifest_sha256: str,
        owner: LockOwner,
        *,
        files: EvidenceFiles,
        wall_time: Callable[[], float],
    ) -> None:
        self.batch_dir = batch_dir
        self.manifest = manifest
        self.manifest_sha256 = manifest_sha256
        self.owner = owner
        self._files = files
        self._wall_time = wall_time
        self._released = False

    # -- creation / opening -----------------------------------------------------

    @classmethod
    def create(
        cls,
        benchmark_root: Path,
        manifest: BatchManifest,
        *,
        probe: ProcessProbe,
        files: EvidenceFiles | None = None,
        wall_time: Callable[[], float] = time.time,
    ) -> BatchStore:
        """Create the batch directory, take its lock, write the manifest and all-pending results."""
        batch_dir = benchmark_root / manifest.batch_id
        benchmark_root.mkdir(parents=True, exist_ok=True)
        batch_dir.mkdir()  # a batch id is never reused
        owner = _acquire_lock(batch_dir, manifest.batch_id, probe, wall_time)
        data = _encode(manifest.to_dict())
        if not _exclusive_write(batch_dir / MANIFEST_FILE, data):
            raise CorruptRecord(f"{MANIFEST_FILE} already exists in a new batch")
        store = cls(
            batch_dir,
            manifest,
            hashlib.sha256(data).hexdigest(),
            owner,
            files=EvidenceFiles() if files is None else files,
            wall_time=wall_time,
        )
        store.write_results([CaseRecord(spec) for spec in manifest.cases], [], "created", None)
        store.append_attempt({"event": "created", "case_id": None})
        return store

    @classmethod
    def open(
        cls,
        benchmark_root: Path,
        batch_id: str,
        *,
        probe: ProcessProbe,
        files: EvidenceFiles | None = None,
        wall_time: Callable[[], float] = time.time,
    ) -> BatchStore:
        """Open an existing batch for resuming: lock it, verify its manifest hash."""
        if not is_valid_run_id(batch_id):
            raise BenchmarkError("usage", f"not a batch id: {safe_repr(batch_id)}")
        batch_dir = benchmark_root / batch_id
        if not batch_dir.is_dir():
            raise BenchmarkError("usage", f"no batch {batch_id} under the benchmark root")
        owner = _acquire_lock(batch_dir, batch_id, probe, wall_time)
        try:
            manifest, digest = read_manifest(batch_dir)
            store = cls(
                batch_dir,
                manifest,
                digest,
                owner,
                files=EvidenceFiles() if files is None else files,
                wall_time=wall_time,
            )
            read_results(batch_dir, manifest, digest)
        except BaseException:
            _release_lock(batch_dir, owner)
            raise
        return store

    # -- records ------------------------------------------------------------------

    def read_cases(self) -> list[CaseRecord]:
        return _cases_from(
            read_results(self.batch_dir, self.manifest, self.manifest_sha256), self.manifest
        )

    def read_invocations(self) -> list[dict[str, JsonValue]]:
        doc = read_results(self.batch_dir, self.manifest, self.manifest_sha256)
        return [_json_mapping(_Doc(RESULTS_FILE, item).value) for item in doc.items("invocations")]

    def write_results(
        self,
        cases: Sequence[CaseRecord],
        invocations: Sequence[Mapping[str, JsonValue]],
        status: ResultsStatus,
        stop_reason: StopReason | None,
    ) -> None:
        """Atomically replace ``results.json`` (before and after every match)."""
        document: dict[str, JsonValue] = {
            "schema_version": SCHEMA_VERSION,
            "batch_id": self.manifest.batch_id,
            "panel": self.manifest.panel,
            "manifest_sha256": self.manifest_sha256,
            "status": status,
            "stop_reason": stop_reason,
            "updated_at": utc_timestamp(self._wall_time()),
            "invocations": [dict(item) for item in invocations],
            "cases": [case.to_dict() for case in cases],
            "scorecard": scorecard(cases),
        }
        assert tuple(document) == _RESULTS_KEYS  # the reader requires exactly these
        _write_document(self._files, self.batch_dir / RESULTS_FILE, document)

    def append_attempt(self, record: Mapping[str, JsonValue]) -> None:
        """Append one attempt record (never rewritten)."""
        line: dict[str, JsonValue] = {
            "schema_version": SCHEMA_VERSION,
            "batch_id": self.manifest.batch_id,
            "invocation_id": self.owner.invocation_id,
            "at": utc_timestamp(self._wall_time()),
        }
        line.update(record)
        text = json.dumps(line, ensure_ascii=True, allow_nan=False, separators=(",", ":"))
        self._files.append(self.batch_dir / ATTEMPTS_FILE, (text + "\n").encode("ascii"))

    def timestamp(self) -> str:
        """Now, as the batch records time (UTC)."""
        return utc_timestamp(self._wall_time())

    def fresh_pycache_prefix(self, case: CaseSpec, attempt: int) -> Path:
        """A bytecode cache prefix for one attempt's child, created here: a new, empty,
        unpredictably named folder (``tempfile.mkdtemp``), so no cache can be planted
        in it beforehand (the child never writes one either)."""
        folder = self.batch_dir / ATTEMPT_LOGS_DIR
        folder.mkdir(exist_ok=True)
        prefix = f"{case.case_id}.{attempt}."
        return Path(tempfile.mkdtemp(prefix=prefix, suffix=".pycache", dir=folder))

    def log_paths(self, case: CaseSpec, attempt: int) -> tuple[Path, Path]:
        folder = self.batch_dir / ATTEMPT_LOGS_DIR
        folder.mkdir(exist_ok=True)
        stem = f"{case.case_id}.{attempt}"
        return folder / f"{stem}.stdout.log", folder / f"{stem}.stderr.log"

    def release(self) -> None:
        if not self._released:
            self._released = True
            _release_lock(self.batch_dir, self.owner)


def read_manifest(batch_dir: Path) -> tuple[BatchManifest, str]:
    """The batch manifest and the SHA-256 of its bytes."""
    path = batch_dir / MANIFEST_FILE
    try:
        data = path.read_bytes()
    except OSError as exc:
        raise CorruptRecord(f"{MANIFEST_FILE} cannot be read") from exc
    manifest = BatchManifest.from_dict(_read_document(path, MANIFEST_FILE))
    return manifest, hashlib.sha256(data).hexdigest()


def _acquire_lock(
    batch_dir: Path, batch_id: str, probe: ProcessProbe, wall_time: Callable[[], float]
) -> LockOwner:
    """Take the batch's exclusive lock; a verifiably stale one is retired first."""
    pid, started = probe.current()
    owner = LockOwner(
        pid=pid,
        started=started,
        host=socket.gethostname(),
        invocation_id=uuid.uuid4().hex,
        created_at=utc_timestamp(wall_time()),
    )
    path = batch_dir / LOCK_FILE
    data = _encode(owner.to_dict())
    for _ in range(2):
        if _exclusive_write(path, data):
            return owner
        try:
            raw = path.read_bytes()
            holder = LockOwner.from_dict(_read_document(path, LOCK_FILE))
        except (OSError, BenchmarkError) as exc:
            raise LockHeld(
                f"batch {batch_id} has an unreadable {LOCK_FILE}; check no benchmark is "
                f"running, then remove it ({safe_exception_text(exc)})"
            ) from exc
        if holder.host != owner.host:
            raise LockHeld(f"batch {batch_id} is locked by host {holder.host!r}")
        if probe.alive(holder.pid, holder.started):
            raise LockHeld(f"batch {batch_id} is owned by running process {holder.pid}")
        retired = batch_dir / f"lock.stale-{owner.invocation_id}.json"
        try:
            path.rename(retired)
        except OSError as exc:
            raise LockHeld(f"batch {batch_id}: the stale lock could not be retired") from exc
        if retired.read_bytes() != raw:  # another process replaced it meanwhile
            with contextlib.suppress(OSError):
                retired.rename(path)
            raise LockHeld(f"batch {batch_id}: concurrent lock takeover detected")
    raise LockHeld(f"batch {batch_id}: the lock could not be taken")


def _release_lock(batch_dir: Path, owner: LockOwner) -> None:
    path = batch_dir / LOCK_FILE
    with contextlib.suppress(OSError, BenchmarkError):
        holder = LockOwner.from_dict(_read_document(path, LOCK_FILE))
        if holder.invocation_id == owner.invocation_id:
            path.unlink()


# ---------------------------------------------------------------------------
# Child process: the production entrypoint, bounded
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ChildLaunch:
    """Exactly how one case's child process is started.

    ``on_started`` receives the child's pid as soon as it exists (the batch records
    it, so a resume can refuse to replay a case whose match may still be playing).
    """

    argv: tuple[str, ...]
    cwd: Path
    env: Mapping[str, str]
    hard_wall_seconds: float
    stdout_path: Path
    stderr_path: Path
    on_started: Callable[[int], None] | None = field(default=None, compare=False)


@dataclass(frozen=True)
class ChildExit:
    """How the child ended. ``returncode`` is None when it never started."""

    returncode: int | None
    wall_seconds: float
    timed_out: bool = False
    interrupted: bool = False
    tree_terminated: bool = False
    launch_error: str | None = None


class ChildLauncher(Protocol):
    """Runs one child to completion within its bound (production: subprocess)."""

    def run(self, launch: ChildLaunch) -> ChildExit: ...


class CaseObserver(Protocol):
    """The launch seam (:class:`DashboardObserver` is the dashboard-first launch).

    ``before_case`` runs before a case's child starts and may append arguments to
    its command line; ``launch_failure`` runs once the child ended and names why its
    launch never let SC2 start (None if it did, or never got that far);
    ``after_case`` runs once the case is recorded; ``finish`` once the invocation
    ends (``outcome`` None when it ended with an error before any outcome).
    """

    def before_case(self, case: CaseSpec, index: int, count: int) -> tuple[str, ...]: ...

    def launch_failure(self, case: CaseSpec, index: int, count: int) -> str | None: ...

    def after_case(self, record: CaseRecord, index: int, count: int) -> None: ...

    def finish(
        self, outcome: BatchOutcome | None, detail: str | None, *, interrupted: bool = False
    ) -> None: ...


class DashboardObserver:
    """One invocation's dashboard-first launch (plan D7) as a :class:`CaseObserver`.

    Every case's child joins the same launch session (``--launch-session``), so the
    one dashboard tab follows each exact run; the session records each case's
    position and, at the end, how the invocation ended.
    """

    def __init__(self, launch: DashboardLaunch) -> None:
        self.launch = launch

    @property
    def session_id(self) -> str:
        return self.launch.session_id

    def before_case(self, case: CaseSpec, index: int, count: int) -> tuple[str, ...]:
        try:
            self.launch.before_case(index, count)
        except (OSError, ValueError) as exc:  # the case stays pending; nothing launched
            raise BenchmarkError(
                "dashboard_unavailable",
                f"the launch session could not be updated: {safe_exception_text(exc)}",
            ) from exc
        return self.launch.child_arguments()

    def launch_failure(self, case: CaseSpec, index: int, count: int) -> str | None:
        return self.launch.launch_failure()

    def after_case(self, record: CaseRecord, index: int, count: int) -> None:
        shown = record.result if record.status == "complete" else record.reason
        summary = f"game {index + 1} of {count} ({record.spec.case_id}): {record.status} {shown}"
        self.launch.after_case(index, count, summary)

    def finish(
        self, outcome: BatchOutcome | None, detail: str | None, *, interrupted: bool = False
    ) -> None:
        if outcome is None:  # no outcome: Ctrl+C is a stop, anything else a failure
            state: LaunchState = "stopped" if interrupted else "failed"
            self.launch.end(state, detail or "the benchmark stopped before playing")
            return
        message = f"batch {outcome.status}" + (f": {outcome.detail}" if outcome.detail else "")
        if outcome.status in ("complete", "incomplete"):
            self.launch.end("finished", message)
        elif outcome.status == "stopped":
            self.launch.end("failed", message)
        else:  # interrupted, or the budget ran out (resume later)
            self.launch.end("stopped", message)


def terminate_process_tree(process: subprocess.Popen[bytes]) -> None:
    """End ``process`` and its descendants -- only that tree, never other SC2 processes."""
    if process.poll() is not None:
        return
    if sys.platform == "win32":
        system_root = os.environ.get("SystemRoot", r"C:\Windows")
        taskkill = Path(system_root) / "System32" / "taskkill.exe"
        with contextlib.suppress(OSError, subprocess.SubprocessError):
            subprocess.run(
                [str(taskkill), "/PID", str(process.pid), "/T", "/F"],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=30,
                check=False,
            )
    else:
        with contextlib.suppress(OSError):
            os.killpg(process.pid, signal.SIGKILL)  # the child leads its own session
    try:
        process.wait(timeout=30)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=10)


class SubprocessLauncher:
    """The production :class:`ChildLauncher`.

    The child's stdout and stderr go to the attempt's log files. A child alive at
    its hard wall bound has its process tree terminated. Ctrl+C reaches the child
    too (Windows: the shared console delivers it; elsewhere it is forwarded once to
    the child's own session), which leaves the game cleanly; after
    :data:`STOP_GRACE_SECONDS` the tree is terminated instead. A second Ctrl+C
    escapes after the tree is ended.
    """

    def __init__(
        self,
        *,
        clock: Callable[[], float] = time.monotonic,
        stop_grace_seconds: float = STOP_GRACE_SECONDS,
        terminate: Callable[[subprocess.Popen[bytes]], None] = terminate_process_tree,
    ) -> None:
        self._clock = clock
        self._grace = stop_grace_seconds
        self._terminate = terminate

    def run(self, launch: ChildLaunch) -> ChildExit:
        start = self._clock()
        session: dict[str, Any] = {} if sys.platform == "win32" else {"start_new_session": True}
        with launch.stdout_path.open("wb") as out, launch.stderr_path.open("wb") as err:
            try:
                process = subprocess.Popen(
                    list(launch.argv),
                    cwd=launch.cwd,
                    env=dict(launch.env),
                    stdin=subprocess.DEVNULL,
                    stdout=out,
                    stderr=err,
                    **session,
                )
            except OSError as exc:
                return ChildExit(None, 0.0, launch_error=safe_exception_text(exc))
            try:
                if launch.on_started is not None:
                    launch.on_started(process.pid)
                return self._wait(process, launch, start)
            finally:
                if process.poll() is None:  # e.g. a second Ctrl+C escaped the grace wait
                    self._terminate(process)

    def _until(self, process: subprocess.Popen[bytes], deadline: float) -> int:
        """Wait for exit by deadline, in short slices so Ctrl+C is handled promptly
        (one long wait on Windows defers it until the child exits)."""
        while True:
            remaining = deadline - self._clock()
            if remaining <= 0:
                raise subprocess.TimeoutExpired(process.args, 0)
            try:
                return process.wait(timeout=min(_WAIT_SLICE_SECONDS, remaining))
            except subprocess.TimeoutExpired:
                continue

    def _wait(
        self, process: subprocess.Popen[bytes], launch: ChildLaunch, start: float
    ) -> ChildExit:
        try:
            code = self._until(process, start + launch.hard_wall_seconds)
            return ChildExit(code, self._clock() - start)
        except subprocess.TimeoutExpired:
            self._terminate(process)
            return ChildExit(
                process.returncode, self._clock() - start, timed_out=True, tree_terminated=True
            )
        except KeyboardInterrupt:
            if sys.platform != "win32":
                with contextlib.suppress(OSError):
                    os.killpg(process.pid, signal.SIGINT)
            try:
                code = self._until(process, self._clock() + self._grace)
                return ChildExit(code, self._clock() - start, interrupted=True)
            except subprocess.TimeoutExpired:
                self._terminate(process)
                return ChildExit(
                    process.returncode,
                    self._clock() - start,
                    interrupted=True,
                    tree_terminated=True,
                )


def child_hard_wall_seconds(limits: BenchmarkLimits, *, dashboard: bool) -> float:
    """THE bound on one case's whole child process tree (the real run and the dry run).

    The match bound, plus the rendered-ready barrier's deadline for a dashboard-first
    child: waiting for the page is not play.
    """
    return float(limits.match_wall_seconds) + (RUN_READY_SECONDS if dashboard else 0.0)


def child_environment(
    base: Mapping[str, str],
    snapshot_dir: Path,
    *,
    hosted: bool,
    pycache_prefix: Path | None = None,
) -> dict[str, str]:
    """The child's environment: the snapshot first on the import path, nothing else.

    Inherited ``PYTHONPATH``-style variables that could redirect imports are dropped;
    bytecode is never written, and with ``pycache_prefix`` (a fresh per-attempt
    folder) no bytecode cache next to the snapshot's sources is ever consulted; the
    service key is passed only to a hosted case (a scripted case never sees it).
    """
    env = {k: v for k, v in base.items() if k.upper() not in _DROPPED_ENV}
    env["PYTHONPATH"] = os.pathsep.join([str(snapshot_dir / "src"), str(snapshot_dir)])
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["PYTHONNOUSERSITE"] = "1"
    if pycache_prefix is not None:
        env["PYTHONPYCACHEPREFIX"] = str(pycache_prefix)
    if not hosted:
        for name in [k for k in env if k.upper() == KEY_ENV]:
            del env[name]
    return env


def child_argv(
    case: CaseSpec,
    limits: BenchmarkLimits,
    model: str,
    run_root: Path,
    *,
    python: str,
    extra: Sequence[str] = (),
) -> tuple[str, ...]:
    """The production entrypoint's exact command line; every option explicit."""
    options = case.match_options(limits, model)
    argv = [
        python,
        "-m",
        case.entrypoint,
        "--map",
        options.map_name,
        "--opponent-race",
        options.opponent_race,
        "--difficulty",
        str(options.difficulty),
        "--seed",
        str(options.seed),
        "--max-game-seconds",
        str(options.max_game_seconds),
        "--max-wall-seconds",
        str(options.max_wall_seconds),
        "--decision-provider",
        options.decision_provider,
        "--decision-model",
        options.decision_model,
        "--decision-max-requests",
        str(options.decision_max_requests),
        "--run-root",
        str(run_root),
    ]
    if options.realtime:
        argv.append("--realtime")
    argv.extend(extra)
    return tuple(argv)


# ---------------------------------------------------------------------------
# Scoring (calibrated against real archives; never transcript based)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RunExpectation:
    """What one run must be: its executable identity and exact options."""

    version: int
    entrypoint: str
    policy_hash: str
    options: MatchOptions
    expected_returned_model: str | None
    snapshot_dir: Path | None = None
    require_diagnostics: bool = True


@dataclass(frozen=True)
class ChildEvidence:
    """What the parent observed of the child: its exit and its summary line."""

    exit: ChildExit
    summary_status: str | None
    summary_result: str | None
    summary_run_id: str | None


@dataclass(frozen=True)
class Finding:
    code: Reason
    message: str

    def to_dict(self) -> dict[str, JsonValue]:
        return {"code": self.code, "message": self.message}


@dataclass(frozen=True)
class ScoredRun:
    """One run's verdict. An ``invalid`` run is never a win, whatever SC2 reported."""

    status: Literal["complete", "invalid"]
    result: CaseResult | None
    reported_result: str | None
    reason: Reason | None
    findings: tuple[Finding, ...]
    run_id: str | None
    evidence: Evidence
    metrics: dict[str, JsonValue]
    verified_calls: int | None

    @property
    def counted_as_win(self) -> bool:
        return self.status == "complete" and self.result == "win"

    @property
    def detail(self) -> str | None:
        if not self.findings:
            return None
        return render_text("; ".join(f.message for f in self.findings))[:2048]

    def to_dict(self) -> dict[str, JsonValue]:
        return {
            "status": self.status,
            "valid": self.status == "complete",
            "result": self.result,
            "reported_result": self.reported_result,
            "counted_as_win": self.counted_as_win,
            "reason": self.reason,
            "findings": [f.to_dict() for f in self.findings],
            "run_id": self.run_id,
            "evidence": self.evidence,
            "metrics": self.metrics,
            "verified_calls": self.verified_calls,
        }


#: The runner's exit code for each terminal (status, result) it records.
def _expected_exit(state: RunState) -> int:
    if state.status == "finished":
        return EXIT_TIMEOUT if state.result == "timeout" else EXIT_OK
    if state.status == "stopped":
        return EXIT_STOPPED
    if state.result == "timeout" and state.error is not None and state.error.code == "wall_timeout":
        return EXIT_TIMEOUT
    return EXIT_FAILURE


def _classify_state(state: RunState) -> tuple[CaseResult | None, Finding | None]:
    """The case result a terminal RunState supports, or the finding it raises."""
    code = state.error.code if state.error is not None else None
    if state.status == "finished":
        if state.result == "timeout":
            return "timeout", None
        for result in ("win", "loss", "draw"):
            if state.result == result:
                return result, None
        return None, Finding("corrupt_evidence", "finished run without a result")
    if state.status == "stopped":
        return None, Finding("interrupted", "the match was stopped before it ended")
    if code == "persistence_failed":
        return None, Finding("infrastructure_failure", "run evidence could not be persisted")
    if code == "sc2_unavailable":
        message = state.error.message if state.error is not None else ""
        return None, Finding("infrastructure_failure", f"SC2 unavailable: {message}")
    if code == "wall_timeout":  # the host could not play the match in time
        return None, Finding(
            "infrastructure_failure",
            "the wall-clock limit ended the match: the host could not play it in time",
        )
    if code == "match_crashed" or code is None:
        return "error", None
    return None, Finding("corrupt_evidence", f"unexpected terminal error {code}")


def _read_diagnostics(run_dir: Path, run_id: str) -> dict[str, object] | None:
    path = run_dir / DIAGNOSTICS_FILE
    if not path.exists() and not path.is_symlink():
        return None
    try:
        if path.resolve(strict=True).parent != run_dir.resolve(strict=True):
            raise CorruptRecord(f"{DIAGNOSTICS_FILE} is not in the run directory")
    except OSError as exc:
        raise CorruptRecord(f"{DIAGNOSTICS_FILE} cannot be resolved") from exc
    doc = _Doc(
        DIAGNOSTICS_FILE,
        _read_document(path, DIAGNOSTICS_FILE, MAX_DIAGNOSTICS_BYTES),
        DIAGNOSTICS_FIELDS,
    )
    if doc.integer("schema_version") != DIAGNOSTICS_SCHEMA_VERSION:
        raise CorruptRecord(f"{DIAGNOSTICS_FILE} has an unsupported schema_version")
    if doc.text("kind") != DIAGNOSTICS_KIND or doc.text("run_id") != run_id:
        raise CorruptRecord(f"{DIAGNOSTICS_FILE} does not describe this run")
    for name in ("entrypoint", "family", "policy_source", "runtime_root"):
        doc.text(name)
    doc.integer("version")
    doc.matching("policy_hash", POLICY_HASH_RE, "a SHA-256 hex digest")
    _Doc(
        f"{DIAGNOSTICS_FILE} options", doc.mapping("options"), match_options_record(MatchOptions())
    )
    doc.mapping("outcome")
    doc.mapping("metrics")
    return doc.value


def _trace_decisions(run_dir: Path, state: RunState) -> tuple[dict[str, JsonValue], bool]:
    """Decision counts rebuilt from a legacy archive's trace; (summary, complete).

    Every coordinator fact change is traced as an ``Army decision source``
    diagnostic, so a complete trace replays the same per-poll accounting the live
    accumulator uses (:class:`jev.bot.MatchMetrics`).
    """
    from jev.bot import MatchMetrics  # burnysc2 import: only when a legacy run is scored

    trace = state.trace
    if trace.segment != trace.rotated_segments + 1:  # the writer rotates one at a time
        raise CorruptRun(f"{STATE_FILE} trace counts are inconsistent")
    complete = (
        trace.complete
        and trace.dropped_events == 0
        and trace.dropped_segments == 0
        and trace.rotated_segments == 0
    )
    metrics = MatchMetrics()
    # Only the retained window can exist on disk (never loop over an untrusted count).
    for segment in range(max(1, trace.segment - MAX_TRACE_SEGMENTS + 1), trace.segment + 1):
        stored = read_trace_segment(run_dir, segment)
        if stored is None:
            complete = False
            continue
        if stored.torn_tail:
            complete = False
        for event in stored.events:
            if event.kind == "diagnostic" and event.reason == "Army decision source":
                metrics.record_decision(dict(event.facts))
    summary = metrics.summary(end_game_seconds=state.game_seconds)
    decisions = summary.get("decisions")
    return (dict(decisions) if isinstance(decisions, dict) else {"status": "unavailable"}), complete


def _expected_options_record(expectation: RunExpectation) -> dict[str, JsonValue]:
    return match_options_record(expectation.options)


def _same_path(path_text: str, root: Path) -> bool:
    try:
        return Path(path_text).resolve() == root.resolve()
    except (OSError, ValueError):
        return False


def _within(path_text: str, root: Path) -> bool:
    try:
        resolved = Path(path_text).resolve()
        return resolved == root.resolve() or root.resolve() in resolved.parents
    except (OSError, ValueError):
        return False


def score_run(
    run_root: Path,
    run_id: str | None,
    expectation: RunExpectation,
    child: ChildEvidence | None = None,
) -> ScoredRun:
    """Score one run archive against ``expectation`` (module docstring, plan D6).

    ``child`` (absent for calibration) adds the child's exit and summary line to the
    comparison. Never raises for a defective archive: every defect is a finding.
    """
    findings: list[Finding] = []

    def fail(code: Reason, message: str) -> None:
        findings.append(Finding(code, render_text(message)[:400]))

    if child is not None:
        if child.exit.launch_error is not None:
            fail("infrastructure_failure", f"the child could not start: {child.exit.launch_error}")
        if child.exit.timed_out:
            fail(
                "infrastructure_failure",
                "the child outlived its hard wall bound; its process tree was terminated",
            )
        if child.exit.interrupted:
            fail("interrupted", "the benchmark was interrupted during this case")
        if (
            child.summary_run_id is None
            and child.exit.launch_error is None
            and not child.exit.interrupted
        ):
            fail("infrastructure_failure", "the child reported no run id")
    if run_id is None:
        return _scored(findings, None, None, None, "none", {}, None)
    if not is_valid_run_id(run_id):
        fail("corrupt_evidence", "the run id is not a UUID4 hex")
        return _scored(findings, None, None, run_id, "none", {}, None)
    run_dir = run_root / run_id
    try:
        metadata = read_run_metadata(run_dir)
        if metadata is None:
            fail("corrupt_evidence", "the run has no metadata.json")
            return _scored(findings, None, None, run_id, "none", {}, None)
        state = read_run_state(run_dir, metadata)
        read_policy_archive(run_dir, metadata)
    except PolicyHashMismatch as exc:
        fail("provenance_mismatch", f"archive policy hash mismatch: {exc.message}")
        return _scored(findings, None, None, run_id, "none", {}, None)
    except CorruptRun as exc:
        fail("corrupt_evidence", f"corrupt run archive: {exc.message}")
        return _scored(findings, None, None, run_id, "none", {}, None)
    reported = state.result
    if state.status not in TERMINAL_RUN_STATUSES:
        fail("corrupt_evidence", f"the run never reached a terminal state ({state.status})")
    result, finding = _classify_state(state)
    if finding is not None:
        findings.append(finding)
    _check_identity(metadata.family, metadata.version, metadata.policy_hash, expectation, fail)
    options = expectation.options
    case_fields = (
        ("map", metadata.map, options.map_name),
        ("opponent race", metadata.opponent_race, options.opponent_race),
        ("difficulty", metadata.difficulty, options.difficulty),
        ("seed", metadata.seed, options.seed),
        ("game-time limit", metadata.max_game_seconds, float(options.max_game_seconds)),
        ("wall-clock limit", metadata.max_wall_seconds, float(options.max_wall_seconds)),
    )
    for name, actual, wanted in case_fields:
        if actual != wanted:
            fail("provenance_mismatch", f"{name} is {actual!r}, expected {wanted!r}")
    if state.status == "finished" and (
        metadata.replay_path != REPLAY_FILE or not _nonempty(run_dir / REPLAY_FILE)
    ):
        fail("corrupt_evidence", "a finished match has no saved replay")
    if child is not None:
        _check_child(child, run_id, state, fail)
    evidence: Evidence = "none"
    diagnostics_corrupt = False
    try:
        diagnostics = _read_diagnostics(run_dir, run_id)
    except CorruptRecord as exc:
        fail("corrupt_evidence", exc.message)
        diagnostics = None
        diagnostics_corrupt = True
    metrics: dict[str, JsonValue] = {}
    decisions: Mapping[str, object] | None = None
    if diagnostics is not None:
        evidence = "diagnostics"
        _check_diagnostics(diagnostics, state, expectation, child, fail)
        raw_metrics = diagnostics["metrics"]
        metrics = _json_mapping(raw_metrics) if isinstance(raw_metrics, dict) else {}
        candidate = metrics.get("decisions")
        decisions = candidate if isinstance(candidate, dict) else {"status": "unavailable"}
        if metrics.get("status") == "failed" and options.decision_provider == "typesafe":
            fail("corrupt_evidence", "the metric accumulator failed: answers are unverifiable")
            decisions = None
        outcome = diagnostics["outcome"]
        if isinstance(outcome, dict):
            for name in ("game_seconds", "commands_accepted", "commands_rejected"):
                value = outcome.get(name)
                metrics[name] = value if isinstance(value, int | float) else None
    elif not diagnostics_corrupt:
        if expectation.require_diagnostics:
            fail("corrupt_evidence", f"{DIAGNOSTICS_FILE} is missing")
        else:
            evidence = "legacy_trace"
            try:
                traced, complete = _trace_decisions(run_dir, state)
            except CorruptRun as exc:
                fail("corrupt_evidence", f"corrupt trace: {exc.message}")
                traced, complete = {"status": "unavailable"}, False
            metrics = {
                "status": "unavailable",
                "reason": "legacy archive without a diagnostic summary",
                "game_seconds": state.game_seconds,
                "decisions": traced,
            }
            decisions = traced if complete else None
            if not complete and options.decision_provider == "typesafe":
                fail("corrupt_evidence", "the trace is incomplete: hosted decisions unverifiable")
    calls = _check_decisions(decisions, expectation, fail)
    return _scored(findings, result, reported, run_id, evidence, metrics, calls)


def _nonempty(path: Path) -> bool:
    try:
        return path.is_file() and path.stat().st_size > 0
    except OSError:
        return False


def _check_identity(
    family: str,
    version: int,
    policy_hash: str,
    expectation: RunExpectation,
    fail: Callable[[Reason, str], None],
) -> None:
    if family != "jev" or version != expectation.version:
        fail(
            "provenance_mismatch",
            f"the run is {family} v{version}, expected jev v{expectation.version}",
        )
    if policy_hash != expectation.policy_hash:
        fail(
            "provenance_mismatch",
            f"policy hash {policy_hash} is not the expected {expectation.policy_hash}",
        )


def _check_child(
    child: ChildEvidence, run_id: str, state: RunState, fail: Callable[[Reason, str], None]
) -> None:
    if child.summary_run_id is not None and child.summary_run_id != run_id:
        fail("corrupt_evidence", "the child's summary names another run")
    shown_result = "None" if state.result is None else state.result
    if child.summary_status is not None and (
        child.summary_status != state.status or child.summary_result != shown_result
    ):
        fail(
            "corrupt_evidence",
            f"the child reported {child.summary_status}/"
            f"{child.summary_result} but the run state is {state.status}/{shown_result}",
        )
    code = child.exit.returncode
    if code is not None and not child.exit.timed_out and code != _expected_exit(state):
        fail(
            "corrupt_evidence",
            f"the child exited {code}; the run state implies {_expected_exit(state)}",
        )


def _check_diagnostics(
    diagnostics: Mapping[str, object],
    state: RunState,
    expectation: RunExpectation,
    child: ChildEvidence | None,
    fail: Callable[[Reason, str], None],
) -> None:
    # _read_diagnostics validated these types.
    family, version, digest = (
        str(diagnostics["family"]),
        int(str(diagnostics["version"])),
        str(diagnostics["policy_hash"]),
    )
    _check_identity(family, version, digest, expectation, fail)
    if diagnostics["entrypoint"] != expectation.entrypoint:
        fail(
            "provenance_mismatch",
            f"entrypoint {diagnostics['entrypoint']!r} is not {expectation.entrypoint!r}",
        )
    options = diagnostics["options"]
    expected_options = _expected_options_record(expectation)
    if isinstance(options, dict):
        for name, wanted in expected_options.items():
            if options.get(name) != wanted:
                fail(
                    "provenance_mismatch",
                    f"option {name} is {options.get(name)!r}, expected {wanted!r}",
                )
    snapshot = expectation.snapshot_dir
    if snapshot is not None:
        runtime_root = diagnostics["runtime_root"]
        source = diagnostics["policy_source"]
        if not isinstance(runtime_root, str) or not _same_path(runtime_root, snapshot):
            fail(
                "provenance_mismatch",
                "the run did not execute from its frozen snapshot (fallback source path)",
            )
        if not isinstance(source, str) or not _within(source, snapshot):
            fail("provenance_mismatch", "the policy was not loaded from the frozen snapshot")
    outcome = diagnostics["outcome"]
    if isinstance(outcome, dict):
        error = state.error.code if state.error is not None else None
        if (outcome.get("status"), outcome.get("result"), outcome.get("error_code")) != (
            state.status,
            state.result,
            error,
        ):
            fail("corrupt_evidence", "the diagnostic outcome disagrees with the run state")
        exit_code = outcome.get("exit_code")
        if (
            child is not None
            and child.exit.returncode is not None
            and not child.exit.timed_out
            and exit_code is not None
            and exit_code != child.exit.returncode
        ):
            fail("corrupt_evidence", "the diagnostic exit code disagrees with the child's")


def _check_decisions(
    decisions: Mapping[str, object] | None,
    expectation: RunExpectation,
    fail: Callable[[Reason, str], None],
) -> int | None:
    """Provider/model checks; the verified number of service calls (None if unknown).

    ``decisions`` is None when the evidence cannot show them (an incomplete legacy
    trace, a failed accumulator: already findings). A run that never polled the
    coordinator (e.g. it crashed first) records no decisions: for a hosted case
    that is no accepted hosted decision, not a hosted result.
    """
    options = expectation.options
    if decisions is None:
        return None
    if decisions.get("status") != "measured":
        if options.decision_provider == "typesafe":
            fail("no_accepted_hosted_decision", "no hosted decision was recorded")
        return None
    provider = decisions.get("provider")
    calls_value = decisions.get("calls")
    calls = None
    if isinstance(calls_value, int) and not isinstance(calls_value, bool):
        calls = calls_value
    if provider != options.decision_provider:
        fail(
            "provenance_mismatch",
            f"decision provider {provider!r} is not "
            f"{options.decision_provider!r} (no implicit fallback is accepted)",
        )
    if options.decision_provider != "typesafe":
        if calls:
            fail("provenance_mismatch", "a scripted run made service calls")
        return calls
    if decisions.get("requested_model") != options.decision_model:
        fail(
            "provenance_mismatch",
            f"requested model {decisions.get('requested_model')!r} is "
            f"not the pinned {options.decision_model!r}",
        )
    if decisions.get("max_requests") != options.decision_max_requests:
        fail("provenance_mismatch", "the per-match request cap differs from the manifest")
    models = decisions.get("returned_models")
    expected_model = expectation.expected_returned_model
    if isinstance(models, dict):
        drifted = sorted(str(m) for m in models if m != expected_model)
        if drifted:
            fail("model_drift", f"answers came from {drifted}, expected only {expected_model!r}")
    else:
        fail("corrupt_evidence", "returned models are not recorded")
    auth = decisions.get("authentication_failures")
    if isinstance(auth, int) and auth > 0:
        fail("authentication_failed", "the service refused the credentials during the match")
    failures = decisions.get("failures")
    if isinstance(failures, dict):  # every request failed, not by refusal: model unavailable
        failed = {k: v for k, v in failures.items() if k not in AUTH_FAILURE_REASONS}
        if decisions.get("answers") == 0 and failed:
            shown = ", ".join(f"{k}={v}" for k, v in sorted(failed.items())[:5])
            fail("model_unavailable", f"every request of the match failed ({shown})")
    accepted = decisions.get("accepted")
    if not isinstance(accepted, int) or accepted < 1:
        fail(
            "no_accepted_hosted_decision",
            "no hosted answer was accepted: not a hosted comparison case",
        )
    return calls


def _scored(
    findings: Sequence[Finding],
    result: CaseResult | None,
    reported: str | None,
    run_id: str | None,
    evidence: Evidence,
    metrics: dict[str, JsonValue],
    calls: int | None,
) -> ScoredRun:
    reason: Reason | None = None
    for code in REASON_PRIORITY:
        if any(f.code == code for f in findings):
            reason = code
            break
    valid = reason is None and result is not None
    if reason is None and result is None:
        reason = "corrupt_evidence"
    return ScoredRun(
        status="complete" if valid else "invalid",
        result=result if valid else None,
        reported_result=reported,
        reason=None if valid else reason,
        findings=tuple(findings),
        run_id=run_id,
        evidence=evidence,
        metrics=metrics,
        verified_calls=calls,
    )


def parse_child_summary(stdout_path: Path) -> tuple[str | None, str | None, str | None]:
    """(status, result, run_id) from the runner's last summary line in the child's stdout."""
    try:
        with stdout_path.open("rb") as handle:
            handle.seek(0, os.SEEK_END)
            size = handle.tell()
            handle.seek(max(0, size - _MAX_STDOUT_TAIL_BYTES))
            text = handle.read().decode("utf-8", errors="replace")
    except OSError:
        return None, None, None
    matches = list(_SUMMARY_RE.finditer(text))
    if not matches:
        return None, None, None
    last = matches[-1]
    return last["status"], last["result"], last["run_id"]


# ---------------------------------------------------------------------------
# Model availability probe (plan D6)
# ---------------------------------------------------------------------------

ProviderFactory = Callable[[str, DecisionConfig], DecisionProvider]


def _typesafe_provider(api_key: str, config: DecisionConfig) -> DecisionProvider:
    return TypesafeProvider(api_key, config)


@dataclass(frozen=True)
class ProbeResult:
    ok: bool
    reason: Reason | None
    detail: str
    returned_model: str | None = None
    latency_ms: float | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None

    def to_dict(self) -> dict[str, JsonValue]:
        return {
            "ok": self.ok,
            "reason": self.reason,
            "detail": self.detail,
            "returned_model": self.returned_model,
            "latency_ms": self.latency_ms,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
        }


def probe_model(
    api_key: str,
    *,
    model: str = PINNED_MODEL,
    expected_model: str = EXPECTED_RETURNED_MODEL,
    factory: ProviderFactory = _typesafe_provider,
    clock: Callable[[], float] = time.monotonic,
) -> ProbeResult:
    """One tiny request through the production provider; the pinned model must answer.

    Authentication refusal is ``authentication_failed``; any other failure is
    ``model_unavailable``; an answer from another model is ``model_drift``. The key
    is never echoed.
    """
    config = DecisionConfig(model=model, timeout=PROBE_TIMEOUT_SECONDS, max_requests=1)
    try:
        provider = factory(api_key, config)
    except ValueError as exc:
        return ProbeResult(False, "missing_configuration", safe_exception_text(exc))
    started = clock()

    async def ask() -> Answer:
        async with asyncio.timeout(PROBE_TIMEOUT_SECONDS + 1):
            return await provider.decide(dict(PROBE_STATE), PROBE_CHOICES)

    try:
        answer = asyncio.run(ask())
    except DecisionError as exc:
        code = str(exc)
        reason: Reason = (
            "authentication_failed" if code in AUTH_FAILURE_REASONS else "model_unavailable"
        )
        return ProbeResult(False, reason, f"probe failed: {render_text(code)[:80]}")
    except TimeoutError:
        return ProbeResult(False, "model_unavailable", "probe timed out")
    except Exception as exc:
        name = safe_repr(type(exc).__name__)
        return ProbeResult(False, "model_unavailable", f"probe failed: {name}")
    latency = round((clock() - started) * 1000, 1)
    if answer.model != expected_model:
        return ProbeResult(
            False,
            "model_drift",
            f"requested {model!r} but the service answered as {answer.model!r}",
            answer.model,
            latency,
            answer.input_tokens,
            answer.output_tokens,
        )
    return ProbeResult(
        True,
        None,
        "pinned model answered",
        answer.model,
        latency,
        answer.input_tokens,
        answer.output_tokens,
    )


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class BatchOutcome:
    status: BatchStatus
    stop_reason: StopReason | None
    detail: str | None
    exit_code: int
    cases: tuple[CaseRecord, ...]


@dataclass
class _Budget:
    """This invocation's budget (plan D6), checked before every match."""

    limits: BenchmarkLimits
    started: float
    games: int = 0
    requests: int = 0

    def refusal(self, case: CaseSpec, now: float) -> str | None:
        limits = self.limits
        if self.games >= limits.max_games:
            return f"the invocation already launched {self.games} of {limits.max_games} games"
        if (now - self.started) + limits.match_wall_seconds > limits.invocation_wall_seconds:
            return (
                f"{now - self.started:.0f} of {limits.invocation_wall_seconds} wall seconds "
                f"used; a match may take {limits.match_wall_seconds}"
            )
        if case.hosted and self.requests + limits.match_requests > limits.invocation_requests:
            return (
                f"{self.requests} of {limits.invocation_requests} requests used; a hosted "
                f"match may use {limits.match_requests}"
            )
        return None


def label_interrupted(
    store: BatchStore, cases: list[CaseRecord], probe: ProcessProbe
) -> list[CaseRecord]:
    """Resume: a case still ``running`` lost its benchmark process; label it interrupted.

    If that case's match process is still alive (its benchmark died, the match did
    not), the resume is refused with :class:`LockHeld`: replaying the case now would
    play two matches at once.
    """
    labeled = list(cases)
    for index, record in enumerate(labeled):
        if record.status == "running":
            if record.child_pid is not None and probe.alive(record.child_pid, record.child_started):
                raise LockHeld(
                    f"case {record.spec.case_id}'s match (pid {record.child_pid}) may still be "
                    "playing; wait for it to end before resuming"
                )
            labeled[index] = replace(
                record,
                status="invalid",
                result=None,
                reason="interrupted",
                detail="the benchmark process ended while this case was running",
                unrecorded_attempts=record.unrecorded_attempts + int(record.spec.hosted),
            )
            store.append_attempt(
                {
                    "event": "interrupted_detected",
                    "case_id": record.spec.case_id,
                    "attempt": record.attempts,
                    "run_id": record.run_id,
                }
            )
    return labeled


def run_batch(
    store: BatchStore,
    sources: Mapping[str, ResolvedSource],
    *,
    launcher: ChildLauncher,
    run_root: Path,
    environ: Mapping[str, str],
    python: str = sys.executable,
    retry_interrupted: bool = False,
    probe_requests: int = 0,
    probe: ProbeResult | None = None,
    observer: CaseObserver | None = None,
    clock: Callable[[], float] = time.monotonic,
    process_probe: ProcessProbe | None = None,
) -> BatchOutcome:
    """Play the batch's remaining cases in order, persisting before and after each.

    Complete cases are skipped and never overwritten; invalid ones too, except a
    case with a :data:`RETRYABLE_REASONS` reason when ``retry_interrupted``. Stops at
    the first stopping
    reason (module docstring) or when the next match could exceed the invocation
    budget. ``probe_requests`` are the service requests the preflight already used
    (``probe`` its answer, recorded with the invocation). Ctrl+C labels only a case
    that was running ``interrupted`` (keeping its run id); a case already recorded
    is never relabeled, and one not yet launched stays pending.
    """
    manifest = store.manifest
    limits = manifest.limits
    budget = _Budget(limits, clock(), requests=probe_requests)
    invocation = _InvocationRecord(
        store.owner.invocation_id,
        store.owner.created_at,
        probe_requests=probe_requests,
        probe=None if probe is None else probe.to_dict(),
    )
    previous = store.read_invocations()
    processes = SystemProcessProbe() if process_probe is None else process_probe
    cases = label_interrupted(store, store.read_cases(), processes)

    def persist(status: ResultsStatus, stop_reason: StopReason | None) -> None:
        invocation.games = budget.games
        invocation.requests_charged = budget.requests
        store.write_results(cases, [*previous, invocation.to_dict()], status, stop_reason)

    def finish(
        status: BatchStatus,
        reason: StopReason | None,
        detail: str | None,
        exit_code: int,
    ) -> BatchOutcome:
        invocation.ended_at = store.timestamp()
        invocation.status, invocation.stop_reason, invocation.detail = status, reason, detail
        persist(status, reason)
        return BatchOutcome(status, reason, detail, exit_code, tuple(cases))

    def save_case(position: int, updated: CaseRecord) -> None:
        cases[position] = updated
        persist("running", None)

    persist("running", None)
    count = len(manifest.cases)
    for index, spec in enumerate(manifest.cases):
        record = cases[index]
        if record.status == "complete":
            continue
        if record.status == "invalid" and not (
            retry_interrupted and record.reason in RETRYABLE_REASONS
        ):
            continue
        refusal = budget.refusal(spec, clock())
        if refusal is not None:
            store.append_attempt(
                {"event": "budget_stop", "case_id": spec.case_id, "detail": refusal}
            )
            return finish("budget_exhausted", "budget_exhausted", refusal, EXIT_TIMEOUT)
        source = sources[spec.version]
        try:
            cases[index] = record = _run_case(
                store,
                spec,
                record,
                source,
                limits,
                manifest,
                budget,
                launcher,
                run_root=run_root,
                environ=environ,
                python=python,
                observer=observer,
                index=index,
                count=count,
                save=functools.partial(save_case, index),
            )
            if observer is not None:
                observer.after_case(record, index, count)
        except KeyboardInterrupt:
            current = cases[index]
            if current.status == "running":  # launched: label it, keeping its run id
                cases[index] = current = replace(
                    current,
                    status="invalid",
                    result=None,
                    reason="interrupted",
                    detail="interrupted while the case was running or being recorded",
                    unrecorded_attempts=current.unrecorded_attempts + int(spec.hosted),
                )
                store.append_attempt(
                    {
                        "event": "interrupted",
                        "case_id": spec.case_id,
                        "attempt": current.attempts,
                        "run_id": current.run_id,
                    }
                )
            # A recorded case keeps its record; one never launched stays pending.
            return finish(
                "interrupted", "interrupted", f"Ctrl+C during {spec.case_id}", EXIT_STOPPED
            )
        if record.status == "invalid" and record.reason == "interrupted":
            return finish("interrupted", "interrupted", record.detail, EXIT_STOPPED)
        if record.status == "invalid" and record.reason in STOPPING_REASONS:
            return finish("stopped", record.reason, record.detail, EXIT_FAILURE)
    remaining = [c for c in cases if c.status != "complete"]
    if remaining:
        detail = f"{len(remaining)} case(s) not complete: " + ", ".join(
            f"{c.spec.case_id} ({c.reason or c.status})" for c in remaining[:6]
        )
        return finish("incomplete", remaining[0].reason or "incomplete", detail, EXIT_FAILURE)
    return finish("complete", None, None, EXIT_OK)


def _run_case(
    store: BatchStore,
    spec: CaseSpec,
    record: CaseRecord,
    source: ResolvedSource,
    limits: BenchmarkLimits,
    manifest: BatchManifest,
    budget: _Budget,
    launcher: ChildLauncher,
    *,
    run_root: Path,
    environ: Mapping[str, str],
    python: str,
    observer: CaseObserver | None,
    index: int,
    count: int,
    save: Callable[[CaseRecord], None],
) -> CaseRecord:
    attempt = record.attempts + 1
    directory = source.snapshot.directory
    if directory is None:
        raise ProvenanceMismatch(f"{spec.version} has no captured snapshot to run from")
    try:
        _expect_fingerprint(verify_snapshot(directory), source.snapshot.fingerprint)
    except ProvenanceMismatch as exc:
        failed = replace(
            record,
            status="invalid",
            reason="provenance_mismatch",
            detail=exc.message,
            attempts=attempt,
        )
        store.append_attempt(
            {
                "event": "snapshot_mismatch",
                "case_id": spec.case_id,
                "attempt": attempt,
                "detail": exc.message,
            }
        )
        return failed
    extra = observer.before_case(spec, index, count) if observer is not None else ()
    argv = child_argv(spec, limits, manifest.requested_model, run_root, python=python, extra=extra)
    stdout_path, stderr_path = store.log_paths(spec, attempt)
    running = replace(
        record,
        status="running",
        run_id=None,
        result=None,
        reason=None,
        detail=None,
        attempts=attempt,
        metrics={},
    )
    budget.games += 1
    # Persist before the match: a crash of this process leaves the case "running".
    store.append_attempt(
        {
            "event": "started",
            "case_id": spec.case_id,
            "attempt": attempt,
            "stdout_log": stdout_path.relative_to(store.batch_dir).as_posix(),
            "stderr_log": stderr_path.relative_to(store.batch_dir).as_posix(),
        }
    )
    save(running)
    current = [running]

    def started(pid: int) -> None:  # record the child at once (a resume checks it)
        current[0] = replace(current[0], child_pid=pid, child_started=_process_state(pid)[1])
        save(current[0])

    launch = ChildLaunch(
        argv=argv,
        cwd=directory,
        env=child_environment(
            environ,
            directory,
            hosted=spec.hosted,
            pycache_prefix=store.fresh_pycache_prefix(spec, attempt),
        ),
        hard_wall_seconds=child_hard_wall_seconds(limits, dashboard=LAUNCH_SESSION_FLAG in extra),
        stdout_path=stdout_path,
        stderr_path=stderr_path,
        on_started=started,
    )
    try:
        exit_info = launcher.run(launch)
    except KeyboardInterrupt:
        exit_info = ChildExit(None, 0.0, interrupted=True)
    status, result, run_id = parse_child_summary(stdout_path)
    running = current[0]
    if run_id is not None:  # an interrupt while scoring keeps the run attached
        running = replace(running, run_id=run_id)
        save(running)
    failure = None
    if observer is not None and not exit_info.interrupted:
        failure = observer.launch_failure(spec, index, count)
    if failure is not None:  # the dashboard never released this case: SC2 never started
        failed = replace(
            running,
            status="invalid",
            result=None,
            reason="launch_failed",
            detail=render_text(failure)[:2048],
        )
        store.append_attempt(
            {
                "event": "launch_failed",
                "case_id": spec.case_id,
                "attempt": attempt,
                "run_id": run_id,
                "exit_code": exit_info.returncode,
                "detail": failed.detail,
            }
        )
        save(failed)
        return failed
    expectation = RunExpectation(
        version=int(spec.version[1:]),
        entrypoint=spec.entrypoint,
        policy_hash=source.policy_hash,
        options=spec.match_options(limits, manifest.requested_model),
        expected_returned_model=manifest.expected_returned_model if spec.hosted else None,
        snapshot_dir=directory,
        require_diagnostics=True,
    )
    scored = score_run(
        run_root, run_id, expectation, ChildEvidence(exit_info, status, result, run_id)
    )
    findings = list(scored.findings)
    try:
        _expect_fingerprint(verify_snapshot(directory), source.snapshot.fingerprint)
    except ProvenanceMismatch as exc:  # modified while the case ran
        findings.append(Finding("provenance_mismatch", exc.message))
        scored = _scored(
            findings,
            scored.result,
            scored.reported_result,
            scored.run_id,
            scored.evidence,
            scored.metrics,
            scored.verified_calls,
        )
    charged = 0
    if spec.hosted:
        trustworthy = (
            scored.status == "complete"
            and scored.result in ("win", "loss", "draw", "timeout")
            and scored.verified_calls is not None
        )
        charged = (
            scored.verified_calls
            if trustworthy and scored.verified_calls is not None
            else limits.match_requests
        )
    budget.requests += charged
    spend = _attempt_spend(scored.metrics)
    finished = replace(
        running,
        status=scored.status,
        run_id=scored.run_id,
        result=scored.result,
        reason=scored.reason,
        detail=scored.detail,
        metrics=scored.metrics,
        # Every attempt's spend is kept: a retry adds to the case's total.
        spent={k: running.spent.get(k, 0) + (spend or {}).get(k, 0) for k in _SPEND_FIELDS},
        unrecorded_attempts=running.unrecorded_attempts + int(spec.hosted and spend is None),
    )
    store.append_attempt(
        {
            "event": "ended",
            "case_id": spec.case_id,
            "attempt": attempt,
            "run_id": scored.run_id,
            "status": scored.status,
            "result": scored.result,
            "reported_result": scored.reported_result,
            "reason": scored.reason,
            "exit_code": exit_info.returncode,
            "timed_out": exit_info.timed_out,
            "interrupted": exit_info.interrupted,
            "tree_terminated": exit_info.tree_terminated,
            "wall_seconds": round(exit_info.wall_seconds, 3),
            "charged_requests": charged,
            "evidence": scored.evidence,
            "spend": None if spend is None else dict(spend),
        }
    )
    save(finished)
    return finished


# ---------------------------------------------------------------------------
# Scorecard
# ---------------------------------------------------------------------------


def scorecard(cases: Sequence[CaseRecord]) -> dict[str, JsonValue]:
    """Counts by status, result and reason, per version and race (plan D6).

    Only ``complete`` cases have a result; an invalid case is counted under its
    reason and never as a win. Ties, timeouts and errors are reported separately.
    """
    statuses: Counter[str] = Counter(c.status for c in cases)
    results: Counter[str] = Counter(
        c.result for c in cases if c.status == "complete" and c.result is not None
    )
    reasons: Counter[str] = Counter(c.reason or "unknown" for c in cases if c.status == "invalid")
    by_version: dict[str, JsonValue] = {}
    by_race: dict[str, JsonValue] = {}
    for key, group in (("version", by_version), ("race", by_race)):
        values = sorted({getattr(c.spec, key) for c in cases})
        for value in values:
            members = [c for c in cases if getattr(c.spec, key) == value]
            group[str(value)] = {
                "cases": len(members),
                "complete": sum(1 for c in members if c.status == "complete"),
                "valid_wins": sum(1 for c in members if c.counted_as_win),
                **{
                    r: sum(1 for c in members if c.status == "complete" and c.result == r)
                    for r in CASE_RESULTS
                },
            }
    coverage: Counter[str] = Counter(
        _text(c.metrics.get("status"), "unavailable") for c in cases if c.status == "complete"
    )
    return {
        "cases": len(cases),
        "statuses": {s: statuses.get(s, 0) for s in CASE_STATUSES},
        "results": {r: results.get(r, 0) for r in CASE_RESULTS},
        "valid_wins": sum(1 for c in cases if c.counted_as_win),
        "invalid_reasons": dict(sorted(reasons.items())),
        "by_version": by_version,
        "by_race": by_race,
        "metric_coverage": dict(sorted(coverage.items())),
        "service": _service_spend(cases),
    }


def _text(value: object, default: str) -> str:
    return value if isinstance(value, str) else default


def _service_spend(cases: Sequence[CaseRecord]) -> dict[str, JsonValue]:
    """Recorded service spend of every attempt of every case, valid or not.

    The totals sum each case's ``spent``, which every attempt with a decision record
    adds to (an interrupted attempt that is retried still counts). ``complete`` is
    the part spent by the attempts that produced a complete case; ``other`` is the
    rest (invalid cases and superseded attempts). ``unrecorded_hosted_attempts``
    counts hosted attempts with unknown spend (the budget charged them the whole
    allowance). Probe answers are listed with each invocation in ``results.json``.
    """
    totals = dict.fromkeys(_SPEND_FIELDS, 0)
    complete = dict.fromkeys(_SPEND_FIELDS, 0)
    for case in cases:
        for name in _SPEND_FIELDS:
            totals[name] += case.spent.get(name, 0)
        latest = _attempt_spend(case.metrics) if case.status == "complete" else None
        for name, value in (latest or {}).items():
            complete[name] += value
    spend: dict[str, JsonValue] = dict(totals)
    spend["complete"] = dict(complete)
    spend["other"] = {name: totals[name] - complete[name] for name in _SPEND_FIELDS}
    spend["unrecorded_hosted_attempts"] = sum(case.unrecorded_attempts for case in cases)
    spend["note"] = "token counts only; no currency cost is claimed"
    return spend


# ---------------------------------------------------------------------------
# Dry run
# ---------------------------------------------------------------------------


def _planned_environment(
    case: CaseSpec, directory: Path, benchmark_root: Path
) -> dict[str, JsonValue]:
    """The variables :func:`child_environment` sets for this case, the key redacted.

    Derived from the production function itself (with a stand-in key), so the
    printed plan cannot drift from what a real run launches.
    """
    marker = "<inherited from the benchmark's environment>"
    prefix = (
        benchmark_root / "<batch_id>" / ATTEMPT_LOGS_DIR / f"{case.case_id}.<n>.<random>.pycache"
    )
    env = child_environment({KEY_ENV: marker}, directory, hosted=case.hosted, pycache_prefix=prefix)
    planned: dict[str, JsonValue] = dict(sorted(env.items()))
    planned.setdefault(KEY_ENV, "<removed>")
    return planned


def dry_run_plan(
    panel: str,
    sources: Mapping[str, ResolvedSource],
    limits: BenchmarkLimits,
    *,
    run_root: Path,
    benchmark_root: Path,
    python: str,
    dashboard: bool = True,
) -> dict[str, JsonValue]:
    """What a real invocation would run, exactly -- without any call, launch or write.

    ``dashboard`` is the invocation's launch mode: dashboard-first (each child joins
    the session the real run creates) or headless (``--no-dashboard``).
    """
    baselines = benchmark_root / BASELINES_DIR
    cases = panel_cases(panel)
    session_placeholder = "<session_id assigned at start>"
    extra = (LAUNCH_SESSION_FLAG, session_placeholder) if dashboard else ()
    planned: list[JsonValue] = []
    for case in cases:
        source = sources[case.version]
        directory = source.snapshot.directory or baselines / source.snapshot.name
        argv = child_argv(case, limits, PINNED_MODEL, run_root, python=python, extra=extra)
        planned.append(
            {
                **case.to_dict(),
                "entrypoint": case.entrypoint,
                "policy_hash": source.policy_hash,
                "source_fingerprint": source.snapshot.fingerprint,
                "options": match_options_record(case.match_options(limits, PINNED_MODEL)),
                "argv": list(argv),
                "cwd": str(directory),
                "env": _planned_environment(case, directory, benchmark_root),
                "hard_wall_seconds": child_hard_wall_seconds(limits, dashboard=dashboard),
            }
        )
    hosted = any(case.hosted for case in cases)
    notes: list[JsonValue] = [
        "dry run: no service call, no SC2 launch, no dashboard, nothing written",
    ]
    if dashboard:
        notes.append(
            f"a real run opens the dashboard first, at {DASHBOARD_URL}/?tab=jev&launch="
            "<session_id>, and starts each game only after that page rendered its exact "
            "run (--no-dashboard runs headless)"
        )
    else:
        notes.append("--no-dashboard: a real run plays headless; no dashboard is opened")
    if hosted:
        notes.append(
            f"real preflight requires {KEY_ENV} in the environment (not inspected by a dry run) "
            f"and sends one tiny availability probe for {PINNED_MODEL} before the first match"
        )
    for source in sources.values():
        if source.state == "capture_pending":
            notes.append(
                f"{source.version}: a real run would capture {len(source.snapshot.files)} files "
                f"into {baselines / source.snapshot.name}"
                + (" and finalize it as the frozen baseline" if panel == "baseline" else "")
            )
    if panel in OFFICIAL_PANELS and not OFFICIAL_PANEL_PLAY_ENABLED:
        notes.append(
            "playing this panel (and finalizing the v1 baseline) waits for Step 224's "
            "dashboard-first launch; the real baseline panel is Step 213"
        )
    return {
        "schema_version": SCHEMA_VERSION,
        "dry_run": True,
        "panel": panel,
        "launch": "dashboard" if dashboard else "headless",
        "claim": PANEL_CLAIMS[panel],
        "requested_model": PINNED_MODEL,
        "expected_returned_model": EXPECTED_RETURNED_MODEL,
        "limits": limits.to_dict(),
        "source_fingerprint": _batch_fingerprint(
            {v: s.snapshot.fingerprint for v, s in sources.items()}
        ),
        "sources": {v: s.to_dict(baselines) for v, s in sources.items()},
        "run_root": str(run_root),
        "batch_root": str(benchmark_root / "<batch_id assigned at start>"),
        "python": python,
        "cases": planned,
        "notes": notes,
    }


# ---------------------------------------------------------------------------
# Command line
# ---------------------------------------------------------------------------


def _limit_type(name: str) -> Callable[[str], int]:
    ceiling = LIMIT_CEILINGS[name]

    def parse(text: str) -> int:
        try:
            value = int(text)
        except ValueError:
            value = 0
        if not 1 <= value <= ceiling:
            raise argparse.ArgumentTypeError(
                f"expected an integer in 1..{ceiling} (limits may only be lowered), "
                f"got {safe_repr(text)}"
            )
        return value

    return parse


_LIMIT_FLAGS: Final[Mapping[str, str]] = {
    "max_games": "--max-games",
    "invocation_wall_seconds": "--invocation-wall-seconds",
    "invocation_requests": "--invocation-requests",
    "match_game_seconds": "--max-game-seconds",
    "match_wall_seconds": "--max-wall-seconds",
    "match_requests": "--max-requests",
}


def build_parser() -> argparse.ArgumentParser:
    parser = TerminalSafeArgumentParser(
        prog="scripts/benchmark_jev.py",
        description="Sequential, resumable Jev benchmarks through the production runner.",
    )
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--panel", choices=PANELS, help="start a new batch of this panel")
    mode.add_argument("--resume", metavar="BATCH_ID", help="resume an existing batch")
    mode.add_argument("--report", metavar="BATCH_ID", help="print a batch's scorecard")
    mode.add_argument(
        "--calibrate-run",
        type=absolute_path,
        metavar="RUN_DIR",
        help="score an existing run archive against --expect-* (no SC2, no service)",
    )
    mode.add_argument(
        "--freeze-candidate",
        metavar="VERSION",
        help="capture a candidate version's current source (e.g. v2) and finalize it as "
        "its frozen source for the held-out and attribution panels (write-once)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="resolve and print the exact plan; no call, launch or write",
    )
    parser.add_argument(
        "--retry-interrupted",
        action="store_true",
        help="with --resume: replay cases labeled interrupted or launch_failed",
    )
    parser.add_argument(
        "--no-dashboard",
        action="store_true",
        help="play headless: open no dashboard and start each game without waiting for it "
        "(default: dashboard first, plan D7)",
    )
    for name, flag in _LIMIT_FLAGS.items():
        parser.add_argument(
            flag,
            dest=name,
            type=_limit_type(name),
            default=None,
            help=f"lower the limit (mandatory maximum {LIMIT_CEILINGS[name]})",
        )
    parser.add_argument(
        "--run-root",
        type=absolute_path,
        default=None,
        help="absolute run root (default: <repository>/data/jev/runs)",
    )
    parser.add_argument(
        "--benchmark-root",
        type=absolute_path,
        default=None,
        help="absolute benchmark root (default: <repository>/data/jev/benchmarks)",
    )
    parser.add_argument(
        "--json",
        type=absolute_path,
        default=None,
        dest="json_path",
        help="also write the report as JSON to this absolute path",
    )
    expect = parser.add_argument_group("calibration expectation (--calibrate-run)")
    expect.add_argument(
        "--expect-version", default=BASELINE_VERSION, help="Jev version (default: %(default)s)"
    )
    expect.add_argument(
        "--expect-provider",
        choices=PROVIDERS,
        default="typesafe",
        help="decision provider the run must have used (default: %(default)s)",
    )
    expect.add_argument("--expect-map", default=MAP, help="map (default: %(default)s)")
    expect.add_argument(
        "--expect-race", choices=OPPONENT_RACES, help="opponent race (required for calibration)"
    )
    expect.add_argument(
        "--expect-difficulty", type=int, help="opponent difficulty (required for calibration)"
    )
    expect.add_argument("--expect-seed", type=int, help="game seed (required for calibration)")
    expect.add_argument(
        "--expect-requested-model",
        default=PINNED_MODEL,
        help="model the run must have requested (default: the pin, %(default)s)",
    )
    expect.add_argument(
        "--expect-returned-model",
        default=EXPECTED_RETURNED_MODEL,
        help="model every recorded answer must name (default: %(default)s)",
    )
    expect.add_argument(
        "--expect-max-requests",
        type=int,
        default=LIMIT_CEILINGS["match_requests"],
        help="per-match request cap (default: %(default)s)",
    )
    expect.add_argument(
        "--expect-max-game-seconds",
        type=int,
        default=LIMIT_CEILINGS["match_game_seconds"],
        help="game-time limit (default: %(default)s)",
    )
    expect.add_argument(
        "--expect-max-wall-seconds",
        type=int,
        default=BenchmarkLimits().child_wall_seconds,
        help="the run's own wall-clock limit (default: %(default)s, the benchmark child's)",
    )
    expect.add_argument(
        "--expect-realtime",
        action="store_true",
        help="the run must have been realtime (checked when the archive has diagnostics)",
    )
    expect.add_argument(
        "--expect-policy-hash",
        default=None,
        help="default: the current package policy hash of --expect-version",
    )
    expect.add_argument(
        "--require-diagnostics",
        action="store_true",
        help="score an archive without diagnostics as invalid",
    )
    return parser


@dataclass
class _Context:
    source_root: Path
    benchmark_root: Path
    run_root: Path
    environ: Mapping[str, str]
    launcher: ChildLauncher
    provider_factory: ProviderFactory
    process_probe: ProcessProbe
    python: str
    clock: Callable[[], float]
    wall_time: Callable[[], float]
    observer: CaseObserver | None
    #: Opens the dashboard-first launch for a given case count (None: headless).
    dashboard: DashboardFactory | None


#: Opens one invocation's dashboard-first launch for its case count; raises
#: :class:`jev.launch.DashboardUnavailable` when the dashboard cannot be used.
DashboardFactory = Callable[[int], CaseObserver]


def _default_dashboard(
    source_root: Path, run_root: Path, environ: Mapping[str, str]
) -> DashboardFactory:
    def open_dashboard(case_count: int) -> CaseObserver:
        launch = DashboardLaunch.open(
            run_root,
            case_count=case_count,
            environ=environ,
            source_root=source_root,
            out=_print,
        )
        return DashboardObserver(launch)

    return open_dashboard


def _open_observer(context: _Context, case_count: int) -> CaseObserver | None:
    """The invocation's observer, opened before anything is probed, captured or played.

    Dashboard mode never falls back to headless: a dashboard that cannot be used
    stops the invocation with ``dashboard_unavailable``.
    """
    if context.observer is not None:
        return context.observer
    if context.dashboard is None:
        return None
    try:
        return context.dashboard(case_count)
    except DashboardUnavailable as exc:
        raise BenchmarkError(
            "dashboard_unavailable",
            f"{exc.message} (nothing was played; --no-dashboard runs headless)",
        ) from exc


def _close_observer(observer: CaseObserver | None, exc: BaseException) -> None:
    """The invocation ended before any outcome: close its launch session visibly."""
    if observer is None:
        return
    interrupted = isinstance(exc, KeyboardInterrupt)
    if isinstance(exc, BenchmarkError):
        detail = f"{exc.code}: {exc.message}"
    elif interrupted:
        detail = "interrupted with Ctrl+C; no game was running"
    else:
        detail = f"stopped: {safe_exception_text(exc)}"
    with contextlib.suppress(Exception):
        observer.finish(None, detail, interrupted=interrupted)


def _print(text: str, *, error: bool = False) -> None:
    print(render_lines(text), file=sys.stderr if error else sys.stdout, flush=True)


def _write_json(path: Path | None, document: Mapping[str, JsonValue]) -> bool:
    if path is None:
        return True
    try:
        EvidenceFiles().write(path, _encode(document))
    except OSError as exc:
        _print(f"jev benchmark: cannot write {path.name}: {safe_exception_text(exc)}", error=True)
        return False
    return True


def _requested_limits(args: argparse.Namespace) -> dict[str, int]:
    return {name: getattr(args, name) for name in LIMIT_CEILINGS if getattr(args, name) is not None}


def main(
    argv: list[str] | None = None,
    *,
    source_root: Path | None = None,
    environ: Mapping[str, str] | None = None,
    launcher: ChildLauncher | None = None,
    provider_factory: ProviderFactory = _typesafe_provider,
    process_probe: ProcessProbe | None = None,
    python: str = sys.executable,
    clock: Callable[[], float] = time.monotonic,
    wall_time: Callable[[], float] = time.time,
    observer: CaseObserver | None = None,
    dashboard: DashboardFactory | None = None,
) -> int:
    """The ``scripts/benchmark_jev.py`` command; returns the exit code.

    A real ``--panel``/``--resume`` run is dashboard-first unless ``--no-dashboard``:
    ``dashboard`` (default: :class:`DashboardObserver` over the local dashboard)
    opens it; an explicit ``observer`` replaces both (tests).

    Exit codes: 0 batch complete / dry run resolved / calibration valid; 1 failure,
    invalid or stopped batch; 2 usage error; 3 invocation budget exhausted (resume
    later); 130 interrupted.
    """
    make_streams_encoding_safe()
    parser = build_parser()
    args = parser.parse_args(argv)
    root = repository_root() if source_root is None else source_root
    run_root = (
        (default_run_root() if source_root is None else root.joinpath(*RUN_ROOT_PARTS))
        if args.run_root is None
        else args.run_root
    )
    chosen_environ = os.environ if environ is None else environ
    factory: DashboardFactory | None = None
    if not args.no_dashboard:
        factory = dashboard or _default_dashboard(root, run_root, chosen_environ)
    context = _Context(
        source_root=root,
        benchmark_root=(
            root.joinpath(*BENCHMARK_ROOT_PARTS)
            if args.benchmark_root is None
            else args.benchmark_root
        ),
        run_root=run_root,
        environ=chosen_environ,
        launcher=SubprocessLauncher(clock=clock) if launcher is None else launcher,
        provider_factory=provider_factory,
        process_probe=SystemProcessProbe() if process_probe is None else process_probe,
        python=python,
        clock=clock,
        wall_time=wall_time,
        observer=observer,
        dashboard=factory,
    )
    if args.retry_interrupted and args.resume is None:
        parser.error("--retry-interrupted requires --resume")
    if args.no_dashboard and args.panel is None and args.resume is None:
        parser.error("--no-dashboard applies to --panel and --resume")
    if args.dry_run and args.panel is None and args.resume is None:
        parser.error("--dry-run applies to --panel and --resume")
    try:
        if args.calibrate_run is not None:
            return _calibrate(args, parser, context)
        if args.freeze_candidate is not None:
            return _freeze(str(args.freeze_candidate), context)
        if args.report is not None:
            return _report(args.report, context, args.json_path)
        return _run(args, context)
    except BenchmarkError as exc:
        _print(f"jev benchmark: {exc.code}: {exc.message}", error=True)
        return EXIT_USAGE if exc.code == "usage" else EXIT_FAILURE
    except KeyboardInterrupt:  # outside a case: nothing was running; records stay as written
        _print("jev benchmark: interrupted", error=True)
        return EXIT_STOPPED


def _run(args: argparse.Namespace, context: _Context) -> int:
    baselines = context.benchmark_root / BASELINES_DIR
    requested = _requested_limits(args)
    if args.resume is not None:
        return _resume(args, context, requested)
    panel = _Doc("--panel", {"panel": args.panel}).choice("panel", PANELS)  # argparse choices
    try:
        limits = BenchmarkLimits(**requested)
    except ValueError as exc:
        raise BenchmarkError("usage", str(exc)) from exc
    if args.dry_run:
        try:
            sources = resolve_sources(
                panel, source_root=context.source_root, baselines_dir=baselines, capture=False
            )
        except BenchmarkError as exc:
            _print(f"jev benchmark dry-run: panel={panel} UNRESOLVED", error=False)
            _print(f"jev benchmark: {exc.code}: {exc.message}", error=True)
            return EXIT_FAILURE
        plan = dry_run_plan(
            panel,
            sources,
            limits,
            run_root=context.run_root,
            benchmark_root=context.benchmark_root,
            python=context.python,
            dashboard=not args.no_dashboard,
        )
        _print_plan(plan)
        return EXIT_OK if _write_json(args.json_path, plan) else EXIT_FAILURE
    if panel in OFFICIAL_PANELS and not OFFICIAL_PANEL_PLAY_ENABLED:
        raise BenchmarkError(
            "launch_integration_pending",
            f"the {panel} panel plays only after Step 224's dashboard-first launch (the "
            "frozen v1 baseline must include its launch hook); use --dry-run, or "
            "--panel staging for a substrate check",
        )
    cases = panel_cases(panel)
    hosted = any(case.hosted for case in cases)
    key = _service_key(context.environ) if hosted else None
    # Fail fast without writing anything; open the dashboard (plan D7: before anything
    # is spent or frozen); then spend the one probe request, and only then capture
    # (and, for the baseline, finalize) the frozen source -- so the finalized v1
    # baseline always includes the launch hook this build runs with.
    resolve_sources(panel, source_root=context.source_root, baselines_dir=baselines, capture=False)
    observer = _open_observer(context, len(cases))
    try:
        probe = _probe_or_raise(key, context) if key is not None else None
        sources = resolve_sources(
            panel,
            source_root=context.source_root,
            baselines_dir=baselines,
            capture=True,
            now=context.wall_time,
        )
        if panel == "baseline":
            finalize_baseline(baselines, sources["v1"].snapshot, now=context.wall_time)
            sources["v1"] = replace(sources["v1"], state="finalized")
        manifest = build_manifest(
            panel,
            sources,
            limits,
            batch_id=uuid.uuid4().hex,
            source_commit=read_source_commit(context.source_root),
            created_at=utc_timestamp(context.wall_time()),
        )
        store = BatchStore.create(
            context.benchmark_root,
            manifest,
            probe=context.process_probe,
            wall_time=context.wall_time,
        )
    except BaseException as exc:
        _close_observer(observer, exc)
        raise
    _print(
        f"jev benchmark: batch {manifest.batch_id} panel={panel} cases={len(cases)} "
        f"dir={store.batch_dir}"
    )
    if probe is not None:
        store.append_attempt({"event": "probe", "case_id": None, **probe.to_dict()})
    return _play(store, sources, context, args, probe, observer)


def _freeze(version: str, context: _Context) -> int:
    if not full_match(_VERSION_RE, version):
        raise BenchmarkError("usage", f"not a Jev version name: {safe_repr(version)}")
    frozen = freeze_candidate(
        context.source_root, version, context.benchmark_root / BASELINES_DIR, now=context.wall_time
    )
    _print(
        f"jev benchmark: froze {version}: fingerprint={frozen.snapshot.fingerprint} "
        f"policy_hash={frozen.policy_hash} files={len(frozen.snapshot.files)} "
        f"snapshot={frozen.snapshot.directory}"
    )
    return EXIT_OK


def _service_key(environ: Mapping[str, str]) -> str:
    key = environ.get(KEY_ENV, "")
    if not key.strip():
        raise BenchmarkError(
            "missing_configuration",
            f"{KEY_ENV} is required for a hosted panel (no scripted fallback is substituted)",
        )
    return key


def _probe_or_raise(key: str, context: _Context) -> ProbeResult:
    result = probe_model(key, factory=context.provider_factory, clock=context.clock)
    if not result.ok:
        raise BenchmarkError(result.reason or "model_unavailable", result.detail)
    _print(f"jev benchmark: probe ok: model={result.returned_model} latency_ms={result.latency_ms}")
    return result


def _resume(args: argparse.Namespace, context: _Context, requested: Mapping[str, int]) -> int:
    batch_dir = context.benchmark_root / str(args.resume)
    if not is_valid_run_id(str(args.resume)) or not batch_dir.is_dir():
        raise BenchmarkError("usage", f"no batch {safe_repr(args.resume)} to resume")
    manifest, _ = read_manifest(batch_dir)
    _check_resume_options(manifest, requested)
    if manifest.panel in OFFICIAL_PANELS and not OFFICIAL_PANEL_PLAY_ENABLED:
        raise BenchmarkError(
            "launch_integration_pending", f"the {manifest.panel} panel plays only after Step 224"
        )
    sources = _manifest_sources(manifest, context.benchmark_root / BASELINES_DIR)
    if args.dry_run:
        plan = dry_run_plan(
            manifest.panel,
            sources,
            manifest.limits,
            run_root=context.run_root,
            benchmark_root=context.benchmark_root,
            python=context.python,
            dashboard=not args.no_dashboard,
        )
        plan["resume"] = manifest.batch_id
        _print_plan(plan)
        return EXIT_OK if _write_json(args.json_path, plan) else EXIT_FAILURE
    hosted = any(case.hosted for case in manifest.cases)
    key = _service_key(context.environ) if hosted else None
    store = BatchStore.open(
        context.benchmark_root,
        manifest.batch_id,
        probe=context.process_probe,
        wall_time=context.wall_time,
    )
    observer: CaseObserver | None = None
    try:
        if store.manifest != manifest:
            raise ProvenanceMismatch(f"{MANIFEST_FILE} changed while resuming")
        observer = _open_observer(context, len(manifest.cases))
        probe = _probe_or_raise(key, context) if key is not None else None
        if probe is not None:
            store.append_attempt({"event": "probe", "case_id": None, **probe.to_dict()})
    except BenchmarkError as exc:
        store.append_attempt(
            {
                "event": "preflight_failed",
                "case_id": None,
                "reason": exc.code,
                "detail": exc.message,
            }
        )
        store.release()
        _close_observer(observer, exc)
        raise
    except BaseException as exc:
        store.release()
        _close_observer(observer, exc)
        raise
    return _play(store, sources, context, args, probe, observer)


def _check_resume_options(manifest: BatchManifest, requested: Mapping[str, int]) -> None:
    """A resume runs the original batch: same panel definition, model and limits."""
    if manifest.requested_model != PINNED_MODEL or (
        manifest.expected_returned_model != EXPECTED_RETURNED_MODEL
    ):
        raise ProvenanceMismatch(
            f"batch {manifest.batch_id} pinned {manifest.requested_model!r}; this benchmark "
            f"pins {PINNED_MODEL!r} -- a new model requires a new batch"
        )
    try:
        defined = panel_cases(manifest.panel)
    except ValueError as exc:
        raise ProvenanceMismatch(str(exc)) from exc
    if defined != manifest.cases:
        raise ProvenanceMismatch(
            f"the {manifest.panel} panel definition changed; start a new batch"
        )
    for name, value in requested.items():
        if getattr(manifest.limits, name) != value:
            raise ProvenanceMismatch(
                f"{_LIMIT_FLAGS[name]} {value} differs from the batch's "
                f"{getattr(manifest.limits, name)}; new options require a new batch"
            )


def _manifest_sources(manifest: BatchManifest, baselines: Path) -> dict[str, ResolvedSource]:
    """Re-verify every snapshot the manifest names (resume requires the same fingerprint)."""
    resolved: dict[str, ResolvedSource] = {}
    for version, entry in manifest.sources.items():
        name = entry.get("snapshot")
        fingerprint = entry.get("fingerprint")
        if not isinstance(name, str) or not isinstance(fingerprint, str):
            raise CorruptRecord(f"{MANIFEST_FILE} source {version} is malformed")
        snapshot = _expect_fingerprint(verify_snapshot(baselines / name), fingerprint)
        if entry.get("state") == "finalized" and read_finalized(baselines, version) != fingerprint:
            raise ProvenanceMismatch(f"the finalized {version} baseline is no longer {fingerprint}")
        bundle = _load_policy(baselines / name, version)
        if bundle.policy_hash != manifest.policy_hashes.get(version):
            raise ProvenanceMismatch(f"{version} policy hash differs from the manifest")
        resolved[version] = ResolvedSource(
            version=version,
            entrypoint=f"bots.jev.{version}",
            policy_hash=bundle.policy_hash,
            snapshot=snapshot,
            state=_Doc(MANIFEST_FILE, dict(entry)).choice("state", ("finalized", "captured")),
        )
    combined = _batch_fingerprint({v: s.snapshot.fingerprint for v, s in resolved.items()})
    if combined != manifest.source_fingerprint or set(resolved) != set(
        panel_versions(manifest.cases)
    ):
        raise ProvenanceMismatch("the batch source fingerprint does not verify")
    return resolved


def _play(
    store: BatchStore,
    sources: Mapping[str, ResolvedSource],
    context: _Context,
    args: argparse.Namespace,
    probe: ProbeResult | None,
    observer: CaseObserver | None,
) -> int:
    try:
        outcome = _play_locked(store, sources, context, args, probe, observer)
    except BaseException as exc:
        _close_observer(observer, exc)
        raise
    finally:
        store.release()
    if observer is not None:
        with contextlib.suppress(Exception):
            observer.finish(outcome, outcome.detail)
    card = scorecard(outcome.cases)
    for case in outcome.cases:
        _print(
            f"  {case.spec.case_id}: {case.status} result={case.result} "
            f"reason={case.reason} run_id={case.run_id}"
        )
    _print(
        f"jev benchmark {outcome.status}: batch={store.manifest.batch_id} "
        f"valid_wins={card['valid_wins']} results={json.dumps(card['results'])} "
        f"invalid={json.dumps(card['invalid_reasons'])}"
    )
    if outcome.detail:
        _print(
            f"jev benchmark: {outcome.stop_reason}: {outcome.detail}",
            error=outcome.exit_code != EXIT_OK,
        )
    report: dict[str, JsonValue] = {
        "batch_id": store.manifest.batch_id,
        "status": outcome.status,
        "stop_reason": outcome.stop_reason,
        "scorecard": card,
    }
    if not _write_json(args.json_path, report):
        return EXIT_FAILURE
    return outcome.exit_code


def _play_locked(
    store: BatchStore,
    sources: Mapping[str, ResolvedSource],
    context: _Context,
    args: argparse.Namespace,
    probe: ProbeResult | None,
    observer: CaseObserver | None,
) -> BatchOutcome:
    return run_batch(
        store,
        sources,
        launcher=context.launcher,
        run_root=context.run_root,
        environ=context.environ,
        python=context.python,
        retry_interrupted=bool(args.retry_interrupted),
        probe_requests=0 if probe is None else 1,
        probe=probe,
        observer=observer,
        clock=context.clock,
        process_probe=context.process_probe,
    )


def _report(batch_id: str, context: _Context, json_path: Path | None) -> int:
    if not is_valid_run_id(batch_id):
        raise BenchmarkError("usage", f"not a batch id: {safe_repr(batch_id)}")
    batch_dir = context.benchmark_root / batch_id
    manifest, digest = read_manifest(batch_dir)
    results = read_results(batch_dir, manifest, digest)
    cases = _cases_from(results, manifest)
    card = scorecard(cases)
    _print(
        f"jev benchmark report: batch={batch_id} panel={manifest.panel} "
        f"status={results.text('status')}"
    )
    for case in cases:
        _print(
            f"  {case.spec.case_id}: {case.status} result={case.result} "
            f"reason={case.reason} run_id={case.run_id}"
        )
    _print(f"  scorecard: {json.dumps(card, sort_keys=True)}")
    return (
        EXIT_OK
        if _write_json(json_path, {"batch_id": batch_id, "scorecard": card})
        else EXIT_FAILURE
    )


def _print_plan(plan: Mapping[str, JsonValue]) -> None:
    _print(f"jev benchmark dry-run: panel={plan['panel']} (no service call, no launch, no write)")
    _print(f"  claim: {plan['claim']}")
    _print(
        f"  model: requested {plan['requested_model']}, every answer must be "
        f"{plan['expected_returned_model']}"
    )
    _print(f"  limits: {json.dumps(plan['limits'], sort_keys=True)}")
    _print(f"  source fingerprint: {plan['source_fingerprint']}")
    sources = plan["sources"]
    if isinstance(sources, dict):
        for version, source in sources.items():
            if isinstance(source, dict):
                _print(
                    f"  {version}: entrypoint={source['entrypoint']} "
                    f"policy_hash={source['policy_hash']} state={source['state']} "
                    f"fingerprint={source['fingerprint']} files={source['files']}"
                )
                _print(f"    snapshot: {source['snapshot_dir']}")
    _print(f"  run root: {plan['run_root']}")
    _print(f"  launch: {plan['launch']}")
    cases = plan["cases"]
    if isinstance(cases, list):
        for index, case in enumerate(cases, 1):
            if isinstance(case, dict):
                _print(f"  case {index}/{len(cases)} {case['case_id']}:")
                argv = case["argv"]
                if isinstance(argv, list):
                    _print("    " + subprocess.list2cmdline([str(a) for a in argv]))
                _print(f"    cwd: {case['cwd']}")
    notes = plan["notes"]
    if isinstance(notes, list):
        for note in notes:
            _print(f"  note: {note}")


def _calibrate(args: argparse.Namespace, parser: argparse.ArgumentParser, context: _Context) -> int:
    run_dir: Path = args.calibrate_run
    if not is_valid_run_id(run_dir.name):
        parser.error("--calibrate-run must name a run directory (<run root>/<run_id>)")
    missing = [
        flag
        for flag, value in (
            ("--expect-race", args.expect_race),
            ("--expect-difficulty", args.expect_difficulty),
            ("--expect-seed", args.expect_seed),
        )
        if value is None
    ]
    if missing:
        parser.error(f"--calibrate-run requires {', '.join(missing)}")
    version: str = args.expect_version
    if not full_match(_VERSION_RE, version):
        parser.error("--expect-version must look like v1")
    policy_hash = args.expect_policy_hash
    if policy_hash is None:
        policy_hash = _load_policy(context.source_root, version).policy_hash
    try:
        options = MatchOptions(
            map_name=args.expect_map,
            opponent_race=args.expect_race,
            difficulty=args.expect_difficulty,
            seed=args.expect_seed,
            max_game_seconds=args.expect_max_game_seconds,
            max_wall_seconds=args.expect_max_wall_seconds,
            realtime=bool(args.expect_realtime),
            decision_provider=args.expect_provider,
            decision_model=args.expect_requested_model,
            decision_max_requests=args.expect_max_requests,
        )
    except ValueError as exc:
        parser.error(str(exc))
    expectation = RunExpectation(
        version=int(version[1:]),
        entrypoint=f"bots.jev.{version}",
        policy_hash=policy_hash,
        options=options,
        expected_returned_model=(
            args.expect_returned_model if args.expect_provider == "typesafe" else None
        ),
        snapshot_dir=None,
        require_diagnostics=bool(args.require_diagnostics),
    )
    scored = score_run(run_dir.parent, run_dir.name, expectation)
    verdict = "VALID" if scored.status == "complete" else "INVALID"
    _print(
        f"jev benchmark calibration: {verdict} run_id={scored.run_id} "
        f"result={scored.result} reported_result={scored.reported_result} "
        f"counted_as_win={scored.counted_as_win} reason={scored.reason} "
        f"evidence={scored.evidence}"
    )
    for finding in scored.findings:
        _print(f"  FAIL {finding.code}: {finding.message}")
    decisions = scored.metrics.get("decisions")
    if isinstance(decisions, dict):
        _print(f"  decisions: {json.dumps(decisions, sort_keys=True)}")
    document = scored.to_dict()
    document["expectation"] = {
        "version": version,
        "policy_hash": policy_hash,
        "options": match_options_record(options),
        "expected_returned_model": expectation.expected_returned_model,
        "require_diagnostics": expectation.require_diagnostics,
    }
    if not _write_json(args.json_path, document):
        return EXIT_FAILURE
    return EXIT_OK if scored.status == "complete" else EXIT_FAILURE
