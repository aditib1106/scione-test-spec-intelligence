import { expect, test } from "@playwright/test";
import type {
  Capability,
  CapabilitySaveResponse,
  Catalog,
  Workspace,
} from "../src/types";

test.use({ actionTimeout: 15_000 });

test("catalog keeps Tests as default and searches paginated method references without writes", async ({
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
  const before = (await (
    await request.get("/api/workspace")
  ).json()) as Workspace;
  expect(before.catalog.methods.length).toBeGreaterThan(10);
  const method = before.catalog.methods.find(
    (entry) =>
      entry.identifier &&
      entry.aliases.some((alias) => alias.text !== entry.identifier) &&
      before.catalog.tests.some((test) => test.method_ids.includes(entry.id)),
  )!;
  expect(method).toBeTruthy();
  const alias = method.aliases.find(
    (entry) => entry.text !== method.identifier,
  )!.text;
  const related = before.catalog.tests.filter((entry) =>
    entry.method_ids.includes(method.id),
  );
  let writes = 0;
  page.on("request", (sent) => {
    if (!["GET", "HEAD", "OPTIONS"].includes(sent.method())) writes++;
  });
  await page.goto("/catalog");
  const switcher = page.getByRole("group", {
    name: "Catalog view",
    exact: true,
  });
  await expect(
    switcher.getByRole("button", { name: "Tests", exact: true }),
  ).toHaveAttribute("aria-pressed", "true");
  await expect(
    page.getByRole("table", { name: "Test catalog", exact: true }),
  ).toBeVisible();
  await switcher.getByRole("button", { name: "Methods", exact: true }).focus();
  await page.keyboard.press("Space");
  const table = page.getByRole("table", {
    name: "Method catalog",
    exact: true,
  });
  await expect(table).toBeVisible();
  await expect(table.getByRole("row")).toHaveCount(11);
  const firstMethod = await table.getByRole("button").first().textContent();
  await page
    .getByRole("button", { name: "Go to next page", exact: true })
    .click();
  await expect(table.getByRole("button").first()).not.toHaveText(firstMethod!);
  const search = page.getByRole("textbox", {
    name: "Search test catalog",
    exact: true,
  });
  await search.fill(alias);
  const result = table.getByRole("button", {
    name: method.identifier,
    exact: true,
  });
  await expect(result).toBeVisible();
  await result.click();
  const detail = page.getByRole("dialog", {
    name: method.identifier,
    exact: true,
  });
  await expect(detail).toBeVisible();
  await expect(detail.getByText(alias, { exact: false }).first()).toBeVisible();
  for (const linked of related)
    await expect(
      detail
        .getByRole("region", { name: "Related test items", exact: true })
        .getByText(linked.name, { exact: true }),
    ).toBeVisible();
  await expect(
    detail.getByText("Not recorded in this catalog.", { exact: true }),
  ).toBeVisible();
  await expect(detail).toContainText("does not establish lab support");
  if (testInfo.project.name === "desktop")
    await detail.screenshot({
      path: testInfo.outputPath("catalog-method-details.png"),
      animations: "disabled",
    });
  await detail.getByRole("button", { name: "Close", exact: true }).click();
  await search.fill(related[0].name);
  await expect(result).toBeVisible();
  if (method.edition) {
    await search.fill(method.edition);
    await expect(
      table.getByRole("cell", { name: method.edition, exact: true }).first(),
    ).toBeVisible();
  }
  await search.fill("");
  await expect(
    page.getByRole("button", { name: "Go to previous page", exact: true }),
  ).toBeDisabled();
  if (testInfo.project.name === "desktop")
    await page.screenshot({
      path: testInfo.outputPath("catalog-method-list.png"),
      animations: "disabled",
    });
  await switcher.getByRole("button", { name: "Tests", exact: true }).click();
  await expect(
    page.getByRole("tab", { name: "All tests", exact: true }),
  ).toHaveAttribute("aria-selected", "true");
  await expect(
    page.getByRole("table", { name: "Test catalog", exact: true }),
  ).toBeVisible();
  const after = (await (
    await request.get("/api/workspace")
  ).json()) as Workspace;
  expect(after.catalog).toEqual(before.catalog);
  expect(after.lab).toEqual(before.lab);
  expect(writes).toBe(0);
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth + 1,
    ),
  ).toBeTruthy();
});

