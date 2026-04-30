import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, waitFor, cleanup } from "@testing-library/react";
import { HelpTab } from "./HelpTab";

function jsonResponse(body: unknown, ok = true, status = 200): Response {
  return {
    ok,
    status,
    json: async () => body,
  } as unknown as Response;
}

const SAMPLE_MARKDOWN = `# Operator commands — Alpha4Gate cheat sheet

Sample copy for the test.

## Quick orientation

\`\`\`powershell
PS> Get-Content data\\evolve_run_state.json
\`\`\`

| Symptom | Check |
|---|---|
| backend down | port 8765 |
`;

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

beforeEach(() => {
  vi.spyOn(globalThis, "fetch").mockImplementation(async () =>
    jsonResponse({ markdown: SAMPLE_MARKDOWN }),
  );
});

describe("HelpTab", () => {
  it("shows loading state before first fetch resolves", () => {
    render(<HelpTab />);
    // Initial render before fetch resolves: loading message visible.
    expect(screen.getByText(/Loading help/i)).toBeDefined();
  });

  it("renders markdown headings, code blocks, and tables once data loads", async () => {
    render(<HelpTab />);
    // The H1 from the doc renders as <h1>...</h1> via react-markdown.
    await waitFor(() =>
      expect(
        screen.getByRole("heading", { level: 1, name: /Operator commands/i }),
      ).toBeDefined(),
    );
    // Subheading also renders.
    expect(
      screen.getByRole("heading", { level: 2, name: /Quick orientation/i }),
    ).toBeDefined();
    // Table cell renders.
    expect(screen.getByText(/port 8765/i)).toBeDefined();
  });
});
