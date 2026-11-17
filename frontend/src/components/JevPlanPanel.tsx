import {
  formatSeconds,
  isOpenPlan,
  sourceName,
  type PlanView,
  type PlansEvidence,
  type SlotView,
} from "./jevEvidence";
import { laneToneClass } from "../types/jev";

/**
 * The Jev tab's Plans and decisions panel (plan J2 Step 221, D5), for runs whose
 * policy keeps plans or hosted v2 questions. It renders what ``plansEvidence``
 * (``jevEvidence.ts``) derived from the run's recent events.
 */

function seconds(value: number | null): string {
  return value === null ? "not recorded" : formatSeconds(value);
}

function untilText(until: number | null, now: number): string {
  if (until === null) return "none";
  const at = formatSeconds(until);
  return until > now ? `${at} (in ${formatSeconds(until - now)})` : `${at} (passed)`;
}

/** An open plan's deadline and what it holds (from its merged PlanRecord facts). */
function holdsText(plan: PlanView): string | null {
  if (plan.deadline === null) return null;
  const holds =
    plan.reservedMinerals === null
      ? ""
      : ` · holds ${plan.reservedMinerals} minerals, ${plan.reservedVespene ?? 0} gas`;
  return `deadline ${formatSeconds(plan.deadline)}${holds}`;
}

function slotOpenText(slot: SlotView): string {
  if (slot.openPlanId === null) return "no open plan";
  const kind = slot.openKind ?? "a";
  return `holds ${kind} plan ${slot.openPlanId} (no other plan of this slot may start)`;
}

function turnText(slot: SlotView, now: number): string {
  if (slot.yieldFrom === null) return "nobody is owed a turn";
  const to = slot.yieldTo.join(", ") || "nobody";
  return `${slot.yieldFrom} yields to ${to} until ${untilText(slot.yieldUntil, now)}`;
}

interface PlanPanelProps {
  evidence: PlansEvidence;
  lanes: ReadonlyMap<string, string>;
  roots: string[];
  live: boolean;
  gameSeconds: number;
  onShowNode: (nodeId: string) => void;
}

/**
 * Plans and decisions (plan D5): each plan's stage, blocker or outcome, who decided
 * it and its request; each slot's turn and which kinds may start next (the store's
 * own check); and each decision family's source. All from the newest recorded
 * events; labeled last known unless live.
 */
