import { expect, test, type Page } from "@playwright/test";
import type { Session } from "../src/auth";

const labUser = {
  id: "test-lab-user",
  username: "labreader",
  display_name: "Alex Reader",
  role: "lab" as const,
  lab_id: "lab-a",
  lab_name: "Lab A",
};
const labSession: Session = {
  authenticated: true,
  enabled: true,
  user: labUser,
  csrf_token: "synthetic-csrf-token-a",
};
const maintainerSession: Session = {
  authenticated: true,
  enabled: true,
  user: {
    ...labUser,
    id: "test-maintainer",
    username: "maintainer",
    display_name: "Maintainer",
    role: "maintainer",
  },
  csrf_token: "synthetic-maintainer-token",
};

async function sessionRoute(page: Page, current: () => Session) {
  await page.route("**/api/auth/session", (route) =>
    route.fulfill({ json: current() }),
  );
}

test("sign-in gates private workspace requests and lab logout clears the old view", async ({
  page,
}) => {
  let current: Session = {
    authenticated: false,
    enabled: true,
    user: null,
    csrf_token: null,
  };
  let release!: () => void;
  const ready = new Promise<void>((resolve) => {
    release = resolve;
  });
  let workspaceBeforeSession = 0;
  let sessionReleased = false;
  page.on("request", (sent) => {
    if (new URL(sent.url()).pathname === "/api/workspace" && !sessionReleased)
      workspaceBeforeSession++;
  });
  await page.route("**/api/auth/session", async (route) => {
    await ready;
    await route.fulfill({ json: current });
  });
  await page.route("**/api/auth/login", async (route) => {
    const body = route.request().postDataJSON();
    if (body.password !== "Synthetic-fixture-password-2026")
      return route.fulfill({
        status: 401,
        json: { detail: "The username or password is incorrect." },
      });
    current = labSession;
    await route.fulfill({ json: current });
  });
  await page.route("**/api/auth/logout", async (route) => {
    expect(route.request().headers()["x-csrf-token"]).toBe(
      labSession.csrf_token,
    );
    current = {
      authenticated: false,
      enabled: true,
      user: null,
      csrf_token: null,
    };
    await route.fulfill({ json: current });
  });
  await page.goto("/reports", { waitUntil: "domcontentloaded" });
  await expect(
    page.getByText("Opening your workspace…", { exact: true }),
  ).toBeVisible();
  expect(workspaceBeforeSession).toBe(0);
  sessionReleased = true;
  release();
  await expect(
    page.getByRole("heading", { name: "Welcome back", exact: true }),
  ).toBeVisible();
  await page.getByRole("textbox", { name: /^Username/ }).fill("labreader");
  await page.getByLabel(/^Password/).fill("wrong-fixture-password");
  await page.getByRole("button", { name: "Sign in", exact: true }).click();
  await expect(page.getByRole("alert")).toContainText("username or password");
  await page.getByLabel(/^Password/).fill("Synthetic-fixture-password-2026");
  await page.getByRole("button", { name: "Sign in", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "Specification library", exact: true }),
  ).toBeVisible();
  if (
    await page
      .getByRole("button", { name: "Open navigation", exact: true })
      .isVisible()
  )
    await page
      .getByRole("button", { name: "Open navigation", exact: true })
      .click();
  await expect(
    page.getByRole("link", { name: "Lab capability", exact: true }),
  ).toBeVisible();
  await expect(
    page.getByRole("link", { name: "Developer Tools", exact: true }),
  ).toHaveCount(0);
  await page.goto("/developer");
  await expect(page).toHaveURL(/\/specifications\/library$/);
  await expect(page.getByText("Developer Tools", { exact: true })).toHaveCount(
    0,
  );
  await page.evaluate(() => {
    sessionStorage.setItem("scione.report.views", "private-old-view");
    localStorage.setItem(
      "scione:completed-duration:matching:v1",
      "private-old-timing",
    );
  });
  await page.getByRole("button", { name: "Account menu", exact: true }).click();
  await page.getByRole("menuitem", { name: "Sign out", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "Welcome back", exact: true }),
  ).toBeVisible();
  await expect(
    page.getByRole("link", { name: "Lab capability", exact: true }),
  ).toHaveCount(0);
  expect(
    await page.evaluate(() => sessionStorage.getItem("scione.report.views")),
  ).toBeNull();
  expect(
    await page.evaluate(() =>
      localStorage.getItem("scione:completed-duration:matching:v1"),
    ),
  ).toBeNull();
});

