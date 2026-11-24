import {
  expect,
  test,
  type APIRequestContext,
  type Page,
} from "@playwright/test";
import type { SpecificationData, Workspace } from "../src/types";

async function reviewFixture(page: Page, request: APIRequestContext) {
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
  expect(source.run!.extraction.requirements.length).toBeGreaterThanOrEqual(2);
  source.review!.decisions = {};
  const requirements = source.run!.extraction.requirements;
  for (const row of requirements) {
    source.assessments[row.id] = { state: "needs_attention", reasons: [] };
  }
  await page.route(`**/api/specifications/${id}`, (route) =>
    route.fulfill({ json: source }),
  );
  return { id, source, requirements };
}

function confirm(source: SpecificationData, ids: string[]) {
  source.review!.version += 1;
  for (const id of ids)
    source.review!.decisions[id] = {
      requirement_id: id,
      status: "confirmed",
      note: "Checked against the source PDF",
      updated_at: new Date().toISOString(),
    };
}

test("returning from a still-loading specification keeps Pending and All selection working", async ({
  page,
  request,
}) => {
  const { id, source, requirements } = await reviewFixture(page, request);
  const confirmed = requirements[0];
  const pending = requirements[1];
  confirm(source, [confirmed.id]);
  const workspace = (await (
    await request.get("/api/workspace")
  ).json()) as Workspace;
  const current = workspace.documents.find((item) => item.document_id === id)!;
  const second = {
    ...current,
    document_id: "delayed-navigation-source",
    original_name: "still-loading-specification.pdf",
  };
  await page.route("**/api/workspace", (route) =>
    route.fulfill({
      json: { ...workspace, documents: [...workspace.documents, second] },
    }),
  );
  let release!: () => void;
  const held = new Promise<void>((resolve) => {
    release = resolve;
  });
  await page.route(
    `**/api/specifications/${second.document_id}`,
    async (route) => {
      await held;
      // Back cancels the destination read; it must never replace the source view.
      await route.abort("aborted").catch(() => {});
    },
  );
  let writes = 0;
  page.on("request", (sent) => {
    if (!["GET", "HEAD", "OPTIONS"].includes(sent.method())) writes++;
  });
  try {
    await page.goto(
      `/specifications?document=${id}&requirement=${confirmed.id}`,
    );
    const selected = page.getByRole("combobox", {
      name: "Selected test",
      exact: true,
    });
    await expect(selected).toBeVisible();
    await expect(selected).toContainText(confirmed.original_test_name);
    await page
      .getByRole("combobox", { name: "Saved specification", exact: true })
      .click();
    const destinationRead = page.waitForRequest(
      (sent) =>
        new URL(sent.url()).pathname ===
        `/api/specifications/${second.document_id}`,
    );
    await page
      .getByRole("option", { name: second.original_name, exact: true })
      .click();
    await destinationRead;
    await expect(page).toHaveURL(new RegExp(`document=${second.document_id}`));
    const returnedRead = page.waitForResponse(
      (response) =>
        new URL(response.url()).pathname === `/api/specifications/${id}`,
    );
    await page.goBack();
    expect((await returnedRead).ok()).toBeTruthy();
    await expect(page).toHaveURL(new RegExp(`document=${id}`));
    await expect(selected).toBeVisible();
    await expect(selected).toContainText(confirmed.original_test_name);
    await page.getByRole("button", { name: "Pending", exact: true }).click();
    await expect(selected).toContainText(pending.original_test_name);
    await expect(
      page.getByText("No tests need your review", { exact: true }),
    ).toHaveCount(0);
    await page.getByRole("button", { name: "All", exact: true }).click();
    await selected.click();
    await page
      .getByRole("option", { name: confirmed.original_test_name, exact: true })
      .click();
    await page.getByRole("button", { name: "Pending", exact: true }).click();
    await expect(selected).toContainText(pending.original_test_name);
    expect(writes).toBe(0);
    expect(Object.keys(source.review!.decisions)).toEqual([confirmed.id]);
  } finally {
    release();
  }
});

