"use client";

/** Copy that depends on the data pack and the interface language. */
import { useLocale, useTranslations } from "next-intl";

import { useData } from "@/components/AppData";
import { coverageName, coverageShape } from "@/lib/data";

type NationKey = "uk" | "IE" | "E" | "W" | "S" | "N" | "I";
type InKey = "in_uk" | "in_uk_I" | "in_I" | "in_E" | "in_W" | "in_S" | "in_N" | "in_E_W";
type OfKey = "of_uk" | "of_uk_I" | "of_I" | "of_E" | "of_W" | "of_S" | "of_N" | "of_E_W";

/** Joins names in the interface language: browsers have no list patterns for Welsh,
 * Gaelic or Irish, so the catalogue's own "and" is used, and Welsh contracts "a yr Alban"
 * to "a'r Alban" and takes "ac" before a vowel ("ac Iwerddon"). */
function useListJoiner(): (names: string[]) => string {
  const t = useTranslations("Nations");
  const locale = useLocale().split("-")[0];
  return (names) => {
    const last = names[names.length - 1];
    let joiner = `${t("and")} ${last}`;
    if (locale === "cy" && /^yr /i.test(last)) joiner = `a'r ${last.slice(3)}`;
    else if (locale === "cy" && /^[aeiouâêîôûŵŷ]/i.test(last)) joiner = `ac ${last}`;
    return `${names.slice(0, -1).join(", ")} ${joiner}`;
  };
}

/** "England and Wales", "the UK and Ireland" in the interface language: the nations of
 * the current data pack grouped by country, or the `codes` given (an indicator built for
 * one nation names that nation). */
export function useCoverageName(codes?: string[]): string {
  const { manifest } = useData();
  const t = useTranslations("Nations");
  const join = useListJoiner();
  const shape = coverageShape(manifest, codes);
  if (shape === "E_W") return t("E_W");
  return coverageName(manifest, (code) => t(code as NationKey), join, codes);
}

/** "in the UK" / "yng Nghymru" / "sa RA": the coverage with the preposition each language
 * needs before that noun (a nation's name mutates or takes a different word). With
 * `codes`, the phrase for those nations alone ("in Northern Ireland"). */
export function useCoverageIn(codes?: string[]): string {
  const { manifest } = useData();
  const t = useTranslations("Nations");
  const list = useCoverageName(codes);
  const shape = coverageShape(manifest, codes);
  if (shape) return t(`in_${shape}` as InKey);
  return `${t("in")} ${list}`;
}

/** "of the UK" / "of Ireland" / "of the UK and Ireland": what a percentile compares
 * against, in the genitive each language needs. */
export function useCoverageOf(codes?: string[]): string {
  const { manifest } = useData();
  const t = useTranslations("Nations");
  const list = useCoverageName(codes);
  const shape = coverageShape(manifest, codes);
  if (shape) return t(`of_${shape}` as OfKey);
  return `${t("of")} ${list}`;
}
