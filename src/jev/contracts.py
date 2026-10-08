"""Typed Jev records (schema version 1).

These are the shapes shared by the policy loader, the interpreter, and (in later
Phase JV steps) the SC2 adapter, telemetry writer and read-only API. Every record
here is a frozen dataclass; the JSON wire forms are produced by ``to_dict`` /
``to_document`` so no consumer hand-authors a second schema.

Wire conventions (plan section 5):

* ``schema_version`` is the integer ``1`` on every top-level document.
* Unit tags are Python ``int`` in memory and decimal *strings* on the wire, so a
  JavaScript consumer never loses precision on a 64-bit tag.
* A task/command target is either an entity tag or an ``[x, y]`` point.

Observation additions (beyond the plan's field list, both required by D3):

* ``mineral_fields`` -- visible mineral patches; D3's worker assignment
  ("visible nearby patches, two-worker target per patch") needs them.
* ``map_center`` -- playable-area centre; D3's army gathers "eight units toward
  map center". It is map metadata, like the start/expansion locations.

Binding rule (the ONE model shared by validator and interpreter): selection
bindings are root-local and live for one root evaluation in one tick. A node's
binding effects commit only if the node SUCCEEDS; a node that fails -- a select
that finds nothing, or a sequence/selector that fails after children re-bound
names -- leaves the bindings exactly as it found them. ``jev.runtime`` enforces
this by snapshot/restore around every node evaluation, and the validator's
definite-assignment analysis (``jev.policy``) computes success-only effects
under the same rule, so a reference the validator accepts is always bound, with
a kind the consuming argument accepts, when the interpreter evaluates it.
"""

from __future__ import annotations

import itertools
import math
import re
import uuid
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Final, Literal, NoReturn, get_args

__all__ = [
    "ACTIVE_TASK_STATUSES",
    "COMPOSITE_KINDS",
    "CommandSpec",
    "ENTITY_COLLECTIONS",
    "Entity",
    "EntityCollection",
    "ErrorCode",
    "Event",
    "EventKind",
    "EventStatus",
    "FAMILY",
    "FrozenJsonDict",
    "FrozenJsonList",
    "JevError",
    "JsonScalar",
    "JsonValue",
    "LATCHES",
    "LEAF_KINDS",
    "LOCATION_SOURCES",
    "Latch",
    "LocationSource",
    "MAX_ABS_NUMBER",
    "MAX_COORDINATE",
    "MAX_DOCUMENT_ELEMENTS",
    "MAX_ENTITY_ORDERS",
    "MAX_FRAGMENT_CHARS",
    "MAX_MESSAGE_CHARS",
    "MAX_OBSERVED_ENTITIES",
    "MAX_OBSERVED_LOCATIONS",
    "MAX_REPORTED_ISSUES",
    "MAX_TAG",
    "Manifest",
    "NAME_RE",
    "NODE_ID_MAX_LENGTH",
    "NODE_ID_RE",
    "NODE_KINDS",
    "NODE_STATUSES",
    "NodeKind",
    "NodeStatus",
    "Observation",
    "Order",
    "POINT_KEYWORDS",
    "Point",
    "PointKeyword",
    "Policy",
    "PolicyError",
    "PolicyIssue",
    "PolicyNode",
    "RUN_RESULTS",
    "RUN_STATUSES",
    "RunMetadata",
    "RunResult",
    "RunState",
    "RunStatus",
    "SCHEMA_VERSION",
    "TASK_STATUSES",
    "Target",
    "Task",
    "TaskStatus",
    "encode_point",
    "encode_tag",
    "encode_target",
    "freeze_json",
    "full_match",
    "is_bounded_number",
    "is_valid_run_id",
    "render_lines",
    "render_text",
    "safe_exception_text",
    "safe_repr",
    "thaw_json",
]

SCHEMA_VERSION: Final = 1
FAMILY: Final = "jev"

type JsonScalar = str | int | float | bool | None
type JsonValue = JsonScalar | list[JsonValue] | dict[str, JsonValue]
type Point = tuple[float, float]
type Target = int | Point | None

