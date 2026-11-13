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

    def test_within_a_county_covers_every_council_that_shares_it(self, store):
        """ "Dublin" is the county's councils together, not the one search puts first; a
        county with one council here ranks that council under the county's name (#233)."""
        r = ranking.rank_areas(store, within="Dublin", level="lsoa", limit=10)
        assert (r.within, r.place, r.radius_km) == ("Dublin", "Dublin", None)
        assert {a.local_authority for a in r.results} == {"Dublin City", "Dún Laoghaire-Rathdown"}
        assert r.candidates == 3
        # The box takes in both councils (Rathmines in the west, Dalkey in the east)
        assert r.bbox[0] < -6.26 and r.bbox[2] > -6.10
        councils = ranking.rank_areas(store, within="Dublin", level="lad")
        assert {a.name for a in councils.results} == {"Dublin City", "Dún Laoghaire-Rathdown"}
        yorkshire = ranking.rank_areas(store, within="Yorkshire", level="lsoa", limit=10)
        assert yorkshire.within == "West Yorkshire" and yorkshire.candidates == 4

    def test_a_county_with_a_radius_is_a_place_again(self, store):
        """A radius is measured from a place: the county reading is for "within" alone."""
        r = ranking.rank_areas(store, within="Dublin", radius_km=3, level="lsoa")
        assert r.radius_km == 3 and r.within.startswith("within 3 km of")

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

    def test_a_one_measure_list_is_ordered_by_the_figure_it_shows(self, store):
        """A neighbourhood's score and value are each averaged over its small areas, so
        ordering by the score put the area with the most protected land fourth (v0.7.0
        release pass); the shown value orders the list now."""
        from lix_api.services.ranking import rank_areas

        for ident, direction in [("tree_canopy", "best"), ("noise_exposure", "worst")]:
            out = rank_areas(
                store, within="Leeds", level="msoa", limit=8,
                sort_by=f"indicator:{ident}", direction=direction,
            )  # fmt: skip
            shown = [r.sort_value for r in out.results if r.sort_value is not None]
            assert len(shown) >= 2, ident
            assert shown == sorted(shown, reverse=True), (ident, shown)

    def test_an_england_only_context_measure_says_where_it_is_built(self, store):
        from lix_api.services.ranking import rank_areas

        if "storm_overflow_spills" not in store.indicators:
            pytest.skip("the test pack predates the measure")
        with pytest.raises(ValueError, match="built for England"):
            rank_areas(store, within="Cardiff", sort_by="indicator:storm_overflow_spills")

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
            # the v0.7.1 hardening pass's bypasses
            "select length(printf('%400000000s', 'x'))",
            "select format('{:>50000000}', 'x')",
            "select bitstring('1', 50000000)",
            "select * from query('select length(rep' || 'eat(''x'', 50000000))')",
            "select * from query_table('lsoa')",
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
        assert search._council_of(store, search._area_place(rows[0], store=store)) == "waterford"


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


def test_london_prefixes_resolve_to_their_lettered_districts():
    """ "SW1" names no district: the districts are SW1A … SW1Y (#210)."""
    import polars as pl

    from lix_api.services.search import postcode_groups

    pcs = pl.DataFrame({
        "postcode_norm": ["SW1A1AA", "SW1A2AB", "SW1P3BU", "LS63HN", "W1A1AA"],
        "lat": [51.50, 51.50, 51.49, 53.82, 51.52], "lon": [-0.14, -0.13, -0.13, -1.57, -0.14],
        "lsoa21cd": ["E01004736", "E01004736", "E01004737", "E01011350", "E01004700"],
    })  # fmt: skip
    groups = postcode_groups(pcs)
    assert groups["prefix"]["SW1"]["count"] == 3 and groups["prefix"]["W1"]["count"] == 1
    assert "LS6" not in groups["prefix"]  # an unlettered district is its own group
    assert groups["district"]["SW1A"]["count"] == 2


