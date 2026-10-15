"use client";

/**
 * The data's labels in the interface language: themes, indicators, units, presets and
 * profile caveats come from the manifest's catalogue for the locale (config/i18n), with
 * the manifest's English as the fallback for anything a draft lacks.
 */
import { useLocale } from "next-intl";
import { useCallback, useMemo } from "react";

import { useData } from "@/components/AppData";
import type { Manifest } from "@/lib/data";

export interface Labels {
  theme: (id: string) => string;
  themeDescription: (id: string) => string;
  indicator: (id: string) => string;
  indicatorDescription: (id: string) => string;
  unit: (indicatorId: string) => string;
  preset: (id: string) => string;
  presetDescription: (id: string) => string;
  /** A profile caveat by code; the English sentence is the fallback */
  flag: (code: string, english?: string) => string;
}

type Entry = { label?: string; description?: string };

export function labelsFor(manifest: Manifest | null, locale: string): Labels {
  const base = locale.split("-")[0];
  const cat = base === "en" ? undefined : manifest?.i18n?.[base];
  const themes = (cat?.themes ?? {}) as Record<string, Entry>;
  const indicators = (cat?.indicators ?? {}) as Record<string, Entry>;
  const presets = (cat?.presets ?? {}) as Record<string, Entry>;
  const units = (cat?.units ?? {}) as Record<string, string>;
  const flags = (cat?.flags ?? {}) as Record<string, string>;
  const enIndicator = (id: string) => manifest?.indicators.find((i) => i.id === id);
  return {
    theme: (id) => themes[id]?.label ?? manifest?.themes[id]?.label ?? id,
    themeDescription: (id) => themes[id]?.description ?? manifest?.themes[id]?.description ?? "",
    indicator: (id) => indicators[id]?.label ?? enIndicator(id)?.label ?? id,
    indicatorDescription: (id) => indicators[id]?.description ?? enIndicator(id)?.description ?? "",
    unit: (id) => {
      const en = enIndicator(id);
      return (en?.unit_code && units[en.unit_code]) || en?.unit || "";
    },
    preset: (id) => presets[id]?.label ?? manifest?.presets[id]?.label ?? id,
    presetDescription: (id) => presets[id]?.description ?? manifest?.presets[id]?.description ?? "",
    flag: (code, english) => flags[code] ?? english ?? code,
  };
}

/** A preset's label for the name the API returns ("balanced (adjusted)" when the weights were changed). */
export function presetLabel(labels: Labels, preset: string, adjusted: string): string {
  const suffix = " (adjusted)";
  return preset.endsWith(suffix)
    ? `${labels.preset(preset.slice(0, -suffix.length))} ${adjusted}`
    : labels.preset(preset);
}

export function useLabels(): Labels {
  const { manifest } = useData();
  const locale = useLocale();
  const labels = useMemo(() => labelsFor(manifest, locale), [manifest, locale]);
  // A stable reference per manifest and locale
  return useCallback(() => labels, [labels])();
}
