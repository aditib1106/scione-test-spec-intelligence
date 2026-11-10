import { expect, test } from "@playwright/test";
import type { SpecificationData } from "../src/types";

test("planning count metadata stays outside operating conditions without rewriting its source role", async ({
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
  const { document_id: documentId } = await imported.json();
  const source = (await (
    await request.get(`/api/specifications/${documentId}`)
  ).json()) as SpecificationData;
  const selected = source.run!.extraction.requirements[0];
  const displayed = structuredClone(source);
  const normalized = displayed.normalized_requirements![selected.id];
  normalized.claims.push({
    id: "display_fixture_lot_count",
    parameter_id: "submitted_lot_count",
    label: "Submitted lot count",
    kind: "condition",
    stage: "general",
    operator: "=",
    value: "2",
    minimum: null,
    maximum: null,
    tolerance: null,
    values: [],
    unit: "lots",
    text_value: null,
    availability: "supported",
    raw_text: "Submit two independently mixed topcoat lots.",
    source_context: "independently mixed topcoat lots",
    source_pages: [1],
    normalization_state: "parsed",
    configuration: null,
    sequence: null,
    source_atom_ids: [],
  });
  // Display-only fixture: this test must never turn presentation grouping into a source edit.
  await page.route(`**/api/specifications/${documentId}`, async (route) => {
    await route.fulfill({ json: displayed });
  });
  await page.goto(
    `/specifications?document=${documentId}&requirement=${selected.id}`,
  );
  const operating = page.getByRole("table", { name: "Condition parameters" });
  await expect(
    operating.getByText("Submitted lot count", { exact: true }),
  ).toHaveCount(0);
  await page
    .getByRole("button", {
      name: "Sample plan & other instructions",
      exact: true,
    })
    .click();
  const edit = page.getByRole("button", {
    name: "Edit sample plan Submitted lot count",
    exact: true,
  });
  await expect(edit).toBeVisible();
  await edit.click();
  const dialog = page.getByRole("dialog", {
    name: "Correct extracted content",
    exact: true,
  });
  await expect(
    dialog.getByRole("tab", { name: "Sample plan", exact: true }),
  ).toHaveAttribute("aria-selected", "true");
  await expect(
    dialog.getByText("Sample plan / instruction", { exact: true }),
  ).toBeVisible();
  await expect(dialog.getByLabel("Value", { exact: true })).toHaveValue("2");
  await dialog.getByRole("tab", { name: "Conditions", exact: true }).click();
  await expect(
    dialog
      .getByRole("table", { name: "Editable parameter list" })
      .getByText("Submitted lot count", { exact: true }),
  ).toHaveCount(0);
  await dialog.getByRole("button", { name: "Cancel", exact: true }).click();
  const after = await (
    await request.get(`/api/specifications/${documentId}`)
  ).json();
  expect(after.facts_version).toBe(source.facts_version);
  expect(after.run).toEqual(source.run);
  expect(normalized.claims.at(-1)!.kind).toBe("condition");
});

test("review keeps a tall PDF, separates parameters from prose, and opens one focused edit", async ({
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
  const imported = await request.post("/api/specifications/worked-example");
  expect(imported.ok()).toBeTruthy();
  const { document_id: documentId } = await imported.json();
  const source = (await (
    await request.get(`/api/specifications/${documentId}`)
  ).json()) as SpecificationData;
  const selected = source.run!.extraction.requirements[0];
  const claims = source.normalized_requirements![selected.id].claims;
  const parameter = claims.find(
    (claim) => claim.kind === "condition" && claim.parameter_id,
  )!;
  expect(parameter).toBeTruthy();
  if (testInfo.project.name === "desktop")
    await page.setViewportSize({ width: 1440, height: 760 });
  await page.goto(
    `/specifications?document=${documentId}&requirement=${selected.id}`,
  );
  const table = page.getByRole("table", { name: "Condition parameters" });
  await expect(table).toBeVisible();
  await expect(
    table.getByText(parameter.label, { exact: true }).first(),
  ).toBeVisible();
  await expect(page.getByRole("button", { name: /^p\. \d/ })).toHaveCount(0);
  const original = page.getByRole("button", {
    name: "Original specification wording",
    exact: true,
  });
  await expect(original).toHaveAttribute("aria-expanded", "false");
  const locator = table
    .getByRole("button", {
      name: `View condition source on page ${parameter.source_pages[0]} for ${parameter.label}`,
      exact: true,
    })
    .first();
  const response = page.waitForResponse(
    (res) =>
      res.url().includes(`/api/specifications/${documentId}/pages/`) &&
      res.url().includes(`claim_id=${encodeURIComponent(parameter.id)}`),
  );
  await locator.click();
  const rendered = await response;
  expect(rendered.ok()).toBeTruthy();
  expect(["parameter", "item"]).toContain(
    rendered.headers()["x-highlight-scope"],
  );
  await expect(
    page.getByRole("img", { name: /PDF page/ }).first(),
  ).toBeVisible();
  if (testInfo.project.name === "desktop") {
    const viewport = page.getByRole("region", {
      name: "Source document scroll area",
    });
    expect((await viewport.boundingBox())!.height).toBeGreaterThan(480);
    await viewport.evaluate((element) => {
      element.scrollTop = 30;
    });
    await page.getByRole("tab", { name: /^Criteria \(/ }).click();
    expect(await viewport.evaluate((element) => element.scrollTop)).toBe(30);
    await page.getByRole("tab", { name: /^Conditions \(/ }).click();
  }
  await table
    .getByRole("button", {
      name: `Edit parameter ${parameter.label}`,
      exact: true,
    })
    .first()
    .click();
  const dialog = page.getByRole("dialog", {
    name: "Correct extracted content",
    exact: true,
  });
  await expect(dialog).toBeVisible();
  await expect(
    dialog.getByRole("tab", { name: "Conditions", exact: true }),
  ).toHaveAttribute("aria-selected", "true");
  await expect(
    dialog.getByText("Edit one requirement", { exact: true }),
  ).toHaveCount(1);
  await expect(dialog.getByLabel("Parameter", { exact: true })).toBeVisible();
  await expect(
    dialog.getByText(parameter.raw_text, { exact: true }).first(),
  ).toBeVisible();
  await dialog.getByRole("button", { name: "Cancel", exact: true }).click();
  const after = (await (
    await request.get(`/api/specifications/${documentId}`)
  ).json()) as SpecificationData;
  expect(after.facts_version).toBe(source.facts_version);
  expect(after.run!.raw_response).toBe(source.run!.raw_response);
  const dimensions = await page.evaluate(() => ({
    width: document.documentElement.scrollWidth,
    viewport: document.documentElement.clientWidth,
  }));
  expect(dimensions.width).toBeLessThanOrEqual(dimensions.viewport + 1);
});

test("a guarded numeric draft stays visible and can be corrected without duplicating its native atom", async ({
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
  const { document_id: documentId } = await imported.json();
  const url = `/api/specifications/${documentId}`;
  const original = (await (await request.get(url)).json()) as SpecificationData;
  const selected = original.run!.extraction.requirements[0];
  const evidence = selected.evidence.find((item) =>
    item.quote.includes("five cycles"),
  )!;
  const nativeId = "human_atom_unclear_cycles";
  const requirements = structuredClone(original.run!.extraction.requirements);
  requirements[0].atomic_claims = [
    {
      id: nativeId,
      parameter_id: null,
      label: "Cycles",
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
      normalization_state: "unresolved",
      configuration: null,
      sequence: null,
    },
  ];
  const seeded = await request.post(`${url}/facts`, {
    data: {
      run_id: original.run!.run_id,
      expected_version: original.facts_version ?? 0,
      expected_review_version: original.review!.version,
      requirements,
      note: "Prepare an unclassified but source-backed numeric draft for a browser correction test.",
    },
  });
  expect(seeded.ok(), await seeded.text()).toBeTruthy();
  const before = (await seeded.json()) as SpecificationData;
  const guarded = before.normalized_requirements![selected.id].claims.find(
    (claim) => claim.source_atom_ids?.includes(nativeId),
  )!;
  expect(guarded.parameter_id).toBeNull();
  await page.goto(
    `/specifications?document=${documentId}&requirement=${selected.id}`,
  );
  const table = page.getByRole("table", { name: "Condition parameters" });
  const row = table
    .getByRole("row")
    .filter({ has: page.getByText("Cycles", { exact: true }) })
    .first();
  await expect(row.getByText("5 cycles", { exact: true })).toBeVisible();
  await expect(
    row.getByText("Check interpretation", { exact: true }),
  ).toBeVisible();
  await row
    .getByRole("button", { name: "Edit parameter Cycles", exact: true })
    .click();
  const dialog = page.getByRole("dialog", {
    name: "Correct extracted content",
    exact: true,
  });
  await dialog.getByLabel("Parameter", { exact: true }).click();
  await page.getByRole("option", { name: "Cycle count", exact: true }).click();
  await dialog.getByLabel("Value", { exact: true }).fill("999");
  await dialog
    .getByRole("button", { name: "Save corrected draft", exact: true })
    .click();
  await expect(dialog.getByRole("alert")).toContainText(
    "not in this parameter's source quotation",
  );
  const rejected = (await (await request.get(url)).json()) as SpecificationData;
  expect(rejected.facts_version).toBe(before.facts_version);
  await dialog.getByLabel("Value", { exact: true }).fill("5");
  await dialog
    .getByRole("button", { name: "Save corrected draft", exact: true })
    .click();
  await expect(dialog).not.toBeVisible();
  const after = (await (await request.get(url)).json()) as SpecificationData;
  const atoms = after.run!.extraction.requirements[0].atomic_claims!;
  expect(atoms).toHaveLength(1);
  expect(atoms[0].id).toBe(nativeId);
  const registry = await (await request.get("/api/parameters")).json();
  expect(atoms[0].parameter_id).toBe(
    registry.parameters.find(
      (item: { label: string }) => item.label === "Cycle count",
    ).id,
  );
  expect(atoms[0].value).toBe("5");
  expect(atoms[0].availability).toBe("supported");
  expect(atoms[0].raw_text).toBe(evidence.quote);
  expect(atoms[0].source_pages).toEqual([evidence.page]);
  expect(after.facts_version).toBe((before.facts_version ?? 0) + 1);
  expect(after.run!.raw_response).toBe(original.run!.raw_response);
  expect(after.facts_revision!.note).toBe(
    "User edited extracted test information.",
  );
});