def test_names_a_council_repeats_carry_their_code():
    import polars as pl

    from lix_api.services.search import distinct_name, repeated_area_names

    areas = pl.DataFrame({
        "level": ["msoa", "msoa", "msoa"],
        "name": ["Kilmeadan", "Kilmeadan", "Tramore"],
        "lad_nm": ["Waterford", "Waterford", "Waterford"],
    })  # fmt: skip
    features = pl.DataFrame({
        "lsoa21nm": ["Togher 001", "Togher 001", "Togher 002"],
        "lad_nm": ["Wicklow", "Wicklow", "Wicklow"],
    })  # fmt: skip
    repeated = repeated_area_names(areas, features)

    class S:
        repeated_areas = repeated

    assert (
        distinct_name(S, "msoa", "Kilmeadan", "Waterford", "I02002702") == "Kilmeadan (I02002702)"
    )
    assert distinct_name(S, "msoa", "Tramore", "Waterford", "I02002800") == "Tramore"
    assert (
        distinct_name(S, "lsoa", "Togher 001", "Wicklow", "I01013732") == "Togher 001 (I01013732)"
    )
    assert (
        distinct_name(S, "lsoa", "Tóchar 001", "Wicklow", "I01013732", "Togher 001")
        == "Tóchar 001 (I01013732)"
    )


def test_the_assistants_catalogue_speaks_the_interface_language(store, monkeypatch):
    """The assistant translated English caveats itself under cy/gd/ga, in terms that
    differed from the drafted catalogues (v0.7.0 release pass)."""
    from lix_api.services.catalogue import list_indicators

    some = next(i for i in store.indicators.values() if i.caveats)
    welsh = {"label": "Label Cymraeg", "description": "Disgrifiad", "caveats": "Cafeat"}
    i18n = {**(store.manifest.get("i18n") or {}), "cy": {"indicators": {some.id: welsh}}}
    monkeypatch.setitem(store.manifest, "i18n", i18n)
    cy = {i.id: i for i in list_indicators(store, detail=True, locale="cy")}
    assert (cy[some.id].label, cy[some.id].description, cy[some.id].caveats) == (
        "Label Cymraeg", "Disgrifiad", "Cafeat",
    )  # fmt: skip
    other = next(i for i in store.indicators.values() if i.id != some.id)
    assert cy[other.id].label == other.label  # no draft: the English
    assert {b.id for b in list_indicators(store, detail=False, locale="cy")} == set(cy)


def test_sql_bounds_generated_sequences(store):
    """`select unnest(range(100000000))` killed the API container (v0.7.0 release pass)."""
    from lix_api.services.sql import SqlError, SqlGuard

    sql = SqlGuard(store)
    with pytest.raises(SqlError, match="at most 1,000,000"):
        sql.run("select unnest(range(100000000)) as n")
    with pytest.raises(SqlError, match="whole-number bounds"):
        sql.run("select unnest(range(power(10, 8)::int)) as n")
    with pytest.raises(SqlError, match="list_resize"):
        sql.run("select list_resize([1], 100000000) as l")
    out = sql.run("select count(*) as n from range(10)")
    assert out.rows == [[10]]


# A name in double quotes, a comment between a name and its bracket, or a schema in
# front of it got each of these past the patterns on the query's text (v0.7.2 pass).
# The engine's own parser says which functions a query calls, however it is written
BUILDERS_ANYHOW = [
    ("select length(\"repeat\"('x', 10))", "repeat"),
    ("select length(repeat/**/('x', 10))", "repeat"),
    ("select length(repeat -- a note\n('x', 10))", "repeat"),
    ("select length(main.\"repeat\"('x', 10))", "repeat"),
    ("select ('x').\"repeat\"(10)", "repeat"),
    ("select \"rpad\"('x', 5, 'y')", "rpad"),
    ("select \"lpad\"(name, 3, '0') from places", "lpad"),
    ("select \"printf\"('%5s', 'x')", "printf"),
    ("select \"format\"('{:>5}', 'x')", "format"),
    ('select "list_resize"([1], 5)', "list_resize"),
    ("select \"bitstring\"('1', 5)", "bitstring"),
    ("select * from \"query\"('select 42')", "query"),
    ("select * from query/**/('select 42')", "query"),
    ("select * from \"query_table\"('lsoa')", "query_table"),
    (
        "select * from \"json_execute_serialized_sql\"(json_serialize_sql('select 42'))",
        "json_execute_serialized_sql",
    ),
    # ... and inside the statements that wrap a query
    ("summarize select \"repeat\"('x', 10) as s", "repeat"),
    ("select * from (describe select \"repeat\"('x', 10) as s)", "repeat"),
]


@pytest.mark.parametrize(("query", "name"), BUILDERS_ANYHOW)
def test_sql_refuses_a_string_builder_however_it_is_written(store, query, name):
    with pytest.raises(SqlError, match=rf"^{name}\(\) is not allowed: it can build"):
        SqlGuard(store).run(query)


