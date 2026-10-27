"""Tests for the API services on the synthetic store."""

import polars as pl
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
        city = [r for r in results if r.kind == "place"][0]
        assert (city.place_type, city.detail) == ("City", "Leeds")

    def test_typo_and_nonsense(self, store):
        assert search.search_place(store, "Headingly")[0].name == "Headingley"
        assert search.search_place(store, "Nowheresville XYZ") == []

    def test_case_and_accents_do_not_matter(self, store):
        assert search.fold("Ynys Môn") == "ynys mon"
        assert search.search_place(store, "leeds")[0].kind == "lad"
        assert search.search_place(store, "ynys mon")[0].name == "Ynys Môn"
        # A query in the other language matches the alternative name and shows it
        caerdydd = search.search_place(store, "CAERDYDD")[0]
        # The council's Welsh name matches whole, so the council comes first, as "leeds" does
        assert (caerdydd.name, caerdydd.kind, caerdydd.name_en) == ("Caerdydd", "lad", "Cardiff")

    def test_a_place_qualified_by_a_county_or_region_is_found_in_every_council(self, store):
        """ "Ranelagh, Dublin" sits in Dublin City and "Dalkey, Dublin" in Dún
        Laoghaire-Rathdown: the county names both councils; a region names its councils
        too ("Headingley, Yorkshire and The Humber") (#156)."""
        assert search.search_place(store, "Ranelagh, Dublin")[0].lsoa21cd == "I01000002"
        assert search.resolve_lsoa(store, "Dalkey, Dublin") == "I01000003"
        assert search.search_place(store, "Dalkey, Dublin City") == []
        yorkshire = search.search_place(store, "Headingley, Yorkshire and The Humber")
        assert yorkshire[0].name == "Headingley"
        assert search.search_place(store, "Headingley, South East") == []

    def test_a_nation_by_any_of_its_names_and_a_country_as_a_scope(self, store):
        """ "Republic of Ireland" is the nation, not a hamlet called Ireland; "UK" scopes
        to the UK's nations (#157)."""
        for text in ("Republic of Ireland", "ROI", "Éire", "the Republic"):
            (hit,) = search.search_place(store, text)
            assert (hit.kind, hit.code) == ("nation", "I92000000"), text
        assert search.search_place(store, "Ireland")[0].kind == "nation"
        assert search.search_place(store, "Leeds, UK")[0].kind == "lad"
        assert search.search_place(store, "Leeds, United Kingdom")[0].kind == "lad"
        assert search.search_place(store, "Leeds, Ireland") == []
        assert search.search_place(store, "Ranelagh, Republic of Ireland")[0].name == "Ranelagh"

    def test_a_council_named_with_a_comma_is_found_whole(self, store):
        """ "Bristol, City of" is a council's own name, not "Bristol" inside "City of"
        (#173)."""
        (hit,) = search.search_place(store, "Bristol, City of")
        assert (hit.kind, hit.code) == ("lad", "E06000023")
        assert search.search_place(store, "bristol, city of")[0].code == "E06000023"

    def test_one_result_per_code(self, store):
        """A place found through two of its names appears once (#173)."""
        names = [p.code for p in search.search_place(store, "Raghnallach")]
        assert names.count("p8") == 1

    def test_a_long_reference_is_refused(self, store):
        with pytest.raises(ValueError, match="200"):
            search.search_place(store, "x" * 201)

    def test_a_neighbourhood_or_council_resolves_to_an_area_inside_it(self, store):
        """A code's profile is its most populous area, not the one nearest the middle of
        its bounding box, which may lie in the next council (#170)."""
        assert search.resolve_lsoa(store, "E02000002") in ("E01000003", "E01000004")
        assert search.resolve_lsoa(store, "E02000002") == "E01000004"  # the more populous
        assert search.resolve_lsoa(store, "I06000008") == "I01000003"
        assert search.resolve_lsoa(store, "I02000001") == "I01000002"

    def test_alternative_name_matches(self, store):
        # "Londonderry" is Derry's alternative name (OpenStreetMap "Derry/Londonderry"):
        # the form typed is shown, with the main name beside it (#132)
        hit = search.search_place(store, "Londonderry")[0]
        assert (hit.kind, hit.name, hit.name_en) == ("place", "Londonderry", "Derry")
        assert search.search_place(store, "Derry")[0].name == "Derry"


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
        assert r.rows == [
            ["Brighton and Hove", 2], ["Bristol, City of", 1], ["Cardiff", 2],
            ["Dublin City", 2], ["Dún Laoghaire-Rathdown", 1], ["Leeds", 4],
        ]  # fmt: skip

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
    assert all(r.stability == 1 for r in rank_areas(store, level="lsoa", limit=20).results)


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
    assert p.overall_percentile_urban_rural is not None  # the class comparison has a figure too
    assert all(t.percentile_urban_rural is not None for t in p.themes if t.score is not None)
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
        with pytest.raises(ValueError, match="list_indicators"):
            ranking.rank_areas(store, sort_by="indicator:nope")
        with pytest.raises(ValueError, match="theme:safety"):
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
                with pytest.raises(ValueError, match="not available in England"):
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
        r = ranking.rank_areas(store, level="lad", limit=10)
        assert {a.code for a in r.results} == {
            "E08000035", "E06000043", "W06000015", "E06000023", "I06000007", "I06000008",
        }  # fmt: skip
        assert all(a.level == "lad" for a in r.results)
        leeds = next(a for a in r.results if a.code == "E08000035")
        assert leeds.name == "Leeds" and leeds.local_authority == "Leeds"
        assert leeds.population == 1500 + 1600 + 1700 + 1800

    def test_region_and_nation_levels_are_refused_with_the_allowed_ones(self, store):
        with pytest.raises(ValueError, match="'lad'"):
            ranking.rank_areas(store, level="region")

    def test_areas_without_the_measure_are_left_out_not_first(self, store):
        # primary_school_quality is built for England only: Cardiff's areas have no value
        r = ranking.rank_areas(
            store, level="msoa", sort_by="indicator:primary_school_quality", limit=10
        )
        assert "W02000384" not in {a.code for a in r.results}
        assert r.candidates == len(r.results) == 4
        assert all(a.sort_score is not None for a in r.results)

    def test_an_indicator_the_scope_lacks_is_explained_not_empty(self, store):
        with pytest.raises(ValueError, match="not available in Wales"):
            ranking.rank_areas(store, within="Cardiff", sort_by="indicator:primary_school_quality")

    def test_theme_names_and_minimum_keys_are_lenient(self, store):
        r = ranking.rank_areas(store, level="msoa", sort_by="theme:schools")
        assert r.sort_by == "theme:education"
        assert ranking.rank_areas(store, sort_by="Safety").sort_by == "theme:safety"
        r = ranking.rank_areas(store, level="lsoa", min_theme_scores={"Safety": 0}, limit=0)
        assert len(r.results) == 1  # limit is at least one
        with pytest.raises(ValueError, match="min_theme_scores"):
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
        best = ranking.rank_areas(store, level="lsoa", limit=20, sort_by="indicator:gp_distance")
        values = [a.sort_value for a in best.results]
        assert values == sorted(values)
        worst = ranking.rank_areas(
            store, level="lsoa", limit=20, sort_by="indicator:gp_distance", direction="worst"
        )
        assert [a.sort_value for a in worst.results] == sorted(values, reverse=True)

    def test_a_theme_nobody_in_scope_has_is_refused(self, store):
        import polars as pl

        from lix_api.services.ranking import Measure, _check_available

        df = pl.DataFrame({"theme__education": [None, None], "nation": ["N", "N"]}).cast(
            {"theme__education": pl.Float64}
        )
        measure = Measure("theme:education", "theme__education", (), label="Schools")
        with pytest.raises(ValueError, match="not scored in Northern Ireland"):
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


