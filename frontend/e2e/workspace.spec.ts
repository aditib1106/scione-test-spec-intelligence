import {
  expect,
  test,
  type APIRequestContext,
  type Page,
} from "@playwright/test";

const emptyBase = "http://127.0.0.1:8010";
const seededBase = "http://127.0.0.1:8011";
type SeedReportResponse = {
  report: {
    lines: {
      requirement: { id: string; original_test_name: string };
      status: string;
    }[];
  };
  line_states: Record<string, { support_basis: string | null }>;
};

async function resetEmptyWorkspace(request: APIRequestContext) {
  const response = await request.post(`${emptyBase}/api/workspace/reset`, {
    data: { acknowledged: true },
  });
  expect(
    response.ok(),
    `empty fixture reset returned ${response.status()}`,
  ).toBeTruthy();
}

async function navigateTo(page: Page, label: string) {
  if (
    await page
      .getByRole("button", { name: "Open navigation" })
      .isVisible()
      .catch(() => false)
  ) {
    await page.getByRole("button", { name: "Open navigation" }).click();
    await page.getByRole("link", { name: label }).click();
  } else {
    await page.getByRole("link", { name: label }).click();
  }
}

async function readJson<T>(
  request: APIRequestContext,
  url: string,
): Promise<T> {
  const response = await request.get(url);
  expect(
    response.ok(),
    `GET ${url} returned ${response.status()}`,
  ).toBeTruthy();
  return response.json() as Promise<T>;
}

async function chooseMuiOption(
  page: Page,
  label: string,
  option: string | RegExp,
) {
  await page.getByRole("combobox", { name: label }).click();
  await page.getByRole("option", { name: option }).click();
}

test("empty lab can load, edit, save, reload, and cancel a capability", async ({
  page,
  request,
}, testInfo) => {
  await resetEmptyWorkspace(request);
  await page.goto(`${emptyBase}/my-lab`);
  await expect(
    page.getByText("Start with your lab’s capabilities"),
  ).toBeVisible();

  await page.getByRole("button", { name: "Load example lab" }).click();
  await expect(
    page.getByText("Pencil hardness", { exact: true }),
  ).toBeVisible();
  const lab = await readJson<{ capabilities: { canonical_test_id: string }[] }>(
    request,
    `${emptyBase}/api/lab`,
  );
  const pencilIndex = lab.capabilities.findIndex(
    (item) => item.canonical_test_id === "pencil_hardness",
  );
  expect(pencilIndex).toBeGreaterThanOrEqual(0);

  await page
    .getByRole("button", { name: "Edit", exact: true })
    .nth(pencilIndex)
    .click();
  const dialog = page.getByRole("dialog");
  await expect(dialog).toBeVisible();
  await dialog.getByLabel("Price").fill("101.25");
  await dialog.getByLabel("Currency").fill("USD");
  await dialog.getByLabel("Pricing basis").fill("per panel");
  await dialog.getByLabel("Internal notes").fill("Browser review saved note");

  if (testInfo.project.name === "narrow") {
    await expect(dialog.getByRole("button", { name: "Cancel" })).toBeVisible();
    await expect(
      dialog.getByRole("button", { name: "Save capability" }),
    ).toBeVisible();
    const dimensions = await page.evaluate(() => ({
      viewport: document.documentElement.clientWidth,
      content: document.documentElement.scrollWidth,
    }));
    expect(dimensions.content).toBeLessThanOrEqual(dimensions.viewport + 1);
  }
  await dialog.getByRole("button", { name: "Save capability" }).click();
  await expect(
    page.getByText("USD 101.25 / per panel", { exact: true }),
  ).toBeVisible();
  await expect
    .poll(async () => {
      const saved = await readJson<{
        capabilities: {
          canonical_test_id: string;
          price: string | null;
          notes: string | null;
        }[];
      }>(request, `${emptyBase}/api/lab`);
      const pencil = saved.capabilities.find(
        (item) => item.canonical_test_id === "pencil_hardness",
      );
      return [pencil?.price, pencil?.notes];
    })
    .toEqual(["101.25", "Browser review saved note"]);

  await page.reload();
  await expect(
    page.getByText("USD 101.25 / per panel", { exact: true }),
  ).toBeVisible();
  await expect(page.getByText("Browser review saved note")).toBeVisible();
  await page
    .getByRole("button", { name: "Edit", exact: true })
    .nth(pencilIndex)
    .click();
  const reopened = page.getByRole("dialog");
  await reopened
    .getByLabel("Internal notes")
    .fill("This edit should be cancelled");
  if (testInfo.project.name === "narrow")
    await expect(
      reopened.getByRole("button", { name: "Cancel" }),
    ).toBeVisible();
  await reopened.getByRole("button", { name: "Cancel" }).click();
  await expect
    .poll(async () => {
      const saved = await readJson<{
        capabilities: { canonical_test_id: string; notes: string | null }[];
      }>(request, `${emptyBase}/api/lab`);
      return saved.capabilities.find(
        (item) => item.canonical_test_id === "pencil_hardness",
      )?.notes;
    })
    .toBe("Browser review saved note");
  await page
    .getByRole("button", { name: "Edit", exact: true })
    .nth(pencilIndex)
    .click();
  const conflictDialog = page.getByRole("dialog");
  await conflictDialog
    .getByLabel("Internal notes")
    .fill("Retain this unsaved conflict draft");
  const otherSessionLab = await readJson<Record<string, unknown>>(
    request,
    `${emptyBase}/api/lab`,
  );
  const concurrent = await request.put(`${emptyBase}/api/lab`, {
    data: {
      ...otherSessionLab,
      description: "Updated from another local session.",
    },
  });
  expect(concurrent.ok()).toBeTruthy();
  await conflictDialog.getByRole("button", { name: "Save capability" }).click();
  await expect(conflictDialog.getByRole("alert")).toContainText("changed");
  await expect(conflictDialog.getByLabel("Internal notes")).toHaveValue(
    "Retain this unsaved conflict draft",
  );
  await conflictDialog
    .getByRole("button", { name: "Discard draft & reload" })
    .click();
  await expect(
    page.getByText("Updated from another local session.", { exact: true }),
  ).toBeVisible();
  await expect(page.getByText("Browser review saved note")).toBeVisible();
  await page.evaluate(() => window.scrollTo(0, 0));
  await page.screenshot({
    path: testInfo.outputPath("lab-capability.png"),
    fullPage: true,
  });
});

