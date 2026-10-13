import { type Page, expect, test } from "@playwright/test";
import en from "../messages/en.json" with { type: "json" };
import { SCHEMA_VERSION } from "../lib/schema";

/**
 * Release smoke test: the published images and data pack, as a new user gets them
 * (make quickstart, no model key). Runs against a running stack:
 *   LIX_SMOKE=1 LIX_E2E_STACK=docker npx playwright test smoke
 * Unlike app.spec.ts it needs no test hooks in the build.
 */
test.skip(
  !process.env.LIX_SMOKE,
  "release smoke test: set LIX_SMOKE=1 with the stack running",
);

async function open(page: Page) {
  await page.goto("/");
  await expect(page.getByTestId("legend")).toBeVisible({ timeout: 60_000 });
  await expect(page.getByTestId("map-loading")).toBeHidden({ timeout: 60_000 });
}

async function ask(page: Page, text: string) {
  await page.getByTestId("tab-assistant").click();
  const input = page.getByTestId("chat").locator("textarea").first();
  await input.fill(text);
  await input.press("Enter");
  const chat = page.getByTestId("copilot-chat");
  await expect(chat)
    .toHaveAttribute("data-copilot-running", "true", { timeout: 10_000 })
    .catch(() => {});
  await expect(chat).toHaveAttribute("data-copilot-running", "false", {
    timeout: 60_000,
  });
}

test("the API and the data pack agree on the build", async ({ request }) => {
  const health = await (await request.get("/health")).json();
  expect(health.status).toBe("ok");
  const manifest = await (await request.get("/data/manifest.json")).json();
  expect(manifest.schema_version).toBe(SCHEMA_VERSION);
  expect(health.lsoas).toBe(manifest.lsoa_count);
  expect(health.geography.active).toEqual(manifest.geography.active);
  expect(health.version).toMatch(/^\d+\.\d+\.\d+/);
});

test("the map loads and a postcode opens its profile", async ({ page }) => {
  await open(page);
  await page.getByTestId("onboarding").getByText(en.Onboarding.skip).click();
  const search = page.getByTestId("search").locator("input");
  await search.fill("LS6 3AA");
  await expect(page.getByRole("option").first()).toContainText("LS6 3AA", {
    timeout: 15_000,
  });
  await search.press("Enter");
  const profile = page.getByTestId("area-profile").last();
  await expect(profile).toBeVisible({ timeout: 30_000 });
  await expect(profile.getByTestId("theme-bars")).toBeVisible();
  await expect(profile.getByTestId("percentile-range")).toContainText(
    "% of the UK",
  );
});

test("without a key the assistant answers the suggested prompts from recordings", async ({
  page,
  request,
}) => {
  const health = await (await request.get("/health")).json();
  test.skip(!health.assistant.problem, "a model key is configured");
  await open(page);
  await ask(page, "Compare Far Headingley and Chapel Allerton");
  await expect(page.getByTestId("comparison").last()).toBeVisible();
  await ask(page, "Which pub has the best beer in Leeds?");
  await expect(
    page.getByTestId("copilot-assistant-message").last(),
  ).toContainText("isn't configured");
});

test("the method and sources pages render", async ({ page }) => {
  await page.goto("/methodology");
  await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
  await page.goto("/about");
  await expect(
    page.getByText("OpenStreetMap contributors").first(),
  ).toBeVisible();
});
