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
  const idx: number[] = [];
  for (let i = 0; i < n; i++) if (!Number.isNaN(values[i])) idx.push(i);
  const m = idx.length;
  if (m === 0) return out;
  if (m === 1) {
    out[idx[0]] = 50;
    return out;
  }
  idx.sort((a, b) => values[a] - values[b]);
  let start = 0;
  while (start < m) {
    let end = start;
    while (end + 1 < m && values[idx[end + 1]] === values[idx[start]]) end++;
    // Average 1-based rank of the tie group
    const rank = (start + end) / 2 + 1;
    const pct = ((rank - 1) / (m - 1)) * 100;
    for (let k = start; k <= end; k++) out[idx[k]] = pct;
    start = end + 1;
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
  const active = columns
    .map((c, i) => [c, weights[i], masks?.[i]] as const)
    .filter(([, w]) => w > 0);
  if (active.length === 0) throw new Error("At least one weight must be positive");
  const n = active[0][0].length;
  const score = new Float64Array(n);
  const coverage = new Float64Array(n);
  for (let i = 0; i < n; i++) {
    let num = 0;
    let avail = 0;
    let total = 0;
    for (const [col, w, mask] of active) {
      if (mask && !mask[i]) continue;
      total += w;
      const v = col[i];
      if (!Number.isNaN(v)) {
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
): ScoreResult {
  const themeNames = [...new Set(indicators.map((i) => i.theme))].sort();
  const themes: Record<string, ThemeResult> = {};
  const themeMasks: Record<string, Uint8Array | undefined> = {};
  const n = groups?.length ?? Object.values(norms)[0]?.length ?? 0;
  const maskOf = (allowed: string[] | undefined): Uint8Array | undefined => {
    if (!groups || !allowed) return undefined;
    const ok = new Set(allowed);
    return Uint8Array.from(groups, (g) => (ok.has(g) ? 1 : 0));
  };
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
    const gapped = groups
      ? Uint8Array.from(groups, (g) => (themeGaps?.[g]?.includes(theme) ? 1 : 0))
      : undefined;
    if (gapped) {
      for (let i = 0; i < n; i++) {
        if (gapped[i]) {
          score[i] = NaN;
          coverage[i] = 0;
        }
      }
    }
    themeMasks[theme] =
      groups && (themeGaps || members.some((i) => i.coverage))
        ? Uint8Array.from(coverage, (c, i) => (c > 0 && !gapped?.[i] ? 1 : 0))
        : undefined;
    themes[theme] = { score, percentile: percentileRank(score), coverage };
    if (groups) themes[theme].percentileWithin = percentileWithin(score, groups);
  }
  const present = Object.keys(themes);
  const { score: overall, coverage } = weightedScore(
    present.map((t) => themes[t].score),
    present.map((t) => themeWeights[t] ?? 0),
    minCoverage,
    present.map((t) => themeMasks[t]),
  );
  const result: ScoreResult = { themes, overall, overallPercentile: percentileRank(overall), coverage };
  if (groups) result.overallPercentileWithin = percentileWithin(overall, groups);
  return result;
}

/** Percentile within groups (e.g. urban/rural class), for like-for-like comparison. */
export function percentileWithin(values: Float64Array, groups: string[]): Float64Array {
  const out = new Float64Array(values.length).fill(NaN);
  const byGroup = new Map<string, number[]>();
  groups.forEach((g, i) => byGroup.set(g, [...(byGroup.get(g) ?? []), i]));
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
