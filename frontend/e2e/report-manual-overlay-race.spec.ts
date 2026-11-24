import {
  expect,
  test,
  type APIRequestContext,
  type Page,
} from "@playwright/test";
import type {
  ComparisonData,
  ComparisonRequirement,
} from "../src/comparisonTypes";
import {
  manualReviewIsBehind,
  sameManualAssessment,
} from "../src/manualReviewVersions";

test.use({ actionTimeout: 15_000 });

async function prepare(request: APIRequestContext) {
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
  const { document_id: id } = await (
    await request.post("/api/specifications/worked-example")
  ).json();
  const path = `/api/comparisons/${id}`;
  const report = (await (await request.get(path)).json()) as ComparisonData;
  const target = report.requirements.find(
    (item) => item.comparison.status !== "excluded",
  )!;
  return { id, path, report, target };
}

async function select(page: Page, target: ComparisonRequirement) {
  await page
    .getByRole("navigation", { name: "Requested tests", exact: true })
    .getByRole("button", { name: target.test_name, exact: true })
    .click();
}

test("a completed review follows a test switch without moving the current selection or offering a stale accept", async ({
  page,
  request,
}) => {
  const { id, path, report, target } = await prepare(request);
  const other = report.requirements.find(
    (item) =>
      item.requirement_id !== target.requirement_id &&
      item.comparison.status !== "excluded",
  )!;
  expect(other).toBeTruthy();
  let release = () => {};
  const gate = new Promise<void>((resolve) => {
    release = resolve;
  });
  let committed = false;
  let writes = 0;
  let modelCalls = 0;
  await page.route(`**${path}/reviews`, async (route) => {
    if (route.request().method() !== "POST") return route.continue();
    writes++;
    const result = await route.fetch();
    expect(result.ok()).toBeTruthy();
    committed = true;
    await gate;
    await route.fulfill({ response: result });
  });
  page.on("request", (sent) => {
    if (
      sent.method() === "POST" &&
      /\/(?:matching|analysis)$/.test(new URL(sent.url()).pathname)
    )
      modelCalls++;
  });
  await page.goto(`/reports?document=${id}`);
  await select(page, target);
  await page
    .getByRole("button", { name: "Accept assessment", exact: true })
    .click();
  try {
    await expect.poll(() => committed).toBe(true);
    await select(page, other);
    await expect(
      page
        .getByTestId("report-detail-pane")
        .getByRole("heading", { name: other.test_name, exact: true }),
    ).toBeVisible();
  } finally {
    release();
  }
  const targetRow = page
    .getByRole("navigation", { name: "Requested tests", exact: true })
    .getByRole("button", { name: target.test_name, exact: true });
  await expect(targetRow.getByText("Reviewed", { exact: true })).toBeVisible();
  await expect(
    page
      .getByTestId("report-detail-pane")
      .getByRole("heading", { name: other.test_name, exact: true }),
  ).toBeVisible();
  await expect(
    page.getByRole("button", { name: "Accept assessment", exact: true }),
  ).toBeVisible();
  await targetRow.click();
  await expect(
    page.getByText("Assessment reviewed", { exact: true }),
  ).toBeVisible();
  await expect(
    page.getByRole("button", { name: "Accept assessment", exact: true }),
  ).toHaveCount(0);
  const saved = (await (await request.get(path)).json()) as ComparisonData;
  expect(saved.assessment_reviews![target.requirement_id].version).toBe(1);
  expect(saved.requirements).toEqual(report.requirements);
  expect(writes).toBe(1);
  expect(modelCalls).toBe(0);
});

