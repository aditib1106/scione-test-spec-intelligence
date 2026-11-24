import { expect, test } from "@playwright/test";

test.use({ actionTimeout: 15_000 });

for (const viewport of [
  { width: 1366, height: 768 },
  { width: 1280, height: 620 },
]) {
  test(`comparison and review remain usable on a ${viewport.width}×${viewport.height} laptop`, async ({
    page,
    request,
  }, testInfo) => {
    test.skip(
      testInfo.project.name !== "desktop",
      "Explicit laptop viewport check.",
    );
    await page.setViewportSize(viewport);
    expect(
      (
        await request.post("/api/workspace/reset", {
          data: { acknowledged: true },
        })
      ).ok(),
    ).toBeTruthy();
    const response = await request.post("/api/specifications/worked-example");
    expect(response.ok()).toBeTruthy();
    const { document_id: id } = await response.json();
    let modelCalls = 0;
    page.on("request", (sent) => {
      if (
        sent.method() === "POST" &&
        /\/(analysis|matching)$/.test(new URL(sent.url()).pathname)
      )
        modelCalls++;
    });
    await page.goto(`/reports?document=${id}`);
    const comparison = page.getByRole("table", {
      name: "Test capability comparison",
      exact: true,
    });
    const review = page.getByRole("region", {
      name: "Your assessment review",
      exact: true,
    });
    const list = page.getByRole("navigation", {
      name: "Requested tests",
      exact: true,
    });
    await expect(comparison).toBeVisible();
    await expect(
      review.getByRole("button", { name: "Accept assessment", exact: true }),
    ).toBeVisible();
    const body = await comparison.boundingBox();
    const items = await list.boundingBox();
    const controls = await review.boundingBox();
    expect(body!.y).toBeLessThan(350);
    expect(body!.width).toBeGreaterThan(items!.width * 2);
    expect(controls!.y + controls!.height).toBeLessThanOrEqual(
      viewport.height + 2,
    );
    // Reading an expanded comparison must not push the decision controls below the viewport.
    const pageScrollBeforeExpansion = await page.evaluate(() => scrollY);
    await page.getByRole("button", { name: "Conditions", exact: true }).click();
    await page.getByTestId("report-detail-pane").evaluate((node) => {
      node.scrollTop = node.scrollHeight;
    });
    const after = await review.boundingBox();
    // Short cards follow their content; expansion may move the footer until it
    // reaches the viewport bound, without scrolling the whole workspace.
    expect(after!.y + after!.height).toBeLessThanOrEqual(viewport.height + 2);
    expect(await page.evaluate(() => scrollY)).toBe(pageScrollBeforeExpansion);
    await expect(
      review.getByRole("button", { name: "Review history", exact: true }),
    ).toBeVisible();
    await expect(
      review.getByRole("button", {
        name: "Customer clarifications",
        exact: true,
      }),
    ).toBeVisible();
    expect(modelCalls).toBe(0);
    await page.getByTestId("report-detail-pane").evaluate((node) => {
      node.scrollTop = 0;
    });
    await page.screenshot({
      path: testInfo.outputPath("report-workbench.png"),
      animations: "disabled",
    });
  });
}
