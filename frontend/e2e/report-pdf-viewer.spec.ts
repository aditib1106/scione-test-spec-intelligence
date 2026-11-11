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

test("PDF zoom keeps a fitting page centered and preserves the viewed point while panning", async ({
  page,
  request,
}) => {
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
  await expect(image).toBeVisible();
  const position = async () =>
    image.evaluate((element) => {
      const area = element.closest('[role="region"]') as HTMLElement;
      const imageBounds = element.getBoundingClientRect();
      const areaBounds = area.getBoundingClientRect();
      const center = areaBounds.left + area.clientLeft + area.clientWidth / 2;
      return {
        horizontal: (center - imageBounds.left) / imageBounds.width,
        vertical:
          (areaBounds.top +
            area.clientTop +
            area.clientHeight / 2 -
            imageBounds.top) /
          imageBounds.height,
        centerOffset: imageBounds.left + imageBounds.width / 2 - center,
        fitting: imageBounds.width < area.clientWidth - 16,
      };
    });
  await page
    .getByRole("button", { name: "Zoom in", exact: true })
    .first()
    .click();
  if ((await position()).fitting) {
    await expect
      .poll(async () => Math.abs((await position()).centerOffset))
      .toBeLessThan(3);
  }
  await page
    .getByRole("button", { name: "Fit width", exact: true })
    .first()
    .click();
  for (let count = 0; count < 3; count += 1) {
    await page
      .getByRole("button", { name: "Zoom in", exact: true })
      .first()
      .click();
  }
  await expect
    .poll(() =>
      viewport.evaluate((element) => element.scrollWidth - element.clientWidth),
    )
    .toBeGreaterThan(40);
  await viewport.evaluate((element) => {
    element.scrollLeft = (element.scrollWidth - element.clientWidth) * 0.35;
    element.scrollTop = (element.scrollHeight - element.clientHeight) * 0.4;
  });
  const before = await position();
  await page
    .getByRole("button", { name: "Zoom in", exact: true })
    .first()
    .click();
  await expect
    .poll(async () =>
      Math.abs((await position()).horizontal - before.horizontal),
    )
    .toBeLessThan(0.01);
  await expect
    .poll(async () => Math.abs((await position()).vertical - before.vertical))
    .toBeLessThan(0.01);
  await page
    .getByRole("button", { name: "Zoom out", exact: true })
    .first()
    .click();
  await expect
    .poll(async () =>
      Math.abs((await position()).horizontal - before.horizontal),
    )
    .toBeLessThan(0.01);
  await expect
    .poll(async () => Math.abs((await position()).vertical - before.vertical))
    .toBeLessThan(0.01);
});
