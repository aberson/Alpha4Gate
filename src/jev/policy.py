"""Strict Jev policy loading, structural validation and the canonical policy hash.

A policy is JSON *data*: it is parsed with the stdlib ``json`` module (duplicate
keys, NaN/Infinity and non-object documents rejected), never evaluated, and may
only name operations declared in :data:`jev.operations.OPERATIONS`.

Validation is exhaustive rather than first-error: :func:`policy_issues` returns
every problem, each naming the offending node ID where one exists, and
:class:`PolicyError` carries them all. Checks (plan D2): unknown/missing keys,
unsupported schema version, duplicate node IDs, missing child/root references,
nodes with more than one parent, cycles, unreachable nodes, depth/size bounds,
arity per kind, unknown operations, operation/kind mismatch, typed operation
arguments, parameter references, unhandled outcomes (siblings a composite can
never reach), and unbound or mistyped root-local binding references.

:func:`policy_hash` is the one canonical hash: SHA-256 over sorted-key compact
JSON. Every producer and consumer imports it from here.

Untrusted-input invariant: every public entry point that parses, validates or
hashes policy/manifest input (:func:`parse_json_document`, :func:`parse_policy`,
:func:`parse_manifest`, :func:`validate_policy`, :func:`policy_issues`,
:func:`policy_hash`, :func:`load_policy_bundle`) raises only :class:`PolicyError`.
Known hazards are rejected explicitly with node/field-specific issues (nesting,
size, number magnitude, lone-surrogate strings, non-string keys, non-JSON value
types); any other exception escaping those paths is converted at the boundary
into an ``unprocessable_input`` issue, chained with ``from`` so the cause stays
inspectable. Callers therefore need exactly one ``except PolicyError``.
"""

from __future__ import annotations

import copy
import dataclasses
import functools
import hashlib
import json
import math
import re
from collections import Counter, defaultdict
from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from importlib.resources.abc import Traversable
from pathlib import Path
from typing import Any, Final, TypeGuard

from jev.contracts import (
    COMPOSITE_KINDS,
    FAMILY,
    MAX_ABS_NUMBER,
    MAX_DOCUMENT_ELEMENTS,
    MAX_FRAGMENT_CHARS,
    MAX_MESSAGE_CHARS,
    MAX_REPORTED_ISSUES,
    NAME_RE,
    NODE_ID_MAX_LENGTH,
    NODE_ID_RE,
    NODE_KINDS,
    SCHEMA_VERSION,
    Manifest,
    NodeKind,
    NodeStatus,
    Policy,
    PolicyError,
    PolicyIssue,
    PolicyNode,
    full_match,
    is_bounded_number,
    render_text,
    safe_exception_text,
    safe_repr,
)
from jev.operations import (
    MAX_ISSUES_PER_FIELD,
    OPERATIONS,
    BindingKind,
    binding_inputs,
    binding_output,
    node_outcomes,
    operation_node_kinds,
    parameter_references,
    validate_operation_args,
)

__all__ = [
    "IssueLog",
    "MANIFEST_KEYS",
    "MAX_CHILDREN",
    "MAX_DEPTH",
    "MAX_FAMILY_VERSION",
    "MAX_JSON_NESTING",
    "MAX_LABEL_LENGTH",
    "MAX_MANIFEST_BYTES",
    "MAX_MESSAGE_CHARS",
    "MAX_NODES",
    "MAX_POLICY_BYTES",
    "MAX_REPORTED_ISSUES",
    "MAX_ROOTS",
    "NODE_KEYS",
    "POLICY_KEYS",
    "PolicyBundle",
    "PolicyError",
    "PolicyIssue",
    "canonical_json",
    "describe_bundle",
    "json_nesting_depth",
    "json_value_issues",
    "load_policy_bundle",
    "parse_json_document",
    "parse_manifest",
    "parse_policy",
    "policy_hash",
    "policy_issues",
    "validate_policy",
]

MAX_POLICY_BYTES: Final = 1_048_576
MAX_MANIFEST_BYTES: Final = 65_536
MAX_NODES: Final = 4096
MAX_ROOTS: Final = 16
MAX_CHILDREN: Final = 128
MAX_DEPTH: Final = 128
MAX_LABEL_LENGTH: Final = 200
#: Deepest ``[``/``{`` nesting accepted before json.loads (the v1 schema needs < 10).
MAX_JSON_NESTING: Final = 64
MAX_FAMILY_VERSION: Final = 1_000_000
# Document key sets derive from the contract records (one source of truth).
POLICY_KEYS: Final = frozenset(f.name for f in dataclasses.fields(Policy) if f.init)
NODE_KEYS: Final = frozenset(f.name for f in dataclasses.fields(PolicyNode))
MANIFEST_KEYS: Final = frozenset(f.name for f in dataclasses.fields(Manifest))
_MODULE_RE: Final = re.compile(r"\A[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*\Z")
_POLICY_FILE_RE: Final = re.compile(r"\A[A-Za-z0-9_][A-Za-z0-9_.-]{0,120}\.json\Z")
#: Unicode surrogate code points (a lone one is not a valid Unicode scalar value).
_SURROGATE_RE: Final = re.compile(r"[\ud800-\udfff]")
type _Env = Mapping[str, frozenset[BindingKind]]

#: Issue codes that make the forest unsafe to walk; tree analyses are skipped.
_STRUCTURAL_CODES: Final = frozenset(
    {
        "duplicate_id",
        "missing_root",
        "duplicate_root",
        "missing_child",
        "duplicate_child",
        "multiple_parents",
        "root_has_parent",
        "cycle",
        "depth_exceeded",
    }
)


