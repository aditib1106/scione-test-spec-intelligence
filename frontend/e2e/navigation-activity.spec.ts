import { expect, test, type Page } from "@playwright/test";
import type { SpecificationData, Workspace } from "../src/types";

async function navigate(page: Page, label: string) {
  const open = page.getByRole("button", {
    name: "Open navigation",
    exact: true,
  });
  const mobile = (page.viewportSize()?.width ?? 1440) < 1200;
  if (mobile) {
    await expect(open).toBeVisible();
    await open.click();
  }
  await page
    .getByRole("list", { name: "Workspace navigation", exact: true })
    .getByRole("link", { name: label, exact: true })
    .click();
  if (mobile)
    await expect(
      page.getByRole("list", { name: "Workspace navigation", exact: true }),
    ).not.toBeVisible();
}

for (const reload of [false, true]) {
  test(`accepted analysis ${reload ? "reattaches after reload" : "continues across navigation"} without a second model request`, async ({
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
    const { document_id: id } = await (
      await request.post("/api/specifications/worked-example")
    ).json();
    const source = (await (
      await request.get(`/api/specifications/${id}`)
    ).json()) as SpecificationData;
    const baseWorkspace = (await (
      await request.get("/api/workspace")
    ).json()) as Workspace;
    const updated = structuredClone(source);
    for (const item of updated.run!.extraction.requirements)
      item.original_test_name = `Completed: ${item.original_test_name}`;
    let started = false;
    let finished = false;
    let calls = 0;
    let release!: () => void;
    const held = new Promise<void>((resolve) => {
      release = resolve;
    });
    await page.route("**/api/auth/session", (route) =>
      route.fulfill({
        json: {
          authenticated: true,
          enabled: true,
          csrf_token: "offline-navigation-csrf",
          user: {
            id: "navigation-user",
            username: "navigation-user",
            display_name: "Reader",
            role: "lab",
            lab_id: "lab-a",
            lab_name: "Lab A",
          },
        },
      }),
    );
    await page.route("**/api/workspace", async (route) => {
      await route.fulfill({
        json: {
          ...baseWorkspace,
          providers: [
            {
              id: "sail",
              model: "offline-navigation-model",
              available: true,
              output_limit: 8192,
              label: "Sail",
            },
          ],
        },
      });
    });
    const active = () =>
      started && !finished
        ? [
            {
              operation_id: "offline-accepted-operation",
              document_id: id,
              kind: "extraction",
              started_at: new Date(Date.now() - 4000).toISOString(),
              elapsed_seconds: 4,
              state: "running",
            },
          ]
        : [];
    await page.route("**/api/operations", (route) =>
      route.fulfill({ json: { active: active() } }),
    );
    await page.route(`**/api/specifications/${id}/operations`, (route) =>
      route.fulfill({
        json: {
          state: active().length ? "running" : "idle",
          active: active(),
          notice: null,
        },
      }),
    );
    await page.route(`**/api/specifications/${id}`, (route) =>
      route.fulfill({ json: finished ? updated : source }),
    );
    await page.route(`**/api/specifications/${id}/analysis`, async (route) => {
      calls += 1;
      started = true;
      await held;
      // A full browser reload closes the old HTTP observer; the independent
      // server status below still represents the already accepted operation.
      await route.fulfill({ json: updated }).catch(() => {});
    });
    await page.goto(`/specifications?document=${id}`);
    const analyze = page.getByRole("button", {
      name: "Analyze again",
      exact: true,
    });
    await expect(analyze).toBeEnabled();
    await analyze.evaluate((button: HTMLButtonElement) => {
      button.click();
      button.click();
    });
    await expect.poll(() => calls).toBe(1);
    await expect(
      page.getByText("You can browse other pages while this finishes."),
    ).toBeVisible();
    if (reload) await page.reload();
    else {
      await navigate(page, "Lab capability");
      await expect(
        page.getByRole("textbox", {
          name: "Search tests and methods",
          exact: true,
        }),
      ).toBeVisible();
      await page
        .getByRole("link", { name: "View progress", exact: true })
        .click();
    }
    await expect(
      page.getByRole("button", { name: "Analyzing…", exact: true }),
    ).toBeDisabled();
    expect(calls).toBe(1);
    finished = true;
    release();
    await expect(
      page.getByText("You can browse other pages while this finishes."),
    ).not.toBeVisible();
    await expect(
      page.getByRole("combobox", { name: "Selected test", exact: true }),
    ).toContainText("Completed:");
    expect(calls).toBe(1);
  });
}

test("returning to the library displays its session cache while a fresh read is pending", async ({
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
  expect(
    (await request.post("/api/specifications/worked-example")).ok(),
  ).toBeTruthy();
  let hold = false;
  let release!: () => void;
  const held = new Promise<void>((resolve) => {
    release = resolve;
  });
  await page.route("**/api/library", async (route) => {
    if (hold) await held;
    const response = await route.fetch();
    await route.fulfill({ response });
  });
  await page.goto("/specifications/library");
  const table = page.getByRole("table", {
    name: "Saved customer specifications",
  });
  await expect(table).toBeVisible();
  await expect(table.getByRole("row")).toHaveCount(2);
  await navigate(page, "Lab capability");
  hold = true;
  try {
    await navigate(page, "Specifications");
    // The second server read is deliberately blocked; rows must be available
    // from the current session's in-memory cache rather than a new response.
    await expect(table).toBeVisible();
    await expect(table.getByRole("row")).toHaveCount(2);
  } finally {
    const refreshed = page.waitForResponse(
      (response) => new URL(response.url()).pathname === "/api/library",
    );
    release();
    await refreshed;
  }
});
