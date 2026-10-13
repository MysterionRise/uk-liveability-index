import { type Page, expect, test } from "@playwright/test";
import en from "../messages/en.json" with { type: "json" };

// A known LSOA (Leeds 034A, Chapel Allerton) to watch the map colour of; it is in both
// the full build and the demo dataset (Leeds + Brighton) CI runs on
const LSOA = "E01011350";

async function mapReady(page: Page) {
  await page.goto("/");
  await expect(page.getByTestId("legend")).toBeVisible({ timeout: 30_000 });
  await page.waitForFunction(
    (code) => {
      const m = (window as any).__map;
      return (
        m &&
        m.getSource("lsoa") &&
        m.getFeatureState({ source: "lsoa", sourceLayer: "lsoa", id: code })
          ?.v != null
      );
    },
    LSOA,
    { timeout: 30_000 },
  );
}

const featureValue = (page: Page, code: string) =>
  page.evaluate(
    (c) =>
      (window as any).__map.getFeatureState({
        source: "lsoa",
        sourceLayer: "lsoa",
        id: c,
      })?.v,
    code,
  );

async function ask(page: Page, text: string) {
  const input = page.getByTestId("chat").locator("textarea").first();
  await input.fill(text);
  await input.press("Enter");
}

test("moving a weight slider recolours the map", async ({ page }) => {
  await mapReady(page);
  const before = await featureValue(page, LSOA);
  await page.getByTestId("tab-weights").click();
  // Only safety counts
  for (const theme of [
    "environment",
    "health",
    "education",
    "transport",
    "amenities",
    "housing",
    "community",
  ]) {
    await page.getByTestId(`weight-${theme}`).fill("0");
  }
  await expect.poll(() => featureValue(page, LSOA)).not.toBe(before);
});

test("recolouring all areas is fast", async ({ page }) => {
  await mapReady(page);
  await page.getByTestId("tab-weights").click();
  await page.getByTestId("weight-safety").fill("3");
  await page.waitForTimeout(500);
  const timings = await page.evaluate(() => ({
    score: performance.getEntriesByName("lix-score").map((e) => e.duration),
    recolour: performance
      .getEntriesByName("lix-recolour")
      .map((e) => e.duration),
  }));
  const last = (xs: number[]) => xs[xs.length - 1];
  console.log(
    "score ms",
    last(timings.score),
    "recolour ms",
    last(timings.recolour),
  );
  expect(last(timings.score) + last(timings.recolour)).toBeLessThan(150);
});

test("the assistant's set_weights moves the sliders", async ({ page }) => {
  await mapReady(page);
  await ask(page, "Use family weights please");
  await expect(page.getByText(en.Registry.weightsSet)).toBeVisible({
    timeout: 30_000,
  });
  await page.getByTestId("tab-weights").click();
  await expect(page.getByTestId("preset-select")).toHaveValue("family");
  // Family preset weights education 2.5
  await expect(page.getByTestId("weight-education")).toHaveValue("2.5");
});

test("a ranking renders a card and outlines the areas on the map", async ({
  page,
}) => {
  await mapReady(page);
  await ask(page, "What are the best areas in Leeds?");
  const card = page.getByTestId("ranked-list");
  await expect(card).toBeVisible({ timeout: 30_000 });
  await expect(card.locator("li")).toHaveCount(5);
  await expect
    .poll(() =>
      page.evaluate(() =>
        JSON.stringify((window as any).__map.getFilter("msoa-highlight")),
      ),
    )
    .toMatch(/E02\d{6}/);
  // Clicking a result selects it and opens nothing else in the chat
  await card.locator("li button").first().click();
  await expect
    .poll(() => page.evaluate(() => (window as any).__map.getZoom()))
    .toBeGreaterThan(10);
});