test("catalog search, mobile navigation, and demo PDF import do not analyze", async ({
  page,
  request,
}, testInfo) => {
  await resetEmptyWorkspace(request);
  await page.goto(`${emptyBase}/catalog`);
  const search = page.getByPlaceholder("Search tests, aliases, methods");
  await search.fill("ISO 15184");
  await expect(
    page.getByRole("button", { name: /^Pencil hardness Pilot/ }),
  ).toBeVisible();
  await page.getByRole("button", { name: /^Pencil hardness Pilot/ }).click();
  await expect(
    page.getByText("ISO 15184", { exact: false }).first(),
  ).toBeVisible();
  await search.fill("铅笔硬度");
  await expect(
    page.getByRole("button", { name: /^Pencil hardness Pilot/ }),
  ).toBeVisible();

  await navigateTo(page, "Specifications");
  await page.getByRole("button", { name: "Import demo specification" }).click();
  await expect(page.getByText("No extraction yet")).toBeVisible();
  if (testInfo.project.name === "narrow") {
    await page.getByRole("button", { name: /Source PDF preview/ }).click();
  }
  await expect(page.getByRole("img", { name: /PDF page 1/ })).toBeVisible();
  const documents = await readJson<{ document_id: string }[]>(
    request,
    `${emptyBase}/api/specifications`,
  );
  expect(documents).toHaveLength(1);
  const stored = await readJson<{ run: unknown | null }>(
    request,
    `${emptyBase}/api/specifications/${documents[0].document_id}`,
  );
  expect(stored.run).toBeNull();
  await page.screenshot({
    path: testInfo.outputPath("demo-source-preview.png"),
    fullPage: true,
  });
});

