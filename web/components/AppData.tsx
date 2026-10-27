"use client";

/**
 * App-wide data and state:
 * - manifest + scores loaded once from the static data server
 * - the shared assistant state (AG-UI), read and written through useLiveability()
 * - scores recomputed in the browser for the current weights (useScores)
 */
import { UseAgentUpdate, useAgent } from "@copilotkit/react-core/v2";
import { useLocale, useTranslations } from "next-intl";
import { usePathname } from "next/navigation";
import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState } from "react";

import { searchPlaces } from "@/lib/api";
import {
  type Manifest,
  type ScoreData,
  headlineWithin,
  loadManifest,
  loadScores,
  scoredIndicators,
  themeGaps,
} from "@/lib/data";
import {
  MIN_THEME_COVERAGE,
  type ScoreResult,
  aggregate,
  headlinePercentile,
  percentileRank,
  percentileWithin,
  scoreLsoas,
} from "@/lib/scoring";
import type { LiveabilityState as WireState } from "@/lib/contracts.gen";
import { DEFAULT_STATE, type LiveabilityState, bboxOf, effectiveWeights, normaliseState } from "@/lib/state";
import { labelsFor } from "@/lib/labels";
import { profileState, readSavedProfile, saveProfile, savedFromState } from "@/lib/profileStore";
import { readShortlist, saveShortlist } from "@/lib/shortlistStore";
import { readHash, writeHash } from "@/lib/urlState";

interface DataContext {
  manifest: Manifest | null;
  scores: ScoreData | null;
  error: string | null;
}

const Ctx = createContext<DataContext>({ manifest: null, scores: null, error: null });

/** Fired when a link pasted into an open tab was followed; detail is the selected area. */
export const LINK_ARRIVED = "lix:link-arrived";

// Only the map page needs the scores file, the largest download of the site: pages of
// text (and the not-found page) need the manifest alone (#188)
const MAP_ROUTE = /^\/?$/;

export function DataProvider({ children }: { children: React.ReactNode }) {
  const [value, setValue] = useState<DataContext>({ manifest: null, scores: null, error: null });
  const pathname = usePathname();
  const docsOnly = !MAP_ROUTE.test(pathname ?? "/");
  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const manifest = await loadManifest();
        if (cancelled) return;
        setValue((v) => ({ ...v, manifest, error: null }));
        if (docsOnly) return;
        const scores = await loadScores(manifest);
        if (!cancelled) setValue({ manifest, scores, error: null });
      } catch (e) {
        if (!cancelled) setValue((v) => ({ ...v, error: String(e) }));
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [docsOnly]);
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
 * - the URL hash mirrors the shareable part, and a shared link seeds the first state;
 *   without one, the profile this browser keeps (lix.profile) does, and while a profile
 *   is active the saved copy follows the sliders;
 * - the shortlist this browser keeps (lix.shortlist) seeds the first state and follows
 *   every change, so an area saved before the Shortlist tab opens is never lost.
 */
