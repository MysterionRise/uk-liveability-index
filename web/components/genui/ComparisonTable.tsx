"use client";

import { useLocale, useTranslations } from "next-intl";

import { useData } from "@/components/AppData";
import { Card, Muted, formatValue } from "@/components/ui";
import type { Comparison } from "@/lib/contracts.gen";
import { useLabels } from "@/lib/labels";
import { themeVar } from "@/lib/palette";

export function ComparisonTable({ comparison }: { comparison: Comparison }) {
  const { manifest } = useData();
  const t = useTranslations("Comparison");
  const tq = useTranslations("Quality");
  const locale = useLocale();
  const labels = useLabels();
  const units = new Map(manifest?.indicators.map((i) => [i.id, i.unit]));
  const areas = comparison.areas;
  const themeRows = Object.entries(comparison.theme_labels);
  const best = (values: (number | null)[]) => Math.max(...values.map((v) => v ?? -Infinity));

  return (
    <Card testId="comparison" title={t("title")} subtitle={t("subtitle", { preset: comparison.preset })}>
      <div className="overflow-x-auto">
        <table className="w-full text-xs">
          <thead>
            <tr className="text-left text-[var(--text-secondary)]">
              <th className="py-1 pr-2 font-medium" />
              {areas.map((a) => (
                <th key={a.code} className="py-1 pr-2 font-medium">{a.name}</th>
              ))}
            </tr>
          </thead>
          <tbody className="tabular-nums">
            <tr className="border-t border-[var(--border)]">
              <td className="py-1 pr-2 font-medium">{t("overall")}</td>
              {areas.map((a) => {
                const top = a.overall === best(areas.map((x) => x.overall));
                return <td key={a.code} className={`py-1 pr-2 text-sm ${top ? "font-semibold" : ""}`}>{a.overall?.toFixed(0) ?? "–"}</td>;
              })}
            </tr>
            {themeRows.map(([theme]) => {
              const label = labels.theme(theme);
              const values = areas.map((a) => (a.themes as Record<string, number | null>)[theme] ?? null);
              const top = best(values);
              return (
                <tr key={theme} className="border-t border-[var(--border)]">
                  <td className="py-1 pr-2 text-[var(--text-secondary)]">{label}</td>
                  {values.map((v, i) => (
                    <td key={areas[i].code} className="py-1 pr-2">
                      <span className="flex items-center gap-1.5">
                        <span className={`w-6 ${v === top ? "font-semibold" : ""}`}>{v?.toFixed(0) ?? "–"}</span>
                        <span className="h-1.5 w-12 rounded-r-[4px] bg-[var(--track)]">
                          <span className="block h-1.5 rounded-r-[4px]" style={{ width: `${v ?? 0}%`, background: themeVar(theme) }} />
                        </span>
                      </span>
                    </td>
                  ))}
                </tr>
              );
            })}
            {Object.keys(comparison.indicator_labels).map((id) => (
              <tr key={id} className="border-t border-[var(--border)]">
                <td className="py-1 pr-2 text-[var(--text-secondary)]">
                  {labels.indicator(id)}
                  {comparison.stand_ins?.[id] && (
                    <span className="block text-[10px] text-[var(--text-muted)]">
                      {tq("standsInFor", { label: labels.indicator(comparison.stand_ins[id]) })}
                    </span>
                  )}
                </td>
                {areas.map((a) => (
                  <td key={a.code} className="py-1 pr-2">
                    {formatValue((a.indicators as Record<string, number | null>)[id], units.get(id) ?? "", locale)}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <Muted>{t("note")}</Muted>
    </Card>
  );
}
