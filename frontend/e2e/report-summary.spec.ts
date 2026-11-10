import { openReportScope, reloadSavedReport } from "./reportScope";
import { expect, test, type Page } from "@playwright/test";
import type { ComparisonCheck, ComparisonData } from "../src/comparisonTypes";
import type { LibraryDocument } from "../src/libraryTypes";

const base = "http://127.0.0.1:8011";

async function mockReport(
  page: Page,
  extraConditions = 0,
  showCandidate = false,
) {
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
    source_run_id: "run-b",
    report_input_fingerprint: "summary-inputs",
    report_workspace_version: 1,
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
  for (let index = 0; index < extraConditions; index += 1) {
    comparison.requirements[0].comparison.checks.push({
      ...temperature,
      label: `Additional condition ${index + 1}`,
      parameter_id: `custom_${index + 1}`,
      requested: `${index + 1} units`,
      declared: `0–${index + 2} units`,
      status: "supported",
      reason: "The requested value is within the recorded lab range.",
    });
  }
  if (showCandidate) {
    comparison.requirements[0].comparison.checks[2].candidate_lab_entries = [
      {
        claim_id: "lab_duration",
        label: "Conditioning duration",
        value: "60 s",
        stage: "conditioning",
        context: "Dry nitrogen",
        confirmed: false,
      },
    ];
  }
  await page.route("**/api/specifications/summary-source/matching", (route) =>
    route.fulfill({
      json: {
        source_run_id: source.run_id,
        report_input_fingerprint: "summary-inputs",
        input_fingerprint: "summary-matching-inputs",
        current_run_id: null,
        runs: [],
      },
    }),
  );
  await page.route("**/api/comparisons/summary-source", (route) =>
    route.fulfill({ json: comparison }),
  );
  return comparison;
}

async function selectFilter(page: Page, label: string) {
  await page.getByRole("combobox", { name: "Show", exact: true }).click();
  await page.getByRole("option", { name: label, exact: true }).click();
}

test("document source coverage stays visible while filtering extracted report items", async ({
  page,
}) => {
  const comparison = await mockReport(page);
  const issue =
    'Literal retention was not verified for "mild steel witnesses" and "Release requires Materials Engineering approval"; check existing scope and instructions.';
  comparison.source_coverage_issues = [issue];
  let modelCalls = 0;
  page.on("request", (request) => {
    if (
      request.method() === "POST" &&
      /\/(?:analyze|matching)$/.test(request.url())
    )
      modelCalls += 1;
  });
  await page.goto(`${base}/reports?document=summary-source`);
  const title = page.getByText("Check extracted requirements", {
    exact: true,
  });
  const warning = page.getByRole("alert").filter({ has: title });
  await expect(title).toHaveCount(1);
  await expect(warning).toContainText("1 source check to review");
  await expect(
    warning.getByRole("link", { name: "Review specification", exact: true }),
  ).toHaveAttribute("href", "/specifications?document=summary-source");
  await expect(warning.getByText(issue, { exact: true })).not.toBeVisible();
  await warning
    .getByRole("button", { name: "Review notes", exact: true })
    .click();
  await expect(warning.getByText(issue, { exact: true })).toBeVisible();
  await selectFilter(page, "Fully matched");
  await expect(title).toHaveCount(1);
  await expect(title).toBeVisible();
  await expect(warning.getByText(issue, { exact: true })).toBeVisible();
  await expect(
    page
      .getByRole("navigation", { name: "Requested tests" })
      .getByRole("button", { name: "Specular gloss", exact: true }),
  ).toBeVisible();
  await expect(
    warning.getByRole("link", { name: "Review specification", exact: true }),
  ).toHaveAttribute("href", "/specifications?document=summary-source");
  expect(modelCalls).toBe(0);
});

test("unconfirmed lab candidates are visible without being reported as matches", async ({
  page,
}) => {
  await mockReport(page, 0, true);
  await page.goto(`${base}/reports?document=summary-source`);
  await page.getByRole("button", { name: "Conditions", exact: true }).click();
  const duration = page
    .getByRole("rowgroup", { name: "Water jet adhesion conditions comparison" })
    .getByRole("row")
    .filter({ has: page.getByText("Duration", { exact: true }) });
  await expect(
    duration.getByText("Lab information to review", { exact: true }),
  ).toBeVisible();
  await expect(
    duration.getByText("Dry nitrogen", { exact: true }),
  ).toBeVisible();
  await expect(
    duration.getByText("Needs information", { exact: true }),
  ).toBeVisible();
  await expect(duration.getByText("Not recorded", { exact: true })).toHaveCount(
    0,
  );
  await expect(duration.getByText("Match", { exact: true })).toHaveCount(0);
});