test("method browsing includes a real private lab method and never adds it to the shared fixture catalog", async ({
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
  const workspace = (await (
    await request.get("/api/workspace")
  ).json()) as Workspace;
  const sharedTest = workspace.catalog.tests.find(
    (entry) => entry.name === "Pencil hardness",
  )!;
  const capability: Capability = {
    id: "cap_catalog_method_search_fixture",
    canonical_test_id: sharedTest.id,
    method_ids: [],
    declared_constraints: null,
    structured_limits: [],
    atomic_claims: [],
    notes: null,
    price: null,
    currency: null,
    pricing_basis: null,
  };
  const name = "Lab fixture surface assessment";
  const identifier = "LAB-SEARCH-731";
  const alias = "Local indenter route";
  const created = await request.post("/api/lab/capabilities", {
    data: {
      version: workspace.lab.version,
      capability,
      custom_methods: [{ name, identifier, aliases: [alias] }],
    },
  });
  expect(created.ok()).toBeTruthy();
  const saved = (await created.json()) as CapabilitySaveResponse;
  const privateMethod = saved.catalog.methods.find(
    (entry) => entry.identifier === identifier,
  )!;
  expect(privateMethod.id).toMatch(/^labmethod_/);
  expect(
    saved.catalog.tests.find((entry) => entry.id === sharedTest.id)!.method_ids,
  ).toContain(privateMethod.id);
  let writes = 0;
  page.on("request", (sent) => {
    if (!["GET", "HEAD", "OPTIONS"].includes(sent.method())) writes++;
  });
  await page.goto("/catalog");
  await page.getByRole("button", { name: "Methods", exact: true }).click();
  await page
    .getByRole("tab", { name: "Private to this lab", exact: true })
    .click();
  const search = page.getByRole("textbox", {
    name: "Search test catalog",
    exact: true,
  });
  await search.fill(alias);
  const table = page.getByRole("table", {
    name: "Method catalog",
    exact: true,
  });
  const row = table.getByRole("row").filter({
    has: page.getByRole("button", { name: identifier, exact: true }),
  });
  await expect(row).toContainText("Private");
  await expect(row).toContainText(sharedTest.name);
  await row.getByRole("button", { name: identifier, exact: true }).click();
  const detail = page.getByRole("dialog", { name: identifier, exact: true });
  await expect(detail).toContainText("Private to this lab");
  await expect(detail.getByText(name, { exact: true })).toBeVisible();
  await expect(
    detail
      .getByRole("region", { name: "Related test items", exact: true })
      .getByText(sharedTest.name, { exact: true }),
  ).toBeVisible();
  await detail.getByRole("button", { name: "Close", exact: true }).click();
  await page
    .getByRole("tab", { name: "Shared reference", exact: true })
    .click();
  await expect(
    page.getByText("No methods match this search.", { exact: true }),
  ).toBeVisible();
  await page.getByRole("tab", { name: "All methods", exact: true }).click();
  await expect(
    table.getByRole("button", { name: identifier, exact: true }),
  ).toBeVisible();
  expect((await (await request.get("/api/workspace")).json()).catalog).toEqual(
    saved.catalog,
  );
  expect(await (await request.get("/api/lab")).json()).toEqual(saved.lab);
  // The second disposable workspace has a separate lab/store, not an auth bypass.
  // Real cross-login authorization is exercised by the authenticated API suite.
  const separate = (await (
    await request.get("http://127.0.0.1:8011/api/catalog")
  ).json()) as Catalog;
  expect(
    separate.methods.some(
      (entry) =>
        entry.id === privateMethod.id || entry.identifier === identifier,
    ),
  ).toBeFalsy();
  expect(writes).toBe(0);
});
