import { randomUUID } from "node:crypto";
import {
  expect,
  test,
  type APIRequestContext,
  type Page,
} from "@playwright/test";
import type {
  ComparisonData,
  ComparisonRequirement,
  CustomerClarificationEvent,
  CustomerClarificationThread,
} from "../src/comparisonTypes";

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
  const report = (await (await request.get(path)).json()) as ComparisonData;
  const target = report.requirements.find((row) =>
    row.comparison.checks.some((check) => check.status === "unknown"),
  )!;
  expect(target).toBeTruthy();
  return { id, path, report, target };
}

async function selectTest(page: Page, target: ComparisonRequirement) {
  await page
    .getByRole("navigation", { name: "Requested tests" })
    .getByRole("button", { name: target.test_name, exact: true })
    .click();
}

async function openThread(page: Page) {
  await page
    .getByRole("button", { name: "Customer clarifications", exact: true })
    .click();
  const panel = page.getByRole("dialog", {
    name: "Customer clarifications",
    exact: true,
  });
  await expect(
    panel.getByText("No customer questions recorded for this test yet.", {
      exact: true,
    }),
  ).toBeVisible();
  return panel;
}

function query(report: ComparisonData, requirementId: string) {
  return new URLSearchParams({
    comparison_id: report.comparison_id,
    input_fingerprint: report.report_input_fingerprint!,
    requirement_id: requirementId,
  });
}

test("customer questions and manually recorded replies load on demand and preserve machine findings", async ({
  page,
  request,
}, testInfo) => {
  const { id, path, report, target } = await prepare(request);
  let reads = 0;
  let writes = 0;
  let modelCalls = 0;
  page.on("request", (sent) => {
    if (new URL(sent.url()).pathname === `${path}/clarifications`) {
      if (sent.method() === "GET") reads++;
      if (sent.method() === "POST") writes++;
    }
    if (
      sent.method() === "POST" &&
      /\/(?:matching|analysis)$/.test(new URL(sent.url()).pathname)
    )
      modelCalls++;
  });
  await page.goto(`/reports?document=${id}`);
  await selectTest(page, target);
  await expect(
    page.getByRole("button", { name: "Customer clarifications", exact: true }),
  ).toHaveAttribute("aria-expanded", "false");
  expect(reads).toBe(0);
  await page.screenshot({
    path: testInfo.outputPath("customer-clarifications-collapsed.png"),
    animations: "disabled",
  });
  const panel = await openThread(page);
  const content = panel.getByRole("region", {
    name: "Customer clarification details",
    exact: true,
  });
  await expect(
    content.getByRole("button", { name: "Record question", exact: true }),
  ).toBeInViewport();
  await expect(
    panel.getByRole("button", { name: "Record question", exact: true }),
  ).toHaveCount(1);
  await panel
    .getByRole("button", { name: "Record question", exact: true })
    .click();
  let form = panel.getByRole("form", { name: "Record question", exact: true });
  await expect(form.getByLabel("Question", { exact: false })).toBeFocused();
  await expect(form.getByLabel("Question", { exact: false })).toBeInViewport();
  await form
    .getByLabel("Question", { exact: false })
    .fill("Unsaved customer question");
  await form.getByRole("button", { name: "Cancel", exact: true }).click();
  expect(writes).toBe(0);
  await panel
    .getByRole("button", { name: "Record question", exact: true })
    .click();
  form = panel.getByRole("form", { name: "Record question", exact: true });
  await form
    .getByLabel("Question", { exact: false })
    .fill("Can the customer confirm the required reporting stage?");
  await form
    .getByLabel("Contact (optional)", { exact: true })
    .fill("Customer materials engineer");
  await form.screenshot({
    path: testInfo.outputPath("record-customer-question.png"),
    animations: "disabled",
  });
  await form
    .getByRole("button", { name: "Save question", exact: true })
    .click();
  await expect(form).not.toBeVisible();
  await expect(panel).toBeVisible();
  await expect(
    panel.getByText("Can the customer confirm the required reporting stage?", {
      exact: true,
    }),
  ).toBeVisible();
  await panel
    .getByRole("button", { name: "Record reply to question 1", exact: true })
    .click();
  const reply = panel.getByRole("form", {
    name: "Record customer reply",
    exact: true,
  });
  await reply
    .getByLabel("Customer reply", { exact: false })
    .fill("The customer confirmed that a post-cycle reading is required.");
  await reply.getByRole("button", { name: "Save reply", exact: true }).click();
  await expect(reply).not.toBeVisible();
  await expect(panel).toBeVisible();
  await expect(
    panel.getByText(
      "The customer confirmed that a post-cycle reading is required.",
      { exact: true },
    ),
  ).toBeVisible();
  await expect(panel.getByText(/^Recorded by /)).toHaveCount(2);
  const saved = (await (
    await request.get(
      `${path}/clarifications?${query(report, target.requirement_id)}`,
    )
  ).json()) as CustomerClarificationThread;
  expect(saved.version).toBe(2);
  expect(saved.events.map((event) => event.event_kind)).toEqual([
    "question",
    "customer_reply",
  ]);
  expect(saved.events[1].question_id).toBe(saved.events[0].event_id);
  const after = (await (await request.get(path)).json()) as ComparisonData;
  expect(after.requirements).toEqual(report.requirements);
  expect(after.summary).toEqual(report.summary);
  expect(after.assessment_reviews).toEqual(report.assessment_reviews);
  expect(after.export_snapshot_id).not.toBe(report.export_snapshot_id);
  expect(writes).toBe(2);
  expect(modelCalls).toBe(0);
  await panel.screenshot({
    path: testInfo.outputPath("customer-questions-and-recorded-replies.png"),
    animations: "disabled",
  });
  await page.screenshot({
    path: testInfo.outputPath("customer-clarifications-expanded.png"),
    animations: "disabled",
  });
  await panel
    .getByRole("button", { name: "Close customer clarifications", exact: true })
    .click();
  await expect(panel).not.toBeVisible();
  await expect(
    page.getByRole("region", { name: "Customer clarification details" }),
  ).toHaveCount(0);
  await expect(
    page.getByRole("region", { name: "Your assessment review" }),
  ).toBeVisible();
});

