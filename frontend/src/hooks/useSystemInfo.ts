/**
 * Shared hook bundling the three /api/system/* endpoints added 2026-04-28
 * for WSL-substrate visibility:
 *
 *  - /api/system/substrate    — host platform + WSL distro detection
 *  - /api/system/wsl-processes — SC2/python procs inside the WSL VM
 *  - /api/system/resources    — Windows host + WSL VM RAM + disk gauge
 *
 * Each is fetched via the same offline-first useApi hook so cached
 * responses keep rendering when the backend blips. Polling intervals are
 * tuned per endpoint:
 *
 *  - Substrate: 30s (essentially static within a session)
 *  - WSL processes + resources: 3s (matches backend cache TTL)
 */
import { useApi } from "./useApi";
import type { UseApiResult } from "./useApi";

export interface SubstrateInfo {
  backend_platform: string;
  wsl: {
    available: boolean;
    distro: string | null;
    kernel: string | null;
    sc2_path: string | null;
    sc2_binary_present: boolean;
  };
}

export interface WslProcess {
  pid: number;
  comm: string;
  etime: string;
  rss_kb: number;
  label: string;
}

export interface WslProcessList {
  available: boolean;
  processes: WslProcess[];
}

export interface ResourceGauges {
  host: {
    available: boolean;
    ram_total_gb: number;
    ram_used_gb: number;
    ram_free_gb: number;
    ram_pct_used: number;
    disk_total_gb: number | null;
    disk_free_gb: number | null;
    disk_pct_used: number | null;
  };
  wsl: {
    available: boolean;
    ram_total_gb: number | null;
    ram_used_gb: number | null;
    ram_free_gb: number | null;
    ram_pct_used: number | null;
    swap_used_gb: number | null;
    swap_total_gb: number | null;
    load_avg_5m: number | null;
  };
}

const SUBSTRATE_POLL_MS = 30_000;
const SHORT_POLL_MS = 3_000;

export function useSubstrateInfo(): UseApiResult<SubstrateInfo> {
  return useApi<SubstrateInfo>("/api/system/substrate", {
    pollMs: SUBSTRATE_POLL_MS,
  });
}

export function useWslProcesses(): UseApiResult<WslProcessList> {
  return useApi<WslProcessList>("/api/system/wsl-processes", {
    pollMs: SHORT_POLL_MS,
  });
}

export function useResourceGauges(): UseApiResult<ResourceGauges> {
  return useApi<ResourceGauges>("/api/system/resources", {
    pollMs: SHORT_POLL_MS,
  });
}
