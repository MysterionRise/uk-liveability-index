import { describe, expect, it } from "vitest";

import { DEFAULT_STATE, type LiveabilityState } from "./state";
import { decodeHash, encodeHash } from "./urlState";

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
});
