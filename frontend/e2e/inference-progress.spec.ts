import {
  expect,
  test,
  type APIRequestContext,
  type Page,
} from "@playwright/test";
import type { SpecificationData } from "../src/types";

async function progressFixture(page: Page, request: APIRequestContext) {
  await request.post("/api/workspace/reset", { data: { acknowledged: true } });
  const { document_id: id } = await (
    await request.post("/api/specifications/worked-example")
  ).json();
  const source = (await (
    await request.get(`/api/specifications/${id}`)
  ).json()) as SpecificationData;
  source.document.page_count = 13;
  const state = {
    active: true,
    failed: false,
    kind: "extraction",
    progress: {
      stage: "extracting",
      completed_steps: 2,
      total_steps: 6,
      percent: 33.3,
      message: "Reading the next source pages.",
    } as Record<string, unknown>,
  };
  await page.route("**/api/operations", (route) =>
    route.fulfill({
      json: {
        active: state.active
          ? [
              {
                operation_id: "offline-progress",
                document_id: id,
                kind: state.kind,
                state: "running",
                started_at: new Date(Date.now() - 1000).toISOString(),
                elapsed_seconds: 1,
                progress: state.progress,
              },
            ]
          : [],
      },
    }),
  );
  await page.route(`**/api/specifications/${id}`, (route) =>
    route.fulfill({
      json: {
        ...source,
        ...(state.failed
          ? {
              failure: {
                attempt_id: "offline-failure",
                message: "Fixture validation failed.",
                occurred_at: new Date().toISOString(),
                previous_run_id: source.run?.run_id,
              },
            }
          : {}),
      },
    }),
  );
  return { id, state };
}

test("observed AI steps update, keep checks visible at 100 percent, and disappear on failure", async ({
  page,
  request,
}) => {
  const { id, state } = await progressFixture(page, request);
  await page.goto(`/specifications?document=${id}`);
  const card = page.getByRole("status", {
    name: "Document analysis in progress",
    exact: true,
  });
  await expect(card).toBeVisible();
  await expect(
    card.getByText("AI steps completed 2/6 · 33%", { exact: true }),
  ).toBeVisible();
  await expect(card.getByRole("progressbar")).toHaveAttribute(
    "aria-valuenow",
    "33",
  );
  await expect(
    page.getByRole("progressbar", { name: "Active AI steps", exact: true }),
  ).toHaveAttribute("aria-valuenow", "33");
  state.progress = {
    stage: "validating",
    completed_steps: 6,
    total_steps: 6,
    percent: 100,
    message: "Checking the returned results.",
  };
  await page.evaluate(() => window.dispatchEvent(new Event("focus")));
  await expect(card.getByText(/AI steps completed 6\/6 · 100%/)).toBeVisible();
  await expect(
    card.getByText(/Final checks and saving are still in progress/),
  ).toBeVisible();
  await expect(card.getByRole("progressbar")).toHaveAttribute(
    "aria-valuenow",
    "100",
  );
  state.failed = true;
  state.active = false;
  await page.evaluate(() => window.dispatchEvent(new Event("focus")));
  await expect(card).not.toBeVisible();
  await expect(
    page.getByRole("progressbar", { name: "Active AI steps", exact: true }),
  ).not.toBeVisible();
});

test("invalid counters fall back without invented percent or short-document ETA", async ({
  page,
  request,
}) => {
  const { id, state } = await progressFixture(page, request);
  state.progress = {
    stage: "extracting",
    completed_steps: -1,
    total_steps: 6,
    percent: 999,
    message: "Reading the source.",
  };
  await page.addInitScript(() =>
    localStorage.setItem(
      "scione:completed-duration:analysis:v1",
      JSON.stringify([{ seconds: 10, units: 1 }]),
    ),
  );
  await page.goto(`/specifications?document=${id}`);
  const card = page.getByRole("status", {
    name: "Document analysis in progress",
    exact: true,
  });
  await expect(card.getByRole("progressbar")).not.toHaveAttribute(
    "aria-valuenow",
  );
  await expect(
    card.getByText(
      /Several AI steps may be needed. Larger workloads can take several minutes/,
    ),
  ).toBeVisible();
  await expect(card.getByText(/Reference time:/)).not.toBeVisible();
  await expect(card.getByText(/999%|NaN%/)).not.toBeVisible();
  state.progress = {
    stage: "planning",
    completed_steps: 0,
    total_steps: null,
    percent: null,
    message: "Planning the analysis steps.",
  };
  await page.evaluate(() => window.dispatchEvent(new Event("focus")));
  await expect(
    card.getByText("AI steps completed 0 · Planning total", { exact: true }),
  ).toBeVisible();
  await expect(card.getByRole("progressbar")).not.toHaveAttribute(
    "aria-valuenow",
  );
  state.active = false;
  await page.evaluate(() => window.dispatchEvent(new Event("focus")));
  await expect(card).not.toBeVisible();
});

test("report generation dialog follows the same server step progress", async ({
  page,
  request,
}) => {
  const { id, state } = await progressFixture(page, request);
  state.kind = "matching";
  state.progress = {
    stage: "matching",
    completed_steps: 3,
    total_steps: 5,
    percent: 60,
    message: "Comparing the remaining tests.",
  };
  await page.goto(`/reports?document=${id}`);
  await page.getByRole("button", { name: "Generating…", exact: true }).click();
  const dialog = page.getByRole("dialog", {
    name: "Generating your report",
    exact: true,
  });
  await expect(
    dialog.getByText("AI steps completed 3/5 · 60%", { exact: true }),
  ).toBeVisible();
  await expect(dialog.getByRole("progressbar")).toHaveAttribute(
    "aria-valuenow",
    "60",
  );
  state.active = false;
  await page.evaluate(() => window.dispatchEvent(new Event("focus")));
  await expect(dialog.getByRole("progressbar")).not.toBeVisible();
});
