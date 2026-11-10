import { expect, test, type Page } from "@playwright/test";

const password = "Synthetic-fixture-password-2026";

async function signIn(page: Page, username: string) {
  await page.goto("/login");
  await page.getByRole("textbox", { name: /^Username/ }).fill(username);
  await page.getByLabel(/^Password/).fill(password);
  await page.getByRole("button", { name: "Sign in", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "Specification library", exact: true }),
  ).toBeVisible();
}

test("a lab signs in to its own files and keeps the chosen upload filename", async ({
  page,
}) => {
  await signIn(page, "labalpha");
  const listing = await (await page.request.get("/api/library")).json();
  expect(listing.documents).toHaveLength(1);
  const id = listing.documents[0].document_id;
  const source = await (
    await page.request.get(`/api/specifications/${id}`)
  ).json();
  expect(source.run).not.toHaveProperty("raw_response");
  expect(source.run).not.toHaveProperty("provider");
  expect(source.run).not.toHaveProperty("model");
  expect(source.run.extraction.requirements).toHaveLength(1);
  const original = await page.request.get(`/api/specifications/${id}/pdf`);
  expect(original.ok()).toBeTruthy();
  await page
    .getByRole("button", { name: "Add specification", exact: true })
    .click();
  const dialog = page.getByRole("dialog");
  await dialog.locator('input[type="file"]').setInputFiles({
    name: "my-uploaded-customer-spec.pdf",
    mimeType: "application/pdf",
    buffer: await original.body(),
  });
  await dialog
    .getByRole("button", { name: "Save & open PDF", exact: true })
    .click();
  await expect(page).toHaveURL(new RegExp(`/specifications\\?document=${id}`));
  await expect(
    page.getByText("my-uploaded-customer-spec.pdf", { exact: true }).first(),
  ).toBeVisible();
  await expect(
    page.getByText(/same PDF|duplicate upload|already in your workspace/i),
  ).toHaveCount(0);
  await expect(page.getByText(/PRIVATE INFERENCE ARTIFACT/)).toHaveCount(0);
  await expect(
    page.getByRole("combobox", { name: "Provider", exact: true }),
  ).toHaveCount(0);
  await expect(
    page.getByRole("button", { name: "Developer report tools", exact: true }),
  ).toHaveCount(0);
  await page.screenshot({
    path: `test-results/auth-lab-review-${test.info().project.name}.png`,
    fullPage: true,
  });
  await page.goto("/developer");
  await expect(page).toHaveURL(/\/specifications\/library$/);
  expect((await page.request.get(`/api/diagnostics/${id}`)).status()).toBe(403);
  expect((await page.request.get("/api/developer/metrics")).status()).toBe(403);
  await page.goto("/monitoring");
  await expect(page).toHaveURL(/\/specifications\/library$/);
});

test("signing out and signing in as another lab never carries the previous file", async ({
  page,
  context,
}) => {
  await signIn(page, "labalpha");
  const first = await (await page.request.get("/api/library")).json();
  const foreignId = first.documents[0].document_id;
  const otherTab = await context.newPage();
  await otherTab.goto("/specifications/library");
  await expect(
    otherTab.getByRole("heading", {
      name: "Specification library",
      exact: true,
    }),
  ).toBeVisible();
  await page.getByRole("button", { name: "Account menu", exact: true }).click();
  await page.getByRole("menuitem", { name: "Sign out", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "Welcome back", exact: true }),
  ).toBeVisible();
  await expect(
    otherTab.getByRole("heading", { name: "Welcome back", exact: true }),
  ).toBeVisible();
  expect((await page.request.get("/api/library")).status()).toBe(401);
  await page.getByRole("textbox", { name: /^Username/ }).fill("labbeta");
  await page.getByLabel(/^Password/).fill(password);
  await page.getByRole("button", { name: "Sign in", exact: true }).click();
  await expect(
    page.getByText("beta-customer-spec.pdf", { exact: false }).first(),
  ).toBeVisible();
  await expect(
    otherTab.getByText("beta-customer-spec.pdf", { exact: false }).first(),
  ).toBeVisible();
  await expect(
    otherTab.getByText("my-uploaded-customer-spec.pdf", { exact: false }),
  ).toHaveCount(0);
  await otherTab.close();
  await expect(
    page.getByText("my-uploaded-customer-spec.pdf", { exact: false }),
  ).toHaveCount(0);
  await expect(
    page.getByText("alpha-customer-spec.pdf", { exact: false }),
  ).toHaveCount(0);
  expect(
    (await page.request.get(`/api/specifications/${foreignId}`)).status(),
  ).toBe(404);
  await page.goto("/catalog");
  await expect(
    page.getByRole("heading", { name: "Test catalog", exact: true }),
  ).toBeVisible();
  await expect(
    page.getByRole("button", { name: /Edit|Add test|Delete/ }),
  ).toHaveCount(0);
});