test("seeded source review, lab support, catalog gap, and immutable report history", async ({
  page,
  request,
}, testInfo) => {
  // Keep the shared seeded server intact across projects; reset only the separate empty server.
  await resetEmptyWorkspace(request);
  const documents = await readJson<
    { document_id: string; original_name: string }[]
  >(request, `${seededBase}/api/specifications`);
  const document =
    documents.find(
      (item) => item.original_name === "offline-customer-coating.pdf",
    ) ?? documents[0];
  if (!document)
    throw new Error("Seeded server supplies an offline specification.");
  const sourceUrl = `${seededBase}/api/specifications/${document.document_id}`;
  let source = await readJson<{
    review: { decisions: Record<string, { status: string }> } | null;
  }>(request, sourceUrl);

  await page.goto(
    `${seededBase}/specifications?document=${encodeURIComponent(document.document_id)}`,
  );
  await expect(
    page.getByRole("heading", { name: "Pencil hardness", exact: true }),
  ).toBeVisible();
  if (source.review?.decisions.req_pencil?.status !== "confirmed") {
    await page.getByRole("button", { name: "Confirm & next" }).click();
    await expect
      .poll(async () => {
        source = await readJson<{
          review: { decisions: Record<string, { status: string }> } | null;
        }>(request, sourceUrl);
        return source.review?.decisions.req_pencil?.status;
      })
      .toBe("confirmed");
  }
  await page.getByRole("button", { name: "Continue to lab review" }).click();
  await expect(
    page.getByRole("tab", { name: "Review & confirm" }),
  ).toBeVisible();

  let report = await readJson<SeedReportResponse>(
    request,
    `${seededBase}/api/reports/${document.document_id}`,
  );
  const pencil = report.report.lines.find(
    (line) => line.requirement.id === "req_pencil",
  );
  expect(pencil).toBeTruthy();
  if (
    !report.line_states.req_pencil?.support_basis &&
    pencil?.status === "in_house"
  ) {
    await chooseMuiOption(page, "Show", "All active");
    await page.getByRole("button", { name: /Pencil hardness/ }).click();
    await page.getByRole("button", { name: "Confirm lab support" }).click();
    await expect
      .poll(async () => {
        report = await readJson<SeedReportResponse>(
          request,
          `${seededBase}/api/reports/${document.document_id}`,
        );
        return report.line_states.req_pencil?.support_basis;
      })
      .toBe("human");
  }

  await chooseMuiOption(page, "Show", "All active");
  await page.getByRole("button", { name: /Pencil hardness/ }).click();
  await page.getByRole("button", { name: "Optional quote quantity" }).click();
  await expect(
    page.getByRole("button", { name: "Save quantity", exact: true }),
  ).toBeDisabled();
  await page
    .getByRole("textbox", { name: "Quantity in catalog pricing basis" })
    .fill("2.5");
  await page
    .getByRole("textbox", { name: "Quantity derivation / applicable scope" })
    .fill("Two and a half equivalent panels, checked for overlap.");
  await page
    .getByRole("checkbox", {
      name: "I checked applicability, overlapping records and pricing basis",
    })
    .check();
  await page
    .getByRole("button", { name: "Save quantity", exact: true })
    .click();
  await expect
    .poll(async () => {
      const current = await readJson<{
        report: { totals_by_currency: Record<string, string> };
      }>(request, `${seededBase}/api/reports/${document.document_id}`);
      return current.report.totals_by_currency.USD;
    })
    .toBe("250.000");
  await expect(
    page.getByRole("button", { name: "Remove quantity", exact: true }),
  ).toBeEnabled();

  const gap = report.report.lines.find((line) =>
    ["test_gap", "method_gap", "condition_gap"].includes(line.status),
  );
  expect(gap, "seeded fixture includes a catalog or scope gap").toBeTruthy();
  await chooseMuiOption(page, "Show", "Gaps");
  await page
    .getByRole("button", {
      name: new RegExp(gap!.requirement.original_test_name),
    })
    .click();
  await expect(
    page.getByRole("button", { name: "Confirm lab support" }),
  ).toHaveCount(0);
  await expect(
    page.getByText(
      /already included as a catalog gap|already visible in the report/,
    ),
  ).toBeVisible();
  await page.screenshot({
    path: testInfo.outputPath("report-gap-review.png"),
    fullPage: true,
  });

  await page.getByRole("link", { name: /Check source text/ }).click();
  if (testInfo.project.name === "narrow") {
    await page.getByRole("button", { name: /Source PDF preview/ }).click();
  }
  await expect(page.getByRole("img", { name: /PDF page 2/ })).toBeVisible();
  await page.goBack();

  await page.getByRole("tab", { name: "Report" }).click();
  await page.getByRole("button", { name: "Create draft report" }).click();
  await expect(page.getByRole("link", { name: "Download PDF" })).toBeVisible();
  await expect(
    page.getByRole("img", { name: "Report preview page 1" }),
  ).toBeVisible();
  const downloadPromise = page.waitForEvent("download");
  await page.getByRole("link", { name: "Download PDF" }).click();
  const download = await downloadPromise;
  expect(download.suggestedFilename()).toMatch(/\.pdf$/i);

  const reports = await readJson<{ saved_reports: { report_id: string }[] }>(
    request,
    `${seededBase}/api/reports/${document.document_id}`,
  );
  const savedId = reports.saved_reports[0]?.report_id;
  expect(savedId).toBeTruthy();
  const lab = await readJson<{ version: number; description: string }>(
    request,
    `${seededBase}/api/lab`,
  );
  const updatedLab = await request.put(`${seededBase}/api/lab`, {
    data: { ...lab, description: `${lab.description} Browser history check.` },
  });
  expect(updatedLab.ok()).toBeTruthy();
  await page.reload();
  await expect(page.getByRole("tab", { name: "Report" })).toBeVisible();
  await page.getByRole("tab", { name: "Report" }).click();
  const savedPicker = page.getByRole("combobox", { name: "Saved report" });
  await savedPicker.click();
  await page
    .getByRole("option", { name: new RegExp(savedId!.slice(0, 8)) })
    .click();
  await expect(
    page.getByText(/Saved snapshot: source, lab or review data has changed/),
  ).toBeVisible();
  await page.screenshot({
    path: testInfo.outputPath("historical-report.png"),
    fullPage: true,
  });
  await page.getByRole("tab", { name: "Developer" }).click();
  await expect(page.getByText("Matching & review diagnostics")).toBeVisible();
});

