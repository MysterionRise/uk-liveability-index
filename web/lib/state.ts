/**
 * The state shared with the assistant (mirrors lix_api.agent.state.LiveabilityState).
 * The page and the assistant both write it; AG-UI keeps them in sync.
 */
import type { LiveabilityState as WireState, MapView, Poi, ProfileRef, ShortlistItem } from "./contracts.gen";
import type { Manifest } from "./data";

export type { Poi, ProfileRef, ShortlistItem };

/** The wire type has optional fields (they have defaults); the page always fills them in. */
export type LiveabilityState = Omit<Required<WireState>, "map" | "theme_weights" | "indicator_weights"> & {
  map: Required<MapView> & { pois: Poi[]; highlighted: string[] };
  theme_weights: Record<string, number>;
  indicator_weights: Record<string, number>;
  profile: ProfileRef | null;
  shortlist: ShortlistItem[];
};

export type Bbox = [number, number, number, number];

export const DEFAULT_STATE: LiveabilityState = {
  preset: "balanced",
  locale: "en",
  theme_weights: {},
  indicator_weights: {},
  profile: null,
  compare_within: "uk",
  compare_within_urban_rural: false,
  mode: "consumer",
  map: { bbox: null, layer: "overall", highlighted: [], selected: null, pois: [] },
  shortlist: [],
};

/** What map percentiles compare against: the whole country, the area's nation, or areas
 * of the same urban/rural class. The old boolean is read as "urban_rural". */
export type CompareWithin = "uk" | "nation" | "urban_rural";

export function compareWithin(s: Pick<WireState, "compare_within" | "compare_within_urban_rural">): CompareWithin {
  if (s.compare_within && s.compare_within !== "uk") return s.compare_within;
  return s.compare_within_urban_rural ? "urban_rural" : "uk";
}

/** The state with every field present (the assistant may send partial snapshots). */
export function normaliseState(s: Partial<WireState> | undefined): LiveabilityState {
  const within = compareWithin(s ?? {});
  return {
    ...DEFAULT_STATE,
    ...(s ?? {}),
    compare_within: within,
    compare_within_urban_rural: within === "urban_rural",
    map: { ...DEFAULT_STATE.map, ...(s?.map ?? {}) },
    theme_weights: s?.theme_weights ?? {},
    indicator_weights: s?.indicator_weights ?? {},
    profile: s?.profile ?? null,
    shortlist: s?.shortlist ?? [],
  } as LiveabilityState;
}

/** Preset weights with the user's overrides on top. */
export function effectiveWeights(manifest: Manifest, state: LiveabilityState) {
  const preset = manifest.presets[state.preset] ?? manifest.presets[manifest.default_preset];
  return {
    themes: { ...preset.themes, ...(state.theme_weights ?? {}) } as Record<string, number>,
    indicators: { ...preset.indicators, ...(state.indicator_weights ?? {}) } as Record<string, number>,
  };
}

export function bboxOf(b: unknown): Bbox | null {
  return Array.isArray(b) && b.length === 4 ? (b.map(Number) as Bbox) : null;
}
