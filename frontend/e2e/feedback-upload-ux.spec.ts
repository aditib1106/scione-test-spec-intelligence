import { expect, test, type Page } from "@playwright/test";
import type { LibraryDetail, LibraryDocument } from "../src/libraryTypes";

const pdf = {
  name: "new-customer-version.pdf",
  mimeType: "application/pdf",
  buffer: Buffer.from(
    "%PDF-1.4\n% Offline file-selection regression fixture\n",
  ),
};

const documents: LibraryDocument[] = [
  {
    document_id: "feedback-source-a",
    company_name: "Alpha Lab Customer",
    specification_id: "CSP-EN-401",
    title: "Exterior coating requirements",
    revision: "Original revision A",
    specification_date: "2026-10-01",
    family_id: "feedback-family-a",
    version: 2,
    original_name: "existing-a.pdf",
    created_at: "2026-10-01T12:00:00Z",
    updated_at: "2026-10-01T12:00:00Z",
    requirement_count: 0,
    review_count: 0,
    run_id: null,
    page_count: 2,
  },
  {
    document_id: "feedback-source-b",
    company_name: "Beta Materials",
    specification_id: "CSP-EN-402",
    title: "Interior material requirements",
    revision: "Original revision B",
    specification_date: "2026-10-02",
    family_id: "feedback-family-b",
    version: 3,
    original_name: "existing-b.pdf",
    created_at: "2026-10-02T12:00:00Z",
    updated_at: "2026-10-02T12:00:00Z",
    requirement_count: 0,
    review_count: 0,
    run_id: null,
    page_count: 2,
  },
];

async function openUpload(page: Page) {
  await page.route("**/api/library", (route) =>
    route.fulfill({ json: { documents } }),
  );
  await page.goto("/specifications/library");
  await page
    .getByRole("button", { name: "Add specification", exact: true })
    .click();
  return page.getByRole("dialog", { name: "Add customer specification" });
}

test("selected PDF can be removed and reselected without uploading or losing metadata", async ({
  page,
}, testInfo) => {
  let uploads = 0;
  page.on("request", (sent) => {
    if (
      sent.method() === "POST" &&
      new URL(sent.url()).pathname === "/api/specifications"
    )
      uploads++;
  });
  const dialog = await openUpload(page);
  const input = dialog.locator('input[type="file"]');
  await dialog
    .getByLabel("Customer company", { exact: true })
    .fill("New customer");
  await input.setInputFiles(pdf);
  await expect(dialog.getByText(pdf.name, { exact: true })).toBeVisible();
  await expect(dialog.getByText(/Selected, not uploaded yet/)).toBeVisible();
  await expect(
    dialog.getByRole("button", { name: "Replace PDF" }),
  ).toBeVisible();

  // Cancelling the native file chooser must not discard the previous choice.
  await input.setInputFiles([]);
  await expect(dialog.getByText(pdf.name, { exact: true })).toBeVisible();
  if (testInfo.project.name === "desktop") {
    await dialog.screenshot({
      path: testInfo.outputPath("selected-pdf-before-upload.png"),
      animations: "disabled",
    });
    await page.mouse.click(2, 2);
    await expect(dialog).toBeVisible();
  }

  await dialog.getByRole("button", { name: "Remove PDF" }).click();
  await expect(dialog.getByText(pdf.name, { exact: true })).toHaveCount(0);
  await expect(
    dialog.getByRole("button", { name: "Choose PDF" }),
  ).toBeVisible();
  await expect(
    dialog.getByLabel("Customer company", { exact: true }),
  ).toHaveValue("New customer");
  await input.setInputFiles(pdf);
  await expect(dialog.getByText(pdf.name, { exact: true })).toBeVisible();
  expect(uploads).toBe(0);
  await dialog.getByRole("button", { name: "Cancel", exact: true }).click();
  await expect(dialog).not.toBeVisible();
});

