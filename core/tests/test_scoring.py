"""Property and example tests for the scoring maths."""

import math

import polars as pl
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from lix_core.scoring import (
    band,
    band_of,
    headline_within,
    normalise,
    percentile_rank,
    score_lsoas,
    shown_percentile_of,
    weighted_score,
)

finite = st.floats(min_value=-1e6, max_value=1e6, allow_nan=False, allow_infinity=False)


def _norm(values, **kw) -> list:
    return pl.DataFrame({"v": values}).select(normalise(pl.col("v"), **kw))["v"].to_list()


class TestPercentileRank:
    def test_bounds_and_ties(self):
        out = pl.DataFrame({"v": [1.0, 2.0, 2.0, 3.0, None]}).select(percentile_rank(pl.col("v")))
        assert out["v"].to_list() == [0.0, 50.0, 50.0, 100.0, None]

    def test_lone_value_is_50_and_null_stays_null(self):
        out = pl.DataFrame({"v": [7.0, None]}).select(percentile_rank(pl.col("v")))
        assert out["v"].to_list() == [50.0, None]

    def test_within_groups(self):
        df = pl.DataFrame({"v": [1.0, 5.0, 10.0, None, 20.0, 3.0], "g": list("aabbbc")})
        out = df.select(percentile_rank(pl.col("v"), pl.col("g")))["v"].to_list()
        # a: 1 < 5 · b: 10 < 20 with a null · c: a lone value
        assert out == [0.0, 100.0, 0.0, None, 100.0, 50.0]

    @given(st.lists(finite, min_size=2, max_size=50))
    def test_monotone_and_in_range(self, values):
        out = pl.DataFrame({"v": values}).select(percentile_rank(pl.col("v")))["v"].to_list()
        assert all(0 <= p <= 100 for p in out)
        for (a, pa), (b, pb) in zip(sorted(zip(values, out)), sorted(zip(values, out))[1:]):
            assert pa <= pb + 1e-9


class TestNormalise:
    def test_rank_direction(self):
        assert _norm([1.0, 2.0, 3.0], direction="lower_better") == [100.0, 50.0, 0.0]

    def test_scale(self):
        assert _norm([0.0, 0.5, 1.2], direction="higher_better", method="scale") == [0, 50, 100]
        assert _norm([20.0], direction="lower_better", method="scale", scale_max=100) == [80.0]

    def test_threshold_linear(self):
        out = _norm([5.0, 10.0, 20.0, 30.0, 40.0], direction="lower_better",
                    method="threshold", good=10, bad=30)  # fmt: skip
        assert out == [100.0, 100.0, 50.0, 0.0, 0.0]

    def test_threshold_log(self):
        (mid,) = _norm([math.expm1((math.log1p(1000) + math.log1p(8000)) / 2)],
                       direction="lower_better", method="threshold", good=1000, bad=8000,
                       log1p=True)  # fmt: skip
        assert mid == pytest.approx(50.0)

    def test_threshold_must_match_direction(self):
        with pytest.raises(ValueError, match="contradict"):
            _norm([1.0], direction="lower_better", method="threshold", good=30, bad=10)

    @given(st.lists(finite, min_size=2, max_size=40))
    def test_every_method_stays_in_range(self, values):
        for kw in (
            {"method": "rank"},
            {"method": "scale", "scale_max": 100},
            {"method": "threshold", "good": -10, "bad": 10},
        ):
            out = _norm(values, direction="lower_better", **kw)
            assert all(0 <= v <= 100 for v in out if v is not None)


class TestWeightedScore:
    DF = pl.DataFrame({"a": [100.0, 0.0, None], "b": [0.0, 0.0, 50.0]})

    def test_weighted_mean_and_coverage(self):
        score, cov = weighted_score(self.DF, {"a": 3, "b": 1})
        assert score.to_list() == [75.0, 0.0, None]  # row 3 has only 25% of the weight
        assert cov.to_list() == [1.0, 1.0, 0.25]

    def test_zero_weight_is_ignored(self):
        score, _ = weighted_score(self.DF, {"a": 0, "b": 1})
        assert score.to_list() == [0.0, 0.0, 50.0]

    def test_needs_a_positive_weight(self):
        with pytest.raises(ValueError):
            weighted_score(self.DF, {"a": 0})

    @settings(max_examples=50)
    @given(st.floats(min_value=0.1, max_value=100))
    def test_scaling_all_weights_changes_nothing(self, k):
        base, _ = weighted_score(self.DF, {"a": 2, "b": 1})
        scaled, _ = weighted_score(self.DF, {"a": 2 * k, "b": k})
        for x, y in zip(base.to_list(), scaled.to_list()):
            assert (x is None and y is None) or x == pytest.approx(y)


INDICATORS = [("crime", "safety", 1.0), ("theft", "safety", 1.0), ("gp", "health", 1.0)]
NORMS = pl.DataFrame({
    "n__crime": [90.0, 10.0, 50.0, 70.0],
    "n__theft": [80.0, 20.0, None, 60.0],
    "n__gp": [10.0, 90.0, 50.0, 30.0],
})  # fmt: skip


