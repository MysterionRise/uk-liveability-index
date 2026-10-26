"use client";

import { useLocale, useTranslations } from "next-intl";

import type { MonthNames } from "@/lib/period";

/**
 * The month names to print dates with: the catalogue's for a language the browser cannot
 * format itself (Welsh, Gaelic, Irish), none for English, which `Intl` handles.
 */
export function useMonths(): MonthNames | undefined {
  const locale = useLocale();
  const t = useTranslations("Dates");
  return locale === "en" ? undefined : (t.raw("months") as MonthNames);
}
