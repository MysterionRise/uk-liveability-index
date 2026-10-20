import { type Browser, type Locator, type Page, expect, test } from "@playwright/test";
import fs from "node:fs";
import en from "../messages/en.json" with { type: "json" };

/**
 * Screen recordings of the interface for the README and the release (`make gif`).
 * Run against the Docker stack with the recorded assistant answers:
 *   LIX_RECORD=1 npx playwright test record
 * Each test writes recordings/<name>.webm and <name>.json (the seconds to trim from the
 * start, while the page loads); scripts/make-gif.sh turns them into GIFs. (Not under
 * test-results/, which Playwright empties on every run.)
 */
test.skip(!process.env.LIX_RECORD, "recordings only: set LIX_RECORD=1 (see make gif)");
test.setTimeout(300_000);

const OUT = "recordings";
const SIZE = { width: 1280, height: 800 };

// Playwright's video has no pointer, so draw one: an arrow that follows the mouse and a
// ring where it clicks
const POINTER = `
addEventListener("DOMContentLoaded", () => {
  const arrow = document.createElement("div");
  arrow.innerHTML = '<svg width="22" height="26" viewBox="0 0 22 26"><path d="M1 1 L1 20 L6 15.5 L9.5 24 L13 22.5 L9.5 14.5 L16 14.5 Z" fill="#111" stroke="#fff" stroke-width="1.5" stroke-linejoin="round"/></svg>';
  Object.assign(arrow.style, { position: "fixed", left: "0", top: "0", zIndex: "2147483647",
    pointerEvents: "none", transform: "translate(-100px, -100px)" });
  document.body.appendChild(arrow);
  let x = -100, y = -100;
  addEventListener("mousemove", (e) => {
    x = e.clientX; y = e.clientY;
    arrow.style.transform = "translate(" + (x - 1) + "px, " + (y - 1) + "px)";
  }, true);
  addEventListener("mousedown", () => {
    const ring = document.createElement("div");
    Object.assign(ring.style, { position: "fixed", left: x - 14 + "px", top: y - 14 + "px", width: "28px",
      height: "28px", borderRadius: "50%", border: "3px solid rgba(37, 99, 235, 0.8)", zIndex: "2147483646",
      pointerEvents: "none", transition: "transform 0.45s ease-out, opacity 0.45s ease-out" });
    document.body.appendChild(ring);
    requestAnimationFrame(() => { ring.style.transform = "scale(1.8)"; ring.style.opacity = "0"; });
    setTimeout(() => ring.remove(), 500);
  }, true);
});
`;

interface Recording {
  page: Page;
  /** Marks the moment the page is ready: everything before it is trimmed */
  ready: () => void;
  done: () => Promise<void>;
}

async function start(browser: Browser, name: string, opts: { welcomed?: boolean } = {}): Promise<Recording> {
  const context = await browser.newContext({
    viewport: SIZE,
    recordVideo: { dir: `${OUT}/raw`, size: SIZE },
  });
  await context.addInitScript(POINTER);
  if (opts.welcomed) {
    await context.addInitScript(() => {
      try {
        localStorage.setItem("lix.welcomed", "1");
      } catch {}
    });
  }
  const page = await context.newPage();
  const started = Date.now();
  let trim = 0;
  await page.goto("/");
  await expect(page.getByTestId("legend")).toBeVisible({ timeout: 60_000 });
  await expect(page.getByTestId("map-loading")).toBeHidden({ timeout: 60_000 });
  await page.waitForTimeout(2500); // tiles draw
  await page.mouse.move(SIZE.width * 0.42, SIZE.height * 0.55);
  return {
    page,
    ready: () => {
      trim = (Date.now() - started) / 1000;
    },
    done: async () => {
      await page.waitForTimeout(800);
      const video = page.video()!;
      await context.close();
      fs.mkdirSync(OUT, { recursive: true });
      await video.saveAs(`${OUT}/${name}.webm`);
      fs.writeFileSync(`${OUT}/${name}.json`, JSON.stringify({ trim: Math.max(trim - 0.2, 0) }));
    },
  };
}

let pointer = { x: SIZE.width * 0.42, y: SIZE.height * 0.55 };

