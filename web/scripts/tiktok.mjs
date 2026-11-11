/**
 * A vertical, TikTok-style clip of the app: dist/tiktok.gif and dist/tiktok.mp4.
 *
 *   LIX_MODEL=replay LIX_REPLAY_DELAY=0.02 docker compose up -d --wait   # the app
 *   cd web && node scripts/tiktok.mjs
 *
 * 1. Records the phone layout (9:16) at 2x against the running app with Chrome's
 *    screencast, noting when each beat happens and where on screen.
 * 2. Composes captions, zooms, speed ramps, stickers and an end card frame by frame in
 *    a browser page (scripts/tiktok.html), so fonts and emoji render properly.
 * 3. Encodes an MP4 (1080×1920, 30 fps) and a GIF from the frames with ffmpeg.
 */
import { execFileSync } from "node:child_process";
import fs from "node:fs";
import path from "node:path";
import { pathToFileURL } from "node:url";

import { chromium } from "@playwright/test";

const BASE = process.env.LIX_BASE_URL ?? "http://localhost:3000";
const WORK = path.resolve("recordings/tiktok");
const DIST = path.resolve("../dist");
const W = 432; // the recorded phone screen, 9:16
const H = 768;
const FPS = 30;
const LEEDS = "We have two young kids and a budget of £350k. Where should we look around Leeds?";

// A tap shows as a soft circle (no mouse pointer on a phone)
const TAPS = `
addEventListener("DOMContentLoaded", () => {
  let dot = null;
  const place = (e) => dot && Object.assign(dot.style, { left: e.clientX - 22 + "px", top: e.clientY - 22 + "px" });
  addEventListener("mousedown", (e) => {
    dot = document.createElement("div");
    Object.assign(dot.style, { position: "fixed", width: "44px", height: "44px", borderRadius: "50%",
      background: "rgba(255,255,255,0.55)", border: "2px solid rgba(0,0,0,0.35)", zIndex: "2147483647",
      pointerEvents: "none", transition: "opacity 0.35s, transform 0.35s" });
    place(e);
    document.body.appendChild(dot);
  }, true);
  addEventListener("mousemove", place, true);
  addEventListener("mouseup", () => {
    const d = dot; dot = null;
    if (!d) return;
    d.style.opacity = "0"; d.style.transform = "scale(1.5)";
    setTimeout(() => d.remove(), 400);
  }, true);
});
`;