def test_rankings_name_their_scope_in_parts(store):
    around = ranking.rank_areas(store, within="Brighton", radius_km=5, level="lsoa")
    assert around.place == "Brighton" and around.radius_km == 5
    assert around.within == "within 5 km of Brighton"
    inside = ranking.rank_areas(store, within="Cardiff", level="msoa", locale="cy")
    assert inside.place == "Caerdydd" and inside.radius_km is None and inside.within == "Caerdydd"


def test_explanations_name_the_area_in_the_locale(store):
    from lix_api.services.explain import explain_score

    assert explain_score(store, "CF10 1AA", locale="cy").name.startswith("Cardiff 032A")
    assert explain_score(store, "CF10 1AA", locale="cy").preset == "balanced"


class TestQaCloseOut:
    """Findings of the v0.6.0 release QA pass, in the services."""

    def test_refusals_speak_the_interface_language(self):
        from lix_api.agent.limits import Limits

        limits = Limits(session_turns=1, daily_budget=0.0)
        assert limits.admit(1, "cy") is None
        assert limits.admit(2, "cy").startswith("Mae'r sgwrs hon wedi cyrraedd ei therfyn o 1")
        assert limits.admit(2, "ga").startswith("Tá teorainn 1 ceist")
        assert limits.admit(2, "xx").startswith("This conversation has reached its limit of 1")
        spent = Limits(daily_budget=0.5)
        spent.spend(0.6)
        assert spent.admit(1, "gd").startswith("Chleachd an cuidiche")

    def test_an_irish_area_compares_within_ireland_and_says_so(self, store):
        """The headline percentile and band of an Irish area are within Ireland
        (headline_within in the manifest); an English area keeps the build-wide one (#147)."""
        dublin = areas.area_profile(store, "I01000001")
        assert dublin.percentile_within == "nation"
        assert dublin.overall_percentile == dublin.overall_percentile_nation
        assert dublin.overall_percentile_all is not None
        assert dublin.themes[0].percentile == dublin.themes[0].percentile_nation
        leeds = areas.area_profile(store, "E01000001")
        assert leeds.percentile_within == "all"
        assert leeds.overall_percentile == leeds.overall_percentile_all
        ranked = ranking.rank_areas(store, level="lsoa", limit=20)
        assert {r.percentile_within for r in ranked.results} == {"all", "nation"}

    def test_flags_and_the_nation_name_follow_the_locale(self, store):
        """An Irish area's theme-gap flag names its themes in the interface language, and
        the nation's name is localised (#176)."""
        en = areas.area_profile(store, "I01000001", locale="en")
        assert any("Schools" in f or "schools" in f.lower() for f in en.flags)
        ga = areas.area_profile(store, "I01000001", locale="ga")
        assert ga.nation_name == "Éire"
        assert any("Foilsíonn an náisiún seo" in f for f in ga.flags)

    def test_explanations_list_only_the_caveats_of_indicators_the_nation_has(self, store):
        from lix_api.services.explain import explain_score

        dublin = explain_score(store, "I01000001")
        assert not any("Ofsted" in c for c in dublin.caveats)
        assert dublin.percentile_within == "nation"

    def test_well_run_pubs_are_found_through_their_own_tree(self, store):
        from lix_api.services.pois import nearest_pois

        out = nearest_pois(store, "well_run_pub", "LS6 3HN", max_km=20, limit=10)
        assert len(out.pois) == 2 and all(p.detail["well_run"] for p in out.pois)
        assert "well_run_pub" in store.poi_trees

    def test_sql_takes_a_trailing_comment_and_refuses_pragma(self, store):
        from lix_api.services.sql import SqlError, SqlGuard

        guard = SqlGuard(store)
        assert guard.run("select 1 as one -- a note").rows == [[1]]
        with pytest.raises(SqlError, match="SELECT"):
            guard.run("PRAGMA table_info('lsoa')")
        wide = guard.run("select * from lsoa")
        assert wide.rows and len(wide.rows) * len(wide.columns) <= 200_000

    def test_sql_cuts_wide_cells_and_refuses_string_builders(self, store, monkeypatch):
        import lix_api.services.sql as sql
        from lix_api.services.sql import MAX_CELL_CHARS, SqlError, SqlGuard

        guard = SqlGuard(store)
        # repeat('x', 1e9) once took the API down: DuckDB's memory limit misses a scalar
        for q in (
            "select repeat('x', 10)",
            "select RPAD('x', 5, 'y')",
            "select lpad(name, 3, '0') from places",
        ):
            with pytest.raises(SqlError, match=r"\w+\(\) is not allowed"):
                guard.run(q)
        # Text and nested cells are cut in SQL before any value reaches Python
        q = (
            "select string_agg(lsoa21cd, '') as s, [1, 2] as "
            + '"a ""list"""'
            + " from lsoa, range(200)"
        )
        long = guard.run(q)
        assert long.columns == ["s", 'a "list"']
        assert long.rows[0][0].endswith("…") and len(long.rows[0][0]) == MAX_CELL_CHARS + 1
        assert long.rows[0][1] == "[1, 2]"
        assert guard.run("select 'short' as s, 1.5::DOUBLE as f").rows == [["short", 1.5]]
        # The reply's character cap stops the fetch and says so
        monkeypatch.setattr(sql, "MAX_CHARS", 100)
        capped = guard.run("select lsoa21cd from lsoa, range(10) order by 1")
        assert capped.truncated and 11 <= len(capped.rows) <= 13

    def test_indicator_listing_matches_a_theme_leniently(self, store):
        from lix_api.services.catalogue import list_indicators

        assert {i.theme for i in list_indicators(store, "Schools", detail=False)} == {"education"}
        with pytest.raises(LookupError):
            list_indicators(store, "vibes", detail=False)


