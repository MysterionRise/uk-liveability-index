/**
 * Interface languages: English, Cymraeg, Gàidhlig and Gaeilge, one catalogue each in
 * messages/<locale>.json (ICU messages; `_meta` says whether a fluent reviewer has
 * signed it off). The choice is kept in localStorage, else taken from the browser's
 * languages; `app/layout.tsx` sets <html lang> before the first paint with the same
 * logic. Place names, numbers and dates follow the locale; the data labels come from
 * the manifest's catalogue (lix_core: config/i18n/*.yaml) with English as fallback.
 */
import cy from "@/messages/cy.json";
import en from "@/messages/en.json";
import gd from "@/messages/gd.json";

export const LOCALES = ["en", "cy", "gd", "ga"] as const;
export type Locale = (typeof LOCALES)[number];
export const DEFAULT_LOCALE: Locale = "en";
export const LOCALE_KEY = "lix.locale";

/** The language's own name, as the switcher shows it */
export const AUTONYMS: Record<Locale, string> = {
  en: "English",
  cy: "Cymraeg",
  gd: "Gàidhlig",
  ga: "Gaeilge",
};
/** "draft" in each language, for the switcher: the option is in that language already. */
export const DRAFT_WORD: Record<Locale, string> = {
  en: "draft",
  cy: "drafft",
  gd: "dreachd",
  ga: "dréacht",
};
/** BCP 47 tags for <html lang> and Intl formatting */
export const BCP47: Record<Locale, string> = {
  en: "en-GB",
  cy: "cy-GB",
  gd: "gd-GB",
  ga: "ga-IE",
};

export interface CatalogueMeta {
  status: "draft" | "reviewed";
  reviewer?: string | null;
  date?: string | null;
}

type Messages = Record<string, unknown> & { _meta?: CatalogueMeta };

/** Catalogues shipped in this build; a language without one falls back to English. */
export const MESSAGES: Partial<Record<Locale, Messages>> = {
  en: en as Messages,
  cy: cy as Messages,
  gd: gd as Messages,
};

export function isLocale(value: unknown): value is Locale {
  return (
    typeof value === "string" && (LOCALES as readonly string[]).includes(value)
  );
}

/** Locales with a catalogue, in LOCALES order */
export function availableLocales(): Locale[] {
  return LOCALES.filter((l) => MESSAGES[l]);
}

export function catalogueMeta(locale: Locale): CatalogueMeta {
  return MESSAGES[locale]?._meta ?? { status: "reviewed" };
}

/** The locale for a browser: the stored choice, else the first supported browser language. */
export function detectLocale(
  stored: string | null,
  languages: readonly string[],
): Locale {
  if (isLocale(stored) && MESSAGES[stored]) return stored;
  for (const tag of languages) {
    const base = tag.toLowerCase().split("-")[0];
    if (isLocale(base) && MESSAGES[base]) return base;
  }
  return DEFAULT_LOCALE;
}