test("report expands conditions and criteria in their own groups", async ({
  page,
}, testInfo) => {
  await mockReport(page);
  await page.goto(`${base}/reports?document=summary-source`);
  await expect(
    page.getByText("Check extracted requirements", { exact: true }),
  ).toHaveCount(0);
  const list = page.getByRole("navigation", { name: "Requested tests" });
  const conditionsButton = page.getByRole("button", {
    name: "Conditions",
    exact: true,
  });
  const criteriaButton = page.getByRole("button", {
    name: "Acceptance criteria / reporting",
    exact: true,
  });
  await expect(
    list.getByRole("button", { name: "Water jet adhesion", exact: true }),
  ).toHaveAttribute("aria-current", "true");
  await expect(conditionsButton).toHaveAttribute("aria-expanded", "false");
  await expect(criteriaButton).toHaveAttribute("aria-expanded", "false");
  const detail = page.getByRole("region", { name: "Selected test details" });
  const checks = detail.getByRole("group", { name: "Comparison checks" });
  await expect(checks.getByText("2 matched", { exact: true })).toBeVisible();
  await expect(checks.getByText("1 gap", { exact: true })).toBeVisible();
  await expect(checks.getByText("1 to clarify", { exact: true })).toBeVisible();
  await expect(
    detail.getByRole("button", { name: "Method", exact: true }),
  ).toHaveCount(0);
  await expect(detail.getByText("ISO 16925", { exact: true })).toHaveCount(2);
  await expect(page.getByText("60 °C", { exact: true })).not.toBeVisible();
  await expect(
    page.getByRole("button", { name: /View detailed comparison/ }),
  ).toHaveCount(0);

  await conditionsButton.click();
  const conditions = page.getByRole("rowgroup", {
    name: "Water jet adhesion conditions comparison",
    exact: true,
  });
  await expect(conditions).toBeVisible();
  await expect(conditions.getByText("60 °C", { exact: true })).toBeVisible();
  await expect(conditions.getByText("Duration", { exact: true })).toBeVisible();
  await expect(
    conditions.getByText("Not recorded", { exact: true }),
  ).toBeVisible();
  const temperature = conditions.getByRole("row").filter({
    has: page.getByText("Water temperature", { exact: true }),
  });
  const duration = conditions.getByRole("row").filter({
    has: page.getByText("Duration", { exact: true }),
  });
  await expect(temperature.getByText("Gap", { exact: true })).toBeVisible();
  await expect(
    duration.getByText("Needs information", { exact: true }),
  ).toBeVisible();
  await temperature
    .getByRole("button", { name: "Reason for Water temperature", exact: true })
    .click();
  await expect(
    page
      .getByRole("dialog", {
        name: "Reason for Water temperature",
        exact: true,
      })
      .getByText("The water temperature is outside the lab range.", {
        exact: true,
      }),
  ).toBeVisible();
  await page.keyboard.press("Escape");
  await duration
    .getByRole("button", { name: "Reason for Duration", exact: true })
    .click();
  await expect(
    page
      .getByRole("dialog", { name: "Reason for Duration", exact: true })
      .getByText("The lab has not recorded an exposure duration.", {
        exact: true,
      }),
  ).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(page.getByText("Rating", { exact: true })).not.toBeVisible();
  await criteriaButton.click();
  await expect(page.getByText("Rating", { exact: true })).toBeVisible();
  await expect(conditionsButton).toHaveAttribute("aria-expanded", "true");
  await expect(criteriaButton).toHaveAttribute("aria-expanded", "true");

  await list
    .getByRole("button", { name: "Specular gloss", exact: true })
    .click();
  await expect(
    page.getByRole("heading", { name: "Specular gloss", exact: true }),
  ).toBeVisible();
  await expect(conditionsButton).toHaveAttribute("aria-expanded", "false");
  const search = page.getByRole("textbox", { name: "Search requested tests" });
  await search.fill("16925");
  await expect(
    list.getByRole("button", { name: "Specular gloss", exact: true }),
  ).toHaveCount(0);
  await expect(
    page.getByRole("heading", { name: "Water jet adhesion", exact: true }),
  ).toBeVisible();
  // Clearing a filter must keep the actual visible selection, not resurrect a
  // stale test ID from before the filter was applied.
  await search.fill("");
  await expect(
    list.getByRole("button", { name: "Water jet adhesion", exact: true }),
  ).toHaveAttribute("aria-current", "true");
  await search.fill("not a recorded method");
  await expect(
    page.getByRole("heading", { name: "Water jet adhesion", exact: true }),
  ).toHaveCount(0);
  await page
    .getByRole("button", { name: "Show all tests", exact: true })
    .click();
  await expect(list.getByRole("button")).toHaveCount(2);
  await expect(search).toBeFocused();
  const widths = await page.evaluate(() => ({
    content: document.documentElement.scrollWidth,
    viewport: document.documentElement.clientWidth,
  }));
  expect(widths.content).toBeLessThanOrEqual(widths.viewport + 1);
  // Capture sticky elements from their natural page origin.
  await page.evaluate(() => window.scrollTo(0, 0));
  await page.screenshot({
    path: testInfo.outputPath("interactive-report-summary.png"),
    animations: "disabled",
    fullPage: true,
  });
});

