"""Jev policy loader/validator, canonical hash, CLI and packaging tests (Step 201).

The invalid-policy tests build documents through the real :func:`parse_policy`
entry point and assert the *node-specific* issue each defect produces. The CLI and
installed-wheel tests run ``python -m bots.jev.v1 --validate-policy`` as a real
subprocess so the production loader resolves the packaged JSON itself.
"""

from __future__ import annotations

import ast
import copy
import dataclasses
import json
import math
import os
import re
import shutil
import subprocess
import sys
import time
import unicodedata
import uuid
import zipfile
from collections.abc import Callable
from pathlib import Path
from typing import Any, get_args

import pytest
from bots.jev.v1 import load_policy

import jev.contracts as contracts_module
import jev.operations as operations_module
import jev.policy as policy_module
import jev.runtime as runtime_module
from jev.contracts import (
    MAX_FRAGMENT_CHARS,
    Entity,
    FrozenJsonDict,
    FrozenJsonList,
    Manifest,
    Observation,
    Policy,
    PolicyNode,
    freeze_json,
    render_lines,
    render_text,
    safe_repr,
    thaw_json,
)
from jev.operations import parameter_references, validate_operation_args
from jev.policy import (
    PolicyError,
    PolicyIssue,
    canonical_json,
    parse_json_document,
    parse_manifest,
    parse_policy,
    policy_hash,
    validate_policy,
)
from orchestrator import registry

REPO_ROOT = Path(__file__).resolve().parents[1]
PACKAGE_DIR = REPO_ROOT / "bots" / "jev" / "v1"


# ---------------------------------------------------------------------------
# Document builders
# ---------------------------------------------------------------------------


def _node(
    node_id: str,
    kind: str,
    children: list[str] | None = None,
    operation: str | None = None,
    **args: Any,
) -> dict[str, Any]:
    return {
        "id": node_id,
        "label": f"label for {node_id}",
        "kind": kind,
        "children": children or [],
        "operation": operation,
        "args": args,
    }


def _document(
    roots: list[str],
    nodes: list[dict[str, Any]],
    parameters: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "family": "jev",
        "version": 1,
        "roots": roots,
        "parameters": parameters or {},
        "nodes": nodes,
    }


def _true_condition(node_id: str) -> dict[str, Any]:
    return _node(node_id, "condition", operation="game_time_compare", op=">=", seconds=0)


def _nexus_select(node_id: str, bind: str = "nexus") -> dict[str, Any]:
    return _node(
        node_id,
        "select",
        operation="select_entities",
        collection="own_structures",
        filter={"types": ["Nexus"]},
        limit=1,
        bind=bind,
    )


def _train_probe(node_id: str, producers: str = "$nexus") -> dict[str, Any]:
    return _node(node_id, "action", operation="train", unit="Probe", producers=producers)


def _minimal_valid() -> dict[str, Any]:
    return _document(
        ["lane"],
        [
            _node("lane", "sequence", ["lane.nexus", "lane.train"]),
            _nexus_select("lane.nexus"),
            _train_probe("lane.train"),
        ],
    )


def _issues_of(doc: dict[str, Any]) -> PolicyError:
    with pytest.raises(PolicyError) as excinfo:
        parse_policy(doc)
    return excinfo.value


def _assert_issue(error: PolicyError, code: str, node_id: str | None) -> None:
    matches = [i for i in error.issues if i.code == code and i.node_id == node_id]
    assert matches, f"expected {code} on {node_id!r}; got {[str(i) for i in error.issues]}"


def _shipped_document() -> dict[str, Any]:
    raw = json.loads((PACKAGE_DIR / "policy.json").read_text(encoding="utf-8"))
    assert isinstance(raw, dict)
    return raw


# ---------------------------------------------------------------------------
# Shipped policy, manifest and canonical hash
# ---------------------------------------------------------------------------


def test_shipped_policy_validates_through_package_loader() -> None:
    bundle = load_policy()
    assert bundle.manifest == Manifest(1, "jev", 1, "bots.jev.v1", "policy.json")
    assert bundle.manifest.display_name == "v1.jev"
    assert bundle.policy.roots == ("economy", "construction", "production", "army")
    assert Path(bundle.source).resolve() == (PACKAGE_DIR / "policy.json").resolve()
    assert bundle.policy_bytes == (PACKAGE_DIR / "policy.json").read_bytes()
    assert bundle.policy_hash == policy_hash(json.loads(bundle.policy_bytes))
    assert re.fullmatch(r"[0-9a-f]{64}", bundle.policy_hash)


def test_shipped_policy_encodes_plan_pinned_defaults_as_referenced_parameters() -> None:
    """Only values the plan pins as contract are asserted here (section 3 and D3)."""
    policy = load_policy().policy
    pinned = {
        "gateway_target": 4,  # section 3 / D3: four Gateways
        "first_attack_zealots": 4,  # section 3 / D3: first attack at four ready Zealots
        "probe_target": 16,  # D3: 16 probes
        "supply_trigger_free": 4,  # D3: Pylon at four free supply or fewer
        "defense_radius": 20,  # D3: defend within 20 of the main
        "rally_distance": 8,  # D3: gather eight units toward map center
    }
    for name, value in pinned.items():
        assert policy.parameters[name] == value, name
    # D3: no gas, one base -- the only structures the policy builds.
    built = {n.args.get("structure") for n in policy.nodes if n.operation == "build"}
    assert built == {"Pylon", "Gateway"}
    # Every operation exists in the registry and every parameter is cited by a node.
    assert {n.operation for n in policy.nodes if n.operation} <= set(policy_module.OPERATIONS)
    cited: set[str] = set()
    for item in policy.nodes:
        cited |= parameter_references(dict(item.args))
    assert cited == set(policy.parameters)


def _reorder(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _reorder(value[key]) for key in reversed(list(value))}
    if isinstance(value, list):
        return [_reorder(item) for item in value]
    return value


def test_policy_hash_is_stable_under_key_reordering_and_whitespace() -> None:
    original = _shipped_document()
    reordered_text = json.dumps(_reorder(original), indent=7)
    reordered = parse_json_document(reordered_text, what="reordered", max_bytes=10_000_000)
    assert list(reordered) != list(original)
    assert policy_hash(reordered) == policy_hash(original)
    assert policy_hash(parse_policy(reordered)) == policy_hash(original)
    changed = copy.deepcopy(original)
    changed["parameters"]["probe_target"] = 17
    assert policy_hash(changed) != policy_hash(original)


def test_parsed_policy_does_not_alias_the_source_document() -> None:
    doc = _minimal_valid()
    doc["parameters"] = {"nexus_limit": 1}
    doc["nodes"][1]["args"]["limit"] = {"param": "nexus_limit"}
    policy = parse_policy(doc)
    before = policy_hash(policy)
    doc["parameters"]["nexus_limit"] = 99
    doc["nodes"][1]["args"]["filter"]["types"].append("Gateway")
    assert policy_hash(policy) == before
    assert policy.parameters["nexus_limit"] == 1


def test_policy_hash_has_one_source_of_truth() -> None:
    """The runtime must import the loader's hash function, never re-implement it."""
    assert runtime_module.policy_hash is policy_module.policy_hash
    bundle = load_policy()
    runtime = runtime_module.JevRuntime(bundle.policy, run_id=uuid.uuid4().hex)
    assert runtime.policy_hash == bundle.policy_hash


def test_manifest_rejects_path_escape_and_unknown_keys() -> None:
    good = json.loads((PACKAGE_DIR / "manifest.json").read_text(encoding="utf-8"))
    assert parse_manifest(good).policy_file == "policy.json"
    for bad_file in ("../policy.json", "sub/policy.json", "policy.py", ".hidden.json"):
        with pytest.raises(PolicyError) as excinfo:
            parse_manifest({**good, "policy_file": bad_file})
        assert "invalid_policy_file" in excinfo.value.codes()
    with pytest.raises(PolicyError) as excinfo:
        parse_manifest({**good, "extra": 1})
    assert "unknown_key" in excinfo.value.codes()


# ---------------------------------------------------------------------------
# Strict JSON and schema-level rejection
# ---------------------------------------------------------------------------


def test_deep_nesting_is_rejected_before_parsing() -> None:
    limit = policy_module.MAX_JSON_NESTING
    ok = '{"a": ' + "[" * (limit - 1) + "]" * (limit - 1) + "}"
    assert parse_json_document(ok, what="ok.json", max_bytes=10_000) == {
        "a": _nested_list(limit - 1)
    }
    deep = '{"a": ' + "[" * limit + "]" * limit + "}"
    with pytest.raises(PolicyError) as excinfo:
        parse_json_document(deep, what="deep.json", max_bytes=10_000)
    assert excinfo.value.codes() == {"too_deep"}
    # Brackets inside strings (including escaped quotes) do not count as nesting.
    stringy = '{"a": "' + "[" * 500 + '\\" {{{"}'
    assert parse_json_document(stringy, what="s.json", max_bytes=10_000)["a"].startswith("[[[")


def _nested_list(depth: int) -> list[Any]:
    value: list[Any] = []
    for _ in range(depth - 1):
        value = [value]
    return value


