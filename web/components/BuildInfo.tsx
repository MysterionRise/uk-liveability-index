"use client";

import { useLocale, useTranslations } from "next-intl";

import { useData } from "@/components/AppData";
import { useMonths } from "@/lib/dates";
import { BCP47, type Locale } from "@/lib/i18n";
import { formatDataTo, formatDay } from "@/lib/period";

/** Which release and data build the page is showing (the About page). */
export function BuildInfo({ version }: { version: string }) {
  const { manifest } = useData();
  const t = useTranslations("BuildInfo");
  const locale = useLocale();
  const months = useMonths();
  const tag = BCP47[locale as Locale] ?? "en-GB";
  const built = manifest ? formatDay(manifest.generated_at, tag, months) : null;
  const dataTo = manifest?.data_to ? formatDataTo(manifest.data_to, tag, months) : null;
  return (
    <p className="mb-6 rounded-md border border-[var(--border)] bg-[var(--surface-1)] px-3 py-2 text-sm" data-testid="build-info">
      {t("version", { version })}
      {built && ` · ${t("dataBuilt", { date: built })}`}
      {dataTo && ` · ${t("dataTo", { date: dataTo })}`}
      {manifest?.data_release && ` · ${t("dataRelease", { tag: manifest.data_release })}`}
      {manifest?.demo && ` (${t("demo")})`} ·{" "}
      <a className="underline" href="https://github.com/MysterionRise/uk-liveability-index" target="_blank" rel="noreferrer">
        {t("sourceCode")}
      </a>
      <br />
      <span className="text-[var(--text-secondary)]">{t("disclaimer")}</span>
    </p>
  );
}