export function PlanPanel({
  evidence,
  lanes,
  roots,
  live,
  gameSeconds,
  onShowNode,
}: PlanPanelProps) {
  const { plans, slots, decisions } = evidence;
  const tone = (nodeId: string | null) => {
    const lane = nodeId === null ? null : (lanes.get(nodeId) ?? null);
    return lane === null ? "" : laneToneClass(lane, roots.indexOf(lane));
  };
  const strategy = decisions.find((view) => view.question === "strategy") ?? null;
  return (
    <section className="jev-plans" aria-labelledby="jev-plans-heading" data-testid="jev-plans">
      <div className="jev-plans-title">
        <h3 id="jev-plans-heading">Plans and decisions</h3>
        <span className="jev-badge" data-testid="jev-plans-evidence">
          {live ? "Live evidence" : "Last known evidence"}
        </span>
      </div>
      <p className="jev-note">
        From the run's newest recorded plan, slot and decision events (each summarized once
        per game second). A plan, slot or question with no recent event is not shown.
      </p>
      <h4>Plans</h4>
      {plans.length === 0 ? (
        <p className="jev-note" data-testid="jev-plans-none">
          No plan recorded in the recent events{live ? "" : " (last known)"}.
        </p>
      ) : (
        <div className="jev-plans-scroll">
          <table>
            <thead>
              <tr>
                <th scope="col">Plan</th>
                <th scope="col">Status</th>
                <th scope="col">Stage</th>
                <th scope="col">Blocker / outcome</th>
                <th scope="col">Decided by</th>
                <th scope="col">Response</th>
              </tr>
            </thead>
            <tbody>
              {plans.map((plan) => {
                const open = isOpenPlan(plan);
                const holds = open ? holdsText(plan) : null;
                return (
                  <tr
                    key={plan.planId}
                    data-testid="jev-plan-row"
                    data-plan-status={plan.status}
                    data-plan-kind={plan.kind}
                  >
                    <td className={`jev-plan-lane ${tone(plan.stage)}`}>
                      <strong>{plan.kind}</strong> · lane{" "}
                      {plan.lane ?? lanes.get(plan.stage) ?? "?"}
                      <br />
                      <code>{plan.planId}</code>
                    </td>
                    <td className="jev-nowrap" data-testid="jev-plan-status">
                      {plan.status}
                      {open && !live ? " (last known)" : ""}
                    </td>
                    <td>
                      <code>{plan.stage}</code>{" "}
                      <button
                        type="button"
                        data-testid="jev-plan-stage"
                        onClick={() => onShowNode(plan.stage)}
                      >
                        Show in graph
                      </button>
                    </td>
                    <td data-testid="jev-plan-blocker">
                      {open
                        ? plan.blocker === null
                          ? `no blocker (${plan.reasonCode})`
                          : `${plan.reasonCode}: ${plan.blocker}`
                        : `${plan.status} (${plan.reasonCode}) at ${seconds(plan.finishedAt)}`}
                      {holds !== null && (
                        <>
                          <br />
                          <span data-testid="jev-plan-holds">{holds}</span>
                        </>
                      )}
                    </td>
                    <td data-testid="jev-plan-decided-by">
                      {plan.decidedBy === null
                        ? "not recorded"
                        : `${sourceName(plan.decidedBy)}${
                            plan.decisionRequestId === null
                              ? ""
                              : ` (request ${plan.decisionRequestId})`
                          }`}
                    </td>
                    <td>
                      {plan.response === null
                        ? "none"
                        : `${plan.response}${
                            plan.responseRequestId === null
                              ? ""
                              : ` (request ${plan.responseRequestId})`
                          }`}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
      <h4>Plan slots: whose turn, and what may start next</h4>
      {slots.length === 0 ? (
        <p className="jev-note">No plan slot recorded in the recent events.</p>
      ) : (
        slots.map((slot) => (
          <div key={slot.slot} data-testid="jev-slot" data-slot={slot.slot}>
            <dl className="jev-facts">
              <dt>{slot.slot} slot</dt>
              <dd data-testid="jev-slot-open">{slotOpenText(slot)}</dd>
              <dt>Next permitted</dt>
              <dd>
                {slot.kinds === null ? (
                  "not recorded (no slot summary in the recent events)"
                ) : (
                  <ul className="jev-source-list">
                    {slot.kinds.map((kind) => (
                      <li
                        key={kind.kind}
                        data-testid="jev-slot-kind"
                        data-may-start={kind.mayStart}
                      >
                        <strong>{kind.kind}</strong>:{" "}
                        {kind.mayStart ? "may start now" : `waits: ${kind.refusal ?? "refused"}`}
                      </li>
                    ))}
                  </ul>
                )}
              </dd>
              <dt>Cooldown until</dt>
              <dd>{untilText(slot.cooldownUntil, gameSeconds)}</dd>
              <dt>Turn</dt>
              <dd data-testid="jev-slot-turn">{turnText(slot, gameSeconds)}</dd>
              {slot.disabled.length > 0 && (
                <>
                  <dt>Disabled</dt>
                  <dd>{slot.disabled.map(([kind, code]) => `${kind} (${code})`).join(", ")}</dd>
                </>
              )}
            </dl>
          </div>
        ))
      )}
      {strategy?.available !== null && strategy?.available !== undefined && (
        <p data-testid="jev-strategy-available">
          Strategy choices the graph can execute now:{" "}
          {strategy.available.length === 0 ? "none" : strategy.available.join(", ")}
          {live ? "" : " (last known)"}
        </p>
      )}
      <h4>Decision sources</h4>
      {decisions.length === 0 ? (
        <p className="jev-note">No decision source recorded in the recent events.</p>
      ) : (
        <div className="jev-plans-scroll">
          <table>
            <thead>
              <tr>
                <th scope="col">Question</th>
                <th scope="col">Source</th>
                <th scope="col">Choice in force</th>
                <th scope="col">Request</th>
                <th scope="col">Reason</th>
                <th scope="col">Pending</th>
              </tr>
            </thead>
            <tbody>
              {decisions.map((view) => (
                <tr
                  key={view.question}
                  data-testid="jev-decision-row"
                  data-question={view.question}
                  data-source={view.source ?? "unknown"}
                >
                  <td>{view.question}</td>
                  <td>
                    {view.source === null ? "not recorded" : sourceName(view.source)}
                    {view.model === null ? "" : ` · model ${view.model}`}
                  </td>
                  <td>{view.choice ?? "none"}</td>
                  <td>{view.request ?? "none"}</td>
                  <td>{view.reason ?? "not recorded"}</td>
                  <td>
                    {view.pending
                      ? `request ${view.pendingRequest ?? "?"}${live ? "" : " (when recorded)"}`
                      : "no"}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}
