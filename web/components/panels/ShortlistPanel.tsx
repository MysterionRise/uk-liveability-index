"use client";

import { useLocale, useTranslations } from "next-intl";
import { useState } from "react";

import { useData, useLiveability } from "@/components/AppData";
import { ComparisonTable } from "@/components/genui/ComparisonTable";
import { compareAreas } from "@/lib/api";
import type { Comparison } from "@/lib/contracts.gen";

const MAX_COMPARE = 5;

/** Saved areas; kept in this browser (localStorage, through the state provider) and
 * shared with the assistant. */
export function ShortlistPanel() {
  const { state, update } = useLiveability();
  const { scores } = useData();
  const t = useTranslations("Shortlist");
  const locale = useLocale();
  const [comparison, setComparison] = useState<{
    key: string;
    result?: Comparison;
    error?: string;
  } | null>(null);
  const codes = state.shortlist.slice(0, MAX_COMPARE).map((i) => i.code);
  const key = `${codes.join(",")}|${state.preset}|${JSON.stringify(state.theme_weights)}|${JSON.stringify(state.indicator_weights)}`;

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
                    // A small area of the pack (any nation) is selected; a wider place is only shown
                    selected: scores?.index.has(item.code) ? item.code : s.map.selected,
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
