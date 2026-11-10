import { expect, test } from "@playwright/test";
import type { LibraryDetail, LibraryDocument } from "../src/libraryTypes";
import type { ComparisonCheck, ComparisonData } from "../src/comparisonTypes";

const base = "http://127.0.0.1:8011";

function record(overrides: Partial<LibraryDocument>): LibraryDocument {
  return {
    document_id: "source-b",
    company_name: "Customer A",
    specification_id: "CSP-301",
    title: "Exterior fascia finish",
    revision: "Issue B",
    specification_date: "2026-11-03",
    family_id: "fascia",
    version: 1,
    original_name: "fascia-b.pdf",
    created_at: "2026-11-03T12:00:00Z",
    updated_at: "2026-11-03T12:00:00Z",
    requirement_count: 1,
    review_count: 0,
    run_id: "run-b",
    page_count: 2,
    ...overrides,
  };
}

test("library keeps source versions separate and links atomic fields to their PDF page", async ({
  page,
}, testInfo) => {
  const documents = [
    record({}),
    record({
      document_id: "source-a",
      revision: "Issue A",
      specification_date: "2026-10-03",
      original_name: "fascia-a.pdf",
      created_at: "2026-10-03T12:00:00Z",
      updated_at: "2026-10-03T12:00:00Z",
      run_id: "run-a",
    }),
    record({
      document_id: "source-other",
      company_name: "Customer B",
      specification_id: "CSP-900",
      title: "Interior trim",
      family_id: "trim",
      revision: "Release 1",
      specification_date: null,
    }),
  ];
  await page.route("**/api/library**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    if (path === "/api/library") {
      await route.fulfill({ json: { documents } });
      return;
    }
    const document = documents.find(
      (item) => path === `/api/library/${item.document_id}`,
    );
    if (!document) {
      await route.fulfill({
        status: 404,
        json: { detail: "Document not found" },
      });
      return;
    }
    const detail: LibraryDetail = {
      document,
      versions: documents.filter(
        (item) => item.family_id === document.family_id,
      ),
      requirements: [
        {
          requirement_id: "req_adhesion",
          test_name: "Cross-cut adhesion",
          methods: ["ISO 60038:2021"],
          claims: [
            {
              parameter_id: "temperature",
              label: "Temperature",
              display_value:
                document.revision === "Issue A" ? "20 ± 2 °C" : "23 ± 2 °C",
              unit: "°C",
              stage: "conditioning",
              source_page: 2,
              raw_text: "Condition the panels at the specified temperature.",
              status: "parsed",
            },
          ],
        },
      ],
    };
    await route.fulfill({ json: detail });
  });

  await page.goto(`${base}/specifications/library`);
  await expect(
    page.getByRole("heading", { name: "Specification library" }),
  ).toBeVisible();
  const listing = page.getByRole("table", {
    name: "Saved customer specifications",
  });
  await expect(listing.getByRole("row")).toHaveCount(3);
  const sourceRow = listing
    .getByRole("row")
    .filter({ hasText: "Exterior fascia finish" });
  await expect(sourceRow.getByRole("cell").nth(2)).toHaveText("Nov 3, 2026");
  await expect(
    listing.getByText("Date not provided", { exact: true }),
  ).toBeVisible();
  await expect(listing.getByText("Issue A", { exact: true })).toHaveCount(0);

  await listing.getByRole("button").first().click();
  await page.getByRole("button", { name: /Cross-cut adhesion/ }).click();
  await expect(
    page.getByRole("heading", { name: "Cross-cut adhesion" }),
  ).toBeVisible();
  await expect(
    page.getByRole("table", { name: "Saved requirement records" }),
  ).not.toBeVisible();
  await page.screenshot({
    path: testInfo.outputPath("library-test-summary.png"),
    animations: "disabled",
    fullPage: true,
  });
  await page
    .getByRole("button", { name: "View detailed requirements" })
    .click();
  let claims = page.getByRole("table", {
    name: "Saved requirement records",
  });
  await expect(claims.getByText("23 ± 2", { exact: true })).toBeVisible();
  await expect(claims.getByText("Conditioning", { exact: true })).toBeVisible();
  await expect(claims.getByRole("link", { name: "Page 2" })).toHaveAttribute(
    "href",
    "/specifications?document=source-b&requirement=req_adhesion&page=2",
  );

  await page.getByRole("combobox", { name: "Specification date" }).click();
  await page.getByRole("option", { name: "Oct 3, 2026" }).click();
  await page.getByRole("button", { name: /Cross-cut adhesion/ }).click();
  await expect(
    page.getByRole("table", { name: "Saved requirement records" }),
  ).not.toBeVisible();
  await page
    .getByRole("button", { name: "View detailed requirements" })
    .click();
  claims = page.getByRole("table", {
    name: "Saved requirement records",
  });
  await expect(claims.getByText("20 ± 2", { exact: true })).toBeVisible();

  await page
    .getByRole("button", { name: "All specifications", exact: true })
    .click();
  await page.getByRole("combobox", { name: "Customer company" }).click();
  await page.getByRole("option", { name: "Customer B", exact: true }).click();
  await expect(listing.getByText("Customer A", { exact: true })).toHaveCount(0);
  await listing.getByRole("button").first().click();
  await expect(
    page.getByRole("heading", { name: "Interior trim" }),
  ).toBeVisible();
});