class TestRepeatedAreaNames:
    def test_a_name_repeated_within_a_council_shows_its_code(self):
        """Waterford has two Kilmeadan divisions (#194): the council alone cannot tell
        them apart, so their results carry the code; a name repeated across councils
        does not need it."""
        from types import SimpleNamespace

        kilmeadan, newtown = ["Kilmeadan"] * 2, ["Newtown"] * 2
        areas = pl.DataFrame(
            {
                "level": ["msoa"] * 4 + ["lsoa"] * 2,
                "name": kilmeadan + newtown + ["Kilmeadan 001"] * 2,
                "lad_nm": ["Waterford", "Waterford", "Carlow", "Wexford", "Waterford", "Waterford"],
                "code": [f"I0200000{i}" for i in range(1, 5)] + ["I01000001", "I01000002"],
                "bbox_w": [-7.2] * 6, "bbox_s": [52.2] * 6,
                "bbox_e": [-7.1] * 6, "bbox_n": [52.3] * 6,
            }
        )  # fmt: skip
        repeated = search.repeated_area_names(areas)
        assert repeated == {
            ("msoa", "kilmeadan", "Waterford"),
            ("lsoa", "kilmeadan 001", "Waterford"),
        }
        store = SimpleNamespace(repeated_areas=repeated)
        rows = [areas.row(i, named=True) for i in range(4)]
        assert search._area_place(rows[0], store=store).detail == "Waterford (I02000001)"
        assert search._area_place(rows[1], store=store).detail == "Waterford (I02000002)"
        assert search._area_place(rows[2], store=store).detail == "Carlow"
        # The council scope still reads through the suffix
        assert search._council_of(search._area_place(rows[0], store=store)) == "waterford"


