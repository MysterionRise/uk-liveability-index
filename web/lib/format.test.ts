import { describe, expect, it } from "vitest";

import { formatValue, unitSuffix } from "./format";

describe("formatValue", () => {
  it("formats money, distance and percentages", () => {
    expect(formatValue(305000, "£")).toBe("£305,000");
    expect(formatValue(944, "metres")).toBe("944 m");
    expect(formatValue(1450, "metres")).toBe("1.4 km");
    expect(formatValue(33.2, "% of homes")).toBe("33%");
    expect(formatValue(null, "£")).toBe("–");
  });

  it("shows rates as percentages and 0–1 indices out of 10", () => {
    expect(formatValue(0.184, "rate")).toBe("18%");
    expect(formatValue(0.68, "index 0–1")).toBe("6.8/10");
    expect(formatValue(0.7, "quality 0–1")).toBe("7/10");
    expect(formatValue(0.75, "rating 0–1 (Good = 0.75)")).toBe("7.5/10");
  });
});

describe("a monthly rent", () => {
  it("prints as pounds, and the unit after it says a month, not a year", () => {
    expect(formatValue(1406.4, "£ a month")).toBe("£1,406");
    expect(unitSuffix("£ a month", "£ a month")).toBe("a month");
    expect(unitSuffix("gbp_month", "£ y mis")).toBe("y mis");
    expect(unitSuffix("£ a year", "£ a year")).toBe("a year");
  });
});

describe("formatValue for people per room", () => {
  it("keeps two decimals, from the English unit or its code", () => {
    expect(formatValue(0.4651, "people per room")).toBe("0.47");
    expect(formatValue(0.4651, "people_per_room")).toBe("0.47");
    expect(formatValue(1.1, "people per room")).toBe("1.10");
  });
});

describe("formatValue never prints a negative zero", () => {
  it("rounds tiny negatives to 0", () => {
    expect(formatValue(-6.5e-15, "hectares")).toBe("0.0");
    expect(formatValue(-0.04, "km")).toBe("0.0");
  });
});
