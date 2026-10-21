/**
 * Browser port of lix_core.scoring: theme and overall scores from per-indicator
 * 0–100 scores, for any weighting, in a few milliseconds for every LSOA in the build.
 *
 * Must match the Python implementation exactly; both are tested against
 * contracts/fixtures/scoring_golden.json. Missing values are NaN.
 */

export const MIN_THEME_COVERAGE = 0.5;

export interface ScoredIndicator {
  id: string;
  theme: string;
  weight: number;
  /** Nations the indicator is built for (manifest `coverage`); every nation when absent. */
  coverage?: string[];
}

export interface ThemeResult {
  score: Float64Array;
  percentile: Float64Array;
  /** Percentile within the group passed to scoreLsoas (the nation), when given. */
  percentileWithin?: Float64Array;
  coverage: Float64Array;
}

export interface ScoreResult {
  themes: Record<string, ThemeResult>;
  overall: Float64Array;
  overallPercentile: Float64Array;
  overallPercentileWithin?: Float64Array;
  coverage: Float64Array;
}

/** 0–100 percentile, ties averaged, lowest 0 and highest 100; NaN stays NaN. */
export function percentileRank(values: Float64Array): Float64Array {
  const n = values.length;
  const out = new Float64Array(n).fill(NaN);
  // Sort the values themselves (a typed-array sort is numeric and fast), then place
  // each row by binary search; ties take the average of their 1-based ranks
  const sorted = new Float64Array(n);
  let m = 0;
  for (let i = 0; i < n; i++)
    if (values[i] === values[i]) sorted[m++] = values[i];
  if (m === 0) return out;
  const s = sorted.subarray(0, m).sort();
  for (let i = 0; i < n; i++) {
    const v = values[i];
    if (v !== v) continue;
    if (m === 1) {
      out[i] = 50;
      continue;
    }
    let lo = 0;
    let hi = m;
    while (lo < hi) {
      const mid = (lo + hi) >>> 1;
      if (s[mid] < v) lo = mid + 1;
      else hi = mid;
    }
    const first = lo;
    hi = m;
    while (lo < hi) {
      const mid = (lo + hi) >>> 1;
      if (s[mid] <= v) lo = mid + 1;
      else hi = mid;
    }
    const rank = (first + lo - 1) / 2 + 1;
    out[i] = ((rank - 1) / (m - 1)) * 100;
  }
  return out;
}

/**
 * Weighted mean of 0–100 columns over the ones present; NaN below minCoverage. `masks`
 * (one per column, optional) say which rows a column applies to (its nation has it), so
 * the row's total weight counts only those columns, as lix_core.scoring does.
 */
export function weightedScore(
  columns: Float64Array[],
  weights: number[],
  minCoverage = MIN_THEME_COVERAGE,
  masks?: (Uint8Array | undefined)[],
): { score: Float64Array; coverage: Float64Array } {
  const cols: Float64Array[] = [];
  const ws: number[] = [];
  const ms: (Uint8Array | undefined)[] = [];
  columns.forEach((c, i) => {
    if (weights[i] > 0) {
      cols.push(c);
      ws.push(weights[i]);
      ms.push(masks?.[i]);
    }
  });
  const k = cols.length;
  if (k === 0) throw new Error("At least one weight must be positive");
  const n = cols[0].length;
  const score = new Float64Array(n);
  const coverage = new Float64Array(n);
  for (let i = 0; i < n; i++) {
    let num = 0;
    let avail = 0;
    let total = 0;
    for (let j = 0; j < k; j++) {
      const mask = ms[j];
      if (mask !== undefined && mask[i] === 0) continue;
      const w = ws[j];
      total += w;
      const v = cols[j][i];
      if (v === v) {
        num += v * w;
        avail += w;
      }
    }
    const share = total > 0 ? avail / total : 0;
    coverage[i] = share;
    score[i] = total > 0 && share >= minCoverage ? num / avail : NaN;
  }
  return { score, coverage };
}

/**
 * Theme and overall scores for every LSOA. With `groups` (one label per row, e.g. the
 * nation) each percentile is also taken within the group, as lix_core.scoring does with
 * `group=`; the same labels say which indicators apply to a row (`coverage` on each
 * indicator) and which themes a nation declares it cannot cover (`themeGaps`, nation →
 * themes), so a theme counts in a row's overall only where it can exist.
 */