NodeKind = Literal["sequence", "selector", "condition", "select", "action", "wait"]
NodeStatus = Literal["success", "failure", "running"]
TaskStatus = Literal["pending", "issued", "running", "succeeded", "failed", "cancelled"]
EventKind = Literal["node", "command", "task", "diagnostic"]
EventStatus = Literal[
    "success",
    "failure",
    "running",
    "pending",
    "issued",
    "succeeded",
    "failed",
    "cancelled",
    "warning",
]
RunStatus = Literal["starting", "running", "finished", "stopped", "failed"]
RunResult = Literal["win", "loss", "draw", "timeout"]
ErrorCode = Literal[
    "invalid_policy",
    "invalid_run_id",
    "corrupt_run",
    "persistence_failed",
    "game_timeout",
    "wall_timeout",
    "sc2_unavailable",
    "match_crashed",
]
#: Observation fields holding entity records; the only collections a policy may query.
EntityCollection = Literal[
    "own_units",
    "own_structures",
    "visible_enemies",
    "remembered_enemy_structures",
    "mineral_fields",
]
#: Observation fields holding map-metadata point lists.
LocationSource = Literal["enemy_start_locations", "expansion_locations"]
#: Named single points a policy may reference directly.
PointKeyword = Literal["start_location", "map_center"]
#: The only cross-tick memory besides task state (plan section 5).
Latch = Literal["attack_launched"]
#: Largest magnitude any number crossing a Jev boundary may have (policy values,
#: observation numbers, config values). Checked with :func:`is_bounded_number`.
MAX_ABS_NUMBER: Final = 1e15
#: SC2 unit tags are unsigned 64-bit integers.
MAX_TAG: Final = 2**64 - 1
#: Largest |coordinate| an Observation point may carry (SC2 maps are < 256 units;
#: the bound keeps every geometric operation far from float overflow).
MAX_COORDINATE: Final = 1e6
#: Size caps for one Observation (far above any real SC2 state: 200 supply, ~100
#: mineral fields, ~20 expansions, a 5-deep production queue). They make every
#: per-tick pass bounded and give huge fan-out a documented ValueError.
MAX_OBSERVED_ENTITIES: Final = 4096
MAX_OBSERVED_LOCATIONS: Final = 256
MAX_ENTITY_ORDERS: Final = 64

NODE_KINDS: Final[tuple[NodeKind, ...]] = get_args(NodeKind)
NODE_STATUSES: Final[tuple[NodeStatus, ...]] = get_args(NodeStatus)
COMPOSITE_KINDS: Final[frozenset[NodeKind]] = frozenset({"sequence", "selector"})
LEAF_KINDS: Final[frozenset[NodeKind]] = frozenset(NODE_KINDS) - COMPOSITE_KINDS
TASK_STATUSES: Final[tuple[TaskStatus, ...]] = get_args(TaskStatus)
ACTIVE_TASK_STATUSES: Final[frozenset[TaskStatus]] = frozenset({"pending", "issued", "running"})
ENTITY_COLLECTIONS: Final[tuple[EntityCollection, ...]] = get_args(EntityCollection)
LOCATION_SOURCES: Final[tuple[LocationSource, ...]] = get_args(LocationSource)
POINT_KEYWORDS: Final[tuple[PointKeyword, ...]] = get_args(PointKeyword)
LATCHES: Final[tuple[Latch, ...]] = get_args(Latch)
RUN_STATUSES: Final[tuple[RunStatus, ...]] = get_args(RunStatus)
RUN_RESULTS: Final[tuple[RunResult, ...]] = get_args(RunResult)

# Shape regexes are anchored with \A...\Z (``$`` would accept a trailing newline)
# and are linear-time (no nested or overlapping quantifiers). Always apply them via
# :func:`full_match`, the one helper for shape checks.

#: Authored node IDs are unique dotted slugs, e.g. ``economy.supply.choose_worker``.
NODE_ID_RE: Final = re.compile(r"\A[a-z][a-z0-9_]*(?:\.[a-z0-9_]+)*\Z")
NODE_ID_MAX_LENGTH: Final = 128
#: Selection binding names and policy parameter names.
NAME_RE: Final = re.compile(r"\A[a-z][a-z0-9_]{0,63}\Z")
_RUN_ID_RE: Final = re.compile(r"\A[0-9a-f]{32}\Z")


def is_bounded_number(value: object, max_abs: float = MAX_ABS_NUMBER) -> bool:
    """THE overflow-safe numeric check: a real (not bool), finite, |value| <= ``max_abs``.

    Ints are compared against the float bound directly (exact in Python, never
    converted), so arbitrarily large ints are rejected instead of overflowing
    ``float()`` or ``math.isfinite``. Shared by the validator and the runtime.
    """
    if isinstance(value, bool) or not isinstance(value, int | float):
        return False
    if isinstance(value, float) and not math.isfinite(value):
        return False
    return -max_abs <= value <= max_abs


