import { expect, test } from "@playwright/test";
import type { ComparisonData } from "../src/comparisonTypes";

test("report assessment acceptance and follow-up persist without approving unknown checks", async ({
  page,
  request,
}, testInfo) => {
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
  const id = imported.document_id;
  const url = `/api/comparisons/${id}`;
  const before = (await (await request.get(url)).json()) as ComparisonData;
  const target = before.requirements.find((row) =>
    row.comparison.checks.some((check) => check.status === "unknown"),
  )!;
  expect(target).toBeTruthy();
  let modelCalls = 0;
  page.on("request", (sent) => {
    if (sent.method() === "POST" && /\/(?:matching|analyze)$/.test(sent.url()))
      modelCalls++;
  });
  await page.goto(`/reports?document=${id}`);
  const select = () =>
    page
      .getByRole("navigation", { name: "Requested tests" })
      .getByRole("button", { name: target.test_name, exact: true })
      .click();
  await select();
  await page
    .getByRole("button", { name: "Accept assessment", exact: true })
    .click();
  await expect(
    page.getByText("Assessment reviewed", { exact: true }),
  ).toBeVisible();
  const accepted = (await (await request.get(url)).json()) as ComparisonData;
  expect(accepted.comparison_id).toBe(before.comparison_id);
  expect(accepted.requirements).toEqual(before.requirements);
  expect(accepted.summary).toEqual(before.summary);
  expect(
    accepted.assessment_reviews?.[target.requirement_id].review_status,
  ).toBe("accepted");
  await page.reload();
  await select();
  await expect(
    page.getByText("Assessment reviewed", { exact: true }),
  ).toBeVisible();
  await page.getByRole("combobox", { name: "Show", exact: true }).click();
  await page.getByRole("option", { name: "Not reviewed", exact: true }).click();
  await expect(
    page
      .getByRole("navigation", { name: "Requested tests" })
      .getByRole("button", {
        name: target.test_name,
        exact: true,
      }),
  ).toHaveCount(0);
  await page.getByRole("combobox", { name: "Show", exact: true }).click();
  await page.getByRole("option", { name: "All tests", exact: true }).click();
  await select();
  await page
    .getByRole("button", { name: "Edit decision", exact: true })
    .click();
  const dialog = page.getByRole("dialog", {
    name: "Review decision",
    exact: true,
  });
  await dialog.getByLabel("Follow-up decision", { exact: true }).click();
  await page
    .getByRole("option", { name: "Ask the customer to clarify", exact: true })
    .click();
  await dialog
    .getByLabel("Follow-up note", { exact: true })
    .fill("Please confirm the missing reporting stage.");
  await dialog.getByRole("button", { name: "Cancel", exact: true }).click();
  await expect(dialog).not.toBeVisible();
  expect(
    (await (await request.get(`${url}/reviews`)).json()).events,
  ).toHaveLength(1);
  await page
    .getByRole("button", { name: "Edit decision", exact: true })
    .click();
  await dialog.getByLabel("Follow-up decision", { exact: true }).click();
  await page
    .getByRole("option", { name: "Ask the customer to clarify", exact: true })
    .click();
  await dialog
    .getByLabel("Follow-up note", { exact: true })
    .fill("Please confirm the missing reporting stage.");
  await dialog
    .getByRole("button", { name: "Save review", exact: true })
    .click();
  await expect(dialog).not.toBeVisible();
  await expect(
    page.getByText("Ask the customer to clarify", { exact: true }),
  ).toBeVisible();
  const updated = (await (await request.get(url)).json()) as ComparisonData;
  expect(updated.requirements).toEqual(before.requirements);
  expect(updated.assessment_reviews?.[target.requirement_id].version).toBe(2);
  const history = await request.get(`${url}/reviews?download=true`);
  expect(history.headers()["content-disposition"]).toContain("attachment");
  expect((await history.json()).events).toHaveLength(2);
  await page.screenshot({
    path: testInfo.outputPath("reviewed-assessment.png"),
    animations: "disabled",
  });
  expect(modelCalls).toBe(0);
  const changedLab = await (await request.get("/api/lab")).json();
  changedLab.description =
    "Changed declaration: previous assessment review is historical.";
  expect(
    (await request.put("/api/lab", { data: changedLab })).ok(),
  ).toBeTruthy();
  await page.reload();
  await select();
  await expect(
    page.getByRole("button", { name: "Accept assessment", exact: true }),
  ).toBeVisible();
  await expect(
    page.getByText("Assessment reviewed", { exact: true }),
  ).toHaveCount(0);
});
