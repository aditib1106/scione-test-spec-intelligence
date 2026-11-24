import { expect, test, type Page } from "@playwright/test";
import type { LibraryDocument } from "../src/libraryTypes";

function specification(index: number): LibraryDocument {
  return {
    document_id: `layout-spec-${index}`,
    company_name: index % 2 ? "Customer Alpha" : "Customer Beta",
    specification_id: `LAYOUT-${String(index).padStart(3, "0")}`,
    title: `Exterior coating qualification ${String(index).padStart(3, "0")}`,
    revision: null,
    specification_date: "2026-11-24",
    family_id: `layout-family-${index}`,
    version: 1,
    original_name: `qualification-${index}.pdf`,
    created_at: "2026-11-24T12:00:00Z",
    updated_at: "2026-11-24T12:00:00Z",
    requirement_count: 4,
    review_count: 1,
    run_id: `layout-run-${index}`,
    page_count: 2,
  };
}

async function noPageOverflow(page: Page) {
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= window.innerWidth + 1,
    ),
  ).toBe(true);
}

const viewports = [
  { width: 1440, height: 1000 },
  { width: 1366, height: 768 },
  { width: 2560, height: 1440 },
  { width: 390, height: 844 },
];

for (const viewport of viewports) {
  test(`library fits its content and keeps long lists usable at ${viewport.width}×${viewport.height}`, async ({
    page,
  }, testInfo) => {
    test.skip(
      viewport.width < 600 !== (testInfo.project.name === "narrow"),
      "Run each exact viewport once in its appropriate responsive project.",
    );
    await page.setViewportSize(viewport);
    let documents: LibraryDocument[] = [];
    const writes: string[] = [];
    page.on("request", (sent) => {
      if (
        new URL(sent.url()).pathname.startsWith("/api/") &&
        sent.method() !== "GET"
      )
        writes.push(`${sent.method()} ${new URL(sent.url()).pathname}`);
    });
    await page.route("**/api/library**", async (route) => {
      const path = new URL(route.request().url()).pathname;
      if (route.request().method() !== "GET")
        return route.fulfill({ status: 400, json: { detail: "No writes" } });
      if (path === "/api/library")
        return route.fulfill({ json: { documents } });
      const selected = documents.find(
        (item) => path === `/api/library/${item.document_id}`,
      );
      return selected
        ? route.fulfill({
            json: {
              document: selected,
              versions: documents.filter(
                (item) => item.family_id === selected.family_id,
              ),
              requirements: [],
            },
          })
        : route.fulfill({ status: 404, json: { detail: "Not found" } });
    });

    // Empty state is intentionally explanatory, without a viewport-height slab.
    await page.goto("/specifications/library");
    const card = page.getByRole("region", {
      name: "Saved specification list",
      exact: true,
    });
    await expect(
      card.getByRole("heading", {
        name: "Your customer specifications, in one place",
        exact: true,
      }),
    ).toBeVisible();
    expect((await card.boundingBox())!.height).toBeLessThan(
      viewport.width < 600 ? 520 : 410,
    );
    await page
      .getByRole("button", { name: "Add specification", exact: true })
      .click();
    const add = page.getByRole("dialog", {
      name: "Add customer specification",
      exact: true,
    });
    await expect(add.getByLabel("Save under", { exact: true })).toBeVisible();
    await add.getByRole("button", { name: "Cancel", exact: true }).click();
    expect(writes).toEqual([]);

    // A single family, including its two dated versions, determines its own height.
    const latest = specification(1);
    const earlier = {
      ...latest,
      document_id: "layout-spec-earlier",
      specification_date: "2026-10-20",
      original_name: "qualification-earlier.pdf",
    };
    documents = [latest, earlier];
    await page.reload();
    const table = page.getByRole("table", {
      name: "Saved customer specifications",
      exact: true,
    });
    await expect(table.getByRole("row")).toHaveCount(2);
    const scroller = page.getByRole("region", {
      name: "Specification table scroll area",
      exact: true,
    });
    const emptySpace = await scroller.evaluate(
      (element) =>
        element.clientHeight - element.querySelector("table")!.clientHeight,
    );
    expect(emptySpace).toBeLessThanOrEqual(2);
    expect((await scroller.boundingBox())!.height).toBeLessThan(235);
    const latestRow = table.getByRole("row").nth(1);
    await expect(
      latestRow.getByRole("link", {
        name: `Review ${latest.title}`,
        exact: true,
      }),
    ).toHaveAttribute("href", `/specifications?document=${latest.document_id}`);
    await latestRow
      .getByRole("button", { name: "2 versions", exact: true })
      .click();
    const versions = page.getByRole("dialog", {
      name: "Specification versions",
      exact: true,
    });
    await expect(
      versions.getByRole("list", { name: "Saved specification versions" }),
    ).toContainText(earlier.original_name);
    await versions.getByRole("button", { name: "Close", exact: true }).click();
    await latestRow
      .getByRole("button", { name: `Remove ${latest.title}`, exact: true })
      .click();
    const remove = page.getByRole("dialog", {
      name: "Remove this specification?",
      exact: true,
    });
    await remove.getByRole("button", { name: "Cancel", exact: true }).click();
    expect(writes).toEqual([]);
    await noPageOverflow(page);
    await page.screenshot({
      path: testInfo.outputPath(`library-one-family-${viewport.width}.png`),
      animations: "disabled",
    });

    // Pagination works on families, not versions; search and company filtering
    // continue to use the complete library rather than only the visible page.
    documents = Array.from({ length: 31 }, (_, index) =>
      specification(index + 1),
    );
    documents[0] = {
      ...documents[0],
      company_name:
        "A company with a lengthy legal name and a materials division",
      title:
        "Production coating requirements for exterior trim assemblies with a long specification title",
      original_name: `${"long-filename-".repeat(9)}.pdf`,
    };
    await page.reload();
    await expect(table.getByRole("row")).toHaveCount(11);
    await expect(card.getByText("1–10 of 31", { exact: true })).toBeVisible();
    await card
      .getByRole("button", { name: "Go to next page", exact: true })
      .click();
    await expect(card.getByText("11–20 of 31", { exact: true })).toBeVisible();
    await expect(table.getByRole("row")).toHaveCount(11);
    const search = page.getByRole("textbox", {
      name: "Search specification library",
      exact: true,
    });
    await search.fill("qualification-31.pdf");
    await expect(table.getByRole("row")).toHaveCount(2);
    await expect(table).toContainText("LAYOUT-031");
    await expect(
      card.getByRole("button", { name: "Go to next page" }),
    ).toHaveCount(0);
    await search.clear();
    await expect(card.getByText("1–10 of 31", { exact: true })).toBeVisible();
    await page
      .getByRole("combobox", { name: "Customer company", exact: true })
      .click();
    await page
      .getByRole("option", { name: "Customer Beta", exact: true })
      .click();
    await expect(card.getByText("1–10 of 15", { exact: true })).toBeVisible();
    await expect(table).not.toContainText("Customer Alpha");
    await card
      .getByRole("button", { name: "Go to next page", exact: true })
      .click();
    await expect(card.getByText("11–15 of 15", { exact: true })).toBeVisible();
    await expect(table.getByRole("row")).toHaveCount(6);
    await noPageOverflow(page);
    expect(writes).toEqual([]);
    await page.screenshot({
      path: testInfo.outputPath(`library-many-families-${viewport.width}.png`),
      animations: "disabled",
    });
  });
}