def full_match(pattern: re.Pattern[str], value: object) -> bool:
    """THE shape check: ``value`` is a str matched by ``pattern`` in its entirety."""
    return isinstance(value, str) and pattern.fullmatch(value) is not None


#: Untrusted text interpolated into a message shows at most this many characters
#: (>= NODE_ID_MAX_LENGTH, so a legal node id is never truncated).
MAX_FRAGMENT_CHARS: Final = 160


def _escape_code_point(char: str) -> str:
    code = ord(char)
    if code <= 0xFF:
        return f"\\x{code:02x}"
    if code <= 0xFFFF:
        return f"\\u{code:04x}"
    return f"\\U{code:08x}"


def render_text(text: str) -> str:
    """Terminal-safe single line: every non-printable code point becomes an escape.

    "Printable" is :meth:`str.isprintable`: control (Cc), format (Cf, incl. bidi
    and zero-width marks), surrogate (Cs), private-use (Co), unassigned (Cn),
    line/paragraph separators and non-ASCII spaces are all escaped; printable
    text, including non-ASCII letters, is kept. Stream encodability is handled
    by the CLI (``errors="backslashreplace"``). Idempotent on its own output.
    """
    if text.isprintable():
        return text
    return "".join(char if char.isprintable() else _escape_code_point(char) for char in text)


def render_lines(text: str) -> str:
    """:func:`render_text` per line, keeping the structural newlines."""
    return "\n".join(render_text(line) for line in text.split("\n"))


#: Nested renderings stop at this depth and show this many items per container,
#: so rendering any value -- a shared-subobject DAG, a cyclic object, a container
#: with millions of items -- is a bounded amount of work.
_REPR_MAX_LEVEL: Final = 3
_REPR_MAX_ITEMS: Final = 6
_REPR_MAX_NESTED_CHARS: Final = 40
_HUGE_INT: Final = 10**18


def _digits_summary(value: int) -> str:
    return f"<integer of about {int(int.bit_length(value) * 0.30103) + 1} digits>"


def _string_repr(value: str, limit: int) -> str:
    size = str.__len__(value)
    head = str.__repr__(str.__getitem__(value, slice(0, limit)))
    return head if size <= limit else f"{head}... (+{size - limit} chars)"


def _bounded_repr(value: object, level: int) -> str:
    """Structural repr over builtin types only, via their unbound methods.

    Never calls a caller-defined ``__repr__``/``__iter__``/``__hash__`` (a hostile
    object may raise or take unbounded time); unknown types render as their name.
    """
    if value is None or isinstance(value, bool):
        return repr(value)  # NoneType and bool cannot be subclassed
    if isinstance(value, int):
        return _digits_summary(value) if int.__abs__(value) > _HUGE_INT else int.__repr__(value)
    if isinstance(value, float):
        return float.__repr__(value)
    if isinstance(value, str):
        return _string_repr(value, _REPR_MAX_NESTED_CHARS)
    if isinstance(value, bytes):
        return bytes.__repr__(bytes.__getitem__(value, slice(0, _REPR_MAX_NESTED_CHARS)))
    parts: list[str]
    if isinstance(value, list | tuple):
        window = slice(0, _REPR_MAX_ITEMS)
        items: Sequence[object]
        if isinstance(value, list):
            size, items, left, right = (
                list.__len__(value),
                list.__getitem__(value, window),
                "[",
                "]",
            )
        else:
            size, items = tuple.__len__(value), tuple.__getitem__(value, window)
            left, right = "(", ",)" if tuple.__len__(value) == 1 else ")"
        if level >= _REPR_MAX_LEVEL:
            return f"{left}...{right}" if size else f"{left}{right}"
        parts = [_bounded_repr(item, level + 1) for item in items]
        if size > _REPR_MAX_ITEMS:
            parts.append(f"... (+{size - _REPR_MAX_ITEMS})")
        return f"{left}{', '.join(parts)}{right}"
    if isinstance(value, dict):
        size = dict.__len__(value)
        if level >= _REPR_MAX_LEVEL:
            return "{...}" if size else "{}"
        parts = [
            f"{_bounded_repr(key, level + 1)}: {_bounded_repr(item, level + 1)}"
            for key, item in itertools.islice(dict.items(value), _REPR_MAX_ITEMS)
        ]
        if size > _REPR_MAX_ITEMS:
            parts.append(f"... (+{size - _REPR_MAX_ITEMS})")
        return "{" + ", ".join(parts) + "}"
    if isinstance(value, set | frozenset):
        members: Iterator[object]
        if isinstance(value, set):
            size, members, left, right = set.__len__(value), set.__iter__(value), "{", "}"
        else:
            size, members = frozenset.__len__(value), frozenset.__iter__(value)
            left, right = "frozenset({", "})"
        if not size:
            return "set()" if left == "{" else "frozenset()"
        if level >= _REPR_MAX_LEVEL:
            return f"{left}...{right}"
        shown = itertools.islice(members, _REPR_MAX_ITEMS)
        parts = [_bounded_repr(member, level + 1) for member in shown]
        if size > _REPR_MAX_ITEMS:
            parts.append(f"... (+{size - _REPR_MAX_ITEMS})")
        return f"{left}{', '.join(parts)}{right}"
    name = type(value).__name__
    return f"<{name if len(name) <= 64 else name[:64] + '...'} object>"


