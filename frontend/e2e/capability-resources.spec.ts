import {
  expect,
  test,
  type APIRequestContext,
  type Page,
} from "@playwright/test";
import type { AtomicClaim, LabProfile, Workspace } from "../src/types";
import { shortcutResourceType } from "../src/components/labResourceClaims";

async function reset(request: APIRequestContext) {
  expect(
    (
      await request.post("/api/workspace/reset", {
        data: { acknowledged: true },
      })
    ).ok(),
  ).toBeTruthy();
  return (await (await request.get("/api/workspace")).json()) as Workspace;
}

async function addCustom(page: Page) {
  await page.goto("/my-lab");
  await page
    .getByRole("button", { name: "Add a test", exact: true })
    .first()
    .click();
  const dialog = page.getByRole("dialog");
  await dialog
    .getByRole("button", { name: "Create custom test", exact: true })
    .click();
  await dialog
    .getByLabel(/^Custom test name/)
    .fill("Resource fixture assessment");
  return dialog;
}

async function lab(request: APIRequestContext) {
  return (await (await request.get("/api/lab")).json()) as LabProfile;
}

test("optional resources round-trip, update and clear without changing the shared catalog", async ({
  page,
  request,
}, testInfo) => {
  const before = await reset(request);
  let writes = 0;
  page.on("request", (sent) => {
    if (["POST", "PUT", "PATCH", "DELETE"].includes(sent.method())) writes++;
  });
  let dialog = await addCustom(page);
  await expect(
    dialog.getByLabel("Equipment categories", { exact: true }),
  ).toHaveValue("");
  await expect(
    dialog.getByLabel("Consumables & materials", { exact: true }),
  ).toHaveValue("");
  await dialog
    .getByLabel("Equipment categories", { exact: true })
    .fill("Environmental chamber; pressure-water-jet tester");
  await dialog
    .getByLabel("Consumables & materials", { exact: true })
    .fill("Adhesion test tape; deionized water");
  await expect(
    dialog.getByRole("button", {
      name: "Edit Equipment categories",
      exact: true,
    }),
  ).toHaveCount(0);
  await expect(
    dialog.getByRole("button", {
      name: "Edit Consumables & materials",
      exact: true,
    }),
  ).toHaveCount(0);
  await dialog
    .getByRole("button", { name: "Save capability", exact: true })
    .click();
  await expect(dialog).not.toBeVisible();
  expect(writes).toBe(1);
  const first = await lab(request);
  expect(first.capabilities).toHaveLength(1);
  const capability = first.capabilities[0];
  expect(capability.method_ids).toEqual([]);
  expect(capability.atomic_claims).toHaveLength(2);
  expect(capability.atomic_claims![0]).toMatchObject({
    parameter_id: "equipment",
    label: "Equipment categories",
    kind: "condition",
    stage: "general",
    operator: "text",
    availability: "supported",
    normalization_state: "reviewed",
    text_value: "Environmental chamber; pressure-water-jet tester",
  });
  expect(capability.atomic_claims![1]).toMatchObject({
    parameter_id: "custom_lab_supplies",
    label: "Consumables & materials",
    kind: "condition",
    operator: "text",
    normalization_state: "unresolved",
  });
  expect(
    capability.atomic_claims!.some(
      (claim) => claim.parameter_id === "specimen_material",
    ),
  ).toBe(false);
  const savedWorkspace = (await (
    await request.get("/api/workspace")
  ).json()) as Workspace;
  expect(
    savedWorkspace.catalog.tests.filter(
      (item) => !item.id.startsWith("labtest_"),
    ),
  ).toEqual(before.catalog.tests);
  expect(savedWorkspace.catalog.methods).toEqual(before.catalog.methods);

  await page
    .getByRole("button", { name: "Resource fixture assessment", exact: true })
    .click();
  const details = page.getByRole("dialog");
  const resources = details.getByRole("region", {
    name: "Resources",
    exact: true,
  });
  await expect(resources).toContainText(
    "Environmental chamber; pressure-water-jet tester",
  );
  await expect(resources).toContainText("Adhesion test tape; deionized water");
  if (testInfo.project.name === "desktop")
    await details.screenshot({
      path: testInfo.outputPath("capability-resources-details.png"),
      animations: "disabled",
    });
  await details.getByRole("button", { name: "Close", exact: true }).click();

  await page.getByRole("button", { name: "Edit", exact: true }).click();
  dialog = page.getByRole("dialog", { name: /^Edit capability/ });
  await expect(
    dialog.getByLabel("Equipment categories", { exact: true }),
  ).toHaveValue("Environmental chamber; pressure-water-jet tester");
  await expect(
    dialog.getByLabel("Consumables & materials", { exact: true }),
  ).toHaveValue("Adhesion test tape; deionized water");
  await dialog
    .getByLabel("Equipment categories", { exact: true })
    .fill("Environmental chamber; gloss meter");
  await dialog.getByLabel("Consumables & materials", { exact: true }).fill("");
  if (testInfo.project.name === "desktop") {
    await dialog
      .getByRole("region", { name: "Resources", exact: true })
      .scrollIntoViewIfNeeded();
    await dialog.screenshot({
      path: testInfo.outputPath("capability-resources-editor.png"),
      animations: "disabled",
    });
  }
  await dialog
    .getByRole("button", { name: "Save capability", exact: true })
    .click();
  await expect(dialog).not.toBeVisible();
  const changed = (await lab(request)).capabilities[0];
  expect(changed.atomic_claims).toHaveLength(1);
  expect(changed.atomic_claims![0].id).toBe(capability.atomic_claims![0].id);
  expect(changed.atomic_claims![0].text_value).toBe(
    "Environmental chamber; gloss meter",
  );
  expect(changed.atomic_claims![0].raw_text).toContain(
    "Environmental chamber; gloss meter",
  );
  expect(changed.atomic_claims![0].raw_text).not.toContain(
    "pressure-water-jet",
  );
  await page.getByRole("button", { name: "Edit", exact: true }).click();
  dialog = page.getByRole("dialog", { name: /^Edit capability/ });
  await dialog.getByLabel("Equipment categories", { exact: true }).fill("");
  await dialog
    .getByRole("button", { name: "Save capability", exact: true })
    .click();
  await expect(dialog).not.toBeVisible();
  expect((await lab(request)).capabilities[0].atomic_claims).toEqual([]);
});

