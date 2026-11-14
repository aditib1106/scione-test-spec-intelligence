import {
  expect,
  test,
  type APIRequestContext,
  type Page,
} from "@playwright/test";
import type { SpecificationData, Workspace } from "../src/types";
import type { ComparisonData } from "../src/comparisonTypes";

async function fixture(page: Page, request: APIRequestContext) {
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
  const workspace = (await (
    await request.get("/api/workspace")
  ).json()) as Workspace;
  const comparison = (await (
    await request.get(`/api/comparisons/${id}`)
  ).json()) as ComparisonData;
  const updated = structuredClone(source);
  updated.run!.run_id = "async-source-result";
  for (const item of updated.run!.extraction.requirements)
    item.original_test_name = `Completed: ${item.original_test_name}`;
  const state = {
    started: false,
    finished: false,
    failed: false,
    expired: false,
    posts: 0,
    reads: 0,
    requestId: "",
    dropAck: false,
    dropNextPoll: false,
    kind: "extraction" as "extraction" | "matching",
  };
  const job = () => ({
    operation_id: "offline-async-job",
    request_id: state.requestId,
    document_id: id,
    kind: state.kind,
    state: state.failed ? "failed" : state.finished ? "completed" : "running",
    started_at: new Date(Date.now() - 3000).toISOString(),
    progress: {
      stage: "extracting",
      completed_steps: 1,
      total_steps: 6,
      percent: 100 / 6,
      message: "Reading document sections.",
    },
    ...(state.finished && {
      result: {
        run_id:
          state.kind === "extraction"
            ? updated.run!.run_id
            : "async-matching-result",
        status: "completed",
        cached: false,
      },
    }),
    ...(state.failed && {
      error: {
        status: 400,
        detail: "Offline job failed validation. No automatic retry was made.",
      },
    }),
  });
  const active = () =>
    state.started && !state.finished && !state.failed && !state.expired
      ? [job()]
      : [];
  await page.route("**/api/auth/session", (route) =>
    route.fulfill({
      json: {
        authenticated: true,
        enabled: true,
        csrf_token: "offline-async-csrf",
        user: {
          id: "async-lab-user",
          username: "async-user",
          display_name: "Reader",
          role: "lab",
          lab_id: "async-lab",
          lab_name: "Lab A",
        },
      },
    }),
  );
  await page.route("**/api/workspace", (route) =>
    route.fulfill({
      json: {
        ...workspace,
        providers: [
          {
            id: "sail",
            label: "Sail",
            model: "offline-async-model",
            available: true,
            output_limit: 8192,
          },
        ],
      },
    }),
  );
  await page.route("**/api/operations", (route) =>
    route.fulfill({ json: { active: active() } }),
  );
  await page.route(`**/api/specifications/${id}/operations`, (route) =>
    route.fulfill({
      json: {
        state: active().length ? "running" : "idle",
        active: active(),
        notice: null,
        recent_job: state.started && !state.expired ? job() : null,
      },
    }),
  );
  await page.route("**/api/operations/offline-async-job", async (route) => {
    state.reads++;
    if (state.dropNextPoll) {
      state.dropNextPoll = false;
      await route.abort("failed");
      return;
    }
    await route.fulfill({
      status: state.expired ? 404 : 200,
      json: state.expired ? { detail: "Operation unavailable." } : job(),
    });
  });
  await page.route(`**/api/specifications/${id}`, (route) =>
    route.fulfill({
      json: state.finished && state.kind === "extraction" ? updated : source,
    }),
  );
  const admit = async (route: import("@playwright/test").Route) => {
    state.posts++;
    state.started = true;
    expect(route.request().headers()["prefer"]).toBe("respond-async");
    state.requestId = route.request().headers()["x-inference-request-id"];
    expect(state.requestId).toMatch(/^[a-f0-9-]{36}$/);
    if (state.dropAck) {
      state.dropAck = false;
      await route.abort("failed");
      return;
    }
    await route.fulfill({
      status: 202,
      json: { ...job(), status_url: "/api/operations/offline-async-job" },
    });
  };
  await page.route(`**/api/specifications/${id}/analysis`, admit);
  await page.route(`**/api/specifications/${id}/matching`, async (route) => {
    if (route.request().method() === "POST") {
      state.kind = "matching";
      await admit(route);
      return;
    }
    await route.fulfill({
      json: {
        source_run_id: source.run!.run_id,
        report_input_fingerprint: comparison.report_input_fingerprint,
        current_run_id: state.finished ? "async-matching-result" : null,
        runs: [],
      },
    });
  });
  await page.route(`**/api/comparisons/${id}`, (route) =>
    route.fulfill({
      json: {
        ...comparison,
        comparison_id: state.finished
          ? "async-comparison-result"
          : comparison.comparison_id,
        matching_run_id: state.finished ? "async-matching-result" : null,
        comparison_engine: state.finished
          ? "ai_semantic_matching"
          : "deterministic_rules",
      },
    }),
  );
  return { id, state, source, updated };
}

