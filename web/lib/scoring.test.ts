import { readFileSync } from "node:fs";
import path from "node:path";
import { describe, expect, it } from "vitest";

import {
  MIN_THEME_COVERAGE,
  aggregate,
  bandOf,
  headlinePercentile,
  percentileRank,
  percentileWithin,
  scoreLsoas,
  shownPercentile,
} from "./scoring";

// Shared with core/tests/test_golden.py: both implementations must match
const golden = JSON.parse(
  readFileSync(
    path.resolve(
      import.meta.dirname,
      "../../contracts/fixtures/scoring_golden.json",
    ),
    "utf8",
  ),
);

const toArray = (xs: (number | null)[]) =>
  Float64Array.from(xs, (x) => (x === null ? NaN : x));
const close = (a: number, b: number | null) =>
  b === null ? Number.isNaN(a) : Math.abs(a - b) < 1e-9;

describe("golden cases match the Python scorer", () => {
  const norms = Object.fromEntries(
    golden.indicators.map((i: { id: string }) => [
      i.id,
      toArray(golden.normalised[`n__${i.id}`]),
    ]),
  );
  const indicators = golden.indicators.map(
    (i: {
      id: string;
      theme: string;
      weight: number;
      coverage?: string[];
    }) => ({
      id: i.id,
      theme: i.theme,
      weight: i.weight,
      coverage: i.coverage,
    }),
  );

  // The Python side scores with group="nation", so every percentile also comes within nation
  const groups: string[] = golden.groups.nation;

  for (const [name, c] of Object.entries(golden.cases) as [string, any][]) {
    it(name, () => {
      const r = scoreLsoas(
        norms,
        indicators,
        c.themes,
        c.indicators,
        MIN_THEME_COVERAGE,
        groups,
        golden.theme_gaps,
        true,
      );
      const expected = golden.expected[name];
      for (const [col, values] of Object.entries(expected) as [
        string,
        (number | null)[],
      ][]) {
        let got: Float64Array | undefined;
        if (col === "overall") got = r.overall;
        else if (col === "overall_pct") got = r.overallPercentile;
        else if (col === "overall_pct_nation") got = r.overallPercentileWithin;
        else if (col === "coverage") got = r.coverage;
        else if (col === "band") got = r.overallPercentile.map(bandOf);
        else if (col === "headline.overall_pct")
          got = headlinePercentile(r.overallPercentile, groups, golden.headline_within);
        else if (col === "headline.overall_pct_all") got = r.overallPercentile;
        else if (col === "headline.headline_within") {
          const want = values as unknown as string[];
          groups.forEach((g, i) =>
            expect(golden.headline_within[g] ?? "all", `${name}.${col}[${i}]`).toBe(want[i]),
          );
          continue;
        } else {
          const [kind, theme] = col.split("__");
          const t = r.themes[theme];
          got =
            kind === "theme"
              ? t.score
              : kind === "theme_pct"
                ? t.percentile
                : kind === "theme_pct_nation"
                  ? t.percentileWithin
                  : t.coverage;
        }
        if (!got) throw new Error(`no column for ${col}`);
        values.forEach((v, i) =>
          expect(close(got[i], v), `${name}.${col}[${i}]`).toBe(true),
        );
      }
    });
  }
});

describe("helpers", () => {
  it("percentileRank averages ties and keeps NaN", () => {
    expect([...percentileRank(toArray([1, 2, 2, 3, null]))]).toEqual([
      0,
      50,
      50,
      100,
      NaN,
    ]);
    expect([...percentileRank(toArray([7, null]))]).toEqual([50, NaN]);
  });

  it("scoreLsoas with groups adds within-group percentiles", () => {
    const norms = { a: toArray([10, 20, 30, 40]) };
    const r = scoreLsoas(
      norms,
      [{ id: "a", theme: "t", weight: 1 }],
      { t: 1 },
      {},
      0.5,
      ["x", "x", "y", "y"],
      undefined,
      true,
    );
    expect([...r.themes.t.percentileWithin!]).toEqual([0, 100, 0, 100]);
    expect([...r.overallPercentileWithin!]).toEqual([0, 100, 0, 100]);
    [...r.overallPercentile].forEach((v, i) =>
      expect(v).toBeCloseTo([0, 100 / 3, 200 / 3, 100][i], 9),
    );
  });

  it("bandOf follows the percentile as shown", () => {
    expect([0, 19.4, 19.5, 19.9, 20, 79.6, 99.6, 100].map(bandOf)).toEqual([
      1, 1, 2, 2, 2, 5, 5, 5,
    ]);
    expect([0, 19.4, 19.5, 79.6, 99.6, 100].map(shownPercentile)).toEqual([
      0, 19, 20, 80, 99, 99,
    ]);
    expect(bandOf(NaN)).toBeNaN();
  });

  it("headlinePercentile re-ranks only the opted-in groups", () => {
    const values = toArray([10, 40, 20, 30]);
    const groups = ["E", "E", "I", "I"];
    const out = headlinePercentile(values, groups, { E: "all", I: "nation" });
    expect([...out]).toEqual([10, 40, 0, 100]);
    expect(headlinePercentile(values, groups, { E: "all" })).toBe(values);
  });

  it("every theme weight at 0 gives an unknown overall rather than an error", () => {
    const norms = { a: toArray([10, 20]), b: toArray([30, 40]) };
    const r = scoreLsoas(
      norms,
      [
        { id: "a", theme: "t1", weight: 1 },
        { id: "b", theme: "t2", weight: 1 },
      ],
      { t1: 0, t2: 0 },
    );
    expect([...r.overall]).toEqual([NaN, NaN]);
    expect([...r.coverage]).toEqual([0, 0]);
    expect([...r.themes.t1.score]).toEqual([10, 20]);
  });

  it("percentileWithin ranks inside each group", () => {
    const out = percentileWithin(toArray([1, 5, 10, 20]), ["u", "r", "u", "r"]);
    expect([...out]).toEqual([0, 0, 100, 100]);
  });

  it("aggregate is population weighted and skips NaN", () => {
    const m = aggregate(
      toArray([10, 20, null]),
      ["a", "a", "a"],
      toArray([1, 3, 5]),
    );
    expect(m.get("a")).toBeCloseTo(17.5);
  });
});