test("recording controls remain discoverable in a long conversation and focus the requested input", async ({
  page,
  request,
}) => {
  const { id, path, report, target } = await prepare(request);
  const questions: CustomerClarificationEvent[] = Array.from(
    { length: 12 },
    (_, index) => {
      const identifier = randomUUID();
      return {
        event_id: identifier,
        question_id: identifier,
        client_request_id: randomUUID(),
        event_kind: "question",
        text: `Customer question ${index + 1}: confirm the intended reporting stage and source drawing.`,
        contact: "Materials engineer",
        actor: "lab1",
        actor_label: "Lab reviewer",
        created_at: "2026-11-24T15:00:00Z",
        version: index + 1,
        comparison_id: report.comparison_id,
        input_fingerprint: report.report_input_fingerprint!,
        document_id: id,
        source_run_id: report.source_run_id!,
        requirement_id: target.requirement_id,
      };
    },
  );
  let reads = 0;
  let writes = 0;
  await page.route(`**${path}/clarifications?**`, (route) => {
    reads++;
    return route.fulfill({
      json: {
        document_id: id,
        source_run_id: report.source_run_id,
        comparison_id: report.comparison_id,
        input_fingerprint: report.report_input_fingerprint,
        requirement_id: target.requirement_id,
        version: 12,
        events: questions,
      },
    });
  });
  page.on("request", (sent) => {
    if (
      new URL(sent.url()).pathname === `${path}/clarifications` &&
      sent.method() === "POST"
    )
      writes++;
  });
  await page.goto(`/reports?document=${id}`);
  await selectTest(page, target);
  await page
    .getByRole("button", { name: "Customer clarifications", exact: true })
    .click();
  const dialog = page.getByRole("dialog", {
    name: "Customer clarifications",
    exact: true,
  });
  const content = dialog.getByRole("region", {
    name: "Customer clarification details",
    exact: true,
  });
  const record = dialog.getByRole("button", {
    name: "Record question",
    exact: true,
  });
  await expect(record).toHaveCount(1);
  await expect(
    content.getByRole("button", { name: "Record question", exact: true }),
  ).toBeInViewport();
  await expect(content).toContainText(
    "Lab staff can record questions and replies from email or phone calls.",
  );
  await expect(dialog.getByRole("listitem")).toHaveCount(12);
  await record.click();
  const form = dialog.getByRole("form", {
    name: "Record question",
    exact: true,
  });
  await expect(form.getByLabel("Question", { exact: false })).toBeFocused();
  await expect(form.getByLabel("Question", { exact: false })).toBeInViewport();
  await form.getByRole("button", { name: "Cancel", exact: true }).click();
  await expect(form).not.toBeVisible();
  await dialog
    .getByRole("button", { name: "Record reply to question 12", exact: true })
    .click();
  const reply = dialog.getByRole("form", {
    name: "Record customer reply",
    exact: true,
  });
  await expect(reply).toContainText(`Reply to: ${questions[11].text}`);
  await expect(
    reply.getByLabel("Customer reply", { exact: false }),
  ).toBeFocused();
  await expect(
    reply.getByLabel("Customer reply", { exact: false }),
  ).toBeInViewport();
  await reply.getByRole("button", { name: "Cancel", exact: true }).click();
  expect(writes).toBe(0);
  expect(reads).toBe(1);
  await dialog.getByRole("button", { name: "Close", exact: true }).click();
  await page
    .getByRole("button", { name: "Customer clarifications", exact: true })
    .click();
  await expect(
    dialog.getByRole("button", { name: "Record question", exact: true }),
  ).toBeVisible();
  await expect(dialog.getByRole("form")).toHaveCount(0);
});

