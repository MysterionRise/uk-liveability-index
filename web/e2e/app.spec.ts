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
    "climate",
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
  // 62,833 areas on the dev server: 170 ms on an M1 Max with the November pack, so the
  // bar catches a regression to seconds, not the build's noise
  expect(last(timings.score) + last(timings.recolour)).toBeLessThan(250);
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
  await expect(bars).toContainText(en.Profile.ukMedian.replace("{in}", "").trim());
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
  // The sources page renders 140-odd registry entries: the dev server's first compile
  // of the route takes longer than the default expectation on a CI runner
  await expect(
    page.getByRole("heading", { name: "Data licence" }),
  ).toBeVisible({ timeout: 30_000 });
  await expect(
    page.getByRole("heading", { name: "Data sources" }),
  ).toBeVisible({ timeout: 30_000 });
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
  await expect(bars).toContainText(en.Profile.ukMedian.replace("{in}", "").trim());
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

// A description of the user becomes a named weighting profile (the scripted model's
// server builds it by matching words)
const DESCRIPTION = "I'm a young professional with one child going into primary school and I have a sun deficit";

test("a sentence about yourself builds a profile that survives a reload and a link", async ({ page, browser }) => {
  await mapReady(page);
  await page.getByTestId("tab-weights").click();
  // The welcome card has a box of its own on a first visit: use the panel's
  const box = page.getByTestId("profile-box");
  await box.getByTestId("profile-input").fill(DESCRIPTION);
  await box.getByTestId("profile-build").click();
  await expect(page.getByTestId("preset-select")).toHaveValue("__profile__", { timeout: 15_000 });
  await expect(page.getByTestId("profile-card")).toContainText("Young professional");
  // young_professional education 0.25 + 1 for the primary school, climate 0.25 + 1 for the sun
  await expect(page.getByTestId("weight-education")).toHaveValue("1.25");
  await expect(page.getByTestId("weight-climate")).toHaveValue("1.25");
  expect(page.url()).toContain(encodeURIComponent('"n":'));
  expect(decodeURIComponent(page.url())).not.toContain("sun deficit");

  // Kept in this browser
  await page.reload();
  await expect(page.getByTestId("legend")).toBeVisible({ timeout: 30_000 });
  await page.getByTestId("tab-weights").click();
  await expect(page.getByTestId("preset-select")).toHaveValue("__profile__");
  await expect(page.getByTestId("weight-education")).toHaveValue("1.25");

  // The link carries the weights and the name to a browser that has never seen it
  const fresh = await browser.newContext();
  const other = await fresh.newPage();
  await other.goto(page.url());
  await expect(other.getByTestId("legend")).toBeVisible({ timeout: 30_000 });
  await other.getByTestId("tab-weights").click();
  await expect(other.getByTestId("preset-select")).toHaveValue("__profile__");
  await expect(other.getByTestId("weight-climate")).toHaveValue("1.25");
  await expect(other.getByTestId("preset-select").locator("option[value='__profile__']")).toContainText("Young professional");
  await fresh.close();

  // Clearing it goes back to the base preset
  await page.getByTestId("profile-clear").click();
  await expect(page.getByTestId("preset-select")).toHaveValue("young_professional");
  await expect(page.getByTestId("weight-education")).toHaveValue("0.25");
});

test("the welcome card's description box builds a profile and opens the weights", async ({ page }) => {
  await mapReady(page);
  const box = page.getByTestId("onboarding").getByTestId("profile-input");
  await box.fill("Retired, my wife has asthma, we want somewhere quiet");
  await box.press("Enter");
  await expect(page.getByTestId("onboarding")).toBeHidden({ timeout: 15_000 });
  await expect(page.getByTestId("tab-weights")).toHaveAttribute("aria-selected", "true");
  await expect(page.getByTestId("preset-select")).toHaveValue("__profile__");
  // retiree environment 1.5 + 1 for the asthma
  await expect(page.getByTestId("weight-environment")).toHaveValue("2.5");
});