export function LiveabilityStateProvider({ children }: { children: React.ReactNode }) {
  const { agent } = useAgent({ updates: [UseAgentUpdate.OnStateChanged] });
  const [state, setState] = useState<LiveabilityState>(() => {
    const hash = readHash();
    const saved = typeof window === "undefined" ? null : readSavedProfile();
    const seed = Object.keys(hash).length ? hash : saved ? profileState(saved) : {};
    return normaliseState({ ...DEFAULT_STATE, ...seed } as Partial<WireState>);
  });
  const latest = useRef(state);
  const pushedTo = useRef<unknown>(null);
  const { scores } = useData();

  // A shared link names an area but no view: take the recipient there once the data is
  // in (the hash never carries the map's position)
  const flownTo = useRef<string | null>(null);
  const flyToSelected = useCallback(() => {
    const code = latest.current.map.selected;
    if (!scores || !code || latest.current.map.bbox || flownTo.current === code) return;
    flownTo.current = code;
    if (!scores.index.has(code)) return;
    searchPlaces(code)
      .then((places) => {
        const hit = places.find((p) => p.code === code || p.lsoa21cd === code) ?? places[0];
        const bbox =
          bboxOf(hit?.bbox) ??
          (hit?.centre
            ? ([hit.centre.lon - 0.02, hit.centre.lat - 0.012, hit.centre.lon + 0.02, hit.centre.lat + 0.012] as const)
            : null);
        if (!bbox || latest.current.map.selected !== code) return;
        const next = { ...latest.current, map: { ...latest.current.map, bbox: [...bbox] as [number, number, number, number] } };
        latest.current = next;
        setState(next);
        agent.setState(next);
      })
      .catch(() => {});
  }, [scores, agent]);
  useEffect(() => {
    flyToSelected();
  }, [flyToSelected]);

  // A link pasted into an open tab changes only the hash: follow it (our own writes use
  // replaceState, which fires no hashchange). The link replaces the shareable state, as
  // a fresh load would, and keeps what is this browser's own (shortlist, mode, language);
  // the assistant is told, so its next answer starts from the view the user sees, the
  // map goes to the area it names and the side panel opens its profile
  useEffect(() => {
    const onHash = () => {
      const hash = readHash();
      if (!Object.keys(hash).length) return;
      const s = latest.current;
      const next = normaliseState({
        ...DEFAULT_STATE,
        ...hash,
        shortlist: s.shortlist,
        mode: s.mode,
        locale: s.locale,
      } as Partial<WireState>);
      latest.current = next;
      setState(next);
      agent.setState(next);
      flownTo.current = null;
      flyToSelected();
      window.dispatchEvent(new CustomEvent(LINK_ARRIVED, { detail: next.map.selected }));
    };
    window.addEventListener("hashchange", onHash);
    return () => window.removeEventListener("hashchange", onHash);
  }, [agent, flyToSelected]);

  // Snapshots from the agent replace the page state (adjusted during render, not in an effect)
  const [seenAgentState, setSeenAgentState] = useState<unknown>(agent.state);
  if (agent.state !== seenAgentState) {
    setSeenAgentState(agent.state);
    const incoming = agent.state as Partial<WireState> | undefined;
    if (incoming && Object.keys(incoming).length) setState(normaliseState(incoming));
  }

  const restored = useRef(false);
  useEffect(() => {
    latest.current = state;
    writeHash(state);
    // Never write the empty first frame over a saved list: persist once it has been read
    if (restored.current || state.shortlist.length) {
      restored.current = true;
      saveShortlist(state.shortlist);
    }
    if (state.profile) {
      const prev = readSavedProfile();
      const next = savedFromState(state, prev);
      if (next && JSON.stringify(next) !== JSON.stringify(prev)) saveProfile(next);
    }
  }, [state]);

  // A new agent instance starts empty: hand it the current view
  useEffect(() => {
    if (pushedTo.current === agent) return;
    pushedTo.current = agent;
    agent.setState(latest.current);
  }, [agent]);

  // The shortlist this browser keeps joins the state right after mount (not in the first
  // render, which must match the server's), merged with anything already there. Declared
  // after the two effects above on purpose: effects run in declaration order, and on the
  // first frame the state effect records that frame in `latest` and the agent effect hands
  // it to the assistant. Run before them, this merge was undone by both (the assistant's
  // snapshot, without the list, replaced the page state in the next render), so a saved
  // list never showed in the production build; development hid it, because StrictMode
  // runs mount effects twice and the second pass skips the hand-over (#217).
  useEffect(() => {
    const saved = readShortlist();
    if (!saved.length) return;
    const s = latest.current;
    const codes = new Set(s.shortlist.map((i) => i.code));
    const next = { ...s, shortlist: [...saved.filter((i) => !codes.has(i.code)), ...s.shortlist] };
    latest.current = next;
    setState(next);
    agent.setState(next);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

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
  /** Why the map shows nothing: every theme weight is 0 */
  notice?: string;
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
      // A layer the pack cannot map (a diagnostic measure, a typo in a link) shows the
      // overall and says so rather than passing itself off as the overall
      label =
        layer === "overall"
          ? t("overall")
          : t("layerUnavailable", { layer: layer.slice(layer.indexOf(":") + 1) });
    }
    // Every theme at 0 is no weighting: the overall is unknown everywhere, and the legend
    // says why instead of the page falling over
    const notice = Object.values(weights?.themes ?? {}).some((w) => w > 0) ? undefined : t("noWeights");
    if (state.compare_within === "uk") {
      // A nation whose data is thinner compares its areas among themselves by default
      // (headline_within in the manifest, #147); the rest keep the build-wide percentile
      lsoa = headlinePercentile(lsoa, scores.nation, headlineWithin(manifest));
    } else if (state.compare_within === "nation") {
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
      notice,
    };
  }, [manifest, scores, result, weights, state.map.layer, state.compare_within, t, locale]);
}
