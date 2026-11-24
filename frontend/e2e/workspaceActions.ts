import { expect, type Locator, type Page } from "@playwright/test";

/** Exercise the visible secondary-action menus, without bypassing their guards. */
async function menuAction(
  page: Page,
  triggerName: string,
  actionName: string,
): Promise<Locator> {
  const action = page.getByRole("menuitem", {
    name: actionName,
    exact: true,
  });
  if (!(await action.isVisible()))
    await page.getByRole("button", { name: triggerName, exact: true }).click();
  await expect(action).toBeVisible();
  return action;
}

export const documentAction = (page: Page, name: string) =>
  menuAction(page, "Document actions", name);

export const reviewAction = (page: Page, name: string) =>
  menuAction(page, "More review actions", name);

export async function openReportFilters(page: Page): Promise<Locator> {
  const filter = page.getByRole("combobox", { name: "Show", exact: true });
  if (!(await filter.isVisible()))
    await page
      .getByRole("button", { name: "Filter and sort tests", exact: true })
      .click();
  await expect(filter).toBeVisible();
  return filter;
}

export async function selectReportFilter(page: Page, name: string) {
  await (await openReportFilters(page)).click();
  await page.getByRole("option", { name, exact: true }).click();
}

export async function expectReportFilter(page: Page, name: string) {
  await expect(await openReportFilters(page)).toContainText(name);
  await page.keyboard.press("Escape");
}
