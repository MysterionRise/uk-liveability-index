import { describe, expect, it } from "vitest";

import { formatDataTo, formatPeriod } from "./period";

describe("formatPeriod", () => {
  it("prints each kind from its dates in the given language", () => {
    expect(formatPeriod({ label: "2024", start: "2024-01-01", end: "2024-12-31", kind: "year" })).toBe("2024");
    expect(formatPeriod({ label: "August 2026", start: "2026-08-01", end: "2026-08-31", kind: "month" })).toBe(
      "August 2026",
    );
    expect(formatPeriod({ label: "12 months to September 2026", start: "2026-09-01", end: "2026-09-30", kind: "month" }, "cy-GB")).toBe(
      "Medi 2026",
    );
    expect(formatPeriod({ label: "11 October 2026", start: "2026-10-11", end: "2026-10-11", kind: "day" })).toBe(
      "11 October 2026",
    );
    expect(
      formatPeriod({ label: "September 2023 to August 2026", start: "2023-09-01", end: "2026-08-31", kind: "range" }),
    ).toBe("September 2023 – August 2026");
  });

  it("keeps a text label as it is", () => {
    expect(formatPeriod({ label: "Round 4 (2021 traffic)", start: "2021-01-01", end: "2021-12-31", kind: "text" })).toBe(
      "Round 4 (2021 traffic)",
    );
    expect(formatPeriod({ label: "Census 2021 geography" })).toBe("Census 2021 geography");
  });

  it("falls back to the label when a date is missing", () => {
    expect(formatPeriod({ label: "some month", kind: "month" })).toBe("some month");
  });
});

describe("formatDataTo", () => {
  it("names the month the data reaches", () => {
    expect(formatDataTo("2026-08-31")).toBe("August 2026");
    expect(formatDataTo("2026-08-31", "cy-GB")).toBe("Awst 2026");
  });
});