def test_recursion_error_from_the_parser_becomes_a_policy_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Belt and braces: even past the prescan, json.loads recursion is converted."""
    monkeypatch.setattr(policy_module, "MAX_JSON_NESTING", 10**9)
    hostile = "[" * 200_000 + "]" * 200_000
    with pytest.raises(PolicyError) as excinfo:
        parse_json_document(hostile, what="hostile.json", max_bytes=10_000_000)
    assert excinfo.value.codes() == {"too_deep"}


def test_huge_integers_are_node_specific_issues_not_crashes() -> None:
    doc = _minimal_valid()
    doc["nodes"][1]["args"]["limit"] = 10**400
    error = _issues_of(doc)
    _assert_issue(error, "invalid_arg", "lane.nexus")
    assert "digits" in str(error)
    doc = _minimal_valid()
    doc["parameters"] = {"nexus_limit": 10**400}
    doc["nodes"][1]["args"]["limit"] = {"param": "nexus_limit"}
    error = _issues_of(doc)
    _assert_issue(error, "invalid_parameters", None)
    assert "nexus_limit" in str(error)
    # validate_policy on a directly built Policy rejects the parameter record safely ...
    built = Policy(
        schema_version=1,
        family="jev",
        version=1,
        roots=("lane",),
        parameters={"nexus_limit": 10**400},
        nodes=tuple(
            PolicyNode(
                n["id"], n["label"], n["kind"], tuple(n["children"]), n["operation"], n["args"]
            )
            for n in doc["nodes"]
        ),
    )
    with pytest.raises(PolicyError) as excinfo:
        validate_policy(built)
    _assert_issue(excinfo.value, "invalid_parameters", None)
    # ... and the node-level parameter-reference check is overflow-safe on its own.
    arg_issues = validate_operation_args(
        "select_entities",
        {"collection": "own_units", "limit": {"param": "huge"}, "bind": "x"},
        parameters={"huge": 10**400},
        node_kinds={},
    )
    assert [code for code, _ in arg_issues] == ["invalid_parameter"]
    assert "digits" in arg_issues[0][1]
    doc = _minimal_valid()
    doc["version"] = 10**400
    assert "invalid_version" in _issues_of(doc).codes()


def test_unknown_and_missing_keys_rejected() -> None:
    doc = _minimal_valid()
    doc["script"] = "print('hi')"
    del doc["parameters"]
    error = _issues_of(doc)
    _assert_issue(error, "unknown_key", None)
    _assert_issue(error, "missing_key", None)
    doc = _minimal_valid()
    doc["nodes"][1]["code"] = "lambda: 0"
    _assert_issue(_issues_of(doc), "unknown_key", "lane.nexus")


# ---------------------------------------------------------------------------
# Structural (forest) rejection: every error names the node
# ---------------------------------------------------------------------------


def test_cycle_rejected_with_node_ids() -> None:
    doc = _minimal_valid()
    doc["nodes"] += [
        _node("loop.a", "sequence", ["loop.b"]),
        _node("loop.b", "sequence", ["loop.a"]),
    ]
    error = _issues_of(doc)
    cycles = [i for i in error.issues if i.code == "cycle"]
    assert len(cycles) == 1
    # Deterministic attribution: DFS runs in node-declaration order, so the cycle is
    # reported on the member reached first (loop.a), listing members in DFS order.
    assert cycles[0].node_id == "loop.a"
    assert "loop.a -> loop.b -> loop.a" in str(error)


def test_self_reference_is_a_cycle() -> None:
    doc = _minimal_valid()
    doc["nodes"][0]["children"].append("lane")
    error = _issues_of(doc)
    _assert_issue(error, "cycle", "lane")


def test_missing_child_reference_names_parent() -> None:
    doc = _minimal_valid()
    doc["nodes"][0]["children"].append("lane.ghost")
    error = _issues_of(doc)
    _assert_issue(error, "missing_child", "lane")
    assert "lane.ghost" in str(error)


def test_missing_root_rejected() -> None:
    doc = _minimal_valid()
    doc["roots"].append("army")
    _assert_issue(_issues_of(doc), "missing_root", "army")


def test_unreachable_node_rejected() -> None:
    doc = _minimal_valid()
    doc["nodes"].append(_true_condition("orphan"))
    _assert_issue(_issues_of(doc), "unreachable", "orphan")


def test_duplicate_node_id_rejected() -> None:
    doc = _minimal_valid()
    doc["nodes"].append(_true_condition("lane.train"))
    _assert_issue(_issues_of(doc), "duplicate_id", "lane.train")


def test_multiple_parents_rejected() -> None:
    doc = _document(
        ["one", "two"],
        [
            _node("one", "sequence", ["shared"]),
            _node("two", "sequence", ["shared"]),
            _true_condition("shared"),
        ],
    )
    _assert_issue(_issues_of(doc), "multiple_parents", "shared")


def test_root_that_is_also_a_child_rejected() -> None:
    doc = _document(
        ["one", "two"],
        [
            _node("one", "sequence", ["two"]),
            _node("two", "sequence", ["leaf"]),
            _true_condition("leaf"),
        ],
    )
    _assert_issue(_issues_of(doc), "root_has_parent", "two")


def test_depth_bound_rejected() -> None:
    depth = policy_module.MAX_DEPTH + 2
    nodes = [_node(f"d{i}", "sequence", [f"d{i + 1}"]) for i in range(depth)]
    nodes.append(_true_condition(f"d{depth}"))
    # d0 sits at depth 1, so the first node past the bound is d{MAX_DEPTH}.
    _assert_issue(
        _issues_of(_document(["d0"], nodes)), "depth_exceeded", f"d{policy_module.MAX_DEPTH}"
    )


# ---------------------------------------------------------------------------
# Arity, operations and typed arguments
# ---------------------------------------------------------------------------


def test_invalid_arity_rejected() -> None:
    doc = _document(
        ["lane"],
        [
            _node("lane", "sequence", ["lane.nexus", "lane.train", "lane.empty"]),
            _nexus_select("lane.nexus"),
            {**_train_probe("lane.train"), "children": ["lane.extra"]},
            _true_condition("lane.extra"),
            _node("lane.empty", "selector"),
        ],
    )
    error = _issues_of(doc)
    _assert_issue(error, "invalid_arity", "lane.empty")
    _assert_issue(error, "invalid_arity", "lane.train")


def test_unknown_operation_rejected_with_node_id() -> None:
    doc = _minimal_valid()
    doc["nodes"][2]["operation"] = "manage_economy"
    error = _issues_of(doc)
    _assert_issue(error, "unknown_operation", "lane.train")
    assert "manage_economy" in str(error)


def test_operation_in_wrong_node_kind_rejected() -> None:
    doc = _minimal_valid()
    doc["nodes"][2]["kind"] = "condition"
    _assert_issue(_issues_of(doc), "operation_kind_mismatch", "lane.train")


def test_composite_with_operation_and_leaf_without_operation_rejected() -> None:
    doc = _minimal_valid()
    doc["nodes"][0]["operation"] = "train"
    doc["nodes"][1]["operation"] = None
    error = _issues_of(doc)
    _assert_issue(error, "invalid_operation", "lane")
    _assert_issue(error, "missing_operation", "lane.nexus")


@pytest.mark.parametrize(
    ("mutate", "code"),
    [
        (lambda a: a.pop("unit"), "missing_arg"),
        (lambda a: a.update(unit="Marine"), "invalid_arg"),
        (lambda a: a.update(hold_reservation="yes"), "invalid_arg"),
        (lambda a: a.update(eval="__import__('os')"), "unknown_arg"),
        (lambda a: a.update(producers="nexus"), "invalid_arg"),
    ],
)
def test_wrong_typed_args_rejected_with_node_id(mutate: Any, code: str) -> None:
    doc = _minimal_valid()
    mutate(doc["nodes"][2]["args"])
    _assert_issue(_issues_of(doc), code, "lane.train")


def test_numeric_bounds_and_bool_ints_rejected() -> None:
    doc = _minimal_valid()
    doc["nodes"][1]["args"]["limit"] = 0
    _assert_issue(_issues_of(doc), "invalid_arg", "lane.nexus")
    doc["nodes"][1]["args"]["limit"] = True
    _assert_issue(_issues_of(doc), "invalid_arg", "lane.nexus")


def test_filter_keys_and_own_types_are_checked() -> None:
    doc = _minimal_valid()
    doc["nodes"][1]["args"]["filter"] = {"types": ["Nexsus"], "colour": "red"}
    error = _issues_of(doc)
    messages = " ".join(i.message for i in error.issues if i.node_id == "lane.nexus")
    assert "Nexsus" in messages and "colour" in messages


def test_parameter_references_resolve_and_are_checked() -> None:
    doc = _minimal_valid()
    doc["parameters"] = {"nexus_limit": 1}
    doc["nodes"][1]["args"]["limit"] = {"param": "nexus_limit"}
    parse_policy(doc)
    doc["nodes"][1]["args"]["limit"] = {"param": "missing"}
    error = _issues_of(doc)
    _assert_issue(error, "unknown_parameter", "lane.nexus")
    _assert_issue(error, "unused_parameter", None)
    doc["nodes"][1]["args"]["limit"] = {"param": "nexus_limit"}
    doc["parameters"]["nexus_limit"] = 1.5
    _assert_issue(_issues_of(doc), "invalid_parameter", "lane.nexus")


def test_task_state_condition_must_reference_an_action_node() -> None:
    doc = _minimal_valid()
    doc["nodes"][0]["children"].insert(0, "lane.check")
    doc["nodes"].append(
        _node(
            "lane.check",
            "condition",
            operation="task_count_compare",
            node="lane.nexus",
            statuses=["issued"],
            op="==",
            value=0,
        )
    )
    _assert_issue(_issues_of(doc), "invalid_arg", "lane.check")
    doc["nodes"][-1]["args"]["node"] = "lane.ghost"
    _assert_issue(_issues_of(doc), "missing_reference", "lane.check")


# ---------------------------------------------------------------------------
# Unhandled outcomes and root-local bindings
# ---------------------------------------------------------------------------


def test_sibling_after_wait_in_selector_is_an_unhandled_outcome() -> None:
    doc = _document(
        ["lane"],
        [
            _node("lane", "selector", ["lane.wait", "lane.dead"]),
            _node("lane.wait", "wait", operation="game_time_compare", op=">=", seconds=60),
            _true_condition("lane.dead"),
        ],
    )
    error = _issues_of(doc)
    _assert_issue(error, "unhandled_outcome", "lane.dead")
    assert "never returns failure" in str(error)


def test_sibling_after_latch_in_selector_is_an_unhandled_outcome() -> None:
    doc = _document(
        ["lane"],
        [
            _node("lane", "selector", ["lane.latch", "lane.dead"]),
            _node("lane.latch", "action", operation="set_latch", latch="attack_launched"),
            _true_condition("lane.dead"),
        ],
    )
    _assert_issue(_issues_of(doc), "unhandled_outcome", "lane.dead")


def test_unbound_binding_rejected_with_node_id() -> None:
    doc = _minimal_valid()
    doc["nodes"][2]["args"]["producers"] = "$ghost"
    error = _issues_of(doc)
    _assert_issue(error, "unbound_binding", "lane.train")
    assert "$ghost" in str(error)


def test_binding_used_before_it_is_selected_rejected() -> None:
    doc = _minimal_valid()
    doc["nodes"][0]["children"] = ["lane.train", "lane.nexus"]
    _assert_issue(_issues_of(doc), "unbound_binding", "lane.train")


def test_bindings_are_root_local() -> None:
    doc = _document(
        ["economy", "army"],
        [
            _node("economy", "sequence", ["economy.nexus", "economy.train"]),
            _nexus_select("economy.nexus"),
            _train_probe("economy.train"),
            _node("army", "sequence", ["army.train"]),
            _train_probe("army.train"),
        ],
    )
    error = _issues_of(doc)
    _assert_issue(error, "unbound_binding", "army.train")
    assert "root-local" in str(error)


def test_selector_guarantees_only_bindings_every_branch_produces() -> None:
    doc = _document(
        ["lane"],
        [
            _node("lane", "sequence", ["lane.pick", "lane.train"]),
            _node("lane.pick", "selector", ["lane.pick.nexus", "lane.pick.none"]),
            _nexus_select("lane.pick.nexus"),
            _true_condition("lane.pick.none"),
            _train_probe("lane.train"),
        ],
    )
    _assert_issue(_issues_of(doc), "unbound_binding", "lane.train")
    # When every branch binds the name, the reference is definitely assigned.
    doc["nodes"][3] = _nexus_select("lane.pick.none")
    parse_policy(doc)


def test_binding_kind_mismatch_rejected() -> None:
    doc = _document(
        ["lane"],
        [
            _node("lane", "sequence", ["lane.probe", "lane.build"]),
            _node(
                "lane.probe",
                "select",
                operation="select_entities",
                collection="own_units",
                filter={"types": ["Probe"]},
                limit=1,
                bind="probe",
            ),
            _node(
                "lane.build",
                "action",
                operation="build",
                structure="Pylon",
                worker="$probe",
                site="$probe",
            ),
        ],
    )
    _assert_issue(_issues_of(doc), "binding_kind_mismatch", "lane.build")


def test_validator_collects_every_issue() -> None:
    doc = _minimal_valid()
    doc["nodes"][2]["operation"] = "rush"
    doc["nodes"].append(_true_condition("orphan"))
    doc["roots"].append("ghost_root")
    error = _issues_of(doc)
    assert {"unknown_operation", "unreachable", "missing_root"} <= error.codes()
    assert {"lane.train", "orphan", "ghost_root"} <= error.node_ids()


@pytest.mark.parametrize(
    ("field", "value", "code"),
    [
        ("schema_version", 99, "unsupported_schema_version"),
        ("schema_version", True, "unsupported_schema_version"),
        ("family", "evil\x1b[31m", "invalid_family"),
        ("version", -3, "invalid_version"),
        ("version", 0, "invalid_version"),
        ("version", True, "invalid_version"),
        ("version", "x", "invalid_version"),
    ],
)
def test_identity_is_enforced_for_built_policies(field: str, value: object, code: str) -> None:
    """validate_policy is the single identity gate; JevRuntime refuses bad identity."""
    policy = dataclasses.replace(load_policy().policy, **{field: value})
    with pytest.raises(PolicyError) as excinfo:
        validate_policy(policy)
    assert code in excinfo.value.codes()
    assert "\x1b" not in str(excinfo.value)  # rendered, never raw
    with pytest.raises(PolicyError) as excinfo:
        runtime_module.JevRuntime(policy, run_id=uuid.uuid4().hex)
    assert code in excinfo.value.codes()


def test_record_fields_are_enforced_for_built_policies() -> None:
    base = load_policy().policy
    first = base.nodes[0]

    def with_first(**changes: Any) -> Policy:
        return dataclasses.replace(
            base, nodes=(dataclasses.replace(first, **changes), *base.nodes[1:])
        )

    # Shapes the records cannot hold are refused at construction; the rest by the
    # validator. Either way: a PolicyError with the node-specific code.
    cases: list[tuple[Callable[[], Policy], str, str | None]] = [
        (lambda: dataclasses.replace(base, roots=()), "invalid_roots", None),
        (
            lambda: dataclasses.replace(base, parameters={**base.parameters, "x": [1]}),
            "invalid_parameters",
            None,
        ),
        (lambda: with_first(label=""), "invalid_label", first.id),
        (lambda: with_first(kind="parallel"), "invalid_kind", first.id),
        (lambda: with_first(children="economy.assign"), "invalid_children", first.id),
        (lambda: with_first(args=[]), "invalid_args", first.id),
        (lambda: with_first(operation=7), "invalid_operation", first.id),
        (lambda: with_first(id="Bad Id"), "invalid_node_id", "nodes[0]"),
    ]
    for build, code, node_id in cases:
        with pytest.raises(PolicyError) as excinfo:
            validate_policy(build())
        _assert_issue(excinfo.value, code, node_id)


def test_non_regular_files_are_reported_accurately(tmp_path: Path) -> None:
    with pytest.raises(PolicyError) as excinfo:
        load_policy(tmp_path)  # a directory
    assert excinfo.value.codes() == {"not_a_file"}
    assert "not a regular file" in str(excinfo.value)
    with pytest.raises(PolicyError) as excinfo:
        load_policy(tmp_path / "absent.json")
    assert excinfo.value.codes() == {"missing_file"}
    assert "does not exist" in str(excinfo.value)


def test_document_key_sets_and_wire_records_match_the_contract_fields() -> None:
    """Self-review (d): key sets and to_dict()/to_document() derive from one shape."""
    from jev import contracts

    def field_names(record: type[Any]) -> set[str]:
        return {f.name for f in dataclasses.fields(record) if f.init}

    policy = load_policy().policy
    assert policy_module.POLICY_KEYS == field_names(Policy) == set(policy.to_document())
    assert policy_module.NODE_KEYS == field_names(PolicyNode)
    assert policy_module.NODE_KEYS == set(policy.nodes[0].to_document())
    assert policy_module.MANIFEST_KEYS == field_names(Manifest)
    task = contracts.Task("t", "n", "k", 1, None, "issued", 0.0, None, 1, None, "r")
    command = contracts.CommandSpec("n", "t", "MOVE", (1,), (0.0, 0.0))
    event = contracts.Event("r", 1, 0, 0.0, "n", None, "node", "success", "ok", {}, None)
    error = contracts.JevError("invalid_policy", "m")
    state = contracts.RunState(
        "r", "jev", 1, "h", "running", "t", 0.0, 0, (), (), (task,), (event,)
    )
    metadata = contracts.RunMetadata(
        "r", "t", "jev", 1, "h", "c", "Simple64", "Terran", 1, 1, 9.0, 9.0
    )
    for record in (task, command, event, error, state, metadata):
        assert set(record.to_dict()) == field_names(type(record)), type(record).__name__


def test_argument_choices_are_the_shared_constants() -> None:
    """Specs reference the one source (``is``), so re-duplicating a list fails CI."""
    ops, specs = operations_module, policy_module.OPERATIONS
    assert specs["count_compare"].args["op"].choices is ops.COMPARATORS
    assert specs["count_compare"].args["collection"].choices is ops.ENTITY_COLLECTIONS
    assert specs["resource_compare"].args["resource"].choices is ops.RESOURCE_NAMES
    assert specs["task_count_compare"].args["statuses"].choices is ops.TASK_STATUSES
    assert specs["set_latch"].args["latch"].choices is ops.LATCHES
    doc = _minimal_valid()
    doc["nodes"][1]["args"]["sort"] = {"by": "tag", "order": "sideways"}
    _assert_issue(_issues_of(doc), "invalid_arg", "lane.nexus")


def _reference_matches(entity: Entity, raw: Any, start: tuple[float, float]) -> bool:
    """Independent reference of the documented JSON filter semantics (AND; any_of = OR)."""
    if not raw:  # absent or {}: match everything
        return True
    flags = {
        "ready": entity.is_ready,
        "idle": entity.is_idle,
        "powered": entity.is_powered,
        "flying": entity.is_flying,
        "structure": entity.is_structure,
        "under_construction": entity.build_progress < 1.0,
    }
    if "types" in raw and entity.type_name not in raw["types"]:
        return False
    if "exclude_types" in raw and entity.type_name in raw["exclude_types"]:
        return False
    if any(key in raw and flags[key] != raw[key] for key in flags):
        return False
    if "order_in" in raw and entity.current_ability not in raw["order_in"]:
        return False
    if "within" in raw:
        dx, dy = entity.position[0] - start[0], entity.position[1] - start[1]
        if (dx * dx + dy * dy) ** 0.5 > raw["within"]["distance"]:
            return False
    if "any_of" in raw and not any(_reference_matches(entity, b, start) for b in raw["any_of"]):
        return False
    return True


_FILTER_SHAPES: list[Any] = [
    {},
    {"any_of": [{}, {"types": ["Zealot"]}]},  # empty branch => match everything
    {"any_of": [{"types": ["Zealot"]}, {}]},
    {"any_of": [{"any_of": [{}, {"types": ["Nexus"]}]}]},
    {"any_of": [{"types": ["Zealot"]}, {"idle": True}]},
    {"types": ["Probe"], "any_of": [{"idle": True}, {"order_in": ["HARVEST_GATHER"]}]},
    {"exclude_types": ["Probe"], "ready": True},
    {"within": {"point": "start_location", "distance": 5}, "any_of": [{"flying": True}, {}]},
    {"under_construction": True},
    {"any_of": [{"powered": True}, {"structure": False}]},
    {"types": ["Gateway", "Pylon"], "any_of": [{"powered": False}, {"under_construction": True}]},
]


@pytest.mark.parametrize("raw", _FILTER_SHAPES)
@pytest.mark.parametrize("collection", ["own_units", "own_structures"])
def test_compiled_filters_match_the_reference_semantics(raw: Any, collection: str) -> None:
    """Compiled matching (validator -> runtime) equals the JSON-level semantics."""
    from jev.contracts import Order

    start = (10.0, 10.0)
    units = (
        Entity(1, "Probe", (11.0, 10.0), 20.0),
        Entity(2, "Probe", (30.0, 10.0), 20.0, orders=(Order("HARVEST_GATHER", 99),)),
        Entity(3, "Zealot", (12.0, 12.0), 150.0, orders=(Order("ATTACK", (50.0, 50.0)),)),
        Entity(4, "Zealot", (40.0, 40.0), 150.0, is_flying=True),
    )
    structures = (
        Entity(10, "Nexus", start, 1000.0, is_structure=True, ready=True, idle=True),
        Entity(11, "Pylon", (14.0, 10.0), 200.0, build_progress=0.4, is_structure=True),
        Entity(12, "Gateway", (16.0, 12.0), 500.0, is_structure=True, ready=True, powered=True),
        Entity(13, "Gateway", (60.0, 60.0), 500.0, is_structure=True, ready=True, powered=False),
    )
    policy = parse_policy(
        _document(
            ["lane"],
            [
                _node("lane", "sequence", ["lane.count"]),
                _node(
                    "lane.count",
                    "condition",
                    operation="count_compare",
                    collection=collection,
                    filter=raw,
                    op=">=",
                    value=0,
                ),
            ],
        )
    )
    observation = Observation(
        game_loop=0,
        game_seconds=0.0,
        minerals=0,
        supply_used=0,
        supply_cap=15,
        own_units=units,
        own_structures=structures,
        visible_enemies=(),
        remembered_enemy_structures=(),
        start_location=start,
        enemy_start_locations=(),
        expansion_locations=(),
        map_center=(50.0, 50.0),
    )
    result = runtime_module.JevRuntime(policy, run_id=uuid.uuid4().hex).tick(observation)
    (event,) = [e for e in result.events if e.node_id == "lane.count"]
    entities = units if collection == "own_units" else structures
    expected = sum(1 for e in entities if _reference_matches(e, raw, start))
    assert event.facts["count"] == expected


def test_duplicate_child_and_duplicate_root_are_node_specific() -> None:
    doc = _minimal_valid()
    doc["nodes"][0]["children"].append("lane.train")
    error = _issues_of(doc)
    _assert_issue(error, "duplicate_child", "lane")
    assert "'lane.train'" in str(error)
    doc = _minimal_valid()
    doc["roots"].append("lane")
    _assert_issue(_issues_of(doc), "duplicate_root", "lane")


def test_game_data_tables_agree_on_their_key_sets() -> None:
    ops = operations_module
    assert set(ops.TRAIN_ABILITY) == set(ops.SUPPLY_COST) == set(ops.PRODUCER_TYPE)
    assert set(ops.BUILD_ABILITY) | set(ops.TRAIN_ABILITY) <= set(ops.MINERAL_COST)
    assert set(ops.BUILD_ABILITY) | set(ops.PRODUCER_TYPE.values()) <= set(ops.FOOTPRINT)


def test_observation_lookup_names_are_its_field_names() -> None:
    from jev import contracts

    fields = {f.name for f in dataclasses.fields(contracts.Observation)}
    names = (*contracts.ENTITY_COLLECTIONS, *contracts.LOCATION_SOURCES, *contracts.POINT_KEYWORDS)
    assert set(names) <= fields


def test_own_type_allowlist_matches_d3_and_rejects_other_units() -> None:
    # D3: one Protoss base, Probes and Zealots only -- no gas, no expansion, no tech.
    assert operations_module.OWN_TYPES == {"Probe", "Zealot", "Nexus", "Pylon", "Gateway"}
    for foreign in ("Assimilator", "Stalker", "CyberneticsCore"):
        doc = _minimal_valid()
        doc["nodes"][1]["args"]["filter"] = {"types": [foreign]}
        error = _issues_of(doc)
        _assert_issue(error, "invalid_arg", "lane.nexus")
        assert foreign in str(error)


def test_sort_keys_and_distance_caps_have_one_source_of_truth() -> None:
    spec = policy_module.OPERATIONS["select_entities"].args["sort"]
    assert spec.choices is operations_module.SORT_KEYS
    assert set(operations_module.ENTITY_SORT_METRICS) == set(operations_module.SORT_KEYS)
    locations = policy_module.OPERATIONS["select_locations"].args
    assert locations["sort"].choices is operations_module.LOCATION_SORT_KEYS
    assert locations["limit"].maximum == operations_module.MAX_LOCATIONS
    for name, arg in (("threat_within", "distance"), ("select_point", "distance")):
        assert (
            policy_module.OPERATIONS[name].args[arg].maximum == operations_module.MAX_QUERY_DISTANCE
        )
    over = operations_module.MAX_QUERY_DISTANCE + 1
    issues = validate_operation_args(
        "count_compare",
        {
            "collection": "own_units",
            "filter": {"within": {"point": "start_location", "distance": over}},
            "op": ">=",
            "value": 0,
        },
        parameters={},
        node_kinds={},
    )
    assert [code for code, _ in issues] == ["invalid_arg"]


def test_unknown_sort_keys_are_rejected_by_the_validator() -> None:
    doc = _minimal_valid()
    doc["nodes"][1]["args"]["sort"] = {"by": "bogus"}
    _assert_issue(_issues_of(doc), "invalid_arg", "lane.nexus")
    doc = _document(
        ["lane"],
        [
            _node("lane", "sequence", ["lane.where"]),
            _node(
                "lane.where",
                "select",
                operation="select_locations",
                source="expansion_locations",
                sort={"by": "tag"},  # locations only sort by distance
                limit=1,
                bind="where",
            ),
        ],
    )
    _assert_issue(_issues_of(doc), "invalid_arg", "lane.where")


def test_runtime_selection_orders_large_tags_exactly() -> None:
    """Tags above 2**53 must order as exact ints (a float key would collapse them)."""
    big = 2**60
    policy = parse_policy(
        _document(
            ["lane"],
            [
                _node("lane", "sequence", ["lane.pick"]),
                _node(
                    "lane.pick",
                    "select",
                    operation="select_entities",
                    collection="own_units",
                    sort={"by": "tag", "order": "desc"},
                    limit=2,
                    bind="picked",
                ),
            ],
        )
    )
    probes = tuple(
        Entity(tag=tag, type_name="Probe", position=(0.0, 0.0), health=20.0)
        for tag in (big, big + 1)
    )
    observation = Observation(
        game_loop=0,
        game_seconds=0.0,
        minerals=0,
        supply_used=2,
        supply_cap=15,
        own_units=probes,
        own_structures=(),
        visible_enemies=(),
        remembered_enemy_structures=(),
        start_location=(0.0, 0.0),
        enemy_start_locations=(),
        expansion_locations=(),
        map_center=(10.0, 10.0),
    )
    result = runtime_module.JevRuntime(policy, run_id=uuid.uuid4().hex).tick(observation)
    (pick,) = [e for e in result.events if e.node_id == "lane.pick"]
    assert pick.facts["tags"] == [str(big + 1), str(big)]


@pytest.mark.parametrize(
    ("make", "code", "node_id"),
    [
        pytest.param(
            lambda d: d["nodes"][2].update(id="lane.train\n"),
            "invalid_node_id",
            "nodes[2]",
            id="node-id",
        ),
        pytest.param(
            lambda d: (
                d["nodes"][1]["args"].update(bind="nexus\n"),
                d["nodes"][2]["args"].update(producers="$nexus\n"),
            ),
            "invalid_arg",
            "lane.nexus",
            id="bind-and-ref",
        ),
        pytest.param(
            lambda d: d["nodes"][2]["args"].update(producers="$nexus\n"),
            "invalid_arg",
            "lane.train",
            id="ref",
        ),
        pytest.param(
            lambda d: d.update(parameters={"x\n": 1}),
            "invalid_parameters",
            None,
            id="parameter-name",
        ),
    ],
)
def test_trailing_newline_never_satisfies_a_shape_check(
    make: Any, code: str, node_id: str | None
) -> None:
    doc = _minimal_valid()
    make(doc)
    _assert_issue(_issues_of(doc), code, node_id)


def test_manifest_shape_fields_reject_trailing_newlines() -> None:
    from jev.contracts import NAME_RE, full_match

    assert not full_match(NAME_RE, 7)  # non-strings never satisfy a shape check
    manifest = json.loads((PACKAGE_DIR / "manifest.json").read_text(encoding="utf-8"))
    for field, value in (("entrypoint", "bots.jev.v1\n"), ("policy_file", "policy.json\n")):
        with pytest.raises(PolicyError):
            parse_manifest({**manifest, field: value})


def _fill(prefix: str, unit: str, suffix: str = "") -> bytes:
    """``prefix + unit * n + suffix`` as large as the policy byte limit allows."""
    room = policy_module.MAX_POLICY_BYTES - 512 - len(prefix) - len(suffix)
    return (prefix + unit * (room // len(unit)) + suffix).encode("utf-8")


def _compact(doc: dict[str, Any]) -> bytes:
    return json.dumps(doc, separators=(",", ":")).encode("utf-8")


def _room(doc: dict[str, Any]) -> int:
    return policy_module.MAX_POLICY_BYTES - 512 - len(_compact(doc))


def _wc_back_edges(chain: int = 2000, duplicates: int | None = None) -> dict[str, Any]:
    """Long chain whose last node lists the first node as a child many times."""
    nodes = [_node(f"n{i}", "sequence", [f"n{i + 1}"]) for i in range(chain - 1)]
    nodes.append(_node(f"n{chain - 1}", "sequence"))
    doc = _document(["n0"], nodes)
    nodes[-1]["children"] = ["n0"] * (duplicates if duplicates is not None else _room(doc) // 5)
    return doc


def _wc_dense() -> dict[str, Any]:
    ids = [f"d{i}" for i in range(340)]
    return _document(["d0"], [_node(i, "sequence", [c for c in ids if c != i]) for i in ids])


def _wc_long_chain() -> dict[str, Any]:
    count = policy_module.MAX_NODES
    nodes = [_node(f"c{i}", "sequence", [f"c{i + 1}"]) for i in range(count - 1)]
    return _document(["c0"], [*nodes, _true_condition(f"c{count - 1}")])


def _wc_wide_fanout() -> dict[str, Any]:
    leaves = [f"w{i}" for i in range(policy_module.MAX_NODES - 1)]
    return _document(["w"], [_node("w", "sequence", leaves), *map(_true_condition, leaves)])


def _wc_missing_children() -> dict[str, Any]:
    doc = _document(["m"], [_node("m", "sequence")])
    doc["nodes"][0]["children"] = [f"g{i}" for i in range(_room(doc) // 10)]
    return doc


def _wc_bindings(selects: int = 3937) -> dict[str, Any]:
    """Many distinct names bound in sequences, each visible to later siblings."""
    groups = selects // 31
    nodes = [_node("b", "sequence", [f"b.g{g}" for g in range(groups)])]
    for g in range(groups):
        kids = [f"b.g{g}.s{k}" for k in range(31)]
        nodes.append(_node(f"b.g{g}", "sequence", kids))
        nodes.extend(
            _node(
                kid,
                "select",
                operation="select_locations",
                source="expansion_locations",
                limit=1,
                bind=f"v{g}x{k}",
            )
            for k, kid in enumerate(kids)
        )
    return _document(["b"], nodes)


def _wc_selector_merge() -> dict[str, Any]:
    """Selectors merging alternatives over a large existing environment."""
    nodes = [_node("s", "sequence", [f"s.p{i}" for i in range(15)] + ["s.choice"])]
    for i in range(15):
        kids = [f"s.p{i}.k{k}" for k in range(127)]
        nodes.append(_node(f"s.p{i}", "sequence", kids))
        nodes.extend(
            _node(
                kid,
                "select",
                operation="select_locations",
                source="expansion_locations",
                limit=1,
                bind=f"q{i}x{k}",
            )
            for k, kid in enumerate(kids)
        )
    nodes.append(_node("s.choice", "selector", [f"s.alt{a}" for a in range(127)]))
    nodes.extend(
        _node(
            f"s.alt{a}",
            "select",
            operation="select_locations",
            source="expansion_locations",
            limit=1,
            bind=f"alt{a % 7}",
        )
        for a in range(127)
    )
    return _document(["s"], nodes)


def _wc_parameters() -> dict[str, Any]:
    doc = _document(["z"], [_true_condition("z")])
    doc["parameters"] = {f"p{i}": i for i in range(_room(doc) // 16)}
    return doc


def _wc_unknown_args() -> dict[str, Any]:
    doc = _document(["z"], [_true_condition("z")])
    doc["nodes"][0]["args"].update({f"u{i}": 0 for i in range(_room(doc) // 12)})
    return doc


def _wc_type_list() -> dict[str, Any]:
    doc = _document(
        ["z"],
        [
            _node(
                "z",
                "select",
                operation="select_entities",
                collection="own_units",
                filter={"types": []},
                limit=1,
                bind="b",
            )
        ],
    )
    doc["nodes"][0]["args"]["filter"]["types"] = [f"T{i}" for i in range(_room(doc) // 10)]
    return doc


def _wc_surrogates() -> bytes:
    head = _compact(_document(["z"], [_true_condition("z")]))[:-1].decode() + ',"pad":['
    return _fill(head, '"\\ud800",', '"x"]}')


#: Worst-case families at near-max size (~1 MiB) for EVERY pass over untrusted input:
#: byte-level pre-parse guard, json.loads, value scan, schema, forest structure,
#: arity, references, outcomes, bindings, argument checks, parameters, rendering.
WORST_CASE_INPUTS: list[tuple[str, Callable[[], bytes]]] = [
    ("quote-backslash-run", lambda: _fill("", '"\\\\')),  # the old regex prescan's ReDoS
    ("quote-backslash-in-array", lambda: _fill("[", '"\\\\')),
    ("unterminated-string", lambda: _fill('{"a": "', "a")),
    ("escape-heavy-string", lambda: _fill('{"a": "', "\\\\n", '"}')),
    ("deep-brackets", lambda: _fill("", "[")),
    ("deep-then-wide", lambda: _fill('{"nodes": ' + "[" * 63, "0,", "0" + "]" * 63 + "}")),
    (
        "many-unknown-keys",
        lambda: ("{" + ",".join(f'"k{i}": 0' for i in range(85_000)) + "}").encode(),
    ),
    ("duplicate-back-edges", lambda: _compact(_wc_back_edges())),
    ("dense-graph", lambda: _compact(_wc_dense())),
    ("long-chain", lambda: _compact(_wc_long_chain())),
    ("wide-fanout", lambda: _compact(_wc_wide_fanout())),
    ("many-missing-children", lambda: _compact(_wc_missing_children())),
    ("many-distinct-bindings", lambda: _compact(_wc_bindings())),
    ("selector-merge", lambda: _compact(_wc_selector_merge())),
    ("many-parameters", lambda: _compact(_wc_parameters())),
    ("many-unknown-arg-keys", lambda: _compact(_wc_unknown_args())),
    ("long-type-list", lambda: _compact(_wc_type_list())),
    ("lone-surrogate-strings", _wc_surrogates),
]


@pytest.mark.parametrize(
    "payload", [pytest.param(build, id=case) for case, build in WORST_CASE_INPUTS]
)
def test_worst_case_inputs_are_validated_in_bounded_time_and_issues(
    tmp_path: Path, payload: Callable[[], bytes]
) -> None:
    """Invariant D: every validation pass is ~O(n log n) in time and memory.

    Each family is near the byte limit and goes through the public loader. Before
    the fix, duplicate back-edges took ~17 s (172k issues) and a dense graph ~420 MB
    (58k issues); the removed regex prescan needed ~5 s per 40 KB of ``"\\\\``.
    """
    data = payload()
    assert len(data) <= policy_module.MAX_POLICY_BYTES
    candidate = tmp_path / "worst.json"
    candidate.write_bytes(data)
    started = time.perf_counter()
    try:
        load_policy(candidate)
        issues: tuple[Any, ...] = ()
    except PolicyError as exc:
        issues = exc.issues
        assert len(str(exc).splitlines()) <= policy_module.MAX_REPORTED_ISSUES + 3
    elapsed = time.perf_counter() - started
    # Generous for slow CI runners; quadratic behaviour here measured minutes-to-hours.
    assert elapsed < 15.0, f"{elapsed:.2f}s"
    assert len(issues) <= policy_module.MAX_REPORTED_ISSUES + 1  # generation is capped


# --- caller-built structures: DAGs, cycles, huge fan-out, hostile objects -------


def _dag_list(depth: int = 40) -> list[Any]:
    """Shared-subobject DAG: ``depth`` objects in memory, 2**depth leaves expanded."""
    value: list[Any] = []
    for _ in range(depth):
        value = [value, value]
    return value


def _dag_dict(depth: int = 40) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for _ in range(depth):
        value = {"a": value, "b": value}
    return value


def _cyclic_dict() -> dict[str, Any]:
    loop: dict[str, Any] = {}
    loop["self"] = loop
    return loop


class _RaisingRepr:
    def __repr__(self) -> str:
        raise RuntimeError("hostile __repr__")

    __str__ = __repr__


class _RaisingStrError(Exception):
    def __str__(self) -> str:
        raise RuntimeError("hostile __str__")


def _with_args(value: object) -> dict[str, Any]:
    doc = _minimal_valid()
    doc["nodes"][1]["args"]["extra"] = value
    return doc


#: Every function that walks a caller-supplied (possibly non-JSON) structure, fed
#: the hostile shapes JSON text can never produce. Before the element budget,
#: ``freeze_json`` on ``x = [x, x]`` took 0.13 s at depth 16 and 10 s at depth 22;
#: these are depth 40 (2**40 expanded leaves), so an exponential walk never ends.
CALLER_BUILT_WORST_CASES: list[tuple[str, Callable[[], object], str]] = [
    ("freeze-dag", lambda: freeze_json(_dag_list()), "too_large"),
    ("freeze-cyclic", lambda: freeze_json(_cyclic_dict()), "too_deep"),
    ("freeze-fan-out", lambda: freeze_json([0] * 1_000_001), "too_large"),
    ("thaw-dag", lambda: thaw_json(_dag_dict()), "too_large"),
    ("policy-parameters-dag", lambda: Policy(1, "jev", 1, ("a",), _dag_dict(), ()), "too_large"),
    (
        "policy-parameters-cyclic",
        lambda: Policy(1, "jev", 1, ("a",), _cyclic_dict(), ()),
        "too_deep",
    ),
    (
        "node-args-dag",
        lambda: PolicyNode("a", "a", "sequence", (), None, {"x": _dag_list()}),
        "too_large",
    ),
    (
        "node-args-in-frozen-wrapper",  # a pre-built Frozen* container is walked too
        lambda: PolicyNode("a", "a", "sequence", (), None, FrozenJsonDict({"x": _dag_list()})),
        "too_large",
    ),
    (
        "node-id-dag-in-error-path",
        lambda: PolicyNode(_dag_list(), "a", "sequence", 5, None, {}),
        "invalid_children",
    ),
    ("parse-args-dag", lambda: parse_policy(_with_args(_dag_list())), "too_large"),
    ("parse-args-cyclic", lambda: parse_policy(_with_args(_cyclic_dict())), "too_deep"),
    ("parse-fan-out", lambda: parse_policy(_with_args([0] * 1_000_001)), "too_large"),
    ("hash-dag", lambda: policy_hash({"a": _dag_dict()}), "too_large"),
    ("hash-cyclic", lambda: policy_hash(_cyclic_dict()), "too_deep"),
    ("canonical-dag", lambda: canonical_json({"a": _dag_list()}), "too_large"),
    (
        "value-issues-dag",
        lambda: policy_module.json_value_issues({"a": _dag_list()}, "x"),
        "too_large",
    ),
    ("validate-not-a-policy", lambda: validate_policy(_dag_list()), "unprocessable_input"),
]


@pytest.mark.parametrize(
    ("call", "code"),
    [pytest.param(call, code, id=case) for case, call, code in CALLER_BUILT_WORST_CASES],
)
def test_caller_built_structures_are_walked_in_bounded_time(
    call: Callable[[], object], code: str
) -> None:
    """Class sweep: every structure walker rejects DAG/cyclic/fan-out input, bounded."""
    started = time.perf_counter()
    try:
        outcome = call()
    except PolicyError as exc:
        codes = exc.codes()
        _assert_bounded_error(exc)
    else:  # json_value_issues returns its issues instead of raising
        assert isinstance(outcome, list)
        codes = {issue.code for issue in outcome}
    assert code in codes
    assert time.perf_counter() - started < 15.0  # measured ~1.5 s worst; exponential never ends


@pytest.mark.parametrize(
    "value",
    [
        pytest.param(_dag_list(), id="dag-list"),
        pytest.param(_dag_dict(), id="dag-dict"),
        pytest.param(_cyclic_dict(), id="cyclic"),
        pytest.param(_RaisingRepr(), id="raising-repr"),
        pytest.param([_RaisingRepr(), {_RaisingRepr()}], id="raising-repr-nested"),
        pytest.param(FrozenJsonList([_dag_list()]), id="dag-in-subclass"),
        pytest.param([10**5000, "\x1b" * 500, b"\x00" * 500], id="huge-leaves"),
        pytest.param(list(range(2_000_000)), id="fan-out"),
    ],
)
def test_safe_repr_is_total_and_bounded_on_hostile_values(value: object) -> None:
    """THE renderer never raises, never runs caller code and is bounded in time/size."""
    started = time.perf_counter()
    text = safe_repr(value)
    assert time.perf_counter() - started < 1.0
    assert len(text) <= MAX_FRAGMENT_CHARS + 3
    assert text.isprintable()
    node_issue = PolicyIssue("x", "m", value)
    assert node_issue.node_id is not None and len(node_issue.node_id) <= MAX_FRAGMENT_CHARS + 3
    assert len(contracts_module.safe_exception_text(ValueError(value))) <= MAX_FRAGMENT_CHARS + 3


def test_safe_repr_falls_back_to_the_type_name_for_hostile_objects() -> None:
    assert safe_repr(_RaisingRepr()) == "<_RaisingRepr object>"
    assert contracts_module.safe_exception_text(_RaisingStrError()) == "<_RaisingStrError>"
    assert safe_repr([1, (2,), {"k": None}, set(), frozenset({3})]) == (
        "[1, (2,), {'k': None}, set(), frozenset({3})]"
    )


@pytest.mark.parametrize(
    "values",
    [(["finished"], "win", None), ("finished", {"win": 1}, None), ("failed", None, 7)],
    ids=["list-status", "dict-result", "int-code"],
)
def test_the_run_outcome_check_is_total_over_untrusted_values(
    values: tuple[object, object, object],
) -> None:
    """``is_run_outcome`` judges untrusted documents: unhashable or ill-typed values
    are not an outcome, never a TypeError."""
    assert contracts_module.is_run_outcome(*values) is False


_HUGE = "k" * 300_000


def _assert_bounded_error(error: PolicyError) -> None:
    """Invariant E: every rendered line is short; the whole error is bounded."""
    lines = str(error).splitlines()
    assert max(len(line) for line in lines) < 700, max(len(line) for line in lines)
    for issue in error.issues:
        assert len(issue.message) <= policy_module.MAX_MESSAGE_CHARS + 40
        assert issue.node_id is None or len(issue.node_id) <= MAX_FRAGMENT_CHARS + 3
    budget = (policy_module.MAX_REPORTED_ISSUES + 3) * (policy_module.MAX_MESSAGE_CHARS + 400)
    assert len(str(error)) < budget


def _huge_operation(doc: dict[str, Any]) -> None:
    doc["nodes"][2]["operation"] = "o" * 300_000


def _huge_children(doc: dict[str, Any]) -> None:
    doc["nodes"][0]["children"] += ["c" * 200_000, "d" * 200_000]


def _huge_root(doc: dict[str, Any]) -> None:
    doc["roots"].append("r" * 300_000)


def _huge_param_ref(doc: dict[str, Any]) -> None:
    doc["nodes"][1]["args"]["limit"] = {"param": "p" * 300_000}


def _huge_node_ref(doc: dict[str, Any]) -> None:
    doc["nodes"][0]["children"].insert(0, "lane.check")
    doc["nodes"].append(
        _node(
            "lane.check",
            "condition",
            operation="task_count_compare",
            node="n" * 300_000,
            statuses=["issued"],
            op="==",
            value=0,
        )
    )


def _huge_key(doc: dict[str, Any]) -> None:
    doc[_HUGE] = 1


@pytest.mark.parametrize(
    "mutate",
    [
        pytest.param(_huge_key, id="huge-unknown-key"),
        pytest.param(_huge_operation, id="huge-operation-name"),
        pytest.param(_huge_children, id="huge-child-names"),
        pytest.param(_huge_root, id="huge-root-id"),
        pytest.param(_huge_param_ref, id="huge-parameter-reference"),
        pytest.param(_huge_node_ref, id="huge-node-reference"),
    ],
)
def test_untrusted_text_in_messages_is_capped(tmp_path: Path, mutate: Any) -> None:
    doc = _minimal_valid()
    mutate(doc)
    candidate = tmp_path / "huge.json"
    candidate.write_text(json.dumps(doc), encoding="utf-8")  # < 1 MiB: reaches validation
    with pytest.raises(PolicyError) as excinfo:
        load_policy(candidate)
    _assert_bounded_error(excinfo.value)


@pytest.mark.parametrize(
    "text",
    [
        pytest.param('{"' + _HUGE + '": 1, "' + _HUGE + '": 2}', id="duplicate-huge-keys"),
        pytest.param('{"a": ' + "1" * 300_000 + "e999}", id="huge-non-finite-literal"),
    ],
)
def test_parse_errors_with_huge_text_are_capped(text: str) -> None:
    with pytest.raises(PolicyError) as excinfo:
        parse_json_document(text, what="policy.json", max_bytes=1_048_576)
    _assert_bounded_error(excinfo.value)


def test_policy_issue_caps_message_and_node_id_centrally() -> None:
    """Backstop: even an uncapped producer cannot emit an unbounded issue."""
    issue = PolicyIssue("x", "y" * 100_000, "n" * 1_000)
    assert len(issue.message) <= policy_module.MAX_MESSAGE_CHARS + 40
    assert issue.message.endswith("chars truncated)")
    assert issue.node_id is not None and len(issue.node_id) == MAX_FRAGMENT_CHARS + 3
    legal_id = "a" * 128  # NODE_ID_MAX_LENGTH: never truncated
    assert PolicyIssue("x", "m", legal_id).node_id == legal_id
    assert safe_repr("z" * 1_000).endswith("... (+840 chars)")


def test_schema_phase_stops_generating_at_the_issue_cap() -> None:
    nodes = [_node(f"n{i}", "parallel") for i in range(2_000)]  # every node has a bad kind
    error = _issues_of(_document(["n0"], nodes))
    assert len(error.issues) == policy_module.MAX_REPORTED_ISSUES + 1
    assert error.issues[-1].code == "issues_truncated"


def test_filter_name_lists_are_capped() -> None:
    def enemy_types(count: int) -> dict[str, Any]:
        return _document(
            ["lane"],
            [
                _node("lane", "sequence", ["lane.pick"]),
                _node(
                    "lane.pick",
                    "select",
                    operation="select_entities",
                    collection="visible_enemies",
                    filter={"types": [f"T{i}" for i in range(count)]},
                    limit=1,
                    bind="enemy",
                ),
            ],
        )

    limit = operations_module.MAX_FILTER_NAMES
    parse_policy(enemy_types(limit))
    error = _issues_of(enemy_types(limit + 1))
    _assert_issue(error, "invalid_arg", "lane.pick")
    assert f"at most {limit}" in str(error)


def test_node_status_sets_derive_from_the_literal() -> None:
    from jev.contracts import NODE_STATUSES, NodeStatus

    assert NODE_STATUSES == get_args(NodeStatus)


def _plain_json(value: Any) -> bool:
    if type(value) is dict:
        return all(isinstance(k, str) and _plain_json(v) for k, v in value.items())
    if type(value) is list:
        return all(_plain_json(item) for item in value)
    return value is None or isinstance(value, str | int | float)


def test_validated_policy_state_is_immutable_and_round_trips() -> None:
    raw = _shipped_document()
    policy = parse_policy(copy.deepcopy(raw))
    assert isinstance(policy.roots, tuple) and isinstance(policy.nodes, tuple)
    assert all(isinstance(node.children, tuple) for node in policy.nodes)
    picked = policy.node("economy.idle_probes")
    mutations: list[Callable[[], object]] = [
        lambda: policy.parameters.__setitem__("probe_target", 99),
        lambda: picked.args.__setitem__("limit", 1),
        lambda: picked.args["filter"]["types"].append("Zealot"),
        lambda: picked.args["filter"].update(idle=False),
    ]
    for mutate in mutations:
        with pytest.raises(TypeError):
            mutate()
    document = policy.to_document()
    assert document == raw and _plain_json(document)  # thawed back to plain JSON
    assert policy_hash(policy) == policy_hash(raw) == load_policy().policy_hash
    document["parameters"]["probe_target"] = 99  # the thawed copy belongs to the caller
    assert policy.parameters["probe_target"] == 16
    assert copy.deepcopy(policy) == policy


@pytest.mark.parametrize(
    ("build", "code"),
    [
        pytest.param(
            lambda: Policy(1, "jev", 1, ("lane",), {}, None), "invalid_nodes", id="nodes-none"
        ),
        pytest.param(
            lambda: Policy(1, "jev", 1, ("lane",), {}, ("not a node",)),
            "invalid_nodes",
            id="bad-node",
        ),
        pytest.param(lambda: Policy(1, "jev", 1, None, {}, ()), "invalid_roots", id="roots-none"),
        pytest.param(
            lambda: Policy(1, "jev", 1, (), [], ()), "invalid_parameters", id="parameters-list"
        ),
    ],
)
def test_malformed_policy_construction_raises_policy_error(build: Any, code: str) -> None:
    with pytest.raises(PolicyError) as excinfo:
        build()
    assert excinfo.value.codes() == {code}


def test_canonical_hash_distinguishes_json_documents() -> None:
    # Values that json.dumps would coerce into one another are distinct documents.
    distinct = [{"a": 1}, {"a": "1"}, {"a": 1.0}, {"a": True}, {"a": [1]}, {"a": None}, {"1": 2}]
    hashes = [policy_hash(document) for document in distinct]
    assert len(set(hashes)) == len(distinct)
    # Inputs that are not JSON (and would otherwise collide after coercion) are refused.
    for bad in ({1: 2}, {"a": (1, 2)}, {"a": {3}}, {"a": math.nan}):
        with pytest.raises(PolicyError):
            canonical_json(bad)


def test_resolved_args_are_fresh_read_only_copies() -> None:
    for spec in policy_module.OPERATIONS.values():
        for arg in spec.args.values():  # registry defaults are immutable scalars
            assert arg.default is None or isinstance(arg.default, bool | int | float | str)
    policy = load_policy().policy
    picked = policy.node("economy.idle_probes")
    first = operations_module.resolve_args("select_entities", picked.args, policy.parameters)
    second = operations_module.resolve_args("select_entities", picked.args, policy.parameters)
    assert first == second and first["filter"] is not second["filter"]
    with pytest.raises(TypeError):
        first["filter"]["types"].append("Zealot")
    with pytest.raises(TypeError):
        first["limit"] = 1


@pytest.mark.parametrize(
    "call",
    [
        pytest.param(lambda: parse_policy(None), id="parse-none"),
        pytest.param(lambda: parse_policy([]), id="parse-list"),
        pytest.param(lambda: parse_policy("policy"), id="parse-str"),
        pytest.param(lambda: parse_policy(10**400), id="parse-huge-int"),
        pytest.param(lambda: validate_policy(None), id="validate-none"),
        pytest.param(lambda: validate_policy({"nodes": []}), id="validate-dict"),
        pytest.param(lambda: policy_hash(None), id="hash-none"),
        pytest.param(lambda: policy_hash([1, 2]), id="hash-list"),
        pytest.param(lambda: canonical_json("x"), id="canonical-str"),
        pytest.param(lambda: parse_manifest(None), id="manifest-none"),
        pytest.param(lambda: parse_json_document(None, what="x", max_bytes=10), id="json-none"),
        pytest.param(lambda: load_policy("not-a-path-object"), id="bundle-str-path"),
    ],
)
def test_public_policy_entry_points_raise_only_policy_error(call: Callable[[], object]) -> None:
    """Bugs-lens sweep: wrong types at every public policy boundary are PolicyErrors."""
    with pytest.raises(PolicyError):
        call()


# ---------------------------------------------------------------------------
# Identity, packaging and import boundaries
# ---------------------------------------------------------------------------


def test_legacy_registry_ignores_jev_family() -> None:
    assert not (REPO_ROOT / "bots" / "jev" / "VERSION").exists()
    versions = registry.list_versions()
    assert versions, "legacy versions should still be discovered"
    assert "jev" not in versions
    assert all(re.fullmatch(r"v\d+", v) for v in versions), versions


def _imported_modules(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found: set[str] = set()
    for item in ast.walk(tree):
        if isinstance(item, ast.Import):
            found.update(alias.name for alias in item.names)
        elif isinstance(item, ast.ImportFrom) and item.module:
            found.add(item.module)
    return found


def test_jev_sources_never_import_legacy_bot_trees() -> None:
    """CLAUDE.md: no ``bots.current`` / ``bots.<version>`` imports from src/.

    Kept as a static scan because it covers every module, including ``jev.runtime``,
    which the validation-path subprocess test never imports. Heavy-dependency
    checks (sc2/torch/anthropic) are left to that runtime ``sys.modules`` test.
    """
    for path in sorted((REPO_ROOT / "src" / "jev").glob("*.py")):
        for module in _imported_modules(path):
            assert module != "bots" and not module.startswith("bots."), (path.name, module)
    for path in sorted((REPO_ROOT / "bots" / "jev").rglob("*.py")):
        for module in _imported_modules(path):
            legacy = module == "bots.current" or re.fullmatch(r"bots\.v\d+(\..*)?", module)
            assert not legacy, (path, module)


def _run_cli(*args: str, cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "bots.jev.v1", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )


def test_cli_validate_policy_prints_hash_and_exits_zero(tmp_path: Path) -> None:
    expected = load_policy().policy_hash
    # Run from an unrelated cwd: the packaged policy must not resolve via cwd.
    proc = _run_cli("--validate-policy", cwd=tmp_path)
    assert proc.returncode == 0, proc.stderr
    assert f"policy_hash={expected}" in proc.stdout
    assert "v1.jev" in proc.stdout
    assert "roots=economy,construction,production,army" in proc.stdout


def test_cli_invalid_candidate_policy_exits_nonzero_with_node_error(tmp_path: Path) -> None:
    doc = _shipped_document()
    for item in doc["nodes"]:
        if item["id"] == "production.probes.train":
            item["operation"] = "expand_now"
    candidate = tmp_path / "candidate.json"
    candidate.write_text(json.dumps(doc), encoding="utf-8")
    proc = _run_cli("--validate-policy", "--policy-file", str(candidate), cwd=tmp_path)
    assert proc.returncode == 1
    assert "node 'production.probes.train'" in proc.stderr
    assert "unknown_operation" in proc.stderr
    assert proc.stdout == ""


def test_cli_usage_errors_are_terminal_safe_and_keep_exit_code_2(tmp_path: Path) -> None:
    hostile = "--\x1b]0;pwned\x07\x1b[2J"
    proc = subprocess.run(
        [sys.executable, "-m", "bots.jev.v1", hostile],
        cwd=tmp_path,
        capture_output=True,
        timeout=60,
        check=False,
    )
    assert proc.returncode == 2  # argparse usage-error code preserved
    assert b"\x1b" not in proc.stderr and b"\x07" not in proc.stderr
    assert b"\\x1b" in proc.stderr and b"unrecognized arguments" in proc.stderr
    assert b"Traceback" not in proc.stderr
    misuse = subprocess.run(
        [sys.executable, "-m", "bots.jev.v1", "--policy-file", "x.json"],
        cwd=tmp_path,
        capture_output=True,
        timeout=60,
        check=False,
    )
    assert misuse.returncode == 2 and b"requires --validate-policy" in misuse.stderr


def test_render_text_leaves_no_invisible_code_points_and_keeps_printable_text() -> None:
    unsafe = [
        "\x00",
        "\x1b",
        "\x7f",
        "\x9b",
        "\n",
        "\t",  # C0 / DEL / C1
        "\u00ad",
        "\u061c",
        "\u200b",
        "\u200e",
        "\u2060",
        "\ufeff",  # format (Cf)
        "\u202e",
        "\u2066",  # bidi override / isolate
        "\u2028",
        "\u2029",
        "\u3000",  # line/paragraph separators, non-ASCII space
        "\ud800",
        "\ue000",
        "\U000e0041",  # surrogate, private use, tag character
    ]
    printable = "plain ASCII, café, 政策, Ωμέγα 🙂"
    rendered = render_text("".join(char + printable for char in unsafe))
    assert all(char.isprintable() for char in rendered)
    assert not any(
        unicodedata.category(char) in {"Cc", "Cf", "Cs", "Zl", "Zp"} for char in rendered
    )
    assert rendered.count(printable) == len(unsafe)  # printable text survives verbatim
    assert render_text(printable) == printable
    assert render_text(rendered) == rendered  # idempotent
    lines = render_lines("one\x1b\ntwo\u202e").split("\n")
    assert len(lines) == 2 and all(line.isprintable() for line in lines)


def test_cli_never_writes_raw_control_characters_from_untrusted_keys(tmp_path: Path) -> None:
    schema_phase = _shipped_document()
    schema_phase["\x1b]0;pwned\x07"] = 1  # OSC title-set sequence as a top-level key
    semantic_phase = _shipped_document()  # reaches node-level messages and node ids
    _doc_node(semantic_phase, "economy.assign")["args"]["\x1b[2J\x07"] = 1  # arg key
    semantic_phase["roots"].append("ghost\x1b[31m\x07root")  # root id echoed as node id
    for name, doc in (("schema", schema_phase), ("semantic", semantic_phase)):
        candidate = tmp_path / f"{name}.json"
        candidate.write_text(json.dumps(doc), encoding="utf-8")
        proc = subprocess.run(
            [
                sys.executable,
                "-m",
                "bots.jev.v1",
                "--validate-policy",
                "--policy-file",
                str(candidate),
            ],
            cwd=tmp_path,
            capture_output=True,
            timeout=60,
            check=False,
        )
        assert proc.returncode == 1, name
        assert b"Traceback" not in proc.stderr, name
        for raw in (b"\x1b", b"\x07"):
            assert raw not in proc.stderr and raw not in proc.stdout, (name, proc.stderr)
        assert b"\\x1b" in proc.stderr and b"\\x07" in proc.stderr, name


def test_cli_output_is_encoding_safe_for_non_ascii_paths(tmp_path: Path) -> None:
    folder = tmp_path / "政策"  # CJK directory name
    folder.mkdir()
    valid = folder / "policy.json"
    valid.write_bytes((PACKAGE_DIR / "policy.json").read_bytes())
    broken = folder / "broken.json"
    broken.write_text("{", encoding="utf-8")
    env = {k: v for k, v in os.environ.items() if k != "PYTHONUTF8"}
    env["PYTHONIOENCODING"] = "cp1252"  # a stream that cannot encode the path
    for candidate, expected in ((valid, 0), (broken, 1)):
        proc = subprocess.run(
            [
                sys.executable,
                "-m",
                "bots.jev.v1",
                "--validate-policy",
                "--policy-file",
                str(candidate),
            ],
            cwd=tmp_path,
            capture_output=True,
            timeout=60,
            env=env,
            check=False,
        )
        assert proc.returncode == expected, proc.stderr.decode("cp1252", "replace")
        assert b"Traceback" not in proc.stderr
        output = proc.stdout if expected == 0 else proc.stderr
        assert b"\\u653f\\u7b56" in output  # escaped, not crashed
        if expected == 0:
            assert f"policy_hash={load_policy().policy_hash}".encode() in proc.stdout


def _assert_structured_cli_failure(proc: subprocess.CompletedProcess[str], code: str) -> None:
    assert proc.returncode == 1, (proc.returncode, proc.stderr[-2000:])
    assert "Traceback" not in proc.stderr
    assert "invalid_policy" in proc.stderr
    assert f"[{code}]" in proc.stderr, proc.stderr[-2000:]
    assert proc.stdout == ""


def _doc_node(doc: dict[str, Any], node_id: str) -> dict[str, Any]:
    found: dict[str, Any] = next(n for n in doc["nodes"] if n["id"] == node_id)
    return found


def _mutated(mutate: Callable[[dict[str, Any]], object]) -> Callable[[], bytes]:
    """Payload builder: the shipped policy with one hostile edit, JSON-encoded.

    ``json.dumps`` escapes lone surrogates as ``\\udXXX``, exactly the JSON a hostile
    author would write; ``json.loads`` turns those escapes back into lone surrogates.
    """

    def build() -> bytes:
        doc = _shipped_document()
        mutate(doc)
        return json.dumps(doc).encode("utf-8")

    return build


def _text_swap(old: str, new: str) -> Callable[[], bytes]:
    """Payload builder: raw-text substitution, for literals ``json.dumps`` won't emit."""

    def build() -> bytes:
        text = json.dumps(_shipped_document())
        assert old in text
        return text.replace(old, new, 1).encode("utf-8")

    return build


