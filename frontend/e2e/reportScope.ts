import { expect, type Page } from "@playwright/test";

/** Scope changes are intentionally secondary to assessment review. */
export async function openReportScope(page: Page) {
  const scope = page.getByRole("button", { name: "Report scope", exact: true });
  await expect(scope).toHaveAttribute("aria-expanded", /true|false/);
  if ((await scope.getAttribute("aria-expanded")) === "false")
    await scope.click();
}

/** Reload only the saved view; it never starts another model operation. */
export async function reloadSavedReport(page: Page) {
  await page
    .getByRole("button", { name: "Report options", exact: true })
    .click();
  await page
    .getByRole("menuitem", { name: "Reload saved report", exact: true })
    .click();
}
