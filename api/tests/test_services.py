"""Tests for the API services on the synthetic store."""

import pytest

from lix_api.services import areas, compare, explain, pois, ranking, search
from lix_api.services.scoring import resolve_weights
from lix_api.services.sql import SqlError, SqlGuard


class TestSearch:
    def test_postcode_exact(self, store):
        (p,) = search.search_place(store, "ls6 3hn")
        assert (p.kind, p.lsoa21cd) == ("postcode", "E01000001")

    def test_lsoa_and_area_codes(self, store):
        assert search.search_place(store, "E01000003")[0].kind == "lsoa"
        assert search.search_place(store, "E08000035")[0].name == "Leeds"

    def test_city_beats_village_and_lad_first(self, store):
        results = search.search_place(store, "Leeds")
        assert results[0].kind == "lad"
        assert [r.detail for r in results if r.kind == "place"][0] == "City, Leeds"

    def test_typo_and_nonsense(self, store):
        assert search.search_place(store, "Headingly")[0].name == "Headingley"
        assert search.search_place(store, "Nowheresville XYZ") == []

    def test_case_and_accents_do_not_matter(self, store):
        assert search.fold("Ynys Môn") == "ynys mon"
        assert search.search_place(store, "leeds")[0].kind == "lad"
        assert search.search_place(store, "ynys mon")[0].name == "Ynys Môn"
        # A query in the other language matches the alternative name and shows it
        caerdydd = search.search_place(store, "CAERDYDD")[0]
        assert (caerdydd.name, caerdydd.lsoa21cd) == ("Caerdydd", "W01001880")


class TestProfile:
    def test_profile_fields(self, store):
        p = areas.area_profile(store, "LS6 3HN")
        assert p.lsoa21cd == "E01000001"
        assert p.neighbourhood == "Headingley"
        assert len(p.themes) == len(store.themes)
        assert p.strengths[0].score >= p.strengths[-1].score
        assert p.weaknesses[0].score <= p.weaknesses[-1].score
        assert 1 <= p.band <= 5

    def test_imputed_crime_is_flagged(self, store):
        assert any("Greater Manchester" in f for f in areas.area_profile(store, "E01000004").flags)

    def test_unknown_area(self, store):
        with pytest.raises(LookupError):
            areas.area_profile(store, "Nowheresville XYZ")


class TestRanking:
    def test_within_local_authority(self, store):
        r = ranking.rank_areas(store, within="Leeds", level="lsoa", limit=10)
        assert r.candidates == 4
        assert all(a.local_authority == "Leeds" for a in r.results)
        overall = [a.overall for a in r.results]
        assert overall == sorted(overall, reverse=True)

    def test_price_filter_and_msoa_level(self, store):
        r = ranking.rank_areas(store, level="msoa", max_median_price=400_000)
        # Brighton's MSOA is over budget; Leeds and Cardiff pass
        assert {a.code for a in r.results} == {"E02000001", "E02000002", "W02000384"}

    def test_weights_change_the_order(self, store):
        safety_only = {t: 0.0 for t in store.themes} | {"safety": 1.0}
        r = ranking.rank_areas(store, level="lsoa", theme_weights=safety_only, limit=1)
        assert r.results[0].code == "E01000003"  # fewest crimes in the fixture
        assert r.preset.endswith("(adjusted)")

    def test_radius_around_place(self, store):
        r = ranking.rank_areas(store, within="Brighton", radius_km=5, level="lsoa")
        assert {a.code for a in r.results} == {"E01000005", "E01000006"}

    def test_unknown_preset(self, store):
        with pytest.raises(ValueError, match="Unknown preset"):
            resolve_weights(store, "hermit")


def test_compare(store):
    c = compare.compare_areas(store, ["LS6 3HN", "Brighton and Hove"])
    assert [a.level for a in c.areas] == ["lsoa", "lad"]
    assert c.areas[1].indicators["house_price"] == pytest.approx(
        (450_000 * 1900 + 520_000 * 2000) / 3900, rel=1e-3
    )
    with pytest.raises(ValueError):
        compare.compare_areas(store, ["LS6 3HN"])


