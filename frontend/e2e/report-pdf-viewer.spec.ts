import { expect, test } from "@playwright/test";

test("report PDF fits a complete page and supports width and expanded reading", async ({
  page,
  request,
}, info) => {
  const imported = await request.post("/api/specifications/worked-example");
  expect(imported.ok()).toBeTruthy();
  const { document_id: id } = await imported.json();
  await page.goto(`/reports?document=${id}`);
  await page.getByRole("tab", { name: "PDF & download", exact: true }).click();
  const viewport = page
    .getByRole("region", { name: "Report preview scroll area", exact: true })
    .first();
  const image = page
    .getByRole("img", { name: "Gap report page 1", exact: true })
    .first();
  const fitPage = page
    .getByRole("button", { name: "Fit page", exact: true })
    .first();
  await expect(image).toBeVisible();
  await expect(fitPage).toHaveAttribute("aria-pressed", "true");
  await expect
    .poll(async () => {
      const bounds = await image.boundingBox();
      return (
        bounds!.height <=
        (await viewport.evaluate((node) => node.clientHeight)) - 8
      );
    })
    .toBeTruthy();
  if (info.project.name === "desktop")
    expect(
      (await image.boundingBox())!.y + (await image.boundingBox())!.height,
    ).toBeLessThanOrEqual(await page.evaluate(() => window.innerHeight));
  await page.screenshot({
    path: info.outputPath("report-pdf-fit-page.png"),
    fullPage: true,
    animations: "disabled",
  });
  await page
    .getByRole("button", { name: "Fit width", exact: true })
    .first()
    .click();
  await expect
    .poll(async () =>
      Math.abs(
        (await image.boundingBox())!.width -
          (await viewport.evaluate((node) => node.clientWidth)) +
          (info.project.name === "narrow" ? 8 : 16),
      ),
    )
    .toBeLessThan(3);
  await fitPage.click();
  await page.getByRole("button", { name: "Expand PDF", exact: true }).click();
  const dialog = page.getByRole("dialog");
  await expect(
    dialog.getByRole("button", { name: "Fit page", exact: true }),
  ).toHaveAttribute("aria-pressed", "true");
  const expandedImage = dialog.getByRole("img", {
    name: "Gap report page 1",
    exact: true,
  });
  const expandedArea = dialog.getByRole("region", {
    name: "Report preview scroll area",
    exact: true,
  });
  await expect
    .poll(
      async () =>
        (await expandedImage.boundingBox())!.height <=
        (await expandedArea.evaluate((node) => node.clientHeight)) - 8,
    )
    .toBeTruthy();
  await dialog
    .getByRole("button", { name: "Close expanded PDF", exact: true })
    .click();
  await expect(dialog).not.toBeVisible();
  expect(
    await page.evaluate(
      () =>
        document.documentElement.scrollWidth <=
        document.documentElement.clientWidth + 1,
    ),
  ).toBeTruthy();
});
