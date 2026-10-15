"use client";

import { useTranslations } from "next-intl";
import { useState } from "react";

import { useData, useLiveability, useScores } from "@/components/AppData";
import { type SqlData, SqlResult } from "@/components/genui/SqlResult";
import { CorrelationHeatmap } from "@/components/panels/CorrelationHeatmap";
import { IndicatorHistogram } from "@/components/panels/IndicatorHistogram";
import { runSql } from "@/lib/api";

const EXAMPLE = `SELECT lad_nm, round(median(overall), 1) AS median_overall, count(*) AS lsoas
FROM lsoa
GROUP BY lad_nm
ORDER BY median_overall DESC
LIMIT 20`;

/** Analyst mode: the distribution of the current map layer and a read-only SQL console. */
export function AnalystPanel() {
  const { scores } = useData();
  const { state } = useLiveability();
  const t = useTranslations("Analyst");
  const values = useScores(state);
  const [query, setQuery] = useState(EXAMPLE);
  const [result, setResult] = useState<SqlData | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const selectedIndex = state.map.selected && scores ? scores.index.get(state.map.selected) : undefined;

  return (
    <div className="space-y-3 p-3 text-sm" data-testid="analyst">
      {values && (
        <IndicatorHistogram
          values={values.score}
          label={values.label}
          theme={values.theme}
          marker={selectedIndex !== undefined ? values.score[selectedIndex] : null}
        />
      )}
      <CorrelationHeatmap />
      <div>
        <label className="mb-1 block text-xs font-medium text-[var(--text-secondary)]" htmlFor="sql">
          {t("sqlLabel")}
        </label>
        <textarea
          id="sql"
          className="input h-36 w-full font-mono text-xs"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          spellCheck={false}
        />
        <button
          className="btn mt-1"
          disabled={busy}
          onClick={async () => {
            setBusy(true);
            setError(null);
            try {
              setResult(await runSql(query));
            } catch (e) {
              setError(String((e as Error).message ?? e));
            } finally {
              setBusy(false);
            }
          }}
        >
          {busy ? t("running") : t("run")}
        </button>
        {error && <p className="mt-1 text-xs text-[var(--critical)]" role="alert">⚠ {error}</p>}
      </div>
      {result && <SqlResult data={result} />}
    </div>
  );
}