test("Pending excludes the selected confirmed test and stays empty after the last confirmation", async ({
  page,
  request,
}, testInfo) => {
  const { id, source, requirements } = await reviewFixture(page, request);
  const confirmed = requirements[0];
  const pending = requirements[1];
  confirm(
    source,
    requirements.filter((row) => row.id !== pending.id).map((row) => row.id),
  );
  await page.route(`**/api/specifications/${id}/review`, async (route) => {
    const body = route.request().postDataJSON();
    confirm(source, body.requirement_ids);
    await route.fulfill({ json: source });
  });
  await page.goto(`/specifications?document=${id}&requirement=${confirmed.id}`);
  const selected = page.getByRole("combobox", {
    name: "Selected test",
    exact: true,
  });
  await expect(selected).toContainText(confirmed.original_test_name);
  await page.getByRole("button", { name: "Pending", exact: true }).click();
  await expect(selected).toContainText(pending.original_test_name);
  await selected.click();
  await expect(
    page.getByRole("option", {
      name: confirmed.original_test_name,
      exact: true,
    }),
  ).toHaveCount(0);
  await expect(
    page.getByRole("option", { name: pending.original_test_name, exact: true }),
  ).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(page.getByText(/Pending item 1 of 1/)).toBeVisible();
  await page
    .getByRole("button", { name: "Confirm & next", exact: true })
    .click();
  await expect(
    page.getByText("No tests need your review", { exact: true }),
  ).toBeVisible();
  await expect(selected).toBeDisabled();
  await expect(
    page.getByRole("button", { name: "Pending", exact: true }),
  ).toHaveClass(/MuiButton-contained/);
  if (testInfo.project.name === "desktop") {
    const sourcePage = page.getByRole("img", { name: /^PDF page/ }).first();
    await expect(sourcePage).toBeVisible();
    await expect
      .poll(() =>
        sourcePage.evaluate((image: HTMLImageElement) => image.naturalWidth),
      )
      .toBeGreaterThan(0);
    await page.evaluate(() => window.scrollTo(0, 0));
    await expect.poll(() => page.evaluate(() => window.scrollY)).toBe(0);
    await page.evaluate(
      () =>
        new Promise<void>((resolve) =>
          requestAnimationFrame(() => requestAnimationFrame(() => resolve())),
        ),
    );
    await page.screenshot({
      path: testInfo.outputPath("pending-review-complete.png"),
      fullPage: false,
    });
  }
  await page
    .getByRole("button", { name: "View all tests", exact: true })
    .click();
  await expect(selected).toBeEnabled();
  await selected.click();
  await expect(page.getByRole("option")).toHaveCount(requirements.length);
});

for (const count of [1, 2]) {
  test(`batch confirmation locks every control and submits once for ${count} selected test(s)`, async ({
    page,
    request,
  }) => {
    const { id, source, requirements } = await reviewFixture(page, request);
    let posts = 0;
    let release!: () => void;
    const held = new Promise<void>((resolve) => {
      release = resolve;
    });
    await page.route(`**/api/specifications/${id}/review`, async (route) => {
      posts += 1;
      const body = route.request().postDataJSON();
      await held;
      confirm(source, body.requirement_ids);
      await route.fulfill({ json: source });
    });
    await page.goto(`/specifications?document=${id}`);
    await page
      .getByRole("button", { name: "Batch review", exact: true })
      .click();
    const dialog = page.getByRole("dialog", {
      name: "Review selected tests",
      exact: true,
    });
    for (const row of requirements.slice(0, count))
      await dialog
        .getByRole("checkbox", { name: row.original_test_name, exact: true })
        .check();
    await dialog
      .getByLabel("Batch note / exclusion reason")
      .fill("Checked against the source PDF");
    if (count > 1)
      await dialog
        .getByRole("checkbox", {
          name: "I checked every selected record against the PDF",
        })
        .check();
    try {
      await dialog
        .getByRole("button", { name: "Confirm selected", exact: true })
        .evaluate((button: HTMLButtonElement) => {
          button.click();
          button.click();
        });
      await expect.poll(() => posts).toBe(1);
      await expect(
        dialog.getByRole("button", { name: "Saving…", exact: true }),
      ).toBeDisabled();
      await expect(
        dialog.getByRole("button", { name: "Select pending", exact: true }),
      ).toBeDisabled();
      await expect(
        dialog.getByRole("button", { name: "Clear selection", exact: true }),
      ).toBeDisabled();
      await expect(
        dialog.getByRole("button", { name: "Exclude selected", exact: true }),
      ).toBeDisabled();
      await expect(
        dialog.getByRole("button", { name: "Close batch review", exact: true }),
      ).toBeDisabled();
      await expect(
        dialog.getByLabel("Batch note / exclusion reason"),
      ).toBeDisabled();
      for (const checkbox of await dialog.getByRole("checkbox").all())
        await expect(checkbox).toBeDisabled();
      await page.keyboard.press("Escape");
      await expect(dialog).toBeVisible();
      expect(posts).toBe(1);
    } finally {
      release();
    }
    await expect(dialog).not.toBeVisible();
    expect(posts).toBe(1);
  });
}

