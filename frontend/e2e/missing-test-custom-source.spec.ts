import {
  expect,
  test,
  type APIRequestContext,
  type Page,
} from "@playwright/test";
import type { ComparisonData } from "../src/comparisonTypes";
import type { SpecificationData, Workspace } from "../src/types";
import { reviewAction } from "./workspaceActions";

test.use({ actionTimeout: 15_000 });

async function sourceFixture(page: Page, request: APIRequestContext) {
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
  const path = `/api/specifications/${id}`;
  const source = (await (await request.get(path)).json()) as SpecificationData;
  await page.goto(`/specifications?document=${id}`);
  await expect(
    page.getByRole("combobox", { name: "Selected test", exact: true }),
  ).toBeVisible();
  return { id: id as string, path, source };
}

test("add missing test immediately previews the current PDF page and cancelling preserves the source", async ({
  page,
  request,
}, testInfo) => {
  const { id, path, source } = await sourceFixture(page, request);
  expect(source.document.page_count).toBeGreaterThan(1);
  if (testInfo.project.name === "narrow") {
    const toggle = page.getByRole("button", { name: /Source PDF preview/ });
    if ((await toggle.getAttribute("aria-expanded")) === "false")
      await toggle.click();
  }
  await page.getByLabel(`Page of ${source.document.page_count}`).fill("2");
  await expect(
    page.getByRole("img", { name: /^PDF page 2(?:,|$)/ }),
  ).toBeVisible();
  let writes = 0;
  let addPreviewReads = 0;
  page.on("request", (sent) => {
    if (!["GET", "HEAD", "OPTIONS"].includes(sent.method())) writes++;
    const url = new URL(sent.url());
    if (
      url.pathname === `/api/specifications/${id}/pages/2` &&
      !url.searchParams.has("requirement_id")
    )
      addPreviewReads++;
  });
  await (await reviewAction(page, "Add missing test")).click();
  const editor = page.getByRole("dialog", {
    name: "Add missing test",
    exact: true,
  });
  await expect(
    editor.getByRole("tab", { name: "Test & method", exact: true }),
  ).toHaveAttribute("aria-selected", "true");
  const preview = editor.getByRole("region", {
    name: "Original page for missing test",
    exact: true,
  });
  const image = preview.getByRole("img", {
    name: "Source evidence PDF page 2",
    exact: true,
  });
  await expect(image).toBeVisible();
  await expect
    .poll(() => image.evaluate((node: HTMLImageElement) => node.naturalWidth))
    .toBeGreaterThan(0);
  await expect(editor.getByRole("img")).toHaveCount(1);
  expect(addPreviewReads).toBe(1);
  await editor.getByLabel("Test name").fill("Unsaved custom source check");
  if (testInfo.project.name === "desktop") {
    const picture = (await preview.boundingBox())!;
    const name = (await editor.getByLabel("Test name").boundingBox())!;
    expect(picture.x + picture.width).toBeLessThanOrEqual(name.x + 1);
  }
  await editor.screenshot({
    path: testInfo.outputPath("add-missing-test-current-page.png"),
    animations: "disabled",
  });
  await editor.getByRole("button", { name: "Cancel", exact: true }).click();
  await expect(editor).not.toBeVisible();
  const after = (await (await request.get(path)).json()) as SpecificationData;
  expect(after.run).toEqual(source.run);
  expect(after.facts_version).toBe(source.facts_version);
  expect(after.review).toEqual(source.review);
  expect(writes).toBe(0);
});

test("a custom source-backed test is saved for review and reaches the report without changing lab or catalog", async ({
  page,
  request,
}) => {
  const { id, path, source } = await sourceFixture(page, request);
  const workspaceBefore = (await (
    await request.get("/api/workspace")
  ).json()) as Workspace;
  const labBefore = await (await request.get("/api/lab")).json();
  const customName = "Five-position panel mapping";
  expect(
    workspaceBefore.catalog.tests.some((entry) => entry.name === customName),
  ).toBeFalsy();
  const quote = "Mark a repeatable five-position map on each panel.";
  const sourcePage = source.document.pages.find((entry) =>
    entry.text.replace(/\s+/g, " ").includes(quote),
  );
  expect(sourcePage).toBeTruthy();
  let inferencePosts = 0;
  page.on("request", (sent) => {
    if (
      sent.method() === "POST" &&
      /\/(analysis|matching)$/.test(new URL(sent.url()).pathname)
    )
      inferencePosts++;
  });
  await (await reviewAction(page, "Add missing test")).click();
  const editor = page.getByRole("dialog", {
    name: "Add missing test",
    exact: true,
  });
  await editor.getByLabel("Test name").fill(customName);
  await editor
    .getByRole("tab", { name: "Source evidence", exact: true })
    .click();
  await editor.getByLabel("Source page").fill(String(sourcePage!.page_number));
  await editor.getByLabel("Source quotation").fill(quote);
  await expect(editor.getByRole("img")).toHaveCount(1);
  const savedResponse = page.waitForResponse(
    (response) =>
      new URL(response.url()).pathname === `${path}/requirements` &&
      response.request().method() === "POST",
  );
  await editor.getByRole("button", { name: "Add test", exact: true }).click();
  expect((await savedResponse).ok()).toBeTruthy();
  await expect(editor).not.toBeVisible();
  const after = (await (await request.get(path)).json()) as SpecificationData;
  const added = after.run!.extraction.requirements.find(
    (entry) => entry.original_test_name === customName,
  )!;
  expect(added.id).toMatch(/^req_human_/);
  expect(added.requested_methods).toEqual([]);
  expect(added.evidence).toEqual([
    { page: sourcePage!.page_number, quote, section: null },
  ]);
  expect(after.run!.extraction.requirements).toHaveLength(
    source.run!.extraction.requirements.length + 1,
  );
  expect(after.run!.raw_response).toBe(source.run!.raw_response);
  expect(after.facts_version).toBe((source.facts_version ?? 0) + 1);
  expect(after.assessments[added.id].state).toBe("needs_attention");
  expect(after.review?.decisions[added.id]?.status).not.toBe("confirmed");

  const report = (await (
    await request.get(`/api/comparisons/${id}`)
  ).json()) as ComparisonData;
  const reportItem = report.requirements.find(
    (entry) => entry.requirement_id === added.id,
  )!;
  expect(reportItem.test_name).toBe(customName);
  expect(reportItem.capability_id).toBeNull();
  expect(reportItem.source_review_state).toBe("needs_attention");
  expect(reportItem.comparison.status).toBe("needs_information");
  expect(
    reportItem.comparison.checks.some((check) => check.status === "supported"),
  ).toBeFalsy();
  await page.goto(`/reports?document=${id}`);
  await expect(
    page
      .getByRole("navigation", { name: "Requested tests" })
      .getByRole("button", { name: customName, exact: true }),
  ).toBeVisible();
  const workspaceAfter = (await (
    await request.get("/api/workspace")
  ).json()) as Workspace;
  expect(workspaceAfter.catalog).toEqual(workspaceBefore.catalog);
  expect(await (await request.get("/api/lab")).json()).toEqual(labBefore);
  expect(inferencePosts).toBe(0);
});
