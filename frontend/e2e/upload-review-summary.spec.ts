import { expect, test } from "@playwright/test";
import type { SpecificationData } from "../src/types";

test("saved uploads are explicit and review notes stay compact above the PDF", async ({
  page,
  request,
}) => {
  await request.post("/api/workspace/reset", { data: { acknowledged: true } });
  const imported = await request.post("/api/specifications/worked-example");
  expect(imported.ok()).toBeTruthy();
  const { document_id: id } = await imported.json();
  const path = `/api/specifications/${id}`;
  const source = (await (await request.get(path)).json()) as SpecificationData;
  const pdf = await (await request.get(`${path}/pdf`)).body();
  const sourceCheck = "Check shared conditioning instructions on page 1.";
  const documentNote = "Check the specification date against the original PDF.";
  await page.route("**/api/auth/session", (route) =>
    route.fulfill({
      json: {
        authenticated: true,
        enabled: true,
        user: {
          id: "offline-lab-user",
          username: "reader",
          display_name: "Reader",
          role: "lab",
          lab_id: "offline-lab",
          lab_name: "Offline lab",
        },
        csrf_token: "offline-csrf",
      },
    }),
  );
  await page.route(`**${path}`, (route) =>
    route.fulfill({
      json: {
        ...source,
        document_notes_review: undefined,
        document_review_issues: [documentNote],
        coverage_issues: [sourceCheck],
      },
    }),
  );
  let modelPosts = 0;
  page.on("request", (sent) => {
    if (
      sent.method() === "POST" &&
      /\/api\/specifications\/[^/]+\/(analysis|matching)$/.test(
        new URL(sent.url()).pathname,
      )
    )
      modelPosts++;
  });
  await page.goto(`/specifications?document=${id}`);
  const summary = page.getByRole("button", { name: /Review summary/ });
  await expect(summary).toContainText("1 document note · 1 source check");
  expect((await summary.boundingBox())!.height).toBeLessThan(90);
  await expect(page.getByText(sourceCheck, { exact: true })).not.toBeVisible();
  await expect(
    page.getByText("Extracted requirements", { exact: true }),
  ).toHaveCount(0);
  await expect(
    page.getByRole("button", { name: "Analyze again", exact: true }),
  ).toBeVisible();
  if (page.viewportSize()!.width >= 900) {
    const sourcePanel = page.getByText("Source document", { exact: true });
    expect((await sourcePanel.boundingBox())!.y).toBeLessThan(450);
  }
  await summary.click();
  await expect(page.getByText(sourceCheck, { exact: true })).toBeVisible();
  await expect(page.getByText(documentNote, { exact: true })).toBeVisible();
  await summary.click();
  await page.locator('input[type="file"]').setInputFiles({
    name: "renamed-copy.pdf",
    mimeType: "application/pdf",
    buffer: pdf,
  });
  await expect(
    page.getByText(
      "Opened saved analysis for this file in your lab. No new analysis started.",
      { exact: false },
    ),
  ).toBeVisible();
  await expect(
    page.getByRole("combobox", { name: "Saved specification" }),
  ).toContainText(source.original_name);
  await expect(
    page.getByText("Saved as renamed-copy.pdf.", { exact: false }),
  ).toHaveCount(0);
  expect(modelPosts).toBe(0);
});

test("adding an existing file through the library preserves metadata and explains saved analysis", async ({
  page,
  request,
}) => {
  await request.post("/api/workspace/reset", { data: { acknowledged: true } });
  const imported = await request.post("/api/specifications/worked-example");
  const { document_id: id } = await imported.json();
  const path = `/api/specifications/${id}`;
  const before = await (await request.get(`/api/library/${id}`)).json();
  const pdf = await (await request.get(`${path}/pdf`)).body();
  // An analyzed file may still have untouched metadata version 0. The upload
  // receipt must prevent Add fields from overwriting that saved source too.
  await page.route(`**/api/library/${id}`, (route) =>
    route.fulfill({
      json: { ...before, document: { ...before.document, version: 0 } },
    }),
  );
  let modelPosts = 0;
  let metadataWrites = 0;
  page.on("request", (sent) => {
    if (
      sent.method() === "PUT" &&
      new URL(sent.url()).pathname === `/api/library/${id}/metadata`
    )
      metadataWrites++;
    if (
      sent.method() === "POST" &&
      /\/api\/specifications\/[^/]+\/(analysis|matching)$/.test(
        new URL(sent.url()).pathname,
      )
    )
      modelPosts++;
  });
  await page.goto("/specifications/library");
  await page
    .getByRole("button", { name: "Add specification", exact: true })
    .click();
  const dialog = page.getByRole("dialog", {
    name: "Add customer specification",
  });
  await dialog.locator('input[type="file"]').setInputFiles({
    name: "renamed-copy-from-library.pdf",
    mimeType: "application/pdf",
    buffer: pdf,
  });
  await dialog
    .getByRole("textbox", { name: "Specification title", exact: true })
    .fill("Do not overwrite the saved title");
  await dialog
    .getByRole("button", { name: "Save & open PDF", exact: true })
    .click();
  await expect(page).toHaveURL(new RegExp(`/specifications\\?document=${id}$`));
  await expect(
    page.getByText(
      "Opened saved analysis for this file in your lab. No new analysis started.",
      {
        exact: false,
      },
    ),
  ).toBeVisible();
  const after = await (await request.get(`/api/library/${id}`)).json();
  expect(after.document.title).toBe(before.document.title);
  expect(after.document.original_name).toBe(before.document.original_name);
  expect(metadataWrites).toBe(0);
  expect(modelPosts).toBe(0);
  await page.reload();
  await expect(
    page.getByText(
      "Opened saved analysis for this file in your lab. No new analysis started.",
      {
        exact: false,
      },
    ),
  ).toHaveCount(0);
});
