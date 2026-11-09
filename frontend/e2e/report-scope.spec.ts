import { expect, test } from "@playwright/test";
import type { ComparisonData } from "../src/comparisonTypes";
import type { LabProfile, SpecificationData } from "../src/types";

test("removing and restoring report scope persists without changing source extraction", async ({
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
  const initial = (await (await request.get("/api/lab")).json()) as LabProfile;
  expect(
    (
      await request.post("/api/lab/example", {
        data: { version: initial.version, example: "csp301" },
      })
    ).ok(),
  ).toBeTruthy();
  const imported = await request.post("/api/specifications/worked-example");
  expect(imported.ok()).toBeTruthy();
  const { document_id: id } = (await imported.json()) as {
    document_id: string;
  };
  const before = (await (
    await request.get(`/api/specifications/${id}`)
  ).json()) as SpecificationData;
  await page.goto(`/reports?document=${id}`);
  const selected = page
    .getByRole("navigation", { name: "Requested tests" })
    .getByRole("button", { name: "Specular gloss", exact: true });
  await expect(selected).toBeVisible();
  await selected.click();
  await page
    .getByRole("button", { name: "Remove from this report", exact: true })
    .click();
  let dialog = page.getByRole("dialog");
  await dialog
    .getByRole("textbox", { name: "Scope note (optional)" })
    .fill("Customer removed this test from the requested quote.");
  await dialog
    .getByRole("button", { name: "Remove test", exact: true })
    .click();
  await expect(dialog).not.toBeVisible();
  await expect(selected).toHaveAttribute("aria-current", "true");
  await expect(
    page.getByRole("button", { name: "Restore to this report", exact: true }),
  ).toBeVisible();
  await expect(
    page.getByText("Test removed from this report. You can restore it here."),
  ).toBeVisible();
  const removed = (await (
    await request.get(`/api/comparisons/${id}`)
  ).json()) as ComparisonData;
  expect(removed.summary.excluded).toBe(1);
  const removedTest = removed.requirements.find(
    (item) => item.test_name === "Specular gloss",
  )!;
  expect(removedTest.comparison.status).toBe("excluded");
  expect(removedTest.report_scope_exclusion?.note).toBe(
    "Customer removed this test from the requested quote.",
  );
  const after = (await (
    await request.get(`/api/specifications/${id}`)
  ).json()) as SpecificationData;
  expect(after.run).toEqual(before.run);
  expect(after.review).toEqual(before.review);

  await page.reload();
  await selected.click();
  await expect(
    page.getByRole("button", { name: "Restore to this report", exact: true }),
  ).toBeVisible();
  await page
    .getByRole("button", { name: "Restore to this report", exact: true })
    .click();
  dialog = page.getByRole("dialog");
  await dialog
    .getByRole("button", { name: "Restore test", exact: true })
    .click();
  await expect(dialog).not.toBeVisible();
  await expect(selected).toHaveAttribute("aria-current", "true");
  await expect(
    page.getByRole("button", { name: "Remove from this report", exact: true }),
  ).toBeVisible();
  const restored = (await (
    await request.get(`/api/comparisons/${id}`)
  ).json()) as ComparisonData;
  expect(restored.summary.excluded).toBe(0);
  expect(
    restored.requirements.find(
      (item) => item.requirement_id === removedTest.requirement_id,
    )?.report_scope_exclusion,
  ).toBeNull();
  expect(
    restored.requirements.find(
      (item) => item.requirement_id === removedTest.requirement_id,
    )?.comparison.status,
  ).not.toBe("excluded");
});