def _raw(data: bytes) -> Callable[[], bytes]:
    return lambda: data


def _nested(depth: int) -> Any:
    value: Any = []
    for _ in range(depth):
        value = [value]
    return value


#: (case id, payload builder, expected issue code, optional stderr fragment)
HOSTILE_DOCUMENTS: list[tuple[str, Callable[[], bytes], str, str | None]] = [
    ("deep-array", _raw(b"[" * 50_000 + b"]" * 50_000), "too_deep", None),
    (
        "deep-inside-args",
        _mutated(lambda d: _doc_node(d, "economy.assign")["args"].update(extra=_nested(200))),
        "too_deep",
        None,
    ),
    (
        "huge-int-arg",
        _mutated(lambda d: _doc_node(d, "economy.idle_probes")["args"].update(limit=10**400)),
        "invalid_arg",
        "node 'economy.idle_probes'",
    ),
    (
        "huge-int-parameter",
        _mutated(lambda d: d["parameters"].update(probe_target=10**400)),
        "invalid_parameters",
        "probe_target",
    ),
    (
        "int-over-json-digit-limit",
        _text_swap('"limit": 32', '"limit": ' + "9" * 5000),
        "invalid_json",
        None,
    ),
    ("nan-literal", _text_swap('"probe_target": 16', '"probe_target": NaN'), "invalid_json", None),
    (
        "infinity-literal",
        _text_swap('"probe_target": 16', '"probe_target": -Infinity'),
        "invalid_json",
        None,
    ),
    (
        "float-overflow",
        _text_swap('"probe_target": 16', '"probe_target": 1e999'),
        "invalid_json",
        None,
    ),
    (
        "surrogate-label",
        _mutated(lambda d: _doc_node(d, "economy.assign").update(label="bad \ud800")),
        "invalid_text",
        "node 'economy.assign'",
    ),
    (
        "surrogate-node-id",
        _mutated(lambda d: _doc_node(d, "economy.assign").update(id="economy.\ud800")),
        "invalid_text",
        "node 'nodes[",
    ),
    (
        "surrogate-arg-value",
        _mutated(
            lambda d: _doc_node(d, "economy.idle_probes")["args"]["filter"].update(
                types=["Probe\udfff"]
            )
        ),
        "invalid_text",
        "node 'economy.idle_probes'",
    ),
    (
        "surrogate-parameter-value",
        _mutated(lambda d: d["parameters"].update(note="\udbff")),
        "invalid_text",
        "parameters.note",
    ),
    (
        "surrogate-object-key",
        _mutated(lambda d: _doc_node(d, "economy.assign")["args"].update({"\ud800": 1})),
        "invalid_text",
        "node 'economy.assign'",
    ),
    (
        "surrogate-root",
        _mutated(lambda d: d["roots"].__setitem__(0, "economy\ud800")),
        "invalid_text",
        None,
    ),
    ("surrogate-raw-cesu-bytes", _raw(b'{"a": "\xed\xa0\x80"}'), "invalid_json", None),
    ("invalid-utf8", _raw(b"\xff\xfe{}"), "invalid_json", None),
    (
        "utf8-bom",
        lambda: b"\xef\xbb\xbf" + json.dumps(_shipped_document()).encode(),
        "invalid_json",
        None,
    ),
    ("empty-file", _raw(b""), "invalid_json", None),
    ("top-level-list", _raw(b"[]"), "invalid_json", None),
    ("top-level-string", _raw(b'"policy"'), "invalid_json", None),
    ("top-level-null", _raw(b"null"), "invalid_json", None),
    ("duplicate-keys", _raw(b'{"schema_version": 1, "schema_version": 1}'), "invalid_json", None),
    ("nodes-not-a-list", _mutated(lambda d: d.update(nodes={})), "invalid_nodes", None),
    ("node-not-an-object", _mutated(lambda d: d["nodes"].__setitem__(0, [])), "invalid_node", None),
    (
        "children-wrong-type",
        _mutated(lambda d: _doc_node(d, "economy").update(children="economy.assign")),
        "invalid_children",
        "node 'economy'",
    ),
    (
        "args-wrong-type",
        _mutated(lambda d: _doc_node(d, "economy.assign").update(args=[])),
        "invalid_args",
        "node 'economy.assign'",
    ),
    ("roots-wrong-type", _mutated(lambda d: d.update(roots="economy")), "invalid_roots", None),
    (
        "parameters-wrong-type",
        _mutated(lambda d: d.update(parameters=[])),
        "invalid_parameters",
        None,
    ),
    (
        "parameter-nested-object",
        _mutated(lambda d: d["parameters"].update(probe_target={"x": 1})),
        "invalid_parameters",
        None,
    ),
    (
        "label-wrong-type",
        _mutated(lambda d: _doc_node(d, "economy.assign").update(label=5)),
        "invalid_label",
        "node 'economy.assign'",
    ),
    (
        "kind-wrong-type",
        _mutated(lambda d: _doc_node(d, "economy.assign").update(kind=1)),
        "invalid_kind",
        None,
    ),
    (
        "operation-wrong-type",
        _mutated(lambda d: _doc_node(d, "economy.assign").update(operation=5)),
        "invalid_operation",
        None,
    ),
    (
        "schema-version-string",
        _mutated(lambda d: d.update(schema_version="1")),
        "unsupported_schema_version",
        None,
    ),
    (
        "filter-wrong-type",
        _mutated(lambda d: _doc_node(d, "economy.idle_probes")["args"].update(filter=[])),
        "invalid_arg",
        "node 'economy.idle_probes'",
    ),
    (
        "bool-as-int",
        _mutated(lambda d: _doc_node(d, "economy.idle_probes")["args"].update(limit=True)),
        "invalid_arg",
        "node 'economy.idle_probes'",
    ),
    ("oversize", lambda: b" " * (policy_module.MAX_POLICY_BYTES + 1), "too_large", None),
]