def _untrusted_input[**P, R](what: str) -> Callable[[Callable[P, R]], Callable[P, R]]:
    """Make ``PolicyError`` the only exception an untrusted-input entry point raises.

    Applied only to the public parse/validate/hash functions of this module, whose
    whole job is processing external policy/manifest input. Interpreter-level
    signals (``KeyboardInterrupt``, ``SystemExit``) are ``BaseException`` and pass.
    """

    def decorate(func: Callable[P, R]) -> Callable[P, R]:
        @functools.wraps(func)
        def guarded(*args: P.args, **kwargs: P.kwargs) -> R:
            try:
                return func(*args, **kwargs)
            except PolicyError:
                raise
            except Exception as exc:
                detail = safe_exception_text(exc)
                raise PolicyError(
                    [
                        PolicyIssue(
                            "unprocessable_input",
                            f"{what} could not be processed ({type(exc).__name__}: {detail})",
                        )
                    ]
                ) from exc

        return guarded

    return decorate


type _Link = tuple[_Link, str | int] | None


def _link_path(link: _Link) -> tuple[str | int, ...]:
    parts: list[str | int] = []
    while link is not None:
        link, part = link
        parts.append(part)
    return tuple(reversed(parts))


def _path_text(path: tuple[str | int, ...]) -> str:
    text = ""
    for part in path:
        if isinstance(part, int):
            text += f"[{part}]"
        elif full_match(NAME_RE, part):
            text += f".{part}" if text else part
        else:
            text += f"[{safe_repr(part)}]"
    if len(text) > MAX_FRAGMENT_CHARS:  # keep the most specific (innermost) part
        text = "..." + text[-MAX_FRAGMENT_CHARS:]
    return text or "<document>"


def _node_for_path(document: Mapping[str, Any], path: tuple[str | int, ...]) -> str | None:
    """Attribute an issue under ``nodes[i]`` to that node's id when the id is clean."""
    if len(path) < 2 or path[0] != "nodes" or not isinstance(path[1], int):
        return None
    nodes = document.get("nodes")
    index = path[1]
    if isinstance(nodes, list) and index < len(nodes) and isinstance(nodes[index], dict):
        node_id = nodes[index].get("id")
        if full_match(NODE_ID_RE, node_id):  # ASCII-only pattern: no surrogates possible
            return str(node_id)
    return f"nodes[{index}]"


class IssueLog:
    """The ONE issue collector shared by every validation pass.

    Caps issues at GENERATION time: once ``limit`` issues are held, further
    ``add`` calls construct nothing (no PolicyIssue, no rendering) and only mark
    the log truncated, so memory and time spent on issues stay bounded however
    many defects a hostile document contains. Passes check :attr:`full` to stop
    early. :meth:`result` appends one ``issues_truncated`` marker when needed.
    """

    def __init__(self, limit: int = MAX_REPORTED_ISSUES) -> None:
        self._items: list[PolicyIssue] = []
        self._codes: set[str] = set()
        self._limit = limit
        self._truncated = False

    @property
    def full(self) -> bool:
        return len(self._items) >= self._limit

    def limit_reached(self) -> bool:
        """True once full -- and records that later checks/issues were skipped."""
        if self.full:
            self._truncated = True
            return True
        return False

    def __bool__(self) -> bool:
        return bool(self._items)

    def __len__(self) -> int:
        return len(self._items)

    def has_code(self, codes: frozenset[str]) -> bool:
        return not self._codes.isdisjoint(codes)

    def add(self, code: str, message: str, node_id: str | None = None) -> None:
        if self.full:
            self._truncated = True
            return
        self._items.append(PolicyIssue(code, message, node_id))
        self._codes.add(code)

    def extend(self, issues: Iterable[PolicyIssue]) -> None:
        for issue in issues:
            if self.full:
                self._truncated = True
                return
            self._items.append(issue)
            self._codes.add(issue.code)

    def result(self) -> list[PolicyIssue]:
        issues = list(self._items)
        if self._truncated:
            issues.append(
                PolicyIssue(
                    "issues_truncated",
                    f"issue limit of {self._limit} reached; further issues and checks were skipped",
                )
            )
        return issues


def _listing(items: Sequence[str], limit: int = MAX_ISSUES_PER_FIELD) -> str:
    """``a, b, c`` with at most ``limit`` entries shown (bounded message size)."""
    shown = ", ".join(safe_repr(item) for item in items[:limit])
    return shown if len(items) <= limit else f"{shown}, ... (+{len(items) - limit} more)"


def _json_value_ok(value: Any, depth: int) -> bool:
    """True when ``value`` itself raises no issue (keys are checked separately)."""
    if isinstance(value, dict | list):
        return depth < MAX_JSON_NESTING
    if isinstance(value, str):
        return _SURROGATE_RE.search(value) is None
    if isinstance(value, float):
        return math.isfinite(value)
    return value is None or isinstance(value, int)  # bool is an int subclass


