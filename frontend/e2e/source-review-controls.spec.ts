import { expect, test } from "@playwright/test";
import type { SpecificationData } from "../src/types";
import { editRequirement } from "./sourceEditor";

test.use({ actionTimeout: 15_000 });

test("source coverage guidance supports correcting scope and instructions without implying a missing test", async ({
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
  const path = `/api/specifications/${id}`;
  const source = (await (await request.get(path)).json()) as SpecificationData;
  const issue =
    'Literal retention was not verified for "mild steel witnesses" and "Release requires Materials Engineering approval"; check existing scope and instructions.';
  await page.route(`**${path}`, (route) =>
    route.fulfill({ json: { ...source, coverage_issues: [issue] } }),
  );
  await page.goto(`/specifications?document=${id}`);
  const title = page.getByText("Check extracted requirements", {
    exact: true,
  });
  await expect(title).toHaveCount(1);
  const warning = page.getByRole("alert").filter({ has: title });
  await expect(warning).toContainText("1 source check to review");
  await expect(warning.getByText(issue, { exact: true })).not.toBeVisible();
  await warning
    .getByRole("button", { name: "Review notes", exact: true })
    .click();
  await expect(warning.getByText(issue, { exact: true })).toBeVisible();
  await expect(
    warning.getByText("Check the PDF and use Add missing test if needed.", {
      exact: true,
    }),
  ).toHaveCount(0);
});

test("source references stay compact, notes expand on demand, and editing focuses one field", async ({
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
  const path = `/api/specifications/${id}`;
  const original = (await (
    await request.get(path)
  ).json()) as SpecificationData;
  const selected = original.run!.extraction.requirements[0];
  const claims = original.normalized_requirements![selected.id].claims;
  const parameter = claims.find(
    (claim) => claim.kind === "condition" && claim.parameter_id,
  )!;
  const displayed = structuredClone(original);
  const retainedSource =
    "From each lot, coat nine flat 100 × 200 mm steel witnesses using the same e-coat, primer and basecoat as the nominated part.";
  const textClaim = (id: string, text: string) => ({
    ...parameter,
    id,
    parameter_id: null,
    label: "Retained wording",
    operator: "text" as const,
    value: null,
    minimum: null,
    maximum: null,
    tolerance: null,
    values: [],
    unit: null,
    text_value: text,
    raw_text: text,
    source_atom_ids: [],
    normalization_state: "unresolved" as const,
  });
  displayed.normalized_requirements![selected.id].claims = [
    parameter,
    {
      ...textClaim("fixture_connective", "we want"),
      raw_text: "We want the same already represented thermal cycle checks.",
    },
    textClaim(
      "fixture_alternative",
      "If an ASTM route is approved, our team will issue the corresponding acceptance class before testing.",
    ),
    {
      ...textClaim("fixture_parser_residual", "coat100200"),
      raw_text: retainedSource,
    },
  ];
  await page.route(`**${path}`, (route) => route.fulfill({ json: displayed }));
  let mutations = 0;
  page.on("request", (sent) => {
    if (
      sent.method() === "POST" &&
      sent.url().includes(`/api/specifications/${id}/`)
    )
      mutations += 1;
  });
  await page.goto(`/specifications?document=${id}&requirement=${selected.id}`);
  const layoutFacts = () =>
    page.evaluate(() => {
      const title = [...document.querySelectorAll("h1,h2,h3,h4,h5,h6")].find(
        (item) => item.textContent?.trim() === "PDF review",
      )!;
      const header = document.querySelector("header")!;
      const rect = (item: Element) => {
        const bounds = item.getBoundingClientRect();
        return {
          top: bounds.top,
          bottom: bounds.bottom,
          height: bounds.height,
        };
      };
      return { scrollY, title: rect(title), header: rect(header) };
    });
  const table = page.getByRole("table", { name: "Condition parameters" });
  // Unstructured notes are not additional primary parameter rows.
  await expect(
    page.getByRole("tab", { name: "Conditions (1)", exact: true }),
  ).toBeVisible();
  await expect(
    page.getByRole("tab", { name: "Criteria (0)", exact: true }),
  ).toBeVisible();
  await expect(table.getByRole("row")).toHaveCount(2); // heading plus one actual parameter
  const reference = table
    .getByRole("button", { name: /^View condition source on page/ })
    .first();
  await expect(reference).toHaveText(String(parameter.source_pages[0]));
  const preview = page.waitForResponse(
    (response) =>
      response.url().includes(`/api/specifications/${id}/pages/`) &&
      response.url().includes(`claim_id=${parameter.id}`),
  );
  await reference.click();
  expect((await preview).ok()).toBeTruthy();
  const notes = page.getByRole("button", {
    name: "Source notes (2)",
    exact: true,
  });
  await expect(notes).toHaveAttribute("aria-expanded", "false");
  await expect(
    page.getByText("Alternative method needs customer approval", {
      exact: true,
    }),
  ).not.toBeVisible();
  await expect(
    page.getByText(retainedSource, { exact: true }),
  ).not.toBeVisible();
  await notes.click();
  await expect(page.getByText("we want", { exact: true })).toHaveCount(0);
  await expect(
    page.getByText(
      "We want the same already represented thermal cycle checks.",
      {
        exact: true,
      },
    ),
  ).toHaveCount(0);
  await expect(page.getByText(retainedSource, { exact: true })).toBeVisible();
  await expect(page.getByText("coat100200", { exact: true })).toHaveCount(0);
  const alternative = page.getByText(
    "Alternative method needs customer approval",
    { exact: true },
  );
  await expect(alternative).toBeVisible();
  await alternative.click();
  await expect(
    page.getByText(
      "If an ASTM route is approved, our team will issue the corresponding acceptance class before testing.",
      { exact: true },
    ),
  ).toBeVisible();
  await notes.click();
  await page.evaluate(() => window.scrollTo(0, 0));
  await expect.poll(async () => page.evaluate(() => window.scrollY)).toBe(0);
  await page.evaluate(
    () =>
      new Promise<void>((resolve) =>
        requestAnimationFrame(() => requestAnimationFrame(() => resolve())),
      ),
  );
  await page
    .getByRole("table", { name: "Test details" })
    .evaluate((element) => {
      for (
        let parent = element.parentElement;
        parent;
        parent = parent.parentElement
      ) {
        if (
          getComputedStyle(parent).overflowY === "auto" &&
          parent.scrollHeight > parent.clientHeight
        ) {
          parent.scrollTop = 0;
          break;
        }
      }
    });
  const cleanLayout = await layoutFacts();
  expect(cleanLayout.header.top).toBe(0);
  expect(cleanLayout.title.top).toBeGreaterThanOrEqual(
    cleanLayout.header.bottom,
  );
  await expect(page.locator('a[href="#main"]')).not.toBeInViewport();
  await expect(
    page.getByRole("tab", { name: "PDF & review", exact: true }),
  ).toBeInViewport();
  const dialog = await editRequirement(page, "Conditions", parameter.label);
  await expect(
    dialog.getByLabel("Choose requirement", { exact: true }),
  ).toBeVisible();
  await expect(dialog.getByLabel("Parameter", { exact: true })).toBeVisible();
  await expect(dialog.getByLabel("Reason for correction")).not.toBeVisible();
  await expect(
    dialog.getByText(parameter.raw_text, { exact: true }).first(),
  ).toBeVisible();
  await dialog.getByRole("button", { name: "Cancel", exact: true }).click();
  const after = (await (await request.get(path)).json()) as SpecificationData;
  expect(after.run).toEqual(original.run);
  expect(after.facts_version).toBe(original.facts_version);
  expect(mutations).toBe(0);
  const size = await page.evaluate(() => ({
    width: document.documentElement.scrollWidth,
    viewport: innerWidth,
  }));
  expect(size.width).toBeLessThanOrEqual(size.viewport + 1);
});

test("batch actions use a secondary dialog and cancelling cannot submit review", async ({
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
  const path = `/api/specifications/${id}`;
  const original = (await (
    await request.get(path)
  ).json()) as SpecificationData;
  await page.goto(`/specifications?document=${id}`);
  await expect(page.getByText("Batch review", { exact: true })).toHaveCount(0);
  await page.getByRole("button", { name: "Batch review", exact: true }).click();
  const dialog = page.getByRole("dialog", {
    name: "Review selected tests",
    exact: true,
  });
  await expect(dialog).toBeVisible();
  for (const item of original.run!.extraction.requirements.slice(0, 2)) {
    await dialog
      .getByRole("checkbox", { name: item.original_test_name, exact: true })
      .check();
  }
  const confirm = dialog.getByRole("button", {
    name: "Confirm selected",
    exact: true,
  });
  await expect(confirm).toBeDisabled();
  await dialog
    .getByRole("checkbox", {
      name: "I checked every selected record against the PDF",
      exact: true,
    })
    .check();
  await expect(confirm).toBeEnabled();
  await dialog
    .getByRole("button", { name: "Close batch review", exact: true })
    .click();
  await expect(dialog).not.toBeVisible();
  const after = (await (await request.get(path)).json()) as SpecificationData;
  expect(after.review).toEqual(original.review);
  expect(after.run).toEqual(original.run);
});

test("an optional review note can accompany a focused correction without changing the detailed facts", async ({
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
  const path = `/api/specifications/${id}`;
  const original = (await (
    await request.get(path)
  ).json()) as SpecificationData;
  const selected = original.run!.extraction.requirements[0];
  await page.goto(`/specifications?document=${id}&requirement=${selected.id}`);
  await page
    .getByRole("button", { name: "Correct extracted content", exact: true })
    .click();
  const dialog = page.getByRole("dialog", {
    name: "Correct extracted content",
    exact: true,
  });
  await dialog
    .getByLabel("Test name")
    .fill(`${selected.original_test_name} (checked)`);
  await expect(dialog.getByLabel("Reason for correction")).not.toBeVisible();
  await dialog
    .getByRole("button", { name: "Add a review note (optional)", exact: true })
    .click();
  await dialog
    .getByLabel("Reason for correction")
    .fill("Checked the test label against the PDF.");
  const sent = page.waitForRequest(
    (sent) => sent.method() === "POST" && sent.url().endsWith(`${path}/facts`),
  );
  await dialog
    .getByRole("button", { name: "Save corrected draft", exact: true })
    .click();
  expect((await sent).postDataJSON().note).toBe(
    "Checked the test label against the PDF.",
  );
  await expect(dialog).not.toBeVisible();
  const after = (await (await request.get(path)).json()) as SpecificationData;
  expect(after.run!.extraction.requirements[0].original_test_name).toBe(
    `${selected.original_test_name} (checked)`,
  );
  expect(after.run!.extraction.requirements[0].atomic_claims).toEqual(
    selected.atomic_claims,
  );
  expect(after.run!.raw_response).toEqual(original.run!.raw_response);
  expect(after.facts_version).toBe((original.facts_version ?? 0) + 1);
});
