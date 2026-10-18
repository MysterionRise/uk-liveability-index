/**
 * Data the browser loads once: the build manifest (geography, indicators, themes,
 * presets, sources) and scores.parquet (per-LSOA indicator scores as uint16 × 100 with
 * a uint8 quality code each).
 */
import { asyncBufferFromUrl, parquetReadObjects } from "hyparquet";

import { DATA_URL } from "./config";
import type { Period } from "./period";
import type { ScoredIndicator } from "./scoring";

export const MISSING_U16 = 65535;
import { SCHEMA_VERSION } from "./schema";

export { SCHEMA_VERSION };

export interface IndicatorMeta extends ScoredIndicator {
  label: string;
  description: string;
  unit: string;
  direction: "higher_better" | "lower_better";
  role: "scored" | "context" | "diagnostic";
  caveats: string | null;
  sources: string[];
  /** The unit's code (lix_core.config.UNIT_CODES): what formatValue formats by */
  unit_code: string;
  /** uk: ranked against the whole country · nation: percentiles within the nation */
  benchmark: "uk" | "nation";
  /** Nation codes (E, W, S, N) the indicator is built for */
  coverage: string[];
}

export interface LevelMeta {
  /** What the publisher calls the tier (LSOA, Data Zone 2022...) */
  official: string;
  count: number | null;
  /** What the interface calls one of them, in English ("data zone") and several */
  label: string;
  labels: string;
}

export interface NationMeta {
  name: string;
  /** The country the nation belongs to (a key of Geography.countries) */
  country: string;
  ctry_cd: string;
  bbox: [number, number, number, number];
  levels: Record<string, LevelMeta>;
  /** Themes the nation's open data cannot cover: shown as limited data, left out of its overall */
  theme_gaps?: string[];
  /** The nation's name in the other interface languages */
  names?: Record<string, string>;
}

export interface CountryMeta {
  name: string;
  /** How copy refers to it ("the UK", "Ireland") */
  short: string;
  names?: Record<string, string>;
  currency: string;
  locale: string;
  /** Null where there is no open postcode file (Ireland) */
  postcode_regex: string | null;
  bbox: [number, number, number, number];
  /** Every nation of the country, active in this build or not */
  nations: string[];
}

export interface Geography {
  build: { working_crs: number; area_key: string };
  /** The countries of the nations in this build */
  countries: Record<string, CountryMeta>;
  nations: Record<string, NationMeta>;
  /** Nation codes in this build, in config order */
  active: string[];
  /** Areas per nation in this build (a demo cut has fewer) */
  area_counts: Record<string, number>;
}

export interface PresetMeta {
  label: string;
  description: string;
  themes: Record<string, number>;
  indicators: Record<string, number>;
}

export interface Manifest {
  schema_version: number;
  generated_at: string;
  geography: Geography;
  /** Quality levels in code order: scores.parquet's q__ columns index this list */
  quality_levels: string[];
  /** Interface languages with a data-label catalogue, and whether each is reviewed */
  languages: { code: string; status: "draft" | "reviewed" }[];
  /** Translated data labels by locale (config/i18n/*.yaml), English being the manifest itself */
  i18n: Record<
    string,
    {
      themes?: unknown;
      indicators?: unknown;
      presets?: unknown;
      units?: unknown;
      flags?: unknown;
      levels?: unknown;
    }
  >;
  scored_indicators: string[];
  /** Context indicators whose percentiles the browser table also carries (mappable) */
  context_indicators?: string[];
  indicators: IndicatorMeta[];
  themes: Record<string, { label: string; description: string }>;
  default_preset: string;
  presets: Record<string, PresetMeta>;
  sources: Record<
    string,
    {
      title: string;
      licence: string;
      attribution: string;
      version?: string | null;
      fetched_at?: string | null;
      /** What period the data is for (see lib/period.ts) */
      period?: Period | null;
      stale?: boolean;
      stale_reason?: "newer_edition" | "older_than_cadence" | "none";
    }
  >;
  /** The latest day any source's data reaches (ISO date) */
  data_to?: string | null;
  /** The data release this pack shipped in (data-YYYY-MM), if any */
  data_release?: string | null;
  /** Areas in this build (43,914 for the UK; fewer in a demo cut) */
  lsoa_count?: number;
  /** A small cut of the full build (CI and quick starts): see `lix demo-data` */
  demo?: boolean;
  /** Checksums of the build's files, used to version their URLs */
  files?: Record<string, { sha256: string; bytes: number }>;
  /** Spearman correlations between scored indicators, ordered by theme */
  correlations?: {
    ids: string[];
    themes: string[];
    rho: (number | null)[][];
  } | null;
}

