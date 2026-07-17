import { useEffect, useState } from "react";
import { theme, darkTheme } from "../../brand/dist/theme";

/**
 * Mode-aware access to the generated brand theme (on-brand pilot Step 16).
 *
 * The CSS layer flips light/dark automatically through tokens.css
 * (@media prefers-color-scheme + [data-theme="dark"]). Recharts, however,
 * renders SVG with CONCRETE color props and cannot read CSS custom
 * properties, so chart chrome (grid/axis/label/tooltip) was previously
 * hardcoded to dark values and never flipped. These helpers give chart
 * components the mode-appropriate concrete colors from ``theme`` /
 * ``darkTheme`` so the chrome now flips with the theme.
 *
 * Token flip coverage (deliberate asymmetry):
 *   - color.chart.chrome.*  — flips in BOTH CSS (tokens.css @media /
 *     [data-theme]) and JS (theme.ts darkTheme). Components read it from JS
 *     because Recharts needs concrete color props.
 *   - color.chart.categorical.* — has NO dark override (identity/series
 *     colours, mode-invariant BY DESIGN). It is a JS-only palette here: a CSS
 *     consumer of --color-chart-categorical-* would get the light values in
 *     both modes. That is intentional; if a future design needs dark-mode
 *     series colours, add a modes.dark.json override and re-run `onbrand build`.
 */

export type ThemeMode = "light" | "dark";

/**
 * Resolve the active mode the same way tokens.css does: an explicit
 * ``[data-theme]`` opt-in on the document element wins; otherwise fall back
 * to the OS ``prefers-color-scheme``. ``matchMedia`` is guarded so this is
 * safe under jsdom (tests), where it is undefined.
 */
export function resolveThemeMode(): ThemeMode {
  if (typeof document !== "undefined") {
    const attr = document.documentElement.dataset.theme;
    if (attr === "dark") return "dark";
    if (attr === "light") return "light";
  }
  if (typeof window !== "undefined" && typeof window.matchMedia === "function") {
    return window.matchMedia("(prefers-color-scheme: dark)").matches
      ? "dark"
      : "light";
  }
  return "light";
}

/**
 * Reactive theme mode. Re-renders on EITHER live signal that
 * ``resolveThemeMode`` reads:
 *   1. the OS ``prefers-color-scheme`` (matchMedia ``change`` event), and
 *   2. a runtime ``[data-theme]`` opt-in on ``<html>`` (MutationObserver).
 * Both subscriptions are cleaned up on unmount. No in-app toggle sets
 * ``[data-theme]`` today, but the observer makes the advertised opt-in path
 * genuinely live for a future toggle without further wiring.
 */
export function useThemeMode(): ThemeMode {
  const [mode, setMode] = useState<ThemeMode>(resolveThemeMode);
  useEffect(() => {
    const update = () => setMode(resolveThemeMode());

    let mq: MediaQueryList | undefined;
    if (typeof window !== "undefined" && typeof window.matchMedia === "function") {
      mq = window.matchMedia("(prefers-color-scheme: dark)");
      mq.addEventListener("change", update);
    }

    let observer: MutationObserver | undefined;
    if (
      typeof document !== "undefined" &&
      typeof MutationObserver === "function"
    ) {
      observer = new MutationObserver(update);
      observer.observe(document.documentElement, {
        attributes: true,
        attributeFilter: ["data-theme"],
      });
    }

    // Re-sync once: the mode could have changed between the render that seeded
    // useState and this effect running.
    update();

    return () => {
      mq?.removeEventListener("change", update);
      observer?.disconnect();
    };
  }, []);
  return mode;
}

/**
 * The active brand theme object (concrete hex strings), mode-aware. Use for
 * Recharts props that need a literal color. The returned object is one of the
 * two generated ``as const`` trees, so property access stays fully typed.
 */
export function useTheme(): typeof theme | typeof darkTheme {
  return useThemeMode() === "dark" ? darkTheme : theme;
}