class TestScoreLsoas:
    def test_columns_and_values(self):
        out = score_lsoas(NORMS, INDICATORS, {"safety": 1, "health": 1})
        assert out["theme__safety"].to_list() == [85.0, 15.0, 50.0, 65.0]
        assert out["overall"].to_list() == [47.5, 52.5, 50.0, 47.5]
        assert out["overall_pct"].to_list() == [
            pytest.approx(100 / 6),
            100.0,
            pytest.approx(200 / 3),
            pytest.approx(100 / 6),
        ]

    def test_theme_weight_zero_has_no_effect(self):
        only_safety = score_lsoas(NORMS, INDICATORS, {"safety": 1, "health": 0})
        assert only_safety["overall"].to_list() == only_safety["theme__safety"].to_list()

    def test_indicator_multiplier(self):
        out = score_lsoas(NORMS, INDICATORS, {"safety": 1, "health": 1}, {"theft": 0})
        assert out["theme__safety"].to_list() == NORMS["n__crime"].to_list()

    @settings(max_examples=40)
    @given(st.permutations(range(4)))
    def test_row_order_does_not_matter(self, order):
        shuffled = NORMS.with_row_index("i").select(pl.all().gather(list(order)))
        a = score_lsoas(NORMS, INDICATORS, {"safety": 2, "health": 1})["overall"].to_list()
        b = score_lsoas(shuffled.drop("i"), INDICATORS, {"safety": 2, "health": 1})["overall"]
        assert [a[i] for i in order] == pytest.approx(b.to_list())

    @settings(max_examples=40)
    @given(st.integers(0, 3), st.floats(0, 10))
    def test_improving_an_indicator_never_lowers_the_score(self, row, delta):
        better = NORMS.with_columns(
            pl.when(pl.int_range(pl.len()) == row)
            .then((pl.col("n__crime") + delta).clip(0, 100))
            .otherwise(pl.col("n__crime"))
            .alias("n__crime")
        )
        before = score_lsoas(NORMS, INDICATORS, {"safety": 1, "health": 1})["overall"][row]
        after = score_lsoas(better, INDICATORS, {"safety": 1, "health": 1})["overall"][row]
        assert after >= before - 1e-9


def test_band_follows_the_percentile_as_shown():
    # 19.4 reads "19%" (bottom fifth); 19.5 reads "20%", so it sits in the second fifth,
    # as does 20.0; 99.6 reads "99%" (the top is never 100) and is the top fifth
    pcts = [0.0, 19.4, 19.5, 19.9, 20.0, 79.6, 99.6, 100.0, None]
    out = pl.DataFrame({"p": pcts}).select(band(pl.col("p")))
    assert out["p"].to_list() == [1, 1, 2, 2, 2, 5, 5, 5, None]
    assert [band_of(p) for p in pcts] == [1, 1, 2, 2, 2, 5, 5, 5, None]
    assert [shown_percentile_of(p) for p in pcts] == [0, 19, 20, 20, 20, 80, 99, 99, None]
    assert band_of(float("nan")) is None


class TestApplicableThemes:
    """A theme the nation can have counts in the row's overall denominator even when the
    row has no data for it; a theme the nation cannot have is left out."""

    IND = [("a", "t1", 1.0), ("b", "t2", 1.0), ("c", "t3", 1.0)]
    APPLICABLE = {"a": ["E", "S"], "b": ["E", "S"], "c": ["E"]}

    def test_empty_theme_pulls_coverage_down_and_past_the_gate_withholds_the_overall(self):
        norms = pl.DataFrame(
            {
                "n__a": [50.0, 50.0, None],
                "n__b": [50.0, None, None],
                "n__c": [50.0, None, 60.0],
                "nation": ["E", "E", "E"],
            }
        )
        out = score_lsoas(
            norms, self.IND, {"t1": 1, "t2": 1, "t3": 1}, nation="nation",
            applicable=self.APPLICABLE,
        )  # fmt: skip
        assert out["coverage"].to_list() == pytest.approx([1.0, 1 / 3, 1 / 3])
        assert out["overall"].to_list() == [50.0, None, None]

    def test_a_theme_the_nation_cannot_have_is_left_out(self):
        # Scotland has no "c": its overall is over t1 and t2 only, at full coverage
        norms = pl.DataFrame(
            {"n__a": [40.0, 40.0], "n__b": [60.0, None], "n__c": [None, None],
             "nation": ["S", "S"]}
        )  # fmt: skip
        out = score_lsoas(norms, self.IND, {"t1": 1, "t2": 1, "t3": 1}, applicable=self.APPLICABLE)
        assert out["coverage"].to_list() == [1.0, 0.5]
        assert out["overall"].to_list() == [50.0, 40.0]

    def test_a_declared_gap_is_left_out_but_an_empty_theme_still_counts(self):
        norms = pl.DataFrame(
            {"n__a": [40.0, None], "n__b": [60.0, 60.0], "n__c": [None, None],
             "nation": ["S", "S"]}
        )  # fmt: skip
        out = score_lsoas(
            norms, self.IND, {"t1": 1, "t2": 1, "t3": 1}, applicable=self.APPLICABLE,
            theme_gaps={"S": ["t3"]},
        )  # fmt: skip
        assert out["theme__t3"].to_list() == [None, None]
        assert out["coverage"].to_list() == [1.0, 0.5]
        assert out["overall"].to_list() == [50.0, 60.0]


