import { expect, test } from "@playwright/test";
import type { AtomicClaim, LabProfile } from "../src/types";

test("lab enters typed conditions, reporting and unknown custom facts without technical keys", async ({
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
  await page.goto("/my-lab");
  await page
    .getByRole("button", { name: "Add a test", exact: true })
    .first()
    .click();
  const dialog = page.getByRole("dialog");
  await dialog
    .getByRole("combobox", { name: "Test name", exact: true })
    .fill("Pressure water jet adhesion");
  await page
    .getByRole("option", { name: "Pressure water jet adhesion", exact: true })
    .click();
  await dialog
    .getByRole("button", { name: "Add condition", exact: true })
    .click();
  const condition = dialog.getByRole("group", { name: "Conditions field 1" });
  await condition.getByLabel("Field", { exact: true }).click();
  await page
    .getByRole("option", { name: "Water temperature", exact: true })
    .click();
  await condition.getByLabel("Minimum", { exact: true }).fill("20");
  await condition.getByLabel("Maximum", { exact: true }).fill("80");
  await expect(condition.getByLabel("Unit", { exact: true })).toHaveValue("°C");
  await condition.getByRole("button", { name: /Context and wording/ }).click();
  await condition
    .getByLabel("Material or context", { exact: true })
    .fill("Test water");
  await condition
    .getByLabel("Setup / configuration", { exact: true })
    .fill("Jet rig A");

  await dialog
    .getByRole("button", { name: "Add measurement", exact: true })
    .click();
  const reporting = dialog.getByRole("group", {
    name: "Acceptance criteria / reporting field 1",
  });
  await reporting.getByLabel("Field", { exact: true }).click();
  await page
    .getByRole("option", { name: "Film thickness", exact: true })
    .click();
  await reporting.getByLabel("Minimum", { exact: true }).fill("5");
  await reporting.getByLabel("Maximum", { exact: true }).fill("500");

  await dialog
    .getByRole("button", { name: "Add condition", exact: true })
    .click();
  const custom = dialog.getByRole("group", { name: "Conditions field 2" });
  await custom.getByLabel("Field", { exact: true }).click();
  await page.getByRole("option", { name: "Other field", exact: true }).click();
  await custom
    .getByLabel("Field name", { exact: true })
    .fill("Special fixture");
  await custom.getByLabel("Availability", { exact: true }).click();
  await page
    .getByRole("option", { name: "Not yet assessed", exact: true })
    .click();
  await dialog
    .getByRole("button", { name: "Add condition", exact: true })
    .click();
  const equipment = dialog.getByRole("group", { name: "Conditions field 3" });
  await equipment.getByLabel("Field", { exact: true }).click();
  await page.getByRole("option", { name: "Equipment", exact: true }).click();
  await equipment
    .getByLabel("Supported items (one per line)")
    .fill("Jet rig\nDigital timer");
  await dialog
    .getByRole("button", { name: "Add condition", exact: true })
    .click();
  const reference = dialog.getByRole("group", { name: "Conditions field 4" });
  await reference.getByLabel("Field", { exact: true }).click();
  await page
    .getByRole("option", { name: "Reference / master panel", exact: true })
    .click();
  await reference.getByLabel("Availability", { exact: true }).click();
  await page
    .getByRole("option", { name: "Not available", exact: true })
    .click();
  await expect(dialog.getByLabel("Parameter key", { exact: true })).toHaveCount(
    0,
  );
  await dialog
    .getByRole("button", { name: "Save capability", exact: true })
    .click();
  await expect(dialog).not.toBeVisible();

  const saved = (await (await request.get("/api/lab")).json()) as LabProfile;
  expect(saved.capabilities).toHaveLength(1);
  const claims = saved.capabilities[0].atomic_claims!;
  expect(claims).toHaveLength(5);
  expect(
    claims.find((claim) => claim.parameter_id === "water_temperature"),
  ).toMatchObject({
    kind: "condition",
    operator: "range",
    minimum: "20",
    maximum: "80",
    unit: "°C",
    source_context: "Test water",
    configuration: "Jet rig A",
    normalization_state: "reviewed",
  });
  expect(
    claims.find((claim) => claim.parameter_id === "film_thickness"),
  ).toMatchObject({
    kind: "reporting",
    minimum: "5",
    maximum: "500",
    unit: "µm",
  });
  expect(
    claims.find((claim) => claim.label === "Special fixture"),
  ).toMatchObject({
    availability: "unknown",
    normalization_state: "unresolved",
  });
  expect(
    claims.find((claim) => claim.parameter_id === "equipment"),
  ).toMatchObject({
    operator: "text",
    text_value: "Jet rig\nDigital timer",
  });
  expect(
    claims.find((claim) => claim.parameter_id === "reference_panel"),
  ).toMatchObject({
    operator: "required",
    availability: "unsupported",
    stage: "measurement",
  });

  await page.reload();
  await page.getByRole("button", { name: "Edit", exact: true }).first().click();
  const reopened = page.getByRole("dialog");
  const water = reopened.getByRole("group", { name: "Conditions field 1" });
  await expect(water.getByLabel("Maximum", { exact: true })).toHaveValue("80");
  await reopened
    .getByRole("button", { name: "Save capability", exact: true })
    .click();
  await expect(reopened).not.toBeVisible();
  const reloaded = (await (await request.get("/api/lab")).json()) as LabProfile;
  expect(reloaded.capabilities[0].atomic_claims).toEqual(claims);
});

test("lab parameter edits persist and unrelated edits preserve declared facts", async ({
  page,
  request,
}, testInfo) => {
  const reset = await request.post("/api/workspace/reset", {
    data: { acknowledged: true },
  });
  expect(reset.ok()).toBeTruthy();
  const initial = (await (await request.get("/api/lab")).json()) as LabProfile;
  const example = await request.post("/api/lab/example", {
    data: { version: initial.version, example: "legacy" },
  });
  expect(example.ok()).toBeTruthy();
  const lab = (await example.json()) as LabProfile;
  const selected = lab.capabilities.find(
    (capability) => capability.id === "cap_cross_cut",
  )!;
  const declaration: AtomicClaim = {
    id: "lab_spacing",
    parameter_id: "spacing",
    label: "Cut spacing",
    kind: "condition",
    stage: "general",
    operator: "range",
    minimum: "1",
    maximum: "5",
    value: null,
    tolerance: null,
    values: [],
    unit: "mm",
    text_value: null,
    availability: "supported",
    raw_text: "Cut spacing from 1 to 5 mm on steel panels.",
    source_pages: [],
    source_context: "Steel panels",
    normalization_state: "parsed",
    configuration: null,
    sequence: null,
  };
  selected.atomic_claims = [declaration];
  selected.declared_constraints =
    "Steel panels only; customer-supplied samples.";
  selected.notes = "Record individual panel results.";
  const seeded = await request.put("/api/lab", { data: lab });
  expect(seeded.ok()).toBeTruthy();
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
  let dialog = page.getByRole("dialog");
  await expect(
    dialog.getByRole("group", { name: "Conditions field 1" }),
  ).toBeVisible();
  await expect(
    dialog.getByRole("button", { name: /Additional wording and notes/ }),
  ).toHaveAttribute("aria-expanded", "false");
  await dialog.getByLabel("Price", { exact: true }).fill("88.00");
  await dialog.getByRole("button", { name: "Save capability" }).click();
  await expect(dialog).not.toBeVisible();
  let saved = (await (await request.get("/api/lab")).json()) as LabProfile;
  expect(
    saved.capabilities.find((capability) => capability.id === selected.id)
      ?.atomic_claims,
  ).toEqual([declaration]);

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
  dialog = page.getByRole("dialog");
  const details = dialog.getByRole("group", { name: "Conditions field 1" });
  await details.getByLabel("Maximum", { exact: true }).fill("7");
  await details.getByRole("button", { name: /Context and wording/ }).click();
  await details
    .getByLabel("Declaration wording")
    .fill("Cut spacing from 1 to 7 mm on steel panels.");
  await dialog.getByRole("button", { name: "Save capability" }).click();
  await expect(dialog).not.toBeVisible();
  saved = (await (await request.get("/api/lab")).json()) as LabProfile;
  const edited = saved.capabilities.find(
    (capability) => capability.id === selected.id,
  )?.atomic_claims?.[0];
  expect(edited).toMatchObject({
    maximum: "7",
    source_context: "Steel panels",
    normalization_state: "reviewed",
  });

  await page.reload();
  await page
    .getByRole("button", { name: "Cross-cut adhesion", exact: true })
    .click();
  dialog = page.getByRole("dialog");
  await expect(
    dialog.getByRole("button", { name: /Operating conditions/ }),
  ).toHaveAttribute("aria-expanded", "false");
  await expect(
    dialog.getByRole("button", { name: /Measurements & reporting/ }),
  ).toHaveAttribute("aria-expanded", "false");
  await expect(dialog.getByText("1–7 mm", { exact: true })).not.toBeVisible();
  await dialog.getByRole("button", { name: /Operating conditions/ }).click();
  const table = dialog.getByRole("table", {
    name: "Operating conditions saved fields",
  });
  await expect(table.getByText("Cut spacing", { exact: true })).toBeVisible();
  await expect(table.getByText("1–7 mm", { exact: true })).toBeVisible();
  await expect(table.getByText("Steel panels", { exact: true })).toBeVisible();
  await expect(
    dialog.getByText(selected.declared_constraints, { exact: true }),
  ).not.toBeVisible();
  await dialog
    .getByText("Additional description", { exact: true })
    .first()
    .click();
  await expect(
    dialog.getByText(selected.declared_constraints, { exact: true }),
  ).toBeVisible();
  await dialog
    .getByRole("button", { name: /Measurements & reporting/ })
    .click();
  await expect(dialog.getByText(selected.notes, { exact: true })).toBeVisible();
  await page.screenshot({
    path: testInfo.outputPath("lab-capability-details.png"),
    animations: "disabled",
  });
  await dialog
    .getByRole("button", { name: "Edit capability", exact: true })
    .click();
  dialog = page.getByRole("dialog");
  await dialog
    .getByRole("button", { name: /Additional wording and notes/ })
    .click();
  await dialog
    .getByLabel("Conditions and scope", { exact: true })
    .fill("Updated declaration: steel panels only.");
  await dialog.getByRole("button", { name: "Save capability" }).click();
  await expect(dialog.getByRole("alert").first()).toContainText(
    "Check the saved conditions and measurements",
  );
  await dialog
    .getByLabel(
      "I checked the conditions and measurements for this capability.",
    )
    .check();
  await dialog.getByRole("button", { name: "Save capability" }).click();
  await expect(dialog).not.toBeVisible();
  saved = (await (await request.get("/api/lab")).json()) as LabProfile;
  expect(
    saved.capabilities.find((capability) => capability.id === selected.id),
  ).toMatchObject({
    declared_constraints: "Updated declaration: steel panels only.",
    atomic_claims: [edited],
  });
});
