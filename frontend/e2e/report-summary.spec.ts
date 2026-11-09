import { expect, test } from "@playwright/test";
import type { ComparisonCheck, ComparisonData } from "../src/comparisonTypes";
import type { LibraryDocument } from "../src/libraryTypes";

const base = "http://127.0.0.1:8011";

test("report keeps parameter details optional while switching and searching tests", async ({
  page,
}, testInfo) => {
  const source: LibraryDocument = {
    document_id: "summary-source",
    company_name: "Customer A",
    specification_id: "CSP-301",
    title: "Coating requirements",
    revision: "Rev B",
    family_id: "coating",
    version: 1,
    original_name: "coating.pdf",
    created_at: "2026-11-09T12:00:00Z",
    updated_at: "2026-11-09T12:00:00Z",
    requirement_count: 2,
    review_count: 0,
    run_id: "run-b",
    page_count: 2,
  };
  await page.route("**/api/workspace", (route) =>
    route.fulfill({
      json: {
        lab: { id: "lab", name: "Example lab", version: 1, capabilities: [] },
        catalog: { tests: [], methods: [] },
        providers: [],
        documents: [
          {
            document_id: source.document_id,
            original_name: source.original_name,
            latest_run_id: source.run_id,
          },
        ],
      },
    }),
  );
  await page.route("**/api/library", (route) =>
    route.fulfill({ json: { documents: [source] } }),
  );
  const temperature: ComparisonCheck = {
    label: "Water temperature",
    parameter_id: "temperature",
    kind: "condition",
    stage: "testing",
    unit: "°C",
    requested: "60 °C",
    declared: "20–50 °C",
    status: "gap",
    reason: "The water temperature is outside the lab range.",
    source_pages: [2],
  };
  const comparison: ComparisonData = {
    comparison_id: "saved-summary",
    created_at: source.created_at,
    document: source,
    lab_name: "Example lab",
    lab_version: 1,
    summary: { partially_matched: 1, fully_matched: 1 },
    requirements: [
      {
        requirement_id: "req_water",
        test_name: "Water jet adhesion",
        methods: ["ISO 16925"],
        capability_id: "cap_water",
        source_review_state: "ready",
        comparison: {
          status: "partially_matched",
          checks: [
            {
              ...temperature,
              label: "Test / method",
              parameter_id: "test_method",
              kind: "method",
              requested: "Water jet adhesion",
              declared: "ISO 16925",
              status: "supported",
              reason: "Matching method is listed.",
            },
            temperature,
            {
              ...temperature,
              label: "Duration",
              parameter_id: "duration",
              requested: "60 s",
              declared: null,
              unit: "s",
              status: "unknown",
              reason: "The lab has not recorded an exposure duration.",
            },
            {
              ...temperature,
              label: "Rating",
              parameter_id: "rating",
              kind: "criterion",
              requested: "≤ 1",
              declared: "0–5",
              status: "supported",
              reason: "The lab reports this scale.",
            },
          ],
        },
      },
      {
        requirement_id: "req_gloss",
        test_name: "Specular gloss",
        methods: ["ASTM D1627-16"],
        capability_id: "cap_gloss",
        source_review_state: "ready",
        comparison: {
          status: "fully_matched",
          checks: [
            {
              ...temperature,
              label: "Test / method",
              parameter_id: "test_method",
              kind: "method",
              requested: "Specular gloss",
              declared: "ASTM D1627-16",
              status: "supported",
              reason: "Matching method is listed.",
            },
          ],
        },
      },
    ],
  };
  await page.route("**/api/comparisons/summary-source", (route) =>
    route.fulfill({ json: comparison }),
  );
  await page.goto(`${base}/reports?document=summary-source`);
  const list = page.getByRole("navigation", { name: "Requested tests" });
  await expect(
    list.getByRole("button", { name: "Water jet adhesion", exact: true }),
  ).toHaveAttribute("aria-current", "true");
  await expect(
    page.getByRole("heading", { name: "Water jet adhesion", exact: true }),
  ).toBeVisible();
  const summary = page.getByRole("table", {
    name: "Test capability comparison",
    exact: true,
  });
  await expect(summary.getByRole("row")).toHaveCount(4);
  await expect(summary.getByText("Conditions", { exact: true })).toBeVisible();
  await expect(summary.getByText("60 °C", { exact: false })).toBeVisible();
  const conditions = summary
    .getByRole("row")
    .filter({ has: page.getByText("Conditions", { exact: true }) });
  await expect(
    conditions
      .getByRole("cell")
      .last()
      .getByText("Duration:", { exact: false }),
  ).toBeVisible();
  await expect(
    conditions
      .getByRole("cell")
      .last()
      .getByText("Not recorded", { exact: false }),
  ).toBeVisible();
  const details = page.getByRole("table", {
    name: "Water jet adhesion requirement details",
  });
  await expect(details).not.toBeVisible();
  await page.getByRole("button", { name: /View detailed comparison/ }).click();
  await expect(
    details.getByText("The water temperature is outside the lab range."),
  ).toBeVisible();
  await list
    .getByRole("button", { name: "Specular gloss", exact: true })
    .click();
  await expect(
    page.getByRole("heading", { name: "Specular gloss", exact: true }),
  ).toBeVisible();
  await expect(
    page.getByRole("button", { name: /View detailed comparison/ }),
  ).toHaveAttribute("aria-expanded", "false");
  await page
    .getByRole("textbox", { name: "Search requested tests" })
    .fill("16925");
  await expect(
    list.getByRole("button", { name: "Specular gloss", exact: true }),
  ).toHaveCount(0);
  await expect(
    page.getByRole("heading", { name: "Water jet adhesion", exact: true }),
  ).toBeVisible();
  await page
    .getByRole("textbox", { name: "Search requested tests" })
    .fill("not a recorded method");
  await expect(
    page.getByRole("heading", { name: "Water jet adhesion", exact: true }),
  ).toHaveCount(0);
  await page
    .getByRole("button", { name: "Show all tests", exact: true })
    .click();
  await expect(list.getByRole("button")).toHaveCount(2);
  await list
    .getByRole("button", { name: "Water jet adhesion", exact: true })
    .click();
  const widths = await page.evaluate(() => ({
    content: document.documentElement.scrollWidth,
    viewport: document.documentElement.clientWidth,
  }));
  expect(widths.content).toBeLessThanOrEqual(widths.viewport + 1);
  await page.screenshot({
    path: testInfo.outputPath("interactive-report-summary.png"),
    animations: "disabled",
    fullPage: true,
  });
});