for (const height of [900, 760]) {
  test(`report comparison remains primary at 1440×${height}`, async ({
    page,
  }, testInfo) => {
    test.skip(
      testInfo.project.name !== "desktop",
      "These two desktop heights cover the client's laptop layout.",
    );
    await page.setViewportSize({ width: 1440, height });
    await mockReport(page);
    await page.goto(`${base}/reports?document=summary-source`);
    const list = page.getByRole("navigation", { name: "Requested tests" });
    const pane = page.getByTestId("report-detail-pane");
    const comparison = pane.getByRole("table", {
      name: "Test capability comparison",
      exact: true,
    });
    await expect(comparison).toBeVisible();
    for (const column of [
      "Comparison",
      "Customer specification",
      "Lab capability",
      "Result",
    ]) {
      await expect(
        comparison.getByRole("columnheader", { name: column, exact: true }),
      ).toBeVisible();
    }
    const geometry = {
      table: await comparison.boundingBox(),
      pane: await pane.boundingBox(),
      list: await list.boundingBox(),
    };
    expect(geometry.table).not.toBeNull();
    expect(geometry.pane).not.toBeNull();
    expect(geometry.list).not.toBeNull();
    expect(geometry.table!.y).toBeLessThan(height / 2);
    expect(geometry.pane!.width).toBeGreaterThan(geometry.list!.width * 2);
    expect(geometry.table!.width).toBeGreaterThan(1440 * 0.55);
    expect(geometry.pane!.y).toBeLessThan(height * 0.3);
    await expect(page.getByRole("button", { name: /^Filter:/ })).toHaveCount(0);
    await expect(
      page.getByRole("combobox", { name: "Show", exact: true }),
    ).toBeVisible();
    await expect(
      page.getByRole("combobox", { name: "Order", exact: true }),
    ).toBeVisible();
    await page.screenshot({
      path: testInfo.outputPath(`report-${height}-collapsed.png`),
      animations: "disabled",
    });

    await page.getByRole("button", { name: "Conditions", exact: true }).click();
    await page
      .getByRole("button", {
        name: "Acceptance criteria / reporting",
        exact: true,
      })
      .click();
    const criteria = pane.getByRole("rowgroup", {
      name: "Water jet adhesion acceptance criteria / reporting comparison",
      exact: true,
    });
    // A single shared table keeps expanded fields under the same headings.
    await expect(comparison.getByRole("columnheader")).toHaveCount(4);
    const methodRow = comparison
      .getByRole("row")
      .filter({ has: page.getByText("Method", { exact: true }) })
      .first();
    const methodCells = methodRow.getByRole("cell");
    const ratingCells = criteria.getByRole("row").last().getByRole("cell");
    for (const column of [1, 2, 3]) {
      const methodBox = await methodCells.nth(column).boundingBox();
      const ratingBox = await ratingCells.nth(column).boundingBox();
      expect(Math.abs((methodBox?.x ?? 0) - (ratingBox?.x ?? 0))).toBeLessThan(
        2,
      );
    }
    const lastRow = criteria.getByRole("row").last();
    await lastRow.scrollIntoViewIfNeeded();
    await expect(lastRow.getByText("Rating", { exact: true })).toBeVisible();
    await expect(lastRow.getByText("Match", { exact: true })).toBeVisible();
    // A fixed action footer previously covered expanded comparison rows. The
    // row must be hit-testable, and the scope action must follow the table.
    expect(
      await lastRow.evaluate((row) => {
        const box = row.getBoundingClientRect();
        return row.contains(
          document.elementFromPoint(
            box.x + box.width / 2,
            box.y + box.height / 2,
          ),
        );
      }),
    ).toBe(true);
    await openReportScope(page);
    const remove = pane.getByRole("button", {
      name: "Remove from this report",
      exact: true,
    });
    const bottom = await criteria.boundingBox();
    const action = await remove.boundingBox();
    expect(bottom).not.toBeNull();
    expect(action).not.toBeNull();
    expect(action!.y).toBeGreaterThanOrEqual(bottom!.y + bottom!.height);
    const widths = await page.evaluate(() => ({
      content: document.documentElement.scrollWidth,
      viewport: document.documentElement.clientWidth,
    }));
    expect(widths.content).toBeLessThanOrEqual(widths.viewport + 1);
    await page.screenshot({
      path: testInfo.outputPath(`report-${height}-expanded.png`),
      animations: "disabled",
    });
  });
}

