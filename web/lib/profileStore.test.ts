import { describe, expect, it } from "vitest";

import type { WeightProfile } from "./contracts.gen";
import { isTweaked, profileState, savedFromProfile, savedFromState } from "./profileStore";
import { DEFAULT_STATE } from "./state";

const generated: WeightProfile = {
  preset: "young_professional",
  theme_weights: { education: 1.25 },
  indicator_weights: { sunshine: 2 },
  name: "Young professional · Sunshine",
  rationale: "Starts from Young professional; Education counts more.",
  locale: "en",
  engine: "rules",
  engine_reason: "free_model",
};

describe("the saved profile", () => {
  it("is built from a generated profile and seeds the state", () => {
    const saved = savedFromProfile(generated, "young professional, sun deficit");
    expect(saved.description).toBe("young professional, sun deficit");
    expect(saved.engine).toBe("rules");
    const seed = profileState(saved);
    expect(seed.preset).toBe("young_professional");
    expect(seed.theme_weights).toEqual({ education: 1.25 });
    expect(seed.indicator_weights).toEqual({ sunshine: 2 });
    expect(seed.profile).toEqual({ name: generated.name, description: "young professional, sun deficit", rationale: generated.rationale });
  });

  it("records the sliders as tweaks and seeds them back, and Reset-style data stays", () => {
    const saved = savedFromProfile(generated, "x");
    const state = { ...DEFAULT_STATE, ...profileState(saved), theme_weights: { education: 1.25, safety: 3 } };
    expect(isTweaked(saved, state)).toBe(true);
    const next = savedFromState(state, saved);
    expect(next?.tweaks?.theme_weights).toEqual({ education: 1.25, safety: 3 });
    expect(next?.theme_weights).toEqual({ education: 1.25 });
    expect(profileState(next!).theme_weights).toEqual({ education: 1.25, safety: 3 });
    const back = savedFromState({ ...state, theme_weights: { education: 1.25 } }, next);
    expect(back?.tweaks).toBeUndefined();
  });

  it("keeps a profile the assistant built or a link named as generated", () => {
    const state = {
      ...DEFAULT_STATE,
      preset: "retiree",
      theme_weights: { health: 2.5 },
      profile: { name: "Retired · Asthma", description: "I'm retired and have asthma", rationale: "Starts from Retired." },
    };
    const saved = savedFromState(state, savedFromProfile(generated, "x"));
    expect(saved?.name).toBe("Retired · Asthma");
    expect(saved?.preset).toBe("retiree");
    expect(saved?.theme_weights).toEqual({ health: 2.5 });
    expect(saved?.tweaks).toBeUndefined();
    // A link with only the name keeps the saved description of the same profile
    const linked = savedFromState({ ...state, profile: { name: "Retired · Asthma", description: "", rationale: "" } }, saved);
    expect(linked?.description).toBe("I'm retired and have asthma");
  });

  it("leaves the saved copy alone when no profile is active", () => {
    const saved = savedFromProfile(generated, "x");
    expect(savedFromState(DEFAULT_STATE, saved)).toBe(saved);
  });
});
