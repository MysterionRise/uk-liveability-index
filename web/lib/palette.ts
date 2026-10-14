/**
 * Every colour in the app, light and dark, in one place.
 *
 * - `scripts/gen-tokens.mjs` writes `app/tokens.gen.css` from this file, so components use
 *   `var(--token)` and never a literal colour.
 * - The map reads the ramp and chrome colours directly (MapLibre needs real values).
 * - `lib/palette.test.ts` checks contrast, ramp order and colour separation.
 *
 * Rules (docs/design.md): the chrome is monochrome warm-neutral and colour means data.
 * The map ramp is diverging around the median (warm = worse than typical, pale neutral =
 * typical, teal = better); each theme has one categorical colour, assigned in a fixed
 * order that was validated for colour-blind separation; the correlation heatmap reuses
 * the ramp's warm/cool pair.
 */

export type Mode = "light" | "dark";

// --- Colour maths (OKLCH ↔ sRGB, WCAG contrast) ----------------------------------------

/** OKLCH → sRGB hex (Björn Ottosson's OKLab; out-of-gamut channels are clipped). */
export function oklch(L: number, C: number, h: number): string {
  const a = C * Math.cos((h * Math.PI) / 180);
  const b = C * Math.sin((h * Math.PI) / 180);
  const l = (L + 0.3963377774 * a + 0.2158037573 * b) ** 3;
  const m = (L - 0.1055613458 * a - 0.0638541728 * b) ** 3;
  const s = (L - 0.0894841775 * a - 1.291485548 * b) ** 3;
  const lin = [
    4.0767416621 * l - 3.3077115913 * m + 0.2309699292 * s,
    -1.2684380046 * l + 2.6097574011 * m - 0.3413193965 * s,
    -0.0041960863 * l - 0.7034186147 * m + 1.707614701 * s,
  ];
  return rgbToHex(lin.map(gamma) as [number, number, number]);
}

function gamma(x: number): number {
  const c = Math.max(0, Math.min(1, x));
  return c <= 0.0031308 ? 12.92 * c : 1.055 * c ** (1 / 2.4) - 0.055;
}

function linear(c: number): number {
  return c <= 0.04045 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4;
}

function rgbToHex([r, g, b]: [number, number, number]): string {
  return `#${[r, g, b].map((x) => Math.round(x * 255).toString(16).padStart(2, "0")).join("")}`;
}

export function hexToRgb(hex: string): [number, number, number] {
  const h = hex.replace("#", "");
  return [0, 2, 4].map((i) => parseInt(h.slice(i, i + 2), 16) / 255) as [number, number, number];
}

/** OKLab (L, a, b) of a hex colour, for lightness checks and colour distances. */
export function hexToOklab(hex: string): [number, number, number] {
  const [r, g, b] = hexToRgb(hex).map(linear);
  const l = Math.cbrt(0.4122214708 * r + 0.5363325363 * g + 0.0514459929 * b);
  const m = Math.cbrt(0.2119034982 * r + 0.6806995451 * g + 0.1073969566 * b);
  const s = Math.cbrt(0.0883024619 * r + 0.2817188376 * g + 0.6299787005 * b);
  return [
    0.2104542553 * l + 0.793617785 * m - 0.0040720468 * s,
    1.9779984951 * l - 2.428592205 * m + 0.4505937099 * s,
    0.0259040371 * l + 0.7827717662 * m - 0.808675766 * s,
  ];
}

/** Euclidean distance in OKLab × 100 (the data-viz method's ΔE). */
export function deltaE(a: string, b: string): number {
  const p = hexToOklab(a);
  const q = hexToOklab(b);
  return Math.hypot(p[0] - q[0], p[1] - q[1], p[2] - q[2]) * 100;
}

export function luminance(hex: string): number {
  const [r, g, b] = hexToRgb(hex).map(linear);
  return 0.2126 * r + 0.7152 * g + 0.0722 * b;
}

