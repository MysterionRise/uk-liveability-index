"use client";

/** Copy that depends on the data pack and the interface language. */
import { useFormatter, useLocale, useTranslations } from "next-intl";

import { useData } from "@/components/AppData";
import { coverageName } from "@/lib/data";

/** "England and Wales" in the interface language, for the current data pack. */
export function useCoverageName(): string {
  const { manifest } = useData();
  const t = useTranslations("Nations");
  const locale = useLocale();
  // A named pair first ("Lloegr a Chymru" mutates), else the catalogue's own "and":
  // browsers have no list patterns for Welsh, Gaelic or Irish
  const active = manifest?.geography?.active ?? [];
  if (active.length === 2 && active.join("_") === "E_W") return t("E_W");
  return coverageName(
    manifest,
    (code) => t(code as "uk" | "E" | "W" | "S" | "N"),
    (names) => {
      const last = names[names.length - 1];
      // Welsh contracts "a yr Alban" to "a'r Alban"
      const joiner = locale === "cy" && /^yr /i.test(last) ? `a'r ${last.slice(3)}` : `${t("and")} ${last}`;
      return `${names.slice(0, -1).join(", ")} ${joiner}`;
    },
  );
}
