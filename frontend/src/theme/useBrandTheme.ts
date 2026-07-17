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

/** Reactive theme mode — re-renders when the OS color scheme flips. */
export function useThemeMode(): ThemeMode {
  const [mode, setMode] = useState<ThemeMode>(resolveThemeMode);
  useEffect(() => {
    if (
      typeof window === "undefined" ||
      typeof window.matchMedia !== "function"
    ) {
      return;
    }
    const mq = window.matchMedia("(prefers-color-scheme: dark)");
    const onChange = () => setMode(resolveThemeMode());
    mq.addEventListener("change", onChange);
    return () => mq.removeEventListener("change", onChange);
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
