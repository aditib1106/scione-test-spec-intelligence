import { expect, test } from "@playwright/test";
import type { Workspace } from "../src/types";

test("lab can create, edit and delete a private test without a standard code", async ({
  page,
  request,
}, testInfo) => {
  const reset = await request.post("/api/workspace/reset", {
    data: { acknowledged: true },
  });
  expect(reset.ok()).toBeTruthy();
  const before = (await (
    await request.get("/api/workspace")
  ).json()) as Workspace;
  await page.goto("/my-lab");
  await page
    .getByRole("button", { name: "Add a test", exact: true })
    .first()
    .click();
  const editor = page.getByRole("dialog");
  if (testInfo.project.name === "desktop") {
    await editor.screenshot({
      path: testInfo.outputPath("capability-catalog-selection.png"),
      animations: "disabled",
    });
  }
  await editor
    .getByRole("button", { name: "Create custom test", exact: true })
    .click();
  await editor
    .getByLabel(/^Custom test name/)
    .fill("In-house bend fixture check");
  await editor
    .getByLabel("Test description (optional)")
    .fill("A lab-defined coating fixture assessment.");
  await editor
    .getByRole("button", { name: "Add custom method", exact: true })
    .click();
  await editor
    .getByLabel(/^Custom method 1/)
    .fill("Internal mandrel procedure");
  await expect(editor.getByLabel("Code (optional)")).toHaveValue("");
  if (testInfo.project.name === "desktop") {
    await editor.locator(".MuiDialogContent-root").evaluate((element) => {
      element.scrollTop = 0;
    });
    await editor.screenshot({
      path: testInfo.outputPath("capability-custom-test.png"),
      animations: "disabled",
    });
  }
  const submitted = page.waitForRequest(
    (sent) =>
      sent.method() === "POST" &&
      new URL(sent.url()).pathname === "/api/lab/capabilities",
  );
  await editor
    .getByRole("button", { name: "Save capability", exact: true })
    .click();
  const payload = (await submitted).postDataJSON();
  expect(payload.custom_methods[0]).toEqual({
    name: "Internal mandrel procedure",
  });
  await expect(editor).not.toBeVisible();
  await expect(
    page.getByRole("button", {
      name: "In-house bend fixture check",
      exact: true,
    }),
  ).toBeVisible();
  let saved = (await (await request.get("/api/workspace")).json()) as Workspace;
  const privateTest = saved.catalog.tests.find(
    (item) => item.name === "In-house bend fixture check",
  )!;
  expect(privateTest.id).toMatch(/^labtest_/);
  const privateMethod = saved.catalog.methods.find(
    (item) => item.name === "Internal mandrel procedure",
  )!;
  expect(privateMethod.id).toMatch(/^labmethod_/);
  expect(privateMethod.identifier).toBe("Internal mandrel procedure");
  expect(
    saved.catalog.tests.filter((item) => !item.id.startsWith("labtest_")),
  ).toEqual(before.catalog.tests);
  await page.getByRole("button", { name: "Edit", exact: true }).click();
  await editor
    .getByLabel(/^Custom test name/)
    .fill("In-house bend fixture assessment");
  await editor
    .getByRole("button", { name: "Save capability", exact: true })
    .click();
  await expect(editor).not.toBeVisible();
  await expect(
    page.getByRole("button", {
      name: "In-house bend fixture assessment",
      exact: true,
    }),
  ).toBeVisible();
  saved = (await (await request.get("/api/workspace")).json()) as Workspace;
  expect(
    saved.catalog.tests.find(
      (item) => item.name === "In-house bend fixture assessment",
    )?.id,
  ).toBe(privateTest.id);
  await page
    .getByRole("button", {
      name: "Delete capability for In-house bend fixture assessment",
      exact: true,
    })
    .click();
  const confirmation = page.getByRole("dialog");
  await expect(confirmation).toContainText(
    "Previously saved reports will be kept.",
  );
  await confirmation
    .getByRole("button", { name: "Cancel", exact: true })
    .click();
  expect(
    (await (await request.get("/api/lab")).json()).capabilities,
  ).toHaveLength(1);
  await page
    .getByRole("button", {
      name: "Delete capability for In-house bend fixture assessment",
      exact: true,
    })
    .click();
  await confirmation
    .getByRole("button", { name: "Delete capability", exact: true })
    .click();
  await expect(confirmation).not.toBeVisible();
  await expect(
    page.getByText("Start with your lab’s capabilities", { exact: true }),
  ).toBeVisible();
  expect(
    (await (await request.get("/api/lab")).json()).capabilities,
  ).toHaveLength(0);
});

test("test and method selection is searchable and stale deletes keep the dialog open", async ({
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
  await page.goto("/my-lab");
  await page
    .getByRole("button", { name: "Add a test", exact: true })
    .first()
    .click();
  const editor = page.getByRole("dialog");
  await editor
    .getByRole("combobox", { name: "Test name", exact: true })
    .fill("ISO 15184");
  await page
    .getByRole("option", { name: "Pencil hardness", exact: true })
    .click();
  await editor
    .getByRole("combobox", {
      name: "Supported methods (optional)",
      exact: true,
    })
    .fill("ISO 15184");
  await page.getByRole("option", { name: /ISO 15184/ }).click();
  await editor
    .getByRole("combobox", {
      name: "Supported methods (optional)",
      exact: true,
    })
    .press("Escape");
  await editor
    .getByRole("button", { name: "Save capability", exact: true })
    .click();
  await expect(editor).not.toBeVisible();
  await page
    .getByRole("button", {
      name: "Delete capability for Pencil hardness",
      exact: true,
    })
    .click();
  const current = await (await request.get("/api/lab")).json();
  expect(
    (
      await request.put("/api/lab", {
        data: { ...current, description: "Concurrent lab update" },
      })
    ).ok(),
  ).toBeTruthy();
  const confirmation = page.getByRole("dialog");
  await confirmation
    .getByRole("button", { name: "Delete capability", exact: true })
    .click();
  await expect(confirmation.getByRole("alert")).toBeVisible();
  expect(
    (await (await request.get("/api/lab")).json()).capabilities,
  ).toHaveLength(1);
  await confirmation
    .getByRole("button", { name: "Reload lab", exact: true })
    .click();
  await expect(confirmation).not.toBeVisible();
  await expect(
    page.getByText("Concurrent lab update", { exact: true }),
  ).toBeVisible();
});
