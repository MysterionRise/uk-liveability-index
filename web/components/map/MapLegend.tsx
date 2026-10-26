import { useTranslations } from "next-intl";
import { useState } from "react";

import { gradientCss } from "@/lib/colors";

/**
 * The diverging ramp as it appears on the map: worse than typical ← typical → better.
 * On a phone the legend starts folded to its title, since with the header it would take
 * most of the map (#188); a tap opens it, and a wider screen always shows it.
 */
export function MapLegend({ label, against, dark }: { label: string; against: string; dark: boolean }) {
  const t = useTranslations("Legend");
  const [open, setOpen] = useState(false);
  return (
    <div
      className="absolute bottom-20 left-3 z-10 w-56 rounded-lg border sm:bottom-8 sm:w-[17rem] border-[var(--border)] bg-[var(--surface-1)] p-3 text-xs shadow-sm"
      data-testid="legend"
    >
      <button
        type="button"
        className="flex w-full items-center justify-between text-left font-medium text-[var(--text-primary)] sm:pointer-events-none"
        aria-expanded={open}
        aria-controls="legend-body"
        onClick={() => setOpen((v) => !v)}
        data-testid="legend-toggle"
      >
        <span>{label}</span>
        <span aria-hidden="true" className="ml-2 text-[var(--text-muted)] sm:hidden">
          {open ? "–" : "+"}
        </span>
      </button>
      <div id="legend-body" className={open ? "block" : "hidden sm:block"} data-testid="legend-body">
      <div className="mb-2 text-[var(--text-muted)]">{against}</div>
      <div className="h-2.5 rounded-sm" style={{ background: gradientCss(dark) }} />
      <div className="mt-1 grid grid-cols-3 text-[var(--text-secondary)]">
        <span>{t("worse")}</span>
        <span className="text-center">{t("typical")}</span>
        <span className="text-right">{t("better")}</span>
      </div>
      <div className="grid grid-cols-3 tabular-nums text-[var(--text-muted)]">
        <span>0</span>
        <span className="text-center">50</span>
        <span className="text-right">100</span>
      </div>
      <div className="mt-2 flex items-center gap-1.5 text-[var(--text-muted)]">
        <span className="inline-block h-2.5 w-2.5 rounded-sm bg-[var(--no-data)]" /> {t("noData")}
      </div>
      </div>
    </div>
  );
}
