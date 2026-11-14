import { expect, test, type APIRequestContext } from "@playwright/test";
import type { SpecificationData } from "../src/types";

async function workedExample(request: APIRequestContext) {
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
  const source = (await (
    await request.get(`/api/specifications/${documentId}`)
  ).json()) as SpecificationData;
  return {
    documentId,
    source,
    selected: source.run!.extraction.requirements[0],
  };
}

test("a document revision question appears once without marking each extracted test for review", async ({
  page,
  request,
}) => {
  const { documentId, source, selected } = await workedExample(request);
  const issue =
    "The header states 'Rev A' while the revision block states 'Rev B'; confirm which document revision applies.";
  const notes = {
    source_fingerprint: "revision-note-fixture",
    version: 0,
    pending_count: 1,
    reviewed_count: 0,
    notes: [
      {
        id: "revision",
        kind: "document_info",
        title: "Document version",
        detail: issue,
        reviewed: false,
      },
    ],
  };
  await page.route(`**/api/specifications/${documentId}`, (route) =>
    route.fulfill({
      json: {
        ...source,
        document_notes_review: notes,
        document_review_issues: [issue],
        coverage_issues: [],
        assessments: {
          ...source.assessments,
          [selected.id]: { state: "ready", reasons: [] },
        },
      },
    }),
  );
  await page.goto(
    `/specifications?document=${documentId}&requirement=${selected.id}`,
  );
  const summary = page.getByRole("button", { name: /Document notes/ });
  await expect(summary).toHaveCount(1);
  await expect(page.getByText(issue, { exact: true })).not.toBeVisible();
  await expect(
    page.getByText("Check extracted requirements", { exact: true }),
  ).toHaveCount(0);
  await summary.click();
  await expect(page.getByText(issue, { exact: true })).toBeVisible();
  await expect(
    page.getByText("Check this item against the PDF", { exact: true }),
  ).toHaveCount(0);
  await page.route(`**/api/library/${documentId}`, async (route) => {
    const response = await route.fetch();
    const saved = await response.json();
    await route.fulfill({
      json: {
        ...saved,
        document: { ...saved.document, document_review_issues: [issue] },
      },
    });
  });
  await page.goto(`/specifications/library?document=${documentId}`);
  await expect(
    page.getByText("Check document revision", { exact: true }),
  ).toHaveCount(1);
  await page
    .getByRole("button", { name: "View revision details", exact: true })
    .click();
  await expect(
    page.getByRole("link", { name: "Open PDF review", exact: true }),
  ).toHaveAttribute("href", new RegExp(`document=${documentId}`));
  await page.route(`**/api/comparisons/${documentId}`, async (route) => {
    const response = await route.fetch();
    const saved = await response.json();
    await route.fulfill({
      json: {
        ...saved,
        document_notes_review: notes,
        document: { ...saved.document, document_review_issues: [issue] },
      },
    });
  });
  await page.goto(`/reports?document=${documentId}`);
  const reportNotes = page.getByRole("button", { name: /Document notes/ });
  await expect(reportNotes).toHaveCount(1);
  await reportNotes.click();
  await expect(page.getByText(issue, { exact: true })).toBeVisible();
  await expect(
    page.getByRole("link", { name: "Open specification", exact: true }),
  ).toHaveAttribute("href", new RegExp(`document=${documentId}`));
});

