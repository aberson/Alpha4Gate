import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, cleanup, act, fireEvent } from "@testing-library/react";
import { AlertToast, TOAST_AUTO_DISMISS_MS, TOAST_MAX_VISIBLE } from "./AlertToast";
import type { Alert } from "../lib/alertRules";

function mkAlert(id: string, ruleId = "training_failed"): Alert {
  return {
    id,
    ruleId,
    severity: "error",
    title: `Title ${id}`,
    message: `Message ${id}`,
    timestamp: "2026-04-09T12:00:00Z",
  };
}

beforeEach(() => {
  vi.useFakeTimers();
});

afterEach(() => {
  vi.useRealTimers();
  cleanup();
});

describe("AlertToast", () => {
  it("renders nothing when no new alerts", () => {
    const { container } = render(
      <AlertToast newAlerts={[]} onView={() => {}} />,
    );
    expect(container.firstChild).toBeNull();
  });

  it("appears when a new alert is pushed", () => {
    const { rerender } = render(
      <AlertToast newAlerts={[]} onView={() => {}} />,
    );
    rerender(<AlertToast newAlerts={[mkAlert("a")]} onView={() => {}} />);
    expect(screen.getByText("Title a")).toBeInTheDocument();
  });

  it("auto-dismisses after TOAST_AUTO_DISMISS_MS", () => {
    render(<AlertToast newAlerts={[mkAlert("a")]} onView={() => {}} />);
    expect(screen.getByText("Title a")).toBeInTheDocument();
    act(() => {
      vi.advanceTimersByTime(TOAST_AUTO_DISMISS_MS);
    });
    expect(screen.queryByText("Title a")).not.toBeInTheDocument();
  });

  it("View button calls onView and removes the toast", () => {
    const onView = vi.fn();
    render(<AlertToast newAlerts={[mkAlert("a")]} onView={onView} />);
    fireEvent.click(screen.getByRole("button", { name: "View" }));
    expect(onView).toHaveBeenCalledTimes(1);
    expect(screen.queryByText("Title a")).not.toBeInTheDocument();
  });

  it("caps visible stack at TOAST_MAX_VISIBLE, dropping oldest first", () => {
    const { rerender } = render(
      <AlertToast newAlerts={[mkAlert("a"), mkAlert("b"), mkAlert("c")]} onView={() => {}} />,
    );
    expect(screen.getByText("Title a")).toBeInTheDocument();
    expect(screen.getByText("Title b")).toBeInTheDocument();
    expect(screen.getByText("Title c")).toBeInTheDocument();

    // Push a fourth — oldest ("a") should be dropped.
    rerender(<AlertToast newAlerts={[mkAlert("d")]} onView={() => {}} />);
    expect(screen.queryByText("Title a")).not.toBeInTheDocument();
    expect(screen.getByText("Title b")).toBeInTheDocument();
    expect(screen.getByText("Title c")).toBeInTheDocument();
    expect(screen.getByText("Title d")).toBeInTheDocument();

    // Verify we're at the cap.
    expect(screen.getAllByRole("alert")).toHaveLength(TOAST_MAX_VISIBLE);
  });

  it("does not duplicate a toast when the same alert ID is pushed again", () => {
    const { rerender } = render(
      <AlertToast newAlerts={[mkAlert("a")]} onView={() => {}} />,
    );
    rerender(<AlertToast newAlerts={[mkAlert("a")]} onView={() => {}} />);
    expect(screen.getAllByText("Title a")).toHaveLength(1);
  });

  it("persistent alerts are not auto-dismissed by the timer", () => {
    // Phase 4.5 #68: backend_error alerts are marked persistent so the
    // timer branch in AlertToast must skip them entirely. A regression
    // that drops the `if (v.alert.persistent) continue;` check would
    // cause this test to fail.
    const persistentAlert: Alert = {
      ...mkAlert("p1", "backend_error"),
      persistent: true,
    };
    render(<AlertToast newAlerts={[persistentAlert]} onView={() => {}} />);
    expect(screen.getByText("Title p1")).toBeInTheDocument();

    // Advance well past the auto-dismiss window. Persistent alerts must
    // remain visible because the timer is skipped.
    act(() => {
      vi.advanceTimersByTime(TOAST_AUTO_DISMISS_MS + 5000);
    });

    expect(screen.getByText("Title p1")).toBeInTheDocument();
  });
});