/** WCAG contrast ratio between two opaque colours. */
export function contrast(a: string, b: string): number {
  const [hi, lo] = [luminance(a), luminance(b)].sort((x, y) => y - x);
  return (hi + 0.05) / (lo + 0.05);
}

/** `fg` at `alpha` over opaque `bg`, as the eye sees it (e.g. a map fill over the basemap). */
export function blend(fg: string, bg: string, alpha: number): string {
  const f = hexToRgb(fg);
  const g = hexToRgb(bg);
  return rgbToHex(f.map((c, i) => c * alpha + g[i] * (1 - alpha)) as [number, number, number]);
}

// --- Chrome: warm neutrals; colour is reserved for data -------------------------------

export interface Chrome {
  page: string;
  surface: string;
  textPrimary: string;
  textSecondary: string;
  textMuted: string;
  border: string;
  hover: string;
  track: string;
  baseline: string;
  /** Buttons, sliders, links, the "working" pulse: ink, so no hue competes with the data. */
  accent: string;
  /** Keyboard focus ring: amber, which passes 3:1 on both surfaces. */
  focus: string;
  /** Areas with no value: a cool grey, so it never reads as "typical" on the warm ramp. */
  noData: string;
  critical: string;
}

export const CHROME: Record<Mode, Chrome> = {
  light: {
    page: "#f9f9f7",
    surface: "#fcfcfb",
    textPrimary: "#0b0b0b",
    textSecondary: "#52514e",
    textMuted: "#6f6d67",
    border: "rgba(11, 11, 11, 0.1)",
    hover: "rgba(11, 11, 11, 0.04)",
    track: "#e1e0d9",
    baseline: "#c3c2b7",
    accent: "#0b0b0b",
    focus: "#b45309",
    noData: "#c8cbcd",
    critical: "#d03b3b",
  },
  dark: {
    page: "#0d0d0d",
    surface: "#1a1a19",
    textPrimary: "#ffffff",
    textSecondary: "#c3c2b7",
    textMuted: "#a09e95",
    border: "rgba(255, 255, 255, 0.1)",
    hover: "rgba(255, 255, 255, 0.05)",
    track: "#2c2c2a",
    baseline: "#383835",
    accent: "#ffffff",
    focus: "#d97706",
    noData: "#46484a",
    critical: "#e66767",
  },
};

// --- Map ramp: diverging around the median ----------------------------------------------

/**
 * Nine stops for percentiles 0, 12.5, …, 100. Warm terracotta below the median, a pale
 * sand neutral at 50, teal-green above. Orange–teal is the colour-blind-safe diverging
 * family (like BrBG). In dark mode the poles are bright and the middle recedes into the
 * surface, so "typical" still reads as "nothing to see".
 */
export const RAMP: Record<Mode, string[]> = {
  light: [
    oklch(0.5, 0.16, 38),
    oklch(0.6, 0.15, 44),
    oklch(0.71, 0.12, 52),
    oklch(0.83, 0.07, 68),
    oklch(0.93, 0.02, 85),
    oklch(0.84, 0.07, 172),
    oklch(0.72, 0.1, 178),
    oklch(0.6, 0.1, 184),
    oklch(0.45, 0.085, 190),
  ],
  dark: [
    oklch(0.76, 0.16, 45),
    oklch(0.68, 0.15, 42),
    oklch(0.58, 0.13, 40),
    oklch(0.46, 0.09, 40),
    oklch(0.3, 0.01, 85),
    oklch(0.46, 0.08, 180),
    oklch(0.58, 0.11, 180),
    oklch(0.69, 0.12, 180),
    oklch(0.8, 0.13, 178),
  ],
};

/** Fill opacity per map tier: zoomed out the areas are the picture; zoomed in the basemap's streets matter. */
export const FILL_OPACITY = { lad: 0.85, msoa: 0.85, lsoa: 0.72 } as const;