@pytest.mark.parametrize(
    ("payload", "code", "fragment"),
    [
        pytest.param(build, code, fragment, id=case)
        for case, build, code, fragment in HOSTILE_DOCUMENTS
    ],
)
def test_hostile_policy_documents_only_ever_yield_structured_errors(
    tmp_path: Path, payload: Callable[[], bytes], code: str, fragment: str | None
) -> None:
    """Structural invariant: untrusted bytes -> PolicyError / exit 1, never a traceback.

    Exercised through BOTH public surfaces: the real CLI subprocess and the package
    loader the runtime path uses.
    """
    candidate = tmp_path / "candidate.json"
    candidate.write_bytes(payload())
    proc = _run_cli("--validate-policy", "--policy-file", str(candidate), cwd=tmp_path)
    _assert_structured_cli_failure(proc, code)
    if fragment is not None:
        assert fragment in proc.stderr, proc.stderr[-2000:]
    with pytest.raises(PolicyError) as excinfo:
        load_policy(candidate)
    assert code in excinfo.value.codes()


class _Opaque:
    pass


def _built_policy(mutate: Callable[[dict[str, Any]], object]) -> Policy:
    """A caller-built Policy (bypassing the JSON loader) carrying one hostile value."""
    doc = _minimal_valid()
    mutate(doc)
    return Policy(
        schema_version=1,
        family="jev",
        version=1,
        roots=tuple(doc["roots"]),
        parameters=doc["parameters"],
        nodes=tuple(
            PolicyNode(
                n["id"], n["label"], n["kind"], tuple(n["children"]), n["operation"], n["args"]
            )
            for n in doc["nodes"]
        ),
    )


