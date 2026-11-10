import {
  expect,
  test,
  type APIRequestContext,
  type Page,
} from "@playwright/test";
import type { ComparisonData } from "../src/comparisonTypes";
import type { SpecificationData, Workspace } from "../src/types";

async function prepareReport(page: Page, request: APIRequestContext) {
  expect(
    (
      await request.post("/api/workspace/reset", {
        data: { acknowledged: true },
      })
    ).ok(),
  ).toBeTruthy();
  const imported = await request.post("/api/specifications/worked-example");
  expect(imported.ok()).toBeTruthy();
  const { document_id: documentId } = await imported.json();
  const source = (await (
    await request.get(`/api/specifications/${documentId}`)
  ).json()) as SpecificationData;
  const comparison = (await (
    await request.get(`/api/comparisons/${documentId}`)
  ).json()) as ComparisonData;
  const state = {
    posts: 0,
    completed: false,
    failNext: false,
    hold: false,
    release: () => {},
  };
  const held = new Promise<void>((resolve) => {
    state.release = () => {
      state.hold = false;
      resolve();
    };
  });
  await page.route("**/api/workspace", async (route) => {
    const response = await route.fetch();
    const workspace = (await response.json()) as Workspace;
    await route.fulfill({
      json: {
        ...workspace,
        providers: [
          {
            id: "sail",
            label: "Sail",
            model: "offline-report-fixture",
            available: true,
            output_limit: 8192,
          },
        ],
      },
    });
  });
  await page.route(`**/api/comparisons/${documentId}`, (route) =>
    route.fulfill({
      json: {
        ...comparison,
        comparison_id: state.completed
          ? "completed-report"
          : comparison.comparison_id,
        matching_run_id: state.completed ? "saved-fixture-matching" : null,
        comparison_engine: state.completed
          ? "ai_semantic_matching"
          : "deterministic_rules",
      },
    }),
  );
  await page.route(
    `**/api/specifications/${documentId}/matching`,
    async (route) => {
      if (route.request().method() === "GET") {
        await route.fulfill({
          json: {
            source_run_id: source.run!.run_id,
            report_input_fingerprint: comparison.report_input_fingerprint,
            input_fingerprint: "fixture-matching-inputs",
            current_run_id: state.completed ? "saved-fixture-matching" : null,
            runs: [],
          },
        });
        return;
      }
      state.posts++;
      expect(route.request().postDataJSON()).toMatchObject({
        run_id: source.run!.run_id,
        input_fingerprint: comparison.report_input_fingerprint,
        provider: "sail",
        model: "offline-report-fixture",
        output_limit: 8192,
        permission: true,
        rerun: false,
      });
      if (state.failNext) {
        state.failNext = false;
        await route.fulfill({
          status: 502,
          json: {
            detail: "Provider failed (HTTP 503): temporarily unavailable.",
          },
        });
        return;
      }
      if (state.hold) await held;
      state.completed = true;
      await route.fulfill({
        json: {
          status: "completed",
          cached: false,
          run_id: "saved-fixture-matching",
        },
      });
    },
  );
  return { documentId, state, comparison };
}

test("source review generates a report once and refresh does not repeat inference", async ({
  page,
  request,
}) => {
  const { documentId, state } = await prepareReport(page, request);
  state.hold = true;
  await page.goto(`/specifications?document=${documentId}`);
  const generate = page.getByRole("button", {
    name: "Generate report",
    exact: true,
  });
  await expect(generate).toBeVisible();
  expect(state.posts).toBe(0);
  await generate.click();
  await expect(page).toHaveURL(new RegExp(`/reports\\?document=${documentId}`));
  const progress = page.getByRole("dialog", {
    name: "Generating your report",
    exact: true,
  });
  await expect(
    progress.getByRole("status", { name: "Comparing your requirements" }),
  ).toBeVisible();
  await expect(
    progress.getByRole("progressbar", { name: "AI request in progress" }),
  ).not.toHaveAttribute("aria-valuenow");
  await expect(
    page.getByRole("combobox", { name: "Model provider" }),
  ).toHaveCount(0);
  await expect(
    page.getByRole("button", { name: "Run AI matching", exact: true }),
  ).toHaveCount(0);
  expect(state.posts).toBe(1);
  await progress
    .getByRole("button", { name: "View saved report", exact: true })
    .click();
  await expect(progress).not.toBeVisible();
  await page.getByRole("button", { name: "Generating…", exact: true }).click();
  await expect(progress).toBeVisible();
  expect(state.posts).toBe(1);
  await expect
    .poll(() =>
      page.evaluate(
        () => window.history.state?.usr?.generationIntentId ?? null,
      ),
    )
    .toBeNull();
  state.release();
  await expect(progress).not.toBeVisible();
  await expect(page.getByText("Compared", { exact: true })).toBeVisible();
  expect(state.posts).toBe(1);

  await page.reload();
  await expect(page.getByText("Compared", { exact: true })).toBeVisible();
  expect(state.posts).toBe(1);
  await page
    .getByRole("button", { name: "Update report", exact: true })
    .click();
  await expect(
    page.getByText("Your report is up to date.", { exact: true }),
  ).toBeVisible();
  expect(state.posts).toBe(1);

  const consumedIntent = await page.evaluate(() =>
    JSON.parse(
      sessionStorage.getItem("scione.report.consumed-generation-intents") ||
        "[]",
    ).at(-1),
  );
  expect(consumedIntent).toBeTruthy();
  await page.evaluate(
    ({ documentId, consumedIntent }) => {
      window.history.replaceState(
        {
          ...window.history.state,
          usr: {
            generateReportFor: documentId,
            generationIntentId: consumedIntent,
          },
        },
        "",
      );
    },
    { documentId, consumedIntent },
  );
  await page.reload();
  await expect(page.getByText("Compared", { exact: true })).toBeVisible();
  expect(state.posts).toBe(1);
});

test("ordinary report visits are free of model calls and failed generation needs an explicit retry", async ({
  page,
  request,
}) => {
  const { documentId, state } = await prepareReport(page, request);
  await page.goto(`/reports?document=${documentId}`);
  await expect(page.getByText("Preview", { exact: true })).toBeVisible();
  expect(state.posts).toBe(0);
  await page.reload();
  await expect(page.getByText("Preview", { exact: true })).toBeVisible();
  expect(state.posts).toBe(0);
  state.failNext = true;
  await page
    .getByRole("button", { name: "Generate report", exact: true })
    .click();
  const failed = page.getByRole("dialog", {
    name: "Report needs another look",
    exact: true,
  });
  await expect(failed.getByRole("alert")).toContainText(
    "The analysis service is busy.",
  );
  await expect(
    failed.getByRole("button", { name: "Try again", exact: true }),
  ).toBeEnabled();
  expect(state.posts).toBe(1);
  await failed.getByRole("button", { name: "Close", exact: true }).click();
  await expect(
    page.getByRole("navigation", { name: "Requested tests" }),
  ).toBeVisible();
  await page.reload();
  await expect(page.getByText("Preview", { exact: true })).toBeVisible();
  expect(state.posts).toBe(1);
  await page
    .getByRole("button", { name: "Generate report", exact: true })
    .click();
  await expect(page.getByText("Compared", { exact: true })).toBeVisible();
  expect(state.posts).toBe(2);
});
