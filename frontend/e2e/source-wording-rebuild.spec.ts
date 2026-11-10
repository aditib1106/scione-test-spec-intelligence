import { expect, test } from "@playwright/test";
import type { SpecificationData } from "../src/types";

test("original wording changes require explicit rebuilding and Cancel keeps the saved details", async ({
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
  const path = `/api/specifications/${documentId}`;
  const initial = (await (await request.get(path)).json()) as SpecificationData;
  const selected = initial.run!.extraction.requirements[0];
  const evidence = selected.evidence.find((item) =>
    item.quote.includes("five cycles"),
  )!;
  const requirements = structuredClone(initial.run!.extraction.requirements);
  requirements[0].atomic_claims = [
    {
      id: "human_atom_wording_fixture",
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
      raw_text: evidence.quote,
      source_pages: [evidence.page],
      normalization_state: "parsed",
      configuration: null,
      sequence: null,
    },
  ];
  const seeded = await request.post(`${path}/facts`, {
    data: {
      run_id: initial.run!.run_id,
      expected_version: initial.facts_version ?? 0,
      expected_review_version: initial.review!.version,
      requirements,
      note: "Prepare a source-backed typed fixture for an offline browser check.",
    },
  });
  expect(seeded.ok(), await seeded.text()).toBeTruthy();
  const saved = (await seeded.json()) as SpecificationData;
  const original = saved.run!.extraction.requirements[0];
  let edits = 0;
  page.on("request", (sent) => {
    if (sent.method() === "POST" && sent.url().endsWith(`${path}/facts`))
      edits++;
  });
  await page.goto(
    `/specifications?document=${documentId}&requirement=${selected.id}`,
  );
  await page.getByRole("button", { name: "All", exact: true }).click();
  const dialog = page.getByRole("dialog", {
    name: "Correct extracted content",
    exact: true,
  });
  const openOriginalWording = async () => {
    await page
      .getByRole("button", { name: "Correct extracted content", exact: true })
      .click();
    await dialog.getByRole("tab", { name: "Conditions", exact: true }).click();
    await dialog
      .getByRole("button", { name: "Edit original wording", exact: true })
      .click();
  };
  await openOriginalWording();
  await dialog
    .getByLabel("Parameter name", { exact: true })
    .fill(`${original.conditions[0].name} (checked)`);
  await expect(dialog.getByRole("alert")).toContainText(
    "Changing original wording rebuilds this test’s detailed fields.",
  );
  const acknowledgement = dialog.getByRole("checkbox", {
    name: "Rebuild this test’s detailed fields",
    exact: true,
  });
  const save = dialog.getByRole("button", {
    name: "Save & rebuild details",
    exact: true,
  });
  await expect(acknowledgement).not.toBeChecked();
  await expect(save).toBeDisabled();
  await acknowledgement.check();
  await expect(save).toBeEnabled();
  await dialog.getByRole("button", { name: "Cancel", exact: true }).click();
  await expect(dialog).not.toBeVisible();
  const cancelled = (await (
    await request.get(path)
  ).json()) as SpecificationData;
  expect(edits).toBe(0);
  expect(cancelled.facts_version).toBe(saved.facts_version);
  expect(cancelled.run).toEqual(saved.run);

  await openOriginalWording();
  await expect(
    dialog.getByLabel("Parameter name", { exact: true }),
  ).toHaveValue(original.conditions[0].name);
  await dialog
    .getByLabel("Parameter name", { exact: true })
    .fill(`${original.conditions[0].name} (checked)`);
  await expect(acknowledgement).not.toBeChecked();
  await expect(save).toBeDisabled();
  await acknowledgement.check();
  await acknowledgement.uncheck();
  await expect(save).toBeDisabled();
  await acknowledgement.check();
  await save.click();
  await expect(dialog).not.toBeVisible();
  const updated = (await (await request.get(path)).json()) as SpecificationData;
  expect(edits).toBe(1);
  expect(updated.facts_version).toBe((saved.facts_version ?? 0) + 1);
  expect(updated.run!.extraction.requirements[0].atomic_claims).toEqual([]);
  expect(updated.run!.extraction.requirements[0].conditions[0].name).toBe(
    `${original.conditions[0].name} (checked)`,
  );
  expect(updated.run!.raw_response).toBe(saved.run!.raw_response);
});
