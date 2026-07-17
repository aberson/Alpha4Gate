import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { renderHook, act } from "@testing-library/react";
import { theme, darkTheme } from "../../brand/dist/theme";
import { resolveThemeMode, useTheme, useThemeMode } from "./useBrandTheme";

/**
 * Dark-mode chart-chrome flip proof (on-brand pilot Step 16).
 *
 * Before this pilot the Recharts chrome (grid/axis/label/tooltip) was
 * hardcoded to dark values and never flipped. Now chrome is sourced from the
 * mode-aware brand theme. These tests prove the flip end-to-end AT THE
 * PRODUCTION PATH:
 *   - the chrome tokens genuinely DIFFER between light and dark;
 *   - the REAL hooks (useTheme / useThemeMode — what the components call)
 *     return the mode-appropriate chrome and RE-RENDER when the OS scheme
 *     flips (matchMedia 'change') or a runtime [data-theme] mutates
 *     (MutationObserver). Flipping the hook's ternary turns these RED.
 *
 * Each test installs its OWN fresh, controllable matchMedia stub and clears
 * [data-theme] before/after, so concurrent full-suite runs stay deterministic.
 */

interface FakeMediaQueryList {
  matches: boolean;
  media: string;
  onchange: null;
  addEventListener: (type: string, cb: () => void) => void;
  removeEventListener: (type: string, cb: () => void) => void;
  addListener: (cb: () => void) => void;
  removeListener: (cb: () => void) => void;
  dispatchEvent: () => boolean;
}

/**
 * Install a controllable window.matchMedia. Returns an ``emitChange`` that
 * updates the dark state and fires every registered 'change' listener — so we
 * exercise the hook's real addEventListener wiring, not just a re-call of the
 * pure resolver.
 */
function installMatchMedia(initialDark: boolean) {
  let dark = initialDark;
  const listeners = new Set<() => void>();
  const mql: FakeMediaQueryList = {
    get matches() {
      return dark;
    },
    media: "(prefers-color-scheme: dark)",
    onchange: null,
    addEventListener: (_type, cb) => {
      listeners.add(cb);
    },
    removeEventListener: (_type, cb) => {
      listeners.delete(cb);
    },
    addListener: (cb) => {
      listeners.add(cb);
    },
    removeListener: (cb) => {
      listeners.delete(cb);
    },
    dispatchEvent: () => false,
  };
  vi.stubGlobal("matchMedia", () => mql);
  return {
    emitChange(nextDark: boolean) {
      dark = nextDark;
      for (const cb of [...listeners]) cb();
    },
  };
}

beforeEach(() => {
  document.documentElement.removeAttribute("data-theme");
});

afterEach(() => {
  vi.unstubAllGlobals();
  document.documentElement.removeAttribute("data-theme");
});

describe("brand theme — tokens + pure resolveThemeMode", () => {
  it("chart chrome tokens differ between light and dark (chrome is not frozen)", () => {
    expect(darkTheme.color.chart.chrome.grid).not.toBe(
      theme.color.chart.chrome.grid,
    );
    expect(darkTheme.color.chart.chrome.axis).not.toBe(
      theme.color.chart.chrome.axis,
    );
    expect(darkTheme.color.chart.chrome.label).not.toBe(
      theme.color.chart.chrome.label,
    );
    expect(darkTheme.color.chart.chrome["tooltip-bg"]).not.toBe(
      theme.color.chart.chrome["tooltip-bg"],
    );
  });

  it("resolves dark / light from prefers-color-scheme", () => {
    installMatchMedia(true);
    expect(resolveThemeMode()).toBe("dark");
    installMatchMedia(false);
    expect(resolveThemeMode()).toBe("light");
  });

  it("an explicit [data-theme='dark'] opt-in wins over an OS light preference", () => {
    installMatchMedia(false);
    document.documentElement.setAttribute("data-theme", "dark");
    expect(resolveThemeMode()).toBe("dark");
  });
});

describe("brand theme — live hooks (production path)", () => {
  it("useTheme returns light chrome, then flips to dark on an OS 'change' event", () => {
    const mm = installMatchMedia(false);
    const { result } = renderHook(() => useTheme());

    // Light mount: the concrete light chrome.
    expect(result.current.color.chart.chrome.grid).toBe(
      theme.color.chart.chrome.grid,
    );
    expect(result.current.color.chart.chrome.grid).toBe("#e5e4e7");
    expect(result.current.color.chart.chrome["tooltip-bg"]).toBe("#ffffff");

    // Fire the real matchMedia listener the hook registered.
    act(() => mm.emitChange(true));

    // Flipped to the concrete dark chrome via the hook's re-render.
    expect(result.current.color.chart.chrome.grid).toBe(
      darkTheme.color.chart.chrome.grid,
    );
    expect(result.current.color.chart.chrome.grid).toBe("#333333");
    expect(result.current.color.chart.chrome["tooltip-bg"]).toBe("#1a1a1a");
  });

  it("useThemeMode reflects a dark mount and re-renders to light on an OS 'change'", () => {
    const mm = installMatchMedia(true);
    const { result } = renderHook(() => useThemeMode());
    expect(result.current).toBe("dark");
    act(() => mm.emitChange(false));
    expect(result.current).toBe("light");
  });

  it("useThemeMode reacts to a runtime [data-theme] mutation (MutationObserver)", async () => {
    installMatchMedia(false); // OS says light
    const { result } = renderHook(() => useThemeMode());
    expect(result.current).toBe("light");

    await act(async () => {
      document.documentElement.setAttribute("data-theme", "dark");
      // Let the MutationObserver microtask/task deliver before asserting.
      await new Promise((resolve) => setTimeout(resolve, 0));
    });

    expect(result.current).toBe("dark");
  });
});
