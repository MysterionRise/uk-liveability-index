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
  /** the interface language the link was made in (absent for English) */
  g?: string;
}

const some = (o?: Record<string, number>) => o && Object.keys(o).length > 0;

/** Weights a link may carry: finite numbers in the sliders' range, nothing else. */
function weights(o: unknown, max = 3): Record<string, number> | undefined {
  if (!o || typeof o !== "object" || Array.isArray(o)) return undefined;
  const out: Record<string, number> = {};
  for (const [k, v] of Object.entries(o as Record<string, unknown>)) {
    if (typeof v !== "number" || !Number.isFinite(v) || typeof k !== "string") continue;
    out[k] = Math.min(max, Math.max(0, v));
  }
  return Object.keys(out).length ? out : undefined;
}

const text = (v: unknown, max = 60): string | undefined =>
  typeof v === "string" && v.length > 0 ? v.slice(0, max) : undefined;

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
    ...(state.locale && state.locale !== "en" ? { g: state.locale } : {}),
  };
  return encodeURIComponent(JSON.stringify(h));
}

/** The state a hash describes; nothing for an empty or unreadable one. */
export function decodeHash(hash: string): Partial<WireState> {
  const hashText = hash.startsWith("#") ? hash.slice(1) : hash;
  if (!hashText) return {};
  try {
    const h: HashState = JSON.parse(decodeURIComponent(hashText));
    if (!h || typeof h !== "object" || Array.isArray(h)) return {};
    // A link is data from anywhere: every field is checked for type and range before it
    // reaches the page (a number as the layer, a nested object as a weight)
    const w = weights(h.w);
    const i = weights(h.i);
    const preset = text(h.p);
    const name = text(h.n, 40);
    const layer = text(h.l, 80);
    const selected = text(h.s, 20);
    return {
      ...(preset ? { preset } : {}),
      ...(w ? { theme_weights: w } : {}),
      ...(i ? { indicator_weights: i } : {}),
      ...(name ? { profile: { name, description: "", rationale: "" } } : {}),
      ...(h.c === 1 || h.c === "urban_rural"
        ? { compare_within: "urban_rural" as const, compare_within_urban_rural: true }
        : h.c === "nation"
          ? { compare_within: "nation" as const }
          : {}),
      map: {
        bbox: null,
        layer: layer ?? "overall",
        highlighted: [],
        selected: selected ?? null,
        pois: [],
      },
    };
  } catch {
    return {};
  }
}

/** The interface language a link was made in ("cy"), if it says; nothing otherwise. */
export function hashLocale(hash: string): string | null {
  const hashText = hash.startsWith("#") ? hash.slice(1) : hash;
  if (!hashText) return null;
  try {
    const h = JSON.parse(decodeURIComponent(hashText));
    return h && typeof h === "object" && typeof h.g === "string" && /^[a-z]{2,3}$/.test(h.g) ? h.g : null;
  } catch {
    return null;
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