def safe_repr(value: object, limit: int = MAX_FRAGMENT_CHARS) -> str:
    """THE capped renderer for any untrusted value interpolated into a message.

    Strings are quoted, escaped (:func:`render_text`) and truncated to ``limit``
    characters with a ``... (+N chars)`` marker; huge ints are summarized by digit
    count; builtin containers render structurally with bounded depth and items;
    any other object renders as ``<TypeName object>``. Total and bounded: it never
    raises and never runs caller code, so it is safe on cyclic, shared-subobject
    (DAG) and hostile objects (a raising ``__repr__`` cannot leak out of a message).
    """
    try:
        if isinstance(value, str):
            size = str.__len__(value)
            head = render_text(str.__getitem__(value, slice(0, limit)))
            return f"'{head}'" if size <= limit else f"'{head}'... (+{size - limit} chars)"
        if isinstance(value, int) and not isinstance(value, bool):
            if int.__abs__(value) > _HUGE_INT:
                return _digits_summary(value)
        text = render_text(_bounded_repr(value, 0))
    except Exception:  # defensive: a builtin method failed on a hostile subclass
        text = f"<{render_text(type(value).__name__)[:64]} object>"
    return text if len(text) <= limit else text[:limit] + "..."


def safe_exception_text(exc: BaseException, limit: int = MAX_FRAGMENT_CHARS) -> str:
    """THE guarded renderer for an exception's message (``str(exc)`` is caller code).

    Exceptions using the stock ``__str__`` render their ``args`` through
    :func:`safe_repr` without calling ``str`` (an arg may be a DAG or hostile
    object); any other ``__str__`` runs guarded, falling back to the type name.
    """
    name = type(exc).__name__
    try:
        if type(exc).__str__ is BaseException.__str__:
            args = exc.args
            if not args:
                return name
            return safe_repr(args[0] if len(args) == 1 else args, limit)
        return safe_repr(str(exc), limit)
    except Exception:
        return f"<{render_text(name)[:64]}>"


#: Rendered PolicyError messages list at most this many issues (all stay in ``.issues``).
MAX_REPORTED_ISSUES: Final = 100
#: Hard cap on one rendered issue message (central backstop; fragments are capped
#: individually by ``safe_repr``).
MAX_MESSAGE_CHARS: Final = 2048


@dataclass(frozen=True)
class PolicyIssue:
    """One validation problem. ``node_id`` is None for policy-level problems."""

    code: str
    message: str
    node_id: str | None = None

    def __post_init__(self) -> None:
        # The single choke point: every PolicyError message is built from issues,
        # so rendering and capping here bound and sanitize all interpolated text.
        message = render_text(self.message)
        if len(message) > MAX_MESSAGE_CHARS:
            cut = len(message) - MAX_MESSAGE_CHARS
            message = f"{message[:MAX_MESSAGE_CHARS]}... (+{cut} chars truncated)"
        object.__setattr__(self, "message", message)
        if self.node_id is not None:
            # A non-str id (caller-built node) is rendered, never str()-ed: it may be
            # a DAG or a hostile object.
            raw = self.node_id
            node_id = render_text(raw) if isinstance(raw, str) else safe_repr(raw)
            if len(node_id) > MAX_FRAGMENT_CHARS:
                node_id = node_id[:MAX_FRAGMENT_CHARS] + "..."
            object.__setattr__(self, "node_id", node_id)

    def __str__(self) -> str:
        where = f"node '{self.node_id}'" if self.node_id is not None else "policy"
        return f"{where}: {self.message} [{self.code}]"


