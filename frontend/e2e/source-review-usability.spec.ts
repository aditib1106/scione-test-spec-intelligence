import { expect, test } from "@playwright/test";
import type { SpecificationData } from "../src/types";

test("source review groups fields, locates citations, and adds or removes a missed test without replacing AI output", async ({
  page,
  request,
}, testInfo) => {
  page.setDefaultTimeout(15_000);
  expect(
    (
      await request.post("/api/workspace/reset", {
        data: { acknowledged: true },
      })
    ).ok(),
  ).toBeTruthy();
  const imported = await request.post("/api/specifications/worked-example");
  expect(imported.ok()).toBeTruthy();
  const { document_id: documentId } = await imported.json();
  const sourceUrl = `/api/specifications/${documentId}`;
  const before = (await (
    await request.get(sourceUrl)
  ).json()) as SpecificationData;
  const selected = before.run!.extraction.requirements[0];
  await page.goto(
    `/specifications?document=${documentId}&requirement=${selected.id}`,
  );
  const details = page.getByRole("table", { name: "Test details" });
  await expect(
    details.getByRole("rowheader", { name: /^Test name/ }),
  ).toBeVisible();
  await expect(page.getByRole("tab", { name: /^Conditions/ })).toBeVisible();
  await expect(
    page.getByRole("tab", { name: /^Acceptance criteria/ }),
  ).toBeVisible();
  const parameterTable = page.getByRole("table", {
    name: "Condition parameters",
  });
  await expect(parameterTable).toBeVisible();
  await expect(
    page.getByRole("combobox", { name: "Selected test", exact: true }),
  ).toBeVisible();
  await expect(
    page.getByRole("button", { name: "OCR · coming soon" }),
  ).toBeDisabled();

  const previewRequest = page.waitForRequest(
    (sent) =>
      sent.url().includes(`/api/specifications/${documentId}/pages/`) &&
      sent.url().includes("field=condition"),
  );
  await parameterTable
    .getByRole("button", { name: /^View condition source on page 1 / })
    .first()
    .click();
  await previewRequest;
  const preview = page.getByRole("img", { name: /PDF page/ }).first();
  await expect(preview).toBeVisible();
  if (testInfo.project.name === "desktop") {
    const viewport = page.getByRole("region", {
      name: "Source document scroll area",
    });
    await expect
      .poll(() => viewport.evaluate((element) => element.scrollTop))
      .toBeGreaterThan(0);
    await viewport.evaluate((element) => {
      element.scrollTop = 20;
    });
    await page.getByRole("button", { name: "All", exact: true }).click();
    expect(await viewport.evaluate((element) => element.scrollTop)).toBe(20);
    await parameterTable
      .getByRole("button", { name: /^View condition source on page 1 / })
      .first()
      .click();
    await expect
      .poll(() => viewport.evaluate((element) => element.scrollTop))
      .toBeGreaterThan(20);
    await viewport.evaluate((element) => {
      element.scrollTop = 20;
    });
    await page.getByRole("button", { name: "Relocate", exact: true }).click();
    await expect
      .poll(() => viewport.evaluate((element) => element.scrollTop))
      .toBeGreaterThan(20);
  }
  if (testInfo.project.name === "narrow") {
    const source = page.getByRole("button", { name: /Source PDF preview/ });
    await expect(source).toHaveAttribute("aria-expanded", "true");
  }

  await page
    .getByRole("button", { name: "Add missing test", exact: true })
    .click();
  const dialog = page.getByRole("dialog", {
    name: "Add missing test",
    exact: true,
  });
  await expect(dialog).toBeVisible();
  const addedName = "Reviewer-added source check";
  await dialog.getByLabel("Test name").fill(addedName);
  await dialog
    .getByRole("tab", { name: "Source evidence", exact: true })
    .click();
  await dialog
    .getByLabel("Source page")
    .fill(String(selected.evidence[0].page));
  await dialog.getByLabel("Source quotation").fill(selected.evidence[0].quote);
  await dialog
    .getByRole("button", { name: "Add a review note (optional)", exact: true })
    .click();
  await dialog
    .getByLabel("Reason for adding this test")
    .fill(
      "The reviewer identified another source check while reading the PDF.",
    );
  await dialog.getByRole("button", { name: "Add test", exact: true }).click();
  await expect(dialog).not.toBeVisible();
  await expect(
    page.getByRole("heading", { name: addedName, exact: true }),
  ).toBeVisible();
  const after = (await (
    await request.get(sourceUrl)
  ).json()) as SpecificationData;
  expect(after.run!.extraction.requirements).toHaveLength(
    before.run!.extraction.requirements.length + 1,
  );
  expect(after.run!.raw_response).toBe(before.run!.raw_response);
  expect(after.facts_version).toBe(1);
  const added = after.run!.extraction.requirements.at(-1)!;

  await page.getByRole("button", { name: "Remove", exact: true }).click();
  const removeDialog = page.getByRole("dialog", { name: "Remove this test?" });
  await expect(removeDialog).toBeVisible();
  await expect(
    removeDialog.getByRole("button", { name: "Remove test", exact: true }),
  ).toBeEnabled();
  await removeDialog
    .getByRole("button", { name: "Remove test", exact: true })
    .click();
  await expect(removeDialog).not.toBeVisible();
  await expect
    .poll(async () => {
      const current = (await (
        await request.get(sourceUrl)
      ).json()) as SpecificationData;
      return current.review?.decisions[added.id]?.status;
    })
    .toBe("excluded");
  const current = (await (
    await request.get(sourceUrl)
  ).json()) as SpecificationData;
  expect(current.run!.extraction.requirements).toHaveLength(
    after.run!.extraction.requirements.length,
  );
  expect(current.run!.raw_response).toBe(before.run!.raw_response);
  const dimensions = await page.evaluate(() => ({
    viewport: document.documentElement.clientWidth,
    width: document.documentElement.scrollWidth,
  }));
  expect(dimensions.width).toBeLessThanOrEqual(dimensions.viewport + 1);
});

