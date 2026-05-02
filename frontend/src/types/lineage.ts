/**
 * Lineage DAG types — matches the response shape of ``GET /api/lineage``.
 *
 * Backend impl: ``scripts/build_lineage.py`` walks each
 * ``bots/vN/manifest.json``, joins the row against
 * ``data/improvement_log.json`` (advised) and ``data/evolve_results.jsonl``
 * (evolve), and writes ``data/lineage.json`` with the schema documented
 * in ``documentation/plans/models-tab-plan.md``.
 *
 * Edges carry the improvement title that promoted ``to`` from ``from``.
 * Per build_lineage rules:
 *   - ``manual`` edges → ``improvement_title === "manual"``
 *   - missing-from-log edges → ``improvement_title === "—"``
 */

import type { Version } from "./version";

/**
 * One node in the lineage DAG. ``id`` and ``version`` are redundant in
 * the v0 schema (always equal); kept separate so a future Phase G race
 * fork can give two nodes the same ``version`` (e.g. ``v0-protoss`` vs
 * ``v0-zerg``) without breaking ``id`` uniqueness.
 */
export interface LineageNode {
  id: string;
  version: string;
  race: string;
  harness_origin: Version["harness_origin"];
  parent: string | null;
}

export interface LineageEdge {
  from: string;
  to: string;
  harness: Version["harness_origin"];
  improvement_title: string;
  ts: string;
  outcome: string;
}

export interface LineageDAG {
  nodes: LineageNode[];
  edges: LineageEdge[];
}