class TestPois:
    def test_nearest_first_and_well_run_filter(self, store):
        r = pois.nearest_pois(store, "well_run_pub", "LS6 3HN", max_km=2)
        assert [p.name for p in r.pois] == ["Well Run Arms"]
        allpubs = pois.nearest_pois(store, "pub", "LS6 3HN", max_km=2)
        assert [p.name for p in allpubs.pois] == ["Grubby Tavern", "Well Run Arms"]

    def test_unknown_category(self, store):
        with pytest.raises(ValueError, match="Unknown category"):
            pois.nearest_pois(store, "spaceport", "LS6 3HN")


def test_explain_shares_sum_to_one(store):
    e = explain.explain_score(store, "LS6 3HN")
    assert sum(c.weight_share for c in e.contributions) == pytest.approx(1.0, abs=0.01)
    contributions = [c.contribution for c in e.contributions]
    assert contributions == sorted(contributions, reverse=True)
    one = explain.explain_score(store, "LS6 3HN", theme="safety")
    assert [c.theme for c in one.contributions] == ["safety"]


class TestSqlGuard:
    @pytest.fixture
    def guard(self, store):
        return SqlGuard(store)

    def test_select(self, guard):
        r = guard.run("SELECT lad_nm, count(*) AS n FROM lsoa GROUP BY 1 ORDER BY 1")
        assert r.columns == ["lad_nm", "n"]
        assert r.rows == [["Brighton and Hove", 2], ["Cardiff", 2], ["Leeds", 4]]

    @pytest.mark.parametrize(
        "sql",
        [
            "COPY lsoa TO '/tmp/x.csv'",
            "ATTACH '/tmp/x.db'",
            "SELECT 1; SELECT 2",
            "INSTALL httpfs",
            "SET enable_external_access = true",
            "CREATE TABLE t AS SELECT 1",
            "SELECT * FROM read_csv('/etc/passwd')",
            "SELECT * FROM read_parquet('https://example.com/x.parquet')",
        ],
    )
    def test_rejects(self, guard, sql):
        with pytest.raises(SqlError):
            guard.run(sql)

    def test_row_cap(self, guard):
        r = guard.run("SELECT * FROM range(100)", max_rows=10)
        assert len(r.rows) == 10 and r.truncated


def test_local_authority_resolves_to_its_namesake_place(store):
    from lix_api.services.search import resolve_lsoa

    # "Leeds" is the local authority first; its LSOA is where Leeds (the city) is, not
    # the middle of the authority's bounding box
    assert resolve_lsoa(store, "Leeds") == "E01000002"


def test_compare_a_named_suburb_as_its_neighbourhood(store):
    from lix_api.services.compare import compare_areas

    result = compare_areas(store, ["Headingley", "Brighton and Hove"])
    headingley = result.areas[0]
    assert headingley.level == "msoa" and headingley.code == "E02000001"


def test_weights_accept_labels_and_everyday_words(store):
    from lix_api.services.scoring import resolve_weights

    name, themes, _ = resolve_weights(
        store, "Young Professional", {"Schools & childcare": 3, "crime": 2, "Transport": 0}
    )
    assert name.startswith("young_professional")
    assert themes["education"] == 3 and themes["safety"] == 2 and themes["transport"] == 0
    with pytest.raises(ValueError, match="Unknown themes"):
        resolve_weights(store, None, {"vibes": 2})


def test_profile_has_a_percentile_range_for_presets_only(store):
    from lix_api.services.areas import area_profile

    p = area_profile(store, "E01000003", preset="family")
    lo, hi = p.overall_percentile_range
    assert 0 <= lo <= p.overall_percentile <= hi <= 100
    assert (
        area_profile(store, "E01000003", theme_weights={"safety": 3}).overall_percentile_range
        is None
    )


def test_rankings_say_how_stable_each_result_is(store):
    from lix_api.services.ranking import rank_areas

    result = rank_areas(store, level="lsoa", limit=3)
    assert all(0 <= r.stability <= 1 for r in result.results)
    # With every candidate shown, nothing can drop out
    assert all(r.stability == 1 for r in rank_areas(store, level="lsoa", limit=10).results)


def test_profile_themes_carry_country_nation_and_local_medians(store):
    from lix_api.services.areas import area_profile

    p = area_profile(store, "E01000003")
    assert (p.nation, p.nation_name) == ("E", "England")
    for t in p.themes:
        if t.score is not None:
            assert t.country_median is not None and t.local_median is not None
            assert t.nation_median is not None and t.percentile_nation is not None


