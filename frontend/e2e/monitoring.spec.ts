import { expect, test, type Page } from "@playwright/test";

const operator = {
  authenticated: true,
  enabled: true,
  csrf_token: "synthetic-monitoring-csrf",
  user: {
    id: "monitor-admin",
    username: "admin",
    display_name: "Administrator",
    role: "maintainer",
    lab_id: "lab-a",
    lab_name: "Example coatings lab",
  },
};
const summary = {
  count: 10,
  success: 8,
  failure: 2,
  running: 0,
  cache_reuse: 0,
  duration_samples: 7,
  median_ms: 20000,
  p95_ms: 55000,
};
const metrics = {
  version: "business_metrics_v1",
  generated_at: "2026-11-10T18:00:00Z",
  scope: "current",
  days: 30,
  coverage: { lab_count: 1, truncated: false, notices: [] },
  summary: {
    extraction: summary,
    matching: {
      ...summary,
      count: 5,
      success: 4,
      failure: 1,
      duration_samples: 4,
      median_ms: 32000,
      p95_ms: 44000,
    },
    volume: { documents: 3, pages: 12, tests: 36 },
    outcomes: {
      fully_matched: 7,
      partially_matched: 3,
      not_matched: 1,
      needs_information: 2,
      excluded: 1,
    },
    source_review: { needs_review: 2, total_tests: 14, review_rate: 2 / 14 },
  },
  daily: [
    {
      date: "2026-11-09",
      extraction_count: 4,
      matching_count: 2,
      success: 5,
      failure: 1,
      pages: 5,
      tests: 15,
      mean_operation_ms: 23000,
    },
    {
      date: "2026-11-10",
      extraction_count: 6,
      matching_count: 3,
      success: 7,
      failure: 2,
      pages: 7,
      tests: 21,
      mean_operation_ms: 28000,
    },
  ],
  recent: [
    {
      id: "historical-extraction",
      kind: "extraction",
      status: "success",
      started_at: "2026-11-10T15:00:00Z",
      finished_at: null,
      duration_ms: null,
      provider_latency_ms: 14000,
      provider_calls: 1,
      lab_id: "lab-a",
      lab_name: "Example coatings lab",
      document_id: "example",
      run_id: "extraction-run",
      page_count: 2,
      test_count: 4,
      historical: true,
      duration_kind: null,
    },
    {
      id: "recent-matching",
      kind: "matching",
      status: "failure",
      started_at: "2026-11-10T14:00:00Z",
      finished_at: "2026-11-10T14:00:32Z",
      duration_ms: 32000,
      provider_latency_ms: 25000,
      provider_calls: 1,
      lab_id: "lab-a",
      lab_name: "Example coatings lab",
      document_id: "example",
      run_id: "matching-run",
      page_count: 2,
      test_count: 4,
      historical: false,
      duration_kind: "wall_clock",
    },
  ],
};
async function session(page: Page, lab = true, role = "maintainer") {
  await page.route("**/api/auth/session", (route) =>
    route.fulfill({
      json: {
        ...operator,
        user: {
          ...operator.user,
          role,
          lab_id: lab ? "lab-a" : null,
          lab_name: lab ? operator.user.lab_name : null,
        },
      },
    }),
  );
  await page.route("**/api/auth/workspaces", (route) =>
    route.fulfill({
      json: { labs: [{ lab_id: "lab-a", lab_name: operator.user.lab_name }] },
    }),
  );
}

test("administrator monitoring shows real metric categories, truthful latency and usable filters", async ({
  page,
}, info) => {
  await session(page);
  const queries: URL[] = [];
  await page.route("**/api/developer/metrics?*", (route) => {
    const url = new URL(route.request().url());
    queries.push(url);
    return route.fulfill({
      json: {
        ...metrics,
        scope: url.searchParams.get("scope"),
        days: Number(url.searchParams.get("days")),
        coverage: {
          ...metrics.coverage,
          lab_count: url.searchParams.get("scope") === "all" ? 2 : 1,
        },
      },
    });
  });
  await page.goto("/monitoring");
  await expect(
    page.getByRole("heading", { name: "Monitoring", exact: true }),
  ).toBeVisible();
  await expect(
    page.getByText("Recorded success", { exact: true }),
  ).toBeVisible();
  await expect(page.getByText("80%", { exact: true })).toBeVisible();
  await expect(page.getByText("20 sec", { exact: true })).toBeVisible();
  await expect(page.getByText("P95", { exact: true })).toHaveCount(2);
  await expect(
    page.getByRole("img", {
      name: "Recorded extraction and matching operations by day",
      exact: true,
    }),
  ).toBeVisible();
  const old = page
    .getByRole("table", { name: "Recent operations", exact: true })
    .getByRole("row")
    .filter({ hasText: "Historical record" });
  await expect(old.getByText("Not recorded", { exact: true })).toBeVisible();
  await expect(
    old.getByText("Provider time (sum): 14 sec", { exact: true }),
  ).toBeVisible();
  await expect(
    page.getByText(/Capability outcomes.*not model accuracy/),
  ).toBeVisible();
  await page.getByRole("combobox", { name: /^Period/ }).click();
  await page.getByRole("option", { name: "Last 7 days", exact: true }).click();
  await expect.poll(() => queries.at(-1)?.searchParams.get("days")).toBe("7");
  await page.getByRole("combobox", { name: /^Monitoring scope/ }).click();
  await page.getByRole("option", { name: "All labs", exact: true }).click();
  await expect(page.getByText(/2 lab workspaces/)).toBeVisible();
  expect(queries.at(-1)?.searchParams.get("scope")).toBe("all");
  const count = queries.length;
  await page.getByRole("button", { name: "Refresh", exact: true }).click();
  await expect.poll(() => queries.length).toBe(count + 1);
  await page.screenshot({
    path: info.outputPath("monitoring.png"),
    fullPage: true,
    animations: "disabled",
  });
  expect(
    await page.evaluate(
      () =>
        document.documentElement.scrollWidth <=
        document.documentElement.clientWidth + 1,
    ),
  ).toBeTruthy();
});

