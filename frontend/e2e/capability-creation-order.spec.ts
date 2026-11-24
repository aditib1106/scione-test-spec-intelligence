import { expect, test, type APIRequestContext } from "@playwright/test";
import {
  capabilitiesNewestFirst,
  capabilityCreationTimes,
} from "../src/capabilityOrder";
import type {
  Capability,
  CapabilitySaveResponse,
  Workspace,
} from "../src/types";

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
async function add(
  request: APIRequestContext,
  workspace: Workspace,
  name: string,
) {
  const response = await request.post("/api/lab/capabilities", {
    data: {
      version: workspace.lab.version,
      capability: {
        id: "placeholder",
        canonical_test_id: workspace.catalog.tests[0].id,
        method_ids: [],
        declared_constraints: null,
        structured_limits: [],
        atomic_claims: [],
        notes: null,
        price: null,
        currency: null,
        pricing_basis: null,
      },
      custom_test: { name },
    },
  });
  expect(response.ok()).toBeTruthy();
  const saved = (await response.json()) as CapabilitySaveResponse;
  const testId = saved.catalog.tests.find((item) => item.name === name)!.id;
  const capability = saved.lab.capabilities.find(
    (item) => item.canonical_test_id === testId,
  )!;
  return {
    workspace: (await (
      await request.get("/api/workspace")
    ).json()) as Workspace,
    capability,
  };
}

test("new capabilities display newest first, while editing preserves real creation order and raw lab data", async ({
  page,
  request,
}, testInfo) => {
  const initial = await reset(request);
  const first = await add(request, initial, "Earlier lab fixture");
  const second = await add(request, first.workspace, "Newest lab fixture");
  expect(second.workspace.lab.capabilities.map((item) => item.id)).toEqual([
    first.capability.id,
    second.capability.id,
  ]);
  const metadata = second.workspace.capability_creation!;
  expect(metadata.lab_version).toBe(second.workspace.lab.version);
  expect(
    Date.parse(metadata.created_at[second.capability.id]),
  ).toBeGreaterThanOrEqual(
    Date.parse(metadata.created_at[first.capability.id]),
  );
  let writes = 0;
  page.on("request", (sent) => {
    if (!["GET", "HEAD", "OPTIONS"].includes(sent.method())) writes++;
  });
  await page.goto("/my-lab");
  const table = page.getByRole("table", {
    name: "Lab capabilities",
    exact: true,
  });
  await expect(table.getByRole("row").nth(1)).toContainText(
    "Newest lab fixture",
  );
  await expect(table.getByRole("row").nth(2)).toContainText(
    "Earlier lab fixture",
  );
  await expect(
    page.getByText("Newest added first", { exact: true }),
  ).toBeVisible();
  expect(writes).toBe(0);
  const edited = await request.put(
    `/api/lab/capabilities/${first.capability.id}`,
    {
      data: {
        version: second.workspace.lab.version,
        capability: {
          ...first.capability,
          price: "12.00",
          currency: "USD",
          pricing_basis: "per test",
        },
      },
    },
  );
  expect(edited.ok()).toBeTruthy();
  await page.reload();
  await expect(table.getByRole("row").nth(1)).toContainText(
    "Newest lab fixture",
  );
  await expect(table.getByRole("row").nth(2)).toContainText(
    "Earlier lab fixture",
  );
  const after = (await (
    await request.get("/api/workspace")
  ).json()) as Workspace;
  expect(after.lab.capabilities.map((item) => item.id)).toEqual([
    first.capability.id,
    second.capability.id,
  ]);
  expect(after.capability_creation!.created_at).toEqual(metadata.created_at);
  expect(after.lab.capabilities[0].price).toBe("12.00");
  expect(writes).toBe(0);
  // Follow the actual form save, without reloading the page: its workspace
  // refresh must bring the new metadata along with the saved declaration.
  await page
    .getByRole("button", { name: "Add a test", exact: true })
    .first()
    .click();
  const editor = page.getByRole("dialog");
  await editor
    .getByRole("button", { name: "Create custom test", exact: true })
    .click();
  await editor
    .getByLabel(/^Custom test name/)
    .fill("Newest test entered through the form");
  await editor
    .getByRole("button", { name: "Save capability", exact: true })
    .click();
  await expect(editor).not.toBeVisible();
  await expect(table.getByRole("row").nth(1)).toContainText(
    "Newest test entered through the form",
  );
  await expect(table.getByRole("row").nth(2)).toContainText(
    "Newest lab fixture",
  );
  await expect(table.getByRole("row").nth(3)).toContainText(
    "Earlier lab fixture",
  );
  expect(writes).toBe(1);
  const formSaved = (await (
    await request.get("/api/workspace")
  ).json()) as Workspace;
  expect(formSaved.lab.capabilities.slice(0, 2)).toEqual(
    after.lab.capabilities,
  );
  expect(formSaved.capability_creation!.created_at[first.capability.id]).toBe(
    metadata.created_at[first.capability.id],
  );
  expect(formSaved.capability_creation!.created_at[second.capability.id]).toBe(
    metadata.created_at[second.capability.id],
  );
  await page.screenshot({
    path: testInfo.outputPath("capability-newest-first.png"),
    animations: "disabled",
  });
});

