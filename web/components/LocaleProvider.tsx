"use client";

/**
 * The interface language, kept like the theme: a stored choice, else the browser's
 * language, applied to <html lang> and handed to next-intl. `app/layout.tsx` runs the
 * same detection inline before the first paint.
 */
import { NextIntlClientProvider } from "next-intl";
import { createContext, useCallback, useContext, useEffect, useSyncExternalStore, type ReactNode } from "react";

import {
  AUTONYMS,
  BCP47,
  DRAFT_WORD,
  DEFAULT_LOCALE,
  LOCALE_KEY,
  MESSAGES,
  availableLocales,
  catalogueMeta,
  detectLocale,
  type Locale,
} from "@/lib/i18n";
import { hashLocale } from "@/lib/urlState";

const listeners = new Set<() => void>();
const notify = () => listeners.forEach((l) => l());

function readLocale(): Locale {
  try {
    // A shared link opens in the language it was made in (for this page only: the
    // browser's own choice is not overwritten)
    const fromLink = hashLocale(window.location.hash);
    return detectLocale(fromLink ?? localStorage.getItem(LOCALE_KEY), navigator.languages ?? [navigator.language]);
  } catch {
    return DEFAULT_LOCALE;
  }
}

export function applyLocale(locale: Locale): void {
  document.documentElement.lang = BCP47[locale];
}

interface LocaleContext {
  locale: Locale;
  setLocale: (locale: Locale) => void;
}

const Context = createContext<LocaleContext>({ locale: DEFAULT_LOCALE, setLocale: () => {} });

export function LocaleProvider({ children }: { children: ReactNode }) {
  const subscribe = useCallback((cb: () => void) => {
    listeners.add(cb);
    return () => {
      listeners.delete(cb);
    };
  }, []);
  // The server renders English; the stored or browser language follows at once
  const locale = useSyncExternalStore(subscribe, readLocale, () => DEFAULT_LOCALE);

  useEffect(() => {
    applyLocale(locale);
  }, [locale]);

  const setLocale = useCallback((next: Locale) => {
    try {
      if (next === DEFAULT_LOCALE) localStorage.removeItem(LOCALE_KEY);
      else localStorage.setItem(LOCALE_KEY, next);
    } catch {
      /* storage blocked: the choice lasts for this page only */
    }
    applyLocale(next);
    notify();
  }, []);

  // English fills any key a draft catalogue lacks
  const messages = { ...MESSAGES.en, ...(MESSAGES[locale] ?? {}) };
  return (
    <Context.Provider value={{ locale, setLocale }}>
      <NextIntlClientProvider
        locale={BCP47[locale]}
        messages={messages}
        timeZone="Europe/London"
        onError={(e) => {
          if (process.env.NODE_ENV !== "production") console.warn(e.message);
        }}
        getMessageFallback={({ key }) => key}
      >
        {children}
      </NextIntlClientProvider>
    </Context.Provider>
  );
}

export function useLocaleChoice(): LocaleContext {
  return useContext(Context);
}

/** English · Cymraeg · … as a compact select; a draft catalogue says so. */
export function LocaleToggle({ label }: { label: string }) {
  const { locale, setLocale } = useLocaleChoice();
  return (
    <select
      className="input py-0.5 text-xs"
      aria-label={label}
      value={locale}
      data-testid="locale-toggle"
      onChange={(e) => setLocale(e.target.value as Locale)}
    >
      {availableLocales().map((l) => (
        <option key={l} value={l}>
          {AUTONYMS[l]}
          {catalogueMeta(l).status === "draft" ? ` (${DRAFT_WORD[l]})` : ""}
        </option>
      ))}
    </select>
  );
}