def _circular(d: dict[str, Any]) -> None:
    args = d["nodes"][1]["args"]
    args["self"] = args


@pytest.mark.parametrize(
    ("mutate", "code"),
    [
        pytest.param(
            lambda d: d["nodes"][2].update(label="x\ud800"), "invalid_text", id="surrogate-label"
        ),
        pytest.param(lambda d: d["nodes"][1]["args"].update({7: "x"}), "invalid_key", id="int-key"),
        pytest.param(
            lambda d: d["nodes"][1]["args"].update(limit=math.nan), "invalid_number", id="nan"
        ),
        pytest.param(
            lambda d: d["nodes"][1]["args"].update(limit={1, 2}), "invalid_value_type", id="set"
        ),
        pytest.param(
            lambda d: d["nodes"][1]["args"].update(limit=_Opaque()),
            "invalid_value_type",
            id="object",
        ),
        pytest.param(
            lambda d: d["parameters"].update({("a",): 1}), "invalid_parameters", id="tuple-key"
        ),
        pytest.param(_circular, "too_deep", id="circular-args"),
        pytest.param(
            lambda d: d["nodes"][1]["args"].update(dag=_dag_list()), "too_large", id="dag-args"
        ),
        pytest.param(
            lambda d: d["parameters"].update(p=_dag_dict()), "too_large", id="dag-parameters"
        ),
        pytest.param(
            lambda d: d["parameters"].update(p=_cyclic_dict()), "too_deep", id="cyclic-parameters"
        ),
        pytest.param(
            lambda d: d["nodes"][1]["args"].update(limit=10**5000), "invalid_arg", id="huge-int"
        ),
    ],
)
def test_runtime_boundary_rejects_hostile_built_policies(
    mutate: Callable[[dict[str, Any]], object], code: str
) -> None:
    """Construction, ``JevRuntime`` (validate + hash) and hashing raise only PolicyError."""
    try:
        policy = _built_policy(mutate)
    except PolicyError as exc:  # refused at construction (e.g. circular args)
        assert code in exc.codes()
        return
    with pytest.raises(PolicyError) as excinfo:
        runtime_module.JevRuntime(policy, run_id=uuid.uuid4().hex)
    assert code in excinfo.value.codes()
    with pytest.raises(PolicyError):
        policy_hash(policy)  # hashing unvalidated hostile input is guarded too


