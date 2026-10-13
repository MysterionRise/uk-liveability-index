import { describe, expect, it } from "vitest";

import { SCHEMA_VERSION, assertSchema } from "./data";

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