class PolicyError(ValueError):
    """Raised for any invalid policy or manifest; stable error code ``invalid_policy``."""

    code: Final = "invalid_policy"

    def __init__(self, issues: Sequence[PolicyIssue]) -> None:
        self.issues: tuple[PolicyIssue, ...] = tuple(issues)
        shown = [f"  - {issue}" for issue in self.issues[:MAX_REPORTED_ISSUES]]  # pre-rendered
        if len(self.issues) > MAX_REPORTED_ISSUES:
            shown.append(f"  - ... and {len(self.issues) - MAX_REPORTED_ISSUES} more issue(s)")
        lines = "\n".join(shown)
        super().__init__(f"invalid_policy: {len(self.issues)} issue(s)\n{lines}")

    def node_ids(self) -> set[str]:
        return {issue.node_id for issue in self.issues if issue.node_id is not None}

    def codes(self) -> set[str]:
        return {issue.code for issue in self.issues}


# ---------------------------------------------------------------------------
# Read-only JSON containers for validated state
# ---------------------------------------------------------------------------

_MAX_FREEZE_DEPTH: Final = 256
#: THE element budget for walking one caller-built structure (freeze/thaw here, the
#: policy value scan in ``jev.policy``). Counted over the *expanded* tree, so a
#: shared sub-object (``x = [x, x]``) costs once per use and a DAG cannot multiply
#: work; JSON text within ``MAX_POLICY_BYTES`` never exceeds ~524k elements.
MAX_DOCUMENT_ELEMENTS: Final = 1_000_000


def _read_only(*args: Any, **kwargs: Any) -> NoReturn:
    raise TypeError("validated Jev policy data is read-only")


class FrozenJsonDict(dict[str, JsonValue]):
    """A JSON object that cannot be mutated (still a ``dict`` for readers/encoders)."""

    __setitem__ = __delitem__ = clear = pop = popitem = setdefault = update = _read_only
    __ior__ = _read_only

    def __copy__(self) -> FrozenJsonDict:
        return self

    def __deepcopy__(self, memo: dict[int, Any]) -> FrozenJsonDict:
        return self

    def __reduce__(self) -> tuple[Any, ...]:
        return (FrozenJsonDict, (dict(self),))


class FrozenJsonList(list[JsonValue]):
    """A JSON array that cannot be mutated (still a ``list`` for readers/encoders)."""

    __setitem__ = __delitem__ = append = extend = insert = pop = remove = _read_only
    clear = sort = reverse = __iadd__ = __imul__ = _read_only

    def __copy__(self) -> FrozenJsonList:
        return self

    def __deepcopy__(self, memo: dict[int, Any]) -> FrozenJsonList:
        return self

    def __reduce__(self) -> tuple[Any, ...]:
        return (FrozenJsonList, (list(self),))


class _WalkBudget:
    """Element + depth budget shared by one freeze/thaw walk (bounded on DAGs/cycles)."""

    __slots__ = ("remaining",)

    def __init__(self) -> None:
        self.remaining = MAX_DOCUMENT_ELEMENTS

    def visit(self, depth: int) -> None:
        self.remaining -= 1
        if self.remaining < 0:
            raise PolicyError(
                [
                    PolicyIssue(
                        "too_large",
                        f"policy data has more than {MAX_DOCUMENT_ELEMENTS} elements "
                        "(a shared sub-object counts once per use)",
                    )
                ]
            )
        if depth > _MAX_FREEZE_DEPTH:
            raise PolicyError(
                [PolicyIssue("too_deep", "policy data nests too deeply (or is circular)")]
            )


def _freeze(value: Any, depth: int, budget: _WalkBudget) -> Any:
    budget.visit(depth)
    if isinstance(value, Mapping):
        return FrozenJsonDict(
            {key: _freeze(item, depth + 1, budget) for key, item in value.items()}
        )
    if isinstance(value, list):
        return FrozenJsonList(_freeze(item, depth + 1, budget) for item in value)
    if isinstance(value, tuple):
        return tuple(_freeze(item, depth + 1, budget) for item in value)
    return value


def freeze_json(value: Any) -> Any:
    """Deep read-only copy: dicts/lists become Frozen* (tuples stay tuples, frozen inside).

    Never aliases the caller's containers -- an existing Frozen* container is
    walked too, since anyone can build one around mutable or oversized contents.
    Non-JSON leaves are kept as-is so the validator can still report them. Work is
    bounded by :data:`MAX_DOCUMENT_ELEMENTS` (``too_large``: huge fan-out or a
    shared-subobject DAG) and nesting depth (``too_deep``: also circular input).
    """
    return _freeze(value, 0, _WalkBudget())


