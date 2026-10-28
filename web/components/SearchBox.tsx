"use client";

import { useLocale, useTranslations } from "next-intl";
import { useEffect, useRef, useState } from "react";

import { useLiveability } from "@/components/AppData";
import { usePanel } from "@/components/PanelContext";
import { searchPlaces } from "@/lib/api";
import type { Place } from "@/lib/contracts.gen";
import { useCoverageIn } from "@/lib/copy";
import { nationOf, useLevelWords } from "@/lib/levels";

// Kinds with a word of their own; an area tier takes its nation's word (lib/levels)
const KINDS = ["postcode", "postcode_district", "postcode_sector", "place", "region", "nation"] as const;
const TIERS = ["lsoa", "msoa", "lad"];
// The gazetteer's settlement types → the words the catalogue has for them
const TYPE_KEYS: Record<string, "city" | "town" | "village" | "hamlet" | "suburb" | "other"> = {
  City: "city",
  Town: "town",
  Village: "village",
  Hamlet: "hamlet",
  "Suburban Area": "suburb",
  "Other Settlement": "other",
};

function capitalise(word: string): string {
  return word.charAt(0).toUpperCase() + word.slice(1);
}

/** Postcode or place search: opens an area's profile without going through the chat. */
export function SearchBox() {
  const t = useTranslations("Search");
  const locale = useLocale();
  const coverageIn = useCoverageIn();
  const level = useLevelWords();
  const { update } = useLiveability();
  const { setTab } = usePanel();
  const [query, setQuery] = useState("");
  // Results remember the query they answer, so Enter never picks a stale one
  const [found, setFound] = useState<{ query: string; places: Place[] }>({
    query: "",
    places: [],
  });
  const results = found.query === query.trim() ? found.places : [];
  const [active, setActive] = useState(0);
  const [open, setOpen] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const latest = useRef("");

  useEffect(() => {
    const q = query.trim();
    if (q.length < 2) return;
    latest.current = q;
    const timer = setTimeout(() => {
      searchPlaces(q, locale)
        .then((places) => {
          if (latest.current !== q) return; // a newer query is on its way
          setFound({ query: q, places });
          setActive(0);
          setOpen(true);
          setError(null);
        })
        .catch(() => latest.current === q && setError(t("unavailable")));
    }, 200);
    return () => clearTimeout(timer);
  }, [query, t, locale]);

  // "City, Leeds": the settlement type in the interface language, then the council
  function detailOf(place: Place): string {
    const type = place.place_type ? t(`types.${TYPE_KEYS[place.place_type] ?? "other"}`) : null;
    const detail = [type, place.detail].filter(Boolean).join(", ");
    return detail ? ` · ${detail}` : "";
  }

  function choose(place: Place) {
    const box = place.bbox
      ? ([...place.bbox] as [number, number, number, number])
      : place.centre
        ? ([
            place.centre.lon - 0.02,
            place.centre.lat - 0.012,
            place.centre.lon + 0.02,
            place.centre.lat + 0.012,
          ] as [number, number, number, number])
        : null;
    update((s) => ({
      ...s,
      map: {
        ...s.map,
        bbox: box ?? s.map.bbox,
        selected: place.lsoa21cd ?? s.map.selected,
        highlighted: place.lsoa21cd
          ? []
          : place.code
            ? [place.code]
            : s.map.highlighted,
      },
    }));
    if (place.lsoa21cd) setTab("area");
    setQuery(place.name);
    setOpen(false);
  }

  return (
    <div className="relative w-full max-w-xs" data-testid="search">
      <input
        className="input w-full text-sm"
        type="search"
        placeholder={t("placeholder")}
        aria-label={t("placeholder")}
        role="combobox"
        aria-expanded={open}
        // Only a list that exists: none while the box is closed (#216)
        aria-controls={open ? "search-results" : undefined}
        aria-activedescendant={
          open && results[active] ? `search-${active}` : undefined
        }
        value={query}
        onChange={(e) => {
          setQuery(e.target.value);
          if (e.target.value.trim().length < 2) setOpen(false);
        }}
        onFocus={() => results.length > 0 && setOpen(true)}
        onBlur={() => setTimeout(() => setOpen(false), 150)}
        onKeyDown={(e) => {
          if (!open || !results.length) return;
          if (e.key === "ArrowDown")
            setActive((a) => Math.min(a + 1, results.length - 1));
          else if (e.key === "ArrowUp") setActive((a) => Math.max(a - 1, 0));
          else if (e.key === "Enter") choose(results[active]);
          else if (e.key === "Escape") setOpen(false);
          else return;
          e.preventDefault();
        }}
      />
      {open && (
        <ul
          id="search-results"
          role="listbox"
          className="absolute left-0 top-full z-30 mt-1 w-[min(26rem,calc(100vw-2rem))] sm:left-auto sm:right-0 overflow-hidden rounded-md border border-[var(--border)] bg-[var(--surface-1)] text-sm shadow-lg"
        >
          {results.length === 0 && (
            <li className="px-3 py-2 text-[var(--text-muted)]">
              {found.query === query.trim()
                ? t("noMatches", { coverageIn })
                : t("searching")}
            </li>
          )}
          {results.map((p, i) => (
            <li
              key={`${p.kind}-${p.code ?? p.name}-${i}`}
              id={`search-${i}`}
              role="option"
              aria-selected={i === active}
              className={`flex cursor-pointer items-baseline gap-1.5 px-3 py-1.5 ${i === active ? "bg-[var(--hover)]" : ""}`}
              onMouseDown={(e) => {
                e.preventDefault();
                choose(p);
              }}
              onMouseEnter={() => setActive(i)}
            >
              <span className="shrink-0 font-medium">{p.name}</span>
              <span className="truncate text-xs text-[var(--text-muted)]">
                {TIERS.includes(p.kind)
                  ? capitalise(level(nationOf(p.code ?? p.lsoa21cd), p.kind))
                  : (KINDS as readonly string[]).includes(p.kind)
                    ? t(`kinds.${p.kind as (typeof KINDS)[number]}`)
                    : p.kind}
                {p.name_en ? ` · ${p.name_en}` : ""}
                {detailOf(p)}
              </span>
            </li>
          ))}
        </ul>
      )}
      {error && (
        <p className="absolute top-full mt-1 text-xs text-[var(--critical)]">
          {error}
        </p>
      )}
    </div>
  );
}