test("a maintainer selects a lab and synchronizes isolated workspaces across tabs", async ({
  page,
  context,
}) => {
  await page.goto("/login");
  await page.getByRole("textbox", { name: /^Username/ }).fill("maintainer");
  await page.getByLabel(/^Password/).fill(password);
  await page.getByRole("button", { name: "Sign in", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "Specification library", exact: true }),
  ).toBeVisible();
  await expect(
    page.getByRole("combobox", { name: /^Viewing lab/ }),
  ).toContainText("Laboratory Alpha");
  await expect(
    page.getByRole("heading", { name: "Specification library", exact: true }),
  ).toBeVisible();
  let listing = await (await page.request.get("/api/library")).json();
  expect(listing.documents).toHaveLength(1);
  expect(listing.documents[0].original_name).toContain("customer-spec.pdf");
  const otherTab = await context.newPage();
  await otherTab.goto("/specifications/library");
  await expect(
    otherTab.getByRole("combobox", { name: /^Viewing lab/ }),
  ).toContainText("Laboratory Alpha");
  await page.getByRole("combobox", { name: /^Viewing lab/ }).click();
  await page
    .getByRole("option", { name: "Laboratory Beta", exact: true })
    .click();
  await expect(
    page.getByRole("combobox", { name: /^Viewing lab/ }),
  ).toContainText("Laboratory Beta");
  await expect(
    page.getByText("beta-customer-spec.pdf", { exact: false }).first(),
  ).toBeVisible();
  listing = await (await page.request.get("/api/library")).json();
  expect(listing.documents).toHaveLength(1);
  expect(listing.documents[0].original_name).toBe("beta-customer-spec.pdf");
  await expect(
    otherTab.getByRole("combobox", { name: /^Viewing lab/ }),
  ).toContainText("Laboratory Beta");
  await expect(
    otherTab.getByText("beta-customer-spec.pdf", { exact: false }).first(),
  ).toBeVisible();
  await expect(
    otherTab.getByText("my-uploaded-customer-spec.pdf", { exact: false }),
  ).toHaveCount(0);
  await otherTab.close();
  await page.goto("/developer");
  await expect(
    page.getByRole("heading", { name: "Developer Tools", exact: true }),
  ).toBeVisible();
  await page.goto("/monitoring");
  await expect(
    page.getByRole("heading", { name: "Monitoring", exact: true }),
  ).toBeVisible();
  await expect(
    page.getByText("Recorded operations", { exact: true }),
  ).toBeVisible();
});

test("a lab can review the report, export PDF, and exclude then restore a test", async ({
  page,
}) => {
  await signIn(page, "labalpha");
  const library = await (await page.request.get("/api/library")).json();
  const id = library.documents[0].document_id;
  await page.goto(`/reports?document=${id}`);
  await expect(
    page.getByRole("heading", { name: "Gap report", exact: true }),
  ).toBeVisible();
  const accept = page.getByRole("button", {
    name: "Accept assessment",
    exact: true,
  });
  if (await accept.count()) await accept.click();
  await expect(
    page.getByText("Assessment reviewed", { exact: true }),
  ).toBeVisible();
  await page.getByRole("tab", { name: "PDF & download", exact: true }).click();
  const download = page.getByRole("link", {
    name: "Download PDF",
    exact: true,
  });
  await expect(download).toBeVisible();
  const href = await download.getAttribute("href");
  expect(href).toBeTruthy();
  const pdf = await page.request.get(href!);
  expect(pdf.ok()).toBeTruthy();
  expect((await pdf.body()).subarray(0, 4).toString()).toBe("%PDF");
  await page.getByRole("tab", { name: "Comparison", exact: true }).click();
  await page
    .getByRole("button", { name: "Remove from this report", exact: true })
    .click();
  let dialog = page.getByRole("dialog");
  await dialog
    .getByLabel("Scope note (optional)", { exact: true })
    .fill("Customer deferred this test.");
  await dialog
    .getByRole("button", { name: "Remove test", exact: true })
    .click();
  await expect(
    page.getByRole("button", { name: "Restore to this report", exact: true }),
  ).toBeVisible();
  const excluded = await (
    await page.request.get(`/api/comparisons/${id}`)
  ).json();
  expect(excluded.requirements[0].comparison.status).toBe("excluded");
  await page
    .getByRole("button", { name: "Restore to this report", exact: true })
    .click();
  dialog = page.getByRole("dialog");
  await dialog
    .getByRole("button", { name: "Restore test", exact: true })
    .click();
  await expect(
    page.getByRole("button", { name: "Remove from this report", exact: true }),
  ).toBeVisible();
  const restored = await (
    await page.request.get(`/api/comparisons/${id}`)
  ).json();
  expect(restored.requirements[0].comparison.status).not.toBe("excluded");
});

