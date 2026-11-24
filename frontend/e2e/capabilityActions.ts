import { expect, type Locator } from "@playwright/test";

/** Open the saved field through its visible summary before editing inputs. */
export async function ensureCapabilityFieldOpen(group: Locator) {
  const trigger = group.getByRole("button", { name: /^Edit / });
  await expect(trigger).toBeVisible();
  if ((await trigger.getAttribute("aria-expanded")) === "false")
    await trigger.click();
  await expect(trigger).toHaveAttribute("aria-expanded", "true");
}