/** Glides the pointer to a point, eased, at about 60 frames a second. */
async function glide(page: Page, to: { x: number; y: number }, ms = 650) {
  const from = pointer;
  const steps = Math.max(Math.round(ms / 16), 4);
  for (let i = 1; i <= steps; i++) {
    const t = i / steps;
    const e = t < 0.5 ? 2 * t * t : 1 - (-2 * t + 2) ** 2 / 2;
    await page.mouse.move(from.x + (to.x - from.x) * e, from.y + (to.y - from.y) * e);
    await page.waitForTimeout(12);
  }
  pointer = to;
}

async function centre(target: Locator) {
  await target.scrollIntoViewIfNeeded();
  const box = (await target.boundingBox())!;
  return { x: box.x + box.width / 2, y: box.y + box.height / 2 };
}

async function click(page: Page, target: Locator, ms?: number) {
  await glide(page, await centre(target), ms);
  await page.waitForTimeout(180);
  await target.click();
}

/** Drags a range slider's thumb to a value, so the change is visible. */
async function slide(page: Page, slider: Locator, to: number, min = 0, max = 3) {
  const box = (await slider.boundingBox())!;
  const at = (v: number) => ({ x: box.x + 8 + ((v - min) / (max - min)) * (box.width - 16), y: box.y + box.height / 2 });
  await glide(page, at(Number(await slider.inputValue())));
  await page.mouse.down();
  await glide(page, at(to), 900);
  await page.mouse.up();
}

async function type(page: Page, field: Locator, text: string) {
  await click(page, field);
  await field.pressSequentially(text, { delay: 28 });
  await page.waitForTimeout(350);
}

/** Clicks the middle of the map (where a ranked result has just been centred) and opens
 * that area's profile (a click keeps the assistant open, so switch tabs). */
async function clickMap(page: Page) {
  await click(page, page.locator("canvas.maplibregl-canvas"), 800);
  await page.waitForTimeout(700);
  await click(page, page.getByTestId("tab-area"));
}

/** Asks the assistant and waits for the whole answer. */
async function ask(page: Page, text: string) {
  const chat = page.getByTestId("copilot-chat");
  await type(page, page.getByTestId("chat").locator("textarea").first(), text);
  await page.keyboard.press("Enter");
  await expect(chat).toHaveAttribute("data-copilot-running", "true", { timeout: 10_000 }).catch(() => {});
  await expect(chat).toHaveAttribute("data-copilot-running", "false", { timeout: 120_000 });
}

/** Scrolls the side panel gently, with the pointer over it. */
async function scrollPanel(page: Page, panel: Locator, px: number) {
  await glide(page, await centre(panel), 500);
  for (let done = 0; done < px; done += 60) {
    await page.mouse.wheel(0, 60);
    await page.waitForTimeout(60);
  }
}

const LEEDS = "We have two young kids and a budget of £350k. Where should we look around Leeds?";

test("hero", async ({ browser }) => {
  const { page, ready, done } = await start(browser, "hero");
  await expect(page.getByTestId("onboarding")).toBeVisible();
  ready();
  await page.waitForTimeout(1400);
  await click(page, page.getByTestId("persona-family"), 900);
  await page.waitForTimeout(1300);
  await click(page, page.getByTestId("tab-weights"));
  await page.waitForTimeout(300);
  await slide(page, page.getByTestId("weight-safety"), 3);
  await page.waitForTimeout(1300);
  await click(page, page.getByTestId("tab-assistant"));
  await ask(page, LEEDS);
  await expect(page.getByTestId("ranked-list")).toBeVisible();
  await page.waitForTimeout(2500); // the map flies to Leeds and outlines the results
  await click(page, page.getByTestId("ranked-list").locator("li button").first());
  await page.waitForTimeout(2500); // zooms to the top result
  await clickMap(page);
  await expect(page.getByTestId("area-profile").last()).toBeVisible({ timeout: 30_000 });
  await page.waitForTimeout(3500);
  await done();
});

test("search-profile", async ({ browser }) => {
  const { page, ready, done } = await start(browser, "search-profile", { welcomed: true });
  ready();
  await page.waitForTimeout(1000);
  await type(page, page.getByTestId("search").locator("input"), "Hebden Bridge");
  const option = page.getByRole("option").first();
  await expect(option).toContainText("Hebden Bridge", { timeout: 15_000 });
  await page.waitForTimeout(500);
  await click(page, option);
  const profile = page.getByTestId("area-profile").last();
  await expect(profile).toBeVisible({ timeout: 30_000 });
  await page.waitForTimeout(2500);
  await scrollPanel(page, profile, 420);
  await page.waitForTimeout(3000);
  await done();
});

