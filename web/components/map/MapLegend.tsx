import { useTranslations } from "next-intl";
import { useEffect, useState } from "react";

import { gradientCss } from "@/lib/colors";

/**
 * The diverging ramp as it appears on the map: worse than typical ← typical → better.
 * On a phone the legend starts folded to its title, since with the header it would take
 * most of the map (#188); a tap opens it, and a wider screen always shows it.
 */
/** Whether the viewport is a phone's (Tailwind's sm breakpoint); false on the server. */
function usePhone(): boolean {
  const [phone, setPhone] = useState(false);
  useEffect(() => {
    const query = window.matchMedia("(max-width: 639px)");
    const update = () => setPhone(query.matches);
    update();
    query.addEventListener("change", update);
    return () => query.removeEventListener("change", update);
  }, []);
  return phone;
}

export function MapLegend({ label, against, dark }: { label: string; against: string; dark: boolean }) {
  const t = useTranslations("Legend");
  const [open, setOpen] = useState(false);
  const phone = usePhone();
  return (
    <div
      className="absolute bottom-20 left-3 z-10 w-56 rounded-lg border sm:bottom-8 sm:w-[17rem] border-[var(--border)] bg-[var(--surface-1)] p-3 text-xs shadow-sm"
      data-testid="legend"
    >
      {/* The fold is a phone thing: on a wider screen the title is plain text, so no
          button announces a fold that is not there (and the label is in the page once) */}
      {phone ? (
        <button
          type="button"
          className="flex w-full items-center justify-between text-left font-medium text-[var(--text-primary)]"
          aria-expanded={open}
          aria-controls="legend-body"
          onClick={() => setOpen((v) => !v)}
          data-testid="legend-toggle"
        >
          <span>{label}</span>
          <span aria-hidden="true" className="ml-2 text-[var(--text-muted)]">
            {open ? "–" : "+"}
          </span>
        </button>
      ) : (
        <div className="font-medium text-[var(--text-primary)]">{label}</div>
      )}
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
