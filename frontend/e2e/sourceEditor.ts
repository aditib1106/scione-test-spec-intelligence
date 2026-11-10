import { expect, type Page } from "@playwright/test";

/** Follow the single user-facing edit action, then choose the intended field. */
export async function editRequirement(
  page: Page,
  section: "Conditions" | "Criteria" | "Sample plan",
  label: string,
) {
  await page
    .getByRole("button", { name: "Correct extracted content", exact: true })
    .click();
  const dialog = page.getByRole("dialog", {
    name: "Correct extracted content",
    exact: true,
  });
  await expect(dialog).toBeVisible();
  await dialog.getByRole("tab", { name: section, exact: true }).click();
  await dialog.getByLabel("Choose requirement", { exact: true }).click();
  const option = page
    .getByRole("option")
    .filter({
      has: page.getByText(label, { exact: true }),
    })
    .first();
  await option.click();
  await expect(page.getByRole("listbox")).toHaveCount(0);
  return dialog;
}
