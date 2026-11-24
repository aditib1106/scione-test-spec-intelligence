import {
  expect,
  test,
  type APIRequestContext,
  type Page,
} from "@playwright/test";
import type {
  AssessmentReview,
  ComparisonData,
  ComparisonRequirement,
} from "../src/comparisonTypes";
import type { Workspace } from "../src/types";

test.use({ actionTimeout: 15_000 });

async function prepareHistory(request: APIRequestContext) {
  expect(
    (
      await request.post("/api/workspace/reset", {
        data: { acknowledged: true },
      })
    ).ok(),
  ).toBeTruthy();
  const lab = await (await request.get("/api/lab")).json();
  expect(
    (
      await request.post("/api/lab/example", {
        data: { version: lab.version, example: "csp301" },
      })
    ).ok(),
  ).toBeTruthy();
  const imported = await (
    await request.post("/api/specifications/worked-example")
  ).json();
  const id = imported.document_id as string;
  const path = `/api/comparisons/${id}`;
  const initial = (await (await request.get(path)).json()) as ComparisonData;
  const target = initial.requirements.find((item) =>
    item.comparison.checks.some((check) => check.status === "unknown"),
  )!;
  expect(target).toBeTruthy();
  for (const [version, decision, note] of [
    [0, "keep", "Initial review: confirm the output stage."],
    [1, "clarify_customer", "Customer clarification is still needed."],
  ] as const) {
    const saved = await request.post(`${path}/reviews`, {
      data: {
        comparison_id: initial.comparison_id,
        input_fingerprint: initial.report_input_fingerprint,
        requirement_id: target.requirement_id,
        expected_version: version,
        review_status: "pending",
        decision,
        note,
      },
    });
    expect(saved.ok()).toBeTruthy();
  }
  const report = (await (await request.get(path)).json()) as ComparisonData;
  const history = (await (await request.get(`${path}/reviews`)).json()) as {
    document_id: string;
    comparison_id: string;
    events: AssessmentReview[];
  };
  expect(history.events).toHaveLength(2);
  return { id, path, initial, report, target, history };
}

async function selectTest(page: Page, target: ComparisonRequirement) {
  await page
    .getByRole("navigation", { name: "Requested tests" })
    .getByRole("button", { name: target.test_name, exact: true })
    .click();
}

test("saved review history is loaded on demand and explains who decided what without changing machine results", async ({
  page,
  request,
}, testInfo) => {
  const { id, path, initial, target, history } = await prepareHistory(request);
  let reads = 0;
  let writes = 0;
  page.on("request", (sent) => {
    if (
      new URL(sent.url()).pathname === `${path}/reviews` &&
      sent.method() === "GET"
    )
      reads++;
    if (!["GET", "HEAD", "OPTIONS"].includes(sent.method())) writes++;
  });
  await page.goto(`/reports?document=${id}`);
  await selectTest(page, target);
  const trigger = page.getByRole("button", {
    name: "Review history",
    exact: true,
  });
  await expect(trigger).toBeVisible();
  expect(reads).toBe(0);
  await trigger.click();
  const panel = page.getByRole("region", {
    name: "Review history details",
    exact: true,
  });
  const dialog = page.getByRole("dialog", {
    name: "Review history",
    exact: true,
  });
  await expect(dialog).toBeVisible();
  await expect(
    panel.getByText("Customer clarification is still needed.", { exact: true }),
  ).toBeVisible();
  await expect(
    panel.getByText("Initial review: confirm the output stage.", {
      exact: true,
    }),
  ).toBeVisible();
  await expect(
    panel.getByText("Ask the customer to clarify", { exact: false }),
  ).toBeVisible();
  await expect(dialog.getByText(target.test_name, { exact: true })).toHaveCount(
    1,
  );
  await expect(panel.getByRole("listitem")).toHaveCount(2);
  await expect(panel.getByRole("listitem").first()).toContainText(
    "Customer clarification is still needed.",
  );
  await expect(panel.locator("time").first()).toHaveAttribute(
    "datetime",
    history.events[1].created_at,
  );
  if (history.events[1].actor)
    await expect(
      panel.getByTitle(history.events[1].actor, { exact: true }).first(),
    ).toBeVisible();
  await expect(
    dialog.getByRole("link", { name: "Download review history", exact: true }),
  ).toHaveAttribute("href", `${path}/reviews?download=true`);
  expect(reads).toBe(1);
  expect(writes).toBe(0);
  const after = (await (await request.get(path)).json()) as ComparisonData;
  expect(after.requirements).toEqual(initial.requirements);
  expect(after.summary).toEqual(initial.summary);
  expect((await (await request.get(`${path}/reviews`)).json()).events).toEqual(
    history.events,
  );
  if (testInfo.project.name === "desktop") {
    await panel.scrollIntoViewIfNeeded();
    await expect(panel).toBeInViewport({ ratio: 1 });
    await panel.getByRole("list").evaluate((element) => {
      element.scrollTop = 0;
    });
    await expect(
      panel.getByText("Customer clarification is still needed.", {
        exact: true,
      }),
    ).toBeInViewport();
    await expect(
      panel.getByText("Initial review: confirm the output stage.", {
        exact: true,
      }),
    ).toBeInViewport();
    await panel.screenshot({
      path: testInfo.outputPath("saved-review-history.png"),
      animations: "disabled",
    });
    await page
      .getByRole("dialog", { name: "Review history", exact: true })
      .screenshot({
        path: testInfo.outputPath("review-history-dialog.png"),
        animations: "disabled",
      });
  }
});

