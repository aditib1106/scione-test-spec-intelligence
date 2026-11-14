import { expect, test } from "@playwright/test";
import type { SpecificationData, Workspace } from "../src/types";

test("new providers are selectable, reset consent, and never run on selection", async ({
  page,
  request,
}) => {
  page.setDefaultTimeout(12_000);
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
  const providers = [
    {
      id: "fireworks",
      label: "Fireworks",
      model: "fireworks-fixture",
      available: true,
      output_limit: 6144,
    },
    {
      id: "sail",
      label: "Sail",
      model: "sail-fixture",
      available: true,
      output_limit: 9216,
    },
    {
      id: "gemini",
      label: "Gemini",
      model: "unconfigured",
      available: false,
      output_limit: 8192,
    },
    {
      id: "groq",
      label: "Groq",
      model: "unconfigured",
      available: false,
      output_limit: 4096,
    },
  ];
  await page.route("**/api/workspace", async (route) => {
    const response = await route.fetch();
    const workspace = (await response.json()) as Workspace;
    await route.fulfill({ json: { ...workspace, providers } });
  });
  let modelPosts = 0;
  await page.route(
    /\/api\/specifications\/[^/]+\/(analysis|matching)$/,
    async (route) => {
      if (route.request().method() === "POST") {
        modelPosts++;
        await route.fulfill({
          status: 400,
          json: {
            detail: "Unexpected model request in offline selection test.",
          },
        });
        return;
      }
      await route.continue();
    },
  );

  await page.goto(`/specifications?document=${documentId}`);
  await page.getByRole("button", { name: /^Analysis settings/ }).click();
  await expect(
    page.getByRole("combobox", { name: "Provider", exact: true }),
  ).toHaveText("Fireworks");
  await page
    .getByRole("button", { name: "Advanced model settings", exact: true })
    .click();
  await expect(page.getByLabel("Model", { exact: true })).toHaveValue(
    "fireworks-fixture",
  );
  await expect(page.getByLabel("Max output tokens per request")).toHaveValue(
    "6144",
  );
  const allowSending = page.getByRole("checkbox", {
    name: /I have permission to send this document/,
  });
  const allowCredits = page.getByRole("checkbox", {
    name: /I understand this uses my Fireworks/,
  });
  const analyze = page.getByRole("button", {
    name: "Analyze again",
    exact: true,
  });
  await expect(analyze).toBeDisabled();
  await allowSending.check();
  await expect(analyze).toBeDisabled();
  await allowCredits.check();
  await expect(analyze).toBeEnabled();
  await page.getByRole("combobox", { name: "Provider", exact: true }).click();
  await expect(page.getByRole("option", { name: /Gemini/ })).toBeDisabled();
  await page.getByRole("option", { name: "Sail", exact: true }).click();
  await expect(allowSending).not.toBeChecked();
  await expect(
    page.getByRole("checkbox", { name: /I understand this uses my Sail/ }),
  ).not.toBeChecked();
  await expect(analyze).toBeDisabled();
  await expect(page.getByLabel("Model", { exact: true })).toHaveValue(
    "sail-fixture",
  );
  await expect(page.getByLabel("Max output tokens per request")).toHaveValue(
    "9216",
  );
  expect(modelPosts).toBe(0);
  const stillSaved = (await (
    await request.get(`/api/specifications/${documentId}`)
  ).json()) as SpecificationData;
  expect(stillSaved.run?.run_id).toBe(before.run?.run_id);
  expect(stillSaved.run?.provider).toBe(before.run?.provider);
  expect(stillSaved.run?.model).toBe(before.run?.model);

  await page.goto(`/reports?document=${documentId}`);
  await page
    .getByRole("button", { name: "Developer report tools", exact: true })
    .click();
  await page
    .getByRole("button", { name: "Run AI matching", exact: true })
    .click();
  const dialog = page.getByRole("dialog", { name: "Run AI matching" });
  await expect(
    dialog.getByRole("combobox", { name: "Model provider" }),
  ).toHaveText("Fireworks");
  await dialog
    .getByRole("button", { name: "Model settings", exact: true })
    .click();
  await expect(dialog.getByLabel("Matching model")).toHaveValue(
    "fireworks-fixture",
  );
  await expect(dialog.getByLabel("Maximum output tokens")).toHaveValue("6144");
  const start = dialog.getByRole("button", {
    name: "Start matching",
    exact: true,
  });
  await expect(start).toBeDisabled();
  await dialog.getByRole("checkbox", { name: /I allow sending/ }).check();
  await expect(start).toBeEnabled();
  await dialog.getByRole("combobox", { name: "Model provider" }).click();
  await page.getByRole("option", { name: "Sail", exact: true }).click();
  await expect(
    dialog.getByRole("checkbox", { name: /I allow sending/ }),
  ).not.toBeChecked();
  await expect(start).toBeDisabled();
  await expect(dialog.getByLabel("Matching model")).toHaveValue("sail-fixture");
  await expect(dialog.getByLabel("Maximum output tokens")).toHaveValue("9216");
  await dialog.getByRole("button", { name: "Cancel", exact: true }).click();
  expect(modelPosts).toBe(0);
});
