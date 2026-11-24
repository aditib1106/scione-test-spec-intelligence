import { expect, test } from "@playwright/test";
import type { Session } from "../src/auth";
import type { LibraryDetail } from "../src/libraryTypes";
import type { SpecificationData } from "../src/types";
import { isSamplePlanClaim } from "../src/components/sourceReviewClaims";
import { editRequirement } from "./sourceEditor";
import { reviewAction } from "./workspaceActions";

test.use({ actionTimeout: 15_000 });

test("library save failures are visible and a completed upload survives conflict retries", async ({
  page,
}, testInfo) => {
  if (testInfo.project.name === "desktop")
    await page.setViewportSize({ width: 1366, height: 768 });
  const id = "recovery-uploaded-pdf";
  const detail: LibraryDetail = {
    document: {
      document_id: id,
      company_name: null,
      specification_id: null,
      title: null,
      revision: null,
      specification_date: null,
      family_id: id,
      version: 0,
      original_name: "recovery-customer.pdf",
      created_at: "2026-11-24T12:00:00Z",
      updated_at: "2026-11-24T12:00:00Z",
      requirement_count: 0,
      review_count: 0,
      run_id: null,
      page_count: 2,
    },
    versions: [],
    requirements: [],
  };
  let uploads = 0;
  const metadata: unknown[] = [];
  const inference: string[] = [];
  page.on("request", (sent) => {
    if (sent.method() === "POST" && /\/(analysis|matching)$/.test(sent.url()))
      inference.push(sent.url());
  });
  await page.route("**/api/library", (route) =>
    route.fulfill({ json: { documents: [] } }),
  );
  await page.route(`**/api/library/${id}`, (route) =>
    route.fulfill({ json: detail }),
  );
  await page.route("**/api/specifications", (route) => {
    uploads++;
    return route.fulfill({
      json: {
        document_id: id,
        original_name: detail.document.original_name,
        reused: false,
      },
    });
  });
  await page.route(`**/api/library/${id}/metadata`, (route) => {
    metadata.push(route.request().postDataJSON());
    return route.fulfill({
      status: 409,
      json: {
        detail: "This specification changed. Check these details and retry.",
      },
    });
  });
  await page.goto("/specifications/library");
  await page
    .getByRole("button", { name: "Add specification", exact: true })
    .click();
  const dialog = page.getByRole("dialog", {
    name: "Add customer specification",
    exact: true,
  });
  const save = dialog.getByRole("button", {
    name: "Save & open PDF",
    exact: true,
  });
  await save.click();
  const error = dialog.getByRole("alert");
  await expect(error).toContainText("Choose the specification PDF first.");
  await expect(error).toBeFocused();
  await expect(error).toBeInViewport({ ratio: 1 });
  expect(uploads).toBe(0);

  // Repeating the same failure must still bring its alert back into view.
  await dialog
    .getByLabel("Customer company", { exact: true })
    .fill("Recovery customer");
  await save.click();
  await expect(error).toBeFocused();
  await expect(error).toBeInViewport({ ratio: 1 });
  await dialog
    .getByLabel("Specification number", { exact: true })
    .fill("REC-301");
  await dialog
    .getByLabel("Specification title", { exact: true })
    .fill("Retained customer details");
  await dialog
    .getByLabel("Specification date", { exact: true })
    .fill("2026-11-24");
  await dialog.locator('input[type="file"]').setInputFiles({
    name: detail.document.original_name,
    mimeType: "application/pdf",
    buffer: Buffer.from("%PDF-1.4\n% UI-only conflict fixture\n"),
  });
  await save.click();
  await expect(error).toContainText("Check these details and retry.");
  await expect(error).toContainText(
    "Upload complete. Retry to finish saving these details.",
  );
  await expect(error).toBeFocused();
  await expect(error).toBeInViewport({ ratio: 1 });
  await expect(
    dialog.getByLabel("Customer company", { exact: true }),
  ).toHaveValue("Recovery customer");
  await expect(
    dialog.getByRole("button", { name: "Replace PDF", exact: true }),
  ).toBeDisabled();
  await expect(
    dialog.getByRole("button", { name: "Remove PDF", exact: true }),
  ).toBeDisabled();
  expect(uploads).toBe(1);
  expect(metadata).toHaveLength(1);
  await save.click();
  await expect.poll(() => metadata.length).toBe(2);
  await expect(error).toBeFocused();
  expect(uploads).toBe(1);
  expect(metadata[1]).toEqual(metadata[0]);
  expect(metadata[1]).toMatchObject({
    company_name: "Recovery customer",
    specification_id: "REC-301",
    title: "Retained customer details",
    specification_date: "2026-11-24",
    expected_version: 0,
  });
  expect(inference).toEqual([]);
  await dialog.screenshot({
    path: testInfo.outputPath("library-visible-save-error.png"),
    animations: "disabled",
  });
  await dialog.getByRole("button", { name: "Cancel", exact: true }).click();
});