test("a lost save response preserves the draft and retries the same idempotent request without duplicate events", async ({
  page,
  request,
}) => {
  const { id, path, report, target } = await prepare(request);
  const attempts: Record<string, unknown>[] = [];
  let release!: () => void;
  const gate = new Promise<void>((resolve) => {
    release = resolve;
  });
  await page.route(`**${path}/clarifications`, async (route) => {
    if (route.request().method() !== "POST") return route.continue();
    attempts.push(route.request().postDataJSON());
    const response = await route.fetch();
    if (attempts.length === 1) {
      await gate;
      return route.fulfill({
        status: 503,
        json: {
          detail: "The save response was interrupted. Your draft is kept.",
        },
      });
    }
    return route.fulfill({ response });
  });
  await page.goto(`/reports?document=${id}`);
  await selectTest(page, target);
  const panel = await openThread(page);
  await panel
    .getByRole("button", { name: "Record question", exact: true })
    .click();
  const form = panel.getByRole("form", {
    name: "Record question",
    exact: true,
  });
  await form
    .getByLabel("Question", { exact: false })
    .fill("Please confirm the drawing revision.");
  await form
    .getByRole("button", { name: "Save question", exact: true })
    .evaluate((button: HTMLButtonElement) => {
      button.click();
      button.click();
    });
  await expect.poll(() => attempts.length).toBe(1);
  await expect(
    form.getByRole("button", { name: "Saving…", exact: true }),
  ).toBeDisabled();
  await expect(
    panel.getByRole("button", {
      name: "Close customer clarifications",
      exact: true,
    }),
  ).toBeDisabled();
  await page.keyboard.press("Escape");
  await expect(panel).toBeVisible();
  release();
  await expect(form.getByRole("alert")).toContainText("draft is kept");
  await panel
    .getByRole("button", { name: "Close customer clarifications", exact: true })
    .click();
  await expect(panel).not.toBeVisible();
  await page
    .getByRole("button", { name: "Customer clarifications", exact: true })
    .click();
  await expect(panel).toBeVisible();
  await expect(form.getByLabel("Question", { exact: false })).toHaveValue(
    "Please confirm the drawing revision.",
  );
  await form
    .getByRole("button", { name: "Save question", exact: true })
    .click();
  await expect(form).not.toBeVisible();
  expect(attempts).toHaveLength(2);
  expect(attempts[1]).toEqual(attempts[0]);
  const saved = await (
    await request.get(
      `${path}/clarifications?${query(report, target.requirement_id)}`,
    )
  ).json();
  expect(saved.events).toHaveLength(1);
});