test("the assistant builds a profile from how you describe yourself", async ({ page }) => {
  await mapReady(page);
  await ask(page, "I'm retired and have asthma");
  await expect(page.getByText(en.Registry.profileSet)).toBeVisible({ timeout: 30_000 });
  await page.getByTestId("tab-weights").click();
  await expect(page.getByTestId("preset-select")).toHaveValue("__profile__");
  await expect(page.getByTestId("weight-environment")).toHaveValue("2.5");
  await expect(page.getByTestId("profile-card")).toContainText("Retired");
});

test("an indicator only one nation has is compared among that nation's areas", async ({ page }) => {
  await mapReady(page);
  await page.getByTestId("tab-weights").click();
  // Domestic rates exist only in Northern Ireland: the legend says so (#136)
  await page.getByTestId("layer-select").selectOption("indicator:domestic_rates");
  await expect(page.getByTestId("legend")).toContainText("Against every area in Northern Ireland");
  await page.getByTestId("layer-select").selectOption("overall");
  await expect(page.getByTestId("legend")).toContainText("Against every area in the UK");
});

test("a context measure colours the map and says so in the legend", async ({ page }) => {
  await mapReady(page);
  await page.getByTestId("tab-weights").click();
  // Context indicators ride in the browser table under their own group (#133)
  await page.getByTestId("layer-select").selectOption("indicator:rainfall");
  await expect(page.getByTestId("legend")).toContainText(/rain/i);
  await page.getByTestId("layer-select").selectOption("overall");
  await expect(page.getByTestId("legend")).toContainText("Overall");
});

test("a profile compared against its urban/rural class says so", async ({ page }) => {
  await mapReady(page);
  await openFromSearch(page, "LS6 3AA");
  await expect(page.getByTestId("theme-bars")).toBeVisible({ timeout: 30_000 });
  await page.getByTestId("tab-weights").click();
  await page.getByTestId("compare-within").selectOption("urban_rural");
  await page.getByTestId("tab-area").click();
  // The card follows "compare against" for the class as it does for the nation (#131)
  await expect(page.getByText("similar urban/rural areas").first()).toBeVisible();
});

// --- the v0.6.0 QA pass's browser findings ---------------------------------------------

const ALL_ZERO = { safety: 0, environment: 0, health: 0, education: 0, transport: 0, amenities: 0, housing: 0, community: 0, climate: 0 };
const encode = (h: object) => `#${encodeURIComponent(JSON.stringify(h))}`;

