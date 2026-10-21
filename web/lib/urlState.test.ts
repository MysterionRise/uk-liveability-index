import { describe, expect, it } from "vitest";

import { DEFAULT_STATE, type LiveabilityState } from "./state";
import { decodeHash, encodeHash, hashLocale } from "./urlState";

const state: LiveabilityState = {
  ...DEFAULT_STATE,
  preset: "young_professional",
  theme_weights: { education: 1.25, climate: 1.25 },
  indicator_weights: { sunshine: 2, primary_school_quality: 2 },
  profile: { name: "Young professional · Sunshine", description: "I'm a young professional with a sun deficit", rationale: "Starts from Young professional; Climate counts more." },
  map: { ...DEFAULT_STATE.map, layer: "theme:climate", selected: "E01011350" },
  compare_within: "nation",
};

describe("the URL hash", () => {
  it("round-trips the preset, both weight maps, the profile's name, the layer and the area", () => {
    const back = decodeHash(`#${encodeHash(state)}`);
    expect(back.preset).toBe("young_professional");
    expect(back.theme_weights).toEqual(state.theme_weights);
    expect(back.indicator_weights).toEqual(state.indicator_weights);
    expect(back.profile).toEqual({ name: "Young professional · Sunshine", description: "", rationale: "" });
    expect(back.map?.layer).toBe("theme:climate");
    expect(back.map?.selected).toBe("E01011350");
    expect(back.compare_within).toBe("nation");
  });

  it("never carries the description or the rationale", () => {
    const text = decodeURIComponent(encodeHash(state));
    expect(text).not.toContain("sun deficit");
    expect(text).not.toContain("Starts from");
  });

  it("leaves out what is at its default", () => {
    const h = JSON.parse(decodeURIComponent(encodeHash(DEFAULT_STATE)));
    expect(h).toEqual({ p: "balanced" });
  });

  it("reads the old urban/rural flag and ignores garbage", () => {
    expect(decodeHash("#%7B%22c%22%3A1%7D").compare_within).toBe("urban_rural");
    expect(decodeHash("#not json")).toEqual({});
    expect(decodeHash("")).toEqual({});
    expect(decodeHash("#%22a%20string%22")).toEqual({});
  });

  it("checks every field's type and clamps weights, so a bad link cannot take the page down", () => {
    const bad = encodeURIComponent(
      JSON.stringify({ p: 5, l: 5, s: ["E01"], w: { safety: "3", climate: Infinity, health: -2, transport: 9, nested: { a: 1 } }, i: [1, 2], n: 42 }),
    );
    const back = decodeHash(`#${bad}`);
    expect(back.preset).toBeUndefined();
    expect(back.map?.layer).toBe("overall");
    expect(back.map?.selected).toBeNull();
    expect(back.theme_weights).toEqual({ health: 0, transport: 3 });
    expect(back.indicator_weights).toBeUndefined();
    expect(back.profile).toBeUndefined();
    expect(decodeHash(`#${encodeURIComponent("[1,2]")}`)).toEqual({});
  });

  it("carries the interface language when it is not English", () => {
    expect(hashLocale(`#${encodeHash(state)}`)).toBeNull();
    const welsh = `#${encodeHash({ ...state, locale: "cy" })}`;
    expect(hashLocale(welsh)).toBe("cy");
    expect(hashLocale(`#${encodeURIComponent(JSON.stringify({ g: "../x" }))}`)).toBeNull();
    expect(hashLocale("#not json")).toBeNull();
  });
});