async function record() {
  fs.rmSync(WORK, { recursive: true, force: true });
  const browser = await chromium.launch();
  const context = await browser.newContext({ viewport: { width: W, height: H }, deviceScaleFactor: 2 });
  await context.addInitScript(TAPS);
  const page = await context.newPage();
  // Chrome's screencast at full device resolution (Playwright's own video records this
  // screen at 1x): frames arrive when something changes, each with its time
  const cast = [];
  fs.mkdirSync(`${WORK}/cast`, { recursive: true });
  const cdp = await context.newCDPSession(page);
  cdp.on("Page.screencastFrame", async ({ data, metadata, sessionId }) => {
    const file = `${WORK}/cast/${String(cast.length).padStart(5, "0")}.jpg`;
    fs.writeFileSync(file, Buffer.from(data, "base64"));
    cast.push({ t: metadata.timestamp, file });
    await cdp.send("Page.screencastFrameAck", { sessionId }).catch(() => {});
  });
  await cdp.send("Page.startScreencast", { format: "jpeg", quality: 92, maxWidth: W * 2, maxHeight: H * 2 });
  const started = Date.now();
  const marks = {};

  /** Notes when a beat happens, and the centre of what it's about (0–1 of the screen). */
  async function mark(name, target) {
    const at = (Date.now() - started) / 1000;
    let focus = [0.5, 0.5];
    if (target) {
      const box = await target.boundingBox();
      if (box) focus = [(box.x + box.width / 2) / W, (box.y + box.height / 2) / H];
    }
    marks[name] = { t: at, focus };
  }

  /** A real click, held a moment so the tap circle shows. */
  async function tap(target) {
    await target.scrollIntoViewIfNeeded();
    if (!(await onTop(target))) await scrollTo(target); // e.g. under the chat's input box
    const box = await target.boundingBox();
    await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2);
    await page.mouse.down();
    await page.waitForTimeout(90);
    await page.mouse.up();
  }

  /** Whether the target's centre is what a tap there would hit. */
  async function onTop(target) {
    return target.evaluate((el) => {
      const r = el.getBoundingClientRect();
      const hit = document.elementFromPoint(r.x + r.width / 2, r.y + r.height / 2);
      return !!hit && (el === hit || el.contains(hit));
    });
  }

  /** Smoothly scrolls the target's scroll container so it sits at the top (or centre). */
  async function scrollTo(target, block = "start") {
    await target.evaluate((el, block) => el.scrollIntoView({ block, behavior: "smooth" }), block);
    await page.waitForTimeout(700);
  }

  /** Taps the map until a neighbourhood is selected (a tap on a wider area zooms in). */
  async function pickOnMap() {
    const canvas = page.locator("canvas.maplibregl-canvas");
    for (let i = 0; i < 4; i++) {
      await tapMap(canvas);
      await page.waitForTimeout(1200);
      if (decodeURIComponent(new URL(page.url()).hash).includes('"s":')) return;
    }
    throw new Error("No neighbourhood selected on the map");
  }

  /** Taps the map where no overlay (the legend, the zoom controls) covers it. */
  async function tapMap(canvas) {
    const spots = [[0.5, 0.4], [0.5, 0.3], [0.62, 0.35], [0.38, 0.3]];
    const [fx, fy] = await canvas.evaluate((el, spots) => {
      const r = el.getBoundingClientRect();
      const clear = ([x, y]) => document.elementFromPoint(r.x + r.width * x, r.y + r.height * y) === el;
      return spots.find(clear) ?? spots[0];
    }, spots);
    const box = await canvas.boundingBox();
    await page.mouse.move(box.x + box.width * fx, box.y + box.height * fy);
    await page.mouse.down();
    await page.waitForTimeout(90);
    await page.mouse.up();
  }

  try {
    await session();
  } catch (e) {
    await page.screenshot({ path: `${WORK}/error.png` });
    await browser.close();
    throw e;
  }
  await cdp.send("Page.stopScreencast");
  await browser.close();
  // An evenly timed sequence: for each 1/FPS step, the latest frame shown by then
  const frames = `${WORK}/frames`;
  fs.mkdirSync(frames, { recursive: true });
  const end = (Date.now() - started) / 1000;
  let j = 0;
  for (let k = 0; k * (1 / FPS) <= end; k++) {
    const at = started / 1000 + k / FPS;
    while (j + 1 < cast.length && cast[j + 1].t <= at) j++;
    fs.copyFileSync(cast[j].file, `${frames}/${String(k + 1).padStart(5, "0")}.jpg`);
  }
  fs.writeFileSync(`${WORK}/marks.json`, JSON.stringify(marks, null, 1));
  return marks;

  async function session() {
  await page.goto(BASE);
  await page.getByTestId("legend").waitFor({ timeout: 60_000 });
  await page.getByTestId("map-loading").waitFor({ state: "hidden", timeout: 60_000 });
  await page.waitForTimeout(2500);
  await mark("start");
  await page.waitForTimeout(2200);

  const persona = page.getByTestId("persona-family");
  await mark("persona", page.locator("canvas.maplibregl-canvas"));
  await tap(persona);
  await page.waitForTimeout(1800);

  await tap(page.getByTestId("tab-assistant"));
  const input = page.getByTestId("chat").locator("textarea").first();
  await tap(input);
  await mark("type", input);
  await input.pressSequentially(LEEDS, { delay: 18 });
  await page.waitForTimeout(250);
  await page.keyboard.press("Enter");
  await mark("asked");
  const chat = page.getByTestId("copilot-chat");
  await chat.locator("xpath=self::*[@data-copilot-running='true']").waitFor({ timeout: 10_000 }).catch(() => {});
  await chat.locator("xpath=self::*[@data-copilot-running='false']").waitFor({ timeout: 120_000 });
  const ranked = page.getByTestId("ranked-list").last();
  const top = ranked.locator("li button").first();
  // The chat sticks to the bottom as the answer lands; scroll until the list stays put
  for (let i = 0; i < 4 && !(await onTop(top)); i++) await scrollTo(ranked);
  await page.waitForTimeout(300);
  await mark("answered", ranked);
  await page.waitForTimeout(2800);

  await mark("pick", top);
  await tap(top);
  await page.waitForTimeout(2200); // the map flies to it
  await mark("map", page.locator("canvas.maplibregl-canvas"));
  await pickOnMap();
  await tap(page.getByTestId("tab-area"));
  const profile = page.getByTestId("area-profile").last();
  await profile.waitFor({ timeout: 30_000 });
  await page.waitForTimeout(600);
  await mark("profile", profile.getByTestId("theme-bars"));
  await page.waitForTimeout(1200);
  await scrollTo(profile.getByTestId("theme-bars"));
  await mark("bars", profile.getByTestId("theme-bars"));
  await page.waitForTimeout(1800);

  const search = page.getByTestId("search").locator("input");
  await mark("search", search);
  await tap(search);
  await search.pressSequentially("Hebden Bridge", { delay: 45 });
  const option = page.getByRole("option").first();
  await option.waitFor({ timeout: 15_000 });
  await page.waitForTimeout(500);
  await tap(option);
  const hebden = page.getByTestId("area-profile").last();
  await hebden.getByText("Hebden Bridge").first().waitFor({ timeout: 30_000 });
  await page.waitForTimeout(900);
  await mark("hebden", page.locator("canvas.maplibregl-canvas"));
  await page.waitForTimeout(1200);
  const flood = hebden.getByText("Homes at risk of flooding").first();
  await scrollTo(flood, "center");
  await page.waitForTimeout(300);
  await mark("flood", flood);
  await page.waitForTimeout(2600);
  await mark("end");
  }
}