async function start(page: Page, id: string) {
  await page.goto(`/specifications?document=${id}`);
  const button = page.getByRole("button", {
    name: "Analyze again",
    exact: true,
  });
  await expect(button).toBeEnabled();
  await button.evaluate((element: HTMLButtonElement) => {
    element.click();
    element.click();
  });
}

test("202 admission releases the POST while real progress stays pending across routes", async ({
  page,
  request,
}) => {
  const { id, state } = await fixture(page, request);
  await start(page, id);
  await expect.poll(() => state.posts).toBe(1);
  await expect(
    page.getByRole("button", { name: "Analyzing…", exact: true }),
  ).toBeDisabled();
  await expect(page.getByText(/AI steps completed 1\/6/).first()).toBeVisible();
  if ((page.viewportSize()?.width ?? 1440) < 1200)
    await page
      .getByRole("button", { name: "Open navigation", exact: true })
      .click();
  await page
    .getByRole("list", { name: "Workspace navigation", exact: true })
    .getByRole("link", { name: "Lab capability", exact: true })
    .click();
  await expect(
    page.getByRole("textbox", {
      name: "Search tests and methods",
      exact: true,
    }),
  ).toBeVisible();
  await page.getByRole("link", { name: "View progress", exact: true }).click();
  state.finished = true;
  await expect(
    page.getByRole("combobox", { name: "Selected test", exact: true }),
  ).toContainText("Completed:");
  await expect(
    page.getByRole("button", { name: "Analyzing…", exact: true }),
  ).toHaveCount(0);
  expect(state.posts).toBe(1);
});

for (const dropped of ["ack", "poll"] as const) {
  test(`lost ${dropped} recovers the exact saved operation without another POST or stale error`, async ({
    page,
    request,
  }) => {
    const { id, state } = await fixture(page, request);
    state.dropAck = dropped === "ack";
    state.dropNextPoll = dropped === "poll";
    await start(page, id);
    await expect.poll(() => state.posts).toBe(1);
    if (dropped === "poll")
      await expect.poll(() => state.reads).toBeGreaterThan(0);
    state.finished = true;
    await expect(
      page.getByRole("combobox", { name: "Selected test", exact: true }),
    ).toContainText("Completed:");
    await expect(
      page.getByText(
        /completion could not be confirmed|Refreshing saved data|analysis could not be completed/i,
      ),
    ).toHaveCount(0);
    expect(state.posts).toBe(1);
  });
}

test("a terminal failure is shown once and does not leak into another document", async ({
  page,
  request,
}) => {
  const { id, state } = await fixture(page, request);
  await start(page, id);
  await expect.poll(() => state.posts).toBe(1);
  state.failed = true;
  await expect(
    page.getByText(/Offline job failed validation/).first(),
  ).toBeVisible();
  await expect(
    page.getByRole("button", { name: "Analyzing…", exact: true }),
  ).toHaveCount(0);
  expect(state.posts).toBe(1);
  const otherId = "abcdef0123456789";
  const other = (await (
    await request.get(`/api/specifications/${id}`)
  ).json()) as SpecificationData;
  await page.route(`**/api/specifications/${otherId}`, (route) =>
    route.fulfill({
      json: {
        ...other,
        document_id: otherId,
        original_name: "Other lab document.pdf",
      },
    }),
  );
  await page.goto(`/specifications?document=${otherId}`);
  await expect(
    page.getByRole("combobox", { name: "Selected test", exact: true }),
  ).toBeVisible();
  await expect(page.getByText(/Offline job failed validation/)).toHaveCount(0);
  expect(state.posts).toBe(1);
});