test("area profile, comparison and pubs render as components", async ({
  page,
}) => {
  await mapReady(page);
  await ask(page, "Tell me about Headingley");
  await expect(page.getByTestId("area-profile").first()).toContainText(
    "Headingley",
    { timeout: 30_000 },
  );
  await ask(page, "Compare Headingley and Chapel Allerton");
  await expect(page.getByTestId("comparison")).toContainText(
    en.Comparison.title,
    { timeout: 30_000 },
  );
  await ask(page, "Show me well-run pubs near LS6 3AA");
  await expect(page.getByTestId("poi-list")).toContainText(
    en.Poi.titles.well_run_pub,
    { timeout: 30_000 },
  );
  await expect
    .poll(() =>
      page.evaluate(
        () =>
          (window as any).__map.getSource("pois").serialize().data.features
            .length,
      ),
    )
    .toBeGreaterThan(0);
});

test("analyst mode runs guarded SQL", async ({ page }) => {
  await mapReady(page);
  await page.getByTestId("analyst-toggle").check();
  await page.getByTestId("tab-analyst").click();
  await expect(page.getByTestId("histogram")).toBeVisible();
  await page.getByRole("button", { name: en.Analyst.run }).click();
  await expect(page.getByTestId("sql-result")).toContainText("rows", {
    timeout: 15_000,
  });
  await page.locator("#sql").fill("COPY lsoa TO '/tmp/x.csv'");
  await page.getByRole("button", { name: en.Analyst.run }).click();
  await expect(page.getByTestId("analyst").getByRole("alert")).toContainText(
    "Only SELECT",
  );
});

async function openFromSearch(page: Page, text: string) {
  const input = page.getByTestId("search").locator("input");
  await input.fill(text);
  await expect(page.getByRole("option").first()).toContainText(text, {
    timeout: 15_000,
  });
  await input.press("Enter");
}

test("searching a postcode opens its profile with benchmarks", async ({
  page,
}) => {
  await mapReady(page);
  await openFromSearch(page, "LS6 3AA");
  await expect(page.getByTestId("tab-area")).toHaveAttribute(
    "aria-selected",
    "true",
  );
  const bars = page.getByTestId("theme-bars");
  await expect(bars).toBeVisible({ timeout: 30_000 });
  await expect(bars).toContainText(en.Profile.ukMedian);
});

test("the welcome card's personas set the weights", async ({ page }) => {
  await mapReady(page);
  await page.getByTestId("persona-retiree").click();
  await expect(page.getByTestId("onboarding")).toBeHidden();
  await page.getByTestId("tab-weights").click();
  await expect(page.getByTestId("preset-select")).toHaveValue("retiree");
  // Dismissed for good in this browser
  await page.reload();
  await expect(page.getByTestId("legend")).toBeVisible({ timeout: 30_000 });
  await expect(page.getByTestId("onboarding")).toBeHidden();
});

test("two shortlisted areas can be compared", async ({ page }) => {
  await mapReady(page);
  for (const postcode of ["LS6 3AA", "LS7 3DJ"]) {
    await openFromSearch(page, postcode);
    await page
      .getByTestId("area-profile")
      .getByRole("button", { name: en.Profile.addToShortlist })
      .click();
  }
  await page.getByTestId("tab-shortlist").click();
  await page.getByTestId("compare-shortlist").click();
  await expect(page.getByTestId("comparison")).toContainText(
    en.Comparison.title,
    { timeout: 15_000 },
  );
});

test("the method and sources pages render the docs", async ({ page }) => {
  await page.goto("/methodology");
  await expect(page.getByRole("heading", { level: 1 })).toContainText(
    "Methodology",
  );
  await page.getByRole("link", { name: en.Docs.sources }).click();
  await expect(
    page.getByRole("heading", { name: "Data licence" }),
  ).toBeVisible();
  await expect(
    page.getByRole("heading", { name: "Data sources" }),
  ).toBeVisible();
  await expect(page.locator("article").nth(1)).toContainText("OGL-3.0");
  await expect(page.getByTestId("build-info")).toContainText(
    /Version \d+\.\d+\.\d+ · data built/,
  );
});

