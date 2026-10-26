import { describe, expect, it } from "vitest";

import cy from "../messages/cy.json";
import en from "../messages/en.json";
import ga from "../messages/ga.json";
import gd from "../messages/gd.json";

// Placeholders sit in narrow boxes (the header search is about 170 px, the chat input
// about 240 px in the side panel); a longer translation would wrap or be cut off
const LIMITS: [string, (m: typeof en) => string, number][] = [
  ["SidePanel.chatPlaceholder", (m) => m.SidePanel.chatPlaceholder, 30],
  ["Search.placeholder", (m) => m.Search.placeholder, 20],
];

describe("placeholders fit their inputs", () => {
  for (const [name, pick, limit] of LIMITS) {
    it(`${name} stays under ${limit} characters in every language`, () => {
      for (const [locale, messages] of [
        ["en", en],
        ["cy", cy],
        ["gd", gd],
        ["ga", ga],
      ] as const) {
        const text = pick(messages as typeof en);
        expect(text.length, `${locale}: "${text}"`).toBeLessThanOrEqual(limit);
      }
    });
  }
});

// The categories the pack serves (pipeline serve/lookups.py) and the gazetteer's
// settlement types: every one has a word in every language, or the id would show (#187)
const POI_CATEGORIES = [
  "arts_centre", "bakery", "bar", "butcher", "cafe", "cinema", "community_centre", "convenience_store",
  "dentist", "ev_charging", "fast_food", "gp", "greengrocer", "gym", "library", "market", "museum",
  "nursery", "other_school", "park", "pharmacy", "playground", "post_office", "primary_school", "pub",
  "restaurant", "secondary_school", "sports_centre", "supermarket", "theatre", "well_run_pub",
];
const SETTLEMENT_TYPES = ["city", "town", "village", "hamlet", "suburb", "other"];

describe("the catalogues word what the data serves", () => {
  for (const [locale, messages] of [
    ["en", en],
    ["cy", cy],
    ["gd", gd],
    ["ga", ga],
  ] as const) {
    it(`${locale} titles every POI category, settlement type and month`, () => {
      const m = messages as typeof en;
      for (const category of POI_CATEGORIES) {
        expect(m.Poi.titles[category as keyof typeof m.Poi.titles], `${locale}: Poi.titles.${category}`).toBeTruthy();
      }
      for (const type of SETTLEMENT_TYPES) {
        expect(m.Search.types[type as keyof typeof m.Search.types], `${locale}: Search.types.${type}`).toBeTruthy();
      }
      expect(m.Dates.months).toHaveLength(12);
      expect(new Set(m.Dates.months).size).toBe(12);
    });
  }
});
