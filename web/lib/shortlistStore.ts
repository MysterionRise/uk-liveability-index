/**
 * The shortlist this browser keeps (localStorage), read once when the page's state is
 * made and written whenever it changes, so an area saved before the Shortlist tab is
 * opened never overwrites the list saved last time.
 */
import type { ShortlistItem } from "./contracts.gen";

export const SHORTLIST_KEY = "lix.shortlist";

export function readShortlist(): ShortlistItem[] {
  try {
    const saved = JSON.parse(localStorage.getItem(SHORTLIST_KEY) ?? "[]");
    return Array.isArray(saved)
      ? saved.filter((i): i is ShortlistItem => !!i && typeof i === "object" && typeof i.code === "string")
      : [];
  } catch {
    return [];
  }
}

export function saveShortlist(items: ShortlistItem[]): void {
  try {
    localStorage.setItem(SHORTLIST_KEY, JSON.stringify(items));
  } catch {
    /* storage may be unavailable */
  }
}
