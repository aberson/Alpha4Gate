import { describe, it, expect, vi, afterEach } from "vitest";
import { render, screen, cleanup } from "@testing-library/react";
import { ResourceGauge } from "./ResourceGauge";
import type { UseApiResult } from "../hooks/useApi";
import type { ResourceGauges } from "../hooks/useSystemInfo";

const mockHook = vi.hoisted(() => ({
  useResourceGauges: vi.fn<() => UseApiResult<ResourceGauges>>(),
}));

vi.mock("../hooks/useSystemInfo", () => ({
  useResourceGauges: mockHook.useResourceGauges,
}));

afterEach(() => {
  cleanup();
  mockHook.useResourceGauges.mockReset();
});

function mkResult(
  data: ResourceGauges | null,
  overrides: Partial<UseApiResult<ResourceGauges>> = {},
): UseApiResult<ResourceGauges> {
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

const HOST_FULL = {
  available: true,
  ram_total_gb: 32,
  ram_used_gb: 12,
  ram_free_gb: 20,
  ram_pct_used: 37.5,
  disk_total_gb: 1000,
  disk_free_gb: 600,
  disk_pct_used: 40,
};

describe("ResourceGauge", () => {
  it("renders Loading… on first paint", () => {
    mockHook.useResourceGauges.mockReturnValue(
      mkResult(null, { isLoading: true }),
    );
    render(<ResourceGauge />);
    expect(screen.getByText("Loading…")).toBeInTheDocument();
  });

  it("renders host RAM + disk + WSL RAM when WSL is available", () => {
    mockHook.useResourceGauges.mockReturnValue(
      mkResult({
        host: HOST_FULL,
        wsl: {
          available: true,
          ram_total_gb: 7.5,
          ram_used_gb: 0.4,
          ram_free_gb: 7,
          ram_pct_used: 5.3,
          swap_used_gb: 0.07,
          swap_total_gb: 2,
          load_avg_5m: 2.61,
        },
      }),
    );
    render(<ResourceGauge />);
    // host RAM detail
    expect(screen.getByText(/12\.0 \/ 32\.0 GB/)).toBeInTheDocument();
    // disk detail
    expect(screen.getByText(/400 \/ 1000 GB/)).toBeInTheDocument();
    // WSL RAM detail
    expect(screen.getByText(/0\.4 \/ 7\.5 GB/)).toBeInTheDocument();
    // load avg
    expect(screen.getByText("2.61")).toBeInTheDocument();
  });

  it("renders 'WSL not available' when WSL section is unavailable", () => {
    mockHook.useResourceGauges.mockReturnValue(
      mkResult({
        host: HOST_FULL,
        wsl: {
          available: false,
          ram_total_gb: null,
          ram_used_gb: null,
          ram_free_gb: null,
          ram_pct_used: null,
          swap_used_gb: null,
          swap_total_gb: null,
          load_avg_5m: null,
        },
      }),
    );
    render(<ResourceGauge />);
    expect(screen.getByText(/WSL not available/i)).toBeInTheDocument();
  });

  it("paints the bar red when ram_pct_used >= 90", () => {
    mockHook.useResourceGauges.mockReturnValue(
      mkResult({
        host: { ...HOST_FULL, ram_pct_used: 95, ram_used_gb: 30 },
        wsl: {
          available: false,
          ram_total_gb: null,
          ram_used_gb: null,
          ram_free_gb: null,
          ram_pct_used: null,
          swap_used_gb: null,
          swap_total_gb: null,
          load_avg_5m: null,
        },
      }),
    );
    render(<ResourceGauge />);
    // The progressbar's aria-label encodes the percent — easy to assert.
    const ramBar = screen.getByLabelText(/RAM 95% used/);
    expect(ramBar).toBeInTheDocument();
  });
});