class TestSearchRemainder:
    """#193: postcode districts and sectors, short names inside long queries, a nation as
    a point, the same area twice in a comparison."""

    def test_a_district_or_sector_resolves_to_a_neighbourhood_inside_it(self, store):
        (district,) = search.search_place(store, "ls6")
        assert (district.kind, district.name, district.lsoa21cd) == (
            "postcode_district",
            "LS6",
            "E01000001",
        )
        assert district.detail == "1 postcodes" and district.bbox is not None
        (sector,) = search.search_place(store, "LS6 3")
        assert (sector.kind, sector.name, sector.code) == ("postcode_sector", "LS6 3", "LS63")
        assert search.search_place(store, "ZZ9") == []  # no such district in the pack
        assert search.resolve_lsoa(store, "LS7") == "E01000003"

    def test_a_short_name_does_not_match_inside_a_long_query(self, store):
        # "Leeds" (five letters) scores against anything that contains it; a whole phrase
        # is not Leeds
        assert "Leeds" not in [
            p.name for p in search.search_place(store, "Cathair Phort Láirge Leeds")
        ]
        assert search.search_place(store, "leeds")[0].name == "Leeds"
        assert search.search_place(store, "Leds")[0].name == "Leeds"  # a typo still finds it

    def test_a_nation_is_no_point(self, store):
        with pytest.raises(LookupError, match="not an area"):
            search.resolve_point(store, "England")
        with pytest.raises(LookupError, match="not an area"):
            pois.nearest_pois(store, "pub", "Ireland")

    def test_compare_dedupes_its_inputs(self, store):
        c = compare.compare_areas(store, ["LS6 3HN", "ls6 3hn", "Brighton and Hove", " LS6 3HN "])
        assert len(c.areas) == 2
        # The same area by another reference (its code) is one area too
        assert len(compare.compare_areas(store, ["LS6 3HN", "E01000001", "BN2 9QA"]).areas) == 2
        with pytest.raises(ValueError, match="different"):
            compare.compare_areas(store, ["LS6 3HN", "ls6 3hn"])
        with pytest.raises(ValueError, match="different"):
            compare.compare_areas(store, ["LS6 3HN", "E01000001"])
