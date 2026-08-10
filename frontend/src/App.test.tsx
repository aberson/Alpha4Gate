import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, cleanup } from "@testing-library/react";
import App from "./App";

function jsonResponse(body: unknown, ok = true, status = 200): Response {
  return {
    ok,
    status,
    json: async () => body,
  } as unknown as Response;
}

/**
 * Deep-link contract: /?tab=<name> selects that tab on first render
 * (used by scripts/launch-a4g.ps1 -Tab and the dev-observatory
 * run-evolution launch button). Unknown/absent values fall back to
 * the advisor tab.
 */
describe("App tab deep-link", () => {
  let fetchSpy: ReturnType<typeof vi.spyOn> | null = null;

  beforeEach(() => {
    // Default fetch so every polling hook mounted by App resolves quietly.
    fetchSpy = vi
      .spyOn(globalThis, "fetch")
      .mockImplementation(async () => jsonResponse({}));
  });

  afterEach(() => {
    fetchSpy?.mockRestore();
    fetchSpy = null;
    window.history.replaceState({}, "", "/");
    cleanup();
  });

  function navButton(name: string): HTMLElement {
    // Nav buttons live inside the header <nav>; scoping avoids matching
    // same-named buttons rendered inside tab content.
    const nav = document.querySelector("nav");
    if (!nav) throw new Error("header nav not rendered");
    const match = Array.from(nav.querySelectorAll("button")).find(
      (btn) => btn.textContent?.trim() === name,
    );
    if (!match) throw new Error(`nav button '${name}' not found`);
    return match;
  }

  it("opens the Evolution tab for ?tab=evolution", () => {
    window.history.replaceState({}, "", "/?tab=evolution");
    render(<App />);
    expect(navButton("Evolution")).toHaveClass("active");
    expect(navButton("Advisor")).not.toHaveClass("active");
  });

  it("matches tab names case-insensitively (?tab=Evolution)", () => {
    window.history.replaceState({}, "", "/?tab=Evolution");
    render(<App />);
    expect(navButton("Evolution")).toHaveClass("active");
  });

  it("defaults to the Advisor tab with no query param", () => {
    render(<App />);
    expect(navButton("Advisor")).toHaveClass("active");
  });

  it("falls back to the Advisor tab for an unknown tab value", () => {
    window.history.replaceState({}, "", "/?tab=nonsense");
    render(<App />);
    expect(navButton("Advisor")).toHaveClass("active");
    expect(screen.queryByText("nonsense")).not.toBeInTheDocument();
  });
});