def _scan_json_values(document: Mapping[str, Any], what: str, log: IssueLog) -> None:
    """Report anything that is not plain, encodable JSON data, naming node/field.

    Checks every key and value iteratively (no recursion): keys must be strings;
    strings (keys and values) must be valid Unicode scalar sequences (no lone
    surrogates); values must be JSON types; floats must be finite. Depth and
    element budgets bound caller-built (possibly circular) structures. Linear:
    each element carries an O(1) parent link, the field path is materialized only
    for a reported issue, and the scan stops once the shared log is full.
    """
    stack: list[tuple[Any, _Link, int]] = [(document, None, 0)]
    seen = 0
    while stack:
        if log.limit_reached():
            return
        value, link, depth = stack.pop()
        seen += 1
        if seen > MAX_DOCUMENT_ELEMENTS:  # shared budget: DAG/fan-out bounded
            log.add(
                "too_large",
                f"{what} has more than {MAX_DOCUMENT_ELEMENTS} elements "
                "(a shared sub-object counts once per use)",
            )
            return
        if isinstance(value, dict):
            if depth >= MAX_JSON_NESTING:
                path = _link_path(link)
                log.add(
                    "too_deep", f"{what} nests deeper than {MAX_JSON_NESTING} at {_path_text(path)}"
                )
                continue
            for key, item in value.items():
                if not isinstance(key, str) or _SURROGATE_RE.search(key):
                    path = _link_path(link)
                    where = _path_text(path)
                    problem = (
                        "is not a string"
                        if not isinstance(key, str)
                        else "contains a lone surrogate (not valid Unicode)"
                    )
                    log.add(
                        "invalid_key" if not isinstance(key, str) else "invalid_text",
                        f"{what}: object key {safe_repr(key)} at {where} {problem}",
                        _node_for_path(document, path),
                    )
                    continue
                stack.append((item, (link, key), depth + 1))
        elif isinstance(value, list):
            if depth >= MAX_JSON_NESTING:
                path = _link_path(link)
                log.add(
                    "too_deep", f"{what} nests deeper than {MAX_JSON_NESTING} at {_path_text(path)}"
                )
                continue
            stack.extend((item, (link, i), depth + 1) for i, item in enumerate(value))
        elif not _json_value_ok(value, depth):
            path = _link_path(link)
            where = _path_text(path)
            node_id = _node_for_path(document, path)
            if isinstance(value, str):
                log.add(
                    "invalid_text",
                    f"{what}: string at {where} contains a lone surrogate (not valid Unicode)",
                    node_id,
                )
            elif isinstance(value, float):
                log.add("invalid_number", f"{what}: non-finite number at {where}", node_id)
            else:
                log.add(
                    "invalid_value_type",
                    f"{what}: {type(value).__name__} at {where} is not a JSON value",
                    node_id,
                )


def json_value_issues(document: Mapping[str, Any], what: str) -> list[PolicyIssue]:
    """Public wrapper: every plain-JSON-data issue in ``document`` (capped)."""
    log = IssueLog()
    _scan_json_values(document, what, log)
    return log.result()


@dataclass(frozen=True)
class PolicyBundle:
    """A validated packaged policy plus its manifest, canonical hash and raw bytes."""

    manifest: Manifest
    policy: Policy
    policy_hash: str
    policy_bytes: bytes
    source: str


# ---------------------------------------------------------------------------
# Canonical hash
# ---------------------------------------------------------------------------


def canonical_json(document: Mapping[str, Any]) -> bytes:
    """Sorted-key, whitespace-free UTF-8 JSON of a plain JSON document.

    Rejects (``PolicyError``) anything that is not JSON data -- non-string keys,
    tuples, sets, NaN/Infinity, lone surrogates -- instead of letting ``json.dumps``
    coerce it, so the hash is injective over JSON documents (``{1: 2}`` would
    otherwise hash like ``{"1": 2}``).
    """
    if not isinstance(document, Mapping):
        raise PolicyError([PolicyIssue("invalid_json", "policy hash input must be a JSON object")])
    issues = json_value_issues(document, "policy hash input")
    if issues:
        raise PolicyError(issues)
    text = json.dumps(
        document, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    )
    return text.encode("utf-8")


@_untrusted_input("policy hash input")
def policy_hash(policy: Policy | Mapping[str, Any]) -> str:
    """SHA-256 hex digest of the canonical JSON of a policy document."""
    document = policy.to_document() if isinstance(policy, Policy) else policy
    return hashlib.sha256(canonical_json(document)).hexdigest()


# ---------------------------------------------------------------------------
# Strict JSON parsing
# ---------------------------------------------------------------------------


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate key {safe_repr(key)}")
        result[key] = value
    return result


def _reject_constant(name: str) -> Any:
    raise ValueError(f"non-finite number {name} is not allowed")


def _finite_float(text: str) -> float:
    value = float(text)
    if not math.isfinite(value):
        raise ValueError(f"non-finite number {safe_repr(text)} is not allowed")
    return value


def json_nesting_depth(text: str, stop_above: int | None = None) -> int:
    """Maximum ``[``/``{`` nesting outside string literals.

    One linear character pass tracking in-string/escape state (no regex, no
    backtracking, no recursion). With ``stop_above``, returns as soon as the depth
    exceeds it, so hostile deep input is rejected early.
    """
    depth = deepest = 0
    in_string = escaped = False
    for char in text:
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
        elif char == '"':
            in_string = True
        elif char == "[" or char == "{":
            depth += 1
            if depth > deepest:
                deepest = depth
                if stop_above is not None and depth > stop_above:
                    return depth
        elif char == "]" or char == "}":
            depth -= 1
    return deepest


@_untrusted_input("JSON document")
def parse_json_document(data: bytes | str, *, what: str, max_bytes: int) -> dict[str, Any]:
    """Parse a JSON object strictly. Raises :class:`PolicyError` on any defect.

    Nesting depth is checked *before* ``json.loads`` (whose scanner recurses), and
    a ``RecursionError`` is still converted, so a hostile document can only ever
    produce a structured ``invalid_policy`` error.
    """
    try:
        raw = data.encode("utf-8") if isinstance(data, str) else data
    except UnicodeEncodeError as exc:
        raise PolicyError(
            [PolicyIssue("invalid_text", f"{what} contains a lone surrogate (not valid Unicode)")]
        ) from exc
    if len(raw) > max_bytes:
        raise PolicyError(
            [PolicyIssue("too_large", f"{what} is {len(raw)} bytes; limit is {max_bytes}")]
        )
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise PolicyError([PolicyIssue("invalid_json", f"{what} is not UTF-8: {exc}")]) from exc
    depth = json_nesting_depth(text, stop_above=MAX_JSON_NESTING)
    if depth > MAX_JSON_NESTING:
        raise PolicyError(
            [
                PolicyIssue(
                    "too_deep",
                    f"{what} nests more than {MAX_JSON_NESTING} levels of arrays/objects",
                )
            ]
        )
    try:
        document = json.loads(
            text,
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=_reject_constant,
            parse_float=_finite_float,
        )
    except RecursionError as exc:
        raise PolicyError([PolicyIssue("too_deep", f"{what} nests too deeply to parse")]) from exc
    except ValueError as exc:
        raise PolicyError(
            [PolicyIssue("invalid_json", f"{what} is not valid JSON: {exc}")]
        ) from exc
    if not isinstance(document, dict):
        raise PolicyError([PolicyIssue("invalid_json", f"{what} must be a JSON object")])
    return document