test("every theme at 0 leaves the map blank with a notice, never an error", async ({ page }) => {
  // A shared or bookmarked all-zero state used to throw inside the scorer (#181)
  await page.goto(`/${encode({ p: "balanced", w: ALL_ZERO })}`);
  await expect(page.getByTestId("legend")).toBeVisible({ timeout: 30_000 });
  await expect(page.getByTestId("legend")).toContainText(en.Map.noWeights);
  await expect(page.getByText(/couldn't load/i)).toHaveCount(0);
  // The sliders cannot reach it: the last theme with weight stays at a quarter
  await page.goto("/");
  await expect(page.getByTestId("legend")).toBeVisible({ timeout: 30_000 });
  await page.getByTestId("tab-weights").click();
  for (const theme of Object.keys(ALL_ZERO)) await page.getByTestId(`weight-${theme}`).fill("0");
  await expect(page.getByTestId("weight-climate")).toHaveValue("0.25");
});

const SAVED = [
  { code: "E01011350", name: "Leeds 034A", centre: { lat: 53.829, lon: -1.539 } },
  { code: "I01018469", name: "Rathmines East D 005", centre: { lat: 53.326, lon: -6.256 } },
  { code: "N21000251", name: "Botanic A", centre: { lat: 54.584, lon: -5.934 } },
];

test("a saved shortlist survives adding an area before the tab is opened", async ({ page }) => {
  // Seeded once: the app's own writes must survive the reload below
  await page.addInitScript((saved) => {
    if (!localStorage.getItem("lix.shortlist")) localStorage.setItem("lix.shortlist", JSON.stringify(saved));
  }, SAVED);
  await mapReady(page);
  // The tab counts the saved areas from the start (#182)
  await expect(page.getByTestId("tab-shortlist")).toContainText("3");
  // A saved area's card knows it is saved; a new one can be added before the tab is opened
  await openFromSearch(page, "LS7 3DJ");
  await expect(page.getByTestId("area-profile")).toContainText(en.Profile.inShortlist, { timeout: 30_000 });
  await openFromSearch(page, "LS6 3AA");
  await page.getByTestId("area-profile").getByRole("button", { name: en.Profile.addToShortlist }).click();
  await page.getByTestId("tab-shortlist").click();
  await expect(page.getByTestId("shortlist").locator("li")).toHaveCount(4);
  await page.reload();
  await expect(page.getByTestId("legend")).toBeVisible({ timeout: 30_000 });
  await expect(page.getByTestId("tab-shortlist")).toContainText("4");
});

test("shortlisted Irish and Northern Irish areas select on click", async ({ page }) => {
  await page.addInitScript((saved) => {
    if (!localStorage.getItem("lix.shortlist")) localStorage.setItem("lix.shortlist", JSON.stringify(saved));
  }, SAVED);
  await mapReady(page);
  await page.getByTestId("tab-shortlist").click();
  for (const [index, code] of [[1, "I01018469"], [2, "N21000251"]] as const) {
    await page.getByTestId("shortlist").locator("li").nth(index).locator("button").first().click();
    // The code test accepted E01/W01/S01/N01 only (#183)
    await expect.poll(() => decodeURIComponent(page.url().split("#")[1] ?? "")).toContain(code);
    await expect(page.getByTestId("tab-area")).toHaveAttribute("aria-selected", "true");
    await page.getByTestId("tab-shortlist").click();
  }
});

test("a shared link with an area opens its profile there, in Ireland's own terms", async ({ page }) => {
  // The hash carries no view: the recipient used to land on the default map (#184)
  await page.goto(`/${encode({ p: "balanced", s: "I01018469" })}`);
  await expect(page.getByTestId("tab-area")).toHaveAttribute("aria-selected", "true", { timeout: 30_000 });
  const profile = page.getByTestId("area-profile");
  await expect(profile).toContainText("Rathmines East D", { timeout: 30_000 });
  await expect.poll(() => page.evaluate(() => (window as any).__map?.getZoom?.() ?? 0), { timeout: 30_000 }).toBeGreaterThan(8);
  // An Irish area's headline compares within Ireland (#147); a Leeds one across the build
  await expect(profile).toContainText("of Ireland");
  await page.goto(`/${encode({ p: "balanced", s: "E01011350" })}`);
  await expect(page.getByTestId("area-profile")).toContainText("of the UK and Ireland", { timeout: 30_000 });
});

test("the first click on the map opens the area's profile", async ({ page }) => {
  await mapReady(page);
  await page.evaluate(() => (window as any).__map.jumpTo({ center: [-1.539, 53.829], zoom: 12 }));
  await page.waitForFunction(() => (window as any).__map.loaded() && !(window as any).__map.isMoving());
  await page.waitForTimeout(1500);
  const box = (await page.getByTestId("map").boundingBox())!;
  await page.mouse.click(box.x + box.width / 2, box.y + box.height / 2);
  // The Assistant tab was open: the profile still shows (#184)
  await expect(page.getByTestId("tab-area")).toHaveAttribute("aria-selected", "true", { timeout: 15_000 });
  await expect(page.getByTestId("area-profile")).toBeVisible({ timeout: 30_000 });
});

test("in-page anchors in the docs resolve", async ({ page }) => {
  // Headings carried no ids, so the methodology's own "#…" links went nowhere (#163)
  await page.goto("/methodology");
  await expect(page.locator("article.doc h2").first()).toBeVisible({ timeout: 30_000 });
  const missing = await page.evaluate(() =>
    [...document.querySelectorAll<HTMLAnchorElement>("article.doc a[href^='#']")]
      .map((a) => decodeURIComponent(a.getAttribute("href")!.slice(1)))
      .filter((id) => id && !document.getElementById(id)),
  );
  expect(missing).toEqual([]);
});

test("the map credits the Irish boundaries", async ({ page }) => {
  await mapReady(page);
  // The attribution control is built from the manifest's boundary sources (#186)
  await expect(page.locator(".maplibregl-ctrl-attrib")).toContainText("Tailte Éireann", { timeout: 15_000 });
});

// ---------------------------------------------------------------- v0.6.3

test("the Welsh interface shows no English in a profile, the search list or the build date", async ({ page }) => {
  await mapReady(page);
  await page.getByTestId("locale-toggle").selectOption("cy");
  await expect(page.locator("html")).toHaveAttribute("lang", "cy-GB");
  // Search results word the settlement type and the council in Welsh (#187)
  const input = page.getByTestId("search").locator("input");
  await input.fill("Cardiff");
  const options = page.getByRole("option");
  await expect(options.first()).toBeVisible({ timeout: 15_000 });
  await expect(options.first()).not.toContainText(/\b(City|Town|Village)\b/);
  await input.fill("");
  // The card's subtitle carries the urban/rural class from the Welsh catalogue
  await openFromSearch(page, "LS6 3AA");
  const card = page.getByTestId("area-profile");
  await expect(card).toBeVisible({ timeout: 30_000 });
  await expect(card).not.toContainText(/\bUrban: |\bRural: /);
  await expect(card).toContainText(/Trefol|Gwledig/);
  // Dates print from the catalogue's month names, whatever the browser knows
  await page.goto("/about");
  await expect(page.getByTestId("build-info")).toContainText(/\d/);
  await expect(page.getByTestId("build-info")).not.toContainText(
    /January|February|March|April|May|June|July|August|September|October|November|December/,
  );
  await page.goto("/");  // the toggle lives on the map page
  await page.getByTestId("locale-toggle").selectOption("en");
});

test("the About page loads without the scores file", async ({ page }) => {
  const parquet: string[] = [];
  page.on("request", (r) => {
    if (r.url().includes("scores.parquet")) parquet.push(r.url());
  });
  await page.goto("/about");
  await expect(page.getByTestId("build-info")).toContainText(/data built/, { timeout: 30_000 });
  expect(parquet, "the docs pages must not download the pack (#188)").toEqual([]);
});

test("printing hides the map chrome and keeps the cards", async ({ page }) => {
  await mapReady(page);
  await page.emulateMedia({ media: "print" });
  await expect(page.getByTestId("search")).toBeHidden();
  // The map prints as a blank (WebGL), so it and its legend are left out
  await expect(page.getByTestId("legend")).toBeHidden();
  await expect(page.getByTestId("side-panel")).toBeVisible();
  await page.emulateMedia({ media: "screen" });
});

test("a postcode district opens a neighbourhood inside it", async ({ page }) => {
  await mapReady(page);
  const input = page.getByTestId("search").locator("input");
  await input.fill("LS6");
  const option = page.getByRole("option").first();
  await expect(option).toBeVisible({ timeout: 15_000 });
  await expect(option).toContainText(en.Search.kinds.postcode_district);
  await option.click();
  await expect(page.getByTestId("area-profile")).toBeVisible({ timeout: 30_000 });
});

test.describe("on a phone, the legend", () => {
  test.use({ viewport: { width: 390, height: 844 } });

  test("starts folded and opens on a tap", async ({ page }) => {
    await mapReady(page);
    // The welcome card covers the legend at this width until it is dismissed
    await page.getByTestId("onboarding").getByRole("button", { name: en.Onboarding.skip }).click();
    await expect(page.getByTestId("onboarding")).toBeHidden();
    await expect(page.getByTestId("legend-body")).toBeHidden();
    await page.getByTestId("legend-toggle").click();
    await expect(page.getByTestId("legend-body")).toBeVisible();
    await expect(page.getByTestId("legend-toggle")).toHaveAttribute("aria-expanded", "true");
  });
});
