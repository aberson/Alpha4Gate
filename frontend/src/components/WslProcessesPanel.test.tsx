import { describe, it, expect, vi, afterEach } from "vitest";
import { render, screen, cleanup } from "@testing-library/react";
import { WslProcessesPanel } from "./WslProcessesPanel";
import type { UseApiResult } from "../hooks/useApi";
import type { WslProcessList } from "../hooks/useSystemInfo";

const mockHook = vi.hoisted(() => ({
  useWslProcesses: vi.fn<() => UseApiResult<WslProcessList>>(),
}));

vi.mock("../hooks/useSystemInfo", () => ({
  useWslProcesses: mockHook.useWslProcesses,
}));

afterEach(() => {
  cleanup();
  mockHook.useWslProcesses.mockReset();
});

function mkResult(
  data: WslProcessList | null,
  overrides: Partial<UseApiResult<WslProcessList>> = {},
): UseApiResult<WslProcessList> {
  return {
    data,
    isLoading: false,
    isStale: false,
    lastSuccess: data === null ? null : new Date(),
    error: null,
    refresh: vi.fn(),
    ...overrides,
  };
}

describe("WslProcessesPanel", () => {
  it("renders Loading… on first paint when no data + isLoading", () => {
    mockHook.useWslProcesses.mockReturnValue(
      mkResult(null, { isLoading: true }),
    );
    render(<WslProcessesPanel />);
    expect(screen.getByText("Loading…")).toBeInTheDocument();
  });

  it("renders 'WSL not available' when backend reports unavailable", () => {
    mockHook.useWslProcesses.mockReturnValue(
      mkResult({ available: false, processes: [] }),
    );
    render(<WslProcessesPanel />);
    expect(screen.getByText(/WSL not available/i)).toBeInTheDocument();
  });

  it("renders empty-list message when WSL is available but no procs match", () => {
    mockHook.useWslProcesses.mockReturnValue(
      mkResult({ available: true, processes: [] }),
    );
    render(<WslProcessesPanel />);
    expect(
      screen.getByText(/No SC2 \/ bots \/ evolve \/ selfplay processes/i),
    ).toBeInTheDocument();
  });

  it("renders a row per process with label + RSS formatting", () => {
    mockHook.useWslProcesses.mockReturnValue(
      mkResult({
        available: true,
        processes: [
          {
            pid: 101,
            comm: "SC2_x64",
            etime: "02:31",
            rss_kb: 524288, // 512 MB
            label: "SC2_x64",
          },
          {
            pid: 102,
            comm: "python3.12",
            etime: "00:45",
            rss_kb: 131072, // 128 MB
            label: "bots.v3",
          },
        ],
      }),
    );
    render(<WslProcessesPanel />);
    expect(screen.getByText("101")).toBeInTheDocument();
    expect(screen.getByText("102")).toBeInTheDocument();
    // "SC2_x64" appears in BOTH the label column (bold) and the comm
    // column (gray), so getAllByText is the right query.
    expect(screen.getAllByText("SC2_x64").length).toBe(2);
    expect(screen.getByText("bots.v3")).toBeInTheDocument();
    expect(screen.getByText("512.0 MB")).toBeInTheDocument();
    expect(screen.getByText("128.0 MB")).toBeInTheDocument();
  });

  it("flags (stale) when isStale", () => {
    mockHook.useWslProcesses.mockReturnValue(
      mkResult(
        { available: true, processes: [] },
        { isStale: true },
      ),
    );
    render(<WslProcessesPanel />);
    expect(screen.getByText("(stale)")).toBeInTheDocument();
  });
});
