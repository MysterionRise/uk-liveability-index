/** Small shared pieces: cards, score bars in their theme's colour, bands and number formatting. */
import { useLocale, useTranslations } from "next-intl";
import type { ReactNode } from "react";

import { bandVar } from "@/lib/colors";
import { useCoverageOf } from "@/lib/copy";
import { classOf, formatValue, pctRank, unitCode, unitSuffix } from "@/lib/format";
import { useLabels } from "@/lib/labels";
import { themeVar } from "@/lib/palette";

export { formatValue } from "@/lib/format";

export function Card({ title, subtitle, children, testId }: {
  title?: ReactNode;
  subtitle?: ReactNode;
  children: ReactNode;
  testId?: string;
}) {
  return (
    <section
      data-testid={testId}
      className="my-2 rounded-xl border border-[var(--border)] bg-[var(--surface-1)] p-3 text-sm text-[var(--text-primary)]"
    >
      {title && <h3 className="text-[15px] font-semibold leading-snug">{title}</h3>}
      {subtitle && <p className="mt-0.5 text-xs text-[var(--text-secondary)]">{subtitle}</p>}
      <div className={title ? "mt-2" : ""}>{children}</div>
    </section>
  );
}

/** A 0–100 score as a thin bar with its value in text (the bar never carries it alone). */
export interface BarMark {
  value: number | null | undefined;
  kind: "local" | "england";
  label: string;
}

export function ScoreBar({
  label,
  score,
  hint,
  marks = [],
  theme,
}: {
  label: string;
  score: number | null | undefined;
  hint?: string;
  marks?: BarMark[];
  /** Theme id: the bar takes that theme's colour (ink when absent, e.g. an overall score) */
  theme?: string;
}) {
  const v = score == null || Number.isNaN(score) ? null : Math.max(0, Math.min(100, score));
  const shown = marks.filter((m) => m.value != null && !Number.isNaN(m.value));
  return (
    <div
      className="grid grid-cols-[minmax(0,9.5rem)_1fr_2.5rem] items-center gap-2 py-0.5"
      title={[hint, ...shown.map((m) => `${m.label}: ${Math.round(m.value as number)}`)].filter(Boolean).join(" · ")}
    >
      <span className="truncate text-xs text-[var(--text-secondary)]" title={label}>{label}</span>
      <span className="relative h-2 rounded-r-[4px] bg-[var(--track)]">
        {v !== null && <span className="block h-2 rounded-r-[4px]" style={{ width: `${v}%`, background: themeVar(theme) }} />}
        {shown.map((m) => (
          <span
            key={m.kind}
            aria-hidden
            className={`absolute -top-0.5 h-3 ${m.kind === "local" ? "w-[2px] bg-[var(--text-primary)]" : "w-px bg-[var(--text-muted)]"}`}
            style={{ left: `calc(${Math.max(0, Math.min(100, m.value as number))}% - 1px)` }}
          />
        ))}
      </span>
      <span className="text-right text-xs tabular-nums text-[var(--text-primary)]">{v === null ? "–" : Math.round(v)}</span>
    </div>
  );
}

export function Band({ band, percentile, nationOf, urbanRural }: { band?: number | null; percentile?: number | null; nationOf?: string; urbanRural?: boolean }) {
  const t = useTranslations("Band");
  const coverageOf = useCoverageOf();
  if (!band) return null;
  return (
    <span className="inline-flex items-center gap-1.5 rounded-full border border-[var(--border)] px-2 py-0.5 text-xs text-[var(--text-secondary)]">
      <span className="flex gap-0.5" aria-hidden>
        {[1, 2, 3, 4, 5].map((b) => (
          <span
            key={b}
            className="h-2 w-1.5 rounded-[1px] border border-[var(--border)]"
            style={{ background: b <= band ? bandVar(band) : "var(--track)" }}
          />
        ))}
      </span>
      {t(String(Math.min(5, Math.max(1, band))) as "1" | "2" | "3" | "4" | "5")}
      {percentile != null &&
        ` · ${
          urbanRural
            ? t("betterThanUrbanRural", { pct: pctRank(percentile) })
            : nationOf
              ? t("betterThanNation", { pct: pctRank(percentile), nationOf })
              : t("betterThan", { pct: pctRank(percentile), of: coverageOf })
        }`}
    </span>
  );
}

const QUALITY_KEYS = ["imputed", "low_n", "broadcast_msoa", "broadcast_lad", "not_available", "missing", "capped"] as const;

/** A short note for a value that isn't measured directly for the LSOA ("estimated"). */
export function useQualityNote(): (quality: string | null | undefined) => string | undefined {
  const t = useTranslations("Quality");
  return (quality) =>
    quality && (QUALITY_KEYS as readonly string[]).includes(quality) && quality !== "missing"
      ? t(quality as (typeof QUALITY_KEYS)[number])
      : undefined;
}

export function Muted({ children }: { children: ReactNode }) {
  return <p className="mt-2 text-xs text-[var(--text-muted)]">{children}</p>;
}

export function parseResult<T>(result: unknown): T | null {
  if (result == null) return null;
  if (typeof result !== "string") return result as T;
  try {
    return JSON.parse(result) as T;
  } catch {
    return null;
  }
}

/** A raw value with its unit in the interface language ("1,419 hours a year", "16.6 µg/m³"). */
export function ValueWithUnit({ value, id, unit }: { value: number | null | undefined; id: string; unit: string }) {
  const locale = useLocale();
  const labels = useLabels();
  const classes = useTranslations("Classes");
  const code = unitCode(unit);
  if ((code === "class4" || code === "class5") && value != null && !Number.isNaN(value)) {
    // "Good (3.4)": the class the homes' mean rounds to, with the mean beside it
    return (
      <>
        {classes(`${code}.${classOf(value, code)}` as "class4.1")}
        <span className="ml-1 text-[var(--text-muted)]">({formatValue(value, unit, locale)})</span>
      </>
    );
  }
  const suffix = value == null ? "" : unitSuffix(unit, labels.unit(id));
  return (
    <>
      {formatValue(value, unit, locale)}
      {suffix && <span className="ml-1 text-[var(--text-muted)]">{suffix}</span>}
    </>
  );
}
