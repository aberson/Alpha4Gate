"""Read-only trace audit; no game, provider, environment or credential access.

This is an observational audit, NOT a counterfactual SC2 replay. Missing observations
stay unknown. Outputs are per-answer evidence and bounded local branch comparisons.
Run with the repository's Python and --output documentation/investigations/<stem>.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import itertools
import json
from collections import Counter
from pathlib import Path
from typing import Any


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def walk_path(
    name: str, graph: dict[str, Any], node_state: dict[str, Any], visited: set[str]
) -> None:
    """Recover evaluated paths from status/deciding-child delta events.

    Values are used only on an evaluated path; untouched child caches cannot
    establish a current predicate. No numeric values are carried.
    """
    if name not in node_state or name not in graph:
        return
    visited.add(name)
    node = graph[name]
    for child in node.get("children", []):
        walk_path(child, graph, node_state, visited)
        status = node_state.get(child, {}).get("status")
        if node["kind"] == "sequence" and status != "success":
            break
        if node["kind"] == "selector" and status != "failure":
            break


def audit(root: Path) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    runs = []
    first = []
    for folder in sorted(root.iterdir()):
        if not folder.is_dir():
            continue
        policy = read_json(folder / "policy.json")
        diagnostic = read_json(folder / "diagnostics.json")
        state = read_json(folder / "state.json")
        events = []
        files = []
        for path in sorted(folder.glob("events.*.jsonl"), key=lambda p: int(p.name.split(".")[1])):
            raw = path.read_bytes()
            files.append({"file": path.name, "sha256": hashlib.sha256(raw).hexdigest()})
            for line in raw.splitlines(keepends=True):
                if line.endswith(b"\n"):
                    events.append(json.loads(line))
        assert all(a["sequence"] < b["sequence"] for a, b in itertools.pairwise(events))
        run_rows: dict[tuple[str, int], dict[str, Any]] = {}
        latest: dict[str, dict[str, Any]] = {}
        node_state: dict[str, dict[str, Any]] = {}
        graph = {n["id"]: n for n in policy.get("nodes", [])}
        launched = False
        first_army = None
        launch_event = None
        regroup_below = policy.get("parameters", {}).get("regroup_below", 4)
        expansion_size = policy.get("parameters", {}).get("expand_army_size", 8)
        for game, group in itertools.groupby(events, key=lambda e: e["game_seconds"]):
            tick = list(group)
            nodes = {e["node_id"]: e for e in tick if e["kind"] == "node"}
            node_state.update(nodes)
            visited: set[str] = set()

            # Runtime evaluates every root on every tick. Recorder omits unchanged
            # root statuses too. A budget stall is excluded from this reconstruction.
            for name in policy.get("roots", []):
                if not any("budget" in x["reason"] for x in tick if x["kind"] == "diagnostic"):
                    walk_path(name, graph, node_state, visited)
            decisions = [
                e
                for e in tick
                if e["kind"] == "diagnostic"
                and e["facts"].get("decision_provider") == "typesafe"
                and "answer_request_id" in e["facts"]
            ]
            for e in decisions:
                latest[e["facts"].get("question_id", "army_mode")] = e
            army_event = latest.get("army_mode", {})
            army = army_event.get("facts", {})
            strategy_event = latest.get("strategy", {})
            strategy = strategy_event.get("facts", {})
            # Only current-tick facts establish an exact count. The trace suppresses
            # numeric changes on nodes whose status does not change: never carry counts.
            count = None
            count_seq = None
            if strategy_event.get("game_seconds") == game:
                count = strategy.get("recovery_progress", {}).get("ready_combat_units")
                if count is not None:
                    count_seq = strategy_event["sequence"]
            for node in ("army.recover.few", "expansion.start.army"):
                if count is None and node in nodes:
                    count = nodes[node]["facts"].get("count")
                    count_seq = nodes[node]["sequence"] if count is not None else None
            army_current = army_event.get("game_seconds") == game
            options = army.get("available_options", []) if army_current else []
            if launched and army_current and options == ["regroup"]:
                count, count_seq = 0, army_event["sequence"]
            few = count < regroup_below if count is not None else None
            few_seq = count_seq
            if few is None and "army.recover.few" in visited:
                predicate = node_state["army.recover.few"]
                if predicate["status"] in ("success", "failure"):
                    few = predicate["status"] == "success"
                    few_seq = predicate["sequence"]
            for e in decisions:
                f = e["facts"]
                request = f.get("answer_request_id")
                if request is None or f.get("answer") is None:
                    continue
                question = f.get("question_id", "army_mode")
                key = (question, request)
                if key in run_rows:
                    continue
                row = {
                    "run_id": folder.name,
                    "version": policy.get("version"),
                    "question": question,
                    "request": request,
                    "seq": e["sequence"],
                    "game": game,
                    "request_game": f.get("observation_game_seconds"),
                    "answer": f["answer"],
                    "reason": f["reason"],
                    "confidence": f.get("confidence"),
                    "probabilities": f.get("probabilities"),
                    "fallback": None,
                    "comparison": "unknown",
                    "comparison_seqs": [],
                    "ready_army": count,
                    "count_seq": count_seq,
                    "patterns": {},
                    "commands_30s": [],
                    "army_failures_30s": [],
                    "plans_30s": [],
                    "outcome": diagnostic.get("outcome", {}).get("result", state.get("result")),
                }
                run_rows[key] = row
                if question == "army_mode" and first_army is None:
                    first_army = row
                if f["reason"] != "accepted" or policy.get("version") != 2:
                    continue
                fallback = None
                if question == "production_mix":
                    if isinstance(f.get("air_seen"), bool):
                        fallback = "stalker_heavy" if f["air_seen"] else "balanced"
                        row["comparison_seqs"] = [e["sequence"]]
                elif question == "expansion_response":
                    fallback = "delay"
                    row["comparison_seqs"] = [e["sequence"]]
                elif question == "army_mode":
                    # Local intent preference, not proof of a successful command:
                    # actual attack also needs an eligible reinforcement/group.
                    if "defend" in options:
                        fallback = "defend"
                    elif few is not None:
                        fallback = "regroup" if few else "attack"
                    row["comparison_seqs"] = [x for x in [army_event.get("sequence"), few_seq] if x]
                elif question == "strategy":
                    offered = f.get("available_options", [])
                    # Compare to D2's priority preference. Local macro/army lanes
                    # can act together, so this is not command equivalence.
                    if "defend" in options:
                        fallback = "defend"
                    elif "tech" in offered:
                        fallback = "tech"
                    elif count is not None:
                        if "expand" in offered and count >= expansion_size:
                            fallback = "expand"
                        else:
                            fallback = "recover" if count < regroup_below else "pressure"
                    elif few is not None and "expand" not in offered:
                        fallback = "recover" if few else "pressure"
                    row["comparison_seqs"] = [
                        x for x in [e["sequence"], army_event.get("sequence"), few_seq] if x
                    ]
                row["fallback"] = fallback
                if fallback is not None:
                    row["comparison"] = "same" if f["answer"] == fallback else "different"
                if question == "strategy" and fallback == "defend":
                    # Defense is not a strategy answer. Do not count a different
                    # label as strategic disagreement when emergency work takes priority.
                    row["comparison"] = "defense_preempts"
                elif question == "strategy" and count == 0 and fallback == "recover":
                    row["comparison"] = "no_army"
            # Flag each accepted answer while it is in force, including renewals.
            for question, event in latest.items():
                if event.get("game_seconds") != game:
                    continue
                f = event["facts"]
                req = f.get("in_force_request_id", f.get("answer_request_id"))
                row = run_rows.get((question, req))
                if not row or row["reason"] != "accepted" or f.get("source") != "typesafe":
                    continue
                patterns = row["patterns"]
                if (
                    question == "army_mode"
                    and "defend" in options
                    and f.get("choice") in ("attack", "regroup")
                ):
                    patterns.setdefault(
                        "defense_suppressed",
                        {
                            "seq": event["sequence"],
                            "game": game,
                            "guard_seqs": [
                                n["sequence"]
                                for name, n in nodes.items()
                                if name == "army.defend.mode" and n["status"] == "failure"
                            ],
                            "cancel_seqs": [
                                x["sequence"]
                                for x in tick
                                if x["kind"] == "task"
                                and x["status"] == "cancelled"
                                and x["node_id"].startswith("army.defend")
                            ],
                        },
                    )
                    attacks = [
                        x["sequence"]
                        for x in tick
                        if x["kind"] == "command" and x["node_id"] == "army.attack.go"
                    ]
                    if attacks:
                        patterns.setdefault(
                            "attack_during_home_threat",
                            {"seq": event["sequence"], "game": game, "command_seqs": attacks},
                        )
                    if "army" in nodes and nodes["army"]["status"] == "failure":
                        patterns.setdefault(
                            "army_lane_failed_during_threat",
                            {"seq": nodes["army"]["sequence"], "game": game},
                        )
                if question == "strategy" and f.get("choice") == "recover" and count == 0:
                    patterns.setdefault(
                        "recover_without_army",
                        {"seq": event["sequence"], "game": game, "count_seq": count_seq},
                    )
            latch = nodes.get("army.attack.launch.first_wave.latch")
            if latch and latch["status"] == "success":
                launched = True
                if launch_event is None:
                    launch_event = {"seq": latch["sequence"], "game": game}
        # Fixed forward window: associations only, includes subsequent decisions.
        for row in run_rows.values():
            if row["reason"] != "accepted" or row["version"] != 2:
                continue
            for e in events:
                if e["sequence"] <= row["seq"] or e["game_seconds"] > row["game"] + 30:
                    continue
                if e["kind"] == "command":
                    row["commands_30s"].append({"seq": e["sequence"], "node": e["node_id"]})
                if e["kind"] == "node" and e["node_id"] == "army" and e["status"] == "failure":
                    row["army_failures_30s"].append(e["sequence"])
                if (
                    e["kind"] == "diagnostic"
                    and "plan_status" in e["facts"]
                    and e["reason"].endswith((" completed", " aborted"))
                ):
                    facts = e["facts"]
                    record = {
                        "seq": e["sequence"],
                        "status": facts["plan_status"],
                        "plan": facts.get("plan_id"),
                    }
                    if facts["plan_status"] in ("completed", "aborted") and not any(
                        x["plan"] == record["plan"] for x in row["plans_30s"]
                    ):
                        row["plans_30s"].append(record)
            row["window_observed_seconds"] = min(
                30, max(0, events[-1]["game_seconds"] - row["game"])
            )
            grouped_commands: dict[str, dict[str, Any]] = {}
            for command in row["commands_30s"]:
                summary = grouped_commands.setdefault(
                    command["node"],
                    {"node": command["node"], "count": 0, "first_seq": command["seq"]},
                )
                summary["count"] += 1
                summary["last_seq"] = command["seq"]
            row["commands_30s"] = list(grouped_commands.values())
        if first_army:
            first.append(
                {
                    k: first_army[k]
                    for k in (
                        "run_id",
                        "version",
                        "request",
                        "seq",
                        "reason",
                        "request_game",
                        "game",
                    )
                }
                | {"launch": launch_event}
            )
        # Every accepted v2 answer receives a classification. Other question
        # families in the preceding 30 seconds are associations, not causes.
        harmful = [r for r in run_rows.values() if r["version"] == 2 and r["patterns"]]
        for row in run_rows.values():
            if row["version"] != 2 or row["reason"] != "accepted":
                continue
            associated = []
            for other in harmful:
                for evidence in other["patterns"].values():
                    onset = evidence.get("game", other["game"])
                    if row["seq"] <= evidence["seq"] and row["game"] <= onset <= row["game"] + 30:
                        associated.append(other["seq"])
                        break
            row["associated_harmful_answers_30s"] = associated
            row["classification"] = (
                "direct_mechanism"
                if row["patterns"]
                else "temporal_association_only"
                if associated
                else "not_established_in_window"
            )
        rows.extend(run_rows.values())
        runs.append(
            {
                "run_id": folder.name,
                "version": policy.get("version"),
                "answers": len(run_rows),
                "options": diagnostic.get("options"),
                "outcome": diagnostic.get("outcome"),
                "trace": state.get("trace"),
                "files": files,
                "metrics": diagnostic.get("metrics", {}).get("decisions"),
            }
        )
    return {"runs": runs, "answers": rows, "first_army": first}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runs", type=Path, default=Path("data/jev/runs"))
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = audit(args.runs)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.with_suffix(".json").write_text(
        json.dumps(result, separators=(",", ":")) + "\n", encoding="utf-8"
    )
    fields = [
        "run_id",
        "version",
        "question",
        "request",
        "seq",
        "game",
        "answer",
        "reason",
        "fallback",
        "comparison",
        "ready_army",
        "count_seq",
        "patterns",
        "classification",
        "associated_harmful_answers_30s",
        "outcome",
    ]
    with args.output.with_suffix(".csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(result["answers"])
    for q in ("army_mode", "strategy", "expansion_response", "production_mix"):
        rows = [r for r in result["answers"] if r["version"] == 2 and r["question"] == q]
        accepted = [r for r in rows if r["reason"] == "accepted"]
        print(
            q,
            "answers",
            len(rows),
            Counter(r["reason"] for r in rows),
            Counter(r["comparison"] for r in accepted),
            Counter(k for r in accepted for k in r["patterns"]),
            flush=True,
        )


if __name__ == "__main__":
    main()
