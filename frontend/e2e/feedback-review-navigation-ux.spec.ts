import { expect, test } from "@playwright/test";
import type { SpecificationData } from "../src/types";

test("previous and next pending navigation skip confirmed tests without saving decisions", async ({
  page,
  request,
}, testInfo) => {
  expect(
    (
      await request.post("/api/workspace/reset", {
        data: { acknowledged: true },
      })
    ).ok(),
  ).toBeTruthy();
  const receipt = await request.post("/api/specifications/worked-example");
  expect(receipt.ok()).toBeTruthy();
  const { document_id: id } = await receipt.json();
  const source = (await (
    await request.get(`/api/specifications/${id}`)
  ).json()) as SpecificationData;
  const rows = source.run!.extraction.requirements;
  expect(rows.length).toBeGreaterThanOrEqual(3);
  source.review!.decisions = {};
  for (const row of rows)
    source.assessments[row.id] = { state: "needs_attention", reasons: [] };
  const confirmed = rows[1];
  source.review!.decisions[confirmed.id] = {
    requirement_id: confirmed.id,
    status: "confirmed",
    note: "Already reviewed",
    updated_at: "2026-11-24T12:00:00Z",
  };
  await page.route(`**/api/specifications/${id}`, (route) =>
    route.fulfill({ json: source }),
  );
  let writes = 0;
  page.on("request", (sent) => {
    if (!["GET", "HEAD", "OPTIONS"].includes(sent.method())) writes++;
  });
  const pending = rows.filter((row) => row.id !== confirmed.id);
  await page.goto(
    `/specifications?document=${id}&requirement=${pending[0].id}`,
  );
  const selected = page.getByRole("combobox", {
    name: "Selected test",
    exact: true,
  });
  const previous = page.getByRole("button", {
    name: "Previous pending test",
    exact: true,
  });
  const next = page.getByRole("button", {
    name: "Next pending test",
    exact: true,
  });
  await expect(previous).toBeDisabled();
  await expect(next).toBeEnabled();
  await next.click();
  await expect(selected).toContainText(pending[1].original_test_name);
  await expect(page).toHaveURL(new RegExp(`requirement=${pending[1].id}`));
  await expect(
    page.getByText(`Pending item 2 of ${pending.length}`),
  ).toBeVisible();
  await expect(previous).toBeEnabled();
  await previous.click();
  await expect(selected).toContainText(pending[0].original_test_name);
  await expect(previous).toBeDisabled();
  for (let index = 1; index < pending.length; index++) await next.click();
  await expect(selected).toContainText(pending.at(-1)!.original_test_name);
  await expect(next).toBeDisabled();
  if (testInfo.project.name === "desktop") {
    const sourceImage = page.getByRole("img", {
      name: /^PDF page \d+/,
    });
    await expect(sourceImage).toBeVisible();
    await expect
      .poll(() =>
        sourceImage.evaluate((node: HTMLImageElement) => node.naturalWidth),
      )
      .toBeGreaterThan(0);
    await page.screenshot({
      path: testInfo.outputPath("pending-navigation-controls.png"),
      fullPage: false,
    });
  }

  await page.getByRole("button", { name: "All", exact: true }).click();
  await selected.click();
  await page
    .getByRole("option", { name: confirmed.original_test_name, exact: true })
    .click();
  await expect(selected).toContainText(confirmed.original_test_name);
  await expect(next).toBeEnabled();
  await next.click();
  await expect(selected).toContainText(pending[1].original_test_name);
  await expect(
    page.getByRole("button", { name: "Pending", exact: true }),
  ).toHaveClass(/MuiButton-contained/);
  expect(source.review!.decisions[confirmed.id].status).toBe("confirmed");
  expect(Object.keys(source.review!.decisions)).toEqual([confirmed.id]);
  expect(writes).toBe(0);
});
