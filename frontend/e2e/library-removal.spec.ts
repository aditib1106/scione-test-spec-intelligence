import { expect, test } from "@playwright/test";
import type { LibraryDocument } from "../src/libraryTypes";

test("removing a saved version is explicit, preserves a failed attempt and leaves earlier versions visible", async ({
  page,
}) => {
  const base: LibraryDocument = {
    document_id: "saved-b",
    company_name: "Harbor",
    specification_id: "CSP-301",
    title: "Exterior finish",
    revision: "B",
    family_id: "finish",
    version: 3,
    original_name: "finish-b.pdf",
    created_at: "2026-11-03T12:00:00Z",
    updated_at: "2026-11-03T12:00:00Z",
    requirement_count: 4,
    review_count: 0,
    run_id: "run-b",
    page_count: 2,
  };
  let documents = [
    base,
    {
      ...base,
      document_id: "saved-a",
      original_name: "finish-a.pdf",
      revision: "A",
      created_at: "2026-10-01T12:00:00Z",
    },
  ];
  const attempts: unknown[] = [];
  await page.route("**/api/library**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    if (path === "/api/library") return route.fulfill({ json: { documents } });
    if (
      path === "/api/library/saved-b" &&
      route.request().method() === "DELETE"
    ) {
      attempts.push(route.request().postDataJSON());
      if (attempts.length === 1) {
        documents = documents.map((row) =>
          row.document_id === "saved-b" ? { ...row, version: 4 } : row,
        );
        return route.fulfill({
          status: 409,
          json: {
            detail: "The saved version changed. Reload before removing it.",
          },
        });
      }
      documents = documents.filter((row) => row.document_id !== "saved-b");
      return route.fulfill({
        json: { document_id: "saved-b", archived: true, version: 4 },
      });
    }
    return route.fulfill({ status: 404, json: { detail: "Not found" } });
  });
  await page.goto("/specifications/library");
  await expect(
    page.getByRole("button", { name: /Try worked|Import demo|walkthrough/i }),
  ).toHaveCount(0);
  await page
    .getByRole("button", { name: "Remove Exterior finish", exact: true })
    .click();
  const dialog = page.getByRole("dialog", {
    name: "Remove this specification?",
  });
  await dialog.getByRole("button", { name: "Cancel", exact: true }).click();
  expect(attempts).toHaveLength(0);
  await page
    .getByRole("button", { name: "Remove Exterior finish", exact: true })
    .click();
  await dialog
    .getByRole("button", { name: "Remove from library", exact: true })
    .click();
  await expect(dialog.getByRole("alert")).toContainText(
    "saved version changed",
  );
  await expect(
    page.getByRole("table", {
      name: "Saved customer specifications",
      includeHidden: true,
    }),
  ).toContainText("finish-b.pdf");
  await expect(
    dialog.getByRole("button", { name: "Remove from library", exact: true }),
  ).toBeDisabled();
  await dialog
    .getByRole("button", { name: "Reload library", exact: true })
    .click();
  await page
    .getByRole("button", { name: "Remove Exterior finish", exact: true })
    .click();
  await dialog
    .getByRole("button", { name: "Remove from library", exact: true })
    .click();
  await expect(dialog).not.toBeVisible();
  await expect(
    page.getByRole("table", { name: "Saved customer specifications" }),
  ).toContainText("finish-a.pdf");
  await expect(
    page.getByRole("table", { name: "Saved customer specifications" }),
  ).not.toContainText("finish-b.pdf");
  expect(attempts).toEqual([{ version: 3 }, { version: 4 }]);
});
