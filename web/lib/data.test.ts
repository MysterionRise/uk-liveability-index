import { describe, expect, it } from "vitest";

import { type Manifest, SCHEMA_VERSION, assertSchema, coverageNations } from "./data";

describe("data pack schema check", () => {
  it("accepts the version this app reads", () => {
    expect(() =>
      assertSchema({ schema_version: SCHEMA_VERSION }),
    ).not.toThrow();
  });

  it("names the versions and the fix for an old or newer pack", () => {
    expect(() => assertSchema({})).toThrow(
      `Data pack schema v1 but this app needs v${SCHEMA_VERSION}: run make data-download`,
    );
    expect(() => assertSchema({ schema_version: SCHEMA_VERSION + 1 })).toThrow(
      `schema v${SCHEMA_VERSION + 1}`,
    );
  });
});

describe("coverageNations", () => {
  const manifest = {
    geography: { active: ["E", "W", "S", "N"] },
    indicators: [
      { id: "domestic_rates", coverage: ["N"] },
      { id: "council_tax", coverage: ["E", "W", "S"] },
      { id: "no2", coverage: ["E", "W", "S", "N"] },
    ],
  } as unknown as Manifest;

  it("names the nations an indicator is built for when they are fewer than the build's", () => {
    expect(coverageNations(manifest, "indicator:domestic_rates")).toEqual(["N"]);
    expect(coverageNations(manifest, "indicator:council_tax")).toEqual(["E", "W", "S"]);
  });

  it("is null for the overall, a theme, an indicator every nation has or an unknown one", () => {
    expect(coverageNations(manifest, "overall")).toBeNull();
    expect(coverageNations(manifest, "theme:safety")).toBeNull();
    expect(coverageNations(manifest, "indicator:no2")).toBeNull();
    expect(coverageNations(manifest, "indicator:nothing")).toBeNull();
    expect(coverageNations(null, "indicator:domestic_rates")).toBeNull();
  });

  it("keeps only the active nations, in the build's order", () => {
    const demo = { ...manifest, geography: { active: ["N", "E"] } } as unknown as Manifest;
    expect(coverageNations(demo, "indicator:council_tax")).toEqual(["E"]);
  });
});
