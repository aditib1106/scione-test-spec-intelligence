import { expect, test } from "@playwright/test";

test("developer synthetic example loading requires acknowledgement and preserves documents", async ({
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
  const imported = await request.post("/api/specifications/worked-example");
  expect(imported.ok()).toBeTruthy();
  const beforeDocuments = await (
    await request.get("/api/specifications")
  ).json();
  const before = await (await request.get("/api/lab")).json();
  await page.goto("/developer");
  await page
    .getByRole("button", { name: "Synthetic lab example", exact: true })
    .click();
  await page
    .getByRole("button", { name: "Load detailed lab example", exact: true })
    .click();
  const dialog = page.getByRole("dialog", {
    name: "Load synthetic lab capabilities?",
  });
  await expect(
    dialog.getByRole("button", { name: "Load example", exact: true }),
  ).toBeDisabled();
  await dialog.getByRole("button", { name: "Cancel", exact: true }).click();
  expect(await (await request.get("/api/lab")).json()).toEqual(before);
  await page
    .getByRole("button", { name: "Load detailed lab example", exact: true })
    .click();
  await dialog
    .getByRole("checkbox", {
      name: "Replace my lab capabilities with the synthetic example",
      exact: true,
    })
    .check();
  await dialog
    .getByRole("button", { name: "Load example", exact: true })
    .click();
  await expect(dialog).not.toBeVisible();
  const after = await (await request.get("/api/lab")).json();
  expect(after.version).toBe(before.version + 1);
  expect(after.capabilities).toHaveLength(4);
  expect(
    after.capabilities.reduce(
      (sum: number, cap: { atomic_claims: unknown[] }) =>
        sum + cap.atomic_claims.length,
      0,
    ),
  ).toBe(76);
  const afterDocuments = await (
    await request.get("/api/specifications")
  ).json();
  expect(afterDocuments).toEqual(beforeDocuments);
  const records = await (await request.get("/api/lab/records")).json();
  expect(
    records.revisions.map((row: { version: number }) => row.version),
  ).toContain(before.version);
});
