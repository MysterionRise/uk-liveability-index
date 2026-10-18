"use client";

/** Copy that depends on the data pack and the interface language. */
import { useLocale, useTranslations } from "next-intl";

import { useData } from "@/components/AppData";
import { coverageName } from "@/lib/data";

type NationKey = "uk" | "E" | "W" | "S" | "N";

/** "England and Wales" in the interface language: the nations of the current data pack,
 * or the `codes` given (an indicator built for one nation names that nation). */
export function useCoverageName(codes?: string[]): string {
  const { manifest } = useData();
  const t = useTranslations("Nations");
  const locale = useLocale().split("-")[0];
  // A named pair first ("Lloegr a Chymru" mutates), else the catalogue's own "and":
  // browsers have no list patterns for Welsh, Gaelic or Irish
  const active = codes ?? manifest?.geography?.active ?? [];
  if (active.length === 2 && active.join("_") === "E_W") return t("E_W");
  return coverageName(
    manifest,
    (code) => t(code as NationKey),
    (names) => {
      const last = names[names.length - 1];
      // Welsh contracts "a yr Alban" to "a'r Alban"
      const joiner = locale === "cy" && /^yr /i.test(last) ? `a'r ${last.slice(3)}` : `${t("and")} ${last}`;
      return `${names.slice(0, -1).join(", ")} ${joiner}`;
    },
    codes,
  );
}

/** "in the UK" / "yng Nghymru" / "sa RA": the coverage with the preposition each language
 * needs before that noun (a nation's name mutates or takes a different word). With
 * `codes`, the phrase for those nations alone ("in Northern Ireland"). */
export function useCoverageIn(codes?: string[]): string {
  const { manifest } = useData();
  const t = useTranslations("Nations");
  const list = useCoverageName(codes);
  const active = codes ?? manifest?.geography?.active ?? [];
  const all = Object.keys(manifest?.geography?.nations ?? {});
  if (!codes && active.length && all.length && active.length === all.length) return t("in_uk");
  if (active.length === 1) return t(`in_${active[0]}` as "in_E" | "in_W" | "in_S" | "in_N");
  if (active.length === 2 && active.join("_") === "E_W") return t("in_E_W");
  return `${t("in")} ${list}`;
}