@pytest.mark.parametrize(
    ("query", "problem"),
    [
        ('select len("range"(2000000)) as n', "at most 1,000,000"),
        ("select count(*) from range/**/(2000000)", "at most 1,000,000"),
        ('select count(*) from "generate_series"(1, 2000000)', "at most 1,000,000"),
        ('select count(*) from "range"(-1000000, 1000001)', "at most 1,000,000"),
        ('select len((2000000)."range"()) as n', "at most 1,000,000"),
        ('select unnest("range"(power(10, 3)::int)) as n', "whole-number bounds"),
        ('select count(*) from "range"(1 + 1)', "whole-number bounds"),
    ],
)
def test_sql_bounds_a_sequence_however_it_is_written(store, query, problem):
    with pytest.raises(SqlError, match=rf"^(range|generate_series)\(\) .*{problem}"):
        SqlGuard(store).run(query)


def test_sql_still_builds_the_sequences_it_built(store):
    guard = SqlGuard(store)
    for query, n in [
        ('select count(*) as n from "range"(10)', 10),
        ("select count(*) as n from range(-5, 5)", 10),
        ('select count(*) as n from "generate_series"(-5, 4)', 10),
        ("select count(*) as n from range/* to ten */(10)", 10),
        ("select count(*) as n from generate_series(1, 1000000)", 1_000_000),
        ("select count(*) as n from range(0, 3000000, 3)", 1_000_000),
        ("select sum(n) as n from (select unnest(range(4)) as n)", 6),
        ("select count(*) as n from unnest([1, 2, 3]) as t(n)", 3),
    ]:
        assert guard.run(query).rows == [[n]], query


# Functions that change the engine for whoever asks next, or read what others asked
ENGINE_STATE_ANYHOW = [
    "select * from enable_logging()",
    'select * from "enable_logging"()',
    "select * from enable_logging/**/()",
    "select * from enable_logging -- a note\n()",
    "select * from \"enable_logging\"(storage = 'memory', level = 'trace')",
    'summarize select * from "enable_logging"()',
    'select * from "disable_logging"()',
    'select * from "truncate_duckdb_logs"()',
    'select * from "enable_profiling"()',
    'select * from "disable_profiling"()',
    'select * from "checkpoint"()',
    'select * from "force_checkpoint"()',
    "select \"write_log\"('a line in the log')",
    'select * from "duckdb_logs"()',
    'select * from "duckdb_log_contexts"()',
    "select * from duckdb_logs",
    'select * from main."duckdb_logs"',
    # In a string it is refused too, as repeat( in a string always was: the patterns
    # read the query's text
    "select 'enable_logging(' as s",
]


@pytest.mark.parametrize("query", ENGINE_STATE_ANYHOW)
def test_sql_cannot_change_the_engine_for_the_next_caller(store, query):
    """``select * from enable_logging()`` is a SELECT, so it ran: from then on everyone's
    queries were kept in a log anyone could read, and with ``storage = 'file'`` every
    later query was a 500 and the process aborted a few queries on (v0.7.2 pass)."""
    guard = SqlGuard(store)

    def settings() -> list:
        return guard.con.execute("select name, value from duckdb_settings() order by 1").fetchall()

    before = settings()
    assert ("enable_logging", "0") in before
    with pytest.raises(SqlError, match=r"^\w+(\(\))? is not (allowed|available)"):
        guard.run(query)
    assert settings() == before
    assert guard.run("select 1 as one").rows == [[1]]


def test_sql_reads_a_name_in_a_string_as_it_always_did(store):
    """The patterns read the query's text, strings included: ``repeat(`` in a string was
    refused before the parsed names were checked, and still is. A quoted name in a string
    is no call, to the patterns or to the parser."""
    guard = SqlGuard(store)
    with pytest.raises(SqlError, match=r"repeat\(\) is not allowed"):
        guard.run("select 'repeat(' as s")
    assert guard.run("select '\"repeat\"(' as s").rows == [['"repeat"(']]


@pytest.mark.parametrize(
    "query",
    [
        "select * from duckdb_settings()",
        "select function_name from duckdb_functions() limit 1",
        "select * from pragma_table_info('lsoa')",
        "select * from duckdb_memory()",
        "select * from json_each('{\"a\": 1}')",
        "select lsoa21cd from lsoa, lateral (select * from duckdb_tables())",
        "select * from read_csv('/etc/passwd')",
    ],
)
def test_sql_calls_three_table_functions_and_no_other(store, query):
    """Eighty-odd table functions come with the engine: some switch its logging on, some
    read its memory or its settings. The tables are what is offered here, with range(),
    generate_series() and unnest() to build rows."""
    with pytest.raises(SqlError, match=r"^\w+\(\) is not available here"):
        SqlGuard(store).run(query)


