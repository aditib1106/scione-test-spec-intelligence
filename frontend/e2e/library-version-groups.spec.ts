import { expect, test, type Page } from "@playwright/test";

test.use({ actionTimeout: 15_000 });

/** Two real, different text PDFs, generated offline without a model or library. */
function pdf(text: string) {
  const stream = `BT /F1 12 Tf 50 730 Td (${text}) Tj ET\n`;
  const objects = [
    "<< /Type /Catalog /Pages 2 0 R >>",
    "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
    "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",
    "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    `<< /Length ${Buffer.byteLength(stream)} >>\nstream\n${stream}endstream`,
  ];
  let content = "%PDF-1.4\n";
  const offsets = [0];
  objects.forEach((object, index) => {
    offsets.push(Buffer.byteLength(content));
    content += `${index + 1} 0 obj\n${object}\nendobj\n`;
  });
  const start = Buffer.byteLength(content);
  content += `xref\n0 ${objects.length + 1}\n0000000000 65535 f \n`;
  content += offsets
    .slice(1)
    .map((offset) => `${String(offset).padStart(10, "0")} 00000 n \n`)
    .join("");
  content += `trailer\n<< /Size ${objects.length + 1} /Root 1 0 R >>\nstartxref\n${start}\n%%EOF\n`;
  return Buffer.from(content);
}

async function uploadVersion(
  page: Page,
  name: string,
  buffer: Buffer,
  date: string,
  existing = false,
) {
  await page.goto("/specifications/library");
  await page
    .getByRole("button", { name: "Add specification", exact: true })
    .click();
  const dialog = page.getByRole("dialog", {
    name: "Add customer specification",
    exact: true,
  });
  if (existing) {
    await dialog
      .getByRole("combobox", { name: "Save under", exact: true })
      .click();
    await page
      .getByRole("option", {
        name: "Versioning Customer · VER-201",
        exact: true,
      })
      .click();
    await expect(
      dialog.getByLabel("Customer company", { exact: true }),
    ).toHaveValue("Versioning Customer");
    await expect(
      dialog.getByLabel("Specification number", { exact: true }),
    ).toHaveValue("VER-201");
    await expect(
      dialog.getByLabel("Specification title", { exact: true }),
    ).toHaveValue("Coating qualification");
  } else {
    await dialog
      .getByLabel("Customer company", { exact: true })
      .fill("Versioning Customer");
    await dialog
      .getByLabel("Specification number", { exact: true })
      .fill("VER-201");
    await dialog
      .getByLabel("Specification title", { exact: true })
      .fill("Coating qualification");
  }
  await dialog.getByLabel("Specification date", { exact: true }).fill(date);
  await dialog
    .locator('input[type="file"]')
    .setInputFiles({ name, mimeType: "application/pdf", buffer });
  const uploaded = page.waitForResponse(
    (response) =>
      new URL(response.url()).pathname === "/api/specifications" &&
      response.request().method() === "POST",
  );
  await dialog
    .getByRole("button", { name: "Save & open PDF", exact: true })
    .click();
  const receipt = await uploaded;
  expect(receipt.ok()).toBeTruthy();
  const { document_id: id } = await receipt.json();
  await expect(page).toHaveURL(new RegExp(`/specifications\\?document=${id}$`));
  return id as string;
}