/** The map's diverging pair, reused by the correlation heatmap (warm = negative, cool = positive). */
export const DIVERGING: Record<Mode, { neg: string; mid: string; pos: string }> = {
  light: { neg: RAMP.light[1], mid: "#f0efec", pos: RAMP.light[7] },
  dark: { neg: RAMP.dark[1], mid: "#383835", pos: RAMP.dark[7] },
};

// --- One colour per theme ---------------------------------------------------------------

/**
 * The nine themes in the order they appear on screen (config/indicators.yaml). The
 * hue order is the colour-blind-safety mechanism: it was chosen from the reference
 * palette's validated steps so that every adjacent pair stays apart under simulated
 * protan/deutan vision in both modes (see docs/design.md). Don't reorder the hues.
 */
export const THEME_ORDER = [
  "safety",
  "environment",
  "health",
  "education",
  "transport",
  "amenities",
  "housing",
  "community",
  "climate",
] as const;

export type ThemeId = (typeof THEME_ORDER)[number];

export const THEME_COLORS: Record<Mode, Record<ThemeId, string>> = {
  light: {
    safety: "#e34948", // red
    environment: "#008300", // green
    health: "#e87ba4", // magenta
    education: "#eda100", // amber
    transport: "#2a78d6", // blue
    amenities: "#eb6834", // orange
    housing: "#1baf7a", // aqua
    community: "#4a3aa7", // violet
    climate: "#0b8fa8", // teal
  },
  dark: {
    safety: "#e66767",
    environment: "#008300",
    health: "#d55181",
    education: "#c98500",
    transport: "#3987e5",
    amenities: "#d95926",
    housing: "#199e70",
    community: "#9085e9",
    climate: "#2aa7bf",
  },
};

/** CSS variable for a theme's colour; unknown themes fall back to the accent. */
export function themeVar(theme: string | null | undefined): string {
  return theme && (THEME_ORDER as readonly string[]).includes(theme) ? `var(--theme-${theme})` : "var(--accent)";
}

// --- CSS tokens ---------------------------------------------------------------------------

function declarations(mode: Mode): string[] {
  const c = CHROME[mode];
  const d = DIVERGING[mode];
  const lines = [
    `color-scheme: ${mode};`,
    `--page: ${c.page};`,
    `--surface-1: ${c.surface};`,
    `--text-primary: ${c.textPrimary};`,
    `--text-secondary: ${c.textSecondary};`,
    `--text-muted: ${c.textMuted};`,
    `--border: ${c.border};`,
    `--hover: ${c.hover};`,
    `--track: ${c.track};`,
    `--baseline: ${c.baseline};`,
    `--accent: ${c.accent};`,
    `--focus: ${c.focus};`,
    `--no-data: ${c.noData};`,
    `--critical: ${c.critical};`,
    `--div-neg: ${d.neg};`,
    `--div-mid: ${d.mid};`,
    `--div-pos: ${d.pos};`,
    ...RAMP[mode].map((hex, i) => `--ramp-${i}: ${hex};`),
    ...THEME_ORDER.map((t) => `--theme-${t}: ${THEME_COLORS[mode][t]};`),
  ];
  return lines;
}

function block(selector: string, mode: Mode, indent = ""): string {
  const body = declarations(mode)
    .map((l) => `${indent}  ${l}`)
    .join("\n");
  return `${indent}${selector} {\n${body}\n${indent}}`;
}

/** The stylesheet `scripts/gen-tokens.mjs` writes: light by default, dark by OS setting or the theme toggle. */
export function tokensCss(): string {
  return [
    "/* Generated by scripts/gen-tokens.mjs from lib/palette.ts. Do not edit: run `npm run tokens`. */",
    block(":root", "light"),
    `@media (prefers-color-scheme: dark) {\n${block(':root:not([data-theme="light"])', "dark", "  ")}\n}`,
    block(':root[data-theme="dark"]', "dark"),
    "",
  ].join("\n\n");
}