def _thaw(value: Any, depth: int, budget: _WalkBudget) -> Any:
    budget.visit(depth)
    if isinstance(value, Mapping):
        return {key: _thaw(item, depth + 1, budget) for key, item in value.items()}
    if isinstance(value, list):
        return [_thaw(item, depth + 1, budget) for item in value]
    if isinstance(value, tuple):
        return tuple(_thaw(item, depth + 1, budget) for item in value)
    return value


def thaw_json(value: Any) -> Any:
    """Plain mutable copy of frozen JSON data (``dict`` / ``list``), same budgets."""
    return _thaw(value, 0, _WalkBudget())


def is_valid_run_id(value: str) -> bool:
    """Return True for a lowercase UUID4 hex run ID (32 hex chars, version 4)."""
    if not full_match(_RUN_ID_RE, value):
        return False
    return uuid.UUID(hex=value).version == 4


def encode_tag(tag: int) -> str:
    """Serialize a unit tag as a decimal string (JavaScript-safe)."""
    return str(tag)


def encode_point(point: Point) -> list[JsonValue]:
    return [float(point[0]), float(point[1])]


def encode_target(target: Target) -> JsonValue:
    """Serialize a task/command target: tag -> decimal string, point -> ``[x, y]``."""
    if target is None:
        return None
    if isinstance(target, tuple):
        return encode_point(target)
    return encode_tag(target)


# ---------------------------------------------------------------------------
# Policy and manifest
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PolicyNode:
    """One authored node. Composites carry children; leaves carry an operation."""

    id: str
    label: str
    kind: NodeKind
    children: tuple[str, ...]
    operation: str | None
    args: Mapping[str, JsonValue]

    def __post_init__(self) -> None:
        # Validated state must stay validated: tuples and read-only JSON containers,
        # never the caller's (mutable) objects. Malformed shapes are PolicyErrors.
        if not isinstance(self.children, list | tuple):
            raise PolicyError(
                [PolicyIssue("invalid_children", "children must be a list of node ids", self.id)]
            )
        if not isinstance(self.args, Mapping):
            raise PolicyError([PolicyIssue("invalid_args", "args must be a JSON object", self.id)])
        object.__setattr__(self, "children", tuple(self.children))
        object.__setattr__(self, "args", freeze_json(self.args))

    def to_document(self) -> dict[str, JsonValue]:
        return {
            "id": self.id,
            "label": self.label,
            "kind": self.kind,
            "children": list(self.children),
            "operation": self.operation,
            "args": thaw_json(self.args),
        }


@dataclass(frozen=True)
class Policy:
    """A parsed policy document. Construct via :func:`jev.policy.parse_policy`."""

    schema_version: int
    family: str
    version: int
    roots: tuple[str, ...]
    parameters: Mapping[str, JsonValue]
    nodes: tuple[PolicyNode, ...]
    _index: Mapping[str, PolicyNode] = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        # Immutable after construction (see PolicyNode); malformed shapes are PolicyErrors.
        if not isinstance(self.roots, list | tuple):
            raise PolicyError([PolicyIssue("invalid_roots", "roots must be a list of node ids")])
        nodes = self.nodes
        if not isinstance(nodes, list | tuple) or not all(isinstance(n, PolicyNode) for n in nodes):
            raise PolicyError(
                [PolicyIssue("invalid_nodes", "nodes must be a list of PolicyNode records")]
            )
        if not isinstance(self.parameters, Mapping):
            raise PolicyError(
                [PolicyIssue("invalid_parameters", "parameters must be a JSON object")]
            )
        object.__setattr__(self, "roots", tuple(self.roots))
        object.__setattr__(self, "nodes", tuple(nodes))
        object.__setattr__(self, "parameters", freeze_json(self.parameters))
        index: dict[str, PolicyNode] = {}
        for node in self.nodes:
            if isinstance(node.id, str):  # other ids are reported by the validator
                index.setdefault(node.id, node)
        object.__setattr__(self, "_index", index)

    def node(self, node_id: str) -> PolicyNode:
        return self._index[node_id]

    def has_node(self, node_id: str) -> bool:
        return node_id in self._index

    def to_document(self) -> dict[str, JsonValue]:
        """Return the JSON document form (the exact shape the loader accepts)."""
        return {
            "schema_version": self.schema_version,
            "family": self.family,
            "version": self.version,
            "roots": list(self.roots),
            "parameters": thaw_json(self.parameters),
            "nodes": [node.to_document() for node in self.nodes],
        }


