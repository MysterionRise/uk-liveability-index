"""#228: a reference to something larger than one small area (a neighbourhood's code, a
town, a council) is profiled as one small area inside it, while compare_areas and
rank_areas score the larger area itself. The profile and the explanation now say which
was named and give its own score, the figure the other two show."""

import pytest

from lix_api.services.areas import area_profile
from lix_api.services.compare import compare_areas
from lix_api.services.explain import explain_score
from lix_api.services.ranking import rank_areas
from lix_api.services.search import named_area, resolve_area, resolve_lsoa, search_place


@pytest.mark.parametrize(
    "ref, kind, code",
    [
        ("E02000001", "msoa", "E02000001"),  # a neighbourhood by its code
        ("Headingley", "msoa", "E02000001"),  # a suburb means the neighbourhood it sits in
        ("Brighton and Hove", "lad", "E06000043"),  # a council
    ],
)
def test_a_larger_area_is_named_with_the_score_compare_gives_it(store, ref, kind, code):
    profile = area_profile(store, ref)
    named = profile.resolved_from
    assert named and (named.kind, named.code) == (kind, code)
    # The profile itself is of one small area inside it
    inside = store.features.filter(
        store.features[{"msoa": "msoa21cd", "lad": "lad_cd"}[kind]] == code
    )
    assert profile.lsoa21cd in inside["lsoa21cd"].to_list()
    # Its own score is the one a comparison shows for the same reference
    compared = compare_areas(store, [ref, "LS6 3HN" if kind == "lad" else "Hanover"]).areas[0]
    assert (compared.level, compared.code) == (kind, code)
    assert named.overall == compared.overall
    # ... and the explanation says the same
    assert explain_score(store, ref).resolved_from == named


def test_a_neighbourhoods_score_is_the_one_a_ranking_gives_it(store):
    named = area_profile(store, "Headingley").resolved_from
    ranked = rank_areas(store, level="msoa", limit=50).results
    row = next(r for r in ranked if r.code == named.code)
    assert row.overall == named.overall


def test_the_weighting_asked_for_is_the_weighting_of_both_scores(store):
    plain = area_profile(store, "Headingley")
    family = area_profile(store, "Headingley", preset="family")
    assert family.resolved_from.overall != plain.resolved_from.overall
    compared = compare_areas(store, ["Headingley", "Hanover"], preset="family").areas[0]
    assert family.resolved_from.overall == compared.overall


def test_a_small_area_named_as_itself_has_nothing_to_add(store):
    for ref in ("LS6 3HN", "E01000001"):  # a postcode, a small area's own code
        assert area_profile(store, ref).resolved_from is None
        assert explain_score(store, ref).resolved_from is None
        assert resolve_area(store, ref)[1] is None


def test_resolve_lsoa_is_unchanged_and_named_area_is_compares_rule(store):
    for ref in ("Headingley", "E02000001", "Brighton and Hove", "LS6 3HN"):
        assert resolve_lsoa(store, ref) == resolve_area(store, ref)[0]
    suburb = search_place(store, "Headingley", limit=1)[0]
    assert named_area(store, suburb) == ("msoa", "E02000001")
    postcode = search_place(store, "LS6 3HN", limit=1)[0]
    assert named_area(store, postcode) is None


# ---------------------------------------------------------------- found by the v0.7.2 pass


def test_a_council_is_profiled_by_a_small_area_inside_it(store, monkeypatch):
    """A council with no point of its own takes its namesake place's small area ("Leeds"
    the city). The first namesake was taken wherever it was: "Croydon" was profiled as a
    village of that name in Cambridgeshire, and the card then called it one small area of
    Croydon, with the borough's score. 24 of 481 council names on the full pack."""
    from lix_api.models import Place
    from lix_api.services import areas, search
    from lix_api.services.scoring import resolve_weights, scores_for

    council = Place(kind="lad", name="Cardiff", code="W06000015")
    elsewhere = Place(kind="place", name="Cardiff", lsoa21cd="E01000007", detail="Bristol")
    at_home = Place(kind="place", name="Cardiff", lsoa21cd="W01001880", detail="Cardiff")
    inside = {"W01001880", "W01001881"}

    monkeypatch.setattr(search, "search_place", lambda *a, **k: [council, elsewhere, at_home])
    assert search.resolve_area(store, "Cardiff")[0] == "W01001880"
    # No namesake inside: the council's most populous small area, never the one elsewhere
    monkeypatch.setattr(search, "search_place", lambda *a, **k: [council, elsewhere])
    assert search.resolve_area(store, "Cardiff")[0] in inside
    # And nothing says "one small area of X" about a small area that is not in X
    _, themes, multipliers = resolve_weights(store)
    every = scores_for(store, themes, multipliers)
    assert areas.resolved_from(store, council, "E01000007", every) is None
    assert areas.resolved_from(store, council, "W01001880", every).code == "W06000015"


def test_the_larger_area_goes_by_its_own_name(store):
    """ "Edinburgh as a whole scores 63" was the score of the one neighbourhood the city's
    point falls in. The score is that neighbourhood's, so the name is too: a place name
    is kept only where the neighbourhood bears it (Headingley)."""
    by_code = {r["msoa21cd"]: r["msoa_name"] for r in store.features.iter_rows(named=True)}
    for ref in ("Headingley", "Brighton", "Ranelagh", "Dalkey", "E02000002"):
        named = area_profile(store, ref).resolved_from
        if named is None or named.kind != "msoa":
            continue
        assert named.name == by_code[named.code], (ref, named)
    # Brighton's point is in the neighbourhood Hanover: it is Hanover that scores this
    brighton = area_profile(store, "Brighton").resolved_from
    assert brighton and (brighton.kind, brighton.name) == ("msoa", "Hanover")


def test_a_ranking_scores_a_neighbourhood_whole_whatever_the_scope_cuts(store):
    """A circle that takes in one small area of a neighbourhood ranked the neighbourhood
    by that small area alone: "Guiseley East & South" 75 in a ranking near Headingley, 70
    on its own card. A row is the neighbourhood's own score, to the same two decimals a
    comparison gives it."""
    whole = {
        a.code: a.overall
        for a in compare_areas(store, ["E02000001", "E02000002", "Hanover", "Cathays"]).areas
    }
    # 300 m around Leeds 044A takes in one of Headingley's two small areas
    near = rank_areas(store, within="E01000001", radius_km=0.3, level="msoa").results
    assert [r.code for r in near] == ["E02000001"]
    assert near[0].overall == whole["E02000001"]
    # Unscoped, every neighbourhood's row is the comparison's figure, digit for digit
    for row in rank_areas(store, level="msoa", limit=50).results:
        if row.code in whole:
            assert row.overall == whole[row.code], row.code
    # The urban filter keeps or drops a neighbourhood whole: a row is never its urban part
    for urban in (True, False):
        for row in rank_areas(store, level="msoa", urban=urban, limit=50).results:
            if row.code in whole:
                assert row.overall == whole[row.code], (urban, row.code)


def test_a_small_area_has_one_score_to_one_decimal_everywhere(store):
    profile = area_profile(store, "LS6 3HN")
    compared = compare_areas(store, ["LS6 3HN", "Hanover"]).areas[0]
    assert (compared.level, compared.code) == ("lsoa", profile.lsoa21cd)
    assert compared.overall == profile.overall
    ranked = rank_areas(store, level="lsoa", limit=50).results
    assert next(r for r in ranked if r.code == profile.lsoa21cd).overall == profile.overall