test("assistant", async ({ browser }) => {
  const { page, ready, done } = await start(browser, "assistant", { welcomed: true });
  ready();
  await page.waitForTimeout(600);
  await click(page, page.getByTestId("tab-assistant"));
  await ask(page, "Compare Far Headingley and Chapel Allerton");
  await expect(page.getByTestId("comparison").last()).toBeVisible();
  await page.waitForTimeout(3000);
  await ask(page, "Show me well-run pubs near LS6 3AA");
  await expect(page.getByTestId("poi-list").last()).toBeVisible();
  await page.waitForTimeout(3500);
  await done();
});

test("analyst", async ({ browser }) => {
  const { page, ready, done } = await start(browser, "analyst", { welcomed: true });
  ready();
  await page.waitForTimeout(600);
  await click(page, page.getByTestId("analyst-toggle"));
  await click(page, page.getByTestId("tab-analyst"));
  const heatmap = page.getByTestId("correlations");
  await expect(heatmap).toBeVisible();
  await page.waitForTimeout(1500);
  const sql = page.locator("#sql");
  await click(page, sql);
  await page.keyboard.press("ControlOrMeta+A");
  await sql.pressSequentially(
    "SELECT lad_nm, round(avg(overall), 1) AS score\nFROM lsoa GROUP BY lad_nm\nORDER BY score DESC LIMIT 10",
    { delay: 22 },
  );
  await page.waitForTimeout(400);
  await click(page, page.getByRole("button", { name: en.Analyst.run }));
  await expect(page.getByTestId("sql-result").last()).toBeVisible({ timeout: 15_000 });
  await page.waitForTimeout(800);
  await scrollPanel(page, page.getByTestId("analyst"), 360);
  await page.waitForTimeout(3000);
  await done();
});

// The whole docs/demo.md story, for the release's MP4 walkthrough
test("walkthrough", async ({ browser }) => {
  const { page, ready, done } = await start(browser, "walkthrough");
  ready();
  await page.waitForTimeout(2500);
  await click(page, page.getByTestId("persona-family"), 900);
  await page.waitForTimeout(2000);
  await click(page, page.getByTestId("tab-weights"));
  await slide(page, page.getByTestId("weight-safety"), 3);
  await page.waitForTimeout(2000);
  await click(page, page.getByTestId("tab-assistant"));
  await ask(page, LEEDS);
  await page.waitForTimeout(4000);
  await click(page, page.getByTestId("ranked-list").locator("li button").first());
  await page.waitForTimeout(2500);
  await clickMap(page);
  await page.waitForTimeout(5000);
  await click(page, page.getByTestId("tab-assistant"));
  await ask(page, "Compare Far Headingley and Chapel Allerton");
  await page.waitForTimeout(4000);
  await ask(page, "Show me well-run pubs near LS6 3AA");
  await page.waitForTimeout(4000);
  await ask(page, "Why does Manchester city centre score low on safety?");
  await page.waitForTimeout(6000);
  // The fourth nation: Belfast's Super Data Zones, ranked and outlined
  await ask(page, "What are the best areas to live in Belfast?");
  await page.waitForTimeout(4000);
  await click(page, page.getByTestId("ranked-list").last().locator("li button").first());
  await page.waitForTimeout(3000);
  // The fifth nation: Dublin's Small Areas, whose card compares within Ireland
  await ask(page, "What are the best areas to live in Dublin?");
  await page.waitForTimeout(4000);
  await click(page, page.getByTestId("ranked-list").last().locator("li button").first());
  await page.waitForTimeout(3000);
  await type(page, page.getByTestId("search").locator("input"), "Hebden Bridge");
  await expect(page.getByRole("option").first()).toContainText("Hebden Bridge", { timeout: 15_000 });
  await click(page, page.getByRole("option").first());
  const profile = page.getByTestId("area-profile").last();
  await expect(profile).toBeVisible({ timeout: 30_000 });
  await page.waitForTimeout(2500);
  await scrollPanel(page, profile, 420);
  await page.waitForTimeout(3000);
  await click(page, page.getByTestId("analyst-toggle"));
  await click(page, page.getByTestId("tab-analyst"));
  await page.waitForTimeout(4000);
  await click(page, page.getByRole("link", { name: en.Header.howScoresWork }));
  await page.waitForTimeout(3000);
  await done();
});