test("an expired operation stays completion unknown rather than claiming success or retrying", async ({
  page,
  request,
}) => {
  const { id, state } = await fixture(page, request);
  await start(page, id);
  await expect.poll(() => state.posts).toBe(1);
  state.expired = true;
  await expect(
    page.getByText(/completion could not be confirmed/).first(),
  ).toBeVisible();
  expect(state.posts).toBe(1);
});

test("persistent observation loss releases local busy state with an unknown outcome and no retry", async ({
  page,
  request,
}) => {
  const { id, state } = await fixture(page, request);
  await page.addInitScript(() => {
    const original = window.setTimeout.bind(window);
    window.setTimeout = ((
      handler: TimerHandler,
      delay?: number,
      ...args: unknown[]
    ) =>
      original(
        handler,
        delay && delay >= 2000 && delay <= 10_000 ? 20 : delay,
        ...args,
      )) as typeof window.setTimeout;
  });
  await page.route("**/api/operations", (route) =>
    route.fulfill({ json: { active: [] } }),
  );
  await page.route("**/api/operations/offline-async-job", (route) =>
    route.abort("failed"),
  );
  await page.route(`**/api/specifications/${id}/operations`, (route) =>
    route.abort("failed"),
  );
  await page.route(`**/api/specifications/${id}`, (route) =>
    state.started ? route.abort("failed") : route.fallback(),
  );
  await start(page, id);
  await expect(
    page.getByText(/completion could not be confirmed/).first(),
  ).toBeVisible();
  await expect(
    page.getByRole("button", { name: "Analyzing…", exact: true }),
  ).toHaveCount(0);
  await expect(
    page.getByRole("button", { name: "Analyze again", exact: true }),
  ).toBeEnabled();
  expect(state.posts).toBe(1);
});

test("report generation reconciles a lost status read and loads the saved comparison", async ({
  page,
  request,
}) => {
  const { id, state } = await fixture(page, request);
  state.dropNextPoll = true;
  await page.goto(`/reports?document=${id}`);
  await page
    .getByRole("button", { name: "Generate report", exact: true })
    .click();
  const progress = page.getByRole("dialog", {
    name: "Generating your report",
    exact: true,
  });
  await expect(progress).toBeVisible();
  await expect.poll(() => state.reads).toBeGreaterThan(0);
  state.finished = true;
  await expect(progress).not.toBeVisible();
  await expect(
    page.getByRole("button", { name: "Check for updates", exact: true }),
  ).toBeVisible();
  await expect(
    page.getByRole("dialog", {
      name: "Report needs another look",
      exact: true,
    }),
  ).toHaveCount(0);
  expect(state.posts).toBe(1);
});

test("changing the authenticated session stops observation and cannot paint the old lab result", async ({
  page,
  request,
}) => {
  const { id, state } = await fixture(page, request);
  await start(page, id);
  await expect.poll(() => state.posts).toBe(1);
  await page.evaluate(() =>
    window.dispatchEvent(new Event("scione:session-expired")),
  );
  state.finished = true;
  await expect(page.getByRole("textbox", { name: /username/i })).toBeVisible();
  await expect(page.getByText(/Completed:/)).toHaveCount(0);
  expect(state.posts).toBe(1);
});

test("a later authoritative saved matching run clears an earlier unknown-outcome dialog", async ({
  page,
  request,
}) => {
  const { id, state } = await fixture(page, request);
  let disconnected = false;
  await page.addInitScript(() => {
    const original = window.setTimeout.bind(window);
    window.setTimeout = ((
      handler: TimerHandler,
      delay?: number,
      ...args: unknown[]
    ) =>
      original(
        handler,
        delay && delay >= 2000 && delay <= 10_000 ? 20 : delay,
        ...args,
      )) as typeof window.setTimeout;
  });
  await page.route("**/api/operations", (route) =>
    disconnected ? route.fulfill({ json: { active: [] } }) : route.fallback(),
  );
  await page.route("**/api/operations/offline-async-job", (route) =>
    disconnected ? route.abort("failed") : route.fallback(),
  );
  await page.route(`**/api/specifications/${id}/operations`, (route) =>
    disconnected ? route.abort("failed") : route.fallback(),
  );
  await page.route(`**/api/specifications/${id}/matching`, (route) =>
    disconnected && route.request().method() === "GET"
      ? route.abort("failed")
      : route.fallback(),
  );
  await page.goto(`/reports?document=${id}`);
  await page
    .getByRole("button", { name: "Generate report", exact: true })
    .click();
  await expect.poll(() => state.posts).toBe(1);
  disconnected = true;
  const failed = page.getByRole("dialog", {
    name: "Report needs another look",
    exact: true,
  });
  await expect(
    failed.getByText(/completion could not be confirmed/),
  ).toBeVisible();
  disconnected = false;
  state.finished = true;
  await page.evaluate(
    (documentId) =>
      window.dispatchEvent(
        new CustomEvent("scione:inference-completed", {
          detail: { documentId },
        }),
      ),
    id,
  );
  await expect(failed).not.toBeVisible();
  await expect(
    page.getByRole("button", { name: "Check for updates", exact: true }),
  ).toBeVisible();
  expect(state.posts).toBe(1);
});

