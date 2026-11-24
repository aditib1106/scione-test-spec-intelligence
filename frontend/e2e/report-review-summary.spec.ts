import { randomUUID } from "node:crypto";
import {
  expect,
  test,
  type APIRequestContext,
  type Page,
} from "@playwright/test";
import type { ComparisonData } from "../src/comparisonTypes";
import { reviewerLabel } from "../src/reviewerLabels";
import {
  buildReviewSummary,
  reviewSummaryCsv,
  type FrozenReviewComparison,
} from "../src/reviewSummary";

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
  const receipt = await (
    await request.post("/api/specifications/worked-example")
  ).json();
  const id = receipt.document_id as string;
  const path = `/api/comparisons/${id}`;
  const original = (await (await request.get(path)).json()) as ComparisonData;
  const target = original.requirements.find((row) =>
    row.comparison.checks.some((check) => check.status === "unknown"),
  )!;
  const excluded = original.requirements.find(
    (row) => row.requirement_id !== target.requirement_id,
  )!;
  const removal = await request.post(`/api/reports/${id}/exclusions`, {
    data: {
      run_id: original.source_run_id,
      input_fingerprint: original.report_input_fingerprint,
      version: original.report_workspace_version,
      requirement_ids: [excluded.requirement_id],
      excluded: true,
      note: "Excluded for this quote; original comparison remains stored.",
    },
  });
  expect(removal.ok()).toBeTruthy();
  let report = (await removal.json()) as ComparisonData;
  expect(
    (
      await request.post(`${path}/reviews`, {
        data: {
          comparison_id: report.comparison_id,
          input_fingerprint: report.report_input_fingerprint,
          requirement_id: target.requirement_id,
          expected_version: 0,
          review_status: "accepted",
          decision: "clarify_customer",
          note: '=HYPERLINK("https://example.invalid","not executable")\nKeep the real unknown visible.',
        },
      })
    ).ok(),
  ).toBeTruthy();
  const question = await request.post(`${path}/clarifications`, {
    data: {
      comparison_id: report.comparison_id,
      input_fingerprint: report.report_input_fingerprint,
      requirement_id: target.requirement_id,
      expected_version: 0,
      client_request_id: randomUUID(),
      event_kind: "question",
      text: "Which reporting stage does the customer require?",
    },
  });
  expect(question.ok()).toBeTruthy();
  report = (await (await request.get(path)).json()) as ComparisonData;
  const snapshotPath = `/api/comparison-snapshots/${report.export_snapshot_id}/json`;
  const frozen = (await (
    await request.get(snapshotPath)
  ).json()) as FrozenReviewComparison;
  return { id, path, report, target, excluded, snapshotPath, frozen };
}
async function openSummary(page: Page) {
  await page
    .getByRole("button", { name: "Report options", exact: true })
    .click();
  await page
    .getByRole("menuitem", { name: "Review summary", exact: true })
    .click();
  const dialog = page.getByRole("dialog", {
    name: "Review summary",
    exact: true,
  });
  await expect(dialog).toBeVisible();
  return dialog;
}