test("rebuilding detailed interpretation is explicit and preserves the original AI output", async ({
  page,
  request,
}) => {
  const prepared = await workedExample(request);
  const documentId = prepared.documentId;
  // The hand-authored demo uses raw fields. Add one source-backed typed fixture
  // through the real correction API so the UI has an interpretation to rebuild.
  const withAtom = structuredClone(prepared.selected);
  const sourceEvidence = withAtom.evidence.find((entry) =>
    entry.quote.includes("five cycles"),
  )!;
  expect(sourceEvidence).toBeTruthy();
  withAtom.atomic_claims = [
    {
      id: "human_atom_fixture_cycles",
      parameter_id: "cycle_count",
      label: "Cycle count",
      kind: "condition",
      stage: "thermal_cycle",
      operator: "=",
      value: "5",
      minimum: null,
      maximum: null,
      tolerance: null,
      values: [],
      unit: "cycles",
      text_value: null,
      availability: "supported",
      raw_text: sourceEvidence.quote,
      source_pages: [sourceEvidence.page],
      normalization_state: "parsed",
      configuration: null,
      sequence: null,
    },
  ];
  const seeded = await request.post(`/api/specifications/${documentId}/facts`, {
    data: {
      run_id: prepared.source.run!.run_id,
      expected_version: prepared.source.facts_version ?? 0,
      expected_review_version: prepared.source.review!.version,
      requirements: prepared.source.run!.extraction.requirements.map((item) =>
        item.id === withAtom.id ? withAtom : item,
      ),
      note: "Prepare one source-backed typed interpretation for the offline browser fixture.",
    },
  });
  expect(seeded.ok(), await seeded.text()).toBeTruthy();
  const source = (await seeded.json()) as SpecificationData;
  const selected = source.run!.extraction.requirements.find(
    (item) => item.id === withAtom.id,
  )!;
  expect(selected.atomic_claims!.length).toBeGreaterThan(0);
  await page.goto(
    `/specifications?document=${documentId}&requirement=${selected.id}`,
  );
  await page.getByRole("button", { name: "All", exact: true }).click();
  await page
    .getByRole("button", { name: "Correct extracted content", exact: true })
    .click();
  const dialog = page.getByRole("dialog", {
    name: "Correct extracted content",
    exact: true,
  });
  await dialog
    .getByRole("button", { name: "Detailed parameters", exact: true })
    .click();
  const rebuild = dialog.getByRole("checkbox", {
    name: "Rebuild detailed parameters from these reviewed fields",
    exact: true,
  });
  await expect(rebuild).not.toBeChecked();
  await dialog
    .getByRole("button", { name: "Add a review note (optional)", exact: true })
    .click();
  await dialog
    .getByLabel("Reason for correction")
    .fill(
      "The reviewed source fields are correct; rebuild the detailed interpretation from them.",
    );
  let mutationCount = 0;
  let submittedAtoms: unknown;
  page.on("request", (sent) => {
    if (
      sent.method() === "POST" &&
      sent.url().endsWith(`/api/specifications/${documentId}/facts`)
    ) {
      mutationCount++;
      submittedAtoms = sent
        .postDataJSON()
        .requirements.find(
          (item: { id: string }) => item.id === selected.id,
        ).atomic_claims;
    }
  });
  await dialog
    .getByRole("button", { name: "Save corrected draft", exact: true })
    .click();
  await expect(dialog.getByRole("alert").last()).toContainText(
    "Change at least one extracted field",
  );
  expect(mutationCount).toBe(0);
  const unchanged = (await (
    await request.get(`/api/specifications/${documentId}`)
  ).json()) as SpecificationData;
  expect(unchanged.facts_version).toBe(source.facts_version);
  expect(unchanged.run!.extraction.requirements[0].atomic_claims).toEqual(
    selected.atomic_claims,
  );
  await rebuild.check();
  await dialog
    .getByRole("button", { name: "Save corrected draft", exact: true })
    .click();
  await expect(dialog).not.toBeVisible();
  expect(mutationCount).toBe(1);
  expect(submittedAtoms).toEqual([]);
  const updated = (await (
    await request.get(`/api/specifications/${documentId}`)
  ).json()) as SpecificationData;
  const corrected = updated.run!.extraction.requirements.find(
    (item) => item.id === selected.id,
  )!;
  expect(corrected.atomic_claims).toEqual([]);
  expect(corrected.conditions).toEqual(selected.conditions);
  expect(corrected.acceptance_criteria).toEqual(selected.acceptance_criteria);
  expect(updated.facts_version).toBe((source.facts_version ?? 0) + 1);
  expect(updated.run!.raw_response).toBe(source.run!.raw_response);
});