test("a completed review from the previous specification is ignored after selecting another document", async ({
  page,
  request,
}) => {
  const { id, path, report, target } = await prepare(request);
  const otherId = "1122334455667788";
  const other = structuredClone(report);
  other.document = {
    ...other.document,
    document_id: otherId,
    original_name: "other-review-specification.pdf",
    title: "Other customer specification",
    specification_id: "CSP-OTHER",
    run_id: "other-source-run",
  };
  other.comparison_id = "other-review-assessment";
  other.source_run_id = "other-source-run";
  other.report_input_fingerprint = "other-review-inputs";
  other.requirements = [
    {
      ...target,
      requirement_id: "req_other_document",
      test_name: "Other customer review fixture",
    },
  ];
  other.assessment_reviews = {};
  other.customer_clarifications = {};
  delete other.document_notes_review;
  delete other.export_snapshot_id;
  await page.route("**/api/workspace", async (route) => {
    const result = await route.fetch();
    const workspace = await result.json();
    await route.fulfill({
      json: {
        ...workspace,
        documents: [
          ...workspace.documents,
          {
            document_id: otherId,
            original_name: other.document.original_name,
            latest_run_id: other.source_run_id,
          },
        ],
      },
    });
  });
  await page.route("**/api/library", async (route) => {
    const result = await route.fetch();
    const library = await result.json();
    await route.fulfill({
      json: { ...library, documents: [...library.documents, other.document] },
    });
  });
  await page.route(`**/api/comparisons/${otherId}`, (route) =>
    route.fulfill({ json: other }),
  );
  await page.route(`**/api/specifications/${otherId}/operations`, (route) =>
    route.fulfill({ json: { recent_job: null, active: [] } }),
  );
  let release = () => {};
  const gate = new Promise<void>((resolve) => {
    release = resolve;
  });
  let committed = false;
  let returned = false;
  let writes = 0;
  await page.route(`**${path}/reviews`, async (route) => {
    if (route.request().method() !== "POST") return route.continue();
    writes++;
    const result = await route.fetch();
    expect(result.ok()).toBeTruthy();
    committed = true;
    await gate;
    await route.fulfill({ response: result });
    returned = true;
  });
  await page.goto(`/reports?document=${id}`);
  await select(page, target);
  await page
    .getByRole("button", { name: "Accept assessment", exact: true })
    .click();
  try {
    await expect.poll(() => committed).toBe(true);
    await page
      .getByRole("combobox", { name: "Customer specification", exact: true })
      .click();
    await page.getByRole("option").filter({ hasText: "CSP-OTHER" }).click();
    await expect(page).toHaveURL(new RegExp(`document=${otherId}`));
    await expect(
      page.getByTestId("report-detail-pane").getByRole("heading", {
        name: other.requirements[0].test_name,
        exact: true,
      }),
    ).toBeVisible();
  } finally {
    release();
  }
  await expect.poll(() => returned).toBe(true);
  await page.evaluate(
    () =>
      new Promise<void>((resolve) =>
        requestAnimationFrame(() => requestAnimationFrame(() => resolve())),
      ),
  );
  await expect(
    page.getByTestId("report-detail-pane").getByRole("heading", {
      name: other.requirements[0].test_name,
      exact: true,
    }),
  ).toBeVisible();
  await expect(
    page.getByText("Assessment reviewed", { exact: true }),
  ).toHaveCount(0);
  await expect(
    page.getByRole("button", { name: "Accept assessment", exact: true }),
  ).toBeVisible();
  expect(
    (await (await request.get(path)).json()).assessment_reviews[
      target.requirement_id
    ].version,
  ).toBe(1);
  expect(writes).toBe(1);
});

