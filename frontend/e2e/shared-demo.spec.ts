import { expect, test, type Page } from "@playwright/test";
import type { ReportData, SpecificationData } from "../src/types";

const base = "http://127.0.0.1:8011";

async function showGroup(page: Page, group: string) {
  await page.getByRole("combobox", { name: "Show" }).click();
  await page.getByRole("option", { name: group, exact: true }).click();
}

async function expectNoHorizontalOverflow(page: Page) {
  const widths = await page.evaluate(() => ({
    viewport: window.document.documentElement.clientWidth,
    content: window.document.documentElement.scrollWidth,
  }));
  expect(widths.content).toBeLessThanOrEqual(widths.viewport + 1);
}

test("shared PDF walkthrough keeps source review, declarations and gap results separate", async ({
  page,
  request,
}, testInfo) => {
  // This is a hand-authored subset of the shared PDF, not a full model extraction.
  const documents = await request.get(`${base}/api/specifications`);
  expect(documents.ok()).toBeTruthy();
  const listed: { document_id: string; original_name: string }[] =
    await documents.json();
  const document = listed.find(
    (item) => item.original_name === "MS-PNT-410_demo_spec.pdf",
  );
  expect(document).toBeTruthy();
  const id = document!.document_id;
  const sourceUrl = `${base}/api/specifications/${id}`;
  const originalResponse = await request.get(sourceUrl);
  expect(originalResponse.ok()).toBeTruthy();
  const original: SpecificationData = await originalResponse.json();
  expect(original.run?.prompt_version).toBe(
    "hand_authored_partial_shared_demo_fixture",
  );
  // Both viewport projects share this disposable server. Start this scenario
  // with an explicit pending review instead of inheriting the other project's
  // confirmation and then waiting for a button that correctly disappeared.
  const prepared = await request.post(`${sourceUrl}/review`, {
    data: {
      run_id: original.run!.run_id,
      version: original.review!.version,
      facts_version: original.facts_version ?? 0,
      requirement_ids: ["req_demo_pencil", "req_demo_crosscut"],
      decision: "needs_review",
      note: "Begin this offline browser scenario with pending source checks.",
      acknowledged: true,
    },
  });
  expect(prepared.ok()).toBeTruthy();

  await page.goto(
    `${base}/specifications?document=${id}&requirement=req_demo_pencil`,
  );
  await page.getByRole("button", { name: "All", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "Pencil hardness", exact: true }),
  ).toBeVisible();
  await expect(
    page.getByRole("table", { name: "Test details", exact: true }),
  ).toBeVisible();
  if (testInfo.project.name === "narrow") {
    await page.getByRole("button", { name: /Source PDF preview/ }).click();
  }
  await expect(page.getByRole("img", { name: /PDF page 6/ })).toBeVisible();
  await expectNoHorizontalOverflow(page);
  await page.screenshot({
    path: testInfo.outputPath("shared-demo-source-review.png"),
    fullPage: true,
  });
  await page.getByRole("button", { name: "Confirm & next" }).click();
  await expect
    .poll(async () => {
      const response = await request.get(sourceUrl);
      const current: SpecificationData = await response.json();
      return current.review?.decisions.req_demo_pencil?.status;
    })
    .toBe("confirmed");

  await expect(
    page.getByRole("heading", { name: "Cross-cut adhesion", exact: true }),
  ).toBeVisible();
  await page.getByRole("button", { name: "Source evidence & notes" }).click();
  const pendingNote =
    "Check all bake conditions and substrate scope against the shared PDF.";
  await page
    .getByRole("textbox", { name: "Review note (optional)" })
    .fill(pendingNote);
  let releaseSave!: () => void;
  const savedResponse = new Promise<void>((resolve) => {
    releaseSave = resolve;
  });
  await page.route(`**/api/specifications/${id}/review`, async (route) => {
    const response = await route.fetch();
    await savedResponse;
    await route.fulfill({ response });
  });
  try {
    await page.getByRole("button", { name: "Keep pending & next" }).click();
    await expect
      .poll(async () => {
        const response = await request.get(sourceUrl);
        const current: SpecificationData = await response.json();
        return current.review?.decisions.req_demo_crosscut;
      })
      .toMatchObject({ status: "needs_review", note: pendingNote });
    // The persisted write can finish before its response reaches the browser.
    // Navigation must wait for that response to avoid an old review navigating back.
    await expect(
      page.getByRole("button", { name: "Generate report" }),
    ).toBeDisabled();
  } finally {
    releaseSave();
  }

  await expect(
    page.getByRole("button", { name: "Generate report" }),
  ).toBeEnabled();
  // The automatic generation entry is covered separately; this offline fixture
  // exercises the retained manual scope review without invoking a provider.
  await page.goto(`${base}/reports/advanced?document=${id}`);
  await expect(
    page.getByRole("tab", { name: "Review & confirm" }),
  ).toBeVisible();
  await showGroup(page, "All active");
  await page.getByRole("button", { name: /Pencil hardness/ }).click();
  await expect(
    page.getByText("Customer asks for", { exact: true }),
  ).toBeVisible();
  await expect(page.getByText("Your lab lists", { exact: true })).toBeVisible();
  const confirmSupport = page.getByRole("button", {
    name: "Confirm lab support",
    exact: true,
  });
  if (await confirmSupport.isVisible()) await confirmSupport.click();
  await expect
    .poll(async () => {
      const response = await request.get(`${base}/api/reports/${id}`);
      const current: ReportData = await response.json();
      return current.line_states.req_demo_pencil?.support_basis;
    })
    .toBe("human");

  await showGroup(page, "Gaps");
  await page.getByRole("button", { name: /Cross-cut adhesion/ }).click();
  await expect(
    page.getByText("Comparison result", { exact: true }),
  ).toBeVisible();
  await expect(
    page.getByText("Lab comparison: Method not listed", { exact: true }),
  ).toBeVisible();
  await expect(
    page.getByText("Source: Needs review", { exact: true }),
  ).toBeVisible();
  const comparisonResponse = await request.get(`${base}/api/reports/${id}`);
  expect(comparisonResponse.ok()).toBeTruthy();
  const comparison: ReportData = await comparisonResponse.json();
  const sourceReasons = comparison.report.lines.find(
    (line) => line.requirement.id === "req_demo_crosscut",
  )!.extraction_review_reasons;
  expect(sourceReasons.length).toBeGreaterThan(0);
  for (const reason of sourceReasons) {
    await expect(page.getByText(reason, { exact: true })).toBeVisible();
  }
  await expect(
    page.getByRole("button", { name: "Confirm lab support", exact: true }),
  ).toHaveCount(0);
  await expect(
    page.getByRole("link", { name: "View lab declarations" }),
  ).toHaveAttribute("href", "/my-lab");
  await expectNoHorizontalOverflow(page);
  await page.screenshot({
    path: testInfo.outputPath("shared-demo-method-gap.png"),
    fullPage: true,
  });

  await page.getByRole("button", { name: /Humidity resistance/ }).click();
  await expect(
    page.getByText("Lab comparison: Outside declared scope", { exact: true }),
  ).toBeVisible();
  await expect(
    page.getByText("Operating scope: Gap", { exact: true }),
  ).toBeVisible();
  await expect(
    page.getByRole("button", { name: "Confirm lab support", exact: true }),
  ).toHaveCount(0);
  await expect(
    page.getByText("40 °C; maximum 120 h continuous exposure.", {
      exact: true,
    }),
  ).toBeVisible();
  await expectNoHorizontalOverflow(page);
  await page.screenshot({
    path: testInfo.outputPath("shared-demo-operating-gap.png"),
    fullPage: true,
  });

  await page.getByRole("button", { name: "Continue to report" }).click();
  await page.getByRole("button", { name: "Review unresolved items" }).click();
  await expect(page.getByRole("combobox", { name: "Show" })).toHaveText(
    "Needs review",
  );
  await page.getByRole("tab", { name: "Report", exact: true }).click();
  await page.getByRole("button", { name: "Create draft report" }).click();
  await expect(
    page.getByRole("img", { name: "Report preview page 1" }),
  ).toBeVisible();
  await expectNoHorizontalOverflow(page);
  for (const format of ["PDF", "JSON"] as const) {
    const download = page.waitForEvent("download");
    await page
      .getByRole("link", { name: `Download ${format}`, exact: true })
      .click();
    const artifact = await download;
    expect(artifact.suggestedFilename()).toMatch(
      new RegExp(`\\.${format.toLowerCase()}$`),
    );
    await artifact.saveAs(
      testInfo.outputPath(`shared-demo-report.${format.toLowerCase()}`),
    );
  }
  await page.screenshot({
    path: testInfo.outputPath("shared-demo-draft-report.png"),
    fullPage: true,
  });

  const afterResponse = await request.get(sourceUrl);
  const after: SpecificationData = await afterResponse.json();
  expect(after.run).toEqual(original.run);
  const reportResponse = await request.get(`${base}/api/reports/${id}`);
  const finalReport: ReportData = await reportResponse.json();
  expect(finalReport.line_states.req_demo_crosscut?.needs_review).toBe(true);
  expect(
    finalReport.report.lines.find(
      (line) => line.requirement.id === "req_demo_humidity",
    )?.status,
  ).toBe("condition_gap");
});