export interface ScoreData {
  n: number;
  codes: string[];
  msoa: string[];
  lad: string[];
  /** Nation letter per LSOA (E, W, S, N) */
  nation: string[];
  /** Harmonised urban | town | rural class, for like-for-like comparison */
  rucClass: string[];
  urban: boolean[];
  population: Float64Array;
  /** Indicator id → 0–100 score per LSOA (NaN when missing) */
  indicators: Record<string, Float64Array>;
  /** Indicator id → quality code per LSOA (index into manifest.quality_levels) */
  quality: Record<string, Uint8Array>;
  index: Map<string, number>;
}

/** Throws a message that says what to do when the data pack is from another layout. */
export function assertSchema(manifest: { schema_version?: number }): void {
  const found = manifest.schema_version ?? 1;
  if (found !== SCHEMA_VERSION) {
    throw new Error(
      `Data pack schema v${found} but this app needs v${SCHEMA_VERSION}: run make data-download`,
    );
  }
}

export async function loadManifest(): Promise<Manifest> {
  // Always fresh: it says which build the other files belong to
  const res = await fetch(`${DATA_URL}/manifest.json`, { cache: "no-store" });
  if (!res.ok) throw new Error(`manifest.json: HTTP ${res.status}`);
  const manifest = (await res.json()) as Manifest;
  assertSchema(manifest);
  return manifest;
}

export async function loadScores(manifest: Manifest): Promise<ScoreData> {
  // Parquet is read in byte ranges; a cached range from a previous build would be
  // garbage in this one, so each build's file gets its own URL
  const version = manifest.files?.["scores.parquet"]?.sha256?.slice(0, 16);
  const url = `${DATA_URL}/scores.parquet${version ? `?v=${version}` : ""}`;
  // A cold load has come back with whole columns empty (byte-range reads under
  // load); a column every active nation lacks is the sign, and one re-read settles it
  const expected = new Set(manifest.geography?.active ?? []);
  for (let attempt = 0; ; attempt++) {
    const data = await readScores(manifest, url);
    const empty = manifest.scored_indicators.filter((id) => {
      const info = manifest.indicators.find((i) => i.id === id);
      const built = (info?.coverage ?? []).some((n) => expected.has(n));
      return built && data.indicators[id].every((v) => Number.isNaN(v));
    });
    if (!empty.length) return data;
    if (attempt >= 1) throw new Error(`scores.parquet read back empty columns: ${empty.slice(0, 5).join(", ")}`);
    console.warn(`scores.parquet: ${empty.length} columns read back empty; reading again`);
  }
}

async function readScores(manifest: Manifest, url: string): Promise<ScoreData> {
  const file = await asyncBufferFromUrl({ url });
  const rows = (await parquetReadObjects({ file })) as Record<
    string,
    unknown
  >[];
  const n = rows.length;
  const indicators: Record<string, Float64Array> = {};
  const quality: Record<string, Uint8Array> = {};
  const missing = Math.max(0, manifest.quality_levels.indexOf("missing"));
  // Context indicators ride along when the pack carries them (older packs do not)
  const context = (manifest.context_indicators ?? []).filter((id) => n === 0 || id in rows[0]);
  for (const id of [...manifest.scored_indicators, ...context]) {
    const col = new Float64Array(n);
    const q = new Uint8Array(n);
    for (let i = 0; i < n; i++) {
      const v = rows[i][id] as number;
      col[i] = v === MISSING_U16 || v == null ? NaN : v / 100;
      const code = rows[i][`q__${id}`];
      q[i] = code == null ? missing : Number(code);
    }
    indicators[id] = col;
    quality[id] = q;
  }
  const codes = rows.map((r) => r.lsoa21cd as string);
  return {
    n,
    codes,
    msoa: rows.map((r) => r.msoa21cd as string),
    lad: rows.map((r) => r.lad_cd as string),
    nation: rows.map((r) => r.nation as string),
    rucClass: rows.map((r) => r.ruc_class as string),
    urban: rows.map((r) => Boolean(r.urban)),
    population: Float64Array.from(rows, (r) => Number(r.population)),
    indicators,
    quality,
    index: new Map(codes.map((c, i) => [c, i])),
  };
}

/**
 * The nations in this build for copy, grouped by country: a country whose every nation is
 * in reads by its name ("the UK", "Ireland", "the UK and Ireland"), a cut of one by its
 * nations ("England and Wales"). `name` translates a nation or country code (the
 * "Nations" catalogue: nation letters, "uk", "IE") and `list` joins names in the interface
 * language; without them the manifest's English names are used. `codes` names the
 * nations to describe instead of the build's.
 */
