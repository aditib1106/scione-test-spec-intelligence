import { expect, test } from "@playwright/test";
import type { SpecificationData, Workspace } from "../src/types";

test("failed analysis is not retried and requires fresh consent before another call", async ({
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
  const imported = await request.post("/api/specifications/worked-example");
  expect(imported.ok()).toBeTruthy();
  const { document_id: documentId } = await imported.json();
  const before = (await (
    await request.get(`/api/specifications/${documentId}`)
  ).json()) as SpecificationData;
  await page.route("**/api/workspace", async (route) => {
    const response = await route.fetch();
    const workspace = (await response.json()) as Workspace;
    await route.fulfill({
      json: {
        ...workspace,
        providers: [
          {
            id: "sail",
            label: "Sail",
            model: "offline-lifecycle-fixture",
            available: true,
            output_limit: 8192,
          },
        ],
      },
    });
  });
  let modelPosts = 0;
  let failed = false;
  await page.route(`**/api/specifications/${documentId}`, async (route) => {
    await route.fulfill({
      json: failed
        ? {
            ...before,
            failure: {
              attempt_id: "offline-failed-attempt",
              message:
                "Fixture structured output did not conform to the schema.",
              occurred_at: new Date().toISOString(),
              previous_run_id: before.run?.run_id,
            },
          }
        : before,
    });
  });
  await page.route(
    `**/api/specifications/${documentId}/analysis`,
    async (route) => {
      modelPosts++;
      failed = true;
      await route.fulfill({
        status: 400,
        json: {
          detail:
            "Fixture structured output did not conform to the schema. No automatic retry was made.",
        },
      });
    },
  );

  await page.goto(`/specifications?document=${documentId}`);
  await page.getByRole("button", { name: /^Analysis settings/ }).click();
  await expect(page.getByText(/Leaving this page stops waiting/)).toBeVisible();
  const sendConsent = page.getByRole("checkbox", {
    name: /I have permission to send this document/,
  });
  const usageConsent = page.getByRole("checkbox", {
    name: /I understand this uses my Sail/,
  });
  await sendConsent.check();
  await usageConsent.check();
  const analyze = page.getByRole("button", {
    name: "Analyze again",
    exact: true,
  });
  await expect(analyze).toBeEnabled();
  await analyze.evaluate((button: HTMLButtonElement) => {
    button.click();
    button.click();
  });
  await expect(
    page.getByText(/No automatic retry was made/).first(),
  ).toBeVisible();
  await expect(sendConsent).not.toBeChecked();
  await expect(usageConsent).not.toBeChecked();
  await expect(analyze).toBeDisabled();
  await page
    .getByRole("button", { name: "Developer details · failed analysis" })
    .click();
  await expect(
    page.getByRole("link", { name: "Download failed attempt" }),
  ).toHaveAttribute(
    "href",
    `/api/specifications/${documentId}/analysis-failures/offline-failed-attempt/artifacts`,
  );
  expect(modelPosts).toBe(1);
  const saved = (await (
    await request.get(`/api/specifications/${documentId}`)
  ).json()) as SpecificationData;
  expect(saved.run?.run_id).toBe(before.run?.run_id);
  await sendConsent.check();
  await usageConsent.check();
  await analyze.click();
  await expect(usageConsent).not.toBeChecked();
  expect(modelPosts).toBe(2);
});