def _is_int(value: object) -> TypeGuard[int]:
    return isinstance(value, int) and not isinstance(value, bool)


def _key_issues(
    document: Mapping[str, Any], expected: frozenset[str], what: str, node_id: str | None
) -> list[PolicyIssue]:
    """Unknown keys (at most MAX_ISSUES_PER_FIELD named, then a summary) + missing keys."""
    unknown = sorted(set(document) - expected)
    issues = [
        PolicyIssue("unknown_key", f"{what} has unknown key {safe_repr(key)}", node_id)
        for key in unknown[:MAX_ISSUES_PER_FIELD]
    ]
    if len(unknown) > MAX_ISSUES_PER_FIELD:
        issues.append(
            PolicyIssue(
                "unknown_key",
                f"{what} has {len(unknown) - MAX_ISSUES_PER_FIELD} more unknown key(s)",
                node_id,
            )
        )
    issues.extend(
        PolicyIssue("missing_key", f"{what} is missing required key '{key}'", node_id)
        for key in sorted(expected - set(document))
    )
    return issues


# ---------------------------------------------------------------------------
# Document -> Policy (schema level)
# ---------------------------------------------------------------------------


# One implementation per record-field invariant. parse_policy applies these to the
# raw document in its schema phase; policy_issues applies them to a Policy's actual
# attributes, so a Policy built directly (or via dataclasses.replace) passes through
# exactly the same checks before JevRuntime will accept it.

_IDENTITY_FIELDS: Final = ("schema_version", "family", "version")


def _identity_issues(values: Mapping[str, object]) -> list[PolicyIssue]:
    """schema_version == 1, family == "jev", version in 1..MAX_FAMILY_VERSION."""
    issues: list[PolicyIssue] = []
    if "schema_version" in values:
        schema_version = values["schema_version"]
        if not _is_int(schema_version) or schema_version != SCHEMA_VERSION:
            issues.append(
                PolicyIssue(
                    "unsupported_schema_version",
                    f"schema_version {safe_repr(schema_version)} is not supported "
                    f"(expected {SCHEMA_VERSION})",
                )
            )
    if "family" in values and values["family"] != FAMILY:
        issues.append(
            PolicyIssue(
                "invalid_family", f"family must be '{FAMILY}', got {safe_repr(values['family'])}"
            )
        )
    if "version" in values:
        version = values["version"]
        if not (_is_int(version) and 1 <= version <= MAX_FAMILY_VERSION):
            issues.append(
                PolicyIssue(
                    "invalid_version",
                    f"version must be an int in 1..{MAX_FAMILY_VERSION}, got {safe_repr(version)}",
                )
            )
    return issues


def _roots_issues(roots: object) -> list[PolicyIssue]:
    ok = (
        isinstance(roots, list | tuple)
        and 1 <= len(roots) <= MAX_ROOTS
        and all(isinstance(r, str) for r in roots)
    )
    return [] if ok else [PolicyIssue("invalid_roots", f"roots must list 1..{MAX_ROOTS} node ids")]


def _nodes_count_issues(nodes: object) -> list[PolicyIssue]:
    ok = isinstance(nodes, list | tuple) and 1 <= len(nodes) <= MAX_NODES
    return [] if ok else [PolicyIssue("invalid_nodes", f"nodes must list 1..{MAX_NODES} nodes")]


def _valid_node_id(node_id: object) -> bool:
    return (
        isinstance(node_id, str)
        and len(node_id) <= NODE_ID_MAX_LENGTH
        and full_match(NODE_ID_RE, node_id)
    )


def _node_field_issues(
    where: str,
    node_id: object,
    label: object,
    kind: object,
    children: object,
    operation: object,
    args: object,
) -> list[PolicyIssue]:
    """Per-node field types and formats (id, label, kind, children, operation, args)."""
    issues: list[PolicyIssue] = []
    if not _valid_node_id(node_id):
        issues.append(
            PolicyIssue(
                "invalid_node_id",
                f"node id must be a dotted lowercase slug of at most {NODE_ID_MAX_LENGTH} "
                f"characters, got {safe_repr(node_id)}",
                where,
            )
        )
    if not isinstance(label, str) or not label.strip() or len(label) > MAX_LABEL_LENGTH:
        issues.append(
            PolicyIssue("invalid_label", f"label must be 1..{MAX_LABEL_LENGTH} characters", where)
        )
    if kind not in NODE_KINDS:
        issues.append(
            PolicyIssue(
                "invalid_kind",
                f"kind must be one of {', '.join(NODE_KINDS)}, got {safe_repr(kind)}",
                where,
            )
        )
    if not isinstance(children, list | tuple) or not all(isinstance(c, str) for c in children):
        issues.append(PolicyIssue("invalid_children", "children must be a list of node ids", where))
    if operation is not None and not isinstance(operation, str):
        issues.append(PolicyIssue("invalid_operation", "operation must be a string or null", where))
    if not isinstance(args, Mapping):
        issues.append(PolicyIssue("invalid_args", "args must be a JSON object", where))
    return issues


