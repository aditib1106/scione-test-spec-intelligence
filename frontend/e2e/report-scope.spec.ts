import { expectReportFilter, selectReportFilter } from "./workspaceActions";
import {
  openReportScope,
  reportTestAction,
  reloadSavedReport,
} from "./reportScope";
import { expect, test, type APIRequestContext } from "@playwright/test";
import type { ComparisonData } from "../src/comparisonTypes";
import type { LabProfile, SpecificationData, Workspace } from "../src/types";

async function savedScopeExample(request: APIRequestContext) {
  expect(
    (
      await request.post("/api/workspace/reset", {
        data: { acknowledged: true },
      })
    ).ok(),
  ).toBeTruthy();
  const lab = (await (await request.get("/api/lab")).json()) as LabProfile;
  expect(
    (
      await request.post("/api/lab/example", {
        data: { version: lab.version, example: "csp301" },
      })
    ).ok(),
  ).toBeTruthy();
  const imported = await request.post("/api/specifications/worked-example");
  expect(imported.ok()).toBeTruthy();
  const { document_id: id } = (await imported.json()) as {
    document_id: string;
  };
  const response = await request.get(`/api/comparisons/${id}`);
  expect(response.ok()).toBeTruthy();
  return { id, report: (await response.json()) as ComparisonData };
}

test("removing and restoring report scope persists without changing source extraction", async ({
  page,
  request,
}) => {
  expect(
    (
      await request.post("/api/workspace/reset", {
        data: { acknowledged: true },
      })
    ).ok(),
  ).toBeTruthy();
  const initial = (await (await request.get("/api/lab")).json()) as LabProfile;
  expect(
    (
      await request.post("/api/lab/example", {
        data: { version: initial.version, example: "csp301" },
      })
    ).ok(),
  ).toBeTruthy();
  const imported = await request.post("/api/specifications/worked-example");
  expect(imported.ok()).toBeTruthy();
  const { document_id: id } = (await imported.json()) as {
    document_id: string;
  };
  let writes = 0;
  let releaseResponse = () => {};
  const responseGate = new Promise<void>((resolve) => {
    releaseResponse = resolve;
  });
  await page.route(`**/api/reports/${id}/exclusions`, async (route) => {
    if (route.request().method() !== "POST") return route.continue();
    writes++;
    const response = await route.fetch();
    await responseGate;
    await route.fulfill({ response });
  });
  const before = (await (
    await request.get(`/api/specifications/${id}`)
  ).json()) as SpecificationData;
  await page.goto(`/reports?document=${id}`);
  const selected = page
    .getByRole("navigation", { name: "Requested tests" })
    .getByRole("button", { name: "Specular gloss", exact: true });
  await expect(selected).toBeVisible();
  await selected.click();
  await (await reportTestAction(page, "Remove from this report")).click();
  let dialog = page.getByRole("dialog");
  await dialog
    .getByRole("textbox", { name: "Note (optional)" })
    .fill("Customer removed this test from the requested quote.");
  await dialog
    .getByRole("button", { name: "Remove test", exact: true })
    .dblclick();
  await expect.poll(() => writes).toBe(1);
  releaseResponse();
  await expect(dialog).not.toBeVisible();
  await expect(selected).toHaveAttribute("aria-current", "true");
  await expect(selected.getByText("Excluded", { exact: true })).toBeVisible();
  await expect(
    await reportTestAction(page, "Restore to this report"),
  ).toBeVisible();
  const removed = (await (
    await request.get(`/api/comparisons/${id}`)
  ).json()) as ComparisonData;
  expect(removed.summary.excluded).toBe(1);
  const removedTest = removed.requirements.find(
    (item) => item.test_name === "Specular gloss",
  )!;
  expect(removedTest.comparison.status).toBe("excluded");
  expect(removedTest.report_scope_exclusion?.note).toBe(
    "Customer removed this test from the requested quote.",
  );
  const after = (await (
    await request.get(`/api/specifications/${id}`)
  ).json()) as SpecificationData;
  expect(after.run).toEqual(before.run);
  expect(after.review).toEqual(before.review);

  await page.reload();
  await selected.click();
  await expect(
    await reportTestAction(page, "Restore to this report"),
  ).toBeVisible();
  await (await reportTestAction(page, "Restore to this report")).click();
  dialog = page.getByRole("dialog");
  await expect(
    dialog.getByText("Reason for removal", { exact: true }),
  ).toBeVisible();
  await expect(
    dialog.getByText("Customer removed this test from the requested quote.", {
      exact: true,
    }),
  ).toBeVisible();
  await dialog
    .getByRole("button", { name: "Restore test", exact: true })
    .click();
  await expect(dialog).not.toBeVisible();
  await expect(selected).toHaveAttribute("aria-current", "true");
  await expect(
    await reportTestAction(page, "Remove from this report"),
  ).toBeVisible();
  expect(writes).toBe(2);
  const restored = (await (
    await request.get(`/api/comparisons/${id}`)
  ).json()) as ComparisonData;
  expect(restored.summary.excluded).toBe(0);
  expect(
    restored.requirements.find(
      (item) => item.requirement_id === removedTest.requirement_id,
    )?.report_scope_exclusion,
  ).toBeNull();
  expect(
    restored.requirements.find(
      (item) => item.requirement_id === removedTest.requirement_id,
    )?.comparison.status,
  ).not.toBe("excluded");
});