test("a late draft response cannot open a different document's report", async ({
  page,
  request,
}) => {
  const documents = await readJson<
    { document_id: string; original_name: string }[]
  >(request, `${seededBase}/api/specifications`);
  const first = documents.find(
    (doc) => doc.original_name === "offline-customer-coating.pdf",
  )!;
  const second = documents.find(
    (doc) => doc.original_name === "offline-two-test-spec.pdf",
  )!;
  const secondDraft = await readJson<{
    report: { source_run: { run_id: string }; input_fingerprint: string };
  }>(request, `${seededBase}/api/reports/${second.document_id}`);
  const saved = await request.post(
    `${seededBase}/api/reports/${second.document_id}/snapshots`,
    {
      data: {
        run_id: secondDraft.report.source_run.run_id,
        input_fingerprint: secondDraft.report.input_fingerprint,
      },
    },
  );
  expect(saved.ok()).toBeTruthy();
  const secondSnapshot = (await saved.json()) as { report_id: string };
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
    `**/api/reports/${first.document_id}/snapshots`,
    async (route) => {
      const response = await route.fetch();
      received();
      await gate;
      await route.fulfill({ response });
      delivered();
    },
  );
  await page.goto(`${seededBase}/reports?document=${first.document_id}`);
  await page.getByRole("tab", { name: "Report", exact: true }).click();
  await page.getByRole("button", { name: "Create draft report" }).click();
  await responseReady;
  await chooseMuiOption(page, "Customer specification", second.original_name);
  await expect(
    page.getByRole("combobox", { name: "Customer specification" }),
  ).toContainText(second.original_name);
  release();
  await responseDelivered;
  await page.getByRole("tab", { name: "Report", exact: true }).click();
  await expect(
    page.getByRole("link", { name: "Download PDF" }),
  ).toHaveAttribute("href", new RegExp(secondSnapshot.report_id));
});
