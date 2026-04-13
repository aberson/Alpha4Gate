import { defineConfig } from "@playwright/test";

export default defineConfig({
  testDir: ".",
  testMatch: "capture.mjs",
  timeout: 30000,
  use: {
    viewport: { width: 1920, height: 1080 },
    headless: true,
  },
  reporter: "list",
});
