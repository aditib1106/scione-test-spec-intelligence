import {
  expect,
  test,
  type Page,
  type APIRequestContext,
} from "@playwright/test";
import type { LibraryDocument } from "../src/libraryTypes";
import type { Workspace } from "../src/types";

const base = "http://127.0.0.1:8011";
test.use({ baseURL: base });

async function manySpecifications(page: Page, request: APIRequestContext) {
  const response = await request.get("/api/workspace");
  expect(response.ok()).toBeTruthy();
  const workspace = (await response.json()) as Workspace;
  const first = workspace.documents.find(
    (doc) => doc.original_name === "offline-two-test-spec.pdf",
  )!;
  const second = workspace.documents.find(
    (doc) => doc.original_name === "offline-customer-coating.pdf",
  )!;
  expect(first).toBeTruthy();
  expect(second).toBeTruthy();
  const extras = Array.from({ length: 40 }, (_, index) => ({
    ...first,
    document_id: `picker-reference-${index}`,
    original_name: `reference-document-${index}.pdf`,
  }));
  await page.route("**/api/workspace", (route) =>
    route.fulfill({
      json: { ...workspace, documents: [...workspace.documents, ...extras] },
    }),
  );
  const library: LibraryDocument[] = [...workspace.documents, ...extras].map(
    (doc, index) => ({
      document_id: doc.document_id,
      original_name: doc.original_name,
      company_name:
        doc.document_id === second.document_id
          ? "Target Customer"
          : "Other Customer",
      specification_id:
        doc.document_id === second.document_id
          ? "CSP-PICKER-02"
          : `CSP-PICKER-${index + 100}`,
      title:
        doc.document_id === second.document_id
          ? "Special verification requirements"
          : "General coating requirements",
      revision: null,
      specification_date: "2026-11-24",
      family_id: `family-${doc.document_id}`,
      version: 0,
      created_at: doc.created_at,
      updated_at: doc.created_at,
      requirement_count: doc.requirement_count,
      review_count: 0,
      run_id: doc.latest_run_id,
      page_count: doc.page_count,
    }),
  );
  await page.route("**/api/library", (route) =>
    route.fulfill({ json: { documents: library } }),
  );
  let writes = 0;
  page.on("request", (sent) => {
    if (!["GET", "HEAD", "OPTIONS"].includes(sent.method())) writes++;
  });
  return { first, second, writes: () => writes };
}

test("saved specification picker searches a large PDF list and switches the exact document without writes", async ({
  page,
  request,
}, testInfo) => {
  const { first, second, writes } = await manySpecifications(page, request);
  await page.goto(`/specifications?document=${first.document_id}`);
  const picker = page.getByRole("combobox", {
    name: "Saved specification",
    exact: true,
  });
  await expect(picker).toHaveValue(first.original_name);
  await picker.fill("reference-document-39");
  await expect(page.getByRole("option")).toHaveCount(1);
  await expect(
    page.getByRole("option", {
      name: "reference-document-39.pdf",
      exact: true,
    }),
  ).toBeVisible();
  expect(writes()).toBe(0);
  await picker.fill("offline-customer-coating");
  await expect(page.getByRole("option")).toHaveCount(1);
  await page
    .getByRole("option", { name: second.original_name, exact: true })
    .click();
  await expect(page).toHaveURL(new RegExp(`document=${second.document_id}`));
  await expect(picker).toHaveValue(second.original_name);
  await expect(
    page.getByRole("combobox", { name: "Selected test", exact: true }),
  ).toBeVisible();
  expect(writes()).toBe(0);
  if (testInfo.project.name === "desktop") {
    await picker.fill("reference-document-39");
    await page.screenshot({
      path: testInfo.outputPath("saved-specification-search.png"),
      fullPage: false,
    });
  }
});

test("report specification picker searches customer and title metadata without inference or edits", async ({
  page,
  request,
}, testInfo) => {
  const { first, second, writes } = await manySpecifications(page, request);
  await page.goto(`/reports?document=${first.document_id}`);
  const picker = page.getByRole("combobox", {
    name: "Customer specification",
    exact: true,
  });
  await expect(picker).toHaveValue(/Other Customer/);
  await picker.fill("Target Customer");
  await expect(page.getByRole("option")).toHaveCount(1);
  await expect(page.getByRole("option")).toContainText("CSP-PICKER-02");
  await picker.fill("Special verification");
  await expect(page.getByRole("option")).toHaveCount(1);
  if (testInfo.project.name === "desktop")
    await page.screenshot({
      path: testInfo.outputPath("report-specification-search.png"),
      fullPage: false,
    });
  const nextReport = page.waitForResponse(
    (response) =>
      new URL(response.url()).pathname ===
      `/api/comparisons/${second.document_id}`,
  );
  await page.getByRole("option").click();
  expect((await nextReport).ok()).toBeTruthy();
  await expect(page).toHaveURL(new RegExp(`document=${second.document_id}`));
  await expect(picker).toHaveValue(/Target Customer · CSP-PICKER-02/);
  expect(writes()).toBe(0);
});
