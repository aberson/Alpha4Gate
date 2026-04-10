import { describe, it, expect, vi, afterEach } from "vitest";
import { render, screen, cleanup, fireEvent, within } from "@testing-library/react";
import { AlertsPanel } from "./AlertsPanel";
import type { Alert } from "../lib/alertRules";

afterEach(() => {
  cleanup();
});

const ALERTS: Alert[] = [
  {
    id: "err-1",
    ruleId: "training_failed",
    severity: "error",
    title: "Training failed",
    message: "Something went wrong",
    timestamp: "2026-04-09T11:00:00Z",
  },
  {
    id: "warn-1",
    ruleId: "win_rate_drop",
    severity: "warning",
    title: "Win rate dropped",
    message: "Recent games are worse",
    timestamp: "2026-04-09T10:30:00Z",
  },
  {
    id: "info-1",
    ruleId: "no_training",
    severity: "info",
    title: "No training in a while",
    message: "Idle for 30 hours",
    timestamp: "2026-04-09T09:00:00Z",
  },
];

function renderPanel(overrides: Partial<React.ComponentProps<typeof AlertsPanel>> = {}) {
  const props: React.ComponentProps<typeof AlertsPanel> = {
    alerts: ALERTS,
    ackedIds: [],
    onAck: vi.fn(),
    onDismiss: vi.fn(),
    onMarkAllRead: vi.fn(),
    onClearHistory: vi.fn(),
    ...overrides,
  };
  return { props, ...render(<AlertsPanel {...props} />) };
}

describe("AlertsPanel", () => {
  it("renders all alerts by default, newest first", () => {
    renderPanel();
    const items = screen.getAllByRole("listitem");
    expect(items).toHaveLength(3);
    expect(items[0].textContent).toContain("Training failed");
    expect(items[1].textContent).toContain("Win rate dropped");
    expect(items[2].textContent).toContain("No training");
  });

  it("filters by severity", () => {
    renderPanel();
    fireEvent.click(screen.getByRole("button", { name: "Errors" }));
    const items = screen.getAllByRole("listitem");
    expect(items).toHaveLength(1);
    expect(items[0].textContent).toContain("Training failed");
  });

  it("calls onAck when Ack clicked", () => {
    const { props } = renderPanel();
    const firstItem = screen.getAllByRole("listitem")[0];
    fireEvent.click(within(firstItem).getByRole("button", { name: "Ack" }));
    expect(props.onAck).toHaveBeenCalledWith("err-1");
  });

  it("calls onDismiss when Dismiss clicked", () => {
    const { props } = renderPanel();
    const firstItem = screen.getAllByRole("listitem")[0];
    fireEvent.click(within(firstItem).getByRole("button", { name: "Dismiss" }));
    expect(props.onDismiss).toHaveBeenCalledWith("err-1");
  });

  it("calls onMarkAllRead and onClearHistory", () => {
    const { props } = renderPanel();
    fireEvent.click(screen.getByRole("button", { name: "Mark all read" }));
    fireEvent.click(screen.getByRole("button", { name: "Clear history" }));
    expect(props.onMarkAllRead).toHaveBeenCalled();
    expect(props.onClearHistory).toHaveBeenCalled();
  });

  it("shows an empty state when no alerts", () => {
    renderPanel({ alerts: [] });
    expect(screen.getByText("No active alerts.")).toBeInTheDocument();
  });

  it("shows filter-specific empty state when filter matches nothing", () => {
    renderPanel({ alerts: [ALERTS[0]] });
    fireEvent.click(screen.getByRole("button", { name: "Info" }));
    expect(
      screen.getByText("No alerts match the current filter."),
    ).toBeInTheDocument();
  });

  it("marks acked alerts with acked class and disables Ack button", () => {
    renderPanel({ ackedIds: ["err-1"] });
    const firstItem = screen.getAllByRole("listitem")[0];
    expect(firstItem.className).toContain("acked");
    const ackButton = within(firstItem).getByRole("button", { name: "Acked" });
    expect(ackButton).toBeDisabled();
  });
});
