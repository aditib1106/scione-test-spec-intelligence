import { expect, test } from "@playwright/test";
import type { SpecificationData } from "../src/types";

test("corrected source facts clear approval and flow into library and reports without replacing model output", async ({
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
  const { document_id: documentId } = (await imported.json()) as {
    document_id: string;
  };
  const sourceUrl = `/api/specifications/${documentId}`;
  const initial = (await (
    await request.get(sourceUrl)
  ).json()) as SpecificationData;
  const selected = initial.run!.extraction.requirements[0];
  const originalRawUrl = `/api/diagnostics/${documentId}/raw?run_id=${initial.run!.run_id}`;
  const originalRaw = await (await request.get(originalRawUrl)).text();
  expect(initial.facts_version).toBe(0);

  await page.goto(
    `/specifications?document=${documentId}&requirement=${selected.id}`,
  );
  await page.getByRole("button", { name: "All", exact: true }).click();
  await expect(
    page.getByRole("heading", {
      name: selected.original_test_name,
      exact: true,
    }),
  ).toBeVisible();
  await page
    .getByRole("button", { name: "Confirm & next", exact: true })
    .click();
  await expect
    .poll(async () => {
      const current = (await (
        await request.get(sourceUrl)
      ).json()) as SpecificationData;
      return current.review?.decisions[selected.id]?.status;
    })
    .toBe("confirmed");
  // Reopen the same row after the review action advances the selection.
  await page.goto(
    `/specifications?document=${documentId}&requirement=${selected.id}`,
  );
  await page.getByRole("button", { name: "All", exact: true }).click();
  await page
    .getByRole("button", { name: "Correct extracted content", exact: true })
    .click();
  const dialog = page.getByRole("dialog", {
    name: "Correct extracted content",
  });
  await expect(dialog).toBeVisible();
  const correctedName = `${selected.original_test_name} (reviewed)`;
  await dialog.getByLabel("Test name").fill(correctedName);
  await dialog
    .getByLabel("Reason for correction")
    .fill(
      "Checked the source wording and clarified the thermal-cycle requirement.",
    );
  let correctionRequests = 0;
  page.on("request", (sent) => {
    if (
      sent.method() === "POST" &&
      sent.url().endsWith(`/api/specifications/${documentId}/facts`)
    )
      correctionRequests++;
  });

  await dialog.getByRole("button", { name: "Add method", exact: true }).click();
  await dialog
    .getByRole("button", { name: "Save corrected draft", exact: true })
    .click();
  await expect(dialog.getByRole("alert").last()).toContainText(
    "Enter a method identifier",
  );
  await dialog
    .getByRole("button", {
      name: `Remove method ${selected.requested_methods.length + 1}`,
      exact: true,
    })
    .click();

  await dialog
    .getByRole("button", { name: "Add condition", exact: true })
    .click();
  await dialog
    .getByRole("button", { name: "Save corrected draft", exact: true })
    .click();
  await expect(dialog.getByRole("alert").last()).toContainText(
    "Add the customer requirement",
  );
  await dialog
    .getByRole("button", {
      name: `Remove condition ${selected.conditions.length + 1}`,
      exact: true,
    })
    .click();

  const evidence = dialog.getByRole("group", {
    name: "Source evidence 1",
    exact: true,
  });
  await evidence.getByLabel("Source page").fill("0");
  await dialog
    .getByRole("button", { name: "Save corrected draft", exact: true })
    .click();
  await expect(dialog.getByRole("alert").last()).toContainText(
    `Enter a whole source page from 1 to ${initial.document.page_count}`,
  );
  await evidence
    .getByLabel("Source page")
    .fill(String(selected.evidence[0].page));
  expect(correctionRequests).toBe(0);

  const condition = dialog.getByRole("group", {
    name: "Conditions 1",
    exact: true,
  });
  await condition.getByLabel("Parameter name").fill("Thermal cycle");
  await condition
    .getByLabel("Customer requirement")
    .fill("5 cycles: -20 °C for 2 h, then 70 °C for 2 h");
  if (testInfo.project.name === "narrow") {
    const dimensions = await dialog.boundingBox();
    expect(dimensions?.width).toBe(390);
    await expect(
      dialog.getByRole("button", { name: "Cancel", exact: true }),
    ).toBeVisible();
  }
  await page.screenshot({
    path: testInfo.outputPath("source-correction-editor.png"),
    animations: "disabled",
  });
  await dialog
    .getByRole("button", { name: "Save corrected draft", exact: true })
    .click();
  await expect(dialog).not.toBeVisible();
  await expect(
    page.getByText("Corrected draft", { exact: true }),
  ).toBeVisible();
  await expect(
    page.getByRole("heading", { name: correctedName, exact: true }),
  ).toBeVisible();
  const corrected = (await (
    await request.get(sourceUrl)
  ).json()) as SpecificationData;
  expect(corrected.facts_version).toBe(1);
  expect(corrected.facts_revision?.changed_requirement_ids).toEqual([
    selected.id,
  ]);
  expect(corrected.review?.decisions[selected.id]).toBeUndefined();
  expect(corrected.run?.run_id).toBe(initial.run!.run_id);
  expect(corrected.run?.raw_response).toBe(initial.run!.raw_response);
  expect(await (await request.get(originalRawUrl)).text()).toBe(originalRaw);
  expect(correctionRequests).toBe(1);

  const staleApproval = await request.post(`${sourceUrl}/review`, {
    data: {
      run_id: corrected.run!.run_id,
      version: corrected.review!.version,
      facts_version: 0,
      requirement_ids: [selected.id],
      decision: "confirmed",
      note: "Stale review must not apply.",
    },
  });
  expect(staleApproval.status()).toBe(409);
  await page
    .getByRole("button", { name: "Confirm & next", exact: true })
    .click();
  await expect
    .poll(async () => {
      const current = (await (
        await request.get(sourceUrl)
      ).json()) as SpecificationData;
      return current.review?.decisions[selected.id]?.status;
    })
    .toBe("confirmed");

  const libraryResponse = await request.get(`/api/library/${documentId}`);
  expect(libraryResponse.ok()).toBeTruthy();
  const library = (await libraryResponse.json()) as {
    facts_revision_id: string;
    requirements: Array<{ requirement_id: string; test_name: string }>;
  };
  expect(library.facts_revision_id).toBe(corrected.facts_revision!.revision_id);
  expect(
    library.requirements.find((row) => row.requirement_id === selected.id)
      ?.test_name,
  ).toBe(correctedName);
  await page.goto(`/specifications/library?document=${documentId}`);
  await expect(
    page.getByRole("button", { name: correctedName, exact: true }),
  ).toBeVisible();

  const reportResponse = await request.get(`/api/reports/${documentId}`);
  expect(reportResponse.ok()).toBeTruthy();
  const report = (await reportResponse.json()) as {
    report: {
      source_facts_revision_id: string;
      lines: Array<{ requirement: { id: string; original_test_name: string } }>;
    };
  };
  expect(report.report.source_facts_revision_id).toBe(
    corrected.facts_revision!.revision_id,
  );
  expect(
    report.report.lines.find((line) => line.requirement.id === selected.id)
      ?.requirement.original_test_name,
  ).toBe(correctedName);
  await page.goto(`/reports?document=${documentId}`);
  await expect(
    page.getByRole("heading", { name: correctedName, exact: true }),
  ).toBeVisible();
  await expect(
    page.getByRole("table", { name: "Test capability comparison" }),
  ).toContainText("Thermal cycle");
});
