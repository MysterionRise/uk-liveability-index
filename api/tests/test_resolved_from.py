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
    assert row.overall == pytest.approx(named.overall, abs=0.05)


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
