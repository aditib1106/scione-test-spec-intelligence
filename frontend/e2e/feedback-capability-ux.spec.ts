import { expect, test } from "@playwright/test";

test("removing a custom method clears obsolete row errors without bypassing validation", async ({
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
  let submissions = 0;
  page.on("request", (sent) => {
    if (
      sent.method() === "POST" &&
      new URL(sent.url()).pathname === "/api/lab/capabilities"
    )
      submissions++;
  });
  await page.goto("/my-lab");
  await page
    .getByRole("button", { name: "Add a test", exact: true })
    .first()
    .click();
  const editor = page.getByRole("dialog");
  await editor
    .getByRole("button", { name: "Create custom test", exact: true })
    .click();
  await editor
    .getByLabel(/^Custom test name/)
    .fill("Method validation fixture");
  const add = editor.getByRole("button", {
    name: "Add custom method",
    exact: true,
  });
  await add.click();
  await add.click();
  await editor.getByLabel(/^Custom method 2/).fill("Valid second method");
  const save = editor.getByRole("button", {
    name: "Save capability",
    exact: true,
  });
  await save.click();
  await expect(editor.getByLabel(/^Custom method 1/)).toHaveAttribute(
    "aria-invalid",
    "true",
  );
  await editor
    .getByRole("button", { name: "Remove custom method 1", exact: true })
    .click();
  const remaining = editor.getByLabel(/^Custom method 1/);
  await expect(remaining).toHaveValue("Valid second method");
  await expect(remaining).not.toHaveAttribute("aria-invalid", "true");
  await expect(editor.getByText(/Enter a name for custom method/)).toHaveCount(
    0,
  );

  await add.click();
  await save.click();
  await expect(editor.getByLabel(/^Custom method 2/)).toHaveAttribute(
    "aria-invalid",
    "true",
  );
  expect(submissions).toBe(0);
  await editor
    .getByRole("button", { name: "Choose a test", exact: true })
    .click();
  await expect(editor.getByLabel(/^Custom method [12]/)).toHaveCount(0);
  await expect(editor.getByText(/Enter a name for custom method/)).toHaveCount(
    0,
  );
  await editor.getByRole("button", { name: "Cancel", exact: true }).click();
});

test("capability validation shows all problems and protects an unsaved draft", async ({
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
  let submissions = 0;
  page.on("request", (sent) => {
    if (
      sent.method() === "POST" &&
      new URL(sent.url()).pathname === "/api/lab/capabilities"
    )
      submissions += 1;
  });
  await page.goto("/my-lab");
  await page
    .getByRole("button", { name: "Add a test", exact: true })
    .first()
    .click();
  const editor = page.getByRole("dialog");
  await editor
    .getByRole("button", { name: "Create custom test", exact: true })
    .click();
  const testName = editor.getByLabel(/^Custom test name/);
  await expect(testName).toHaveAttribute("required", "");
  await editor
    .getByRole("button", { name: "Add custom method", exact: true })
    .click();
  await editor
    .getByRole("button", { name: "Add condition", exact: true })
    .click();
  await editor
    .getByRole("button", { name: "Add condition", exact: true })
    .click();
  await editor.getByLabel("Price", { exact: true }).fill("125");
  await editor
    .getByRole("button", { name: "Save capability", exact: true })
    .click();

  const summary = editor
    .getByRole("alert")
    .filter({ hasText: "Check the highlighted fields" });
  await expect(summary).toContainText("Enter your test name.");
  await expect(summary).toContainText("Enter a name for custom method 1");
  await expect(summary).toContainText("Field 1: Choose a field");
  await expect(summary).toContainText("Field 2: Choose a field");
  await expect(summary).toContainText("Add a currency with the price.");
  await expect(summary).toContainText("Add a pricing basis with the price.");
  await expect(testName).toBeFocused();
  expect(submissions).toBe(0);

  if (testInfo.project.name === "desktop") {
    await editor.locator(".MuiDialogContent-root").evaluate((element) => {
      element.scrollTop = 0;
    });
    await editor.screenshot({
      path: testInfo.outputPath("capability-validation-summary.png"),
      animations: "disabled",
    });
    await testName.fill("Draft fixture test");
    await page.mouse.click(5, 5);
    await expect(editor).toBeVisible();
    await expect(testName).toHaveValue("Draft fixture test");
  }
  await editor.getByRole("button", { name: "Cancel", exact: true }).click();
  await expect(editor).not.toBeVisible();
});

test("saving a capability locks editing and gives a confirmed success message", async ({
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
  let release!: () => void;
  const gate = new Promise<void>((resolve) => {
    release = resolve;
  });
  let submissions = 0;
  let payload: Record<string, unknown> | null = null;
  await page.route("**/api/lab/capabilities", async (route) => {
    if (route.request().method() !== "POST") {
      await route.continue();
      return;
    }
    submissions += 1;
    payload = route.request().postDataJSON();
    await gate;
    await route.continue();
  });
  await page.goto("/my-lab");
  await page
    .getByRole("button", { name: "Add a test", exact: true })
    .first()
    .click();
  const editor = page.getByRole("dialog");
  await editor
    .getByRole("button", { name: "Create custom test", exact: true })
    .click();
  const testName = editor.getByLabel(/^Custom test name/);
  await testName.fill("Feedback fixture test");
  await editor
    .getByRole("button", { name: "Save capability", exact: true })
    .click();
  try {
    await expect(
      editor.getByRole("button", { name: "Saving…", exact: true }),
    ).toBeDisabled();
    await expect(
      editor.getByRole("button", { name: "Cancel", exact: true }),
    ).toBeDisabled();
    await expect(testName).toBeDisabled();
    await expect(editor.locator("fieldset[aria-busy=true]")).toHaveAttribute(
      "inert",
      "",
    );
    await expect.poll(() => submissions).toBe(1);
    await page.keyboard.press("Escape");
    await expect(editor).toBeVisible();
    expect(submissions).toBe(1);
    expect(payload).toMatchObject({
      custom_test: { name: "Feedback fixture test", description: "" },
      capability: { atomic_claims: [], structured_limits: [], method_ids: [] },
    });
    if (testInfo.project.name === "desktop") {
      await editor.screenshot({
        path: testInfo.outputPath("capability-saving-locked.png"),
        animations: "disabled",
      });
    }
  } finally {
    release();
  }
  await expect(editor).not.toBeVisible();
  await expect(
    page.getByRole("alert").filter({ hasText: "Capability added." }),
  ).toBeVisible();
  await expect(
    page.getByRole("button", { name: "Feedback fixture test", exact: true }),
  ).toBeVisible();
  expect(submissions).toBe(1);
});