test("result filters preserve the visible selected test when cleared or refreshed", async ({
  page,
}) => {
  await mockReport(page);
  await page.goto(`${base}/reports?document=summary-source`);
  const list = page.getByRole("navigation", { name: "Requested tests" });
  await expect(
    list.getByRole("button", { name: "Water jet adhesion", exact: true }),
  ).toHaveAttribute("aria-current", "true");
  await selectFilter(page, "Fully matched");
  const gloss = list.getByRole("button", {
    name: "Specular gloss",
    exact: true,
  });
  await expect(gloss).toHaveAttribute("aria-current", "true");
  await expect(list.getByRole("button")).toHaveCount(1);
  await selectFilter(page, "All tests");
  await expect(gloss).toHaveAttribute("aria-current", "true");
  await selectFilter(page, "Fully matched");
  await reloadSavedReport(page);
  await expect(
    page.getByRole("combobox", { name: "Show", exact: true }),
  ).toContainText("Fully matched");
  await expect(gloss).toHaveAttribute("aria-current", "true");
  await expect(list.getByRole("button")).toHaveCount(1);
});

test("desktop navigation collapse preserves the report selection and expanded category", async ({
  page,
}, testInfo) => {
  test.skip(
    testInfo.project.name !== "desktop",
    "The narrow layout uses the mobile navigation drawer.",
  );
  await mockReport(page);
  await page.goto(`${base}/reports?document=summary-source`);
  const conditions = page.getByRole("button", {
    name: "Conditions",
    exact: true,
  });
  await conditions.click();
  await page
    .getByRole("textbox", { name: "Search requested tests" })
    .fill("16925");
  const before = await page.evaluate(() => window.scrollY);
  await page
    .getByRole("button", { name: "Collapse navigation", exact: true })
    .click();
  await expect(
    page.getByRole("button", { name: "Expand navigation", exact: true }),
  ).toBeVisible();
  await expect(conditions).toHaveAttribute("aria-expanded", "true");
  await expect(
    page.getByRole("textbox", { name: "Search requested tests" }),
  ).toHaveValue("16925");
  expect(await page.evaluate(() => window.scrollY)).toBe(before);
  await page.reload();
  await expect(
    page.getByRole("button", { name: "Expand navigation", exact: true }),
  ).toBeVisible();
  await page
    .getByRole("button", { name: "Expand navigation", exact: true })
    .click();
  await expect(
    page.getByRole("button", { name: "Collapse navigation", exact: true }),
  ).toBeVisible();
});

