import { describe, expect, it } from "vitest";

import { type Manifest, SCHEMA_VERSION, assertSchema, coverageName, coverageNations, coverageShape } from "./data";

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

describe("coverageName groups the build's nations by country", () => {
  const nations = {
    E: { name: "England", country: "UK" },
    W: { name: "Wales", country: "UK" },
    S: { name: "Scotland", country: "UK" },
    N: { name: "Northern Ireland", country: "UK" },
    I: { name: "Ireland", country: "IE" },
  };
  const countries = {
    UK: { short: "the UK", nations: ["E", "W", "S", "N"] },
    IE: { short: "Ireland", nations: ["I"] },
  };
  const build = (active: string[]) =>
    ({ geography: { active, nations, countries } }) as unknown as Manifest;
  const t = (code: string) => ({ uk: "the UK", IE: "Ireland" })[code] ?? nations[code as keyof typeof nations].name;

  it("names a whole country, a cut of it, or both countries", () => {
    expect(coverageName(build(["E", "W", "S", "N"]), t)).toBe("the UK");
    expect(coverageName(build(["E", "W", "S", "N", "I"]), t)).toBe("the UK and Ireland");
    expect(coverageName(build(["I"]), t)).toBe("Ireland");
    expect(coverageName(build(["E", "W"]), t)).toBe("England and Wales");
    expect(coverageName(build(["S", "I"]), t)).toBe("Scotland and Ireland");
    expect(coverageName(build(["E", "W", "S", "N"]), t, undefined, ["N"])).toBe("Northern Ireland");
  });

  it("falls back to the UK without a manifest", () => {
    expect(coverageName(null)).toBe("the UK");
  });

  it("recognises the shapes the phrase catalogues have words for", () => {
    expect(coverageShape(build(["E", "W", "S", "N", "I"]))).toBe("uk_I");
    expect(coverageShape(build(["E", "W", "S", "N"]))).toBe("uk");
    expect(coverageShape(build(["I"]))).toBe("I");
    expect(coverageShape(build(["E", "W"]))).toBe("E_W");
    expect(coverageShape(build(["E", "S"]))).toBeNull();
  });
});