test("saved review summary and CSV keep machine findings, exclusions and human decisions distinct", async ({
  page,
  request,
}, info) => {
  const { id, path, report, target, excluded, snapshotPath, frozen } =
    await prepare(request);
  let writes = 0;
  let snapshotReads = 0;
  page.on("request", (sent) => {
    if (!["GET", "HEAD", "OPTIONS"].includes(sent.method())) writes++;
    if (new URL(sent.url()).pathname === snapshotPath) snapshotReads++;
  });
  await page.goto(`/reports?document=${id}`);
  // The summary must export all saved tests, even when the report list is filtered.
  await page
    .getByRole("navigation", { name: "Requested tests" })
    .getByRole("textbox", { name: "Search requested tests", exact: true })
    .fill(target.test_name);
  expect(snapshotReads).toBe(0);
  const dialog = await openSummary(page);
  const table = dialog.getByRole("table");
  await expect(table.getByRole("row")).toHaveCount(
    report.requirements.length + 1,
  );
  const reviewedRow = table
    .getByRole("row")
    .filter({ has: page.getByText(target.test_name, { exact: true }) });
  await expect(reviewedRow).toContainText("Reviewed");
  await expect(reviewedRow).toContainText("Ask customer");
  await expect(reviewedRow).toContainText(/awaiting.*reply/i);
  await expect(reviewedRow).not.toContainText("Fully matched");
  await expect(
    table
      .getByRole("row")
      .filter({ has: page.getByText(excluded.test_name, { exact: true }) }),
  ).toContainText("Excluded");
  await expect(dialog).toContainText("not a final approval");
  const downloadEvent = page.waitForEvent("download");
  await dialog
    .getByRole("button", { name: "Download summary CSV", exact: true })
    .click();
  const download = await downloadEvent;
  expect(download.suggestedFilename()).toContain(
    report.export_snapshot_id!.slice(0, 8),
  );
  const stream = await download.createReadStream();
  const chunks: Buffer[] = [];
  for await (const chunk of stream!) chunks.push(Buffer.from(chunk));
  const csv = Buffer.concat(chunks).toString("utf8");
  expect(csv).toBe(reviewSummaryCsv(frozen, report.comparison_id));
  expect(csv).toContain(
    "'" + '=HYPERLINK(""https://example.invalid"",""not executable"")',
  );
  expect(csv).toContain(excluded.test_name);
  expect(snapshotReads).toBe(1);
  expect(writes).toBe(0);
  const after = (await (await request.get(path)).json()) as ComparisonData;
  expect(after.requirements).toEqual(report.requirements);
  expect(after.assessment_reviews).toEqual(report.assessment_reviews);
  expect(after.customer_clarifications).toEqual(report.customer_clarifications);
  await dialog.screenshot({
    path: info.outputPath("saved-review-summary.png"),
    animations: "disabled",
  });
});

test("summary rejects a foreign immutable snapshot without exposing or downloading it", async ({
  page,
  request,
}) => {
  const { id, snapshotPath, frozen } = await prepare(request);
  await page.route(`**${snapshotPath}`, (route) =>
    route.fulfill({
      json: {
        ...frozen,
        document: {
          ...frozen.document,
          document_id: "different-document",
          title: "Foreign secret summary",
        },
      },
    }),
  );
  await page.goto(`/reports?document=${id}`);
  const dialog = await openSummary(page);
  await expect(dialog.getByRole("alert")).toBeVisible();
  await expect(
    dialog.getByText("Foreign secret summary", { exact: true }),
  ).toHaveCount(0);
  await expect(
    dialog.getByRole("button", { name: "Download summary CSV", exact: true }),
  ).toBeDisabled();
  await expect(dialog.getByRole("table")).toHaveCount(0);
});

test("a delayed summary cannot reopen or display after leaving its specification", async ({
  page,
  request,
}) => {
  const { id, snapshotPath, frozen } = await prepare(request);
  let release = () => {};
  const gate = new Promise<void>((resolve) => {
    release = resolve;
  });
  await page.route(`**${snapshotPath}`, async (route) => {
    await gate;
    await route.fulfill({ json: frozen }).catch(() => {});
  });
  await page.goto("/specifications/library");
  await page.goto(`/reports?document=${id}`);
  await openSummary(page);
  // Opening fires the request immediately; use the loading view as the receipt.
  await expect(
    page.getByRole("dialog", { name: "Review summary", exact: true }),
  ).toContainText(/Loading/);
  await page.goBack();
  release();
  await expect(
    page.getByRole("dialog", { name: "Review summary", exact: true }),
  ).toHaveCount(0);
  await expect(
    page.getByRole("heading", { name: "Specification library", exact: true }),
  ).toBeVisible();
});

test("summary treats replies and review notes as records, not automatic approval", async ({
  request,
}) => {
  const { report, frozen, target } = await prepare(request);
  const summary = buildReviewSummary(frozen, report.comparison_id);
  expect(summary.counts).toEqual({
    included: report.requirements.length - 1,
    excluded: 1,
    reviewed: 1,
    notReviewed: report.requirements.length - 2,
  });
  const original = summary.rows.find(
    (row) => row.requirementId === target.requirement_id,
  )!;
  expect(original.reviewed).toBe(true);
  expect(original.unansweredQuestionCount).toBe(1);
  const changed = structuredClone(frozen);
  const thread = changed.customer_clarifications![target.requirement_id];
  const question = thread.events[0];
  thread.events.push({
    ...question,
    event_id: randomUUID(),
    event_kind: "customer_reply",
    question_id: question.event_id,
    version: 2,
    text: "Yes, our reply is recorded.",
  });
  thread.version = 2;
  const answered = buildReviewSummary(changed, report.comparison_id).rows.find(
    (row) => row.requirementId === target.requirement_id,
  )!;
  expect(answered.unansweredQuestionCount).toBe(0);
  expect(answered.machineStatus).toBe(original.machineStatus);
  expect(answered.review?.decision).toBe("clarify_customer");
  // A saved decision belonging to another assessment is not current approval.
  changed.assessment_reviews![target.requirement_id].comparison_id =
    "older-assessment";
  expect(
    buildReviewSummary(changed, report.comparison_id).rows.find(
      (row) => row.requirementId === target.requirement_id,
    )!.reviewed,
  ).toBe(false);
});

