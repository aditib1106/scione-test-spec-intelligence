import {
  expect,
  test,
  type APIRequestContext,
  type Page,
} from "@playwright/test";
import type { ComparisonCheck, ComparisonData } from "../src/comparisonTypes";

async function fixture(page: Page, request: APIRequestContext) {
  expect(
    (
      await request.post("/api/workspace/reset", {
        data: { acknowledged: true },
      })
    ).ok(),
  ).toBeTruthy();
  const created = await request.post("/api/specifications/worked-example");
  expect(created.ok()).toBeTruthy();
  const { document_id: id } = await created.json();
  const report = (await (
    await request.get(`/api/comparisons/${id}`)
  ).json()) as ComparisonData;
  const target = report.requirements[0];
  target.test_name = "Water-jet review layout fixture";
  target.source_review_state = "ready";
  target.comparison.status = "partially_matched";
  const condition: ComparisonCheck = {
    label: "Temperature",
    parameter_id: "temperature",
    kind: "condition",
    stage: "testing",
    unit: null,
    requested: "25 °C",
    declared: "20–70 °C",
    status: "supported",
    reason:
      "Offline layout fixture: a recorded operating range covers this setting.",
    source_pages: [1],
  };
  target.comparison.checks = [
    {
      ...condition,
      label: "Test / method",
      parameter_id: "test_method",
      kind: "method",
      requested: target.methods.join(" · "),
      declared: target.methods.join(" · "),
    },
    ...Array.from({ length: 60 }, (_, index) => ({
      ...condition,
      label: `Operating condition ${index + 1}`,
      parameter_id: `layout_fixture_${index + 1}`,
    })),
    {
      ...condition,
      label: "Reported rating",
      parameter_id: "rating",
      kind: "criterion",
      requested: "≤ 1",
      declared: "0–5",
    },
  ];
  report.requirements = [target];
  report.summary = { partially_matched: 1 };
  await page.route(`**/api/comparisons/${id}`, (route) =>
    route.fulfill({ json: report }),
  );
  return { id, target };
}

for (const viewport of [
  { width: 1440, height: 1000, narrow: false },
  { width: 1366, height: 768, narrow: false },
  { width: 2560, height: 1440, narrow: false },
  { width: 390, height: 844, narrow: true },
]) {
  test(`review follows collapsed content and remains reachable at ${viewport.width}×${viewport.height}`, async ({
    page,
    request,
  }, info) => {
    test.skip(
      (info.project.name === "narrow") !== viewport.narrow,
      "Explicit viewport coverage.",
    );
    await page.setViewportSize({
      width: viewport.width,
      height: viewport.height,
    });
    const { id, target } = await fixture(page, request);
    let writes = 0;
    page.on("request", (sent) => {
      if (!["GET", "HEAD", "OPTIONS"].includes(sent.method())) writes++;
    });
    await page.goto(`/reports?document=${id}`);
    const pane = page.getByTestId("report-detail-pane");
    const panel = page.getByTestId("report-comparison-panel");
    const comparison = pane.getByRole("table", {
      name: "Test capability comparison",
      exact: true,
    });
    const review = page.getByRole("region", {
      name: "Your assessment review",
      exact: true,
    });
    const conditions = pane.getByRole("button", {
      name: "Conditions",
      exact: true,
    });
    const specification = pane.getByRole("button", {
      name: "Test specification",
      exact: true,
    });
    await expect(comparison).toBeVisible();
    await expect(conditions).toHaveAttribute("aria-expanded", "false");
    await expect(specification).toHaveAttribute("aria-expanded", "false");
    await expect(
      review.getByRole("button", { name: "Accept assessment", exact: true }),
    ).toBeVisible();
    const tableBox = (await comparison.boundingBox())!;
    const reviewBox = (await review.boundingBox())!;
    const panelBox = (await panel.boundingBox())!;
    // A short report must not grow a blank internal region before its actions.
    expect(reviewBox.y - (tableBox.y + tableBox.height)).toBeGreaterThanOrEqual(
      -2,
    );
    expect(reviewBox.y - (tableBox.y + tableBox.height)).toBeLessThanOrEqual(4);
    expect(
      panelBox.y + panelBox.height - (reviewBox.y + reviewBox.height),
    ).toBeLessThanOrEqual(3);
    if (!viewport.narrow) {
      expect(reviewBox.y + reviewBox.height).toBeLessThanOrEqual(
        viewport.height + 2,
      );
      expect(panelBox.height).toBeLessThan(650);
      await page.screenshot({
        path: info.outputPath("report-collapsed-compact.png"),
        animations: "disabled",
      });
    }
    await conditions.scrollIntoViewIfNeeded();
    const pageY = await page.evaluate(() => scrollY);
    await conditions.click();
    await expect(conditions).toHaveAttribute("aria-expanded", "true");
    await expect(
      pane.getByRole("heading", { name: target.test_name, exact: true }),
    ).toBeVisible();
    if (!viewport.narrow) {
      await expect
        .poll(() =>
          pane.evaluate((node) => node.scrollHeight - node.clientHeight),
        )
        .toBeGreaterThan(500);
      expect(await page.evaluate(() => scrollY)).toBe(pageY);
      const expandedReview = (await review.boundingBox())!;
      expect(expandedReview.y + expandedReview.height).toBeLessThanOrEqual(
        viewport.height + 2,
      );
      await pane.evaluate((node) => {
        node.scrollTop = Math.round(
          (node.scrollHeight - node.clientHeight) / 2,
        );
      });
    } else {
      await review.scrollIntoViewIfNeeded();
    }
    await expect(
      review.getByRole("button", { name: "Edit decision", exact: true }),
    ).toBeInViewport();
    const scrollBeforeDialog = await pane.evaluate((node) => node.scrollTop);
    await review
      .getByRole("button", { name: "Review history", exact: true })
      .click();
    const history = page.getByRole("dialog", {
      name: "Review history",
      exact: true,
    });
    await expect(history).toBeVisible();
    await history
      .getByRole("button", { name: "Close review history", exact: true })
      .click();
    await expect(history).not.toBeVisible();
    expect(await pane.evaluate((node) => node.scrollTop)).toBe(
      scrollBeforeDialog,
    );
    await expect(conditions).toHaveAttribute("aria-expanded", "true");
    await expect(
      pane.getByRole("heading", { name: target.test_name, exact: true }),
    ).toBeVisible();
    expect(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= innerWidth + 1,
      ),
    ).toBeTruthy();
    expect(writes).toBe(0);
    await page.screenshot({
      path: info.outputPath("report-expanded-reachable-actions.png"),
      animations: "disabled",
    });
  });
}
