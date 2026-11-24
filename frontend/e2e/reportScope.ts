import { expect, type Page } from "@playwright/test";

/** Destructive test actions are secondary, next to the selected test title. */
export async function openReportScope(page: Page) {
  // MUI hides the background controls from the accessibility tree while its
  // menu is open. Reuse the open test menu rather than querying that trigger.
  if (
    await page
      .getByRole("menuitem", {
        name: /^(Remove from this report|Restore to this report|Review source exclusion)$/,
      })
      .isVisible()
  )
    return;
  const scope = page.getByRole("button", {
    name: "More test actions",
    exact: true,
  });
  await expect(scope).toHaveAttribute("aria-expanded", /true|false/);
  if ((await scope.getAttribute("aria-expanded")) === "false")
    await scope.click();
}

export async function reportTestAction(page: Page, name: string) {
  await openReportScope(page);
  return page.getByRole("menuitem", { name, exact: true });
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