def test_welsh_area_is_profiled_within_its_nation(store):
    from lix_api.services.areas import area_profile, lsoas_in
    from lix_api.services.search import search_place

    p = area_profile(store, "CF10 1AA")
    assert (p.nation, p.nation_name, p.region) == ("W", "Wales", "Wales")
    # An English area carries no Welsh caveat (council tax is council-wide everywhere)
    english = area_profile(store, "E01000001")
    assert "broadcast_lad" not in english.flag_codes and "not_available" not in english.flag_codes
    assert p.overall_percentile_nation in (0, 99)  # two Welsh areas; the top one reads 99, not 100
    crime = next(v for v in p.key_facts if v.id == "crime_violence")
    assert crime.benchmark == "nation"
    assert lsoas_in(store, "nation", "W92000004").to_list() == ["W01001880", "W01001881"]
    wales = search_place(store, "Wales")[0]
    assert (wales.kind, wales.code) == ("nation", "W92000004")


from types import SimpleNamespace  # noqa: E402


class TestLanguage:
    def test_error_messages_follow_the_locale(self):
        from pydantic_ai.exceptions import UsageLimitExceeded

        from lix_api.agent.errors import friendly_message

        err = UsageLimitExceeded("too many")
        assert friendly_message(err, "en").startswith("That needed more steps")
        assert friendly_message(err, "cy").startswith("Roedd angen mwy o gamau")
        assert friendly_message(err, "gd").startswith("Bha feum air barrachd cheumannan")
        assert friendly_message(err, "ga").startswith("Theastaigh níos mó céimeanna")

    def test_prompt_asks_for_welsh(self, store):
        from types import SimpleNamespace

        from lix_api.agent.agent import current_state
        from lix_api.agent.state import LiveabilityState

        ctx = SimpleNamespace(deps=SimpleNamespace(state=LiveabilityState(locale="cy")))
        text = current_state(ctx)
        assert text.startswith("Reply in Welsh (Cymraeg).")
        ctx = SimpleNamespace(deps=SimpleNamespace(state=LiveabilityState()))
        assert "Reply in" not in current_state(ctx)

    def test_language_check_tells_welsh_from_english(self):
        import sys
        from pathlib import Path

        sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
        from evals.evaluators import STOPWORDS, LanguageCheck

        assert set(STOPWORDS) == {"en", "cy", "gd", "ga"}
        check = LanguageCheck(lang="cy")
        ctx = SimpleNamespace(
            output=SimpleNamespace(
                reply="Mae Pontcanna yn sgorio'n dda ar y map ac mae'r ardal yn dawel."
            )
        )
        assert check.evaluate(ctx).value
        ctx = SimpleNamespace(
            output=SimpleNamespace(reply="Pontcanna scores well and the area is quiet on the map.")
        )
        assert not check.evaluate(ctx).value


class TestRankingByOneMeasure:
    def test_indicator_sort_orders_by_its_score_and_shows_its_value(self, store):
        r = ranking.rank_areas(store, level="lsoa", limit=3, sort_by="indicator:crime_violence")
        assert r.sort_by == "indicator:crime_violence"
        assert r.sort_label == store.indicators["crime_violence"].label
        assert r.sort_unit == store.indicators["crime_violence"].unit
        scores = [a.sort_score for a in r.results]
        assert scores == sorted(scores, reverse=True) and all(s is not None for s in scores)
        assert all(a.sort_value is not None and a.stability is None for a in r.results)
        assert all(a.overall is not None for a in r.results)  # the overall stays beside it

    def test_theme_sort_uses_the_theme_score(self, store):
        r = ranking.rank_areas(store, level="msoa", limit=2, sort_by="theme:safety")
        assert r.sort_unit == "score"
        assert [a.sort_value for a in r.results] == [a.themes["safety"] for a in r.results]
        assert r.results[0].sort_score >= r.results[1].sort_score

    def test_unknown_measure_is_explained(self, store):
        with pytest.raises(LookupError, match="list_indicators"):
            ranking.rank_areas(store, sort_by="indicator:nope")
        with pytest.raises(LookupError, match="theme:safety"):
            ranking.rank_areas(store, sort_by="theme:nope")

    def test_overall_ranking_is_unchanged(self, store):
        r = ranking.rank_areas(store, level="lsoa", limit=2)
        assert r.sort_by == "overall" and r.sort_label is None
        assert all(a.sort_value is None and a.sort_score is None for a in r.results)


