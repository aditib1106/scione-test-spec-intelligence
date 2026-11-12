import {
  expect,
  test,
  type Page,
  type APIRequestContext,
} from "@playwright/test";
import type { DocumentNotesReview } from "../src/documentNotesTypes";
import type { SpecificationData } from "../src/types";

async function setup(page: Page, request: APIRequestContext) {
  await request.post("/api/workspace/reset", { data: { acknowledged: true } });
  const imported = await request.post("/api/specifications/worked-example");
  const { document_id: id } = await imported.json();
  const source = (await (
    await request.get(`/api/specifications/${id}`)
  ).json()) as SpecificationData;
  const report = await (await request.get(`/api/comparisons/${id}`)).json();
  let review: DocumentNotesReview = {
    source_fingerprint: "offline-source-fingerprint",
    version: 0,
    notes: [
      {
        id: "materials",
        kind: "context",
        title: "Covered supply",
        detail: "Check which tests use this material.",
        quote: "Extruded EPDM door-gasket rings and companion rubber sheets",
        page: 1,
        reviewed: false,
      },
    ],
    pending_count: 1,
    reviewed_count: 0,
  };
  let modelPosts = 0;
  let forceConflict = false;
  await page.route("**/api/auth/session", (route) =>
    route.fulfill({
      json: {
        authenticated: true,
        enabled: true,
        user: {
          id: "reader",
          username: "reader",
          display_name: "Reader",
          role: "lab",
          lab_id: "offline-lab",
          lab_name: "Offline lab",
        },
        csrf_token: "offline-csrf",
      },
    }),
  );
  await page.route(`**/api/specifications/${id}`, (route) =>
    route.fulfill({
      json: {
        ...source,
        document_notes_review: review,
        assessments: Object.fromEntries(
          Object.entries(source.assessments).map(([key, value]) => [
            key,
            { ...value, state: "confirmed" },
          ]),
        ),
        review: source.review
          ? {
              ...source.review,
              decisions: Object.fromEntries(
                source.run!.extraction.requirements.map((row) => [
                  row.id,
                  {
                    requirement_id: row.id,
                    status: "confirmed",
                    note: "",
                    updated_at: "2026-11-11T00:00:00Z",
                  },
                ]),
              ),
            }
          : null,
      },
    }),
  );
  await page.route(`**/api/comparisons/${id}`, (route) =>
    route.fulfill({ json: { ...report, document_notes_review: review } }),
  );
  await page.route(
    `**/api/specifications/${id}/document-notes/review`,
    async (route) => {
      const body = route.request().postDataJSON();
      if (forceConflict) {
        forceConflict = false;
        review = { ...review, version: review.version + 1 };
        await route.fulfill({
          status: 409,
          json: { detail: "The saved notes changed." },
        });
        return;
      }
      expect(body.source_fingerprint).toBe(review.source_fingerprint);
      expect(body.expected_version).toBe(review.version);
      review = {
        ...review,
        version: review.version + 1,
        notes: review.notes.map((note) =>
          body.note_ids.includes(note.id)
            ? { ...note, reviewed: body.reviewed }
            : note,
        ),
        pending_count: body.reviewed ? 0 : 1,
        reviewed_count: body.reviewed ? 1 : 0,
      };
      await route.fulfill({ json: review });
    },
  );
  page.on("request", (sent) => {
    if (
      sent.method() === "POST" &&
      /\/(analysis|matching)$/.test(new URL(sent.url()).pathname)
    )
      modelPosts++;
  });
  return {
    id,
    report,
    getModelPosts: () => modelPosts,
    conflict: () => {
      forceConflict = true;
    },
  };
}

test("confirmed tests keep document notes separate and checked notes persist across report navigation", async ({
  page,
  request,
}) => {
  const state = await setup(page, request);
  await page.goto(`/specifications?document=${state.id}`);
  await expect(
    page.getByText("Check extracted requirements", { exact: true }),
  ).toHaveCount(0);
  const notes = page.getByRole("button", { name: /Document notes/ });
  await expect(notes).toContainText("1 to check");
  expect((await notes.boundingBox())!.height).toBeLessThan(90);
  await expect(
    page.getByText(
      "Extruded EPDM door-gasket rings and companion rubber sheets",
      { exact: true },
    ),
  ).not.toBeVisible();
  await notes.click();
  await expect(
    page.getByText(
      "Extruded EPDM door-gasket rings and companion rubber sheets",
      { exact: true },
    ),
  ).toBeVisible();
  const position = await page.evaluate(() => window.scrollY);
  await page.getByRole("button", { name: "Mark checked", exact: true }).click();
  await expect(notes).toContainText("Reviewed");
  expect(
    Math.abs((await page.evaluate(() => window.scrollY)) - position),
  ).toBeLessThan(4);
  await page.goto(`/reports?document=${state.id}`);
  const savedNotes = page.getByRole("button", { name: /Document notes/ });
  await expect(savedNotes).toContainText("Reviewed");
  await expect(
    page.getByText("Check extracted requirements", { exact: true }),
  ).toHaveCount(0);
  await savedNotes.click();
  await expect(page.getByText("Checked", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "Reopen", exact: true }).click();
  await expect(savedNotes).toContainText("1 to check");
  await page.reload();
  await expect(
    page.getByRole("button", { name: /Document notes/ }),
  ).toContainText("1 to check");
  const unchanged = await page.request.get(`/api/comparisons/${state.id}`);
  expect((await unchanged.json()).summary).toEqual(state.report.summary);
  expect(state.getModelPosts()).toBe(0);
});

test("a stale note review stays unsaved until current notes are loaded", async ({
  page,
  request,
}) => {
  const state = await setup(page, request);
  await page.goto(`/reports?document=${state.id}`);
  const notes = page.getByRole("button", { name: /Document notes/ });
  await notes.click();
  state.conflict();
  await page.getByRole("button", { name: "Mark checked", exact: true }).click();
  await expect(page.getByRole("alert")).toContainText(
    "These notes have changed",
  );
  await expect(notes).toContainText("1 to check");
  await page
    .getByRole("button", { name: "Load current notes", exact: true })
    .click();
  await expect(page.getByRole("alert")).toHaveCount(0);
  await page.getByRole("button", { name: "Mark checked", exact: true }).click();
  await expect(notes).toContainText("Reviewed");
  expect(state.getModelPosts()).toBe(0);
});
