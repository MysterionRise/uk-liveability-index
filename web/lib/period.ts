/**
 * The period a source's data describes (the manifest's `sources[slug].period`), printed
 * in the interface language from its dates. A "text" period has no dates to print, so
 * its English label stands.
 */
export interface Period {
  label: string;
  start?: string | null;
  end?: string | null;
  kind?: "year" | "month" | "day" | "range" | "text";
}

function utcDate(iso: string): Date | null {
  const d = new Date(`${iso}T00:00:00Z`);
  return Number.isNaN(d.getTime()) ? null : d;
}

export function formatPeriod(period: Period, locale = "en-GB"): string {
  const fmt = (o: Intl.DateTimeFormatOptions) => new Intl.DateTimeFormat(locale, { timeZone: "UTC", ...o });
  const start = period.start ? utcDate(period.start) : null;
  const end = period.end ? utcDate(period.end) : null;
  switch (period.kind) {
    case "year":
      return end ? fmt({ year: "numeric" }).format(end) : period.label;
    case "month":
      return end ? fmt({ month: "long", year: "numeric" }).format(end) : period.label;
    case "day":
      return end ? fmt({ day: "numeric", month: "long", year: "numeric" }).format(end) : period.label;
    case "range": {
      if (!start || !end) return period.label;
      const month = fmt({ month: "long", year: "numeric" });
      return `${month.format(start)} – ${month.format(end)}`;
    }
    default:
      return period.label;
  }
}

/** "data to August 2026": the latest day the data reaches, as a month. */
export function formatDataTo(iso: string, locale = "en-GB"): string {
  const d = utcDate(iso);
  return d ? new Intl.DateTimeFormat(locale, { timeZone: "UTC", month: "long", year: "numeric" }).format(d) : iso;
}