test("a concurrent conversation update refreshes the thread, keeps the draft and uses a new request identity on deliberate retry", async ({
  page,
  request,
}) => {
  const { id, path, report, target } = await prepare(request);
  const attempts: Record<string, unknown>[] = [];
  page.on("request", (sent) => {
    if (
      new URL(sent.url()).pathname === `${path}/clarifications` &&
      sent.method() === "POST"
    )
      attempts.push(sent.postDataJSON());
  });
  await page.goto(`/reports?document=${id}`);
  await selectTest(page, target);
  const panel = await openThread(page);
  await panel
    .getByRole("button", { name: "Record question", exact: true })
    .click();
  const form = panel.getByRole("form", {
    name: "Record question",
    exact: true,
  });
  await form
    .getByLabel("Question", { exact: false })
    .fill("My unsent scope question");
  await form
    .getByLabel("Contact (optional)", { exact: true })
    .fill("My customer contact");
  expect(
    (
      await request.post(`${path}/clarifications`, {
        data: {
          comparison_id: report.comparison_id,
          input_fingerprint: report.report_input_fingerprint,
          requirement_id: target.requirement_id,
          expected_version: 0,
          client_request_id: randomUUID(),
          event_kind: "question",
          text: "Another lab user already asked about the stage.",
        },
      })
    ).ok(),
  ).toBeTruthy();
  await form
    .getByRole("button", { name: "Save question", exact: true })
    .click();
  await expect(form.getByRole("alert")).toContainText("Your draft is kept");
  await expect(
    panel.getByText("Another lab user already asked about the stage.", {
      exact: true,
    }),
  ).toBeVisible();
  await expect(form.getByLabel("Question", { exact: false })).toHaveValue(
    "My unsent scope question",
  );
  await expect(
    form.getByLabel("Contact (optional)", { exact: true }),
  ).toHaveValue("My customer contact");
  expect(attempts).toHaveLength(1);
  await form
    .getByRole("button", { name: "Save question", exact: true })
    .click();
  await expect(form).not.toBeVisible();
  expect(attempts).toHaveLength(2);
  expect(attempts[0].expected_version).toBe(0);
  expect(attempts[1].expected_version).toBe(1);
  expect(attempts[1].client_request_id).not.toBe(attempts[0].client_request_id);
});

test("editing a failed draft creates a new request identity without an automatic retry", async ({
  page,
  request,
}) => {
  const { id, path, report, target } = await prepare(request);
  const attempts: Record<string, unknown>[] = [];
  await page.route(`**${path}/clarifications`, async (route) => {
    if (route.request().method() !== "POST") return route.continue();
    attempts.push(route.request().postDataJSON());
    if (attempts.length === 1)
      return route.fulfill({
        status: 503,
        json: { detail: "Unable to save this record. Your draft is kept." },
      });
    return route.fulfill({ response: await route.fetch() });
  });
  await page.goto(`/reports?document=${id}`);
  await selectTest(page, target);
  const panel = await openThread(page);
  await panel
    .getByRole("button", { name: "Record question", exact: true })
    .click();
  const form = panel.getByRole("form", {
    name: "Record question",
    exact: true,
  });
  await form.getByLabel("Question", { exact: false }).fill("Original question");
  await form
    .getByRole("button", { name: "Save question", exact: true })
    .click();
  await expect(form.getByRole("alert")).toContainText("draft is kept");
  expect(attempts).toHaveLength(1);
  await form
    .getByLabel("Question", { exact: false })
    .fill("Corrected question");
  await form
    .getByRole("button", { name: "Save question", exact: true })
    .click();
  await expect(form).not.toBeVisible();
  expect(attempts).toHaveLength(2);
  expect(attempts[1].client_request_id).not.toBe(attempts[0].client_request_id);
  expect(attempts[1].text).toBe("Corrected question");
  const saved = await (
    await request.get(
      `${path}/clarifications?${query(report, target.requirement_id)}`,
    )
  ).json();
  expect(saved.events).toHaveLength(1);
  expect(saved.events[0].text).toBe("Corrected question");
});