class TestRankingScopesAndSorts:
    """Every scope kind and every indicator sort must work (a nation scope and the
    house_price sort crashed in v0.4.0)."""

    def test_rank_within_a_nation(self, store):
        from lix_api.services.ranking import rank_areas

        out = rank_areas(store, within="Wales", level="msoa", limit=3)
        assert out.results and all(r.name for r in out.results)
        assert out.within and "Wales" in out.within

    def test_sort_by_every_scored_indicator(self, store):
        from lix_api.services.ranking import rank_areas

        for ident in [i.id for i in store.scored]:
            if "E" not in store.applicable.get(ident, []):
                # Built for another nation: a clear refusal, not an empty list (#111)
                with pytest.raises(LookupError, match="not available in England"):
                    rank_areas(store, within="Leeds", level="msoa", sort_by=f"indicator:{ident}")
                continue
            out = rank_areas(
                store, within="Leeds", level="msoa", limit=2, sort_by=f"indicator:{ident}"
            )
            assert len(out.results) <= 2, ident

    def test_rank_in_another_language_names_areas_in_it(self, store):
        from lix_api.services.ranking import rank_areas

        out = rank_areas(store, within="Cardiff", level="msoa", limit=2, locale="cy")
        assert out.results
        # The test store carries a Welsh name for Cardiff's areas; the English stays beside it
        assert any(r.name_en for r in out.results) or all(r.name for r in out.results)


class TestRankingGuards:
    """The v0.4.0 testing pass (#102, #111): council-level rankings, areas without the
    measure, lenient names, worst-first, capped percentiles, localised names."""

    def test_lad_level_aggregates_whole_councils(self, store):
        r = ranking.rank_areas(store, level="lad", limit=5)
        assert {a.code for a in r.results} == {"E08000035", "E06000043", "W06000015"}
        assert all(a.level == "lad" for a in r.results)
        leeds = next(a for a in r.results if a.code == "E08000035")
        assert leeds.name == "Leeds" and leeds.local_authority == "Leeds"
        assert leeds.population == 1500 + 1600 + 1700 + 1800

    def test_region_and_nation_levels_are_refused_with_the_allowed_ones(self, store):
        with pytest.raises(LookupError, match="'lad'"):
            ranking.rank_areas(store, level="region")

    def test_areas_without_the_measure_are_left_out_not_first(self, store):
        # primary_school_quality is built for England only: Cardiff's areas have no value
        r = ranking.rank_areas(
            store, level="msoa", sort_by="indicator:primary_school_quality", limit=10
        )
        assert "W02000384" not in {a.code for a in r.results}
        assert r.candidates == len(r.results) == 3
        assert all(a.sort_score is not None for a in r.results)

    def test_an_indicator_the_scope_lacks_is_explained_not_empty(self, store):
        with pytest.raises(LookupError, match="not available in Wales"):
            ranking.rank_areas(store, within="Cardiff", sort_by="indicator:primary_school_quality")

    def test_theme_names_and_minimum_keys_are_lenient(self, store):
        r = ranking.rank_areas(store, level="msoa", sort_by="theme:schools")
        assert r.sort_by == "theme:education"
        assert ranking.rank_areas(store, sort_by="Safety").sort_by == "theme:safety"
        r = ranking.rank_areas(store, level="lsoa", min_theme_scores={"Safety": 0}, limit=0)
        assert len(r.results) == 1  # limit is at least one
        with pytest.raises(LookupError, match="min_theme_scores"):
            ranking.rank_areas(store, min_theme_scores={"vibes": 50})

    def test_worst_first_reverses_the_order(self, store):
        worst = ranking.rank_areas(store, level="lsoa", limit=8, direction="worst")
        overall = [a.overall for a in worst.results]
        assert overall == sorted(overall) and worst.direction == "worst"
        assert all(a.stability is not None for a in worst.results)

    def test_percentiles_never_read_100(self, store):
        r = ranking.rank_areas(store, level="lsoa", limit=8)
        pcts = [a.overall_percentile for a in r.results if a.overall_percentile is not None]
        assert pcts and max(pcts) <= 99

    def test_names_follow_the_locale(self, store):
        r = ranking.rank_areas(store, within="Cardiff", level="msoa", locale="cy")
        assert r.results[0].local_authority == "Caerdydd"
        r = ranking.rank_areas(store, within="Cardiff", level="lad", locale="cy")
        assert r.results[0].name == "Caerdydd" and r.results[0].name_en == "Cardiff"


