"use client";

import { useLocale, useTranslations } from "next-intl";
import { useEffect, useState } from "react";

import { useLiveability } from "@/components/AppData";
import { ComparisonTable } from "@/components/genui/ComparisonTable";
import { compareAreas } from "@/lib/api";
import type { Comparison } from "@/lib/contracts.gen";

const MAX_COMPARE = 5;

const KEY = "lix.shortlist";

/** Saved areas; kept in this browser (localStorage) and shared with the assistant. */
export function ShortlistPanel() {
  const { state, update } = useLiveability();
  const t = useTranslations("Shortlist");
  const locale = useLocale();
  const [comparison, setComparison] = useState<{
    key: string;
    result?: Comparison;
    error?: string;
  } | null>(null);
  const codes = state.shortlist.slice(0, MAX_COMPARE).map((i) => i.code);
  const key = `${codes.join(",")}|${state.preset}|${JSON.stringify(state.theme_weights)}|${JSON.stringify(state.indicator_weights)}`;

  // Restore once, then persist on change
  useEffect(() => {
    try {
      const saved = JSON.parse(localStorage.getItem(KEY) ?? "[]");
      if (Array.isArray(saved) && saved.length)
        update((s) => (s.shortlist.length ? s : { ...s, shortlist: saved }));
    } catch {
      /* storage may be unavailable */
    }
  }, [update]);
  useEffect(() => {
    try {
      localStorage.setItem(KEY, JSON.stringify(state.shortlist));
    } catch {
      /* ignore */
    }
  }, [state.shortlist]);

  if (state.shortlist.length === 0) {
    return (
      <p className="p-4 text-sm text-[var(--text-secondary)]">{t("empty")}</p>
    );
  }
  const current = comparison?.key === key ? comparison : null;
  return (
    <div className="p-2">
      <ul
        className="divide-y divide-[var(--border)] text-sm"
        data-testid="shortlist"
      >
        {state.shortlist.map((item) => (
          <li
            key={item.code}
            className="flex items-center justify-between gap-2 py-2"
          >
            <button
              className="min-w-0 text-left"
              onClick={() =>
                update((s) => ({
                  ...s,
                  map: {
                    ...s.map,
                    selected: /^[EWSN]01\d{6}$/.test(item.code) ? item.code : s.map.selected,
                    bbox: item.centre
                      ? [
                          item.centre.lon - 0.02,
                          item.centre.lat - 0.012,
                          item.centre.lon + 0.02,
                          item.centre.lat + 0.012,
                        ]
                      : s.map.bbox,
                  },
                }))
              }
            >
              <span className="block truncate font-medium">{item.name}</span>
              {item.note && (
                <span className="block truncate text-xs text-[var(--text-secondary)]">
                  {item.note}
                </span>
              )}
            </button>
            <button
              className="text-xs text-[var(--text-muted)] underline"
              onClick={() =>
                update((s) => ({
                  ...s,
                  shortlist: s.shortlist.filter((i) => i.code !== item.code),
                }))
              }
            >
              {t("remove")}
            </button>
          </li>
        ))}
      </ul>
      {codes.length >= 2 && (
        <div className="mt-2">
          <button
            className="btn"
            data-testid="compare-shortlist"
            onClick={() =>
              compareAreas(codes, state.preset, locale, state.theme_weights, state.indicator_weights)
                .then((result) => setComparison({ key, result }))
                .catch((e) =>
                  setComparison({ key, error: String(e.message ?? e) }),
                )
            }
          >
            {codes.length === state.shortlist.length ? t("compareThese") : t("compareFirst", { count: MAX_COMPARE })}
          </button>
          {current?.error && (
            <p className="mt-2 text-xs text-[var(--critical)]">
              {t("compareError", { error: current.error })}
            </p>
          )}
          {current?.result && (
            <div className="mt-2">
              <ComparisonTable comparison={current.result} />
            </div>
          )}
        </div>
      )}
    </div>
  );
}
