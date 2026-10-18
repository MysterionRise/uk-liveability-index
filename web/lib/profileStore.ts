/**
 * The generated profile this browser keeps (localStorage "lix.profile"): the weights
 * as generated, the sliders since (so a reload keeps the tweaks and Reset undoes them),
 * and the description and rationale the page shows. The assistant only ever sees the
 * name, description and rationale, through the shared state.
 */
import { useSyncExternalStore } from "react";

import type { WeightProfile } from "./contracts.gen";
import type { LiveabilityState } from "./state";

export const KEY = "lix.profile";
/** The preset picker's value while a profile is active */
export const PROFILE_OPTION = "__profile__";

export interface SavedProfile {
  name: string;
  description: string;
  rationale: string;
  engine?: "model" | "rules";
  preset: string;
  /** Overrides as generated */
  theme_weights: Record<string, number>;
  /** Multipliers as generated */
  indicator_weights: Record<string, number>;
  /** The sliders since, when they differ from the generated ones */
  tweaks?: { theme_weights: Record<string, number>; indicator_weights: Record<string, number> };
}

const listeners = new Set<() => void>();
let cachedRaw: string | null | undefined;
let cached: SavedProfile | null = null;

function parse(raw: string | null): SavedProfile | null {
  if (!raw) return null;
  try {
    const p = JSON.parse(raw);
    if (!p || typeof p !== "object" || typeof p.name !== "string" || typeof p.preset !== "string") return null;
    return {
      name: p.name,
      description: typeof p.description === "string" ? p.description : "",
      rationale: typeof p.rationale === "string" ? p.rationale : "",
      engine: p.engine === "model" || p.engine === "rules" ? p.engine : undefined,
      preset: p.preset,
      theme_weights: p.theme_weights ?? {},
      indicator_weights: p.indicator_weights ?? {},
      ...(p.tweaks ? { tweaks: { theme_weights: p.tweaks.theme_weights ?? {}, indicator_weights: p.tweaks.indicator_weights ?? {} } } : {}),
    };
  } catch {
    return null;
  }
}

/** The saved profile, the same object until the storage changes. */
export function readSavedProfile(): SavedProfile | null {
  let raw: string | null = null;
  try {
    raw = localStorage.getItem(KEY);
  } catch {
    return null;
  }
  if (raw !== cachedRaw) {
    cachedRaw = raw;
    cached = parse(raw);
  }
  return cached;
}

/** Keep a profile (or forget it with null) and tell the hooks. */
export function saveProfile(profile: SavedProfile | null): void {
  try {
    if (profile) localStorage.setItem(KEY, JSON.stringify(profile));
    else localStorage.removeItem(KEY);
  } catch {
    /* storage blocked: the profile lives in the page state for this visit */
  }
  listeners.forEach((l) => l());
}

export function useSavedProfile(): SavedProfile | null {
  return useSyncExternalStore(
    (cb) => {
      listeners.add(cb);
      return () => listeners.delete(cb);
    },
    readSavedProfile,
    () => null,
  );
}

const sameWeights = (a: Record<string, number>, b: Record<string, number>) =>
  JSON.stringify(Object.entries(a).sort()) === JSON.stringify(Object.entries(b).sort());

/** What a freshly generated profile becomes in storage. */
export function savedFromProfile(p: WeightProfile, description: string): SavedProfile {
  return {
    name: p.name,
    description,
    rationale: p.rationale,
    engine: p.engine,
    preset: p.preset,
    theme_weights: { ...(p.theme_weights ?? {}) },
    indicator_weights: { ...(p.indicator_weights ?? {}) },
  };
}

/** The part of the page state a saved profile sets: its base preset, the sliders as last
 * left (or as generated), and the profile the assistant is told about. */
export function profileState(saved: SavedProfile): Pick<LiveabilityState, "preset" | "theme_weights" | "indicator_weights" | "profile"> {
  const current = saved.tweaks ?? saved;
  return {
    preset: saved.preset,
    theme_weights: { ...current.theme_weights },
    indicator_weights: { ...current.indicator_weights },
    profile: { name: saved.name, description: saved.description, rationale: saved.rationale },
  };
}

/** Whether the sliders have moved from what the profile generated. */
export function isTweaked(saved: SavedProfile, s: Pick<LiveabilityState, "theme_weights" | "indicator_weights">): boolean {
  return !sameWeights(saved.theme_weights, s.theme_weights) || !sameWeights(saved.indicator_weights, s.indicator_weights);
}

/** The saved copy after a state change while a profile is active: the same profile keeps
 * its generated weights and records the sliders as tweaks; a new one (the assistant built
 * it, or a link named it) is kept as generated from the state. */
export function savedFromState(s: LiveabilityState, prev: SavedProfile | null): SavedProfile | null {
  if (!s.profile) return prev;
  const same =
    prev && prev.name === s.profile.name && (s.profile.rationale === "" || s.profile.rationale === prev.rationale);
  if (same && prev) {
    const tweaked = isTweaked(prev, s);
    const { tweaks: _old, ...rest } = prev;
    void _old;
    return tweaked ? { ...rest, tweaks: { theme_weights: { ...s.theme_weights }, indicator_weights: { ...s.indicator_weights } } } : rest;
  }
  return {
    name: s.profile.name,
    description: s.profile.description ?? "",
    rationale: s.profile.rationale ?? "",
    preset: s.preset,
    theme_weights: { ...s.theme_weights },
    indicator_weights: { ...s.indicator_weights },
  };
}