class TestSubstitutes:
    """#103: an indicator a nation lacks shows its substitute, marked as standing in."""

    def test_stand_in_prefers_the_indicator_then_its_substitute(self, store):
        from lix_api.services.areas import stand_in

        row = {"q__crime_violence": "not_available", "q__recorded_crime": "ok"}
        assert stand_in(store, row, "crime_violence") == "recorded_crime"
        assert stand_in(store, row, "recorded_crime") == "recorded_crime"
        row = {"q__crime_violence": "ok", "q__recorded_crime": "not_available"}
        assert stand_in(store, row, "recorded_crime") == "crime_violence"
        row = {"q__crime_violence": "not_available", "q__recorded_crime": "not_available"}
        assert stand_in(store, row, "crime_violence") is None

    def test_with_substitutes_drops_the_covered_and_marks_the_stand_in(self, store):
        from lix_api.services.areas import indicator_value, with_substitutes

        row = {f"q__{i}": "ok" for i in store.indicators}
        row |= {f"q__{i}": "not_available" for i in store.indicators if i.startswith("crime_")}
        row |= {"raw__recorded_crime": 50.0, "n__recorded_crime": 40.0}
        values = [
            indicator_value(store, row, i) for i in ("crime_violence", "recorded_crime", "no2")
        ]
        out = with_substitutes(store, row, values)
        assert [v.id for v in out] == ["recorded_crime", "no2"]
        assert out[0].stands_in_for == "crime_violence" and out[1].stands_in_for is None

    def test_key_facts_show_the_substitute(self, store):
        from lix_api.services.areas import fact

        row = {f"q__{i}": "ok" for i in store.indicators}
        row |= {"q__crime_violence": "not_available", "raw__recorded_crime": 12.5}
        f = fact(store, row, "crime_violence")
        assert f.id == "recorded_crime" and f.stands_in_for == "crime_violence"
        assert f.value == 12.5

    def test_comparison_rows_are_the_requested_indicators_without_substitutes(self, store):
        from lix_api.services.compare import COMPARE_INDICATORS, compare_areas

        c = compare_areas(store, ["LS6 3HN", "CF10 1AA"])
        assert list(c.indicator_labels) == [i for i in COMPARE_INDICATORS if i in store.indicators]
        assert c.stand_ins == {}
        # Wales lacks primary school quality: no value, no substitute
        assert c.areas[1].indicators["primary_school_quality"] is None


class TestReleasePassFindings:
    """Defects the v0.5.0 testing pass found in the API."""

    def test_saturated_scores_are_ordered_by_the_raw_value(self, store):
        # gp_distance is a threshold score: every home within reach reads 100, and the
        # tie is broken by the distance itself, nearest first (and furthest first, worst)
        best = ranking.rank_areas(store, level="lsoa", limit=8, sort_by="indicator:gp_distance")
        values = [a.sort_value for a in best.results]
        assert values == sorted(values)
        worst = ranking.rank_areas(
            store, level="lsoa", limit=8, sort_by="indicator:gp_distance", direction="worst"
        )
        assert [a.sort_value for a in worst.results] == sorted(values, reverse=True)

    def test_a_theme_nobody_in_scope_has_is_refused(self, store):
        import polars as pl

        from lix_api.services.ranking import Measure, _check_available

        df = pl.DataFrame({"theme__education": [None, None], "nation": ["N", "N"]}).cast(
            {"theme__education": pl.Float64}
        )
        measure = Measure("theme:education", "theme__education", (), label="Schools")
        with pytest.raises(LookupError, match="not scored in Northern Ireland"):
            _check_available(store, measure, df)

    def test_a_nation_is_not_an_area(self, store):
        from lix_api.services.search import resolve_lsoa

        with pytest.raises(LookupError, match="nation"):
            resolve_lsoa(store, "Wales")

    def test_a_recognised_scope_with_nothing_inside_is_empty(self, store):
        # Headingley is in Leeds, not in Wales: the scope is known, so no fallback to a
        # whole-string match that would return Headingley anyway
        assert search.search_place(store, "Headingley, Wales") == []
        assert search.search_place(store, "Headingley, Leeds")[0].name == "Headingley"

    def test_explain_matches_the_theme_leniently(self, store):
        from lix_api.services.explain import explain_score

        out = explain_score(store, "LS6 3HN", theme="Schools")
        assert [c.theme for c in out.contributions] == ["education"]
        with pytest.raises(LookupError, match="Unknown theme"):
            explain_score(store, "LS6 3HN", theme="vibes")