test("removing and restoring a filtered test never silently resets the result filter", async ({
  page,
  request,
}) => {
  expect(
    (
      await request.post("/api/workspace/reset", {
        data: { acknowledged: true },
      })
    ).ok(),
  ).toBeTruthy();
  const lab = (await (await request.get("/api/lab")).json()) as LabProfile;
  expect(
    (
      await request.post("/api/lab/example", {
        data: { version: lab.version, example: "csp301" },
      })
    ).ok(),
  ).toBeTruthy();
  const imported = await request.post("/api/specifications/worked-example");
  expect(imported.ok()).toBeTruthy();
  const { document_id: id } = (await imported.json()) as {
    document_id: string;
  };
  await page.goto(`/reports?document=${id}`);
  await selectReportFilter(page, "Partially matched");
  const search = page.getByRole("textbox", { name: "Search requested tests" });
  await search.fill("gloss");
  const list = page.getByRole("navigation", { name: "Requested tests" });
  await expect(
    list.getByRole("button", { name: "Specular gloss", exact: true }),
  ).toBeVisible();
  await (await reportTestAction(page, "Remove from this report")).click();
  let dialog = page.getByRole("dialog");
  await dialog
    .getByRole("button", { name: "Remove test", exact: true })
    .click();
  await expect(dialog).not.toBeVisible();
  await expectReportFilter(page, "Partially matched");
  await expect(search).toHaveValue("gloss");
  await expect(list.getByRole("list").getByRole("button")).toHaveCount(0);
  await reloadSavedReport(page);
  await expectReportFilter(page, "Partially matched");
  await expect(search).toHaveValue("gloss");
  await selectReportFilter(page, "Excluded");
  await expect(
    list.getByRole("button", { name: "Specular gloss", exact: true }),
  ).toBeVisible();
  await (await reportTestAction(page, "Restore to this report")).click();
  dialog = page.getByRole("dialog");
  await dialog
    .getByRole("button", { name: "Restore test", exact: true })
    .click();
  await expect(dialog).not.toBeVisible();
  await expectReportFilter(page, "Excluded");
  await expect(search).toHaveValue("gloss");
  await expect(list.getByRole("list").getByRole("button")).toHaveCount(0);
});

test("source-excluded tests link back to source review without offering a report restore", async ({
  page,
  request,
}) => {
  const { id, report } = await savedScopeExample(request);
  const selected = report.requirements[0];
  selected.source_review_state = "excluded";
  selected.comparison.status = "excluded";
  await page.route(`**/api/comparisons/${id}`, (route) =>
    route.fulfill({ json: report }),
  );
  let writes = 0;
  page.on("request", (request) => {
    if (request.method() === "POST") writes++;
  });
  await page.goto(`/reports?document=${id}`);
  await page
    .getByRole("navigation", { name: "Requested tests" })
    .getByRole("button", { name: selected.test_name, exact: true })
    .click();
  await openReportScope(page);
  await expect(
    page.getByRole("menuitem", {
      name: "Review source exclusion",
      exact: true,
    }),
  ).toHaveAttribute(
    "href",
    `/specifications?document=${id}&requirement=${selected.requirement_id}`,
  );
  await expect(
    page.getByRole("menuitem", { name: "Restore to this report", exact: true }),
  ).toHaveCount(0);
  await expect(
    page.getByRole("menuitem", {
      name: "Remove from this report",
      exact: true,
    }),
  ).toHaveCount(0);
  expect(writes).toBe(0);
});

