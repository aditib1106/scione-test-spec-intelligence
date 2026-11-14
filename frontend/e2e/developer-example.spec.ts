import { expect, test } from "@playwright/test";

test("developer diagnostics no longer offer data replacement shortcuts", async ({
  page,
  request,
}) => {
  const beforeDocuments = await (
    await request.get("/api/specifications")
  ).json();
  const beforeLab = await (await request.get("/api/lab")).json();
  await page.goto("/developer");
  await expect(
    page.getByRole("heading", { name: "Developer Tools", exact: true }),
  ).toBeVisible();
  await expect(
    page.getByRole("button", { name: /Load.*(?:example|demo|walkthrough)/i }),
  ).toHaveCount(0);
  await expect(
    page.getByRole("button", { name: "Synthetic lab example", exact: true }),
  ).toHaveCount(0);
  expect(await (await request.get("/api/lab")).json()).toEqual(beforeLab);
  expect(await (await request.get("/api/specifications")).json()).toEqual(
    beforeDocuments,
  );
});
