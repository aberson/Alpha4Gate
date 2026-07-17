import { describe, it, expect, afterEach, vi } from "vitest";
import { theme, darkTheme } from "../../brand/dist/theme";
import { resolveThemeMode } from "./useBrandTheme";

/**
 * Dark-mode chart-chrome flip proof (on-brand pilot Step 16).
 *
 * Before this pilot the Recharts chrome (grid/axis/label/tooltip) was
 * hardcoded to dark values and never flipped with the theme. Now chrome is
 * sourced from the mode-aware brand theme. These tests prove the flip
 * mechanism end-to-end: (1) the chrome tokens genuinely DIFFER between light
 * and dark, and (2) mode resolution picks the right one from
 * prefers-color-scheme (and an explicit [data-theme] opt-in wins).
 */

function mockMatchMedia(matchesDark: boolean): void {
  vi.stubGlobal("matchMedia", (query: string) => ({
    matches: query.includes("dark") ? matchesDark : false,
    media: query,
    onchange: null,
    addEventListener: () => {},
    removeEventListener: () => {},
    addListener: () => {},
    removeListener: () => {},
    dispatchEvent: () => false,
  }));
}

function activeChrome() {
  return (resolveThemeMode() === "dark" ? darkTheme : theme).color.chart.chrome;
}

afterEach(() => {
  vi.unstubAllGlobals();
  document.documentElement.removeAttribute("data-theme");
});

describe("brand theme — chart-chrome dark flip", () => {
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

  it("resolves dark mode from prefers-color-scheme and selects dark chrome", () => {
    mockMatchMedia(true);
    expect(resolveThemeMode()).toBe("dark");
    expect(activeChrome().grid).toBe(darkTheme.color.chart.chrome.grid);
    expect(activeChrome()["tooltip-bg"]).toBe("#1a1a1a");
  });

  it("resolves light mode from prefers-color-scheme and selects light chrome", () => {
    mockMatchMedia(false);
    expect(resolveThemeMode()).toBe("light");
    expect(activeChrome().grid).toBe(theme.color.chart.chrome.grid);
    expect(activeChrome()["tooltip-bg"]).toBe("#ffffff");
  });

  it("an explicit [data-theme='dark'] opt-in wins over an OS light preference", () => {
    mockMatchMedia(false);
    document.documentElement.setAttribute("data-theme", "dark");
    expect(resolveThemeMode()).toBe("dark");
    expect(activeChrome().grid).toBe(darkTheme.color.chart.chrome.grid);
  });

  it("the selected chrome flips end-to-end when the OS scheme changes", () => {
    mockMatchMedia(true);
    const dark = activeChrome();
    mockMatchMedia(false);
    const light = activeChrome();
    expect(dark.grid).not.toBe(light.grid);
    expect(dark.axis).not.toBe(light.axis);
    expect(dark.label).not.toBe(light.label);
    expect(dark["tooltip-bg"]).not.toBe(light["tooltip-bg"]);
  });
});