test("a single-test batch failure stays visible with the draft intact and retries only on explicit submit", async ({
  page,
  request,
}) => {
  const { id, source, requirements } = await reviewFixture(page, request);
  let posts = 0;
  const message = "The saved review is temporarily unavailable. Please retry.";
  await page.route(`**/api/specifications/${id}/review`, async (route) => {
    posts += 1;
    if (posts === 1) {
      await route.fulfill({ status: 503, json: { detail: message } });
      return;
    }
    confirm(source, route.request().postDataJSON().requirement_ids);
    await route.fulfill({ json: source });
  });
  await page.goto(`/specifications?document=${id}`);
  await page.getByRole("button", { name: "Batch review", exact: true }).click();
  const dialog = page.getByRole("dialog", {
    name: "Review selected tests",
    exact: true,
  });
  const chosen = dialog.getByRole("checkbox", {
    name: requirements[0].original_test_name,
    exact: true,
  });
  const note = dialog.getByLabel("Batch note / exclusion reason");
  const submit = dialog.getByRole("button", {
    name: "Confirm selected",
    exact: true,
  });
  await chosen.check();
  await note.fill("My source review note");
  await submit.click();
  await expect(dialog.getByRole("alert")).toContainText(message);
  await expect(chosen).toBeChecked();
  await expect(chosen).toBeEnabled();
  await expect(note).toHaveValue("My source review note");
  await expect(note).toBeEnabled();
  await expect(submit).toBeEnabled();
  expect(posts).toBe(1);
  await note.fill("My source review note, retained after the failed save");
  await page.evaluate(() => window.dispatchEvent(new Event("focus")));
  expect(posts).toBe(1);
  await submit.click();
  await expect(dialog).not.toBeVisible();
  expect(posts).toBe(2);
});

test("inline progress hides only the same task; other documents, task kinds and connection warnings stay visible", async ({
  page,
  request,
}) => {
  const { id } = await reviewFixture(page, request);
  const operation = {
    operation_id: "feedback-progress-fixture",
    document_id: id,
    kind: "extraction",
    state: "running",
    started_at: new Date(Date.now() - 2000).toISOString(),
    progress: {
      stage: "extracting",
      completed_steps: 1,
      total_steps: 2,
      percent: 50,
      message: "Reading the remaining source pages.",
    },
  };
  let unavailable = false;
  await page.route("**/api/operations", (route) =>
    unavailable
      ? route.fulfill({
          status: 503,
          json: { detail: "Status temporarily unavailable" },
        })
      : route.fulfill({ json: { active: [operation] } }),
  );
  let inferencePosts = 0;
  page.on("request", (sent) => {
    if (
      sent.method() === "POST" &&
      /\/(analysis|matching)$/.test(new URL(sent.url()).pathname)
    )
      inferencePosts += 1;
  });
  await page.goto(`/specifications?document=${id}`);
  const inline = page.getByRole("status", {
    name: "Document analysis in progress",
    exact: true,
  });
  const global = page.getByRole("link", { name: "View progress", exact: true });
  await expect(inline).toBeVisible();
  await expect(global).not.toBeVisible();

  await page.goto("/specifications/library");
  await expect(global).toBeVisible();
  await global.click();
  await expect(inline).toBeVisible();
  await expect(global).not.toBeVisible();

  operation.kind = "matching";
  await page.evaluate(() => window.dispatchEvent(new Event("focus")));
  await expect(inline).not.toBeVisible();
  await expect(global).toBeVisible();
  await expect(page.getByText(/^Generating your report/)).toBeVisible();

  operation.kind = "extraction";
  await page.evaluate(() => window.dispatchEvent(new Event("focus")));
  await expect(inline).toBeVisible();
  await expect(global).not.toBeVisible();
  operation.document_id = "different-document-fixture";
  await page.evaluate(() => window.dispatchEvent(new Event("focus")));
  await expect(inline).not.toBeVisible();
  await expect(global).toBeVisible();

  operation.document_id = id;
  await page.evaluate(() => window.dispatchEvent(new Event("focus")));
  await expect(inline).toBeVisible();
  await expect(global).not.toBeVisible();
  unavailable = true;
  await page.evaluate(() => window.dispatchEvent(new Event("focus")));
  await expect(page.getByText(/^Analysis status unavailable/)).toBeVisible();
  await expect(global).toBeVisible();
  expect(inferencePosts).toBe(0);
});
