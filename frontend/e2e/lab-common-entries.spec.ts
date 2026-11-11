import { expect, test } from "@playwright/test";
import type { AtomicClaim, LabProfile } from "../src/types";

test("common entries explain their scope and edit only the selected test", async ({
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
  const initial = (await (await request.get("/api/lab")).json()) as LabProfile;
  const example = await request.post("/api/lab/example", {
    data: { version: initial.version, example: "legacy" },
  });
  expect(example.ok()).toBeTruthy();
  const lab = (await example.json()) as LabProfile;
  const originalText = "Report each specimen separately.";
  lab.capabilities.forEach((capability) => {
    const claim: AtomicClaim = {
      id: `${capability.id}_individual_reporting`,
      parameter_id: "individual_reporting",
      label: "Individual result reporting",
      kind: "reporting",
      stage: "general",
      operator: "text",
      value: null,
      minimum: null,
      maximum: null,
      tolerance: null,
      values: [],
      unit: null,
      text_value: originalText,
      availability: "supported",
      raw_text: originalText,
      source_context: null,
      source_pages: [],
      normalization_state: "reviewed",
      configuration: null,
      sequence: null,
    };
    capability.atomic_claims = [claim];
  });
  expect((await request.put("/api/lab", { data: lab })).ok()).toBeTruthy();
  const workspace = await (await request.get("/api/workspace")).json();
  const selected = lab.capabilities[0];
  const testName = workspace.catalog.tests.find(
    (item: { id: string }) => item.id === selected.canonical_test_id,
  ).name;
  await page.goto("/my-lab");
  const common = page.getByRole("button", { name: /^Common entries \(1\)/ });
  await common.click();
  await expect(
    page.getByText(
      `Identical entries recorded on all ${lab.capabilities.length} tests.`,
      { exact: true },
    ),
  ).toBeVisible();
  await expect(
    page.getByText(
      "Choose a test to edit these entries. Changes apply to that test only.",
      { exact: true },
    ),
  ).toBeVisible();
  await expect(
    page.getByRole("button", { name: "Edit selected test", exact: true }),
  ).toBeDisabled();
  await page
    .getByRole("combobox", { name: "Test to edit", exact: true })
    .click();
  await page.getByRole("option").filter({ hasText: testName }).first().click();
  await page
    .getByRole("button", { name: "Edit selected test", exact: true })
    .click();
  const editor = page.getByRole("dialog", { name: "Edit capability" });
  const reporting = editor.getByRole("group", {
    name: "Acceptance criteria / reporting field 1",
  });
  const updatedText =
    "Report each specimen separately with a specimen photograph.";
  await reporting
    .getByLabel("Declared capability", { exact: true })
    .fill(updatedText);
  await reporting.getByRole("button", { name: /Context and wording/ }).click();
  await reporting
    .getByLabel("Declaration wording", { exact: true })
    .fill(updatedText);
  await editor
    .getByRole("button", { name: "Save capability", exact: true })
    .click();
  await expect(editor).not.toBeVisible();
  const saved = (await (await request.get("/api/lab")).json()) as LabProfile;
  expect(
    saved.capabilities.find((capability) => capability.id === selected.id)!
      .atomic_claims![0].text_value,
  ).toBe(updatedText);
  expect(
    saved.capabilities
      .filter((capability) => capability.id !== selected.id)
      .every(
        (capability) =>
          capability.atomic_claims![0].text_value === originalText,
      ),
  ).toBeTruthy();
  await expect(
    page.getByRole("button", { name: /^Common entries/ }),
  ).toHaveCount(0);
  await page.screenshot({
    path: testInfo.outputPath("common-entries-edited-test.png"),
    animations: "disabled",
  });
});