def _parse_node(index: int, raw: Any) -> tuple[PolicyNode | None, list[PolicyIssue]]:
    """One node's schema issues (bounded) and, when clean, its PolicyNode."""
    if not isinstance(raw, dict):
        return None, [PolicyIssue("invalid_node", "node must be a JSON object", f"nodes[{index}]")]
    raw_id = raw.get("id")
    where = raw_id if isinstance(raw_id, str) and _valid_node_id(raw_id) else f"nodes[{index}]"
    issues = _key_issues(raw, NODE_KEYS, "node", where)
    issues.extend(
        _node_field_issues(
            where,
            raw_id,
            raw.get("label"),
            raw.get("kind"),
            raw.get("children"),
            raw.get("operation"),
            raw.get("args"),
        )
    )
    if issues:
        return None, issues
    kind = raw["kind"]
    node_kind: NodeKind = next(k for k in NODE_KINDS if k == kind)
    operation = raw["operation"]
    node = PolicyNode(
        id=str(raw_id),
        label=str(raw["label"]),
        kind=node_kind,
        children=tuple(str(c) for c in raw["children"]),
        operation=operation if isinstance(operation, str) else None,
        args=copy.deepcopy(raw["args"]),  # never alias the caller's mutable document
    )
    return node, []


def _parameter_issues(parameters: object, log: IssueLog) -> None:
    if not isinstance(parameters, Mapping):
        log.add("invalid_parameters", "parameters must be a JSON object")
        return
    for name, value in parameters.items():
        if log.limit_reached():
            return
        if not full_match(NAME_RE, name):
            log.add(
                "invalid_parameters", f"parameter name {safe_repr(name)} is not a lowercase slug"
            )
            continue
        is_numeric = isinstance(value, int | float) and not isinstance(value, bool)
        if not isinstance(value, str | int | float | bool) or (
            is_numeric and not is_bounded_number(value)
        ):
            log.add(
                "invalid_parameters",
                f"parameter '{name}' must be a string, boolean, or number with magnitude "
                f"<= {MAX_ABS_NUMBER:g}, got {safe_repr(value)}",
            )


def _record_issues(policy: Policy, log: IssueLog) -> None:
    """The record-field invariants, checked on a Policy's actual attributes."""
    log.extend(
        _identity_issues(
            {
                "schema_version": policy.schema_version,
                "family": policy.family,
                "version": policy.version,
            }
        )
    )
    log.extend(_roots_issues(policy.roots))
    _parameter_issues(policy.parameters, log)
    count_issues = _nodes_count_issues(policy.nodes)
    log.extend(count_issues)
    if count_issues:
        return
    for index, node in enumerate(policy.nodes):
        if log.limit_reached():
            break
        where = node.id if _valid_node_id(node.id) else f"nodes[{index}]"
        log.extend(
            _node_field_issues(
                where, node.id, node.label, node.kind, node.children, node.operation, node.args
            )
        )


@_untrusted_input("policy document")
def parse_policy(document: Mapping[str, Any]) -> Policy:
    """Build and fully validate a :class:`Policy` from a parsed JSON document.

    The schema phase runs the same field helpers :func:`policy_issues` uses, so
    every field rule has one implementation; the built Policy then passes through
    :func:`validate_policy` like any directly constructed Policy.
    """
    if not isinstance(document, Mapping):
        raise PolicyError([PolicyIssue("invalid_json", "policy must be a JSON object")])
    log = IssueLog()
    _scan_json_values(document, "policy", log)
    if log:
        raise PolicyError(log.result())
    log.extend(_key_issues(document, POLICY_KEYS, "policy", None))
    log.extend(_identity_issues({k: document[k] for k in _IDENTITY_FIELDS if k in document}))
    if "roots" in document:
        log.extend(_roots_issues(document["roots"]))
    if "parameters" in document:
        _parameter_issues(document["parameters"], log)
    nodes: list[PolicyNode] = []
    if "nodes" in document:
        raw_nodes = document["nodes"]
        count_issues = _nodes_count_issues(raw_nodes)
        log.extend(count_issues)
        if not count_issues:
            for index, raw in enumerate(raw_nodes):
                if log.limit_reached():  # stop generating once the shared cap is hit
                    break
                node, node_issues = _parse_node(index, raw)
                log.extend(node_issues)
                if node is not None:
                    nodes.append(node)
    if log:
        raise PolicyError(log.result())
    policy = Policy(
        schema_version=document["schema_version"],
        family=document["family"],
        version=document["version"],
        roots=tuple(str(r) for r in document["roots"]),
        parameters=copy.deepcopy(document["parameters"]),
        nodes=tuple(nodes),
    )
    validate_policy(policy)
    return policy


# ---------------------------------------------------------------------------
# Semantic validation (forest structure, operations, outcomes, bindings)
# ---------------------------------------------------------------------------


@_untrusted_input("policy")
def validate_policy(policy: Policy) -> None:
    """Raise :class:`PolicyError` unless ``policy`` passes every check."""
    issues = policy_issues(policy)
    if issues:
        raise PolicyError(issues)


def _unique_edges(index: Mapping[str, PolicyNode]) -> dict[str, tuple[str, ...]]:
    """Each node's children de-duplicated once (order kept): O(V + E)."""
    return {node_id: tuple(dict.fromkeys(node.children)) for node_id, node in index.items()}


