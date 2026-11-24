import {
  expect,
  test,
  type APIRequestContext,
  type Page,
} from "@playwright/test";
import type {
  ComparisonData,
  ComparisonRequirement,
} from "../src/comparisonTypes";

test.use({ actionTimeout: 15_000, timezoneId: "America/New_York" });

async function fixture(request: APIRequestContext) {
  expect(
    (
      await request.post("/api/workspace/reset", {
        data: { acknowledged: true },
      })
    ).ok(),
  ).toBeTruthy();
  const lab = await (await request.get("/api/lab")).json();
  expect(
    (
      await request.post("/api/lab/example", {
        data: { version: lab.version, example: "csp301" },
      })
    ).ok(),
  ).toBeTruthy();
  const { document_id: id } = await (
    await request.post("/api/specifications/worked-example")
  ).json();
  const report = (await (
    await request.get(`/api/comparisons/${id}`)
  ).json()) as ComparisonData;
  const target = report.requirements.find((item) =>
    item.comparison.checks.some((check) => check.status === "unknown"),
  )!;
  const reference = {
    review: {
      review_id: "previous-reference-1",
      comparison_id: "previous-assessment",
      requirement_id: "previous-test",
      version: 1,
      review_status: "pending",
      decision: "clarify_customer",
      note: "Customer agreed to confirm the earlier panel preparation.",
      actor: "local_reviewer",
      created_at: "2026-10-20T10:00:00Z",
      machine_status: "needs_information",
    },
    document: {
      document_id: "previous-source",
      title: "Previous coating request",
      company_name: "Earlier customer",
      specification_id: "CSP-OLD",
      specification_date: "2026-10-19",
      original_name: "prior-request.pdf",
      revision: null,
    },
    test_name: target.test_name,
    methods: target.methods,
    lab_version: Math.max(0, report.lab_version - 1),
    lab_changed: true,
    same_source: false,
    different_customer: true,
    customer_context: "different",
    comparison: {
      status: "needs_information",
      checks: [
        {
          id: "oldcheck",
          label: "Earlier temperature",
          kind: "condition",
          parameter_id: "temperature",
          stage: "Conditioning",
          unit: "°C",
          requested: "20 ± 2 °C",
          declared: null,
          status: "unknown",
          reason: "No temperature declaration was recorded at the time.",
          source_pages: [1],
        },
      ],
    },
  };
  return {
    id: id as string,
    report,
    target,
    response: {
      document_id: id,
      comparison_id: report.comparison_id,
      requirement_id: target.requirement_id,
      reference_only: true,
      entries: [reference],
    },
  };
}
async function openHistory(page: Page, target: ComparisonRequirement) {
  await page
    .getByRole("navigation", { name: "Requested tests" })
    .getByRole("button", { name: target.test_name, exact: true })
    .click();
  await page
    .getByRole("button", { name: "Review history", exact: true })
    .click();
  return page.getByRole("dialog", { name: "Review history", exact: true });
}

test("past decisions are read on demand with old evidence, without applying them to the current report", async ({
  page,
  request,
}, testInfo) => {
  const { id, report, target, response } = await fixture(request);
  let reads = 0,
    writes = 0;
  await page.route(`**/api/comparisons/${id}/related-reviews?*`, (route) => {
    reads++;
    const url = new URL(route.request().url());
    expect(url.searchParams.get("comparison_id")).toBe(report.comparison_id);
    expect(url.searchParams.get("requirement_id")).toBe(target.requirement_id);
    expect(url.searchParams.get("input_fingerprint")).toBe(
      report.report_input_fingerprint,
    );
    return route.fulfill({ json: response });
  });
  page.on("request", (sent) => {
    if (!["GET", "HEAD", "OPTIONS"].includes(sent.method())) writes++;
  });
  await page.goto(`/reports?document=${id}`);
  const dialog = await openHistory(page, target);
  await expect(
    dialog.getByText("No saved decisions yet", { exact: true }),
  ).toBeVisible();
  expect(reads).toBe(0);
  await dialog
    .getByRole("tab", { name: "Related past reviews", exact: true })
    .click();
  await expect(
    dialog.getByText(response.entries[0].review.note, { exact: true }),
  ).toBeVisible();
  await expect(dialog).toContainText("Oct 19, 2026");
  await expect(
    dialog.getByText("Different customer", { exact: true }),
  ).toBeVisible();
  await expect(
    dialog.getByText("Lab information has changed", { exact: true }),
  ).toBeVisible();
  await expect(dialog.getByText("Needs review", { exact: true })).toBeVisible();
  await expect(dialog.getByText(/Past decisions are references/)).toBeVisible();
  await expect(
    dialog.getByRole("table", { name: "Previous requirement comparison" }),
  ).not.toBeVisible();
  await dialog
    .getByRole("button", { name: "View the previous comparison", exact: true })
    .click();
  const table = dialog.getByRole("table", {
    name: "Previous requirement comparison",
  });
  await expect(table).toContainText("20 ± 2 °C");
  await expect(table).toContainText(
    "No temperature declaration was recorded at the time.",
  );
  await expect(
    dialog.getByRole("button", { name: /Apply|Accept|Save/ }),
  ).toHaveCount(0);
  expect(reads).toBe(1);
  expect(writes).toBe(0);
  const after = (await (
    await request.get(`/api/comparisons/${id}`)
  ).json()) as ComparisonData;
  expect(after.requirements).toEqual(report.requirements);
  expect(after.assessment_reviews).toEqual(report.assessment_reviews);
  expect(after.summary).toEqual(report.summary);
  await dialog.screenshot({
    path: testInfo.outputPath("related-past-review.png"),
    animations: "disabled",
  });
  await dialog
    .getByRole("tab", { name: "This assessment", exact: true })
    .click();
  await expect(
    dialog.getByText("No saved decisions yet", { exact: true }),
  ).toBeVisible();
  await expect(
    dialog.getByText(response.entries[0].review.note, { exact: true }),
  ).toHaveCount(0);
});