export function coverageName(
  manifest: Manifest | null | undefined,
  name?: (code: string) => string,
  list?: (names: string[]) => string,
  codes?: string[],
): string {
  const geo = manifest?.geography;
  const uk = name ? name("uk") : "the UK";
  if (!geo) return uk;
  const active = codes ?? geo.active;
  const countryOf = (c: string) => geo.nations[c]?.country ?? "UK";
  const parts: string[] = [];
  for (const country of [...new Set(active.map(countryOf))]) {
    const nations = active.filter((c) => countryOf(c) === country);
    const whole = geo.countries?.[country]?.nations ?? (country === "UK" ? ["E", "W", "S", "N"] : nations);
    if (whole.every((n) => nations.includes(n))) {
      const key = country === "UK" ? "uk" : country;
      parts.push(name ? name(key) : (geo.countries?.[country]?.short ?? country));
    } else {
      parts.push(...nations.map((c) => (name ? name(c) : (geo.nations[c]?.name ?? c))));
    }
  }
  if (parts.length === 0) return uk;
  if (parts.length === 1) return parts[0];
  if (list) return list(parts);
  return `${parts.slice(0, -1).join(", ")} and ${parts[parts.length - 1]}`;
}

/** The nations of the build (or `codes`) as a shape for the coverage phrases: a whole
 * UK, a whole Ireland, both, a single nation, England and Wales, or something else. */
export function coverageShape(
  manifest: Manifest | null | undefined,
  codes?: string[],
): "uk" | "I" | "uk_I" | "E_W" | string | null {
  const geo = manifest?.geography;
  if (!geo) return "uk";
  const active = codes ?? geo.active;
  if (active.length === 0) return "uk";
  const ukNations = geo.countries?.UK?.nations ?? ["E", "W", "S", "N"];
  const wholeUk = ukNations.every((n) => active.includes(n));
  const ireland = active.includes("I");
  if (wholeUk && ireland && active.length === ukNations.length + 1) return "uk_I";
  if (wholeUk && active.length === ukNations.length) return "uk";
  if (active.length === 1) return active[0];
  if (active.length === 2 && active.join("_") === "E_W") return "E_W";
  return null;
}

/**
 * The nations an indicator layer is built for when they are fewer than the build's
 * (domestic rates exist only in Northern Ireland), so the legend and hover can say which
 * areas the percentile compares against; null for the overall, a theme or an indicator
 * every active nation has.
 */
export function coverageNations(
  manifest: Manifest | null | undefined,
  layer: string | undefined,
): string[] | null {
  if (!manifest || !layer?.startsWith("indicator:")) return null;
  const info = manifest.indicators.find((i) => i.id === layer.slice(10));
  const active = manifest.geography?.active ?? [];
  if (!info?.coverage?.length || !active.length) return null;
  const covered = active.filter((n) => info.coverage.includes(n));
  return covered.length && covered.length < active.length ? covered : null;
}

/** West, south, east, north around every nation in this build. */
export function activeBbox(
  manifest: Manifest,
): [number, number, number, number] {
  const geo = manifest.geography;
  const boxes = geo.active
    .map((c) => geo.nations[c]?.bbox)
    .filter((b): b is NationMeta["bbox"] => !!b);
  if (boxes.length === 0) {
    const all = Object.values(geo.countries).map((c) => c.bbox);
    return [
      Math.min(...all.map((b) => b[0])),
      Math.min(...all.map((b) => b[1])),
      Math.max(...all.map((b) => b[2])),
      Math.max(...all.map((b) => b[3])),
    ];
  }
  return [
    Math.min(...boxes.map((b) => b[0])),
    Math.min(...boxes.map((b) => b[1])),
    Math.max(...boxes.map((b) => b[2])),
    Math.max(...boxes.map((b) => b[3])),
  ];
}

export function scoredIndicators(manifest: Manifest): ScoredIndicator[] {
  const byId = new Map(manifest.indicators.map((i) => [i.id, i]));
  return manifest.scored_indicators.map((id) => {
    const i = byId.get(id)!;
    return { id, theme: i.theme, weight: i.weight, coverage: i.coverage };
  });
}

/** Nation → the themes it declares it cannot cover (manifest geography). */
export function themeGaps(manifest: Manifest): Record<string, string[]> {
  return Object.fromEntries(
    Object.entries(manifest.geography.nations).map(([code, n]) => [
      code,
      n.theme_gaps ?? [],
    ]),
  );
}