def _find_cycles(edges: Mapping[str, tuple[str, ...]]) -> list[list[str]]:
    """Cycles as strongly connected components: iterative Tarjan, O(V + E).

    One entry per component that contains a cycle (size > 1, or a self-loop),
    members in DFS order. Expects de-duplicated edges; edges to missing nodes
    are ignored.
    """
    order: dict[str, int] = {}
    low: dict[str, int] = {}
    on_stack: set[str] = set()
    scc_stack: list[str] = []
    cycles: list[list[str]] = []
    counter = 0
    for start in edges:
        if start in order:
            continue
        order[start] = low[start] = counter
        counter += 1
        scc_stack.append(start)
        on_stack.add(start)
        work: list[tuple[str, Iterator[str]]] = [(start, iter(edges[start]))]
        while work:
            node_id, children = work[-1]
            advanced = False
            for child in children:
                if child not in edges:
                    continue
                if child not in order:
                    order[child] = low[child] = counter
                    counter += 1
                    scc_stack.append(child)
                    on_stack.add(child)
                    work.append((child, iter(edges[child])))
                    advanced = True
                    break
                if child in on_stack:
                    low[node_id] = min(low[node_id], order[child])
            if advanced:
                continue
            work.pop()
            if work:
                parent = work[-1][0]
                low[parent] = min(low[parent], low[node_id])
            if low[node_id] == order[node_id]:
                members: list[str] = []
                while True:
                    member = scc_stack.pop()
                    on_stack.discard(member)
                    members.append(member)
                    if member == node_id:
                        break
                members.reverse()
                if len(members) > 1 or node_id in edges[node_id]:
                    cycles.append(members)
    return cycles


def _structure_issues(
    policy: Policy,
    index: Mapping[str, PolicyNode],
    edges: Mapping[str, tuple[str, ...]],
    log: IssueLog,
) -> None:
    """Forest checks over de-duplicated edges; every step O(V + E)."""
    for node_id, count in Counter(n.id for n in policy.nodes).items():
        if count > 1:
            log.add("duplicate_id", f"node id is defined {count} times", node_id)
    for root, count in Counter(policy.roots).items():
        if count > 1:
            log.add("duplicate_root", "root is listed more than once", root)
        if root not in index:
            log.add("missing_root", "root references a missing node", root)
    parents: dict[str, list[str]] = defaultdict(list)
    for node_id, node in index.items():
        if log.limit_reached():
            return
        unique = edges[node_id]
        duplicates = len(node.children) - len(unique)
        if duplicates:
            repeated = [c for c, n in Counter(node.children).items() if n > 1]
            log.add(
                "duplicate_child",
                f"{duplicates} duplicate child entr{'y' if duplicates == 1 else 'ies'} "
                f"({_listing(repeated)})",
                node_id,
            )
        missing = [c for c in unique if c not in index]
        if missing:
            noun = "child does" if len(missing) == 1 else f"{len(missing)} children do"
            log.add("missing_child", f"{noun} not exist: {_listing(missing)}", node_id)
        for child in unique:
            if child in index:
                parents[child].append(node_id)
    for child, owners in parents.items():
        if log.limit_reached():
            return
        if len(owners) > 1:
            log.add(
                "multiple_parents",
                f"node has {len(owners)} parents ({_listing(owners)}); the graph must be a forest",
                child,
            )
    for root in dict.fromkeys(policy.roots):
        if root in parents:
            log.add("root_has_parent", f"root is also a child of {_listing(parents[root])}", root)
    for cycle in _find_cycles(edges):
        if log.limit_reached():
            return
        shown = cycle[:MAX_ISSUES_PER_FIELD]
        loop = " -> ".join([*shown, cycle[0] if len(cycle) <= MAX_ISSUES_PER_FIELD else "..."])
        size = f" ({len(cycle)} nodes)" if len(cycle) > MAX_ISSUES_PER_FIELD else ""
        log.add("cycle", f"cycle detected: {loop}{size}", cycle[0])
    reachable: set[str] = set()
    frontier = [(root, 1) for root in dict.fromkeys(policy.roots) if root in index]
    depth_reported = False
    while frontier:
        node_id, depth = frontier.pop()
        if node_id in reachable:
            continue
        reachable.add(node_id)
        if depth > MAX_DEPTH and not depth_reported:
            depth_reported = True
            log.add("depth_exceeded", f"node is deeper than {MAX_DEPTH} levels", node_id)
        frontier.extend((c, depth + 1) for c in edges[node_id] if c in index and c not in reachable)
    for node_id in index:
        if log.limit_reached():
            return
        if node_id not in reachable:
            log.add("unreachable", "node is not reachable from any root", node_id)


def _node_issues(policy: Policy, index: Mapping[str, PolicyNode], log: IssueLog) -> None:
    node_kinds: dict[str, NodeKind] = {node_id: node.kind for node_id, node in index.items()}
    for node in index.values():
        if log.limit_reached():
            return
        if node.kind in COMPOSITE_KINDS:
            if not node.children:
                log.add("invalid_arity", f"{node.kind} needs at least one child", node.id)
            elif len(node.children) > MAX_CHILDREN:
                log.add(
                    "invalid_arity", f"{node.kind} has more than {MAX_CHILDREN} children", node.id
                )
            if node.operation is not None:
                log.add("invalid_operation", f"{node.kind} must not name an operation", node.id)
            if node.args:
                log.add("invalid_args", f"{node.kind} takes no args", node.id)
            continue
        if node.children:
            log.add("invalid_arity", f"{node.kind} is a leaf and cannot have children", node.id)
        if node.operation is None:
            log.add("missing_operation", f"{node.kind} must name an operation", node.id)
            continue
        spec = OPERATIONS.get(node.operation)
        if spec is None:
            log.add("unknown_operation", f"unknown operation {safe_repr(node.operation)}", node.id)
            continue
        allowed = operation_node_kinds(spec)
        if node.kind not in allowed:
            log.add(
                "operation_kind_mismatch",
                f"operation '{node.operation}' cannot run in a {node.kind} node "
                f"(allowed: {', '.join(sorted(allowed))})",
                node.id,
            )
            continue
        for code, message in validate_operation_args(
            node.operation, node.args, parameters=policy.parameters, node_kinds=node_kinds
        ):
            log.add(code, message, node.id)
    referenced: set[str] = set()
    for node in index.values():
        referenced |= parameter_references(dict(node.args))
    unused = sorted(set(policy.parameters) - referenced)
    for name in unused[:MAX_ISSUES_PER_FIELD]:
        log.add("unused_parameter", f"parameter '{name}' is not referenced by any node")
    if len(unused) > MAX_ISSUES_PER_FIELD:
        log.add(
            "unused_parameter",
            f"{len(unused) - MAX_ISSUES_PER_FIELD} more parameter(s) are not referenced",
        )