for (const failRead of [false, true]) {
  test(`a late acceptance cannot replace a newer clarification${failRead ? " even when the fresh read fails" : " or its immutable export"}`, async ({
    page,
    request,
  }) => {
    const { id, path, target } = await prepare(request);
    let release = () => {};
    const gate = new Promise<void>((resolve) => {
      release = resolve;
    });
    let committed = false;
    let released = false;
    let reviewWrites = 0;
    let clarificationWrites = 0;
    let rereads = 0;
    let modelCalls = 0;
    let snapshotRead = "";
    await page.route(`**${path}/reviews`, async (route) => {
      if (route.request().method() !== "POST") return route.continue();
      reviewWrites++;
      const saved = await route.fetch();
      committed = true;
      await gate;
      await route.fulfill({ response: saved });
    });
    await page.route(`**${path}`, async (route) => {
      if (released) {
        rereads++;
        if (failRead)
          return route.fulfill({
            status: 503,
            json: {
              detail: "Offline fixture: saved view temporarily unavailable.",
            },
          });
      }
      await route.continue();
    });
    page.on("request", (sent) => {
      const snapshot = new URL(sent.url()).pathname.match(
        /^\/api\/comparison-snapshots\/([^/]+)\/json$/,
      );
      if (snapshot) snapshotRead = snapshot[1];
      if (
        sent.method() === "POST" &&
        new URL(sent.url()).pathname === `${path}/clarifications`
      )
        clarificationWrites++;
      if (
        sent.method() === "POST" &&
        /\/(?:matching|analysis)$/.test(new URL(sent.url()).pathname)
      )
        modelCalls++;
    });
    await page.goto(`/reports?document=${id}`);
    await select(page, target);
    await page
      .getByRole("button", { name: "Accept assessment", exact: true })
      .click();
    await expect.poll(() => committed).toBe(true);
    await page
      .getByRole("button", { name: "Customer clarifications", exact: true })
      .click();
    const conversation = page.getByRole("dialog", {
      name: "Customer clarifications",
      exact: true,
    });
    await conversation
      .getByRole("button", { name: "Record question", exact: true })
      .click();
    const form = conversation.getByRole("form", {
      name: "Record question",
      exact: true,
    });
    await form
      .getByLabel("Question", { exact: false })
      .fill(
        "Keep this later customer question visible after the acceptance returns.",
      );
    await form
      .getByRole("button", { name: "Save question", exact: true })
      .click();
    await expect(form).not.toBeVisible();
    await expect(
      conversation.getByText(
        "Keep this later customer question visible after the acceptance returns.",
        { exact: true },
      ),
    ).toBeVisible();
    const latest = (await (await request.get(path)).json()) as ComparisonData;
    expect(latest.assessment_reviews![target.requirement_id].version).toBe(1);
    expect(latest.customer_clarifications![target.requirement_id].version).toBe(
      1,
    );
    released = true;
    release();
    await expect.poll(() => rereads).toBe(1);
    if (failRead) {
      await conversation
        .getByRole("button", { name: "Close", exact: true })
        .click();
      const notice = page
        .getByRole("alert")
        .filter({ hasText: "latest combined review could not be loaded" });
      await expect(notice).toContainText("Your saved records have been kept");
      await page
        .getByRole("button", { name: "Customer clarifications", exact: true })
        .click();
      await expect(
        conversation.getByText(
          "Keep this later customer question visible after the acceptance returns.",
          { exact: true },
        ),
      ).toBeVisible();
    }
    await expect(
      conversation.getByText(
        "Keep this later customer question visible after the acceptance returns.",
        { exact: true },
      ),
    ).toBeVisible();
    await conversation
      .getByRole("button", { name: "Close", exact: true })
      .click();
    await expect(
      page.getByText("Assessment reviewed", { exact: true }),
    ).toBeVisible();
    await page
      .getByRole("button", { name: "Report options", exact: true })
      .click();
    await page
      .getByRole("menuitem", { name: "Review summary", exact: true })
      .click();
    const summary = page.getByRole("dialog", {
      name: "Review summary",
      exact: true,
    });
    const row = summary
      .getByRole("row")
      .filter({ has: page.getByText(target.test_name, { exact: true }) });
    await expect(row).toContainText("Reviewed");
    await expect(row).toContainText("1 question awaiting recorded reply");
    await expect.poll(() => snapshotRead).toBe(latest.export_snapshot_id);
    expect(reviewWrites).toBe(1);
    expect(clarificationWrites).toBe(1);
    expect(rereads).toBe(1);
    expect(modelCalls).toBe(0);
  });
}