@dataclass(frozen=True)
class Manifest:
    """``bots/jev/vN/manifest.json``: immutable identity and schema metadata."""

    schema_version: int
    family: str
    version: int
    entrypoint: str
    policy_file: str

    @property
    def display_name(self) -> str:
        """Family-qualified display identity, e.g. ``v1.jev``."""
        return f"v{self.version}.{self.family}"


# ---------------------------------------------------------------------------
# Observation
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Order:
    """One queued order on an entity. ``ability`` is a generic SC2 AbilityId name."""

    ability: str
    target: Target = None
    progress: float = 0.0


@dataclass(frozen=True)
class Entity:
    """A visible (or remembered) entity.

    ``ready`` / ``idle`` / ``powered`` are populated for own production structures
    and may be ``None`` elsewhere; the ``is_*`` properties apply the documented
    fallbacks (ready := fully built, idle := no orders, powered := explicit True).
    ``shield`` is the Protoss shield (0 for other races); damage lands on it first.
    """

    tag: int
    type_name: str
    position: Point
    health: float
    build_progress: float = 1.0
    orders: tuple[Order, ...] = ()
    is_structure: bool = False
    is_flying: bool = False
    ready: bool | None = None
    idle: bool | None = None
    powered: bool | None = None
    shield: float = 0.0

    @property
    def durability(self) -> float:
        """Health plus shield: what damage must remove before the entity dies."""
        return self.health + self.shield

    @property
    def is_ready(self) -> bool:
        return self.ready if self.ready is not None else self.build_progress >= 1.0

    @property
    def is_idle(self) -> bool:
        return self.idle if self.idle is not None else not self.orders

    @property
    def is_powered(self) -> bool:
        return self.powered is True

    @property
    def current_ability(self) -> str | None:
        return self.orders[0].ability if self.orders else None


@dataclass(frozen=True)
class Observation:
    """Normalized visible game state for one interpreter tick."""

    game_loop: int
    game_seconds: float
    minerals: int
    supply_used: int
    supply_cap: int
    own_units: tuple[Entity, ...]
    own_structures: tuple[Entity, ...]
    visible_enemies: tuple[Entity, ...]
    remembered_enemy_structures: tuple[Entity, ...]
    start_location: Point
    enemy_start_locations: tuple[Point, ...]
    expansion_locations: tuple[Point, ...]
    map_center: Point
    mineral_fields: tuple[Entity, ...] = ()

    # Collection / location / point names ARE field names (one source of truth:
    # the Literal-derived tuples), so lookup is by field, never a re-listed branch.

    def collection(self, name: EntityCollection) -> tuple[Entity, ...]:
        if name not in ENTITY_COLLECTIONS:
            raise ValueError(f"unknown entity collection {safe_repr(name)}")
        entities: tuple[Entity, ...] = getattr(self, name)
        return entities

    def locations(self, name: LocationSource) -> tuple[Point, ...]:
        if name not in LOCATION_SOURCES:
            raise ValueError(f"unknown location source {safe_repr(name)}")
        points: tuple[Point, ...] = getattr(self, name)
        return points

    def point(self, name: PointKeyword) -> Point:
        if name not in POINT_KEYWORDS:
            raise ValueError(f"unknown point keyword {safe_repr(name)}")
        point: Point = getattr(self, name)
        return point

    def own_entity(self, tag: int) -> Entity | None:
        for entity in self.own_units:
            if entity.tag == tag:
                return entity
        for entity in self.own_structures:
            if entity.tag == tag:
                return entity
        return None


# ---------------------------------------------------------------------------
# Commands, tasks, events, run records
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CommandSpec:
    """An explicit graph-selected command. Issuing it to SC2 is the adapter's job.

    ``alternatives`` carries further placement candidates for a build (the adapter
    tries them in order, at most eight in total per attempt).
    """

    node_id: str
    task_id: str
    ability: str
    actor_tags: tuple[int, ...]
    target: Target
    alternatives: tuple[Point, ...] = ()

    def to_dict(self) -> dict[str, JsonValue]:
        return {
            "node_id": self.node_id,
            "task_id": self.task_id,
            "ability": self.ability,
            "actor_tags": [encode_tag(tag) for tag in self.actor_tags],
            "target": encode_target(self.target),
            "alternatives": [encode_point(p) for p in self.alternatives],
        }