def test_sql_still_describes_shows_and_summarizes(store):
    """DESCRIBE, SHOW and SUMMARIZE are statements, not functions: the catalogue is still
    there to read."""
    guard = SqlGuard(store)
    assert "lsoa21cd" in [row[0] for row in guard.run("describe lsoa").rows]
    assert {row[0] for row in guard.run("show tables").rows} >= {"lsoa", "pois", "areas"}
    described = guard.run("describe select lad_nm, count(*) as n from lsoa group by 1")
    assert [row[0] for row in described.rows] == ["lad_nm", "n"]
    assert guard.run("summarize select overall from lsoa").rows[0][0] == "overall"
    columns = guard.run(
        "select count(*) as n from information_schema.columns where table_name = 'lsoa'"
    )
    assert columns.rows[0][0] > 20


def test_sql_on_a_broken_engine_is_a_refusal_with_nothing_left_open(store, monkeypatch):
    """With logging sent to a file it could not write, the engine failed every query
    where the tables are attached to the cursor: outside the ``try``, so a 500, with
    the cursor left open. Whatever the engine raises is a refusal, and the cursor and
    the timer are let go."""
    import threading

    import duckdb

    import lix_api.services.sql as sql

    timers, closed = [], []

    class Timer(threading.Timer):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            timers.append(self)

    class Cursor:
        def __init__(self, cursor):
            self._cursor = cursor

        def __getattr__(self, name):
            return getattr(self._cursor, name)

        def register(self, name, table):
            raise duckdb.PermissionException(
                f"Failed to create view '{name}': Cannot access directory \"/app\""
            )

        def close(self):
            closed.append(self)
            self._cursor.close()

    class Connection:
        def __init__(self, con, cursor_fails=False):
            self._con, self._cursor_fails = con, cursor_fails

        def __getattr__(self, name):
            return getattr(self._con, name)

        def cursor(self):
            if self._cursor_fails:
                raise duckdb.ConnectionException("Connection Error: the database is gone")
            return Cursor(self._con.cursor())

    monkeypatch.setattr(sql.threading, "Timer", Timer)
    guard = SqlGuard(store)
    real = guard.con
    guard.con = Connection(real)
    with pytest.raises(SqlError, match="Failed to create view 'lsoa'"):
        guard.run("select 1")
    assert len(closed) == 1 and len(timers) == 1 and timers[0].finished.is_set()
    guard.con = Connection(real, cursor_fails=True)
    with pytest.raises(SqlError, match="the database is gone"):
        guard.run("select 1")
    assert len(timers) == 1  # no cursor, so no timer was started for it


def test_sql_refuses_what_it_cannot_send_or_read(store, monkeypatch):
    """Half of a surrogate pair cannot be sent to the engine as text: that was a
    TypeError, and a 500 (v0.7.2 pass). And a query the engine's parser takes but cannot
    hand back as a tree, or hands back nested deeper than can be walked, is not run
    unchecked."""
    import lix_api.services.sql as sql

    guard = SqlGuard(store)
    for query in ('select 1 as "\ud800"', "select '\udfff' as s", "select 1 \ud83d"):
        with pytest.raises(SqlError, match="^Couldn't parse the query") as refused:
            guard.run(query)
        assert "codec" not in str(refused.value) and "\\ud" not in ascii(str(refused.value))
    # 700 calls deep: within the engine's own limit of 1,000, past what Python 3.11 reads
    # back from JSON. It runs or it is refused in words; it is never a RecursionError
    deep = "select " + "abs(" * 700 + "1" + ")" * 700 + " as n"
    try:
        assert guard.run(deep).rows == [[1]]
    except SqlError as e:
        assert "nested too deeply" in str(e)
    assert guard.run("select " + "abs(" * 100 + "1" + ")" * 100 + " as n").rows == [[1]]
    monkeypatch.setattr(
        sql, "_parsed", lambda cursor, query: {"error": True, "error_message": "no tree"}
    )
    with pytest.raises(SqlError, match="^Couldn't parse the query: no tree"):
        guard.run("select 1")


