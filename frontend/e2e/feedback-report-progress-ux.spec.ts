import {
  expect,
  test,
  type APIRequestContext,
  type Page,
} from "@playwright/test";
import type { ComparisonData } from "../src/comparisonTypes";
import type { SpecificationData, Workspace } from "../src/types";

async function fixture(request: APIRequestContext) {
  expect(
    (
      await request.post("/api/workspace/reset", {
        data: { acknowledged: true },
      })
    ).ok(),
  ).toBeTruthy();
  const response = await request.post("/api/specifications/worked-example");
  expect(response.ok()).toBeTruthy();
  const { document_id: id } = await response.json();
  const source = (await (
    await request.get(`/api/specifications/${id}`)
  ).json()) as SpecificationData;
  const comparison = (await (
    await request.get(`/api/comparisons/${id}`)
  ).json()) as ComparisonData;
  return { id, source, comparison };
}

// DOM selection is intentional: MUI hides background nodes from accessibility
// while a dialog is open, which must not be mistaken for removing duplication.
const globalProgress = (page: Page) =>
  page.locator("a").filter({ hasText: /^View progress$/ });

test("report progress appears once, returns when the dialog closes and retains connection warnings", async ({
  page,
  request,
}) => {
  const { id } = await fixture(request);
  let posts = 0;
  let unavailable = false;
  const operation = {
    operation_id: "feedback-report-progress",
    document_id: id,
    kind: "matching",
    state: "running",
    started_at: new Date().toISOString(),
    progress: {
      stage: "matching",
      completed_steps: 1,
      total_steps: 2,
      percent: 50,
      message: "Comparing the next tests.",
    },
  };
  await page.route("**/api/operations", (route) =>
    unavailable
      ? route.fulfill({
          status: 503,
          json: { detail: "Temporary status interruption" },
        })
      : route.fulfill({ json: { active: [operation] } }),
  );
  page.on("request", (sent) => {
    if (
      sent.method() === "POST" &&
      /\/(analysis|matching)$/.test(new URL(sent.url()).pathname)
    )
      posts++;
  });
  await page.goto(`/reports?document=${id}`);
  await expect(globalProgress(page)).toHaveCount(1);
  await page.getByRole("button", { name: "Generating…", exact: true }).click();
  const dialog = page.getByRole("dialog", {
    name: "Generating your report",
    exact: true,
  });
  await expect(
    dialog.getByRole("status", {
      name: "Preparing your comparison",
      exact: true,
    }),
  ).toBeVisible();
  await expect(globalProgress(page)).toHaveCount(0);
  await dialog
    .getByRole("button", { name: "View saved report", exact: true })
    .click();
  await expect(globalProgress(page)).toHaveCount(1);
  await page.getByRole("button", { name: "Generating…", exact: true }).click();
  await expect(dialog).toBeVisible();
  await expect(globalProgress(page)).toHaveCount(0);
  unavailable = true;
  await page.evaluate(() => window.dispatchEvent(new Event("focus")));
  await expect(globalProgress(page)).toHaveCount(1);
  await expect(
    page
      .locator(".MuiAlert-root")
      .filter({ hasText: "Analysis status unavailable" }),
  ).toBeVisible();
  expect(posts).toBe(0);
});

test("developer matching hides duplicate progress only during the mounted matching view", async ({
  page,
  request,
}) => {
  const { id, source, comparison } = await fixture(request);
  let posts = 0;
  let completed = false;
  let release!: () => void;
  const gate = new Promise<void>((resolve) => {
    release = resolve;
  });
  await page.route("**/api/workspace", async (route) => {
    const original = await route.fetch();
    const workspace = (await original.json()) as Workspace;
    await route.fulfill({
      json: {
        ...workspace,
        providers: [
          {
            id: "sail",
            label: "Sail",
            model: "offline-feedback-progress",
            available: true,
            output_limit: 8192,
          },
        ],
      },
    });
  });
  await page.route(`**/api/specifications/${id}/matching`, async (route) => {
    if (route.request().method() === "GET") {
      await route.fulfill({
        json: {
          source_run_id: source.run!.run_id,
          report_input_fingerprint: comparison.report_input_fingerprint,
          input_fingerprint: "feedback-progress-input",
          current_run_id: completed ? "feedback-progress-complete" : null,
          runs: [],
        },
      });
      return;
    }
    posts++;
    await gate;
    completed = true;
    await route.fulfill({
      json: {
        run_id: "feedback-progress-complete",
        status: "completed",
        cached: false,
      },
    });
  });
  await page.goto(`/reports?document=${id}`);
  await page
    .getByRole("button", { name: "Developer report tools", exact: true })
    .click();
  await page
    .getByRole("button", { name: "Run AI matching", exact: true })
    .click();
  const dialog = page.getByRole("dialog", {
    name: "Run AI matching",
    exact: true,
  });
  await dialog.getByRole("checkbox", { name: /I allow sending/ }).check();
  await dialog
    .getByRole("button", { name: "Start matching", exact: true })
    .click();
  try {
    await expect.poll(() => posts).toBe(1);
    await expect(
      dialog.getByRole("status", {
        name: "Comparing your requirements",
        exact: true,
      }),
    ).toBeVisible();
    await expect(globalProgress(page)).toHaveCount(0);
  } finally {
    release();
  }
  await expect(dialog).not.toBeVisible();
  await expect(globalProgress(page)).toHaveCount(0);
  expect(posts).toBe(1);
});
