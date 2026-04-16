/**
 * Capture a focused screenshot of the Reward Trends component for the README.
 * Saves to documentation/images/reward-trends.png.
 */
import { chromium } from "playwright";
import { resolve, dirname } from "path";
import { fileURLToPath } from "url";

const __dirname = dirname(fileURLToPath(import.meta.url));
const PROJECT_ROOT = resolve(__dirname, "../../..");
const OUT = resolve(PROJECT_ROOT, "documentation/images/reward-trends.png");

const browser = await chromium.launch({ headless: true });
const page = await browser.newPage({
  viewport: { width: 1280, height: 900 },
  deviceScaleFactor: 2,
});

await page.goto("http://localhost:3000", {
  waitUntil: "domcontentloaded",
  timeout: 15000,
});
await page.waitForSelector("nav button", { timeout: 10000 });
await page.waitForTimeout(500);

const link = page.locator("nav a", { hasText: "Improvements" }).first();
if ((await link.count()) > 0) {
  await link.click();
} else {
  await page.getByText("Improvements", { exact: true }).first().click();
}
await page.waitForSelector(".reward-trends", { timeout: 10000 });
await page.waitForTimeout(2500);

const el = page.locator(".reward-trends").first();
await el.screenshot({ path: OUT });
console.log(`Saved ${OUT}`);

await browser.close();
