import { expect, test } from "@playwright/test";
import type {
  Capability,
  CapabilitySaveResponse,
  Workspace,
} from "../src/types";

test("catalog distinguishes shared tests from private tests and attached private methods", async ({
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
  const workspace = (await (
    await request.get("/api/workspace")
  ).json()) as Workspace;
  const shared = workspace.catalog.tests.find(
    (item) => item.name === "Pencil hardness",
  )!;
  const sharedMethod = workspace.catalog.methods.find((item) =>
    shared.method_ids.includes(item.id),
  )!;
  const capability: Capability = {
    id: "cap_visibility_fixture",
    canonical_test_id: shared.id,
    method_ids: [sharedMethod.id],
    declared_constraints: null,
    structured_limits: [],
    atomic_claims: [],
    notes: null,
    price: null,
    currency: null,
    pricing_basis: null,
  };
  const first = await request.post("/api/lab/capabilities", {
    data: {
      version: workspace.lab.version,
      capability,
      custom_methods: [{ name: "Lab surface fixture check" }],
    },
  });
  expect(first.ok()).toBeTruthy();
  const afterFirst = (await first.json()) as CapabilitySaveResponse;
  expect(
    (
      await request.post("/api/lab/capabilities", {
        data: {
          version: afterFirst.lab.version,
          capability: {
            ...capability,
            canonical_test_id: "custom",
            method_ids: [],
          },
          custom_test: { name: "Lab thermal fixture assessment" },
        },
      })
    ).ok(),
  ).toBeTruthy();
  await page.goto("/catalog");
  const search = page.getByRole("textbox", {
    name: "Search test catalog",
    exact: true,
  });
  await search.fill("Pencil hardness");
  const sharedRow = page.getByRole("row").filter({
    has: page.getByRole("button", { name: "Pencil hardness", exact: true }),
  });
  await expect(sharedRow.getByRole("cell").first()).not.toContainText(
    "Shared reference",
  );
  await expect(sharedRow).toContainText("Lab surface fixture check");
  await expect(sharedRow).toContainText("Private");
  await page
    .getByRole("tab", { name: "Private to this lab", exact: true })
    .click();
  await search.fill("");
  await expect(
    page.getByRole("button", { name: "Pencil hardness", exact: true }),
  ).toBeVisible();
  await expect(
    page.getByRole("button", {
      name: "Lab thermal fixture assessment",
      exact: true,
    }),
  ).toBeVisible();
  await expect(sharedRow.getByRole("cell").first()).not.toContainText(
    "Shared reference",
  );
  await page
    .getByRole("button", { name: "Pencil hardness", exact: true })
    .click();
  const details = page.getByRole("dialog");
  await expect(details).toContainText(
    "Private methods are visible only within your lab.",
  );
  const sharedMethodRow = details
    .getByRole("row")
    .filter({ hasText: sharedMethod.identifier });
  await expect(sharedMethodRow).toContainText("Shared reference");
  const privateMethodRow = details
    .getByRole("row")
    .filter({ hasText: "Lab surface fixture check" });
  await expect(privateMethodRow).toContainText("Private to this lab");
  await details.getByRole("button", { name: "Close", exact: true }).click();
  await page
    .getByRole("button", {
      name: "Lab thermal fixture assessment",
      exact: true,
    })
    .click();
  await expect(details).toContainText(
    "This test is visible only within your lab.",
  );
  await expect(details).toContainText("No method is recorded for this test.");
  await details.getByRole("button", { name: "Close", exact: true }).click();
  await page
    .getByRole("tab", { name: "Shared reference", exact: true })
    .click();
  await search.fill("Lab thermal fixture assessment");
  await expect(
    page.getByRole("button", {
      name: "Lab thermal fixture assessment",
      exact: true,
    }),
  ).toHaveCount(0);
  await expect(
    page.getByText("No tests match this search.", { exact: true }),
  ).toBeVisible();
  await page.getByRole("tab", { name: "All tests", exact: true }).click();
  await expect(
    page.getByRole("button", {
      name: "Lab thermal fixture assessment",
      exact: true,
    }),
  ).toBeVisible();
  if (testInfo.project.name === "desktop") {
    await page
      .getByRole("tab", { name: "Private to this lab", exact: true })
      .click();
    await search.fill("");
    await page.screenshot({
      path: testInfo.outputPath("catalog-private-visibility.png"),
      fullPage: true,
      animations: "disabled",
    });
    await page.getByRole("tab", { name: "All tests", exact: true }).click();
    await page.screenshot({
      path: testInfo.outputPath("catalog-overview.png"),
      fullPage: true,
      animations: "disabled",
    });
  }
});