@dataclass(frozen=True)
class Task:
    """Wire snapshot of one in-match task (embedded in RunState)."""

    id: str
    node_id: str
    intent_key: str
    actor_tag: int | None
    target: Target
    status: TaskStatus
    created_game_seconds: float
    deadline_game_seconds: float | None
    attempts: int
    last_progress_game_seconds: float | None
    reason: str

    def to_dict(self) -> dict[str, JsonValue]:
        return {
            "id": self.id,
            "node_id": self.node_id,
            "intent_key": self.intent_key,
            "actor_tag": None if self.actor_tag is None else encode_tag(self.actor_tag),
            "target": encode_target(self.target),
            "status": self.status,
            "created_game_seconds": self.created_game_seconds,
            "deadline_game_seconds": self.deadline_game_seconds,
            "attempts": self.attempts,
            "last_progress_game_seconds": self.last_progress_game_seconds,
            "reason": self.reason,
        }


@dataclass(frozen=True)
class Event:
    """One trace event. Every event names the node it originated from."""

    run_id: str
    sequence: int
    game_loop: int
    game_seconds: float
    node_id: str
    task_id: str | None
    kind: EventKind
    status: EventStatus
    reason: str
    facts: Mapping[str, JsonValue]
    action: Mapping[str, JsonValue] | None
    schema_version: int = SCHEMA_VERSION

    def to_dict(self) -> dict[str, JsonValue]:
        return {
            "schema_version": self.schema_version,
            "run_id": self.run_id,
            "sequence": self.sequence,
            "game_loop": self.game_loop,
            "game_seconds": self.game_seconds,
            "node_id": self.node_id,
            "task_id": self.task_id,
            "kind": self.kind,
            "status": self.status,
            "reason": self.reason,
            "facts": dict(self.facts),
            "action": None if self.action is None else dict(self.action),
        }


@dataclass(frozen=True)
class JevError:
    """Stable error record: ``code`` is machine-readable, ``message`` human-readable."""

    code: ErrorCode
    message: str

    def to_dict(self) -> dict[str, JsonValue]:
        return {"code": self.code, "message": self.message}


@dataclass(frozen=True)
class RunState:
    """Atomic run snapshot (written by telemetry in a later step)."""

    run_id: str
    family: str
    version: int
    policy_hash: str
    status: RunStatus
    updated_at: str
    game_seconds: float
    last_sequence: int
    active_nodes: tuple[str, ...]
    waiting_nodes: tuple[str, ...]
    tasks: tuple[Task, ...]
    recent_events: tuple[Event, ...]
    result: RunResult | None = None
    error: JevError | None = None
    schema_version: int = SCHEMA_VERSION

    def to_dict(self) -> dict[str, JsonValue]:
        return {
            "schema_version": self.schema_version,
            "run_id": self.run_id,
            "family": self.family,
            "version": self.version,
            "policy_hash": self.policy_hash,
            "status": self.status,
            "updated_at": self.updated_at,
            "game_seconds": self.game_seconds,
            "last_sequence": self.last_sequence,
            "active_nodes": list(self.active_nodes),
            "waiting_nodes": list(self.waiting_nodes),
            "tasks": [task.to_dict() for task in self.tasks],
            "recent_events": [event.to_dict() for event in self.recent_events],
            "result": self.result,
            "error": None if self.error is None else self.error.to_dict(),
        }


@dataclass(frozen=True)
class RunMetadata:
    """Immutable per-run provenance (written once by the runner in a later step)."""

    run_id: str
    created_at: str
    family: str
    version: int
    policy_hash: str
    source_commit: str
    map: str
    opponent_race: str
    difficulty: int
    seed: int
    max_game_seconds: float
    max_wall_seconds: float
    replay_path: str | None = None
    schema_version: int = SCHEMA_VERSION

    def to_dict(self) -> dict[str, JsonValue]:
        return {
            "schema_version": self.schema_version,
            "run_id": self.run_id,
            "created_at": self.created_at,
            "family": self.family,
            "version": self.version,
            "policy_hash": self.policy_hash,
            "source_commit": self.source_commit,
            "map": self.map,
            "opponent_race": self.opponent_race,
            "difficulty": self.difficulty,
            "seed": self.seed,
            "max_game_seconds": self.max_game_seconds,
            "max_wall_seconds": self.max_wall_seconds,
            "replay_path": self.replay_path,
        }
