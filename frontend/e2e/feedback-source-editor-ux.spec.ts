import {
  expect,
  test,
  type APIRequestContext,
  type Page,
} from "@playwright/test";
import type { Requirement, SpecificationData } from "../src/types";

test.use({ actionTimeout: 15_000 });

async function sourceFixture(page: Page, request: APIRequestContext) {
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
  const row = source.run!.extraction.requirements[0];
  await page.goto(`/specifications?document=${id}&requirement=${row.id}`);
  return { id, source, row };
}

test("source evidence shows the real PDF page and keeps unsaved edits when its preview is unavailable", async ({
  page,
  request,
}, testInfo) => {
  const { id, row, source } = await sourceFixture(page, request);
  expect(source.document.page_count).toBeGreaterThan(1);
  let inferencePosts = 0;
  let correctionPosts = 0;
  const previewPages: number[] = [];
  page.on("request", (sent) => {
    const url = new URL(sent.url());
    if (sent.method() === "POST" && /\/(analysis|matching)$/.test(url.pathname))
      inferencePosts++;
    if (
      sent.method() === "POST" &&
      /\/(facts|requirements)$/.test(url.pathname)
    )
      correctionPosts++;
    const pageNumber = url.pathname.match(/\/pages\/(\d+)$/)?.[1];
    if (pageNumber) previewPages.push(Number(pageNumber));
  });
  await page
    .getByRole("button", { name: "Correct extracted content", exact: true })
    .click();
  const editor = page.getByRole("dialog", {
    name: "Correct extracted content",
    exact: true,
  });
  await expect(
    editor.getByRole("tab", { name: "Test specification", exact: true }),
  ).toBeVisible();
  await editor
    .getByRole("tab", { name: "Source evidence", exact: true })
    .click();
  const passage = editor.getByRole("combobox", {
    name: /^Quoted passage(?:\s|$)/,
  });
  await passage.click();
  await page.getByRole("option").first().click();
  const pageField = editor.getByLabel("Source page");
  const originalPage = row.evidence[0].page;
  await pageField.fill(String(originalPage));
  const image = editor.getByRole("img", {
    name: `Source evidence PDF page ${originalPage}`,
    exact: true,
  });
  await expect(image).toBeVisible();
  await expect
    .poll(() => image.evaluate((node: HTMLImageElement) => node.naturalWidth))
    .toBeGreaterThan(0);
  const quotation = editor.getByLabel("Source quotation");
  const unsaved = `${row.evidence[0].quote} Reviewer note still in this draft.`;
  await quotation.fill(unsaved);
  if (testInfo.project.name === "desktop") {
    await editor.screenshot({
      path: testInfo.outputPath("source-evidence-with-original-page.png"),
      animations: "disabled",
    });
    await page.mouse.click(2, 2);
    await expect(editor).toBeVisible();
    await expect(quotation).toHaveValue(unsaved);
  }
  await pageField.fill("0");
  await expect(editor.getByRole("alert")).toContainText(
    `Choose a source page from 1 to ${source.document.page_count}`,
  );
  expect(previewPages).not.toContain(0);
  await page.route(`**/api/specifications/${id}/pages/2?*`, (route) =>
    route.fulfill({
      status: 503,
      json: { detail: "Offline preview unavailable" },
    }),
  );
  await pageField.fill("2");
  await expect(editor.getByRole("alert")).toContainText(
    "This PDF page could not be displayed",
  );
  await expect(quotation).toHaveValue(unsaved);
  await expect(
    editor.getByRole("link", { name: "Open original PDF", exact: true }),
  ).toHaveAttribute("href", `/api/specifications/${id}/pdf#page=2`);
  await editor.getByRole("button", { name: "Cancel", exact: true }).click();
  await expect(editor).not.toBeVisible();
  expect(inferencePosts).toBe(0);
  expect(correctionPosts).toBe(0);
  const after = (await (
    await request.get(`/api/specifications/${id}`)
  ).json()) as SpecificationData;
  expect(after.run!.extraction.requirements).toEqual(
    source.run!.extraction.requirements,
  );
});

test("the source editor locks a pending save and keeps its existing payload and failed draft", async ({
  page,
  request,
}) => {
  const { id, row, source } = await sourceFixture(page, request);
  let posts = 0;
  let submitted: Record<string, unknown> | null = null;
  let release!: () => void;
  const held = new Promise<void>((resolve) => {
    release = resolve;
  });
  await page.route(`**/api/specifications/${id}/facts`, async (route) => {
    posts++;
    submitted = route.request().postDataJSON();
    await held;
    await route.fulfill({
      status: 503,
      json: { detail: "The correction could not be saved. Please retry." },
    });
  });
  await page
    .getByRole("button", { name: "Correct extracted content", exact: true })
    .click();
  const editor = page.getByRole("dialog", {
    name: "Correct extracted content",
    exact: true,
  });
  const name = editor.getByLabel(/^Test name/);
  await name.fill("Reviewed customer test name");
  try {
    await editor
      .getByRole("button", { name: "Save corrected draft", exact: true })
      .evaluate((button: HTMLButtonElement) => {
        button.click();
        button.click();
      });
    await expect.poll(() => posts).toBe(1);
    await expect(name).toBeDisabled();
    await expect(
      editor.getByRole("tab", { name: "Source evidence", exact: true }),
    ).toBeDisabled();
    await expect(
      editor.getByRole("button", { name: "Cancel", exact: true }),
    ).toBeDisabled();
    await page.keyboard.press("Escape");
    await expect(editor).toBeVisible();
    expect(submitted).toMatchObject({
      run_id: source.run!.run_id,
      expected_version: source.facts_version ?? 0,
      expected_review_version: source.review?.version ?? 0,
    });
    const submittedRows = submitted!.requirements as Requirement[];
    expect(submittedRows).toEqual(
      source.run!.extraction.requirements.map((item) =>
        item.id === row.id
          ? { ...item, original_test_name: "Reviewed customer test name" }
          : item,
      ),
    );
  } finally {
    release();
  }
  await expect(editor.getByRole("alert")).toContainText(
    "The correction could not be saved. Please retry.",
  );
  await expect(name).toBeEnabled();
  await expect(name).toHaveValue("Reviewed customer test name");
  await expect(
    editor.getByRole("button", { name: "Save corrected draft", exact: true }),
  ).toBeEnabled();
  expect(posts).toBe(1);
  await editor.getByRole("button", { name: "Cancel", exact: true }).click();
});
