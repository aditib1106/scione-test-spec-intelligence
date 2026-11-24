import { expect, test } from "@playwright/test";
import type { LibraryDetail, LibraryDocument } from "../src/libraryTypes";

test.use({ actionTimeout: 15_000 });

test("specification actions keep editing and removal explicit, with no writes on cancel", async ({
  page,
}, testInfo) => {
  const latest: LibraryDocument = {
    document_id: "library-action-latest",
    company_name: "Coating customer",
    specification_id: "CSP-EN-301",
    title: "Exterior finish requirements",
    revision: "B",
    specification_date: "2026-11-24",
    family_id: "library-action-family",
    version: 3,
    original_name: "exterior-finish-current.pdf",
    created_at: "2026-11-24T12:00:00Z",
    updated_at: "2026-11-24T12:00:00Z",
    requirement_count: 0,
    review_count: 0,
    run_id: null,
    page_count: 2,
  };
  const earlier = {
    ...latest,
    document_id: "library-action-earlier",
    revision: "A",
    specification_date: "2026-10-01",
    version: 2,
    original_name: "exterior-finish-earlier.pdf",
    created_at: "2026-10-01T12:00:00Z",
    updated_at: "2026-10-01T12:00:00Z",
  };
  let documents = [latest, earlier];
  const writes: { method: string; path: string; body: unknown }[] = [];
  await page.route("**/api/library**", async (route) => {
    const sent = route.request();
    const path = new URL(sent.url()).pathname;
    if (sent.method() !== "GET") {
      writes.push({ method: sent.method(), path, body: sent.postDataJSON() });
      if (sent.method() === "DELETE" && path.endsWith(latest.document_id)) {
        documents = documents.filter(
          (item) => item.document_id !== latest.document_id,
        );
        return route.fulfill({
          json: {
            document_id: latest.document_id,
            archived: true,
            version: latest.version,
          },
        });
      }
      return route.fulfill({
        status: 400,
        json: { detail: "Unexpected write" },
      });
    }
    if (path === "/api/library") return route.fulfill({ json: { documents } });
    const document = documents.find(
      (item) => path === `/api/library/${item.document_id}`,
    );
    if (!document)
      return route.fulfill({ status: 404, json: { detail: "Not found" } });
    const detail: LibraryDetail = {
      document,
      versions: documents,
      requirements: [],
    };
    return route.fulfill({ json: detail });
  });

  await page.goto(`/specifications/library?document=${latest.document_id}`);
  await expect(
    page
      .getByRole("main")
      .getByRole("link", { name: "Read PDF & review", exact: true }),
  ).toHaveAttribute("href", `/specifications?document=${latest.document_id}`);
  await expect(
    page
      .getByRole("main")
      .getByRole("link", { name: "Gap report", exact: true }),
  ).toHaveAttribute("href", `/reports?document=${latest.document_id}`);
  await expect(page.getByRole("button", { name: "Edit details" })).toHaveCount(
    0,
  );
  await expect(
    page.getByRole("link", { name: "Analyze this specification", exact: true }),
  ).toHaveCount(0);
  await expect(
    page.getByRole("button", { name: "Remove specification", exact: true }),
  ).toHaveCount(0);
  const actions = page.getByRole("button", {
    name: "Specification actions",
    exact: true,
  });
  await actions.click();
  // The open menu makes background content aria-hidden while focus is inside it.
  await expect(page.locator("#specification-actions-button")).toHaveAttribute(
    "aria-expanded",
    "true",
  );
  await page
    .getByRole("menuitem", { name: "Edit details", exact: true })
    .click();
  const edit = page.getByRole("dialog", {
    name: "Specification details",
    exact: true,
  });
  await expect(
    edit.getByLabel("Specification number", { exact: true }),
  ).toHaveValue(latest.specification_id!);
  await edit
    .getByLabel("Customer company", { exact: true })
    .fill("Unsaved change");
  await edit.getByRole("button", { name: "Cancel", exact: true }).click();
  await expect(edit).not.toBeVisible();
  expect(writes).toEqual([]);

  await actions.click();
  await page
    .getByRole("menuitem", { name: "Remove from library", exact: true })
    .click();
  const remove = page.getByRole("dialog", {
    name: "Remove this specification?",
    exact: true,
  });
  await expect(remove).toContainText("Other versions remain available");
  await remove.getByRole("button", { name: "Cancel", exact: true }).click();
  expect(writes).toEqual([]);

  // A menu opened for one version must not carry over through browser history.
  await page.getByRole("button", { name: "Versions (2)", exact: true }).click();
  await page
    .getByRole("dialog", { name: "Specification versions", exact: true })
    .getByRole("button", {
      name: "Open version Oct 1, 2026: exterior-finish-earlier.pdf",
      exact: true,
    })
    .click();
  await expect(page).toHaveURL(new RegExp(`document=${earlier.document_id}$`));
  await actions.click();
  await page.goBack();
  await expect(actions).toHaveAttribute("aria-expanded", "false");
  await expect(
    page.getByRole("menuitem", { name: "Remove from library", exact: true }),
  ).toHaveCount(0);
  expect(writes).toEqual([]);

  if (testInfo.project.name === "desktop")
    await page.screenshot({
      path: testInfo.outputPath("library-primary-and-secondary-actions.png"),
      animations: "disabled",
    });
  await actions.click();
  await page
    .getByRole("menuitem", { name: "Remove from library", exact: true })
    .click();
  await remove
    .getByRole("button", { name: "Remove from library", exact: true })
    .click();
  await expect(remove).not.toBeVisible();
  await expect(
    page.getByRole("table", { name: "Saved customer specifications" }),
  ).toContainText(earlier.original_name);
  await expect(
    page.getByRole("table", { name: "Saved customer specifications" }),
  ).not.toContainText(latest.original_name);
  expect(writes).toEqual([
    {
      method: "DELETE",
      path: `/api/library/${latest.document_id}`,
      body: { version: latest.version },
    },
  ]);
});
