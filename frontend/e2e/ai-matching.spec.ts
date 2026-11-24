import { expect, test } from "@playwright/test";
import type { ComparisonData } from "../src/comparisonTypes";
import type { LibraryDocument } from "../src/libraryTypes";

const base = "http://127.0.0.1:8011";

test("AI matching requires consent, reuses results and keeps the last success after failure", async ({
  page,
}) => {
  const source: LibraryDocument = {
    document_id: "a1b2c3d4e5f60718",
    company_name: "Customer A",
    specification_id: "CSP-301",
    title: "Coating requirements",
    revision: "Rev B",
    family_id: "coating",
    version: 1,
    original_name: "coating.pdf",
    created_at: "2026-11-09T12:00:00Z",
    updated_at: "2026-11-09T12:00:00Z",
    requirement_count: 1,
    review_count: 0,
    run_id: "source-run",
    page_count: 2,
  };
  await page.route("**/api/workspace", (route) =>
    route.fulfill({
      json: {
        lab: { id: "lab", name: "Example lab", version: 1, capabilities: [] },
        catalog: { tests: [], methods: [] },
        providers: [
          {
            id: "gemini",
            label: "Gemini",
            model: "offline-fixture-model",
            available: true,
            output_limit: 8000,
          },
        ],
        documents: [
          {
            document_id: source.document_id,
            original_name: source.original_name,
            latest_run_id: source.run_id,
          },
        ],
      },
    }),
  );
  await page.route("**/api/library", (route) =>
    route.fulfill({ json: { documents: [source] } }),
  );
  const comparison: ComparisonData = {
    comparison_id: "rules-snapshot",
    created_at: source.created_at,
    document: source,
    lab_name: "Example lab",
    lab_version: 1,
    source_run_id: source.run_id!,
    report_input_fingerprint: "report-inputs",
    report_workspace_version: 0,
    comparison_engine: "deterministic_rules",
    summary: { needs_information: 1 },
    requirements: [
      {
        requirement_id: "req_water",
        test_name: "Water jet adhesion",
        methods: ["ISO 16925"],
        capability_id: "cap_water",
        source_review_state: "ready",
        comparison: { status: "needs_information", checks: [] },
      },
    ],
  };
  const overview = {
    source_run_id: source.run_id,
    report_input_fingerprint: "report-inputs",
    input_fingerprint: "matching-inputs",
    current_run_id: null as string | null,
    runs: [] as {
      run_id: string;
      status: string;
      provider: string;
      model: string;
      created_at: string;
      current_inputs: boolean;
    }[],
  };
  let submissions = 0;
  let failNext = false;
  const requestIds = new Set<string>();
  interface MatchingJob {
    operation_id: string;
    request_id: string;
    document_id: string;
    kind: "matching";
    state: "running" | "completed" | "failed";
    started_at: string;
    result?: { run_id: string; status: "completed"; cached: boolean };
    error?: {
      status: number;
      detail: { message: string; run_id: string; error_code: string };
    };
  }
  const jobs = new Map<
    string,
    { job: MatchingJob; cached: boolean; fail: boolean }
  >();
  let latestJob: MatchingJob | null = null;
  let operationReads = 0;
  let release!: () => void;
  const heldResponse = new Promise<void>((resolve) => {
    release = resolve;
  });
  await page.route(`**/api/comparisons/${source.document_id}`, (route) =>
    route.fulfill({ json: comparison }),
  );
  await page.route(
    `**/api/specifications/${source.document_id}/operations`,
    (route) =>
      route.fulfill({
        json: {
          recent_job: latestJob,
          active: latestJob?.state === "running" ? [latestJob] : [],
        },
      }),
  );
  await page.route(
    `**/api/specifications/${source.document_id}/matching`,
    async (route) => {
      if (route.request().method() === "GET") {
        await route.fulfill({ json: overview });
        return;
      }
      submissions += 1;
      expect(route.request().headers()["prefer"]).toBe("respond-async");
      const requestId = route.request().headers()["x-inference-request-id"];
      expect(requestId).toMatch(/^[a-f0-9]{8}-[a-f0-9-]{27}$/i);
      expect(requestIds.has(requestId)).toBe(false);
      requestIds.add(requestId);
      expect(route.request().postDataJSON()).toMatchObject({
        run_id: "source-run",
        input_fingerprint: "report-inputs",
        provider: "gemini",
        model: "offline-fixture-model",
        permission: true,
        rerun: false,
        output_limit: 8000,
      });
      const cached = submissions > 1 && !failNext;
      const job: MatchingJob = {
        operation_id: `offline-matching-operation-${submissions}`,
        request_id: requestId,
        document_id: source.document_id,
        kind: "matching",
        state: cached ? "completed" : "running",
        started_at: source.created_at,
      };
      latestJob = job;
      jobs.set(job.operation_id, { job, cached, fail: failNext });
      // Even a cached terminal receipt must read its saved result when the
      // admission response contains no result body.
      await route.fulfill({ status: 202, json: job });
    },
  );
  await page.route(
    "**/api/operations/offline-matching-operation-*",
    async (route) => {
      operationReads++;
      const operationId = new URL(route.request().url()).pathname
        .split("/")
        .at(-1)!;
      const entry = jobs.get(operationId)!;
      expect(entry).toBeTruthy();
      const { job, cached, fail } = entry;
      if (job.operation_id === "offline-matching-operation-1")
        await heldResponse;
      if (job.result || job.error) {
        await route.fulfill({ json: job });
        return;
      }
      if (fail) {
        overview.runs.unshift({
          run_id: "failed-attempt",
          status: "failed",
          provider: "gemini",
          model: "offline-fixture-model",
          created_at: source.created_at,
          current_inputs: true,
        });
        job.state = "failed";
        job.error = {
          status: 502,
          detail: {
            message: "Model temporarily unavailable. Please try again later.",
            run_id: "failed-attempt",
            error_code: "provider_unavailable",
          },
        };
        await route.fulfill({ json: job });
        return;
      }
      if (!cached) {
        overview.current_run_id = "saved-match";
        overview.runs = [
          {
            run_id: "saved-match",
            status: "completed",
            provider: "gemini",
            model: "offline-fixture-model",
            created_at: source.created_at,
            current_inputs: true,
          },
        ];
        comparison.comparison_engine = "ai_semantic_matching";
        comparison.matching_run_id = "saved-match";
        comparison.comparison_id = "ai-snapshot";
      }
      job.state = "completed";
      job.result = { run_id: "saved-match", status: "completed", cached };
      await route.fulfill({ json: job });
    },
  );
  await page.goto(`${base}/reports?document=${source.document_id}`);
  await expect(
    page.getByRole("button", { name: "Generate report", exact: true }),
  ).toBeVisible();
  expect(submissions).toBe(0);
  await page
    .getByRole("button", { name: "Developer report tools", exact: true })
    .click();
  await expect(
    page.getByText("Rules-based comparison", { exact: true }),
  ).toBeVisible();
  expect(submissions).toBe(0);
  await page
    .getByRole("button", { name: "Run AI matching", exact: true })
    .click();
  const dialog = page.getByRole("dialog", { name: "Run AI matching" });
  await expect(
    dialog.getByRole("button", { name: "Start matching", exact: true }),
  ).toBeDisabled();
  await dialog.getByRole("checkbox", { name: /I allow sending/ }).check();
  await dialog
    .getByRole("button", { name: "Start matching", exact: true })
    .click();
  try {
    await expect(
      dialog.getByRole("status", { name: "Comparing your requirements" }),
    ).toBeVisible();
    await expect(
      dialog.getByRole("progressbar", { name: "AI request in progress" }),
    ).toBeVisible();
    await expect(
      dialog.getByRole("progressbar", { name: "AI request in progress" }),
    ).not.toHaveAttribute("aria-valuenow");
    await expect(
      dialog.getByRole("button", { name: "Matching…", exact: true }),
    ).toBeDisabled();
    await expect(
      dialog.getByRole("button", { name: "Cancel", exact: true }),
    ).toBeDisabled();
    expect(submissions).toBe(1);
  } finally {
    release();
  }
  await expect(dialog).not.toBeVisible();
  await expect(
    page.getByText("AI-assisted comparison", { exact: true }),
  ).toBeVisible();
  await page.reload();
  await expect(
    page.getByRole("button", { name: "Check for updates", exact: true }),
  ).toBeVisible();
  await page
    .getByRole("button", { name: "Developer report tools", exact: true })
    .click();
  await expect(
    page.getByText("AI-assisted comparison", { exact: true }),
  ).toBeVisible();
  expect(submissions).toBe(1);
  await expect(
    page.getByRole("link", { name: "Download eval artifacts" }),
  ).not.toBeVisible();
  await page
    .getByRole("button", { name: /Developer details · matching history/ })
    .click();
  await expect(
    page.getByRole("link", { name: "Download eval artifacts" }),
  ).toHaveAttribute(
    "href",
    `/api/specifications/${source.document_id}/matching/saved-match/artifacts`,
  );
  await page
    .getByRole("button", { name: "Close history", exact: true })
    .click();

  await page
    .getByRole("button", { name: "Run AI matching", exact: true })
    .click();
  await dialog.getByRole("checkbox", { name: /I allow sending/ }).check();
  await dialog
    .getByRole("button", { name: "Start matching", exact: true })
    .click();
  await expect(dialog).not.toBeVisible();
  await expect(
    page.getByText("Reused the saved AI matching for these inputs."),
  ).toBeVisible();
  expect(submissions).toBe(2);

  failNext = true;
  await page
    .getByRole("button", { name: "Run AI matching", exact: true })
    .click();
  await dialog.getByRole("checkbox", { name: /I allow sending/ }).check();
  await dialog
    .getByRole("button", { name: "Start matching", exact: true })
    .click();
  await expect(dialog.getByRole("alert")).toContainText(
    "Model temporarily unavailable",
  );
  await expect(
    dialog.getByRole("checkbox", { name: /I allow sending/ }),
  ).not.toBeChecked();
  await expect(
    dialog.getByRole("button", { name: "Start matching", exact: true }),
  ).toBeDisabled();
  expect(submissions).toBe(3);
  await dialog.getByRole("button", { name: "Cancel", exact: true }).click();
  await expect(
    page.getByText("AI-assisted comparison", { exact: true }),
  ).toBeVisible();
  await page
    .getByRole("button", { name: /Developer details · matching history/ })
    .click();
  await expect(
    page
      .getByRole("dialog", { name: "Developer matching history" })
      .getByRole("alert"),
  ).toContainText(
    "The latest AI matching attempt failed. The last saved comparison is still available.",
  );
  expect(requestIds.size).toBe(3);
  expect(operationReads).toBeGreaterThanOrEqual(3);
  expect(overview.current_run_id).toBe("saved-match");
  expect(comparison.matching_run_id).toBe("saved-match");
});
