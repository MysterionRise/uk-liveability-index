"use client";

/**
 * App-wide data and state:
 * - manifest + scores loaded once from the static data server
 * - the shared assistant state (AG-UI), read and written through useLiveability()
 * - scores recomputed in the browser for the current weights (useScores)
 */
import { UseAgentUpdate, useAgent } from "@copilotkit/react-core/v2";
import { useLocale, useTranslations } from "next-intl";
import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState } from "react";

import { type Manifest, type ScoreData, loadManifest, loadScores, scoredIndicators, themeGaps } from "@/lib/data";
import {
  MIN_THEME_COVERAGE,
  type ScoreResult,
  aggregate,
  percentileRank,
  percentileWithin,
  scoreLsoas,
} from "@/lib/scoring";
import type { LiveabilityState as WireState } from "@/lib/contracts.gen";
import { DEFAULT_STATE, type LiveabilityState, effectiveWeights, normaliseState } from "@/lib/state";
import { labelsFor } from "@/lib/labels";
import { readHash, writeHash } from "@/lib/urlState";

interface DataContext {
  manifest: Manifest | null;
  scores: ScoreData | null;
  error: string | null;
}

const Ctx = createContext<DataContext>({ manifest: null, scores: null, error: null });

export function DataProvider({ children }: { children: React.ReactNode }) {
  const [value, setValue] = useState<DataContext>({ manifest: null, scores: null, error: null });
  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const manifest = await loadManifest();
        const scores = await loadScores(manifest);
        if (!cancelled) setValue({ manifest, scores, error: null });
      } catch (e) {
        if (!cancelled) setValue((v) => ({ ...v, error: String(e) }));
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export const useData = () => useContext(Ctx);

interface StateContext {
  state: LiveabilityState;
  update: (fn: (s: LiveabilityState) => LiveabilityState) => void;
}

const StateCtx = createContext<StateContext>({ state: DEFAULT_STATE, update: () => {} });

/**
 * The page owns the view state (preset, weights, map, shortlist) and mirrors it into the
 * assistant's shared AG-UI state:
 * - user changes update the page state and are pushed to the agent;
 * - the agent's snapshots (the assistant set weights or highlighted areas) update the page;
 * - a new agent instance (CopilotKit swaps in the runtime's agent once it connects, a few
 *   seconds after load) is given the current state, so early changes aren't lost;
 * - the URL hash mirrors the shareable part, and a shared link seeds the first state.
 */
export function LiveabilityStateProvider({ children }: { children: React.ReactNode }) {
  const { agent } = useAgent({ updates: [UseAgentUpdate.OnStateChanged] });
  const [state, setState] = useState<LiveabilityState>(() =>
    normaliseState({ ...DEFAULT_STATE, ...readHash() } as Partial<WireState>),
  );
  const latest = useRef(state);
  const pushedTo = useRef<unknown>(null);

  // Snapshots from the agent replace the page state (adjusted during render, not in an effect)
  const [seenAgentState, setSeenAgentState] = useState<unknown>(agent.state);
  if (agent.state !== seenAgentState) {
    setSeenAgentState(agent.state);
    const incoming = agent.state as Partial<WireState> | undefined;
    if (incoming && Object.keys(incoming).length) setState(normaliseState(incoming));
  }

  useEffect(() => {
    latest.current = state;
    writeHash(state);
  }, [state]);

  // A new agent instance starts empty: hand it the current view
  useEffect(() => {
    if (pushedTo.current === agent) return;
    pushedTo.current = agent;
    agent.setState(latest.current);
  }, [agent]);

  const update = useCallback(
    (fn: (s: LiveabilityState) => LiveabilityState) => {
      const next = fn(latest.current);
      latest.current = next;
      setState(next);
      agent.setState(next);
    },
    [agent],
  );

  const value = useMemo(() => ({ state, update }), [state, update]);
  return <StateCtx.Provider value={value}>{children}</StateCtx.Provider>;
}

/** The view state shared with the assistant, plus an updater that syncs it. */
export const useLiveability = () => useContext(StateCtx);

export interface LayerValues {
  /** Percentile (0–100) per LSOA for the active layer (NaN = no value) */
  lsoa: Float64Array;
  /** The layer's 0–100 score per LSOA before ranking (what the histogram shows) */
  score: Float64Array;
  msoa: Map<string, number>;
  lad: Map<string, number>;
  result: ScoreResult;
  label: string;
  /** Theme of the active layer (a theme, or the indicator's theme); none for the overall score */
  theme?: string;
}

/** Scores for the current weights and the values the map colours by. */
export function useScores(state: LiveabilityState): LayerValues | null {
  const t = useTranslations("Map");
  const locale = useLocale();
  const { manifest, scores } = useData();
  // Recompute only when the weighting changes, not on every map move
  const themeKey = JSON.stringify(state.theme_weights);
  const indicatorKey = JSON.stringify(state.indicator_weights);
  const preset = state.preset;
  const weights = useMemo(() => {
    if (!manifest) return null;
    return effectiveWeights(manifest, {
      ...DEFAULT_STATE,
      preset,
      theme_weights: JSON.parse(themeKey),
      indicator_weights: JSON.parse(indicatorKey),
    });
  }, [manifest, preset, themeKey, indicatorKey]);
  const result = useMemo(() => {
    if (!manifest || !scores || !weights) return null;
    performance.mark("lix-score-start");
    // Each area's nation says which indicators apply to it and which themes it cannot
    // have, so a Scottish area is scored from what Scotland publishes (lix_core.scoring)
    const r = scoreLsoas(
      scores.indicators,
      scoredIndicators(manifest),
      weights.themes,
      weights.indicators,
      MIN_THEME_COVERAGE,
      scores.nation,
      themeGaps(manifest),
    );
    performance.measure("lix-score", "lix-score-start");
    return r;
  }, [manifest, scores, weights]);

  return useMemo(() => {
    if (!manifest || !scores || !result) return null;
    const layer = state.map.layer || "overall";
    let lsoa: Float64Array;
    let score: Float64Array;
    let label: string;
    let theme: string | undefined;
    // Colour by percentile: scores bunch in the middle of 0–100, so percentiles use the
    // whole colour ramp and read as "better than X% of the UK"
    if (layer.startsWith("theme:") && result.themes[layer.slice(6)]) {
      const t = layer.slice(6);
      lsoa = result.themes[t].percentile;
      score = result.themes[t].score;
      label = labelsFor(manifest, locale).theme(t);
      theme = t;
    } else if (layer.startsWith("indicator:") && scores.indicators[layer.slice(10)]) {
      const id = layer.slice(10);
      lsoa = percentileRank(scores.indicators[id]);
      score = scores.indicators[id];
      const info = manifest.indicators.find((i) => i.id === id);
      label = labelsFor(manifest, locale).indicator(id);
      theme = info?.theme;
    } else {
      lsoa = result.overallPercentile;
      score = result.overall;
      label = t("overall");
    }
    if (state.compare_within === "nation") {
      lsoa = percentileWithin(lsoa, scores.nation);
      label = t("withinNation", { label });
    } else if (state.compare_within === "urban_rural") {
      lsoa = percentileWithin(lsoa, scores.rucClass);
      label = t("withinUrbanRural", { label });
    }
    return {
      lsoa,
      score,
      msoa: aggregate(lsoa, scores.msoa, scores.population),
      lad: aggregate(lsoa, scores.lad, scores.population),
      result,
      label,
      theme,
    };
  }, [manifest, scores, result, state.map.layer, state.compare_within, t, locale]);
}