test("refresh and result filtering preserve the review pane scroll position", async ({
  page,
}, testInfo) => {
  test.skip(
    testInfo.project.name !== "desktop",
    "Desktop review panes scroll independently of the page.",
  );
  await mockReport(page, 35);
  await page.goto(`${base}/reports?document=summary-source`);
  const conditions = page.getByRole("button", {
    name: "Conditions",
    exact: true,
  });
  await conditions.click();
  const pane = page.getByTestId("report-detail-pane");
  await expect(pane).toBeVisible();
  await expect
    .poll(() =>
      pane.evaluate((element) => element.scrollHeight > element.clientHeight),
    )
    .toBe(true);
  await pane.evaluate((element) => {
    element.scrollTop = 260;
  });
  const before = await pane.evaluate((element) => ({
    top: element.scrollTop,
    viewportTop: element.getBoundingClientRect().top,
    pageY: window.scrollY,
  }));
  await reloadSavedReport(page);
  await expect(conditions).toHaveAttribute("aria-expanded", "true");
  await expect
    .poll(() => pane.evaluate((element) => element.scrollTop))
    .toBe(before.top);
  await selectFilter(page, "Partially matched");
  await expect
    .poll(() => pane.evaluate((element) => element.scrollTop))
    .toBe(before.top);
  const after = await pane.evaluate((element) => ({
    viewportTop: element.getBoundingClientRect().top,
    pageY: window.scrollY,
  }));
  expect(after.pageY).toBe(before.pageY);
  expect(Math.abs(after.viewportTop - before.viewportTop)).toBeLessThanOrEqual(
    2,
  );
});

for (const viewport of [
  { width: 1440, height: 1000 },
  { width: 1280, height: 800 },
  { width: 390, height: 844 },
]) {
  test(`report action hierarchy stays clear at ${viewport.width}x${viewport.height}`, async ({
    page,
  }, info) => {
    test.skip(
      viewport.width < 600 !== (info.project.name === "narrow"),
      "Use the relevant viewport project.",
    );
    await page.setViewportSize(viewport);
    await mockReport(page);
    await page.goto(`${base}/reports?document=summary-source`);
    const list = page.getByRole("navigation", {
      name: "Requested tests",
      exact: true,
    });
    const pane = page.getByTestId("report-detail-pane");
    const tools = pane.getByRole("region", {
      name: "Source and lab details",
      exact: true,
    });
    const review = pane.getByRole("region", {
      name: "Your assessment review",
      exact: true,
    });
    await expect(
      tools.getByRole("link", { name: "Review source PDF", exact: true }),
    ).toHaveAttribute("href", /requirement=req_water/);
    await expect(
      tools.getByRole("link", { name: "Update lab information", exact: true }),
    ).toHaveAttribute("href", "/my-lab?capability=cap_water");
    await expect(
      review.getByRole("button", { name: "Accept assessment", exact: true }),
    ).toHaveClass(/MuiButton-contained/);
    await expect(
      review.getByRole("button", { name: "Edit decision", exact: true }),
    ).toHaveClass(/MuiButton-outlined/);
    const scope = pane.getByRole("button", {
      name: "Report scope",
      exact: true,
    });
    await expect(scope).toHaveAttribute("aria-expanded", "false");
    await expect(
      pane.getByRole("button", {
        name: "Remove from this report",
        exact: true,
      }),
    ).toHaveCount(0);
    if (viewport.width >= 1200) {
      const rail = await list.boundingBox();
      const detail = await pane.boundingBox();
      expect(rail!.width).toBeGreaterThanOrEqual(300);
      expect(rail!.width).toBeLessThanOrEqual(360);
      expect(detail!.width).toBeGreaterThan(rail!.width * 1.8);
      expect((await tools.boundingBox())!.y).toBeLessThan(
        (await review.boundingBox())!.y,
      );
    }
    await page.screenshot({
      path: info.outputPath(`report-actions-${viewport.width}.png`),
      fullPage: true,
      animations: "disabled",
    });
    const conditions = pane.getByRole("button", {
      name: "Conditions",
      exact: true,
    });
    const criteria = pane.getByRole("button", {
      name: "Acceptance criteria / reporting",
      exact: true,
    });
    await conditions.click();
    await criteria.click();
    await openReportScope(page);
    await expect(conditions).toHaveAttribute("aria-expanded", "true");
    await expect(criteria).toHaveAttribute("aria-expanded", "true");
    await expect(
      pane.getByRole("button", {
        name: "Remove from this report",
        exact: true,
      }),
    ).toHaveClass(/MuiButton-textError/);
    await expect(
      list.getByRole("button", { name: "Water jet adhesion", exact: true }),
    ).toHaveAttribute("aria-current", "true");
    expect(
      await page.evaluate(
        () =>
          document.documentElement.scrollWidth <=
          document.documentElement.clientWidth + 1,
      ),
    ).toBeTruthy();
  });
}
