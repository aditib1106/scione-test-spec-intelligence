import { expect, test } from "@playwright/test";
import type { SpecificationData, Workspace } from "../src/types";

test.use({ actionTimeout: 15_000 });

test("PDF review keeps primary actions visible and secondary operations available without mutating source data", async ({
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
  const saved = (await (
    await request.get(`/api/specifications/${id}`)
  ).json()) as SpecificationData;
  const source = structuredClone(saved);
  const requirement = source.run!.extraction.requirements[0];
  const reason =
    "The alternative method needs clarification against the customer PDF.";
  source.review!.decisions = {};
  source.assessments[requirement.id] = {
    state: "needs_attention",
    reasons: [reason],
  };
  await page.route(`**/api/specifications/${id}`, (route) =>
    route.fulfill({ json: source }),
  );
  let writes = 0;
  page.on("request", (sent) => {
    if (!["GET", "HEAD", "OPTIONS"].includes(sent.method())) writes++;
  });
  await page.goto(
    `/specifications?document=${id}&requirement=${requirement.id}`,
  );
  await expect(
    page.getByRole("button", { name: "Confirm & next", exact: true }),
  ).toBeVisible();
  await expect(
    page.getByRole("button", {
      name: "Correct extracted content",
      exact: true,
    }),
  ).toBeVisible();
  await expect(
    page.getByRole("button", { name: "Add missing test", exact: true }),
  ).toHaveCount(0);
  await expect(
    page.getByRole("button", { name: "Remove", exact: true }),
  ).toHaveCount(0);
  await expect(
    page.getByRole("button", { name: "Batch review", exact: true }),
  ).toHaveCount(0);
  const evidence = page.getByRole("button", {
    name: "View extraction evidence",
    exact: true,
  });
  await expect(evidence).toHaveAttribute("aria-expanded", "false");
  await expect(page.getByText(reason, { exact: true })).not.toBeVisible();
  await evidence.click();
  await expect(page.getByText(reason, { exact: true })).toBeVisible();
  await expect(
    page.getByText(requirement.evidence[0].quote, { exact: true }).first(),
  ).toBeVisible();
  await evidence.click();

  const more = page.getByRole("button", {
    name: "More review actions",
    exact: true,
  });
  await more.click();
  await page
    .getByRole("menuitem", { name: "Add missing test", exact: true })
    .click();
  const add = page.getByRole("dialog", {
    name: "Add missing test",
    exact: true,
  });
  await expect(add).toBeVisible();
  await add.getByLabel(/^Test name/).fill("Unsaved source-backed test");
  await add.getByRole("button", { name: "Cancel", exact: true }).click();
  await expect(add).not.toBeVisible();

  await more.click();
  await page
    .getByRole("menuitem", { name: "Review selected tests", exact: true })
    .click();
  const batch = page.getByRole("dialog", {
    name: "Review selected tests",
    exact: true,
  });
  await expect(batch).toBeVisible();
  await batch
    .getByRole("button", { name: "Close batch review", exact: true })
    .click();
  await more.click();
  await page
    .getByRole("menuitem", { name: "Remove selected test", exact: true })
    .click();
  const remove = page.getByRole("dialog", {
    name: "Remove this test?",
    exact: true,
  });
  await expect(remove).toBeVisible();
  await remove.getByRole("button", { name: "Cancel", exact: true }).click();
  await expect(remove).not.toBeVisible();

  await page
    .getByRole("button", { name: "Document actions", exact: true })
    .click();
  await expect(
    page.getByRole("menuitem", { name: "Analysis settings", exact: true }),
  ).toBeVisible();
  await expect(
    page.getByRole("menuitem", { name: "OCR · coming soon", exact: true }),
  ).toBeDisabled();
  await page.keyboard.press("Escape");

  const sourceAccordion = page.getByRole("button", {
    name: /^Source PDF preview/,
  });
  if (
    (await sourceAccordion.isVisible()) &&
    (await sourceAccordion.getAttribute("aria-expanded")) === "false"
  )
    await sourceAccordion.click();
  await page
    .getByRole("button", { name: "PDF view options", exact: true })
    .first()
    .click();
  await page.getByRole("menuitem", { name: "Fit page", exact: true }).click();
  await page
    .getByRole("button", { name: "Fit width", exact: true })
    .first()
    .click();
  await expect(
    page.getByRole("button", { name: "Fit width", exact: true }).first(),
  ).toHaveAttribute("aria-pressed", "true");
  const image = page.getByRole("img", { name: /^PDF page/ }).first();
  await expect(image).toBeVisible();
  await expect
    .poll(() => image.evaluate((node: HTMLImageElement) => node.naturalWidth))
    .toBeGreaterThan(0);
  if (testInfo.project.name === "desktop") {
    await page.evaluate(() => window.scrollTo(0, 0));
    await page.screenshot({
      path: testInfo.outputPath("simplified-source-review.png"),
      animations: "disabled",
    });
  }
  expect(writes).toBe(0);
  const after = (await (
    await request.get(`/api/specifications/${id}`)
  ).json()) as SpecificationData;
  expect(after.run).toEqual(saved.run);
  expect(after.facts_version).toBe(saved.facts_version);
  expect(after.review).toEqual(saved.review);
});

test("a cancelled add-missing request stays consumed when returning to its source document", async ({
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
  const receipt = await request.post("/api/specifications/worked-example");
  expect(receipt.ok()).toBeTruthy();
  const { document_id: id } = await receipt.json();
  const original = (await (
    await request.get(`/api/specifications/${id}`)
  ).json()) as SpecificationData;
  const workspace = (await (
    await request.get("/api/workspace")
  ).json()) as Workspace;
  const current = workspace.documents.find(
    (entry) => entry.document_id === id,
  )!;
  const otherId = "source-menu-context-b";
  const other = {
    ...current,
    document_id: otherId,
    original_name: "second-source-context.pdf",
  };
  await page.route("**/api/workspace", (route) =>
    route.fulfill({
      json: { ...workspace, documents: [...workspace.documents, other] },
    }),
  );
  await page.route(`**/api/specifications/${otherId}`, (route) =>
    route.fulfill({
      json: {
        ...original,
        document_id: otherId,
        original_name: other.original_name,
      },
    }),
  );
  await page.route(`**/api/specifications/${otherId}/operations`, (route) =>
    route.fulfill({ json: { state: "idle", notice: null } }),
  );
  await page.route(
    `**/api/specifications/${otherId}/pages/*?*`,
    async (route) => {
      const response = await route.fetch({
        url: route
          .request()
          .url()
          .replace(`/specifications/${otherId}/`, `/specifications/${id}/`),
      });
      await route.fulfill({ response });
    },
  );
  let writes = 0;
  page.on("request", (sent) => {
    if (!["GET", "HEAD", "OPTIONS"].includes(sent.method())) writes++;
  });
  const row = original.run!.extraction.requirements[0];
  await page.goto(`/specifications?document=${id}&requirement=${row.id}`);
  const more = page.getByRole("button", {
    name: "More review actions",
    exact: true,
  });
  await more.click();
  await page
    .getByRole("menuitem", { name: "Add missing test", exact: true })
    .click();
  const add = page.getByRole("dialog", {
    name: "Add missing test",
    exact: true,
  });
  await expect(add).toBeVisible();
  await add.getByRole("button", { name: "Cancel", exact: true }).click();
  await expect(add).not.toBeVisible();
  const picker = page.getByRole("combobox", {
    name: "Saved specification",
    exact: true,
  });
  await picker.click();
  await page
    .getByRole("option", { name: other.original_name, exact: true })
    .click();
  await expect(picker).toHaveValue(other.original_name);
  await expect(
    page.getByRole("button", {
      name: "Correct extracted content",
      exact: true,
    }),
  ).toBeVisible();
  const returnedRead = page.waitForResponse(
    (response) =>
      new URL(response.url()).pathname === `/api/specifications/${id}`,
  );
  await page.goBack();
  expect((await returnedRead).ok()).toBeTruthy();
  await expect(picker).toHaveValue(current.original_name);
  await expect(
    page.getByRole("button", {
      name: "Correct extracted content",
      exact: true,
    }),
  ).toBeVisible();
  await expect(page.getByRole("dialog")).toHaveCount(0);
  // Consuming an old request does not disable future explicit opens.
  await more.click();
  await page
    .getByRole("menuitem", { name: "Add missing test", exact: true })
    .click();
  await expect(add).toBeVisible();
  await add.getByRole("button", { name: "Cancel", exact: true }).click();
  expect(writes).toBe(0);
  const after = (await (
    await request.get(`/api/specifications/${id}`)
  ).json()) as SpecificationData;
  expect(after.run).toEqual(original.run);
  expect(after.review).toEqual(original.review);
});