test("lab review uses one analysis action and keeps model controls out of the interface", async ({
  page,
  request,
}) => {
  const imported = await (
    await request.post("/api/specifications/worked-example")
  ).json();
  const id = imported.document_id;
  const source = await (await request.get(`/api/specifications/${id}`)).json();
  await sessionRoute(page, () => labSession);
  await page.route("**/api/workspace", async (route) => {
    const upstream = await route.fetch();
    const data = await upstream.json();
    await route.fulfill({
      json: {
        ...data,
        providers: [
          {
            id: "fireworks",
            label: "Fireworks",
            model: "synthetic-test-model",
            available: true,
            output_limit: 8192,
          },
        ],
      },
    });
  });
  let analysisCalls = 0;
  await page.route(`**/api/specifications/${id}/analysis`, async (route) => {
    analysisCalls++;
    expect(route.request().headers()["x-csrf-token"]).toBe(
      labSession.csrf_token,
    );
    expect(route.request().postDataJSON().permission).toBe(true);
    await route.fulfill({ json: source });
  });
  await page.goto(`/specifications?document=${id}`);
  await expect(
    page.getByRole("button", { name: "Analyze again", exact: true }),
  ).toBeVisible();
  await expect(
    page.getByRole("combobox", { name: "Provider", exact: true }),
  ).toHaveCount(0);
  await expect(page.getByLabel("Model", { exact: true })).toHaveCount(0);
  await expect(
    page.getByText(
      /API quota|API.*credits|Max output tokens|Developer details/,
    ),
  ).toHaveCount(0);
  await expect(
    page.getByText("Manually prepared example requirements.", { exact: false }),
  ).toHaveCount(0);
  await page
    .getByRole("button", { name: "Analyze again", exact: true })
    .click();
  await expect.poll(() => analysisCalls).toBe(1);
  await page.goto(`/reports?document=${id}`);
  await expect(
    page.getByRole("heading", { name: "Gap report", exact: true }),
  ).toBeVisible();
  await expect(
    page.getByRole("button", { name: "Developer report tools", exact: true }),
  ).toHaveCount(0);
  await expect(
    page.getByRole("button", { name: "Report options", exact: true }),
  ).toHaveCount(0);
});

test("maintainer lab switching rotates request identity and drops previous lab view state", async ({
  page,
}) => {
  let current = maintainerSession;
  await sessionRoute(page, () => current);
  await page.route("**/api/auth/workspaces", (route) =>
    route.fulfill({
      json: {
        labs: [
          { lab_id: "lab-a", lab_name: "Lab A" },
          { lab_id: "lab-b", lab_name: "Lab B" },
        ],
      },
    }),
  );
  await page.route("**/api/auth/workspace", async (route) => {
    expect(route.request().headers()["x-csrf-token"]).toBe(
      maintainerSession.csrf_token,
    );
    expect(route.request().postDataJSON()).toEqual({ lab_id: "lab-b" });
    current = {
      ...maintainerSession,
      user: { ...maintainerSession.user!, lab_id: "lab-b", lab_name: "Lab B" },
      csrf_token: "synthetic-maintainer-token-b",
    };
    await route.fulfill({ json: current });
  });
  await page.goto("/specifications/library");
  if (
    await page
      .getByRole("button", { name: "Open navigation", exact: true })
      .isVisible()
  )
    await page
      .getByRole("button", { name: "Open navigation", exact: true })
      .click();
  await expect(
    page.getByRole("link", { name: "Developer Tools", exact: true }),
  ).toBeVisible();
  if (
    await page
      .getByRole("button", { name: "Close navigation", exact: true })
      .isVisible()
  )
    await page
      .getByRole("button", { name: "Close navigation", exact: true })
      .click();
  await page.evaluate(() =>
    sessionStorage.setItem("scione.report.views", "old-lab-selection"),
  );
  await page.getByRole("combobox", { name: /^Viewing lab/ }).click();
  await page.getByRole("option", { name: "Lab B", exact: true }).click();
  await expect(
    page.getByRole("combobox", { name: /^Viewing lab/ }),
  ).toContainText("Lab B");
  expect(
    await page.evaluate(() => sessionStorage.getItem("scione.report.views")),
  ).toBeNull();
});

test("an expired private request returns to sign-in without showing the old lab", async ({
  page,
}) => {
  await sessionRoute(page, () => labSession);
  await page.route("**/api/workspace", (route) =>
    route.fulfill({ status: 401, json: { detail: "Sign in to continue." } }),
  );
  await page.goto("/specifications/library");
  await expect(
    page.getByRole("heading", { name: "Welcome back", exact: true }),
  ).toBeVisible();
  await expect(page).toHaveURL(/\/login$/);
  await expect(
    page.getByRole("link", { name: "Lab capability", exact: true }),
  ).toHaveCount(0);
});