test("two real PDF uploads group through Save under and keep each dated source after version removal", async ({
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
  let modelPosts = 0;
  page.on("request", (sent) => {
    if (
      sent.method() === "POST" &&
      /\/api\/specifications\/[^/]+\/(analysis|matching)$/.test(
        new URL(sent.url()).pathname,
      )
    )
      modelPosts++;
  });
  const firstPdf = pdf(
    "Coating qualification - specification date 2026-11-01 - temperature 20 C",
  );
  const secondPdf = pdf(
    "Coating qualification - specification date 2026-11-24 - temperature 25 C",
  );
  const firstId = await uploadVersion(
    page,
    "coating-2026-11-01.pdf",
    firstPdf,
    "2026-11-01",
  );
  const first = await (await request.get(`/api/library/${firstId}`)).json();
  const secondId = await uploadVersion(
    page,
    "coating-2026-11-24.pdf",
    secondPdf,
    "2026-11-24",
    true,
  );
  expect(secondId).not.toBe(firstId);
  const second = await (await request.get(`/api/library/${secondId}`)).json();
  expect(second.document.family_id).toBe(first.document.family_id);
  expect(second.versions).toHaveLength(2);
  expect(
    (
      await (await request.get(`/api/specifications/${firstId}/pdf`)).body()
    ).equals(firstPdf),
  ).toBe(true);
  expect(
    await (await request.get(`/api/library/${firstId}`)).json(),
  ).toMatchObject({ document: first.document });

  await page.goto("/specifications/library");
  const listing = page.getByRole("table", {
    name: "Saved customer specifications",
    exact: true,
  });
  await expect(listing.getByRole("row")).toHaveCount(2);
  await page
    .getByRole("textbox", { name: "Search specification library", exact: true })
    .fill("coating-2026-11-01.pdf");
  await expect(listing.getByRole("row")).toHaveCount(2);
  await expect(listing).toContainText("coating-2026-11-24.pdf");
  await listing
    .getByRole("button", { name: "2 versions", exact: true })
    .click();
  const versions = page.getByRole("dialog", {
    name: "Specification versions",
    exact: true,
  });
  await expect(versions).toContainText("Versioning Customer");
  const entries = versions
    .getByRole("list", { name: "Saved specification versions", exact: true })
    .getByRole("button");
  await expect(entries).toHaveCount(2);
  await expect(entries.nth(0)).toContainText("Nov 24, 2026");
  await expect(entries.nth(1)).toContainText("Nov 1, 2026");
  await expect(
    versions.getByText("coating-2026-11-01.pdf", { exact: true }),
  ).toBeVisible();
  await expect(
    versions.getByText("coating-2026-11-24.pdf", { exact: true }),
  ).toBeVisible();
  await versions.screenshot({
    path: testInfo.outputPath("dated-specification-versions.png"),
    animations: "disabled",
  });
  await entries.nth(1).click();
  await expect(page).toHaveURL(new RegExp(`document=${firstId}$`));
  await expect(
    page
      .getByRole("main")
      .getByRole("link", { name: "Read PDF & review", exact: true }),
  ).toHaveAttribute("href", `/specifications?document=${firstId}`);
  await expect(
    page
      .getByRole("main")
      .getByRole("link", { name: "Gap report", exact: true }),
  ).toHaveAttribute("href", `/reports?document=${firstId}`);
  await page.getByRole("button", { name: "Versions (2)", exact: true }).click();
  await expect(versions.getByText("Viewing", { exact: true })).toHaveCount(1);
  await versions
    .getByRole("button", {
      name: "Open version Nov 24, 2026: coating-2026-11-24.pdf",
      exact: true,
    })
    .click();
  await expect(page).toHaveURL(new RegExp(`document=${secondId}$`));
  await page
    .getByRole("button", { name: "Specification actions", exact: true })
    .click();
  await page
    .getByRole("menuitem", { name: "Remove from library", exact: true })
    .click();
  await page
    .getByRole("dialog", { name: "Remove this specification?", exact: true })
    .getByRole("button", { name: "Remove from library", exact: true })
    .click();
  await expect(listing).toBeVisible();
  await expect(listing).toContainText("coating-2026-11-01.pdf");
  await expect(listing).not.toContainText("coating-2026-11-24.pdf");
  await expect(
    listing.getByRole("button", { name: "2 versions", exact: true }),
  ).toHaveCount(0);
  expect(
    (
      await (await request.get(`/api/specifications/${firstId}/pdf`)).body()
    ).equals(firstPdf),
  ).toBe(true);
  expect(
    (
      await (await request.get(`/api/specifications/${secondId}/pdf`)).body()
    ).equals(secondPdf),
  ).toBe(true);
  expect(modelPosts).toBe(0);
});
