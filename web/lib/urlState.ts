/**
 * Shareable views: preset, weight overrides, indicator multipliers, the profile's name,
 * map layer and selected area in the URL hash. The profile's description and rationale
 * never enter a link: the name is enough to label the weights it carries.
 */
import type { LiveabilityState as WireState } from "./contracts.gen";
import type { LiveabilityState } from "./state";

interface HashState {
  p?: string;
  w?: Record<string, number>;
  /** indicator multipliers */
  i?: Record<string, number>;
  /** the name of the profile the weights come from */
  n?: string;
  l?: string;
  s?: string;
  /** compare against: "nation" or "urban_rural" (1 is the old urban/rural flag) */
  c?: 1 | "nation" | "urban_rural";
}

const some = (o?: Record<string, number>) => o && Object.keys(o).length > 0;

/** The shareable part of the state as the hash text (without the "#"). */
export function encodeHash(state: LiveabilityState): string {
  const h: HashState = {
    p: state.preset,
    ...(some(state.theme_weights) ? { w: state.theme_weights } : {}),
    ...(some(state.indicator_weights) ? { i: state.indicator_weights } : {}),
    ...(state.profile?.name ? { n: state.profile.name } : {}),
    ...(state.map.layer !== "overall" ? { l: state.map.layer } : {}),
    ...(state.map.selected ? { s: state.map.selected } : {}),
    ...(state.compare_within !== "uk" ? { c: state.compare_within } : {}),
  };
  return encodeURIComponent(JSON.stringify(h));
}

/** The state a hash describes; nothing for an empty or unreadable one. */
export function decodeHash(hash: string): Partial<WireState> {
  const text = hash.startsWith("#") ? hash.slice(1) : hash;
  if (!text) return {};
  try {
    const h: HashState = JSON.parse(decodeURIComponent(text));
    if (!h || typeof h !== "object") return {};
    return {
      ...(h.p ? { preset: h.p } : {}),
      ...(h.w ? { theme_weights: h.w } : {}),
      ...(h.i ? { indicator_weights: h.i } : {}),
      ...(h.n ? { profile: { name: String(h.n).slice(0, 40), description: "", rationale: "" } } : {}),
      ...(h.c === 1 || h.c === "urban_rural"
        ? { compare_within: "urban_rural" as const, compare_within_urban_rural: true }
        : h.c === "nation"
          ? { compare_within: "nation" as const }
          : {}),
      map: {
        bbox: null,
        layer: h.l ?? "overall",
        highlighted: [],
        selected: h.s ?? null,
        pois: [],
      },
    };
  } catch {
    return {};
  }
}

export function readHash(): Partial<WireState> {
  if (typeof window === "undefined") return {};
  return decodeHash(window.location.hash);
}

export function writeHash(state: LiveabilityState): void {
  const next = `#${encodeHash(state)}`;
  if (window.location.hash !== next) window.history.replaceState(null, "", next);
}
