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

/**
 * The twelve month names of the interface language (the `Dates.months` catalogue), for
 * the languages the browser's own date formatting does not know: Chromium ships no Welsh,
 * Gaelic or Irish locale data, so without these "19 October 2026" stays English (#187).
 */
export type MonthNames = readonly string[];

interface DateWords {
  year: (d: Date) => string;
  month: (d: Date) => string;
  day: (d: Date) => string;
}

function words(locale: string, months?: MonthNames): DateWords {
  if (months && months.length === 12) {
    const month = (d: Date) => `${months[d.getUTCMonth()]} ${d.getUTCFullYear()}`;
    return {
      year: (d) => String(d.getUTCFullYear()),
      month,
      day: (d) => `${d.getUTCDate()} ${month(d)}`,
    };
  }
  const fmt = (o: Intl.DateTimeFormatOptions) => new Intl.DateTimeFormat(locale, { timeZone: "UTC", ...o });
  return {
    year: (d) => fmt({ year: "numeric" }).format(d),
    month: (d) => fmt({ month: "long", year: "numeric" }).format(d),
    day: (d) => fmt({ day: "numeric", month: "long", year: "numeric" }).format(d),
  };
}

export function formatPeriod(period: Period, locale = "en-GB", months?: MonthNames): string {
  const w = words(locale, months);
  const start = period.start ? utcDate(period.start) : null;
  const end = period.end ? utcDate(period.end) : null;
  switch (period.kind) {
    case "year":
      return end ? w.year(end) : period.label;
    case "month":
      return end ? w.month(end) : period.label;
    case "day":
      return end ? w.day(end) : period.label;
    case "range":
      return start && end ? `${w.month(start)} – ${w.month(end)}` : period.label;
    default:
      return period.label;
  }
}

/** "data to August 2026": the latest day the data reaches, as a month. */
export function formatDataTo(iso: string, locale = "en-GB", months?: MonthNames): string {
  const d = utcDate(iso);
  return d ? words(locale, months).month(d) : iso;
}

/** "19 October 2026" from an ISO date or datetime. */
export function formatDay(iso: string, locale = "en-GB", months?: MonthNames): string {
  const d = utcDate(iso.slice(0, 10));
  return d ? words(locale, months).day(d) : iso;
}
