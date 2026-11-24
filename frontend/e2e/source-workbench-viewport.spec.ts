import {
  expect,
  test,
  type APIRequestContext,
  type Page,
} from "@playwright/test";
import type { SpecificationData } from "../src/types";

async function openReview(page: Page, request: APIRequestContext) {
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
  const saved = (await (
    await request.get(`/api/specifications/${id}`)
  ).json()) as SpecificationData;
  const source = structuredClone(saved);
  const target = source.run!.extraction.requirements[0];
  source.review!.decisions = {};
  source.assessments[target.id] = {
    state: "needs_attention",
    reasons: ["Check the source before confirming this extracted test."],
  };
  await page.route(`**/api/specifications/${id}`, (route) =>
    route.fulfill({ json: source }),
  );
  await page.goto(`/specifications?document=${id}&requirement=${target.id}`);
  await expect(
    page.getByRole("button", { name: "Confirm & next", exact: true }),
  ).toBeVisible();
  return { id, saved };
}

for (const viewport of [
  { width: 1366, height: 768 },
  { width: 1280, height: 620 },
]) {
  test(`source workbench uses the ${viewport.width}x${viewport.height} laptop viewport for PDF, extracted details and review actions`, async ({
    page,
    request,
  }, testInfo) => {
    test.skip(
      testInfo.project.name !== "desktop",
      "Explicit desktop laptop dimensions.",
    );
    await page.setViewportSize(viewport);
    const { id, saved } = await openReview(page, request);
    const workbench = page.getByRole("region", {
      name: "Source review workbench",
      exact: true,
    });
    await expect(
      workbench.getByRole("img", { name: /^PDF page/ }),
    ).toBeVisible();
    await expect
      .poll(() =>
        workbench.evaluate((element) => {
          const rectangle = (selector: string) =>
            element.querySelector(selector)!.getBoundingClientRect();
          const bench = element.getBoundingClientRect();
          const pdf = rectangle('[aria-label="Source document scroll area"]');
          const review = rectangle('[aria-label="Extracted test review"]');
          const details = rectangle('[aria-label="Selected test details"]');
          const actions = rectangle('[aria-label="Source review actions"]');
          return (
            bench.top < innerHeight * 0.4 &&
            bench.bottom <= innerHeight - 6 &&
            pdf.height >= 220 &&
            details.height >= 145 &&
            actions.bottom <= innerHeight - 6 &&
            review.width / bench.width > 0.44 &&
            review.width / bench.width < 0.54 &&
            document.documentElement.scrollWidth <= innerWidth + 1
          );
        }),
      )
      .toBe(true);
    await expect(
      page.getByRole("button", { name: "Generate report", exact: true }),
    ).toBeInViewport();
    await expect(
      page.getByRole("button", { name: "Confirm & next", exact: true }),
    ).toBeInViewport();
    await expect(
      page.getByRole("button", {
        name: "Correct extracted content",
        exact: true,
      }),
    ).toBeInViewport();
    // Inspect actual content before scrolling: a large empty container is not
    // evidence that the extracted atomic requirements are readable on a laptop.
    await expect(
      page
        .getByRole("table", { name: "Condition parameters", exact: true })
        .getByRole("row")
        .nth(1),
    ).toBeInViewport({ ratio: 0.75 });
    await page.screenshot({
      path: testInfo.outputPath(
        `source-workbench-initial-${viewport.width}x${viewport.height}.png`,
      ),
      animations: "disabled",
    });
    const details = page.getByRole("region", {
      name: "Selected test details",
      exact: true,
    });
    await details.evaluate((element) => {
      element.scrollTop = element.scrollHeight;
    });
    await expect(
      page.getByRole("button", { name: "Confirm & next", exact: true }),
    ).toBeInViewport();
    await page.screenshot({
      path: testInfo.outputPath(
        `source-workbench-${viewport.width}x${viewport.height}.png`,
      ),
      animations: "disabled",
    });
    const after = (await (
      await request.get(`/api/specifications/${id}`)
    ).json()) as SpecificationData;
    expect(after.run).toEqual(saved.run);
    expect(after.review).toEqual(saved.review);
    expect(after.facts_version).toBe(saved.facts_version);
  });
}

test("narrow source review remains stacked, with source preview and actions reachable without horizontal overflow", async ({
  page,
  request,
}, testInfo) => {
  test.skip(testInfo.project.name !== "narrow", "Mobile stacked layout.");
  await openReview(page, request);
  await expect(
    page.getByRole("region", { name: "Extracted test review", exact: true }),
  ).toBeVisible();
  const preview = page.getByRole("button", { name: /^Source PDF preview/ });
  await preview.click();
  await expect(page.getByRole("img", { name: /^PDF page/ })).toBeVisible();
  await preview.click();
  const actions = page.getByRole("group", {
    name: "Source review actions",
    exact: true,
  });
  await actions.scrollIntoViewIfNeeded();
  await expect(
    actions.getByRole("button", { name: "Confirm & next", exact: true }),
  ).toBeInViewport();
  await expect
    .poll(() =>
      page.evaluate(() => document.documentElement.scrollWidth - innerWidth),
    )
    .toBeLessThan(2);
  await page.screenshot({
    path: testInfo.outputPath("source-workbench-narrow.png"),
    animations: "disabled",
  });
});