test("review history retries only the read and rejects an obsolete assessment response", async ({
  page,
  request,
}) => {
  const { id, path, target, history } = await prepareHistory(request);
  let reads = 0;
  let writes = 0;
  await page.route(`**${path}/reviews`, (route) => {
    expect(route.request().method()).toBe("GET");
    reads++;
    if (reads === 1)
      return route.fulfill({
        status: 503,
        json: { detail: "Review history is temporarily unavailable." },
      });
    if (reads === 2)
      return route.fulfill({
        json: {
          ...history,
          comparison_id: "obsolete-assessment",
          events: [
            {
              ...history.events[0],
              note: "Do not display an older assessment note.",
            },
          ],
        },
      });
    return route.fulfill({ json: history });
  });
  page.on("request", (sent) => {
    if (!["GET", "HEAD", "OPTIONS"].includes(sent.method())) writes++;
  });
  await page.goto(`/reports?document=${id}`);
  await selectTest(page, target);
  await page
    .getByRole("button", { name: "Review history", exact: true })
    .click();
  const panel = page.getByRole("region", {
    name: "Review history details",
    exact: true,
  });
  await expect(panel.getByRole("alert")).toContainText(
    "Review history is temporarily unavailable.",
  );
  await panel.getByRole("button", { name: "Retry", exact: true }).click();
  await expect(panel.getByRole("alert")).toContainText(
    "The saved assessment changed",
  );
  await expect(
    page.getByText("Do not display an older assessment note.", { exact: true }),
  ).toHaveCount(0);
  await panel.getByRole("button", { name: "Retry", exact: true }).click();
  await expect(
    panel.getByText("Customer clarification is still needed.", { exact: true }),
  ).toBeVisible();
  expect(reads).toBe(3);
  expect(writes).toBe(0);
});

test("a delayed review-history response cannot appear after switching specifications", async ({
  page,
  request,
}) => {
  const { id, path, report, target, history } = await prepareHistory(request);
  const workspace = (await (
    await request.get("/api/workspace")
  ).json()) as Workspace;
  const otherId = "review-history-second-source";
  const other = structuredClone(report);
  other.comparison_id = "review-history-second-assessment";
  other.document.document_id = otherId;
  other.document.original_name = "second-history-specification.pdf";
  other.document.title = "Second history specification";
  other.assessment_reviews = {};
  const otherEvent: AssessmentReview = {
    ...history.events[0],
    review_id: "second-source-review",
    comparison_id: other.comparison_id,
    note: "Only the second specification's saved decision.",
  };
  await page.route("**/api/workspace", (route) =>
    route.fulfill({
      json: {
        ...workspace,
        documents: [
          ...workspace.documents,
          {
            ...workspace.documents.find((item) => item.document_id === id)!,
            document_id: otherId,
            original_name: other.document.original_name,
          },
        ],
      },
    }),
  );
  await page.route(`**/api/comparisons/${otherId}`, (route) =>
    route.fulfill({ json: other }),
  );
  await page.route(`**/api/comparisons/${otherId}/reviews`, (route) =>
    route.fulfill({
      json: {
        document_id: otherId,
        comparison_id: other.comparison_id,
        events: [otherEvent],
      },
    }),
  );
  let release!: () => void;
  let started!: () => void;
  const held = new Promise<void>((resolve) => {
    release = resolve;
  });
  const requested = new Promise<void>((resolve) => {
    started = resolve;
  });
  await page.route(`**${path}/reviews`, async (route) => {
    started();
    await held;
    await route.fulfill({ json: history }).catch(() => {});
  });
  let writes = 0;
  page.on("request", (sent) => {
    if (!["GET", "HEAD", "OPTIONS"].includes(sent.method())) writes++;
  });
  try {
    await page.goto(`/reports?document=${id}`);
    await selectTest(page, target);
    await page
      .getByRole("button", { name: "Review history", exact: true })
      .click();
    await requested;
    await expect(page.getByRole("status")).toContainText(
      "Loading review history",
    );
    await page
      .getByRole("button", { name: "Close review history", exact: true })
      .click();
    const picker = page.getByRole("combobox", {
      name: "Customer specification",
      exact: true,
    });
    await picker.fill("second-history");
    await page.getByRole("option").first().click();
    await expect(page).toHaveURL(new RegExp(`document=${otherId}`));
    await selectTest(page, target);
    await expect(
      page.getByRole("button", { name: "Review history", exact: true }),
    ).toHaveAttribute("aria-expanded", "false");
    release();
    await page
      .getByRole("button", { name: "Review history", exact: true })
      .click();
    const panel = page.getByRole("region", {
      name: "Review history details",
      exact: true,
    });
    await expect(
      panel.getByText(otherEvent.note, { exact: true }),
    ).toBeVisible();
    await expect(
      page.getByText("Initial review: confirm the output stage.", {
        exact: true,
      }),
    ).toHaveCount(0);
    await expect(
      page.getByText("Customer clarification is still needed.", {
        exact: true,
      }),
    ).toHaveCount(0);
    expect(writes).toBe(0);
  } finally {
    release();
  }
});