test("resource drafts cancel without writing and empty optional fields save no claims", async ({
  page,
  request,
}) => {
  await reset(request);
  const original = await lab(request);
  let dialog = await addCustom(page);
  await dialog
    .getByLabel("Equipment categories", { exact: true })
    .fill("Pressure-water-jet tester");
  await dialog
    .getByLabel("Consumables & materials", { exact: true })
    .fill("Water");
  await dialog.getByRole("button", { name: "Cancel", exact: true }).click();
  await expect(dialog).not.toBeVisible();
  expect(await lab(request)).toEqual(original);
  dialog = await addCustom(page);
  await expect(
    dialog.getByLabel("Equipment categories", { exact: true }),
  ).toHaveValue("");
  await expect(
    dialog.getByLabel("Consumables & materials", { exact: true }),
  ).toHaveValue("");
  await dialog
    .getByRole("button", { name: "Save capability", exact: true })
    .click();
  await expect(dialog).not.toBeVisible();
  expect((await lab(request)).capabilities[0].atomic_claims).toEqual([]);
});

test("editing price preserves legacy equipment, specimen material and phase-scoped claims exactly", async ({
  page,
  request,
}) => {
  await reset(request);
  const initial = await lab(request);
  const seededResponse = await request.post("/api/lab/example", {
    data: { version: initial.version, example: "legacy" },
  });
  expect(seededResponse.ok()).toBeTruthy();
  const seeded = (await seededResponse.json()) as LabProfile;
  const capability = seeded.capabilities.find(
    (entry) => entry.id === "cap_cross_cut",
  )!;
  const equipment: AtomicClaim = {
    id: "legacy_equipment",
    parameter_id: "equipment",
    label: "Equipment",
    kind: "condition",
    stage: "general",
    operator: "text",
    value: null,
    minimum: null,
    maximum: null,
    tolerance: null,
    values: [],
    unit: null,
    text_value: "Climate chamber",
    availability: "supported",
    raw_text: "Our original equipment declaration.",
    source_pages: [],
    source_context: null,
    configuration: null,
    sequence: null,
    normalization_state: "parsed",
  };
  capability.atomic_claims = [
    equipment,
    {
      ...equipment,
      id: "legacy_cold_equipment",
      stage: "cold conditioning",
      text_value: "Cold chamber",
      configuration: "Rig B",
      raw_text: "Cold chamber for phase B.",
    },
    {
      ...equipment,
      id: "legacy_specimen",
      parameter_id: "specimen_material",
      label: "Specimen material",
      text_value: "Steel",
      raw_text: "Steel specimens only.",
    },
    {
      ...equipment,
      id: "cap_resource_equipment_00000000000000000000000000000000",
      label: "Equipment categories",
      value: "0",
      raw_text: "Imported resource entry also retains a zero-valued fact.",
    },
  ];
  // Numeric JSON literals must not be considered empty either. The API may
  // normalize Decimal values to strings; both representations retain the fact.
  const marker = capability.atomic_claims[3];
  expect(shortcutResourceType(marker)).toBeNull();
  for (const field of [
    "value",
    "minimum",
    "maximum",
    "tolerance",
    "sequence",
  ]) {
    expect(
      shortcutResourceType({
        ...equipment,
        ...marker,
        value: null,
        [field]: 0,
      } as unknown as AtomicClaim),
    ).toBeNull();
  }
  expect((await request.put("/api/lab", { data: seeded })).ok()).toBeTruthy();
  const before = await lab(request);
  await page.goto("/my-lab");
  await page
    .getByRole("row")
    .filter({
      has: page.getByRole("button", {
        name: "Cross-cut adhesion",
        exact: true,
      }),
    })
    .getByRole("button", { name: "Edit", exact: true })
    .click();
  const dialog = page.getByRole("dialog", { name: /^Edit capability/ });
  await expect(
    dialog.getByLabel("Equipment categories", { exact: true }),
  ).toHaveValue("");
  await expect(
    dialog.getByLabel("Consumables & materials", { exact: true }),
  ).toHaveValue("");
  await expect(
    dialog.getByRole("button", { name: "Edit Equipment", exact: true }),
  ).toHaveCount(2);
  await expect(
    dialog.getByRole("button", { name: "Edit Specimen material", exact: true }),
  ).toBeVisible();
  await expect(
    dialog.getByRole("button", {
      name: "Edit Equipment categories",
      exact: true,
    }),
  ).toBeVisible();
  await dialog.getByLabel("Price", { exact: true }).fill("149");
  await dialog.getByLabel("Currency", { exact: true }).fill("USD");
  await dialog.getByLabel("Pricing basis", { exact: true }).fill("per test");
  await dialog
    .getByRole("button", { name: "Save capability", exact: true })
    .click();
  await expect(dialog).not.toBeVisible();
  const after = await lab(request);
  expect(
    after.capabilities.find((entry) => entry.id === capability.id)!
      .atomic_claims,
  ).toEqual(
    before.capabilities.find((entry) => entry.id === capability.id)!
      .atomic_claims,
  );
  expect(
    after.capabilities.find((entry) => entry.id === capability.id)!.price,
  ).toBe("149");
});