for (const action of ["edit", "add"] as const) {
  test(`a late ${action} response cannot replace a different specification's review`, async ({
    page,
    request,
  }) => {
    page.setDefaultTimeout(15_000);
    expect(
      (
        await request.post("/api/workspace/reset", {
          data: { acknowledged: true },
        })
      ).ok(),
    ).toBeTruthy();
    const first = await request.post("/api/specifications/worked-example");
    const second = await request.post("/api/specifications/demo");
    expect(first.ok()).toBeTruthy();
    expect(second.ok()).toBeTruthy();
    const { document_id: firstId } = await first.json();
    const { document_id: secondId } = await second.json();
    expect(firstId).not.toBe(secondId);
    const original = (await (
      await request.get(`/api/specifications/${firstId}`)
    ).json()) as SpecificationData;
    const originalSecond = (await (
      await request.get(`/api/specifications/${secondId}`)
    ).json()) as SpecificationData;
    const selected = original.run!.extraction.requirements[0];
    await page.goto(`/specifications?document=${secondId}`);
    await expect(
      page.getByText("No extraction yet", { exact: true }),
    ).toBeVisible();
    // Same-document browser navigation keeps this React tree and the delayed
    // mutation alive. A full page.goto would destroy the context and miss the bug.
    await page.evaluate(
      ({ firstId, requirementId }) => {
        window.history.pushState(
          {},
          "",
          `/specifications?document=${firstId}&requirement=${requirementId}`,
        );
        window.dispatchEvent(new PopStateEvent("popstate"));
      },
      { firstId, requirementId: selected.id },
    );
    await expect(
      page.getByRole("heading", {
        name: selected.original_test_name,
        exact: true,
      }),
    ).toBeVisible();
    let release!: () => void;
    let received!: () => void;
    let delivered!: () => void;
    const gate = new Promise<void>((resolve) => {
      release = resolve;
    });
    const responseReady = new Promise<void>((resolve) => {
      received = resolve;
    });
    const responseDelivered = new Promise<void>((resolve) => {
      delivered = resolve;
    });
    const endpoint = action === "edit" ? "facts" : "requirements";
    await page.route(
      `**/api/specifications/${firstId}/${endpoint}`,
      async (route) => {
        const response = await route.fetch();
        received();
        await gate;
        await route.fulfill({ response });
        delivered();
      },
    );
    await page
      .getByRole("button", {
        name:
          action === "edit" ? "Correct extracted content" : "Add missing test",
        exact: true,
      })
      .click();
    const dialog = page.getByRole("dialog", {
      name:
        action === "edit" ? "Correct extracted content" : "Add missing test",
      exact: true,
    });
    const newName = `${selected.original_test_name} (human ${action})`;
    await dialog.getByLabel("Test name").fill(newName);
    if (action === "add") {
      await dialog
        .getByRole("tab", { name: "Source evidence", exact: true })
        .click();
      await dialog
        .getByLabel("Source page")
        .fill(String(selected.evidence[0].page));
      await dialog
        .getByLabel("Source quotation")
        .fill(selected.evidence[0].quote);
    }
    await dialog
      .getByRole("button", {
        name: "Add a review note (optional)",
        exact: true,
      })
      .click();
    await dialog
      .getByLabel(
        action === "edit"
          ? "Reason for correction"
          : "Reason for adding this test",
      )
      .fill("A source-backed human update for the first document.");
    await dialog
      .getByRole("button", {
        name: action === "edit" ? "Save corrected draft" : "Add test",
        exact: true,
      })
      .click();
    await responseReady;
    await page.goBack();
    await expect(page).toHaveURL(new RegExp(`document=${secondId}`));
    await expect(
      page.getByText("No extraction yet", { exact: true }),
    ).toBeVisible();
    release();
    await responseDelivered;
    await expect(
      page.getByRole("combobox", { name: "Saved specification", exact: true }),
    ).toContainText(originalSecond.original_name);
    await expect(page).toHaveURL(new RegExp(`document=${secondId}`));
    await expect(
      page.getByText("No extraction yet", { exact: true }),
    ).toBeVisible();
    await expect(
      page.getByRole("heading", { name: newName, exact: true }),
    ).toHaveCount(0);
    const saved = (await (
      await request.get(`/api/specifications/${firstId}`)
    ).json()) as SpecificationData;
    expect(
      saved.run!.extraction.requirements.some(
        (item) => item.original_test_name === newName,
      ),
    ).toBeTruthy();
    expect(saved.run!.raw_response).toBe(original.run!.raw_response);
    expect(
      (
        (await (
          await request.get(`/api/specifications/${secondId}`)
        ).json()) as SpecificationData
      ).run,
    ).toBeNull();
  });
}

