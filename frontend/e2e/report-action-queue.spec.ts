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

async function queueFixture(page: Page, request: APIRequestContext) {
  expect(
    (
      await request.post("/api/workspace/reset", {
        data: { acknowledged: true },
      })
    ).ok(),
  ).toBeTruthy();
  const receipt = await request.post("/api/specifications/worked-example");
  expect(receipt.ok()).toBeTruthy();
  const { document_id: id } = await receipt.json();
  const report = (await (
    await request.get(`/api/comparisons/${id}`)
  ).json()) as ComparisonData;
  const template = report.requirements[0];
  const make = (
    requirement_id: string,
    test_name: string,
    status: ComparisonRequirement["comparison"]["status"],
    methods: string[],
  ): ComparisonRequirement => ({
    ...structuredClone(template),
    requirement_id,
    test_name,
    methods,
    comparison: {
      status,
      checks: [
        {
          kind: "method",
          label: "Test method",
          parameter_id: "test_method",
          stage: null,
          unit: null,
          requested: methods.join(", "),
          declared: status === "not_matched" ? null : methods.join(", "),
          status: status === "not_matched" ? "gap" : "supported",
          reason: "Fixed UI-test method declaration.",
          source_pages: [1],
        },
        {
          kind: "condition",
          label: "Water temperature",
          parameter_id: "water_temperature",
          stage: "testing",
          unit: "°C",
          requested: "60 °C",
          declared:
            status === "needs_information"
              ? null
              : status === "not_matched"
                ? "20–50 °C"
                : "20–70 °C",
          status:
            status === "needs_information"
              ? "unknown"
              : status === "not_matched"
                ? "gap"
                : "supported",
          reason: "Fixed UI-test range declaration.",
          source_pages: [1],
        },
      ],
    },
  });
  const matched = make("queue-match", "Matched control test", "fully_matched", [
    "CONTROL-1",
  ]);
  const gap = make("queue-gap", "Reviewed gap test", "not_matched", ["GAP-42"]);
  const unknown = make(
    "queue-info",
    "Missing lab information test",
    "needs_information",
    ["INFO-9"],
  );
  report.requirements = [matched, gap, unknown];
  report.summary = { fully_matched: 1, not_matched: 1, needs_information: 1 };
  report.assessment_reviews = {
    [gap.requirement_id]: {
      review_id: "queue-review",
      comparison_id: report.comparison_id,
      requirement_id: gap.requirement_id,
      version: 1,
      review_status: "accepted",
      decision: "clarify_customer",
      note: "Customer clarification is still required.",
      actor: "Fixture reviewer",
      created_at: "2026-11-24T14:00:00Z",
      machine_status: "not_matched",
      input_fingerprint: report.report_input_fingerprint!,
    },
  };
  await page.route(`**/api/comparisons/${id}`, (route) =>
    route.fulfill({ json: report }),
  );
  return { id, report, matched, gap, unknown };
}

test("the attention queue keeps reviewed gaps visible and advanced filters preserve the machine assessment", async ({
  page,
  request,
}, testInfo) => {
  const { id, report, matched, gap, unknown } = await queueFixture(
    page,
    request,
  );
  const before = structuredClone(report);
  let writes = 0;
  page.on("request", (sent) => {
    if (!["GET", "HEAD", "OPTIONS"].includes(sent.method())) writes++;
  });
  await page.goto(`/reports?document=${id}`);
  const list = page.getByRole("navigation", { name: "Requested tests" });
  const row = (name: string) => list.getByRole("button", { name, exact: true });
  await expect(row(matched.test_name)).toBeVisible();
  await expect(
    page.getByRole("combobox", { name: "Show", exact: true }),
  ).toHaveCount(0);
  await page
    .getByRole("button", { name: "Needs attention", exact: true })
    .click();
  await expect(row(matched.test_name)).toHaveCount(0);
  await expect(row(gap.test_name)).toBeVisible();
  await expect(row(unknown.test_name)).toBeVisible();
  await expect(
    row(gap.test_name).getByText("Reviewed", { exact: true }),
  ).toBeVisible();
  await row(gap.test_name).click();
  await expect(
    page.getByText("Customer clarification is still required.", {
      exact: true,
    }),
  ).toBeVisible();
  if (testInfo.project.name === "desktop")
    await page.screenshot({
      path: testInfo.outputPath("report-attention-queue.png"),
      animations: "disabled",
    });
  const search = page.getByRole("textbox", { name: "Search requested tests" });
  await search.fill("GAP-42");
  await expect(row(gap.test_name)).toBeVisible();
  await expect(row(unknown.test_name)).toHaveCount(0);
  await search.clear();
  await page
    .getByRole("button", { name: "Filter and sort tests", exact: true })
    .click();
  const filters = page.getByRole("dialog", {
    name: "Filter and sort tests",
    exact: true,
  });
  await filters.getByRole("combobox", { name: "Show", exact: true }).click();
  await page
    .getByRole("option", { name: "Fully matched", exact: true })
    .click();
  await expect(filters).not.toBeVisible();
  await expect(row(matched.test_name)).toBeVisible();
  await expect(row(gap.test_name)).toHaveCount(0);
  await page.reload();
  await expect(row(matched.test_name)).toBeVisible();
  await expect(row(gap.test_name)).toHaveCount(0);
  await page.getByRole("button", { name: "All tests", exact: true }).click();
  await expect(row(gap.test_name)).toBeVisible();
  await expect(row(unknown.test_name)).toBeVisible();
  expect(writes).toBe(0);
  expect(report).toEqual(before);
});

test("an empty attention queue stays explicit while all matched tests remain accessible", async ({
  page,
  request,
}) => {
  const { id, report, matched } = await queueFixture(page, request);
  report.requirements = [matched];
  report.assessment_reviews = {};
  report.summary = { fully_matched: 1 };
  let writes = 0;
  page.on("request", (sent) => {
    if (!["GET", "HEAD", "OPTIONS"].includes(sent.method())) writes++;
  });
  await page.goto(`/reports?document=${id}`);
  const attention = page.getByRole("button", {
    name: "Needs attention",
    exact: true,
  });
  await attention.click();
  await expect(attention).toHaveAttribute("aria-pressed", "true");
  await expect(
    page.getByText("No tests need attention", { exact: true }),
  ).toBeVisible();
  await page.reload();
  await expect(attention).toHaveAttribute("aria-pressed", "true");
  await expect(
    page.getByText("No tests need attention", { exact: true }),
  ).toBeVisible();
  await page.getByRole("button", { name: "All tests", exact: true }).click();
  await expect(
    page
      .getByRole("navigation", { name: "Requested tests" })
      .getByRole("button", { name: matched.test_name, exact: true }),
  ).toBeVisible();
  expect(writes).toBe(0);
});