test("a delayed clarification read cannot appear after selecting another test", async ({
  page,
  request,
}) => {
  const { id, path, report, target } = await prepare(request);
  const other = report.requirements.find(
    (row) =>
      row.requirement_id !== target.requirement_id &&
      row.comparison.status !== "excluded",
  )!;
  expect(other).toBeTruthy();
  let release!: () => void;
  let started = false;
  let finished = false;
  const gate = new Promise<void>((resolve) => {
    release = resolve;
  });
  await page.route(`**${path}/clarifications?**`, async (route) => {
    const requirementId = new URL(route.request().url()).searchParams.get(
      "requirement_id",
    )!;
    const value: CustomerClarificationThread = {
      document_id: id,
      source_run_id: report.source_run_id!,
      comparison_id: report.comparison_id,
      input_fingerprint: report.report_input_fingerprint!,
      requirement_id: requirementId,
      version: 0,
      events: [],
    };
    if (requirementId !== target.requirement_id)
      return route.fulfill({ json: value });
    started = true;
    await gate;
    const eventId = randomUUID();
    try {
      await route.fulfill({
        json: {
          ...value,
          version: 1,
          events: [
            {
              event_id: eventId,
              question_id: eventId,
              client_request_id: randomUUID(),
              event_kind: "question",
              text: "Late question for the previously selected test",
              contact: "",
              actor: "lab1",
              created_at: "2026-11-24T15:00:00Z",
              version: 1,
              comparison_id: report.comparison_id,
              input_fingerprint: report.report_input_fingerprint!,
              document_id: id,
              source_run_id: report.source_run_id!,
              requirement_id: requirementId,
            },
          ],
        },
      });
    } finally {
      finished = true;
    }
  });
  await page.goto(`/reports?document=${id}`);
  await selectTest(page, target);
  try {
    await page
      .getByRole("button", { name: "Customer clarifications", exact: true })
      .click();
    await expect.poll(() => started).toBe(true);
    await page
      .getByRole("button", {
        name: "Close customer clarifications",
        exact: true,
      })
      .click();
    await selectTest(page, other);
    await expect(
      page.getByRole("button", {
        name: "Customer clarifications",
        exact: true,
      }),
    ).toHaveAttribute("aria-expanded", "false");
    await openThread(page);
    release();
    await expect.poll(() => finished).toBe(true);
    await expect(
      page.getByText("Late question for the previously selected test", {
        exact: true,
      }),
    ).toHaveCount(0);
  } finally {
    release();
  }
});

test("clarifications reject another source run and identify earlier-assessment records", async ({
  page,
  request,
}) => {
  const { id, path, report, target } = await prepare(request);
  let reads = 0;
  const earlier: CustomerClarificationEvent = {
    event_id: randomUUID(),
    question_id: "",
    client_request_id: randomUUID(),
    event_kind: "question",
    text: "A saved question from the earlier assessment",
    contact: "Customer contact",
    actor: "lab1",
    created_at: "2026-11-24T15:00:00Z",
    version: 1,
    comparison_id: "previous-assessment",
    input_fingerprint: "previous-fingerprint",
    document_id: id,
    source_run_id: report.source_run_id!,
    requirement_id: target.requirement_id,
  };
  earlier.question_id = earlier.event_id;
  await page.route(`**${path}/clarifications?**`, (route) => {
    reads++;
    const value: CustomerClarificationThread = {
      document_id: id,
      source_run_id: report.source_run_id!,
      comparison_id: report.comparison_id,
      input_fingerprint: report.report_input_fingerprint!,
      requirement_id: target.requirement_id,
      version: 1,
      events: [earlier],
    };
    if (reads === 1)
      return route.fulfill({
        json: {
          ...value,
          source_run_id: "obsolete-extraction",
          events: [
            {
              ...earlier,
              text: "Do not display stale extraction data",
              source_run_id: "obsolete-extraction",
            },
          ],
        },
      });
    return route.fulfill({ json: value });
  });
  await page.goto(`/reports?document=${id}`);
  await selectTest(page, target);
  await page
    .getByRole("button", { name: "Customer clarifications", exact: true })
    .click();
  const panel = page.getByRole("region", {
    name: "Customer clarification details",
    exact: true,
  });
  await expect(panel.getByRole("alert")).toContainText("assessment changed");
  await expect(
    page.getByText("Do not display stale extraction data", { exact: true }),
  ).toHaveCount(0);
  await panel.getByRole("button", { name: "Retry", exact: true }).click();
  await expect(panel.getByText(earlier.text, { exact: true })).toBeVisible();
  await expect(
    panel.getByText("From an earlier assessment of this source extraction.", {
      exact: true,
    }),
  ).toBeVisible();
});