test("an obsolete source-location response cannot scroll a browsed page or another test", async ({
  page,
  request,
}) => {
  page.setDefaultTimeout(15_000);
  expect(
    (
      await request.post("/api/workspace/reset", {
        data: { acknowledged: true },
      })
    ).ok(),
  ).toBeTruthy();
  const imported = await request.post("/api/specifications/worked-example");
  const { document_id: documentId } = await imported.json();
  const source = (await (
    await request.get(`/api/specifications/${documentId}`)
  ).json()) as SpecificationData;
  const selected = source.run!.extraction.requirements[0];
  const other = source.run!.extraction.requirements[1];
  await page.goto(
    `/specifications?document=${documentId}&requirement=${selected.id}`,
  );
  const parameter = source.normalized_requirements![selected.id].claims.find(
    (claim) =>
      claim.kind === "condition" &&
      claim.parameter_id &&
      claim.source_pages.includes(1),
  )!;
  const details = page.getByRole("table", { name: "Condition parameters" });
  let release!: () => void;
  let received!: () => void;
  let delivered!: () => void;
  const gate = new Promise<void>((resolve) => {
    release = resolve;
  });
  const responseReady = new Promise<void>((resolve) => {
    received = resolve;
  });
  const responseDelivered = new Promise<void>((resolve) => {
    delivered = resolve;
  });
  await page.route(
    (url) =>
      url.pathname === `/api/specifications/${documentId}/pages/1` &&
      url.searchParams.get("field") === "condition",
    async (route) => {
      const response = await route.fetch();
      received();
      await gate;
      try {
        await route.fulfill({ response });
      } catch {
        /* The browser may have aborted the obsolete image request. */
      }
      delivered();
    },
  );
  await details
    .getByRole("button", {
      name: `View condition source on page 1 for ${parameter.label}`,
      exact: true,
    })
    .first()
    .click();
  await responseReady;
  const nextResponse = page.waitForResponse((response) => {
    const url = new URL(response.url());
    return (
      url.pathname === `/api/specifications/${documentId}/pages/2` &&
      url.searchParams.get("field") === "condition"
    );
  });
  await page.getByRole("button", { name: "Next page", exact: true }).click();
  const browsed = await nextResponse;
  expect(browsed.ok()).toBeTruthy();
  expect(browsed.headers()["x-highlight-focus"]).toBeDefined();
  await expect(page.getByRole("img", { name: /PDF page 2/ })).toBeVisible();
  const viewport = page.getByRole("region", {
    name: "Source document scroll area",
  });
  expect(await viewport.evaluate((element) => element.scrollTop)).toBe(0);
  await page
    .getByRole("combobox", { name: "Selected test", exact: true })
    .click();
  const otherPreview = page.waitForResponse((response) => {
    const url = new URL(response.url());
    return (
      url.pathname.includes(`/api/specifications/${documentId}/pages/`) &&
      url.searchParams.get("requirement_id") === other.id
    );
  });
  await page
    .getByRole("option", { name: other.original_test_name, exact: true })
    .click();
  await otherPreview;
  await expect(
    page.getByRole("heading", { name: other.original_test_name, exact: true }),
  ).toBeVisible();
  await expect(
    page.getByRole("img", { name: /PDF page/ }).first(),
  ).toBeVisible();
  await page.evaluate(
    () =>
      new Promise<void>((resolve) =>
        requestAnimationFrame(() => requestAnimationFrame(() => resolve())),
      ),
  );
  const otherScroll = await viewport.evaluate((element) => element.scrollTop);
  release();
  await responseDelivered;
  await expect(
    page.getByRole("heading", { name: other.original_test_name, exact: true }),
  ).toBeVisible();
  expect(await viewport.evaluate((element) => element.scrollTop)).toBe(
    otherScroll,
  );
});
