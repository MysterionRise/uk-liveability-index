"use client";

import { useFormatter, useLocale, useTranslations } from "next-intl";
import { useState } from "react";

import { Card, Muted, ValueWithUnit, formatValue, useQualityNote } from "@/components/ui";
import type { Explanation } from "@/lib/contracts.gen";
import { BCP47, type Locale } from "@/lib/i18n";
import { useLabels, usePresetLabel } from "@/lib/labels";
import { themeVar } from "@/lib/palette";
import { formatPeriod } from "@/lib/period";

export function MethodExplainer({ explanation }: { explanation: Explanation }) {
  const t = useTranslations("Explainer");
  const tw = useTranslations("Weights");
  const locale = useLocale();
  const format = useFormatter();
  const qualityNote = useQualityNote();
  const labels = useLabels();
  const presetName = usePresetLabel();
  const formatDate = (iso: string) => {
    const d = new Date(iso);
    return Number.isNaN(d.getTime()) ? iso : format.dateTime(d, { day: "numeric", month: "short", year: "numeric" });
  };
  const [open, setOpen] = useState<string | null>(explanation.contributions.length === 1 ? explanation.contributions[0].theme : null);
  const maxContribution = Math.max(...explanation.contributions.map((c) => c.contribution ?? 0), 1);

  return (
    <Card
      testId="explainer"
      title={t("title", { name: explanation.name, score: explanation.overall?.toFixed(0) ?? "–" })}
      subtitle={t("subtitle", { preset: presetName(explanation.preset) })}
    >
      <ul>
        {explanation.contributions.map((c) => (
          <li key={c.theme} className="border-t border-[var(--border)] first:border-t-0">
            <button
              className="grid w-full grid-cols-[minmax(0,8rem)_1fr_4.5rem] items-center gap-2 py-1 text-left hover:bg-[var(--hover)]"
              onClick={() => setOpen(open === c.theme ? null : c.theme)}
              aria-expanded={open === c.theme}
            >
              <span className="truncate text-xs">{labels.theme(c.theme)}</span>
              <span className="h-2 rounded-r-[4px] bg-[var(--track)]">
                <span
                  className="block h-2 rounded-r-[4px]"
                  style={{ width: `${((c.contribution ?? 0) / maxContribution) * 100}%`, background: themeVar(c.theme) }}
                />
              </span>
              <span className="text-right text-xs tabular-nums text-[var(--text-secondary)]">
                {c.score?.toFixed(0) ?? "–"} × {(c.weight_share * 100).toFixed(0)}%
              </span>
            </button>
            {open === c.theme && (
              <table className="mb-2 w-full text-xs">
                <tbody className="tabular-nums">
                  {c.indicators.map((i) => (
                    <tr key={i.id} className={i.role === "context" ? "text-[var(--text-muted)]" : ""}>
                      <td className="py-0.5 pr-2">{labels.indicator(i.id)}{i.role === "context" && ` ${t("notScored")}`}</td>
                      <td className="py-0.5 pr-2 text-right"><ValueWithUnit value={i.value} id={i.id} unit={i.unit} /></td>
                      <td className="py-0.5 text-right">{i.score?.toFixed(0) ?? "–"}</td>
                      <td className="py-0.5 pl-1 text-[var(--text-muted)]">{qualityNote(i.quality) ?? ""}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </li>
        ))}
      </ul>
      {explanation.caveats.length > 0 && (
        <details className="mt-2 text-xs text-[var(--text-secondary)]">
          <summary className="cursor-pointer">{t("caveats", { count: explanation.caveats.length })}</summary>
          <ul className="mt-1 list-disc pl-4">{explanation.caveats.map((c) => <li key={c}>{c}</li>)}</ul>
        </details>
      )}
      <details className="mt-1 text-xs text-[var(--text-muted)]" data-testid="sources">
        <summary className="cursor-pointer">{t("sources", { count: explanation.sources.length })}</summary>
        <ul className="mt-1 list-disc pl-4">
          {explanation.sources.map((s) => (
            <li key={s.id} title={s.attribution}>
              {s.title}
              {s.period ? (
                <> · {formatPeriod(s.period, BCP47[locale as Locale] ?? "en-GB")}</>
              ) : (
                s.fetched_at && <> · {t("dataAsOf", { date: formatDate(s.fetched_at) })}</>
              )}
              {s.stale && (
                <span className="text-[var(--critical)]">
                  {" "}
                  · {t(s.stale_reason === "newer_edition" ? "staleNewer" : "staleOld")}
                </span>
              )}
            </li>
          ))}
        </ul>
      </details>
      <Muted>{t("note")}</Muted>
    </Card>
  );
}
