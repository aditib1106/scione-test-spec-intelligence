import { defineConfig } from "@playwright/test";
import { existsSync } from "node:fs";

const quote = (value: string) => `'${value.replaceAll("'", "'\\''")}'`;
const python = quote(process.env.SCIONE_TEST_PYTHON ?? "python");

export default defineConfig({
  testDir: "./e2e",
  fullyParallel: false,
  workers: 1,
  timeout: 120_000,
  expect: { timeout: 15_000 },
  use: {
    baseURL: "http://127.0.0.1:8010",
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
    launchOptions: {
      executablePath:
        process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE ??
        (existsSync("/usr/bin/chromium") ? "/usr/bin/chromium" : undefined),
    },
  },
  projects: [
    { name: "desktop", use: { viewport: { width: 1440, height: 1000 } } },
    { name: "narrow", use: { viewport: { width: 390, height: 844 } } },
  ],
  webServer: [
    {
      command: `${python} ../tests/fixtures/workspace_server.py --port 8010`,
      url: "http://127.0.0.1:8010/api/health",
      reuseExistingServer: false,
      timeout: 120_000,
    },
    {
      command: `${python} ../tests/fixtures/workspace_server.py --port 8011 --seed-review`,
      url: "http://127.0.0.1:8011/api/health",
      reuseExistingServer: false,
      timeout: 120_000,
    },
  ],
});
