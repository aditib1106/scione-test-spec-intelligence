import {
  expect,
  test,
  type APIRequestContext,
  type Page,
} from "@playwright/test";
import type { AtomicClaim, LabProfile } from "../src/types";
import { ensureCapabilityFieldOpen } from "./capabilityActions";

test.use({ actionTimeout: 15_000 });

async function seed(request: APIRequestContext) {
  expect(
    (
      await request.post("/api/workspace/reset", {
        data: { acknowledged: true },
      })
    ).ok(),
  ).toBeTruthy();
  const initial = (await (await request.get("/api/lab")).json()) as LabProfile;
  const response = await request.post("/api/lab/example", {
    data: { version: initial.version, example: "legacy" },
  });
  expect(response.ok()).toBeTruthy();
  const lab = (await response.json()) as LabProfile;
  const capability = lab.capabilities.find(
    (item) => item.id === "cap_cross_cut",
  )!;
  const numeric: AtomicClaim = {
    id: "compact_water",
    parameter_id: "water_temperature",
    label: "Water temperature",
    kind: "condition",
    stage: "conditioning",
    operator: "range",
    minimum: "20",
    maximum: "80",
    value: null,
    tolerance: null,
    values: [],
    unit: "°C",
    text_value: null,
    availability: "supported",
    raw_text: "Water temperature: 20–80 °C.",
    source_context: "Test water",
    configuration: "Rig A",
    sequence: 1,
    source_pages: [],
    normalization_state: "parsed",
  };
  capability.atomic_claims = [
    numeric,
    {
      ...numeric,
      id: "compact_fixture",
      parameter_id: "custom_fixture",
      label: "Special fixture",
      operator: "required",
      minimum: null,
      maximum: null,
      unit: null,
      kind: "scope",
      text_value: "not yet assessed",
      availability: "unknown",
      stage: "general",
      raw_text: "Special fixture not yet assessed.",
      source_context: null,
      configuration: null,
      sequence: null,
      normalization_state: "unresolved",
    },
    {
      ...numeric,
      id: "compact_thickness",
      parameter_id: "film_thickness",
      label: "Film thickness",
      kind: "reporting",
      minimum: "5",
      maximum: "500",
      unit: "µm",
      stage: "measurement",
      raw_text: "Film thickness: 5–500 µm.",
      source_context: null,
      configuration: null,
      sequence: null,
    },
  ];
  expect((await request.put("/api/lab", { data: lab })).ok()).toBeTruthy();
  return (await (await request.get("/api/lab")).json()) as LabProfile;
}

async function edit(page: Page) {
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
  return page.getByRole("dialog", { name: /^Edit capability/ });
}

test("saved capability fields show compact summaries and cancelling preserves every declared fact", async ({
  page,
  request,
}, testInfo) => {
  const before = await seed(request);
  let writes = 0;
  page.on("request", (sent) => {
    if (["POST", "PUT", "PATCH", "DELETE"].includes(sent.method())) writes++;
  });
  const dialog = await edit(page);
  const water = dialog.getByRole("group", {
    name: "Conditions field 1",
    exact: true,
  });
  const fixture = dialog.getByRole("group", {
    name: "Conditions field 2",
    exact: true,
  });
  const reporting = dialog.getByRole("group", {
    name: "Measurements & reporting field 1",
    exact: true,
  });
  await expect(
    water.getByRole("button", { name: "Edit Water temperature", exact: true }),
  ).toHaveAttribute("aria-expanded", "false");
  expect(
    await water
      .getByRole("button", { name: "Edit Water temperature", exact: true })
      .evaluate((element) =>
        Boolean(
          document.getElementById(element.getAttribute("aria-controls")!),
        ),
      ),
  ).toBe(true);
  await expect(water.getByText("20–80 °C", { exact: true })).toBeVisible();
  await expect(
    water.getByText("conditioning · Test water · Rig A · Step 1", {
      exact: true,
    }),
  ).toBeVisible();
  await expect(water.getByLabel("Minimum", { exact: true })).toHaveCount(0);
  await expect(
    fixture.getByText("Not assessed", { exact: true }),
  ).toBeVisible();
  await expect(reporting.getByText("5–500 µm", { exact: true })).toBeVisible();
  if (testInfo.project.name === "desktop") {
    await dialog.screenshot({
      path: testInfo.outputPath("compact-capability-editor.png"),
      animations: "disabled",
    });
  }
  await ensureCapabilityFieldOpen(water);
  await water.getByLabel("Minimum", { exact: true }).fill("25");
  await water
    .getByRole("button", { name: "Edit Water temperature", exact: true })
    .click();
  await expect(water.getByLabel("Minimum", { exact: true })).toHaveCount(0);
  await expect(water.getByText("25–80 °C", { exact: true })).toBeVisible();
  await ensureCapabilityFieldOpen(water);
  await expect(water.getByLabel("Minimum", { exact: true })).toHaveValue("25");
  await dialog
    .getByRole("button", { name: "Add condition", exact: true })
    .click();
  const added = dialog.getByRole("group", {
    name: "Conditions field 3",
    exact: true,
  });
  await expect(
    added.getByRole("button", { name: "Edit new field", exact: true }),
  ).toHaveAttribute("aria-expanded", "true");
  await expect(added.getByLabel("Field", { exact: true })).toBeVisible();
  await added
    .getByRole("button", { name: "Remove field", exact: true })
    .click();
  await dialog.getByRole("button", { name: "Cancel", exact: true }).click();
  await expect(dialog).not.toBeVisible();
  expect(writes).toBe(0);
  expect(await (await request.get("/api/lab")).json()).toEqual(before);
});

