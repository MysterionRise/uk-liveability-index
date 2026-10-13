"use client";

/**
 * What the interface calls an area tier: "small area" and "neighbourhood" in England and
 * Wales, "data zone" and "intermediate zone" in Scotland. The words come from each
 * nation's `levels` in the manifest (config/nations.yaml), translated by the locale's
 * `levels` catalogue (config/i18n), with English as the fallback.
 */
import { useLocale } from "next-intl";
import { useMemo } from "react";

import { useData } from "@/components/AppData";
import type { Manifest } from "@/lib/data";

/** The API's level names → the tiers of the manifest's geography (lix_core LEVEL_KEYS) */
export const LEVEL_KEYS: Record<string, string> = {
  lsoa: "low",
  msoa: "mid",
  lad: "upper",
};

export type LevelWords = (
  nation: string | undefined,
  level: string,
  count?: number,
) => string;

/** The nation an area code belongs to (the first letter of a GSS code). */
export function nationOf(code: string | null | undefined): string | undefined {
  return code ? code[0] : undefined;
}

export function levelWords(
  manifest: Manifest | null,
  locale: string,
): LevelWords {
  const base = locale.split("-")[0];
  const catalogue = (
    base === "en" ? undefined : manifest?.i18n?.[base]?.levels
  ) as
    | Record<string, Record<string, { one?: string; other?: string }>>
    | undefined;
  return (nation, level, count = 1) => {
    const tier = LEVEL_KEYS[level] ?? level;
    const code = nation ?? manifest?.geography.active[0];
    const translated = code ? catalogue?.[code]?.[tier] : undefined;
    const english = code
      ? manifest?.geography.nations[code]?.levels[tier]
      : undefined;
    const one = translated?.one ?? english?.label;
    const other = translated?.other ?? english?.labels;
    const word = count === 1 ? one : other;
    return word ?? (count === 1 ? "area" : "areas");
  };
}

export function useLevelWords(): LevelWords {
  const { manifest } = useData();
  const locale = useLocale();
  return useMemo(() => levelWords(manifest, locale), [manifest, locale]);
}