test("past-review lookup retries only reads and rejects a different assessment response", async ({
  page,
  request,
}) => {
  const { id, target, response } = await fixture(request);
  let reads = 0,
    writes = 0;
  await page.route(`**/api/comparisons/${id}/related-reviews?*`, (route) => {
    reads++;
    if (reads === 1)
      return route.fulfill({
        status: 503,
        json: { detail: "Past reviews are temporarily unavailable." },
      });
    if (reads === 2)
      return route.fulfill({
        json: { ...response, comparison_id: "foreign-assessment" },
      });
    return route.fulfill({ json: { ...response, entries: [] } });
  });
  page.on("request", (sent) => {
    if (!["GET", "HEAD", "OPTIONS"].includes(sent.method())) writes++;
  });
  await page.goto(`/reports?document=${id}`);
  const dialog = await openHistory(page, target);
  await dialog
    .getByRole("tab", { name: "Related past reviews", exact: true })
    .click();
  await expect(dialog.getByRole("alert")).toContainText(
    "temporarily unavailable",
  );
  await dialog.getByRole("button", { name: "Retry", exact: true }).click();
  await expect(dialog.getByRole("alert")).toContainText(
    "The assessment changed",
  );
  await expect(
    dialog.getByText(response.entries[0].review.note, { exact: true }),
  ).toHaveCount(0);
  await dialog.getByRole("button", { name: "Retry", exact: true }).click();
  await expect(
    dialog.getByText("No related past reviews found", { exact: true }),
  ).toBeVisible();
  expect(reads).toBe(3);
  expect(writes).toBe(0);
});

test("a delayed past review cannot leak into another test's history", async ({
  page,
  request,
}) => {
  const { id, report, target, response } = await fixture(request);
  const other = report.requirements.find(
    (item) => item.requirement_id !== target.requirement_id,
  )!;
  let release!: () => void, started!: () => void;
  const held = new Promise<void>((resolve) => {
    release = resolve;
  });
  const requested = new Promise<void>((resolve) => {
    started = resolve;
  });
  await page.route(
    `**/api/comparisons/${id}/related-reviews?*`,
    async (route) => {
      const selected = new URL(route.request().url()).searchParams.get(
        "requirement_id",
      );
      if (selected === target.requirement_id) {
        started();
        await held;
        await route.fulfill({ json: response }).catch(() => {});
      } else
        await route.fulfill({
          json: {
            ...response,
            requirement_id: other.requirement_id,
            entries: [],
          },
        });
    },
  );
  try {
    await page.goto(`/reports?document=${id}`);
    let dialog = await openHistory(page, target);
    await dialog
      .getByRole("tab", { name: "Related past reviews", exact: true })
      .click();
    await requested;
    await expect(dialog.getByRole("status")).toContainText(
      "Looking for past reviews",
    );
    await dialog
      .getByRole("button", { name: "Close review history", exact: true })
      .click();
    dialog = await openHistory(page, other);
    await expect(
      dialog.getByRole("tab", { name: "This assessment", exact: true }),
    ).toHaveAttribute("aria-selected", "true");
    await dialog
      .getByRole("tab", { name: "Related past reviews", exact: true })
      .click();
    await expect(
      dialog.getByText("No related past reviews found", { exact: true }),
    ).toBeVisible();
    release();
    await expect(
      dialog.getByText(response.entries[0].review.note, { exact: true }),
    ).toHaveCount(0);
  } finally {
    release();
  }
});