async function compose(marks) {
  const frames = `${WORK}/frames`;
  const count = fs.readdirSync(frames).filter((f) => f.endsWith(".jpg")).length;

  const browser = await chromium.launch();
  const page = await browser.newPage({ viewport: { width: 540, height: 960 }, deviceScaleFactor: 2 });
  await page.goto(pathToFileURL(path.resolve("scripts/tiktok.html")).href);
  const total = await page.evaluate(
    ([m, c, fps, folder]) => window.setup(m, c, fps, folder),
    [marks, count, FPS, pathToFileURL(frames).href],
  );
  const out = `${WORK}/out`;
  fs.mkdirSync(out, { recursive: true });
  const n = Math.round(total * FPS);
  for (let i = 0; i < n; i++) {
    await page.evaluate((i) => window.frameAt(i), i);
    await page.screenshot({ path: `${out}/${String(i).padStart(5, "0")}.jpg`, type: "jpeg", quality: 92 });
    if (i % 60 === 0) process.stdout.write(`\rcomposed ${i}/${n}`);
  }
  console.log(`\rcomposed ${n}/${n} frames (${total.toFixed(1)} s)`);
  await browser.close();
  return out;
}

function encode(out) {
  fs.mkdirSync(DIST, { recursive: true });
  execFileSync("ffmpeg", ["-y", "-loglevel", "error", "-framerate", String(FPS), "-i", `${out}/%05d.jpg`,
    "-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "20", "-preset", "slow", "-movflags", "+faststart",
    `${DIST}/tiktok.mp4`]);
  const gifWidth = process.env.GIF_WIDTH ?? "432";
  execFileSync("ffmpeg", ["-y", "-loglevel", "error", "-framerate", String(FPS), "-i", `${out}/%05d.jpg`, "-vf",
    `fps=15,scale=${gifWidth}:-1:flags=lanczos,split[a][b];[a]palettegen=max_colors=160:stats_mode=diff[p];[b][p]paletteuse=dither=none:diff_mode=rectangle`,
    `${DIST}/tiktok.gif`]);
  for (const f of ["tiktok.gif", "tiktok.mp4"]) {
    console.log(`dist/${f}: ${(fs.statSync(`${DIST}/${f}`).size / 1e6).toFixed(1)} MB`);
  }
}

const marks = process.env.REUSE ? JSON.parse(fs.readFileSync(`${WORK}/marks.json`, "utf8")) : await record();
encode(await compose(marks));
