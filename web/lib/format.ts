/** Indicator values in words and units a reader can take in at a glance. */
export function formatValue(value: number | null | undefined, unit: string, locale = "en-GB"): string {
  if (value == null || Number.isNaN(value)) return "–";
  // Round as toFixed does (so the English output is unchanged), then group and punctuate
  // for the locale
  const n = (v: number, digits = 0) =>
    Number(v.toFixed(digits)).toLocaleString(locale, { minimumFractionDigits: digits, maximumFractionDigits: digits });
  const code = unitCode(unit);
  if (code === "gbp" || code === "gbp_year") return `£${n(Math.round(value))}`;
  if (code === "metres") return value >= 1000 ? `${n(value / 1000, 1)} km` : `${n(Math.round(value))} m`;
  if (code.startsWith("pct")) return `${n(value, value > 0 && value < 10 ? 1 : 0)}%`;
  // A share of residents (IoD rates are 0–1)
  if (code === "rate") return `${n(Math.round(value * 100))}%`;
  // Access and quality indices run 0–1 (1 = what a typical suburb has); out of 10 reads
  // better than a decimal and can't be mistaken for a 0–100 score
  if (code === "index" || code === "quality" || code === "rating") return `${(value * 10).toFixed(1).replace(/\.0$/, "")}/10`;
  if (Math.abs(value) >= 100) return n(Math.round(value));
  return n(value, 1);
}

/** Whether formatValue already prints the unit (£, m/km, %, out of 10); the others are
 * appended by the caller from the catalogue in the interface language. */
export function formatsUnit(unit: string): boolean {
  const code = unitCode(unit);
  return code === "gbp" || code === "metres" || code.startsWith("pct") || code === "rate" ||
    code === "index" || code === "quality" || code === "rating" || code === "score" || code.startsWith("score");
}

/** The unit text to show after a formatted value: "hours a year", "°C", "a year" after a
 * £ figure; nothing when the formatter printed it. */
export function unitSuffix(unit: string, translated: string): string {
  const code = unitCode(unit);
  if (code === "gbp_year") return translated.replace(/^£\s*/, "");
  if (formatsUnit(unit)) return "";
  return translated;
}

/** The unit's code when given its English text (lix_core.config.UNIT_CODES), else as is. */
export function unitCode(unit: string): string {
  if (unit.startsWith("£")) return unit === "£" ? "gbp" : "gbp_year";
  if (unit === "metres") return "metres";
  if (unit.startsWith("%")) return "pct";
  if (unit === "rate") return "rate";
  if (/^(index|quality|rating) 0–1/.test(unit)) return unit.split(" ")[0];
  return unit;
}

/** A percentile as "better than N%": whole numbers, and the top area is 99, never 100. */
export function pctRank(percentile: number): number {
  return Math.min(99, Math.round(percentile));
}
