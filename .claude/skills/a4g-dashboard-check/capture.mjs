/**
 * Capture screenshots of every Alpha4Gate dashboard tab.
 *
 * Usage: node <this-file> [tab1 tab2 ...]
 *
 * Saves PNGs to .ui-dashboard-evidence/ in the project root.
 */

import { chromium } from "playwright";
import { mkdirSync } from "fs";
import { resolve, dirname } from "path";
import { fileURLToPath } from "url";

const __dirname = dirname(fileURLToPath(import.meta.url));
const PROJECT_ROOT = resolve(__dirname, "../../..");
const EVIDENCE_DIR = resolve(PROJECT_ROOT, ".ui-dashboard-evidence");

const ALL_TABS = [
  { name: "live", label: "Live" },
  { name: "stats", label: "Stats" },
  { name: "decisions", label: "Decisions" },
  { name: "training", label: "Training" },
  { name: "loop", label: "Loop" },
  { name: "advisor", label: "Advisor" },
  { name: "improvements", label: "Improvements" },
  { name: "processes", label: "Processes" },
  { name: "alerts", label: "Alerts" },
  { name: "ladder", label: "Ladder" },
  { name: "evolution", label: "Evolution" },
];

// Filter tabs from CLI args
const args = process.argv.slice(2);
const tabs = args.length > 0
  ? ALL_TABS.filter((t) => args.includes(t.name))
  : ALL_TABS;

async function main() {
  mkdirSync(EVIDENCE_DIR, { recursive: true });

  const browser = await chromium.launch({ headless: true });
  const page = await browser.newPage({ viewport: { width: 1920, height: 1080 } });

  console.log(`Capturing ${tabs.length} tabs to ${EVIDENCE_DIR}/`);

  // Load the app — use domcontentloaded instead of networkidle because
  // Vite's HMR WebSocket keeps the network active and can cause timeouts.
  await page.goto("http://localhost:3000", { waitUntil: "domcontentloaded", timeout: 15000 });
  // Wait for the React app to render (nav buttons appear)
  await page.waitForSelector("nav button", { timeout: 10000 });
  await page.waitForTimeout(1000);

  for (const tab of tabs) {
    try {
      // Click the tab link in the nav bar
      const link = page.locator(`nav a`, { hasText: tab.label }).first();
      if (await link.count() > 0) {
        await link.click();
      } else {
        // Fallback: try any link/button with matching text
        await page.getByText(tab.label, { exact: true }).first().click();
      }
      if (tab.name === "processes") {
        // Wait for the process scan API to resolve (shows "Active Processes" heading)
        await page.waitForSelector("h3:has-text('Active Processes')", { timeout: 15000 }).catch(() => {});
        await page.waitForTimeout(500);
      } else {
        await page.waitForTimeout(2000);
      }

      const out = resolve(EVIDENCE_DIR, `${tab.name}.png`);
      await page.screenshot({ path: out, fullPage: true });
      console.log(`  ✓ ${tab.name}`);
    } catch (err) {
      console.error(`  ✗ ${tab.name}: ${err.message.split("\n")[0]}`);
    }
  }

  await browser.close();
  console.log(`\nDone. Screenshots in ${EVIDENCE_DIR}/`);
}

main().catch((err) => {
  console.error("Fatal:", err.message);
  process.exit(1);
});