def _outcome_issues(policy: Policy, index: Mapping[str, PolicyNode], log: IssueLog) -> None:
    """Reject siblings a composite can never reach (an unhandled outcome). O(V + E)."""

    def outcomes(node_id: str) -> frozenset[NodeStatus]:
        node = index[node_id]
        if node.kind not in COMPOSITE_KINDS:
            return node_outcomes(node.kind, node.operation)
        child_outcomes = [outcomes(child) for child in node.children]
        advance: NodeStatus = "success" if node.kind == "sequence" else "failure"
        possible: set[NodeStatus] = set()
        for position, child_set in enumerate(child_outcomes):
            possible |= child_set - {advance}
            if advance not in child_set:
                dead = node.children[position + 1 :]
                if dead:
                    log.add(
                        "unhandled_outcome",
                        f"{node.kind} '{node.id}' can never reach this node: preceding "
                        f"sibling '{node.children[position]}' never returns {advance} "
                        f"(unreachable siblings: {_listing(dead)})",
                        dead[0],
                    )
                return frozenset(possible)
        possible.add(advance)
        return frozenset(possible)

    for root in policy.roots:
        outcomes(root)


_UNBOUND: Final = frozenset[BindingKind]()


def _binding_issues(policy: Policy, index: Mapping[str, PolicyNode], log: IssueLog) -> None:
    """Definite-assignment analysis of root-local bindings.

    Follows the binding rule in :mod:`jev.contracts` (effects commit only on
    success; failure restores the entry bindings), which the interpreter applies
    in ``JevRuntime._evaluate``. Under that rule: a sequence child sees every
    binding produced by earlier siblings' success; a selector child sees exactly
    what its parent saw (earlier siblings failed, so their effects were rolled
    back); a selector's success guarantees only names bound by *every* child, with
    the union of their kinds.

    Linear-time bookkeeping: one environment is mutated in place (no per-node
    copies). Every write is recorded on an undo log, so a selector analyses each
    alternative from the parent's state, rolls it back, and merges only the
    alternatives' changes (their deltas) -- O(V + E) per selector nesting level.
    """
    produced_in_root: dict[str, set[str]] = defaultdict(set)
    for root in policy.roots:
        stack = [root]
        while stack:
            node = index[stack.pop()]
            stack.extend(node.children)
            if node.operation is not None and node.operation in OPERATIONS:
                output = binding_output(node.operation, node.args)
                if output is not None:
                    produced_in_root[output[0]].add(root)

    env: dict[str, frozenset[BindingKind]] = {}
    undo: list[tuple[str, frozenset[BindingKind]]] = []  # (name, previous kinds or _UNBOUND)

    def bind(name: str, kinds: frozenset[BindingKind]) -> None:
        undo.append((name, env.get(name, _UNBOUND)))
        env[name] = kinds

    def rollback(mark: int) -> None:
        while len(undo) > mark:
            name, previous = undo.pop()
            if previous:
                env[name] = previous
            else:
                env.pop(name, None)

    def analyze(node_id: str, root: str) -> None:
        """Leave ``env`` holding the node's SUCCESS environment."""
        node = index[node_id]
        if node.kind == "sequence":
            for child in node.children:
                analyze(child, root)
            return
        if node.kind == "selector":
            if not node.children:  # arity error already reported; nothing is guaranteed
                return
            deltas: list[dict[str, frozenset[BindingKind]]] = []
            for child in node.children:
                mark = len(undo)
                analyze(child, root)
                deltas.append({name: env[name] for name, _ in undo[mark:]})
                rollback(mark)
            present: Counter[str] = Counter()
            kinds: dict[str, set[BindingKind]] = defaultdict(set)
            for delta in deltas:
                for name, name_kinds in delta.items():
                    present[name] += 1
                    kinds[name] |= name_kinds
            for name, count in present.items():
                if name in env:  # bound before: stays bound; un-rebinding branches keep it
                    if count < len(deltas):
                        kinds[name] |= env[name]
                    bind(name, frozenset(kinds[name]))
                elif count == len(deltas):  # newly bound by every alternative
                    bind(name, frozenset(kinds[name]))
            return
        if node.operation is None or node.operation not in OPERATIONS:
            return
        for name, accepted, path in binding_inputs(node.operation, node.args):
            if name not in env:
                elsewhere = sorted(produced_in_root.get(name, set()) - {root})
                hint = (
                    f"; it is bound only in root(s) {', '.join(elsewhere)} and bindings are "
                    "root-local"
                    if elsewhere
                    else ""
                )
                log.add(
                    "unbound_binding",
                    f"argument '{path}' references ${name}, which is not bound by an "
                    f"earlier successful select in root '{root}'{hint}",
                    node.id,
                )
            elif not env[name] <= accepted:
                log.add(
                    "binding_kind_mismatch",
                    f"argument '{path}' accepts {', '.join(sorted(accepted))} but "
                    f"${name} may hold {', '.join(sorted(env[name]))}",
                    node.id,
                )
        output = binding_output(node.operation, node.args)
        if output is not None:
            bind(output[0], frozenset({output[1]}))

    for root in policy.roots:
        env.clear()
        undo.clear()
        analyze(root, root)