test("administrator without a selected lab can open empty all-lab metrics and retry an error", async ({
  page,
}) => {
  await session(page, false);
  let workspaceRequests = 0,
    metricsRequests = 0;
  await page.route("**/api/workspace", (route) => {
    workspaceRequests++;
    return route.fulfill({
      status: 400,
      json: { detail: "No workspace selected." },
    });
  });
  await page.route("**/api/developer/metrics?*", (route) => {
    metricsRequests++;
    if (metricsRequests === 2)
      return route.fulfill({
        status: 503,
        json: { detail: "Metrics are temporarily unavailable." },
      });
    const zero = {
      ...summary,
      count: 0,
      success: 0,
      failure: 0,
      duration_samples: 0,
      median_ms: null,
      p95_ms: null,
    };
    return route.fulfill({
      json: {
        ...metrics,
        scope: "all",
        coverage: {
          ...metrics.coverage,
          notices:
            metricsRequests >= 4
              ? [
                  "Metrics are temporarily unavailable for Example coatings lab.",
                ]
              : [],
        },
        summary: {
          extraction: zero,
          matching: zero,
          volume: { documents: 0, pages: 0, tests: 0 },
          outcomes: {},
          source_review: { needs_review: 0, total_tests: 0, review_rate: null },
        },
        daily: [],
        recent: [],
      },
    });
  });
  await page.goto("/monitoring");
  await expect(
    page.getByText(/No operations recorded in this period/),
  ).toBeVisible();
  await expect(
    page.getByText("No recent operations to display.", { exact: true }),
  ).toBeVisible();
  await expect(
    page.getByRole("img", {
      name: "Recorded extraction and matching operations by day",
    }),
  ).toHaveCount(0);
  expect(workspaceRequests).toBe(0);
  await page.getByRole("button", { name: "Refresh", exact: true }).click();
  await expect(
    page
      .getByRole("alert")
      .filter({ hasText: "Metrics are temporarily unavailable" }),
  ).toBeVisible();
  await page.getByRole("button", { name: "Retry", exact: true }).click();
  await expect(
    page.getByText(/No operations recorded in this period/),
  ).toBeVisible();
  await page.getByRole("button", { name: "Refresh", exact: true }).click();
  await expect(
    page.getByText(/Some workspace metrics could not be loaded/),
  ).toBeVisible();
  await expect(
    page.getByText(/No operations recorded in this period/),
  ).toHaveCount(0);
  await expect(
    page
      .getByRole("alert")
      .filter({ hasText: "Metrics are temporarily unavailable for" }),
  ).toHaveClass(/MuiAlert-standardWarning/);
});

test("ordinary lab accounts cannot see or enter the monitoring page", async ({
  page,
}) => {
  await session(page, true, "lab");
  let metricsRequests = 0;
  await page.route("**/api/developer/metrics?*", (route) => {
    metricsRequests++;
    return route.fulfill({
      status: 403,
      json: { detail: "Maintainers only." },
    });
  });
  await page.goto("/monitoring");
  await expect(page).toHaveURL(/\/specifications\/library$/);
  await expect(
    page.getByRole("heading", { name: "Specification library", exact: true }),
  ).toBeVisible();
  if (
    await page
      .getByRole("button", { name: "Open navigation", exact: true })
      .isVisible()
  )
    await page
      .getByRole("button", { name: "Open navigation", exact: true })
      .click();
  await expect(
    page.getByRole("link", { name: "Monitoring", exact: true }),
  ).toHaveCount(0);
  await expect(
    page.getByRole("link", { name: "Developer Tools", exact: true }),
  ).toHaveCount(0);
  expect(metricsRequests).toBe(0);
});