test("saving opens a collapsed invalid capability field and preserves unrelated declarations", async ({
  page,
  request,
}) => {
  const before = await seed(request);
  const original = before.capabilities.find(
    (item) => item.id === "cap_cross_cut",
  )!;
  let writes = 0;
  page.on("request", (sent) => {
    if (["POST", "PUT", "PATCH", "DELETE"].includes(sent.method())) writes++;
  });
  const dialog = await edit(page);
  const water = dialog.getByRole("group", {
    name: "Conditions field 1",
    exact: true,
  });
  await ensureCapabilityFieldOpen(water);
  await water.getByLabel("Minimum", { exact: true }).fill("90");
  await water
    .getByRole("button", { name: "Edit Water temperature", exact: true })
    .click();
  await expect(
    water.getByRole("button", { name: "Edit Water temperature", exact: true }),
  ).toHaveAttribute("aria-expanded", "false");
  await dialog
    .getByRole("button", { name: "Save capability", exact: true })
    .click();
  await expect(
    water.getByRole("button", { name: "Edit Water temperature", exact: true }),
  ).toHaveAttribute("aria-expanded", "true");
  await expect(water.getByRole("alert")).toContainText(
    "Minimum must not exceed maximum",
  );
  await expect(water.getByLabel("Minimum", { exact: true })).toHaveValue("90");
  expect(writes).toBe(0);
  // The same invalid draft must reopen on a second Save, even after collapse.
  await water
    .getByRole("button", { name: "Edit Water temperature", exact: true })
    .click();
  await expect(
    water.getByRole("button", { name: "Edit Water temperature", exact: true }),
  ).toHaveAttribute("aria-expanded", "false");
  await dialog
    .getByRole("button", { name: "Save capability", exact: true })
    .click();
  await expect(
    water.getByRole("button", { name: "Edit Water temperature", exact: true }),
  ).toHaveAttribute("aria-expanded", "true");
  await expect(water.getByLabel("Minimum", { exact: true })).toHaveValue("90");
  expect(writes).toBe(0);
  await water.getByLabel("Minimum", { exact: true }).fill("25");
  await dialog
    .getByRole("button", { name: "Save capability", exact: true })
    .click();
  await expect(dialog).not.toBeVisible();
  expect(writes).toBe(1);
  const after = (await (await request.get("/api/lab")).json()) as LabProfile;
  const saved = after.capabilities.find((item) => item.id === original.id)!;
  expect(saved.atomic_claims![0]).toMatchObject({
    ...original.atomic_claims![0],
    minimum: "25",
    normalization_state: "reviewed",
  });
  expect(saved.atomic_claims!.slice(1)).toEqual(
    original.atomic_claims!.slice(1),
  );
  expect(after.capabilities.filter((item) => item.id !== original.id)).toEqual(
    before.capabilities.filter((item) => item.id !== original.id),
  );
});