test("an unrelated newer saved source does not prove a dropped admission completed", async ({
  page,
  request,
}) => {
  const { id, state, source, updated } = await fixture(page, request);
  state.dropAck = true;
  await page.route("**/api/operations", (route) =>
    route.fulfill({ json: { active: [] } }),
  );
  await page.route(`**/api/specifications/${id}/operations`, (route) =>
    route.fulfill({
      json: { state: "idle", active: [], notice: null, recent_job: null },
    }),
  );
  await page.route(`**/api/specifications/${id}`, (route) =>
    route.fulfill({ json: state.started ? updated : source }),
  );
  await start(page, id);
  await expect(
    page.getByText(/completion could not be confirmed/).first(),
  ).toBeVisible();
  await expect(
    page.getByRole("combobox", { name: "Selected test", exact: true }),
  ).toContainText("Completed:");
  await expect(
    page.getByRole("button", { name: "Analyzing…", exact: true }),
  ).toHaveCount(0);
  expect(state.posts).toBe(1);
});

test("a completed report with a hung saved-result GET releases busy state without another POST", async ({
  page,
  request,
}) => {
  const { id, state } = await fixture(page, request);
  await page.addInitScript(() => {
    const original = window.setTimeout.bind(window);
    window.setTimeout = ((
      handler: TimerHandler,
      delay?: number,
      ...args: unknown[]
    ) =>
      original(
        handler,
        delay === 20_000 &&
          sessionStorage.getItem("offline-short-json-deadline")
          ? 250
          : delay,
        ...args,
      )) as typeof window.setTimeout;
  });
  let release!: () => void;
  const held = new Promise<void>((resolve) => {
    release = resolve;
  });
  await page.route(`**/api/comparisons/${id}`, async (route) => {
    if (state.finished) await held;
    await route.fallback().catch(() => {});
  });
  try {
    await page.goto(`/reports?document=${id}`);
    await page
      .getByRole("button", { name: "Generate report", exact: true })
      .click();
    await expect.poll(() => state.posts).toBe(1);
    await page.evaluate(() =>
      sessionStorage.setItem("offline-short-json-deadline", "true"),
    );
    state.finished = true;
    const failed = page.getByRole("dialog", {
      name: "Report needs another look",
      exact: true,
    });
    await expect(failed.getByRole("alert")).toContainText(
      "The comparison is saved, but its results could not be loaded.",
    );
    await expect(
      failed.getByRole("button", { name: "Try again", exact: true }),
    ).toBeEnabled();
    await expect(
      page.getByRole("button", { name: "Updating report…", exact: true }),
    ).toHaveCount(0);
    expect(state.posts).toBe(1);
  } finally {
    release();
  }
});

