"use client";

import { useLocale, useTranslations } from "next-intl";

import { useLiveability } from "@/components/AppData";
import { Card, Muted, formatValue } from "@/components/ui";
import type { PoiResult } from "@/lib/contracts.gen";

const TITLED = ["well_run_pub", "pub", "gp", "primary_school", "secondary_school", "nursery", "supermarket"] as const;

export function PoiList({ result }: { result: PoiResult }) {
  const { update } = useLiveability();
  const t = useTranslations("Poi");
  const locale = useLocale();
  const title = (TITLED as readonly string[]).includes(result.category)
    ? t(`titles.${result.category as (typeof TITLED)[number]}`)
    : result.category.replaceAll("_", " ");
  const sources = [...new Set(result.pois.map((p) => `${p.source} (${p.licence})`))];

  function detailLine(category: string, d: Record<string, unknown>): string {
    if (category.includes("pub") && d.rating != null) {
      return `${t("hygiene", { rating: String(d.rating) })}${d.real_ale === "yes" ? ` · ${t("realAle")}` : ""}`;
    }
    if (category.includes("school") && typeof d.quality === "number") {
      return `${t("inspection", { score: (d.quality * 100).toFixed(0) })}${d.phase ? ` · ${d.phase}` : ""}`;
    }
    if (typeof d.brand === "string") return d.brand;
    return "";
  }

  return (
    <Card testId="poi-list" title={t("near", { title, place: result.origin_label })} subtitle={t("found", { count: result.pois.length })}>
      {result.pois.length === 0 && <p className="text-xs text-[var(--text-secondary)]">{t("none")}</p>}
      <ul className="divide-y divide-[var(--border)]">
        {result.pois.map((p, i) => (
          <li key={`${p.name}-${i}`}>
            <button
              className="flex w-full items-baseline justify-between gap-2 py-1.5 text-left hover:bg-[var(--hover)]"
              onClick={() =>
                update((s) => ({
                  ...s,
                  map: { ...s.map, bbox: [p.point.lon - 0.006, p.point.lat - 0.004, p.point.lon + 0.006, p.point.lat + 0.004] },
                }))
              }
            >
              <span className="min-w-0">
                <span className="block truncate font-medium">{p.name ?? t("unnamed")}</span>
                <span className="block truncate text-xs text-[var(--text-secondary)]">{detailLine(result.category, p.detail ?? {})}</span>
              </span>
              <span className="shrink-0 text-xs tabular-nums text-[var(--text-secondary)]">{formatValue(p.distance_m, "metres", locale)}</span>
            </button>
          </li>
        ))}
      </ul>
      {sources.length > 0 && <Muted>{t("note", { sources: sources.join("; ") })}</Muted>}
    </Card>
  );
}
