import { describe, expect, it } from "vitest";

import { presetLabel } from "./presetLabel";

const name = (id: string) => ({ balanced: "Balanced", family: "Family with children" })[id] ?? id;

describe("presetLabel", () => {
  it("names a preset, with the adjusted suffix translated", () => {
    expect(presetLabel(name, "family", "(wedi'i addasu)")).toBe("Family with children");
    expect(presetLabel(name, "family (adjusted)", "(wedi'i addasu)")).toBe("Family with children (wedi'i addasu)");
  });

  it("names the active profile when the result was scored on its base preset", () => {
    const profile = { name: "Sunny parent", preset: "family" };
    expect(presetLabel(name, "family (adjusted)", "(adjusted)", profile)).toBe("Sunny parent");
    expect(presetLabel(name, "family", "(adjusted)", profile)).toBe("Sunny parent");
    expect(presetLabel(name, "balanced (adjusted)", "(adjusted)", profile)).toBe("Balanced (adjusted)");
    expect(presetLabel(name, "balanced", "(adjusted)", null)).toBe("Balanced");
  });
});
