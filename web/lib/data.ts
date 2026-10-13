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
/** Layout of the data pack this app reads (lix_core.config.SERVE_SCHEMA_VERSION) */
export const SCHEMA_VERSION = 3;

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

export interface NationMeta {
  name: string;
  ctry_cd: string;
  bbox: [number, number, number, number];
  levels: Record<string, { official: string; count: number | null }>;
}

export interface Geography {
  country: {
    code: string;
    name: string;
    currency: string;
    locale: string;
    bbox: [number, number, number, number];
    area_key: string;
  };
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
  i18n: Record<string, { themes?: unknown; indicators?: unknown; presets?: unknown; units?: unknown; flags?: unknown }>;
  scored_indicators: string[];
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
  /** Areas in this build (35,672 for England and Wales; fewer in a demo cut) */
  lsoa_count?: number;
  /** A small cut of the full build (CI and quick starts): see `lix demo-data` */
  demo?: boolean;
  /** Checksums of the build's files, used to version their URLs */
  files?: Record<string, { sha256: string; bytes: number }>;
  /** Spearman correlations between scored indicators, ordered by theme */
  correlations?: { ids: string[]; themes: string[]; rho: number[][] } | null;
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
  const file = await asyncBufferFromUrl({ url });
  const rows = (await parquetReadObjects({ file })) as Record<string, unknown>[];
  const n = rows.length;
  const indicators: Record<string, Float64Array> = {};
  const quality: Record<string, Uint8Array> = {};
  const missing = Math.max(0, manifest.quality_levels.indexOf("missing"));
  for (const id of manifest.scored_indicators) {
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
 * The nations in this build for copy: "England and Wales"; all four read as "the UK".
 * `name` translates a nation code (the "Nations" catalogue) and `list` joins names in
 * the interface language; without them the manifest's English names are used.
 */
export function coverageName(
  manifest: Manifest | null | undefined,
  name?: (code: string) => string,
  list?: (names: string[]) => string,
): string {
  const geo = manifest?.geography;
  const uk = name ? name("uk") : "the UK";
  if (!geo) return uk;
  const names = geo.active.map((c) => (name ? name(c) : (geo.nations[c]?.name ?? c)));
  if (names.length === 0 || names.length >= 4) return uk;
  if (names.length === 1) return names[0];
  if (list) return list(names);
  return `${names.slice(0, -1).join(", ")} and ${names[names.length - 1]}`;
}

/** West, south, east, north around every nation in this build. */
export function activeBbox(manifest: Manifest): [number, number, number, number] {
  const geo = manifest.geography;
  const boxes = geo.active.map((c) => geo.nations[c]?.bbox).filter((b): b is NationMeta["bbox"] => !!b);
  if (boxes.length === 0) return geo.country.bbox;
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
    return { id, theme: i.theme, weight: i.weight };
  });
}