test("CSV neutralizes formula-like free text while preserving Unicode and quoted multiline fields", async ({
  request,
}) => {
  const { report, frozen, target } = await prepare(request);
  frozen.requirements[0].test_name = '温度, "test"\nSecond line';
  const notes = ["=2+2", "  +CMD()", "\t@SUM(1)", "-1+1", "\r=1+1"];
  for (const note of notes) {
    frozen.assessment_reviews![target.requirement_id].note = note;
    frozen.assessment_reviews![target.requirement_id].actor_label = note;
    const csv = reviewSummaryCsv(frozen, report.comparison_id);
    expect(csv.charCodeAt(0)).toBe(0xfeff);
    expect(csv).toContain('"温度, ""test""\nSecond line"');
    expect(csv).toContain("'" + note);
    expect(csv).toContain("\r\n");
  }
});

test("reviewer names use the saved identity and legacy exports never expose account IDs", async ({
  request,
}) => {
  const { report, frozen, target } = await prepare(request);
  const review = frozen.assessment_reviews![target.requirement_id];
  const actor = "private-staff-identity-8b0fea79";
  review.actor = actor;
  review.actor_label = "Name when review was saved";
  expect(
    reviewerLabel(review, {
      id: actor,
      display_name: "Renamed staff member",
      username: "staff",
    }),
  ).toBe("Name when review was saved");
  let csv = reviewSummaryCsv(frozen, report.comparison_id);
  expect(csv).toContain("Name when review was saved");
  expect(csv).not.toContain(actor);
  delete review.actor_label;
  csv = reviewSummaryCsv(frozen, report.comparison_id);
  expect(csv).toContain("Lab reviewer");
  expect(csv).not.toContain(actor);
  expect(
    reviewerLabel(review, {
      id: actor,
      display_name: "Current staff member",
      username: "staff",
    }),
  ).toBe("Current staff member");
  expect(reviewerLabel({ actor: "local_reviewer" })).toBe("Local reviewer");
  expect(reviewerLabel(undefined)).toBe("Reviewer not recorded");
});

test("an open summary stays frozen until it is explicitly reopened after a saved review changes", async ({
  page,
  request,
}) => {
  const { id, path, report, target } = await prepare(request);
  await page.goto(`/reports?document=${id}`);
  let dialog = await openSummary(page);
  const originalNote = report.assessment_reviews![target.requirement_id].note;
  await expect(dialog.getByText(originalNote, { exact: true })).toBeVisible();
  const changed = await request.post(`${path}/reviews`, {
    data: {
      comparison_id: report.comparison_id,
      input_fingerprint: report.report_input_fingerprint,
      requirement_id: target.requirement_id,
      expected_version:
        report.assessment_reviews![target.requirement_id].version,
      review_status: "accepted",
      decision: "keep",
      note: "A later saved review must not silently replace this summary.",
    },
  });
  expect(changed.ok()).toBeTruthy();
  await page.evaluate(() =>
    window.dispatchEvent(new Event("scione:inference-completed")),
  );
  await expect(dialog.getByRole("alert")).toContainText(
    "Newer saved review available",
  );
  await expect(dialog.getByText(originalNote, { exact: true })).toBeVisible();
  await expect(
    dialog.getByText(
      "A later saved review must not silently replace this summary.",
      { exact: true },
    ),
  ).toHaveCount(0);
  await dialog.getByRole("button", { name: "Close", exact: true }).click();
  dialog = await openSummary(page);
  await expect(
    dialog.getByText(
      "A later saved review must not silently replace this summary.",
      { exact: true },
    ),
  ).toBeVisible();
  await expect(dialog.getByText(originalNote, { exact: true })).toHaveCount(0);
});