test("an incomparable full save response uses the authoritative snapshot instead of merging exports", async ({
  page,
  request,
}) => {
  const { id, path, report, target } = await prepare(request);
  const firstReview = await request.post(`${path}/reviews`, {
    data: {
      comparison_id: report.comparison_id,
      input_fingerprint: report.report_input_fingerprint,
      requirement_id: target.requirement_id,
      expected_version: 0,
      review_status: "pending",
      decision: "keep",
      note: "Initial review.",
    },
  });
  expect(firstReview.ok()).toBeTruthy();
  const question = await request.post(`${path}/clarifications`, {
    data: {
      comparison_id: report.comparison_id,
      input_fingerprint: report.report_input_fingerprint,
      requirement_id: target.requirement_id,
      expected_version: 0,
      client_request_id: crypto.randomUUID(),
      event_kind: "question",
      text: "The saved conversation must survive an incomplete transport response.",
    },
  });
  expect(question.ok()).toBeTruthy();
  let authoritative: ComparisonData | null = null;
  let writes = 0;
  let rereads = 0;
  let snapshotRead = "";
  page.on("request", (sent) => {
    const snapshot = new URL(sent.url()).pathname.match(
      /^\/api\/comparison-snapshots\/([^/]+)\/json$/,
    );
    if (snapshot) snapshotRead = snapshot[1];
  });
  await page.route(`**${path}/reviews`, async (route) => {
    if (route.request().method() !== "POST") return route.continue();
    writes++;
    const result = await route.fetch();
    authoritative = (await result.json()) as ComparisonData;
    const incomplete = structuredClone(authoritative);
    // Simulate a full response captured with the newer review but an older
    // conversation overlay. Its export must never be spliced into current UI.
    incomplete.customer_clarifications = {};
    incomplete.export_snapshot_id = "do-not-publish-this-incomparable-export";
    await route.fulfill({ json: incomplete });
  });
  await page.route(`**${path}`, async (route) => {
    if (authoritative) rereads++;
    await route.continue();
  });
  await page.goto(`/reports?document=${id}`);
  await select(page, target);
  await page
    .getByRole("button", { name: "Accept assessment", exact: true })
    .click();
  await expect.poll(() => rereads).toBe(1);
  await expect(
    page.getByText("Assessment reviewed", { exact: true }),
  ).toBeVisible();
  await page
    .getByRole("button", { name: "Customer clarifications", exact: true })
    .click();
  const conversation = page.getByRole("dialog", {
    name: "Customer clarifications",
    exact: true,
  });
  await expect(
    conversation.getByText(
      "The saved conversation must survive an incomplete transport response.",
      { exact: true },
    ),
  ).toBeVisible();
  await conversation
    .getByRole("button", { name: "Close", exact: true })
    .click();
  await page
    .getByRole("button", { name: "Report options", exact: true })
    .click();
  await page
    .getByRole("menuitem", { name: "Review summary", exact: true })
    .click();
  const summary = page.getByRole("dialog", {
    name: "Review summary",
    exact: true,
  });
  await expect(
    summary.getByRole("table", { name: "Saved review summary", exact: true }),
  ).toBeVisible();
  await expect.poll(() => snapshotRead).toBe(authoritative!.export_snapshot_id);
  await expect(summary).not.toContainText("do-not-publish");
  expect(
    authoritative!.assessment_reviews![target.requirement_id].version,
  ).toBe(2);
  expect(
    authoritative!.customer_clarifications![target.requirement_id].version,
  ).toBe(1);
  expect(writes).toBe(1);
  expect(rereads).toBe(1);
});

test("overlay versions compare all independent records and never use dates or export IDs as ordering", async ({
  request,
}) => {
  const { report, target } = await prepare(request);
  const current = structuredClone(report);
  const review = {
    review_id: "review-a",
    comparison_id: report.comparison_id,
    requirement_id: target.requirement_id,
    version: 1,
    review_status: "accepted" as const,
    decision: "keep" as const,
    note: "",
    actor: "Reviewer",
    created_at: "2099-01-01T00:00:00Z",
    machine_status: target.comparison.status,
    input_fingerprint: report.report_input_fingerprint!,
  };
  current.assessment_reviews = { [target.requirement_id]: review };
  current.customer_clarifications = {
    [target.requirement_id]: { version: 1, events: [] },
  };
  const incomparable = structuredClone(current);
  incomparable.assessment_reviews![target.requirement_id].version = 2;
  incomparable.customer_clarifications![target.requirement_id].version = 0;
  incomparable.created_at = "9999-01-01T00:00:00Z";
  expect(sameManualAssessment(current, incomparable)).toBe(true);
  expect(manualReviewIsBehind(current, incomparable)).toBe(true);
  expect(manualReviewIsBehind(incomparable, current)).toBe(true);
  const newest = structuredClone(current);
  newest.assessment_reviews![target.requirement_id].version = 2;
  expect(manualReviewIsBehind(current, newest)).toBe(false);
  current.document_notes_review = {
    source_fingerprint: "notes-a",
    version: 3,
    notes: [],
    pending_count: 0,
    reviewed_count: 0,
  };
  newest.document_notes_review = {
    ...current.document_notes_review,
    version: 2,
  };
  expect(manualReviewIsBehind(current, newest)).toBe(true);
  newest.document_notes_review.version = 3;
  expect(manualReviewIsBehind(current, newest)).toBe(false);
  const foreign = structuredClone(newest);
  foreign.source_run_id = "other-source";
  expect(sameManualAssessment(current, foreign)).toBe(false);
});