class TestHeadlineWithin:
    def _scores(self):
        norms = pl.DataFrame(
            {"n__a": [10.0, 40.0, 20.0, 30.0], "nation": ["E", "E", "I", "I"]}
        )  # fmt: skip
        return score_lsoas(norms, [("a", "t", 1.0)], {"t": 1.0}, group="nation"), norms["nation"]

    def test_opted_in_rows_take_the_nation_percentile_and_the_build_wide_one_is_kept(self):
        scores, nations = self._scores()
        out = headline_within(scores, nations, {"E": "all", "I": "nation"})
        assert out["overall_pct_all"].to_list() == pytest.approx([0, 100, 100 / 3, 200 / 3])
        assert out["overall_pct"].to_list() == pytest.approx([0, 100, 0, 100])
        assert out["theme_pct__t"].to_list() == pytest.approx([0, 100, 0, 100])
        assert out["theme_pct_all__t"].to_list() == pytest.approx([0, 100, 100 / 3, 200 / 3])
        assert out["headline_within"].to_list() == ["all", "all", "nation", "nation"]

    def test_without_an_opted_in_nation_nothing_moves(self):
        scores, nations = self._scores()
        out = headline_within(scores, nations, {"E": "all", "I": "all"})
        assert out["overall_pct"].to_list() == scores["overall_pct"].to_list()
        assert out["overall_pct_all"].to_list() == scores["overall_pct"].to_list()
        assert out["headline_within"].unique().to_list() == ["all"]

    def test_needs_the_nation_percentiles(self):
        norms = pl.DataFrame({"n__a": [10.0, 40.0], "nation": ["E", "I"]})
        scores = score_lsoas(norms, [("a", "t", 1.0)], {"t": 1.0})
        with pytest.raises(ValueError, match="group='nation'"):
            headline_within(scores, norms["nation"], {"I": "nation"})


class TestGroupedScores:
    def test_score_lsoas_adds_within_group_percentiles(self):
        norms = pl.DataFrame({"n__a": [10.0, 20.0, 30.0, 40.0], "nation": ["E", "E", "W", "W"]})
        out = score_lsoas(norms, [("a", "t", 1.0)], {"t": 1.0}, group="nation")
        assert out["theme_pct__t"].to_list() == pytest.approx([0, 100 / 3, 200 / 3, 100])
        assert out["theme_pct_nation__t"].to_list() == [0.0, 100.0, 0.0, 100.0]
        assert out["overall_pct_nation"].to_list() == [0.0, 100.0, 0.0, 100.0]
        assert list(out.columns) == [
            "theme__t", "theme_pct__t", "theme_pct_nation__t", "theme_coverage__t",
            "overall", "overall_pct", "overall_pct_nation", "coverage",
        ]  # fmt: skip

    def test_several_groups_each_get_their_percentiles(self):
        norms = pl.DataFrame(
            {
                "n__a": [10.0, 20.0, 30.0, 40.0],
                "nation": ["E", "E", "W", "W"],
                "ruc_class": ["urban", "rural", "urban", "rural"],
            }
        )
        out = score_lsoas(norms, [("a", "t", 1.0)], {"t": 1.0}, group=["nation", "ruc_class"])
        assert out["overall_pct_nation"].to_list() == [0.0, 100.0, 0.0, 100.0]
        assert out["overall_pct_ruc_class"].to_list() == [0.0, 0.0, 100.0, 100.0]
        assert out["theme_pct_ruc_class__t"].to_list() == [0.0, 0.0, 100.0, 100.0]

    def test_without_group_columns_are_unchanged(self):
        norms = pl.DataFrame({"n__a": [10.0, 20.0]})
        out = score_lsoas(norms, [("a", "t", 1.0)], {"t": 1.0})
        assert "overall_pct_nation" not in out.columns
        assert not any(c.startswith("theme_pct_nation") for c in out.columns)

    def test_rank_normalise_within_group(self):
        df = pl.DataFrame({"v": [5.0, 5.0, 2.0, 30.0, None, 1.0], "n": list("EEWEWW")})
        out = df.select(normalise(pl.col("v"), "lower_better", group=pl.col("n")))["v"].to_list()
        assert out == [75.0, 75.0, 0.0, 0.0, None, 100.0]