def test_a_repeated_small_area_name_carries_its_code_in_one_bracket():
    from lix_api.services.search import small_area_label

    class S:
        repeated_areas = {("lsoa", "togher 001", "Wicklow")}

    label = small_area_label(S, "Togher 001", "Togher 001", "Wicklow", "I01013732", "Togher")
    assert label == "Togher 001 (Togher, I01013732)"
    assert (
        small_area_label(S, "Togher 002", None, "Wicklow", "I01013734", "Togher")
        == "Togher 002 (Togher)"
    )
    assert (
        small_area_label(S, "Togher 001", None, "Wicklow", "I01013733", None)
        == "Togher 001 (I01013733)"
    )


class TestCountyScope:
    """What a county word covers, on a store that holds only the names (#233)."""

    COUNTIES = {
        "Devon": {"East Devon", "Exeter", "Plymouth", "Torbay"},
        "County Durham": {"County Durham", "Darlington", "Hartlepool", "Stockton-on-Tees"},
        "North Yorkshire": {"North Yorkshire", "York"},
        "West Yorkshire": {"Leeds", "Bradford"},
        "East Riding of Yorkshire": {"East Riding of Yorkshire", "Kingston upon Hull"},
        "East Sussex": {"Brighton and Hove", "Lewes"},
        "West Sussex": {"Worthing"},
        "West Midlands": {"Birmingham", "Coventry"},
        "Greater Manchester": {"Manchester", "Salford"},
    }
    REGIONS = {
        "East Midlands": {"Derby", "Leicester"},
        "West Midlands": {"Birmingham", "Coventry", "Stoke-on-Trent"},
        "Yorkshire and The Humber": {"Leeds", "York", "North Lincolnshire"},
        "North East": {"Darlington"},
        "South East": {"Brighton and Hove"},
    }

    @pytest.fixture
    def names(self, monkeypatch):
        from types import SimpleNamespace

        from lix_api.models import Place
        from lix_api.services.text import fold

        # Places and areas whose whole name a bare word can be
        exact = {"durham": "place", "manchester": "lad", "york": "place"}

        def found(_store, words, limit=8, locale=None):
            kind = exact.get(fold(words))
            return [Place(kind=kind, name=words.title())] if kind else []

        monkeypatch.setattr(search, "search_place", found)
        return SimpleNamespace(
            county_names={fold(c): c for c in self.COUNTIES},
            county_councils={fold(c): set(v) for c, v in self.COUNTIES.items()},
            region_names={fold(r): r for r in self.REGIONS},
            region_councils={fold(r): set(v) for r, v in self.REGIONS.items()},
            scope_aliases={"england": ("E",)},
        )

    def test_a_countys_own_name_is_all_of_its_councils(self, names):
        assert search.county_scope(names, "devon") == ("Devon", self.COUNTIES["Devon"])
        label, councils = search.county_scope(names, "County Durham")
        assert label == "County Durham" and "Hartlepool" in councils

    def test_a_word_that_ends_several_counties_names_them_all(self, names):
        label, councils = search.county_scope(names, "Yorkshire")
        assert label == "East Riding of Yorkshire, North Yorkshire and West Yorkshire"
        assert {"York", "Leeds", "Kingston upon Hull"} <= councils
        # Not the region that carries the word: North Lincolnshire is not in Yorkshire
        assert "North Lincolnshire" not in councils
        assert search.county_scope(names, "Sussex")[0] == "East Sussex and West Sussex"

    def test_a_word_that_ends_two_regions_names_the_regions(self, names):
        label, councils = search.county_scope(names, "Midlands")
        assert label == "East Midlands and West Midlands"
        assert {"Derby", "Stoke-on-Trent"} <= councils

    @pytest.mark.parametrize(
        "words",
        [
            "Durham",  # the city: only "County Durham" is the county
            "Manchester",  # the council: only "Greater Manchester" is the county
            "West Midlands",  # a region of that name stays the region
            "England",  # a nation
            "East",  # a compass point names nothing
            "York",  # not a part of "Yorkshire"
            "Leeds",
            "",
        ],
    )
    def test_what_is_left_to_the_place_search(self, names, words):
        assert search.county_scope(names, words) is None

    def test_a_scope_after_a_comma_takes_the_countys_councils(self, names):
        assert search.named_counties(names, "Durham") == {
            "County Durham": self.COUNTIES["County Durham"]
        }
        assert search.named_counties(names, "north") == {}