test("source validation opens the evidence field while retaining the new test draft", async ({
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
  const receipt = await (
    await request.post("/api/specifications/worked-example")
  ).json();
  const id = receipt.document_id;
  const path = `/api/specifications/${id}`;
  const source = (await (await request.get(path)).json()) as SpecificationData;
  const writes: string[] = [];
  page.on("request", (sent) => {
    if (
      sent.method() !== "GET" &&
      new URL(sent.url()).pathname.startsWith("/api/")
    )
      writes.push(sent.method());
  });
  await page.goto(`/specifications?document=${id}`);
  await (await reviewAction(page, "Add missing test")).click();
  const editor = page.getByRole("dialog", {
    name: "Add missing test",
    exact: true,
  });
  const overview = editor.getByRole("tab", {
    name: "Test & method",
    exact: true,
  });
  const evidence = editor.getByRole("tab", {
    name: "Source evidence",
    exact: true,
  });
  const save = editor.getByRole("button", { name: "Add test", exact: true });
  await save.click();
  await expect(editor.getByRole("alert").first()).toContainText(
    "Enter the test name stated in the source.",
  );
  await expect(overview).toHaveAttribute("aria-selected", "true");
  await editor.getByLabel(/^Test name/).fill("Retained custom inspection");
  await expect(editor.getByRole("alert")).toHaveCount(0);
  await editor.getByRole("button", { name: "Add method", exact: true }).click();
  await editor.getByLabel(/^Method identifier/).fill("RECOVERY-METHOD-1");
  await editor.getByRole("tab", { name: "Conditions", exact: true }).click();
  await editor.getByRole("button", { name: "Add", exact: true }).click();
  await editor.getByLabel(/^Parameter name/).fill("Temperature");
  await editor.getByLabel(/^Customer requirement/).fill("23 °C");
  await overview.click();
  await save.click();
  await expect(evidence).toHaveAttribute("aria-selected", "true");
  await expect(editor.getByRole("alert").first()).toContainText(
    "Keep the exact source quotation",
  );
  await expect(editor.getByLabel(/^Source quotation/)).toHaveValue("");
  await editor
    .getByLabel(/^Source quotation/)
    .fill("Mark a repeatable five-position map on each panel.");
  await expect(editor.getByRole("alert")).toHaveCount(0);
  await editor.getByLabel(/^Source page/).fill("0");
  await overview.click();
  await save.click();
  await expect(evidence).toHaveAttribute("aria-selected", "true");
  await expect(editor.getByRole("alert").first()).toContainText(
    `Select a source page from 1 to ${source.document.page_count}.`,
  );
  await overview.click();
  await expect(editor.getByRole("alert").first()).toContainText(
    `Select a source page from 1 to ${source.document.page_count}.`,
  );
  await save.click();
  await expect(evidence).toHaveAttribute("aria-selected", "true");
  await editor.getByLabel(/^Source page/).fill("1");
  await expect(editor.getByRole("alert")).toHaveCount(0);
  await expect(editor.getByLabel(/^Source quotation/)).toHaveValue(
    "Mark a repeatable five-position map on each panel.",
  );
  await overview.click();
  await expect(editor.getByLabel(/^Test name/)).toHaveValue(
    "Retained custom inspection",
  );
  await expect(editor.getByLabel(/^Method identifier/)).toHaveValue(
    "RECOVERY-METHOD-1",
  );
  await editor.getByRole("tab", { name: "Conditions", exact: true }).click();
  await expect(editor.getByLabel(/^Parameter name/)).toHaveValue("Temperature");
  await expect(editor.getByLabel(/^Customer requirement/)).toHaveValue("23 °C");
  await editor.screenshot({
    path: testInfo.outputPath("source-recovery-retained-draft.png"),
    animations: "disabled",
  });
  await editor.getByRole("button", { name: "Cancel", exact: true }).click();
  const after = (await (await request.get(path)).json()) as SpecificationData;
  expect(after.run).toEqual(source.run);
  expect(after.facts_version).toBe(source.facts_version);
  expect(after.review).toEqual(source.review);
  expect(writes).toEqual([]);
});

test("invalid numeric corrections stay on their current parameter tab", async ({
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
  const receipt = await (
    await request.post("/api/specifications/worked-example")
  ).json();
  const id = receipt.document_id;
  const source = (await (
    await request.get(`/api/specifications/${id}`)
  ).json()) as SpecificationData;
  const selected = source.run!.extraction.requirements[0];
  const parameter = source.normalized_requirements![selected.id].claims.find(
    (item) =>
      item.kind === "condition" &&
      !isSamplePlanClaim(item) &&
      item.operator === "=" &&
      item.value !== null,
  )!;
  expect(parameter).toBeTruthy();
  let attempts = 0;
  await page.route(`**/api/specifications/${id}/facts`, (route) => {
    attempts++;
    return route.fulfill({
      status: 503,
      json: {
        detail: "Correction save unavailable. Keep this draft and retry.",
      },
    });
  });
  await page.goto(`/specifications?document=${id}&requirement=${selected.id}`);
  const editor = await editRequirement(page, "Conditions", parameter.label);
  await editor.getByLabel("Value", { exact: true }).fill("not-a-number");
  await editor
    .getByRole("button", { name: "Save corrected draft", exact: true })
    .click();
  await expect(editor.getByRole("alert").first()).toContainText(
    `Enter a valid number for ${parameter.label}.`,
  );
  await expect(
    editor.getByRole("tab", { name: "Conditions", exact: true }),
  ).toHaveAttribute("aria-selected", "true");
  await expect(editor.getByLabel("Value", { exact: true })).toHaveValue(
    "not-a-number",
  );
  expect(attempts).toBe(0);
  await editor.getByLabel("Value", { exact: true }).fill(parameter.value!);
  await expect(editor.getByRole("alert")).toHaveCount(0);
  await editor
    .getByRole("button", { name: "Save corrected draft", exact: true })
    .click();
  const backendError = editor.getByRole("alert");
  await expect(backendError).toContainText("Keep this draft and retry.");
  await editor
    .getByLabel("Value", { exact: true })
    .fill(String(Number(parameter.value) + 1));
  await expect(backendError).toContainText("Keep this draft and retry.");
  expect(attempts).toBe(1);
  await editor
    .getByRole("button", { name: "Save corrected draft", exact: true })
    .click();
  await expect.poll(() => attempts).toBe(2);
  await expect(backendError).toContainText("Keep this draft and retry.");
  await editor.getByRole("button", { name: "Cancel", exact: true }).click();
  expect(
    (await (await request.get(`/api/specifications/${id}`)).json())
      .facts_version,
  ).toBe(source.facts_version);
});

test("an earlier passage error does not hide a still-invalid source page", async ({
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
  const receipt = await (
    await request.post("/api/specifications/worked-example")
  ).json();
  const id = receipt.document_id;
  const source = (await (
    await request.get(`/api/specifications/${id}`)
  ).json()) as SpecificationData;
  const displayed = structuredClone(source);
  const selected = displayed.run!.extraction.requirements[0];
  const quote = selected.evidence[0].quote;
  selected.evidence = [
    { page: 1, quote },
    { page: 2, quote },
  ];
  // A display-only fixture exercises both passage controls; nothing is persisted.
  await page.route(`**/api/specifications/${id}`, (route) =>
    route.fulfill({ json: displayed }),
  );
  const writes: string[] = [];
  page.on("request", (sent) => {
    if (
      sent.method() !== "GET" &&
      new URL(sent.url()).pathname.startsWith("/api/")
    )
      writes.push(sent.method());
  });
  await page.goto(`/specifications?document=${id}&requirement=${selected.id}`);
  await page
    .getByRole("button", { name: "Correct extracted content", exact: true })
    .click();
  const editor = page.getByRole("dialog", {
    name: "Correct extracted content",
    exact: true,
  });
  await editor
    .getByRole("tab", { name: "Source evidence", exact: true })
    .click();
  const choose = async (label: string) => {
    await editor.getByLabel("Quoted passage", { exact: true }).click();
    await page.getByRole("option", { name: label, exact: true }).click();
  };
  await choose("Page 2 · Passage 2");
  await editor.getByLabel(/^Source page/).fill("0");
  const save = editor.getByRole("button", {
    name: "Save corrected draft",
    exact: true,
  });
  await save.click();
  await expect(editor.getByRole("alert").first()).toContainText(
    `Select a source page from 1 to ${source.document.page_count}.`,
  );
  await choose("Page 1 · Passage 1");
  await editor.getByLabel(/^Source quotation/).clear();
  await expect(editor.getByRole("alert").first()).toContainText(
    `Select a source page from 1 to ${source.document.page_count}.`,
  );
  await choose("Page 0 · Passage 2");
  await editor.getByLabel(/^Source page/).fill("2");
  await expect(editor.getByRole("alert")).toHaveCount(0);
  await save.click();
  await expect(
    editor.getByLabel("Quoted passage", { exact: true }),
  ).toContainText("Page 1 · Passage 1");
  await expect(editor.getByRole("alert").first()).toContainText(
    "Keep the exact source quotation",
  );
  await editor.getByLabel(/^Source quotation/).fill(quote);
  await expect(editor.getByRole("alert")).toHaveCount(0);
  await editor.getByRole("button", { name: "Cancel", exact: true }).click();
  expect(writes).toEqual([]);
  const after = (await (
    await request.get(`/api/specifications/${id}`)
  ).json()) as SpecificationData;
  expect(after.run).toEqual(source.run);
  expect(after.facts_version).toBe(source.facts_version);
});

test("password backdrop clicks retain a draft and pending saves remain locked", async ({
  page,
}) => {
  const session: Session = {
    authenticated: true,
    enabled: true,
    csrf_token: "synthetic-recovery-csrf",
    user: {
      id: "synthetic-password-recovery",
      username: "fixture-lab-user",
      display_name: "Fixture Lab User",
      role: "lab",
      lab_id: "fixture-lab",
      lab_name: "Fixture Lab",
    },
  };
  await page.route("**/api/auth/session", (route) =>
    route.fulfill({ json: session }),
  );
  let release!: () => void;
  const gate = new Promise<void>((resolve) => {
    release = resolve;
  });
  let saves = 0;
  await page.route("**/api/auth/password", async (route) => {
    saves++;
    await gate;
    await route.fulfill({
      status: 503,
      json: { detail: "Fixture save unavailable. Your draft is retained." },
    });
  });
  await page.goto("/specifications/library");
  const openPassword = async () => {
    await page
      .getByRole("button", { name: "Account menu", exact: true })
      .click();
    await page
      .getByRole("menuitem", { name: "Change password", exact: true })
      .click();
  };
  await openPassword();
  const dialog = page.getByRole("dialog", {
    name: "Change password",
    exact: true,
  });
  const current = dialog.getByLabel(/^Current password/);
  const next = dialog.getByLabel(/^New password/);
  const confirmation = dialog.getByLabel(/^Confirm new password/);
  await current.fill("Synthetic-current-fixture-password");
  await next.fill("Synthetic-new-fixture-password");
  await confirmation.fill("Synthetic-new-fixture-password");
  await page.mouse.click(2, 2);
  await expect(dialog).toBeVisible();
  await expect(current).toHaveValue("Synthetic-current-fixture-password");
  await expect(next).toHaveValue("Synthetic-new-fixture-password");
  await expect(confirmation).toHaveValue("Synthetic-new-fixture-password");
  expect(saves).toBe(0);
  await dialog
    .getByRole("button", { name: "Update password", exact: true })
    .click();
  await expect.poll(() => saves).toBe(1);
  await expect(current).toBeDisabled();
  await expect(next).toBeDisabled();
  await expect(confirmation).toBeDisabled();
  await expect(
    dialog.getByRole("button", { name: "Cancel", exact: true }),
  ).toBeDisabled();
  await page.mouse.click(2, 2);
  await page.keyboard.press("Escape");
  await expect(dialog).toBeVisible();
  expect(saves).toBe(1);
  release();
  await expect(dialog.getByRole("alert")).toContainText(
    "Your draft is retained.",
  );
  await expect(current).toHaveValue("Synthetic-current-fixture-password");
  await expect(next).toHaveValue("Synthetic-new-fixture-password");
  await expect(confirmation).toHaveValue("Synthetic-new-fixture-password");
  await dialog.getByRole("button", { name: "Cancel", exact: true }).click();
  await openPassword();
  await expect(current).toHaveValue("");
  await expect(next).toHaveValue("");
  await expect(confirmation).toHaveValue("");
  await dialog.getByRole("button", { name: "Cancel", exact: true }).click();
  expect(saves).toBe(1);
});
