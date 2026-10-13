import { describe, expect, it } from "vitest";

import cy from "../messages/cy.json";
import en from "../messages/en.json";
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
      ] as const) {
        const text = pick(messages as typeof en);
        expect(text.length, `${locale}: "${text}"`).toBeLessThanOrEqual(limit);
      }
    });
  }
});