export function scoreLsoas(
  norms: Record<string, Float64Array>,
  indicators: ScoredIndicator[],
  themeWeights: Record<string, number>,
  multipliers: Record<string, number> = {},
  minCoverage = MIN_THEME_COVERAGE,
  groups?: string[],
  themeGaps?: Record<string, string[]>,
  withinGroups = false,
): ScoreResult {
  // Within-group percentiles (the compare-within toggle) are computed on demand by the
  // page from whichever layer is shown; scoring every theme's would cost nine extra sorts
  const themeNames = [...new Set(indicators.map((i) => i.theme))].sort();
  const themes: Record<string, ThemeResult> = {};
  const themeMasks: Record<string, Uint8Array | undefined> = {};
  const n = groups?.length ?? Object.values(norms)[0]?.length ?? 0;
  const present = groups ? new Set(groups) : new Set<string>();
  // One mask per distinct coverage list; none when it covers every group in the data
  const maskCache = new Map<string, Uint8Array | undefined>();
  const maskOf = (allowed: string[] | undefined): Uint8Array | undefined => {
    if (!groups || !allowed) return undefined;
    const key = allowed.join(",");
    if (!maskCache.has(key)) {
      const ok = new Set(allowed);
      const full = [...present].every((g) => ok.has(g));
      maskCache.set(
        key,
        full ? undefined : Uint8Array.from(groups, (g) => (ok.has(g) ? 1 : 0)),
      );
    }
    return maskCache.get(key);
  };
  const gapKeys = new Set<string>();
  for (const [g, ts] of Object.entries(themeGaps ?? {}))
    for (const t of ts) gapKeys.add(`${g}|${t}`);
  for (const theme of themeNames) {
    const members = indicators.filter((i) => i.theme === theme);
    const weights = members.map((i) => i.weight * (multipliers[i.id] ?? 1));
    if (!weights.some((w) => w > 0)) continue;
    const { score, coverage } = weightedScore(
      members.map((i) => norms[i.id]),
      weights,
      minCoverage,
      members.map((i) => maskOf(i.coverage)),
    );
    const gapped =
      groups && [...present].some((g) => gapKeys.has(`${g}|${theme}`))
        ? Uint8Array.from(groups, (g) => (gapKeys.has(`${g}|${theme}`) ? 1 : 0))
        : undefined;
    if (gapped) {
      for (let i = 0; i < n; i++) {
        if (gapped[i]) {
          score[i] = NaN;
          coverage[i] = 0;
        }
      }
    }
    // The theme counts in a row's overall wherever its nation can have it (any weighted
    // indicator applies), data or not, so an area missing most of its themes fails the
    // gate rather than taking a band from the one or two it has; a theme the nation
    // cannot have, or declares a gap for, is left out (as lix_core.scoring does)
    const indicatorMasks = members
      .map((i, j) => (weights[j] > 0 ? maskOf(i.coverage) : null))
      .filter((m) => m !== null);
    const everywhere = indicatorMasks.some((m) => m === undefined);
    themeMasks[theme] =
      groups && (gapped || !everywhere)
        ? Uint8Array.from(coverage, (_, i) => {
            const applies =
              everywhere || indicatorMasks.some((m) => m !== undefined && m[i] === 1);
            return applies && !gapped?.[i] ? 1 : 0;
          })
        : undefined;
    themes[theme] = { score, percentile: percentileRank(score), coverage };
    if (groups && withinGroups)
      themes[theme].percentileWithin = percentileWithin(score, groups);
  }
  const scoredThemes = Object.keys(themes);
  // Every theme at weight 0 (sliders, or a shared link) is no weighting at all: the
  // overall is unknown everywhere rather than an error that takes the page down
  const { score: overall, coverage } = scoredThemes.some((t) => (themeWeights[t] ?? 0) > 0)
    ? weightedScore(
        scoredThemes.map((t) => themes[t].score),
        scoredThemes.map((t) => themeWeights[t] ?? 0),
        minCoverage,
        scoredThemes.map((t) => themeMasks[t]),
      )
    : { score: new Float64Array(n).fill(NaN), coverage: new Float64Array(n) };
  const result: ScoreResult = {
    themes,
    overall,
    overallPercentile: percentileRank(overall),
    coverage,
  };
  if (groups && withinGroups)
    result.overallPercentileWithin = percentileWithin(overall, groups);
  return result;
}

/**
 * The headline percentiles after each nation's rule (lix_core.scoring.headline_within):
 * rows whose group is in `rule` as "nation" take their percentile within that group, the
 * rest keep the build-wide value. One extra sort per opted-in nation.
 */
export function headlinePercentile(
  values: Float64Array,
  groups: string[],
  rule: Record<string, "all" | "nation">,
): Float64Array {
  const own = new Set(Object.keys(rule).filter((g) => rule[g] === "nation"));
  if (own.size === 0) return values;
  const out = Float64Array.from(values);
  const byGroup = new Map<string, number[]>();
  for (let i = 0; i < groups.length; i++) {
    if (!own.has(groups[i])) continue;
    let members = byGroup.get(groups[i]);
    if (!members) byGroup.set(groups[i], (members = []));
    members.push(i);
  }
  for (const members of byGroup.values()) {
    const ranks = percentileRank(Float64Array.from(members, (i) => values[i]));
    members.forEach((i, k) => (out[i] = ranks[k]));
  }
  return out;
}

/** The percentile as people read it: whole (halves up), and the top area is 99, never 100. */
export function shownPercentile(percentile: number): number {
  return Math.min(99, Math.floor(percentile + 0.5));
}

/** Consumer-facing band 1–5 (fifths) from the percentile as shown, so "better than 80%"
 * never sits beside "fourth fifth". NaN for NaN. */
export function bandOf(percentile: number): number {
  if (percentile !== percentile) return NaN;
  return Math.min(Math.floor(shownPercentile(percentile) / 20), 4) + 1;
}

/** Percentile within groups (e.g. urban/rural class), for like-for-like comparison. */
export function percentileWithin(
  values: Float64Array,
  groups: string[],
): Float64Array {
  const out = new Float64Array(values.length).fill(NaN);
  const byGroup = new Map<string, number[]>();
  for (let i = 0; i < groups.length; i++) {
    let members = byGroup.get(groups[i]);
    if (!members) byGroup.set(groups[i], (members = []));
    members.push(i);
  }
  for (const members of byGroup.values()) {
    const ranks = percentileRank(Float64Array.from(members, (i) => values[i]));
    members.forEach((i, k) => (out[i] = ranks[k]));
  }
  return out;
}

/** Population-weighted mean of LSOA values per parent area (MSOA or local authority). */
export function aggregate(
  values: Float64Array,
  parents: string[],
  population: Float64Array,
): Map<string, number> {
  const sums = new Map<string, [number, number]>();
  for (let i = 0; i < values.length; i++) {
    const v = values[i];
    if (Number.isNaN(v)) continue;
    const acc = sums.get(parents[i]) ?? [0, 0];
    acc[0] += v * population[i];
    acc[1] += population[i];
    sums.set(parents[i], acc);
  }
  return new Map([...sums].map(([k, [s, w]]) => [k, s / w]));
}