@_untrusted_input("policy")
def policy_issues(policy: Policy) -> list[PolicyIssue]:
    """Every validation issue for ``policy`` (empty list means valid; capped).

    The single gate for a Policy however it was built: record-field invariants
    (identity, roots, parameters, node fields) first, then plain-JSON data, then
    forest structure, operations, outcomes and bindings. Every pass is bounded by
    O(n log n) in input size, and all share one generation-capped :class:`IssueLog`.
    """
    log = IssueLog()
    _record_issues(policy, log)
    if log:  # later checks assume well-typed fields
        return log.result()
    _scan_json_values(policy.to_document(), "policy", log)
    if log:  # later checks assume plain JSON data
        return log.result()
    index: dict[str, PolicyNode] = {}
    for node in policy.nodes:
        index.setdefault(node.id, node)
    edges = _unique_edges(index)
    _structure_issues(policy, index, edges, log)
    structural = log.has_code(_STRUCTURAL_CODES)
    _node_issues(policy, index, log)
    if not structural and not log.limit_reached():
        _outcome_issues(policy, index, log)
        _binding_issues(policy, index, log)
    return log.result()


# ---------------------------------------------------------------------------
# Manifest and packaged bundle
# ---------------------------------------------------------------------------


@_untrusted_input("manifest")
def parse_manifest(document: Mapping[str, Any]) -> Manifest:
    if not isinstance(document, Mapping):
        raise PolicyError([PolicyIssue("invalid_json", "manifest must be a JSON object")])
    data_issues = json_value_issues(document, "manifest")
    if data_issues:
        raise PolicyError(data_issues)
    issues = _key_issues(document, MANIFEST_KEYS, "manifest", None)
    schema_version = document.get("schema_version")
    if not _is_int(schema_version) or schema_version != SCHEMA_VERSION:
        issues.append(
            PolicyIssue(
                "unsupported_schema_version",
                f"manifest schema_version {safe_repr(schema_version)} is not supported",
            )
        )
    if document.get("family") != FAMILY:
        issues.append(PolicyIssue("invalid_family", f"manifest family must be '{FAMILY}'"))
    version = document.get("version")
    if not (_is_int(version) and 1 <= version <= MAX_FAMILY_VERSION):
        issues.append(
            PolicyIssue(
                "invalid_version", f"manifest version must be an int in 1..{MAX_FAMILY_VERSION}"
            )
        )
    entrypoint = document.get("entrypoint")
    if not full_match(_MODULE_RE, entrypoint):
        issues.append(
            PolicyIssue("invalid_entrypoint", "manifest entrypoint must be a module path")
        )
    policy_file = document.get("policy_file")
    if not full_match(_POLICY_FILE_RE, policy_file):
        issues.append(
            PolicyIssue(
                "invalid_policy_file",
                "manifest policy_file must be a plain .json file name inside the package",
            )
        )
    if issues:
        raise PolicyError(issues)
    assert isinstance(version, int) and isinstance(entrypoint, str)
    assert isinstance(policy_file, str)
    return Manifest(SCHEMA_VERSION, FAMILY, version, entrypoint, policy_file)


def _read_capped(resource: Traversable | Path, max_bytes: int) -> bytes:
    """Read at most ``max_bytes + 1`` bytes; an oversize file is never fully loaded."""
    if not resource.is_file():
        if resource.is_dir() or (isinstance(resource, Path) and resource.exists()):
            raise PolicyError(
                [
                    PolicyIssue(
                        "not_a_file",
                        f"{resource} is not a regular file (directory, FIFO or device)",
                    )
                ]
            )
        raise PolicyError([PolicyIssue("missing_file", f"{resource} does not exist")])
    with resource.open("rb") as handle:
        data = handle.read(max_bytes + 1)
    if len(data) > max_bytes:
        raise PolicyError(
            [PolicyIssue("too_large", f"{resource} exceeds the {max_bytes}-byte limit")]
        )
    return data


@_untrusted_input("policy package")
def load_policy_bundle(
    package_root: Traversable,
    *,
    expected_entrypoint: str,
    policy_path: Path | None = None,
) -> PolicyBundle:
    """Load ``manifest.json`` and its policy from a policy package directory.

    ``package_root`` is the package's own resource root (``importlib.resources.files``),
    so the packaged policy resolves relative to the package, never the caller's cwd.
    ``policy_path`` validates an alternative candidate document against the same
    packaged manifest (used to check an authored policy before it is packaged).
    """
    manifest_resource = package_root / "manifest.json"
    manifest = parse_manifest(
        parse_json_document(
            _read_capped(manifest_resource, MAX_MANIFEST_BYTES),
            what="manifest.json",
            max_bytes=MAX_MANIFEST_BYTES,
        )
    )
    if manifest.entrypoint != expected_entrypoint:
        raise PolicyError(
            [
                PolicyIssue(
                    "invalid_entrypoint",
                    f"manifest entrypoint {safe_repr(manifest.entrypoint)} does not match "
                    f"package {safe_repr(expected_entrypoint)}",
                )
            ]
        )
    if policy_path is None:
        resource = package_root / manifest.policy_file
        source = str(resource)
        data = _read_capped(resource, MAX_POLICY_BYTES)
    else:
        source = str(policy_path)
        data = _read_capped(policy_path, MAX_POLICY_BYTES)
    document = parse_json_document(data, what=source, max_bytes=MAX_POLICY_BYTES)
    policy = parse_policy(document)
    if policy.version != manifest.version:
        raise PolicyError(
            [
                PolicyIssue(
                    "identity_mismatch",
                    f"policy version {policy.version} does not match manifest version "
                    f"{manifest.version}",
                )
            ]
        )
    return PolicyBundle(
        manifest=manifest,
        policy=policy,
        policy_hash=policy_hash(document),
        policy_bytes=data,
        source=source,
    )


def describe_bundle(bundle: PolicyBundle) -> str:
    """One-line validation summary (printed by ``--validate-policy``)."""
    policy = bundle.policy
    return render_text(
        f"jev policy valid: {bundle.manifest.display_name} "
        f"policy_hash={bundle.policy_hash} nodes={len(policy.nodes)} "
        f"roots={','.join(policy.roots)} source={bundle.source}"
    )