test("display sorting rejects stale or foreign metadata and leaves legacy dates and the scientific declaration intact", async ({
  page,
  request,
}) => {
  const initial = await reset(request);
  const first = await add(request, initial, "Legacy order first");
  const second = await add(request, first.workspace, "Legacy order second");
  const lab = second.workspace.lab;
  const original = JSON.stringify(lab);
  const a = first.capability.id,
    b = second.capability.id;
  const ids = (rows: readonly Capability[]) => rows.map((entry) => entry.id);
  const bound = { lab_id: lab.id, lab_version: lab.version };
  for (const metadata of [
    undefined,
    null,
    {
      ...bound,
      lab_id: "different-lab",
      created_at: { [b]: "2026-11-24T10:00:00Z" },
    },
    {
      ...bound,
      lab_version: lab.version - 1,
      created_at: { [b]: "2026-11-24T10:00:00Z" },
    },
    { ...bound, created_at: { [b]: "2026-11-24" } },
    { ...bound, created_at: { [b]: "not-a-date" } },
  ])
    expect(
      ids(
        capabilitiesNewestFirst(
          lab.capabilities,
          capabilityCreationTimes(lab, metadata),
        ),
      ),
    ).toEqual([a, b]);
  const mixed = capabilityCreationTimes(lab, {
    ...bound,
    created_at: { [b]: "2026-11-24T10:00:00Z" },
  });
  expect(ids(capabilitiesNewestFirst(lab.capabilities, mixed))).toEqual([b, a]);
  expect(
    ids(
      capabilitiesNewestFirst(
        lab.capabilities,
        capabilityCreationTimes(lab, {
          ...bound,
          created_at: {
            [a]: "2026-11-24T09:00:00Z",
            [b]: "2026-11-24T10:00:00+02:00",
          },
        }),
      ),
    ),
  ).toEqual([a, b]);
  expect(
    ids(
      capabilitiesNewestFirst(
        lab.capabilities,
        capabilityCreationTimes(lab, {
          ...bound,
          created_at: {
            [a]: "2026-11-24T10:00:00Z",
            [b]: "2026-11-24T10:00:00Z",
          },
        }),
      ),
    ),
  ).toEqual([a, b]);
  expect(JSON.stringify(lab)).toBe(original);
  let payload: Workspace = {
    ...second.workspace,
    capability_creation: {
      ...bound,
      lab_version: lab.version - 1,
      created_at: { [b]: "2026-11-24T10:00:00Z" },
    },
  };
  await page.route("**/api/workspace", (route) =>
    route.fulfill({ json: payload }),
  );
  let writes = 0;
  page.on("request", (sent) => {
    if (!["GET", "HEAD", "OPTIONS"].includes(sent.method())) writes++;
  });
  await page.goto("/my-lab");
  const table = page.getByRole("table", {
    name: "Lab capabilities",
    exact: true,
  });
  await expect(table.getByRole("row").nth(1)).toContainText(
    "Legacy order first",
  );
  await expect(
    page.getByText("Newest added first", { exact: true }),
  ).toHaveCount(0);
  payload = { ...payload, capability_creation: { ...bound, created_at: {} } };
  await page.reload();
  await expect(table.getByRole("row").nth(1)).toContainText(
    "Legacy order first",
  );
  expect(writes).toBe(0);
});
