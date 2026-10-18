"use client";

import { useLocale, useTranslations } from "next-intl";

import { useLiveability } from "@/components/AppData";
import { Card, Muted, ValueWithUnit, formatValue } from "@/components/ui";
import type { RankResult } from "@/lib/contracts.gen";
import { useCoverageIn } from "@/lib/copy";
import { useLabels, usePresetLabel } from "@/lib/labels";
import { nationOf, useLevelWords } from "@/lib/levels";

// Below this share of plausible weightings keeping it in the list, a result is a close call
const CLOSE_CALL = 0.6;

export function RankedList({ result }: { result: RankResult }) {
  const { update } = useLiveability();
  const t = useTranslations("Ranked");
  const tw = useTranslations("Weights");
  const locale = useLocale();
  const coverageIn = useCoverageIn();
  // A radius around a place, a named area, or the whole coverage, in the interface language
  const where =
    result.place && result.radius_km != null
      ? t("withinKm", { km: result.radius_km, place: result.place })
      : result.place || result.within
        ? t("inPlace", { place: result.place ?? result.within ?? "" })
        : t("inCoverage", { coverageIn });
  const count = result.results.length;
  const labels = useLabels();
  const presetName = usePresetLabel();
  const level = useLevelWords();
  // The tier's word in the results' nation ("data zones" in Edinburgh); a list that
  // spans nations just says "areas"
  const nations = new Set(result.results.map((r) => nationOf(r.code)));
  const tierWord =
    nations.size === 1
      ? level([...nations][0], result.level, count)
      : t("areas");
  // The Celtic titles lead with the tier ("Parthau data yng Nghaerdydd: y 5 gorau")
  const tier = locale.startsWith("en") ? tierWord : tierWord.charAt(0).toUpperCase() + tierWord.slice(1);
  const titleKey = result.direction === "worst" ? "titleWorst" : "title";
  // Ranked by one measure (the quietest, the cheapest…) rather than the weighted overall
  const measure =
    result.sort_by && result.sort_by !== "overall" ? result.sort_by : null;
  const [measureKind, measureId] = measure
    ? measure.split(":", 2)
    : [null, null];
  const measureLabel = measure
    ? measureKind === "theme"
      ? labels.theme(measureId ?? "")
      : labels.indicator(measureId ?? "")
    : null;
  const subtitle = measureLabel
    ? t("rankedBy", { label: measureLabel, count: result.candidates })
    : t("subtitle", { count: result.candidates, preset: presetName(result.preset) });

  return (
    <Card
      testId="ranked-list"
      title={t(titleKey, { count, tier, where })}
      subtitle={subtitle}
    >
      <ol className="divide-y divide-[var(--border)]">
        {result.results.map((r) => (
          <li key={r.code}>
            <button
              className="grid w-full grid-cols-[1.5rem_1fr_auto] items-center gap-2 py-1.5 text-left hover:bg-[var(--hover)]"
              onClick={() =>
                update((s) => ({
                  ...s,
                  map: {
                    ...s.map,
                    selected: r.level === "lsoa" ? r.code : s.map.selected,
                    highlighted: [r.code],
                    bbox: [
                      r.centre.lon - 0.025,
                      r.centre.lat - 0.015,
                      r.centre.lon + 0.025,
                      r.centre.lat + 0.015,
                    ],
                  },
                }))
              }
            >
              <span className="text-xs tabular-nums text-[var(--text-muted)]">
                {r.rank}
              </span>
              <span className="min-w-0">
                <span className="flex items-baseline gap-1.5">
                  <span className="truncate font-medium">{r.name}</span>
                  {r.stability != null && r.stability < CLOSE_CALL && (
                    <span
                      className="shrink-0 rounded border border-[var(--border)] px-1 text-[10px] text-[var(--text-muted)]"
                      title={t("closeCallTitle", {
                        n: result.results.length,
                        pct: Math.round(r.stability * 100),
                      })}
                    >
                      {t("closeCall")}
                    </span>
                  )}
                </span>
                <span className="block truncate text-xs text-[var(--text-secondary)]">
                  {r.local_authority}
                  {r.median_price != null &&
                    ` · ${t("typicalPrice", { price: formatValue(r.median_price, "£", locale) })}`}
                </span>
              </span>
              <span className="text-right">
                {measure ? (
                  <>
                    <span
                      className="block text-base font-semibold tabular-nums"
                      data-testid="sort-value"
                    >
                      {measureKind === "theme"
                        ? (r.sort_value?.toFixed(0) ?? "–")
                        : r.sort_value != null && measureId
                          ? <ValueWithUnit value={r.sort_value} id={measureId} unit={result.sort_unit ?? ""} />
                          : "–"}
                    </span>
                    <span className="block h-1.5 w-14 rounded-r-[4px] bg-[var(--track)]">
                      <span
                        className="block h-1.5 rounded-r-[4px] bg-[var(--accent)]"
                        style={{ width: `${r.sort_score ?? 0}%` }}
                      />
                    </span>
                    <span className="block text-[10px] tabular-nums text-[var(--text-muted)]">
                      {t("overallScore", {
                        score: r.overall?.toFixed(0) ?? "–",
                      })}
                    </span>
                  </>
                ) : (
                  <>
                    <span className="block text-base font-semibold tabular-nums">
                      {r.overall?.toFixed(0) ?? "–"}
                    </span>
                    <span className="block h-1.5 w-14 rounded-r-[4px] bg-[var(--track)]">
                      <span
                        className="block h-1.5 rounded-r-[4px] bg-[var(--accent)]"
                        style={{ width: `${r.overall ?? 0}%` }}
                      />
                    </span>
                  </>
                )}
              </span>
            </button>
          </li>
        ))}
      </ol>
      <Muted>
        {t("note")}
        {result.results.some(
          (r) => r.stability != null && r.stability < CLOSE_CALL,
        ) && ` ${t("closeCallNote")}`}
      </Muted>
    </Card>
  );
}