test("Save under selects an existing family and preserves a completed upload during a metadata retry", async ({
  page,
}, testInfo) => {
  let releaseUpload!: () => void;
  const uploadGate = new Promise<void>((resolve) => {
    releaseUpload = resolve;
  });
  let uploadCalls = 0;
  const writes: { path: string; body: Record<string, unknown> }[] = [];
  const newId = "feedback-new-version";
  const newDetail: LibraryDetail = {
    document: {
      ...documents[0],
      document_id: newId,
      family_id: "unassigned-new-version",
      company_name: null,
      specification_id: null,
      title: null,
      revision: null,
      specification_date: null,
      original_name: pdf.name,
      version: 0,
    },
    versions: [],
    requirements: [],
  };
  await page.route("**/api/specifications", async (route) => {
    uploadCalls++;
    await uploadGate;
    await route.fulfill({
      json: { document_id: newId, original_name: pdf.name, reused: false },
    });
  });
  await page.route(`**/api/library/${newId}`, (route) =>
    route.fulfill({ json: newDetail }),
  );
  await page.route("**/api/library/*/metadata", (route) => {
    writes.push({
      path: new URL(route.request().url()).pathname,
      body: route.request().postDataJSON(),
    });
    // The PDF has saved successfully, but details must remain editable for retry.
    return route.fulfill({
      status: 503,
      json: { detail: "Could not save specification details." },
    });
  });
  const dialog = await openUpload(page);
  await dialog
    .getByRole("combobox", { name: "Save under", exact: true })
    .click();
  await page
    .getByRole("option", { name: "Beta Materials · CSP-EN-402", exact: true })
    .click();
  await expect(
    dialog.getByLabel("Customer company", { exact: true }),
  ).toHaveValue("Beta Materials");
  await expect(
    dialog.getByLabel("Specification number", { exact: true }),
  ).toHaveValue("CSP-EN-402");
  await expect(
    dialog.getByLabel("Specification title", { exact: true }),
  ).toHaveValue("Interior material requirements");
  await expect(
    dialog.getByLabel("Specification date", { exact: true }),
  ).toHaveValue("");
  await dialog
    .getByLabel("Specification date", { exact: true })
    .fill("2026-11-24");
  await dialog.locator('input[type="file"]').setInputFiles(pdf);
  if (testInfo.project.name === "desktop")
    await dialog.screenshot({
      path: testInfo.outputPath("save-under-existing-family.png"),
      animations: "disabled",
    });

  await dialog
    .getByRole("button", { name: "Save & open PDF", exact: true })
    .click();
  await expect.poll(() => uploadCalls).toBe(1);
  await expect(
    dialog.getByRole("button", { name: "Saving…", exact: true }),
  ).toBeDisabled();
  await expect(
    dialog.getByRole("button", { name: "Cancel", exact: true }),
  ).toBeDisabled();
  await expect(
    dialog.getByRole("button", { name: "Remove PDF" }),
  ).toBeDisabled();
  await page.keyboard.press("Escape");
  await expect(dialog).toBeVisible();
  releaseUpload();

  await expect(dialog.getByRole("alert")).toContainText(
    "Could not save specification details.",
  );
  await expect(dialog.getByText(/KB · Uploaded/)).toBeVisible();
  await expect(
    dialog.getByRole("button", { name: "Replace PDF" }),
  ).toBeDisabled();
  await expect(
    dialog.getByRole("button", { name: "Remove PDF" }),
  ).toBeDisabled();
  expect(writes).toHaveLength(1);
  expect(writes[0]).toEqual({
    path: `/api/library/${newId}/metadata`,
    body: {
      family_id: "feedback-family-b",
      company_name: "Beta Materials",
      specification_id: "CSP-EN-402",
      title: "Interior material requirements",
      revision: "",
      specification_date: "2026-11-24",
      expected_version: 0,
    },
  });
  await dialog
    .getByRole("button", { name: "Save & open PDF", exact: true })
    .click();
  await expect.poll(() => writes.length).toBe(2);
  expect(uploadCalls).toBe(1);
  expect(writes[1]).toEqual(writes[0]);
});