def test_unexpected_exception_in_untrusted_path_becomes_policy_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The boundary converts anything unforeseen, keeping the cause chained."""

    def explode(*args: Any, **kwargs: Any) -> list[Any]:
        raise ZeroDivisionError("unforeseen validator defect")

    monkeypatch.setattr(policy_module, "_structure_issues", explode)
    with pytest.raises(PolicyError) as excinfo:
        parse_policy(_minimal_valid())
    assert excinfo.value.codes() == {"unprocessable_input"}
    assert "ZeroDivisionError" in str(excinfo.value)
    assert isinstance(excinfo.value.__cause__, ZeroDivisionError)

    def explode_unprintably(*args: Any, **kwargs: Any) -> list[Any]:
        raise _RaisingStrError

    monkeypatch.setattr(policy_module, "_structure_issues", explode_unprintably)
    with pytest.raises(PolicyError) as excinfo:  # str(exc) raising must not escape
        parse_policy(_minimal_valid())
    assert "_RaisingStrError" in str(excinfo.value)


def test_validation_path_imports_no_sc2_or_model_stack(tmp_path: Path) -> None:
    code = (
        "import sys\n"
        "from bots.jev.v1.__main__ import main\n"
        "rc = main(['--validate-policy'])\n"
        "heavy = sorted(m for m in ('sc2', 'torch', 'anthropic', 'bots.current', 'bots.v0', "
        "'jev.bot', 'jev.sc2_adapter', 'orchestrator') if m in sys.modules)\n"
        "print('HEAVY', heavy)\n"
        "raise SystemExit(rc)\n"
    )
    proc = subprocess.run(
        [sys.executable, "-c", code], cwd=tmp_path, capture_output=True, text=True, timeout=60
    )
    assert proc.returncode == 0, proc.stderr
    assert "HEAVY []" in proc.stdout


def test_installed_wheel_validates_its_bundled_policy(tmp_path: Path) -> None:
    """Build the real wheel, install it in isolation, validate from outside the repo."""
    uv = shutil.which("uv")
    if uv is None:
        pytest.skip("uv is required to build the wheel")
    dist = tmp_path / "dist"
    build = subprocess.run(
        [uv, "build", "--wheel", "--out-dir", str(dist), str(REPO_ROOT)],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=600,
        check=False,
    )
    assert build.returncode == 0, build.stderr
    wheel = next(dist.glob("alpha4gate-*.whl"))
    with zipfile.ZipFile(wheel) as archive:
        names = set(archive.namelist())
    for required in (
        "bots/jev/v1/policy.json",
        "bots/jev/v1/manifest.json",
        "bots/jev/v1/__main__.py",
        "jev/policy.py",
        "jev/runtime.py",
    ):
        assert required in names, required
    assert "bots/jev/VERSION" not in names

    site = tmp_path / "site"
    install = subprocess.run(
        [
            uv,
            "pip",
            "install",
            "--no-deps",
            "--python",
            sys.executable,
            "--target",
            str(site),
            str(wheel),
        ],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=300,
        check=False,
    )
    assert install.returncode == 0, install.stderr

    # -I -S: no site-packages (so the editable .pth cannot shadow the wheel copy),
    # no PYTHON* env vars, no cwd on sys.path. Only the stdlib plus the target dir.
    script = (
        "import runpy, sys\n"
        "sys.path.insert(0, sys.argv[1])\n"
        "import bots.jev.v1, jev.policy\n"
        "print('PKG', bots.jev.v1.__file__)\n"
        "print('JEV', jev.policy.__file__)\n"
        "sys.argv = ['bots.jev.v1', '--validate-policy']\n"
        "runpy.run_module('bots.jev.v1', run_name='__main__', alter_sys=True)\n"
    )
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    env = {k: v for k, v in os.environ.items() if not k.startswith("PYTHON")}
    proc = subprocess.run(
        [sys.executable, "-I", "-S", "-c", script, str(site)],
        cwd=outside,
        capture_output=True,
        text=True,
        timeout=60,
        env=env,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    lines = dict(line.split(" ", 1) for line in proc.stdout.splitlines() if " " in line)
    site_root = site.resolve()
    assert Path(lines["PKG"]).resolve().is_relative_to(site_root)
    assert Path(lines["JEV"]).resolve().is_relative_to(site_root)
    summary = next(line for line in proc.stdout.splitlines() if line.startswith("jev policy valid"))
    source = Path(summary.split("source=", 1)[1]).resolve()
    assert source == (site_root / "bots" / "jev" / "v1" / "policy.json")
    assert f"policy_hash={load_policy().policy_hash}" in summary