test("a previously observed running task becomes visibly unknown during an outage and is checked before another admission", async ({
  page,
  request,
}) => {
  const { id, state } = await fixture(page, request);
  let disconnected = false;
  let observedRunning = false;
  await page.addInitScript(() => {
    const original = window.setTimeout.bind(window);
    window.setTimeout = ((
      handler: TimerHandler,
      delay?: number,
      ...args: unknown[]
    ) =>
      original(
        handler,
        delay && delay >= 2000 && delay <= 10_000 ? 20 : delay,
        ...args,
      )) as typeof window.setTimeout;
  });
  await page.route("**/api/operations", async (route) => {
    if (disconnected) {
      await route.abort("failed");
      return;
    }
    if (state.started && !state.finished) observedRunning = true;
    await route.fallback();
  });
  for (const suffix of [
    `/api/operations/offline-async-job`,
    `/api/specifications/${id}/operations`,
    `/api/specifications/${id}/matching`,
  ])
    await page.route(`**${suffix}`, (route) =>
      disconnected && route.request().method() === "GET"
        ? route.abort("failed")
        : route.fallback(),
    );
  await page.goto(`/reports?document=${id}`);
  await page
    .getByRole("button", { name: "Generate report", exact: true })
    .click();
  await expect.poll(() => observedRunning).toBe(true);
  disconnected = true;
  const failed = page.getByRole("dialog", {
    name: "Report needs another look",
    exact: true,
  });
  await expect(failed.getByRole("alert")).toContainText(
    "completion could not be confirmed",
  );
  await expect(
    page.getByText("Analysis status unavailable", { exact: false }),
  ).toBeVisible();
  await expect(
    page.getByRole("button", { name: "Generating…", exact: true }),
  ).toHaveCount(0);
  expect(state.posts).toBe(1);
  await failed.getByRole("button", { name: "Try again", exact: true }).click();
  await expect(failed.getByRole("alert")).toContainText(
    "completion could not be confirmed",
  );
  expect(state.posts).toBe(1);
  disconnected = false;
  state.finished = true;
  await failed.getByRole("button", { name: "Try again", exact: true }).click();
  await expect(failed).not.toBeVisible();
  await expect(
    page.getByRole("button", { name: "Check for updates", exact: true }),
  ).toBeVisible();
  expect(state.posts).toBe(1);
});

test("a matching failure while away is visible on return and a newer saved success supersedes it", async ({
  page,
  request,
}) => {
  const { id, state } = await fixture(page, request);
  let laterSuccess = false;
  const failedAt = new Date(Date.now() - 10_000).toISOString();
  await page.route(`**/api/specifications/${id}/operations`, (route) =>
    route.fulfill({
      json: {
        state:
          state.started && !state.failed && !state.finished
            ? "running"
            : "idle",
        active: [],
        notice: null,
        recent_job: state.failed
          ? {
              operation_id: "offline-async-job",
              document_id: id,
              kind: "matching",
              state: "failed",
              started_at: failedAt,
              request_id: state.requestId,
              error: {
                status: 400,
                detail: "Do not show private provider diagnostic text.",
              },
            }
          : null,
        latest: laterSuccess
          ? {
              kind: "matching",
              status: "success",
              run_id: "async-matching-result",
              started_at: new Date().toISOString(),
            }
          : null,
      },
    }),
  );
  await page.goto(`/reports?document=${id}`);
  await page
    .getByRole("button", { name: "Generate report", exact: true })
    .click();
  await expect.poll(() => state.posts).toBe(1);
  await page
    .getByRole("dialog", { name: "Generating your report", exact: true })
    .getByRole("button", { name: "View saved report", exact: true })
    .click();
  if ((page.viewportSize()?.width ?? 1440) < 1200)
    await page
      .getByRole("button", { name: "Open navigation", exact: true })
      .click();
  await page
    .getByRole("list", { name: "Workspace navigation", exact: true })
    .getByRole("link", { name: "Lab capability", exact: true })
    .click();
  state.failed = true;
  await expect(
    page.getByText("You can browse other pages while this finishes."),
  ).toHaveCount(0);
  await page.goto(`/reports?document=${id}`);
  const notice = page.getByText(
    "The latest comparison attempt did not finish. Showing the saved report. No automatic retry was made.",
    { exact: true },
  );
  await expect(notice).toBeVisible();
  await expect(
    page.getByRole("navigation", { name: "Requested tests" }),
  ).toBeVisible();
  await expect(page.getByText(/private provider diagnostic/)).toHaveCount(0);
  state.finished = true;
  laterSuccess = true;
  await page.evaluate(
    (documentId) =>
      window.dispatchEvent(
        new CustomEvent("scione:inference-completed", {
          detail: { documentId },
        }),
      ),
    id,
  );
  await expect(notice).not.toBeVisible();
  await expect(
    page.getByRole("button", { name: "Check for updates", exact: true }),
  ).toBeVisible();
  expect(state.posts).toBe(1);
});
