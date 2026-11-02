import { expect, test } from "@playwright/test";
import type { SpecificationData, Workspace } from "../src/types";

const base = "http://127.0.0.1:8011";

for (const change of ["document", "run"] as const) {
  test(`batch review resets when the ${change} changes with overlapping requirement IDs`, async ({
    page,
    request,
  }) => {
    const workspaceResponse = await request.get(`${base}/api/workspace`);
    expect(workspaceResponse.ok()).toBeTruthy();
    const workspace: Workspace = await workspaceResponse.json();
    const first = workspace.documents.find(
      (doc) => doc.original_name === "offline-two-test-spec.pdf",
    )!;
    const second = workspace.documents.find(
      (doc) => doc.document_id !== first.document_id,
    )!;
    const sourceResponse = await request.get(
      `${base}/api/specifications/${first.document_id}`,
    );
    expect(sourceResponse.ok()).toBeTruthy();
    const source: SpecificationData = await sourceResponse.json();
    const requirements = source.run!.extraction.requirements;
    expect(requirements.length).toBeGreaterThanOrEqual(2);

    // Keep both requirement IDs and the run ID identical across documents.
    // For extraction, only change the run ID so that each reset is independent.
    const updated = structuredClone(source);
    if (change === "document") {
      updated.document_id = second.document_id;
      await page.route(`**/api/specifications/${second.document_id}`, (route) =>
        route.fulfill({ json: updated }),
      );
    } else {
      updated.run!.run_id = `${source.run!.run_id}-rerun`;
      await page.route("**/api/workspace", (route) =>
        route.fulfill({
          json: {
            ...workspace,
            providers: workspace.providers.map((provider) => ({
              ...provider,
              available: provider.id === "groq",
            })),
          },
        }),
      );
      await page.route(
        `**/api/specifications/${first.document_id}/analysis`,
        (route) => route.fulfill({ json: updated }),
      );
    }

    await page.goto(`${base}/specifications?document=${first.document_id}`);
    await page
      .getByRole("button", { name: "Batch review", exact: true })
      .click();
    const boxes = requirements.slice(0, 2).map((item) =>
      page.getByRole("checkbox", {
        name: item.original_test_name,
        exact: true,
      }),
    );
    for (const box of boxes) await box.check();
    const note = page.getByLabel("Batch note / exclusion reason");
    await note.fill("Checked the previous source against its PDF");
    const acknowledgement = page.getByRole("checkbox", {
      name: "I checked every selected record against the PDF",
    });
    await acknowledgement.check();
    const confirm = page.getByRole("button", { name: "Confirm selected" });
    const exclude = page.getByRole("button", { name: "Exclude selected" });
    await expect(confirm).toBeEnabled();
    await expect(exclude).toBeEnabled();

    if (change === "document") {
      await page.getByRole("combobox", { name: "Saved specification" }).click();
      await page.getByRole("option", { name: second.original_name }).click();
      // Loading a document remounts the collapsed batch panel.
      await page
        .getByRole("button", { name: "Batch review", exact: true })
        .click();
    } else {
      await page.getByRole("button", { name: /Analysis settings/ }).click();
      await page
        .getByRole("checkbox", { name: /I have permission to send/ })
        .check();
      await page.getByRole("button", { name: "Analyze again" }).click();
      await expect(
        page.getByRole("button", { name: "Analyze again" }),
      ).toBeEnabled();
    }

    for (const box of boxes) await expect(box).not.toBeChecked();
    await expect(note).toHaveValue("");
    await expect(confirm).toBeDisabled();
    await expect(exclude).toBeDisabled();

    // Reselecting the same IDs must require fresh acknowledgement as well.
    for (const box of boxes) await box.check();
    await expect(acknowledgement).not.toBeChecked();
    await expect(confirm).toBeDisabled();
    await acknowledgement.check();
    await expect(confirm).toBeEnabled();
    await expect(exclude).toBeDisabled();
  });
}