test("library edits a specification date while retaining its original revision", async ({
  page,
}) => {
  let document = record({ specification_date: null });
  let submitted: Record<string, unknown> | null = null;
  const detail = (): LibraryDetail => ({
    document,
    versions: [document],
    requirements: [],
  });
  await page.route("**/api/library**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    if (path === "/api/library") {
      await route.fulfill({ json: { documents: [document] } });
    } else if (path.endsWith("/metadata")) {
      submitted = route.request().postDataJSON();
      document = {
        ...document,
        specification_date: String(submitted!.specification_date),
        version: document.version + 1,
      };
      await route.fulfill({ json: detail() });
    } else {
      await route.fulfill({ json: detail() });
    }
  });
  await page.goto(`${base}/specifications/library?document=source-b`);
  await page.getByRole("button", { name: "Edit details" }).click();
  const dialog = page.getByRole("dialog");
  await expect(dialog.getByLabel("Specification date")).toHaveValue("");
  await expect(
    dialog.getByText("Original source revision: Issue B"),
  ).toBeVisible();
  await dialog.getByLabel("Specification date").fill("2026-11-08");
  await dialog.getByRole("button", { name: "Save details" }).click();
  await expect(dialog).not.toBeVisible();
  expect(submitted?.["specification_date"]).toBe("2026-11-08");
  expect(submitted?.["revision"]).toBe("Issue B");
  await expect(page.getByText(/Nov 8, 2026/).first()).toBeVisible();
});

for (const scenario of [
  {
    name: "source question",
    parameter: "source_review",
    kind: "source",
    status: "unknown",
    capability: "cap_gloss",
    label: "Review extracted details",
    destination: "/specifications?document=source-b&requirement=req_gloss",
  },
  {
    name: "unclear method identity",
    parameter: "test_method",
    kind: "method",
    status: "unknown",
    capability: null,
    label: "Review test and method",
    destination: "/specifications?document=source-b&requirement=req_gloss",
  },
  {
    name: "uninterpreted source clause",
    parameter: null,
    kind: "condition",
    status: "unknown",
    capability: "cap_gloss",
    label: "Review extracted details",
    destination: "/specifications?document=source-b&requirement=req_gloss",
  },
  {
    name: "missing lab declaration",
    parameter: "illuminant",
    kind: "condition",
    status: "unknown",
    capability: "cap_gloss",
    label: "Update lab information",
    destination: "/my-lab?capability=cap_gloss",
  },
  {
    name: "method absent from lab",
    parameter: "test_method",
    kind: "method",
    status: "gap",
    capability: null,
    label: "Add a lab capability",
    destination: "/my-lab",
  },
] as const) {
  test(`comparison directs ${scenario.name} to the relevant action`, async ({
    page,
  }) => {
    const document = record({});
    await page.route("**/api/workspace", (route) =>
      route.fulfill({
        json: {
          lab: { id: "lab", name: "Example lab", version: 1, capabilities: [] },
          catalog: { tests: [], methods: [] },
          documents: [
            {
              document_id: document.document_id,
              original_name: document.original_name,
              latest_run_id: document.run_id,
            },
          ],
          providers: [],
        },
      }),
    );
    await page.route("**/api/library", (route) =>
      route.fulfill({ json: { documents: [document] } }),
    );
    const check: ComparisonCheck = {
      label: "Requirement to check",
      parameter_id: scenario.parameter,
      kind: scenario.kind,
      stage: "measurement",
      unit: null,
      requested: "Customer requirement",
      declared: null,
      status: scenario.status,
      reason: "Fixture review reason",
      source_pages: [2],
    };
    const comparison: ComparisonData = {
      comparison_id: "comparison",
      created_at: "2026-11-03T12:00:00Z",
      document,
      lab_name: "Example lab",
      lab_version: 1,
      summary: { needs_information: 1 },
      requirements: [
        {
          requirement_id: "req_gloss",
          test_name: "Specular gloss",
          methods: ["ASTM D1627-16"],
          capability_id: scenario.capability,
          source_review_state:
            scenario.kind === "source" ? "needs_attention" : "ready",
          comparison: { status: "needs_information", checks: [check] },
        },
      ],
    };
    await page.route("**/api/comparisons/source-b", (route) =>
      route.fulfill({ json: comparison }),
    );
    await page.goto(`${base}/reports?document=source-b`);
    await expect(
      page.getByRole("link", { name: scenario.label, exact: true }),
    ).toHaveAttribute("href", scenario.destination);
    if (!scenario.capability || scenario.status !== "unknown")
      await expect(
        page.getByRole("link", { name: "Update lab information", exact: true }),
      ).toHaveCount(0);
  });
}
