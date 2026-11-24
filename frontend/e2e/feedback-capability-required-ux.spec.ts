import { expect, test } from "@playwright/test";
import type { CapabilitySubmission } from "../src/types";

test("required markers follow declaration type without requiring both range bounds", async ({
  page,
  request,
}) => {
  expect(
    (
      await request.post("/api/workspace/reset", {
        data: { acknowledged: true },
      })
    ).ok(),
  ).toBeTruthy();
  await page.goto("/my-lab");
  await page
    .getByRole("button", { name: "Add a test", exact: true })
    .first()
    .click();
  const dialog = page.getByRole("dialog");
  await dialog
    .getByRole("button", { name: "Create custom test", exact: true })
    .click();
  await dialog.getByLabel(/^Custom test name/).fill("Required-marker fixture");
  await dialog
    .getByRole("button", { name: "Add condition", exact: true })
    .click();
  const group = dialog.getByRole("group", {
    name: "Conditions field 1",
    exact: true,
  });
  await group.getByLabel("Field", { exact: true }).click();
  await page.getByRole("option", { name: "Other field", exact: true }).click();
  await group.getByLabel("Field name", { exact: true }).fill("Fixture setting");
  const text = group.getByLabel("Declared capability", { exact: true });
  await expect(text).toHaveAttribute("required", "");
  await group.getByLabel("Availability", { exact: true }).click();
  await page
    .getByRole("option", { name: "Not yet assessed", exact: true })
    .click();
  await expect(text).not.toHaveAttribute("required");
  await group.getByLabel("Value type", { exact: true }).click();
  await page
    .getByRole("option", { name: "Fixed value / resolution", exact: true })
    .click();
  const value = group.getByLabel("Value", { exact: true });
  await expect(value).not.toHaveAttribute("required");
  await group.getByLabel("Availability", { exact: true }).click();
  await page.getByRole("option", { name: "Available", exact: true }).click();
  await expect(value).toHaveAttribute("required", "");
  await group.getByLabel("Value type", { exact: true }).click();
  await page
    .getByRole("option", { name: "Specific numeric settings", exact: true })
    .click();
  await expect(
    group.getByLabel("Specific values", { exact: true }),
  ).toHaveAttribute("required", "");
  await group.getByLabel("Value type", { exact: true }).click();
  await page
    .getByRole("option", { name: "Numeric range", exact: true })
    .click();
  await expect(
    group.getByText("Range * — enter a minimum, a maximum, or both.", {
      exact: true,
    }),
  ).toBeVisible();
  const minimum = group.getByLabel("Minimum", { exact: true });
  const maximum = group.getByLabel("Maximum", { exact: true });
  await expect(minimum).not.toHaveAttribute("required");
  await expect(maximum).not.toHaveAttribute("required");
  await minimum.fill("10");
  let submitted: CapabilitySubmission | null = null;
  page.on("request", (sent) => {
    if (
      sent.method() === "POST" &&
      new URL(sent.url()).pathname === "/api/lab/capabilities"
    )
      submitted = sent.postDataJSON();
  });
  await dialog
    .getByRole("button", { name: "Save capability", exact: true })
    .click();
  await expect(dialog).not.toBeVisible();
  expect(submitted!.capability.atomic_claims?.[0]).toMatchObject({
    operator: "range",
    minimum: "10",
    maximum: null,
    availability: "supported",
  });
});

test("unknown numeric declarations and included boolean resources still save without invented values", async ({
  page,
  request,
}) => {
  expect(
    (
      await request.post("/api/workspace/reset", {
        data: { acknowledged: true },
      })
    ).ok(),
  ).toBeTruthy();
  await page.goto("/my-lab");
  await page
    .getByRole("button", { name: "Add a test", exact: true })
    .first()
    .click();
  const dialog = page.getByRole("dialog");
  await dialog
    .getByRole("button", { name: "Create custom test", exact: true })
    .click();
  await dialog
    .getByLabel(/^Custom test name/)
    .fill("Unknown capability fixture");
  await dialog
    .getByRole("button", { name: "Add condition", exact: true })
    .click();
  const first = dialog.getByRole("group", {
    name: "Conditions field 1",
    exact: true,
  });
  await first.getByLabel("Field", { exact: true }).click();
  await page
    .getByRole("option", { name: "Water temperature", exact: true })
    .click();
  await first.getByLabel("Availability", { exact: true }).click();
  await page
    .getByRole("option", { name: "Not yet assessed", exact: true })
    .click();
  await expect(first.getByText(/Range is optional/)).toBeVisible();
  await dialog
    .getByRole("button", { name: "Add condition", exact: true })
    .click();
  const second = dialog.getByRole("group", {
    name: "Conditions field 2",
    exact: true,
  });
  await second.getByLabel("Field", { exact: true }).click();
  await page
    .getByRole("option", { name: "Reference / master panel", exact: true })
    .click();
  await expect(
    second.getByLabel("Declared capability", { exact: true }),
  ).toHaveCount(0);
  let submitted: CapabilitySubmission | null = null;
  page.on("request", (sent) => {
    if (
      sent.method() === "POST" &&
      new URL(sent.url()).pathname === "/api/lab/capabilities"
    )
      submitted = sent.postDataJSON();
  });
  await dialog
    .getByRole("button", { name: "Save capability", exact: true })
    .click();
  await expect(dialog).not.toBeVisible();
  expect(submitted!.capability.atomic_claims).toEqual(
    expect.arrayContaining([
      expect.objectContaining({
        parameter_id: "water_temperature",
        availability: "unknown",
        operator: "required",
        text_value: "not yet assessed",
        minimum: null,
        maximum: null,
      }),
      expect.objectContaining({
        parameter_id: "reference_panel",
        availability: "supported",
        operator: "required",
        text_value: "available",
      }),
    ]),
  );
});