test("failed removal keeps the saved findings and note available for retry", async ({
  page,
  request,
}) => {
  const { id, report } = await savedScopeExample(request);
  const selected = report.requirements[0];
  let writes = 0;
  await page.route(`**/api/reports/${id}/exclusions`, async (route) => {
    writes++;
    await route.fulfill({
      status: 409,
      json: {
        detail: "The report changed. Reload saved data before retrying.",
      },
    });
  });
  await page.goto(`/reports?document=${id}`);
  await page
    .getByRole("navigation", { name: "Requested tests" })
    .getByRole("button", { name: selected.test_name, exact: true })
    .click();
  await (await reportTestAction(page, "Remove from this report")).click();
  const dialog = page.getByRole("dialog");
  await dialog
    .getByRole("textbox", { name: "Note (optional)", exact: true })
    .fill("Keep this reason if the save fails.");
  await dialog
    .getByRole("button", { name: "Remove test", exact: true })
    .click();
  await expect(dialog.getByRole("alert")).toContainText("The report changed.");
  await expect(
    dialog.getByRole("textbox", { name: "Note (optional)", exact: true }),
  ).toHaveValue("Keep this reason if the save fails.");
  await expect(
    dialog.getByRole("button", { name: "Remove test", exact: true }),
  ).toBeEnabled();
  expect(writes).toBe(1);
  const saved = (await (
    await request.get(`/api/comparisons/${id}`)
  ).json()) as ComparisonData;
  expect(saved).toEqual(report);
  await dialog.getByRole("button", { name: "Cancel", exact: true }).click();
  await expect(
    page.getByRole("button", { name: "More test actions", exact: true }),
  ).toBeFocused();
});

test("a pending test removal cannot replace another document with the same requirement IDs", async ({
  page,
  request,
}) => {
  const { id, report } = await savedScopeExample(request);
  const otherId = "bbbbbbbbbbbbbbbb";
  const otherReport = structuredClone(report);
  otherReport.comparison_id = "other-document-comparison";
  otherReport.document = {
    ...otherReport.document,
    document_id: otherId,
    original_name: "other-document.pdf",
    title: "Other document",
    specification_id: "OTHER",
    company_name: "Other company",
  };
  otherReport.requirements[0].test_name = "Other document test";
  const workspace = (await (
    await request.get("/api/workspace")
  ).json()) as Workspace;
  workspace.documents.push({
    ...workspace.documents.find((document) => document.document_id === id)!,
    document_id: otherId,
    original_name: "other-document.pdf",
  });
  const library = (await (await request.get("/api/library")).json()) as {
    documents: ComparisonData["document"][];
  };
  library.documents.push(otherReport.document);
  await page.route("**/api/workspace", (route) =>
    route.fulfill({ json: workspace }),
  );
  await page.route("**/api/library", (route) =>
    route.fulfill({ json: library }),
  );
  await page.route(`**/api/comparisons/${otherId}`, (route) =>
    route.fulfill({ json: otherReport }),
  );
  await page.route(`**/api/specifications/${otherId}/matching`, (route) =>
    route.fulfill({
      json: {
        source_run_id: otherReport.source_run_id,
        report_input_fingerprint: otherReport.report_input_fingerprint,
        current_run_id: null,
        runs: [],
      },
    }),
  );
  await page.route(`**/api/specifications/${otherId}/operations`, (route) =>
    route.fulfill({ json: { recent_job: null, latest: null } }),
  );
  let release = () => {};
  const gate = new Promise<void>((resolve) => {
    release = resolve;
  });
  let handled = () => {};
  const finished = new Promise<void>((resolve) => {
    handled = resolve;
  });
  let writes = 0;
  await page.route(`**/api/reports/${id}/exclusions`, async (route) => {
    writes++;
    await gate;
    await route.fulfill({ json: report });
    handled();
  });
  await page.goto(`/reports?document=${id}`);
  await (await reportTestAction(page, "Remove from this report")).click();
  let dialog = page.getByRole("dialog");
  await dialog
    .getByRole("textbox", { name: "Note (optional)", exact: true })
    .fill("Only for the old document.");
  await dialog
    .getByRole("button", { name: "Remove test", exact: true })
    .click();
  await expect.poll(() => writes).toBe(1);
  // Browser Back/Forward can change routes even while a modal save is pending.
  await page.evaluate((documentId) => {
    window.history.pushState({}, "", `/reports?document=${documentId}`);
    window.dispatchEvent(new PopStateEvent("popstate"));
  }, otherId);
  await expect(page.getByRole("dialog")).not.toBeVisible();
  const list = page.getByRole("navigation", { name: "Requested tests" });
  await list
    .getByRole("button", { name: "Other document test", exact: true })
    .click();
  release();
  await finished;
  await expect(
    page
      .getByTestId("report-detail-pane")
      .getByRole("heading", { name: "Other document test", exact: true }),
  ).toBeVisible();
  await (await reportTestAction(page, "Remove from this report")).click();
  dialog = page.getByRole("dialog");
  await expect(
    dialog.getByRole("textbox", { name: "Note (optional)", exact: true }),
  ).toHaveValue("");
  await expect(dialog).toContainText("Other document test");
  expect(writes).toBe(1);
  await dialog.getByRole("button", { name: "Cancel", exact: true }).click();
  await page.unrouteAll({ behavior: "wait" });
});
