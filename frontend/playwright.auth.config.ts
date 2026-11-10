import { defineConfig } from "@playwright/test";
import { existsSync } from "node:fs";

const quote = (value: string) => `'${value.replaceAll("'", "'\\''")}'`;
const python = quote(process.env.SCIONE_TEST_PYTHON ?? "python");

export default defineConfig({
  testDir: "./e2e",
  testMatch: "auth-real.spec.ts",
  workers: 1,
  timeout: 60_000,
  expect: { timeout: 10_000 },
  use: {
    baseURL: "http://127.0.0.1:8012",
    screenshot: "only-on-failure",
    trace: "retain-on-failure",
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
  webServer: {
    command: `${python} ../tests/fixtures/auth_server.py --port 8012`,
    url: "http://127.0.0.1:8012/api/health",
    reuseExistingServer: false,
    timeout: 120_000,
  },
});