test.describe("on a phone", () => {
  test.use({ viewport: { width: 390, height: 844 } });

  test("fits the screen and search still opens a profile", async ({ page }) => {
    await mapReady(page);
    const overflow = await page.evaluate(
      () => document.documentElement.scrollWidth - window.innerWidth,
    );
    expect(overflow).toBeLessThanOrEqual(0);
    await page.getByRole("button", { name: en.Onboarding.skip }).click();
    await openFromSearch(page, "LS6 3AA");
    await expect(page.getByTestId("area-profile")).toBeVisible({
      timeout: 30_000,
    });
  });
});

test("a Scottish postcode opens a data zone profile", async ({ page }) => {
  await mapReady(page);
  await openFromSearch(page, "EH3 9SR");
  await expect(page.getByTestId("tab-area")).toHaveAttribute(
    "aria-selected",
    "true",
  );
  const bars = page.getByTestId("theme-bars");
  await expect(bars).toBeVisible({ timeout: 30_000 });
  await expect(bars).toContainText(en.Profile.ukMedian);
  // Area tiers are named by the nation's words from the manifest
  const input = page.getByTestId("search").locator("input");
  await input.fill("Marchmont");
  await expect(
    page.getByRole("option").filter({ hasText: "Intermediate zone" }).first(),
  ).toBeVisible({
    timeout: 15_000,
  });
});

test("the interface switches to Gàidhlig", async ({ page }) => {
  await mapReady(page);
  await page.getByTestId("locale-toggle").selectOption("gd");
  await expect(page.locator("html")).toHaveAttribute("lang", "gd-GB");
  await expect(page.locator("main header h1")).toContainText(
    "Clàr-amais Càileachd Beatha na RA",
  );
  await expect(page.getByTestId("legend")).toContainText("An aghaidh");
  // Data labels and the tier words come from the Gaelic catalogue in the manifest
  await page.getByTestId("tab-weights").click();
  await expect(page.getByTestId("weights")).toContainText("Sàbhailteachd");
  const input = page.getByTestId("search").locator("input");
  await input.fill("Marchmont");
  await expect(
    page
      .getByRole("option")
      .filter({ hasText: "Sòn eadar-mheadhanach" })
      .first(),
  ).toBeVisible({
    timeout: 15_000,
  });
  // Documentation stays English, and says so in Gaelic
  await page
    .getByRole("link", { name: "Mar a dh'obraicheas na sgòran" })
    .click();
  await expect(page.getByTestId("english-only")).toContainText("Bheurla");
  await page.goto("/");
  await page.getByTestId("locale-toggle").selectOption("en");
});

test("the interface switches to Cymraeg", async ({ page }) => {
  await mapReady(page);
  await page.getByTestId("locale-toggle").selectOption("cy");
  await expect(page.locator("html")).toHaveAttribute("lang", "cy-GB");
  await expect(page.locator("main header h1")).toContainText(
    "Mynegai Byw yn y DU",
  );
  await expect(page.getByTestId("legend")).toContainText("Yn erbyn");
  await expect(page.getByTestId("locale-toggle")).toHaveValue("cy");
  // Data labels come from the Welsh catalogue in the manifest
  await page.getByTestId("tab-weights").click();
  await expect(page.getByTestId("weights")).toContainText("Diogelwch");
  // Documentation stays English, and says so in Welsh
  await page
    .getByRole("link", { name: "Sut mae'r sgoriau'n gweithio" })
    .click();
  await expect(page.getByTestId("english-only")).toContainText("Saesneg");
  // The choice is remembered
  await page.goto("/");
  await expect(page.locator("html")).toHaveAttribute("lang", "cy-GB");
  await page.getByTestId("locale-toggle").selectOption("en");
});