test("changing a password validates input, revokes old sessions and accepts only the new password", async ({
  page,
  context,
  browser,
}, testInfo) => {
  const username = `password.${testInfo.project.name}`;
  const updatedPassword = "Synthetic-new-password-2026";
  const independent = await browser.newContext({
    baseURL: "http://127.0.0.1:8012",
  });
  try {
    await signIn(page, username);
    const independentPage = await independent.newPage();
    await signIn(independentPage, username);
    const otherTab = await context.newPage();
    await otherTab.goto("/specifications/library");
    await expect(
      otherTab.getByRole("heading", {
        name: "Specification library",
        exact: true,
      }),
    ).toBeVisible();
    await page
      .getByRole("button", { name: "Account menu", exact: true })
      .click();
    await page
      .getByRole("menuitem", { name: "Change password", exact: true })
      .click();
    const dialog = page.getByRole("dialog", {
      name: "Change password",
      exact: true,
    });
    const current = dialog.getByLabel(/^Current password/);
    const next = dialog.getByLabel(/^New password/);
    const confirm = dialog.getByLabel(/^Confirm new password/);
    await expect(current).toHaveAttribute("type", "password");
    await expect(next).toHaveAttribute("type", "password");
    await expect(confirm).toHaveAttribute("type", "password");
    await current.fill(password);
    await next.fill("too-short");
    await confirm.fill("too-short");
    await dialog
      .getByRole("button", { name: "Update password", exact: true })
      .click();
    await expect(dialog.getByRole("alert")).toContainText(
      "between 15 and 1,024",
    );
    await next.fill(updatedPassword);
    await confirm.fill("different-fixture-password");
    await dialog
      .getByRole("button", { name: "Update password", exact: true })
      .click();
    await expect(dialog.getByRole("alert")).toContainText("do not match");
    await confirm.fill(updatedPassword);
    await current.fill("incorrect-fixture-password");
    await dialog
      .getByRole("button", { name: "Update password", exact: true })
      .click();
    await expect(dialog.getByRole("alert")).toContainText(
      "current password is incorrect",
    );
    await current.fill(password);
    await dialog
      .getByRole("button", { name: "Update password", exact: true })
      .click();
    await expect(
      page.getByRole("heading", { name: "Welcome back", exact: true }),
    ).toBeVisible();
    await expect(
      page.getByText("Password updated. Sign in with your new password.", {
        exact: true,
      }),
    ).toBeVisible();
    await expect(
      otherTab.getByRole("heading", { name: "Welcome back", exact: true }),
    ).toBeVisible();
    expect((await independent.request.get("/api/library")).status()).toBe(401);
    await page.getByRole("textbox", { name: /^Username/ }).fill(username);
    await page.getByLabel(/^Password/).fill(password);
    await page.getByRole("button", { name: "Sign in", exact: true }).click();
    await expect(
      page.getByRole("alert").filter({ hasText: "username or password" }),
    ).toBeVisible();
    await page.getByLabel(/^Password/).fill(updatedPassword);
    await page.getByRole("button", { name: "Sign in", exact: true }).click();
    await expect(
      page.getByRole("heading", { name: "Specification library", exact: true }),
    ).toBeVisible();
    await otherTab.close();
  } finally {
    await independent.close();
  }
});
