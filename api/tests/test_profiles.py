"""A weighting profile from a description of the user: the rules engine, the checks on
a model's draft, the engine choice and the model path."""

import json
import logging
import os

import pytest
from pydantic import ValidationError
from pydantic_ai.messages import ModelResponse, TextPart, ToolCallPart
from pydantic_ai.models.function import FunctionModel

from lix_api.agent.messages import message
from lix_api.models import ProfileDraft, WeightProfile
from lix_api.services import profiles
from lix_api.services.profiles import _from_draft, engine_choice, profile_by_rules, profile_run
from lix_core.config import load_label_catalogues

OWNER = "I'm a young professional with one child going into primary school and I have a sun deficit"
LOCALES = ["en", "cy", "gd", "ga"]


@pytest.fixture
def translated(store, monkeypatch):
    """The store with the label catalogues of config/i18n, as the pipeline writes them
    into a pack's manifest. The test pack carries next to none, and the Welsh, Gaelic
    and Irish labels are what makes a note run out of room."""
    catalogues = {
        locale: {k: v for k, v in labels.items() if k != "_meta"}
        for locale, labels in load_label_catalogues().items()
    }
    monkeypatch.setitem(store.manifest, "i18n", catalogues)
    return store


class TestRules:
    def test_the_owners_example(self, store):
        p = profile_by_rules(store, OWNER, "en", "free_model")
        assert p.preset == "young_professional"
        assert p.theme_weights["education"] == 1.25  # 0.25 + the primary school rule
        assert p.theme_weights["climate"] >= 1.25  # 0.25 + the sun deficit rule
        assert p.indicator_weights["primary_school_quality"] == 2
        assert p.indicator_weights["sunshine"] == 2
        assert p.name.startswith("Young professional") and len(p.name) <= 40
        assert not any(ch.isdigit() for ch in p.rationale)
        assert p.engine == "rules" and p.engine_reason == "free_model"

    @pytest.mark.parametrize(
        ("description", "preset", "themes", "indicators"),
        [
            ("Retired couple, my wife has asthma, we want somewhere quiet", "retiree",
             {"environment": 2.5}, {"no2": 2, "pm25": 2, "noise_exposure": 2}),
            ("WFH developer, no kids, love pubs and live gigs", "balanced",
             {"education": 0, "transport": 0.5, "amenities": 2},
             {"gigabit_broadband": 2, "well_run_pubs": 2, "culture": 2, "rail_service": 0.5}),
            ("Nurse on night shifts, no car, need a GP and a supermarket close by", "balanced",
             {"transport": 2, "health": 2, "environment": 1.5},
             {"noise_exposure": 2, "gp_distance": 2, "supermarket_distance": 2}),
            ("First-time buyer on a tight budget commuting by train into Leeds", "commuter",
             {"housing": 2.5}, {"house_price": 2, "rail_service": 2}),
        ],
    )  # fmt: skip
    def test_descriptions(self, store, description, preset, themes, indicators):
        p = profile_by_rules(store, description, "en")
        assert p.preset == preset
        for theme, value in themes.items():
            assert p.theme_weights.get(theme) == value, (theme, p.theme_weights)
        for ind, value in indicators.items():
            assert p.indicator_weights.get(ind) == value, (ind, p.indicator_weights)

    def test_rationale_in_the_locale_and_the_plain_case(self, store):
        assert profile_by_rules(store, "asthma", "cy").rationale.startswith("Yn dechrau o")
        plain = profile_by_rules(store, "xyzzy plugh", "en")
        assert plain.preset == store.default_preset and plain.theme_weights == {}
        assert plain.rationale.startswith("Nothing in the description matched")

    @pytest.mark.parametrize(
        "description",
        [
            "a couple with two kids who cycle everywhere and want a pub nearby",
            "we have a child starting school next year",
            "three children, so parks and a good primary matter most",
        ],
    )
    def test_a_household_with_children_starts_from_the_family_preset(self, store, description):
        assert profile_by_rules(store, description, "en").preset == "family"

    @pytest.mark.parametrize(
        "description",
        [
            "We're retiring and want somewhere quiet and safe",
            "I'm about to retire, near the sea please",
            "two pensioners who want a GP close by",
        ],
    )
    def test_retiring_starts_from_the_retiree_preset(self, store, description):
        # "retired" and "retirement" did; "retiring", "retire" and "pensioners" started
        # from Balanced (v0.7.1 eval rerun, retiree_devon)
        assert profile_by_rules(store, description, "en").preset == "retiree"


class TestLanguages:
    """#161: each language's words apply in its own interface, in their mutated forms;
    a negator turns a plain word around within its clause; a rationale is cut at a
    clause, never mid-phrase, and reads singular when one thing is named."""

    def test_welsh_phrases_with_mutations(self, store):
        p = profile_by_rules(store, "gweithio o gartref, dim plant, tafarndai da a thren", "cy")
        assert p.indicator_weights["gigabit_broadband"] == 2  # working from home
        assert p.theme_weights["education"] == 0  # no children
        assert p.indicator_weights["well_run_pubs"] == 2  # tafarndai
        assert p.indicator_weights["rail_service"] == 2  # "thren": trên after "a"

    def test_gaelic_and_irish_phrases(self, store):
        gd = profile_by_rules(store, "ag obair bhon taigh, gun chlann", "gd")
        assert gd.indicator_weights["gigabit_broadband"] == 2 and gd.theme_weights["education"] == 0
        ga = profile_by_rules(store, "ag obair ón mbaile, gan pháistí, torann", "ga")
        assert ga.indicator_weights["gigabit_broadband"] == 2 and ga.theme_weights["education"] == 0
        assert ga.indicator_weights["noise_exposure"] == 2
        assert ga.rationale and not ga.rationale.startswith("Starts from")
        # Eclipsed and lenited forms of a word list entry match too
        assert (
            profile_by_rules(store, "an-chóngarach don dtraein", "ga").indicator_weights[
                "rail_service"
            ]
            == 2
        )

    def test_a_language_s_words_fire_only_in_its_interface(self, store):
        # Welsh "ci" (a dog) must not read an English "CI pipeline" as a dog walker
        en = profile_by_rules(store, "I run the CI pipeline for a living", "en")
        assert "green_space_access" not in en.indicator_weights
        cy = profile_by_rules(store, "mae ci gyda ni", "cy")
        assert cy.indicator_weights["green_space_access"] == 2

    def test_a_negator_turns_a_plain_word_around_within_its_clause(self, store):
        p = profile_by_rules(store, "not bothered about schools, but safety matters", "en")
        assert p.theme_weights["education"] == 0  # 1 - 1
        assert p.theme_weights["safety"] == 2  # the negator stops at the comma
        q = profile_by_rules(store, "no need for broadband", "en")
        assert q.indicator_weights["gigabit_broadband"] == 0.5
        assert "Broadband" not in q.name
        assert profile_by_rules(store, "dim ots am ysgolion", "cy").theme_weights["education"] == 0

    def test_singular_templates_and_clause_cuts(self, store):
        one = profile_by_rules(store, "asthma", "en").rationale
        assert "Environment & health counts more" in one or "counts more" in one
        assert (
            profile_by_rules(store, "safety and schools", "en").rationale.count("count more") == 1
        )
        assert profiles._cut(
            "Starts from Balanced; safety counts more, and broadband weighs more", 50
        ) == ("Starts from Balanced; safety counts more")
        assert not profiles._cut("one two three and four five six", 20).endswith("and")
        # A long Irish rationale names fewer measures rather than stopping mid-phrase
        ga = profile_by_rules(
            store,
            "ag obair ón mbaile, torann, dochtúir, cógaslann, ollmhargadh, traein, leathanbhanda",
            "ga",
        ).rationale
        assert len(ga) <= 200 and ga.endswith(".")


class TestDraft:
    def test_a_draft_is_checked_against_the_catalogue(self, store):
        draft = ProfileDraft(
            preset="Families",
            theme_weights={"crime": 5, "vibes": 1},
            indicator_weights={"sunshine": 9, "nope": 1, "Broadband": 1.5},
            name="x" * 60,
            rationale="Scores 72 here, which is good.",
        )
        p = _from_draft(store, draft, "en", "model", description="sunshine and broadband")
        assert p.preset == "family"
        assert p.theme_weights == {"safety": 3}
        assert p.indicator_weights == {"sunshine": 3, "gigabit_broadband": 1.5}
        assert len(p.name) <= 40
        assert "72" not in p.rationale and p.rationale  # a rationale with numbers is replaced

    def test_bounds(self):
        with pytest.raises(ValidationError):
            WeightProfile(
                preset="balanced",
                theme_weights={"safety": 4},
                name="x",
                rationale="y",
                engine="rules",
            )
        with pytest.raises(ValidationError):
            WeightProfile(preset="balanced", name="x" * 41, rationale="y", engine="rules")


class TestEngine:
    def test_engine_choice(self, monkeypatch):
        from lix_api.agent import models as agent_models
        from lix_api.agent.limits import limits

        monkeypatch.setenv("LIX_MODEL", "test")
        assert engine_choice() == ("rules", "free_model")
        monkeypatch.setenv("LIX_MODEL", "openrouter:anthropic/claude-haiku-5.5")
        monkeypatch.setattr(agent_models, "MODEL_PROBLEM", "no key")
        assert engine_choice() == ("rules", "no_key")
        monkeypatch.setattr(agent_models, "MODEL_PROBLEM", None)
        monkeypatch.setattr(limits, "spent", 99.0)
        monkeypatch.setattr(limits, "daily_budget", 1.0)
        assert engine_choice() == ("rules", "budget")
        monkeypatch.setattr(limits, "spent", 0.0)
        assert engine_choice() == ("model", None)

    @pytest.mark.anyio
    async def test_the_model_path_and_its_fallback(self, store, monkeypatch):
        def answer(messages, info):
            return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name, {
                "preset": "retiree", "theme_weights": {"climate": 2},
                "indicator_weights": {"sunshine": 3}, "name": "Sun seeker",
                "rationale": "Sunshine and mild winters matter most.",
            })])  # fmt: skip

        profile, result = await profile_run(store, "sun please", "en", model=FunctionModel(answer))
        assert profile.engine == "model" and result is not None
        assert profile.preset == "retiree" and profile.theme_weights == {"climate": 2}
        assert profile.indicator_weights == {"sunshine": 3} and profile.name == "Sun seeker"
        log = os.path.join(os.environ["LIX_DATA_DIR"], "logs", "agent.jsonl")
        last = json.loads(open(log).read().splitlines()[-1])
        assert last["kind"] == "profile"

        def babble(messages, info):
            return ModelResponse(parts=[TextPart("I'd rather not.")])

        profile, result = await profile_run(store, OWNER, "en", model=FunctionModel(babble))
        assert profile.engine == "rules" and profile.engine_reason == "model_error"
        assert result is None and profile.preset == "young_professional"


def test_match_indicator_knows_everyday_words(store):
    from lix_api.services.scoring import match_indicator

    assert match_indicator(store, "broadband") == "gigabit_broadband"
    assert match_indicator(store, "Sunshine") == "sunshine"
    assert match_indicator(store, "vibes") is None
    assert profiles.scored_ids(store) >= {"sunshine", "gigabit_broadband"}


class TestNegationPolarity:
    """The v0.6.3 release pass: a negator's meaning depends on what it touches. Aversion
    to a problem ("hate noise", "no crime") raises its weight; aversion to an asset ("no
    pubs") and indifference ("schools don't matter") lower it; a conjunction ends the
    negator's reach; a language's "or" is not the English "no"."""

    @pytest.mark.parametrize(
        ("description", "locale", "indicator", "multiplier"),
        [
            ("I hate noise", "en", "noise_exposure", 2),
            ("I want no noise", "en", "noise_exposure", 2),
            ("somewhere without flooding", "en", "flood_risk", 2),
            ("I don't mind noise", "en", "noise_exposure", 0.5),
            ("noise doesn't matter to me", "en", "noise_exposure", 0.5),
            ("I don't need it quiet", "en", "noise_exposure", 0.5),
            ("no need for broadband", "en", "gigabit_broadband", 0.5),
            ("dw i'n casáu sŵn", "cy", "noise_exposure", 2),
            ("gan torann", "ga", "noise_exposure", 2),
            ("gun fhuaim", "gd", "noise_exposure", 2),
        ],
    )
    def test_aversion_and_indifference(self, store, description, locale, indicator, multiplier):
        assert (
            profile_by_rules(store, description, locale).indicator_weights[indicator] == multiplier
        )

    def test_a_problem_theme_word_under_aversion_counts_more(self, store):
        assert (
            profile_by_rules(store, "I want somewhere with no crime", "en").theme_weights["safety"]
            == 2
        )
        assert (
            profile_by_rules(store, "avoid areas with high crime", "en").theme_weights["safety"]
            == 2
        )
        assert profile_by_rules(store, "dim trosedd", "cy").theme_weights["safety"] == 2
        assert (
            profile_by_rules(store, "schools are not important", "en").theme_weights["education"]
            == 0
        )
        assert (
            profile_by_rules(store, "I am indifferent to schools", "en").theme_weights["education"]
            == 0
        )

    def test_a_conjunction_ends_the_negator(self, store):
        p = profile_by_rules(store, "No pubs but good schools", "en")
        assert p.theme_weights["education"] == 2 and p.indicator_weights["well_run_pubs"] == 0.5
        assert (
            profile_by_rules(store, "I have no car and good schools matter", "en").theme_weights[
                "education"
            ]
            == 2
        )
        assert (
            profile_by_rules(store, "dim tafarndai ond ysgolion da", "cy").theme_weights[
                "education"
            ]
            == 2
        )

    def test_or_is_not_no(self, store):
        assert profile_by_rules(store, "pobal nó scoileanna", "ga").theme_weights["education"] == 2
        assert profile_by_rules(store, "grian no sgoiltean", "gd").theme_weights["education"] == 2

    def test_english_words_of_another_language_and_colliding_mutations(self, store):
        # Gaelic and Irish "air" is "on", not the English air; Welsh "chi" is you, not a dog
        assert not profile_by_rules(store, "tha gaol agam air a' ghrian", "gd").theme_weights.get(
            "environment"
        )
        assert (
            "green_space_access"
            not in profile_by_rules(store, "diolch yn fawr i chi", "cy").indicator_weights
        )
        assert (
            "green_space_access"
            not in profile_by_rules(store, "gwn i ddim", "cy").indicator_weights
        )
        assert profile_by_rules(store, "tawelwch", "cy").indicator_weights["noise_exposure"] == 2
        assert profile_by_rules(store, "ciúnas", "ga").indicator_weights["noise_exposure"] == 2

    def test_a_word_counts_once_however_many_lists_name_it(self, store):
        assert (
            profile_by_rules(store, "school", "en").theme_weights["education"]
            == profile_by_rules(store, "schools", "en").theme_weights["education"]
        )
        assert profile_by_rules(store, "diogelwch", "cy").theme_weights["safety"] == 2

    def test_zero_width_marks_inside_a_word(self, store):
        assert profile_by_rules(store, "sch​ools", "en").theme_weights["education"] == 2


class TestNamesAndRationales:
    def test_a_name_never_cuts_a_label(self, store):
        labels = profiles._labels(store, "en")
        everything = [("indicator", i) for i in store.indicators]
        for preset in store.presets:
            name = profiles._name(labels, preset, everything)
            assert len(name) <= 40
            for part in name.split(" · ")[1:]:
                assert part in labels["indicators"].values(), part

    def test_lowered_measures_do_not_weigh_more(self, store):
        p = profile_by_rules(store, "I work from home", "en")
        assert p.indicator_weights["rail_service"] == 0.5
        # Turned down: named among what weighs less (#229), never among what weighs more
        more, _, less = p.rationale.partition("counts less.")
        assert "Rail service" not in more and "weighs more" in more
        assert "Rail service" in less and "weighs less within its theme" in less
        # "fibre" names the measure alone ("broadband" is the transport theme as well)
        only_down = profile_by_rules(store, "I don't care about fibre", "en").rationale
        assert "weighs less within its theme" in only_down

    def test_what_counts_less_is_said_after_what_counts_more(self, store):
        # #229: a description that turned one theme up and another down named only the first
        r = profile_by_rules(store, "pubs and gigs, not bothered about schools", "en").rationale
        assert "counts more" in r and r.endswith("Schools & childcare counts less.")
        assert len(r) <= profiles.RATIONALE_MAX
        cy = profile_by_rules(store, "dim ots am ysgolion, tafarndai da", "cy").rationale
        assert "yn pwyso mwy" in cy and "yn cyfrif llai." in cy
        gd = profile_by_rules(store, "ag obair bhon taigh, gun chlann", "gd").rationale
        assert "nas truime" in gd and "a' cunntadh nas lugha." in gd
        ga = profile_by_rules(store, "ag obair ón mbaile, gan pháistí", "ga").rationale
        assert "níos mó meáchain" in ga and ga.endswith("níos lú.")

    @pytest.mark.parametrize("locale", LOCALES)
    def test_single_measures_give_way_before_a_theme_that_counts_less(
        self, translated, monkeypatch, locale
    ):
        """When the 200 characters run out, names are given room in this order: one
        theme that counts more, one that counts less, the other themes, then single
        measures (one that weighs more, those that weigh less, the rest). It used to be
        the other way round for what counts less (its sentence was added only if room
        was left), and "pubs and gigs, no kids" never said in Welsh, Gaelic or Irish that
        schools were switched off: a theme turned down changes the ranking more than the
        third measure turned up, so the note must say it (v0.7.2 test pass). A name is
        left out only when it would not fit beside what outranks it."""
        labels = profiles._labels(translated, locale)
        base = dict(translated.presets["balanced"].themes)
        up, down = ["safety", "health", "transport"], ["education", "amenities"]
        more, less = ["gigabit_broadband", "gp_distance"], ["well_run_pubs", "rail_service"]

        def label(ident: str) -> str:
            return labels["themes" if ident in up + down else "indicators"][ident]

        def note(ids: list[str], room: int = profiles.RATIONALE_MAX) -> str:
            themes = {t: 2.0 if t in up else 0.0 for t in ids if t in up + down}
            measures = {i: 2.0 if i in more else 0.5 for i in ids if i in more + less}
            with monkeypatch.context() as m:
                m.setattr(profiles, "RATIONALE_MAX", room)
                return profiles._rationale(translated, locale, "balanced", themes, base, measures)

        sizes = [
            (a, b, c, d) for a in (1, 2, 3) for b in (0, 1, 2) for c in (0, 1, 2) for d in (0, 1, 2)
        ]
        for n_up, n_down, n_more, n_less in sizes:
            lists = {"up": up[:n_up], "down": down[:n_down]}
            lists |= {"more": more[:n_more], "less": less[:n_less]}
            # Each list's names, in the order they are given room
            ranked = [("up", 0, 1), ("down", 0, 1), ("up", 1, 9), ("down", 1, 9)]
            ranked += [("more", 0, 1), ("less", 0, 9), ("more", 1, 9)]
            r = note([i for key, a, b in ranked for i in lists[key][a:b]])
            assert len(r) <= profiles.RATIONALE_MAX and r.endswith("."), r
            # A theme turned down is always said
            assert not n_down or label(down[0]) in r, r
            said: list[str] = []
            ended: set[str] = set()
            for key, a, b in ranked:
                for ident in lists[key][a:b]:
                    if key in ended:
                        # A list loses names from its end
                        assert label(ident) not in r, r
                    elif label(ident) in r:
                        said.append(ident)
                    else:
                        # Left out: it would not have fitted beside what outranks it
                        whole = note([*said, ident], room=10_000)
                        assert len(whole) > profiles.RATIONALE_MAX, (r, whole)
                        ended.add(key)

    def test_a_name_gives_way_only_for_one_that_then_fits(self, store, monkeypatch):
        # Working from home, no children, noise: broadband and noise weigh more, rail
        # less. With room for all but the sentence about rail, the second measure that
        # weighs more stays: dropping it would not have made room for that sentence
        themes = {"transport": 0.5, "education": 0.0}
        base = dict(store.presets["balanced"].themes)

        def note(measures: dict[str, float]) -> str:
            return profiles._rationale(store, "en", "balanced", themes, base, measures)

        without_rail = note({"gigabit_broadband": 2.0, "noise_exposure": 2.0})
        assert without_rail.endswith("Transport & connectivity, Schools & childcare count less.")
        without_noise = note({"gigabit_broadband": 2.0, "rail_service": 0.5})
        assert len(without_noise) > len(without_rail)
        monkeypatch.setattr(profiles, "RATIONALE_MAX", len(without_rail))
        every = {"gigabit_broadband": 2.0, "rail_service": 0.5, "noise_exposure": 2.0}
        assert note(every) == without_rail

    @pytest.mark.parametrize("locale", ["cy", "gd", "ga"])
    def test_one_measure_reads_singular_beside_one_theme(self, store, locale):
        # Welsh, Gaelic and Irish have one form for a single measure, whatever the number
        # of themes: with one theme as well, the plural form was picked ("eu themâu")
        base = dict(store.presets["balanced"].themes)
        labels = profiles._labels(store, locale)
        r = profiles._rationale(
            store, locale, "balanced", {"amenities": 2.0}, base, {"well_run_pubs": 2.0}
        )
        assert r == message(
            "profile_rationale_one_indicator",
            locale,
            preset=labels["presets"]["balanced"],
            themes=labels["themes"]["amenities"],
            indicators=labels["indicators"]["well_run_pubs"],
        )

    @pytest.mark.parametrize("locale", LOCALES)
    def test_pubs_and_gigs_no_kids_says_schools_count_less(self, translated, locale):
        # The weights this description comes to, by the model and by the rules alike
        themes = {"amenities": 2.0, "education": 0.0}
        measures = {"well_run_pubs": 2.0, "culture": 2.0}
        labels = profiles._labels(translated, locale)
        if locale != "en":  # the catalogue is in: the labels are this language's
            assert labels["themes"] != profiles._labels(translated, "en")["themes"]
        base = dict(translated.presets["balanced"].themes)
        r = profiles._rationale(translated, locale, "balanced", themes, base, measures)
        more = message(
            "profile_rationale_themes_one_theme",
            locale,
            fallback="profile_rationale_themes",
            preset=labels["presets"]["balanced"],
            themes=labels["themes"]["amenities"],
        )
        less = message(
            "profile_less_note_one",
            locale,
            fallback="profile_less_note",
            themes=labels["themes"]["education"],
        )
        assert r.startswith(more.rstrip(".")), r  # Amenities still counts more
        assert r.endswith(less), r  # and Schools & childcare counts less
        assert len(r) <= profiles.RATIONALE_MAX
        said = [labels["indicators"][i] in r for i in measures]
        assert said == sorted(said, reverse=True)  # the measures go from the end
        if locale == "en":  # it all fits: nothing is dropped
            assert all(said) and len(r) == 188
        # By the rules, and as the model path gives it when a draft carries no weights
        assert profile_by_rules(translated, "pubs and gigs, no kids", locale).rationale == r

    def test_a_theme_that_counts_less_is_said_beside_a_context_measure(self, store):
        # The note about measures the cards show keeps its room (#223); what counts less
        # is said before any single measure that weighs more
        r = profile_by_rules(store, "pubs and gigs, no kids, near a beach", "en").rationale
        assert r.startswith("Starts from Balanced; Amenities & nightlife counts more")
        assert "Schools & childcare counts less." in r
        assert r.endswith("Distance to a bathing water is shown on the cards, not scored.")
        assert len(r) <= profiles.RATIONALE_MAX

    def test_a_long_list_of_what_counts_less_loses_names_not_its_ending(self, translated):
        # Nothing counts more here, so the first sentence is the one that names what counts
        # less: it is shortened by a name, not cut in the middle of one
        ids = ["safety", "environment", "health", "education", "transport", "amenities"]
        base = dict(translated.presets["balanced"].themes)
        labels = profiles._labels(translated, "gd")
        r = profiles._rationale(translated, "gd", "balanced", dict.fromkeys(ids, 0.0), base, {})
        assert len(r) <= profiles.RATIONALE_MAX
        kept = [labels["themes"][t] for t in ids if labels["themes"][t] in r]
        assert 1 < len(kept) < len(ids)
        assert r == message(
            "profile_rationale_less",
            "gd",
            preset=labels["presets"]["balanced"],
            themes=", ".join(kept),
        )

    def test_not_wanting_one_measure_leaves_its_theme_alone(self, store):
        # "broadband", "pubs" and "gp" are words for a theme as well as for one measure:
        # "no need for broadband" used to set Transport & connectivity to 0
        p = profile_by_rules(store, "no need for broadband", "en")
        assert p.indicator_weights["gigabit_broadband"] == 0.5
        assert "transport" not in p.theme_weights
        q = profile_by_rules(store, "not bothered about pubs, schools matter", "en")
        assert q.indicator_weights["well_run_pubs"] == 0.5 and "amenities" not in q.theme_weights
        assert q.theme_weights["education"] == 2
        # Wanting it still lifts the theme
        assert profile_by_rules(store, "broadband matters", "en").theme_weights["transport"] == 2

    def test_not_needing_something_nearby_turns_its_distance_down(self, store):
        # Lower is better for a distance as it is for noise, so "no need for a GP" was read
        # like "no noise" and weighed the distance to a GP more
        assert (
            profile_by_rules(store, "no need for a GP", "en").indicator_weights["gp_distance"]
            == 0.5
        )
        shop = profile_by_rules(store, "we don't need a supermarket", "en").indicator_weights
        assert shop["supermarket_distance"] == 0.5
        # A problem is still a problem
        assert profile_by_rules(store, "no noise", "en").indicator_weights["noise_exposure"] == 2
        assert profile_by_rules(store, "no crime", "en").theme_weights["safety"] == 2

    def test_a_long_gaelic_rationale_ends_on_a_sentence(self, store):
        for text in ("buthan agus cinema", "buthan, theatre"):
            r = profile_by_rules(store, text, "gd").rationale
            assert len(r) <= 200 and r.endswith("."), r

    def test_the_phrase_table_is_built_once(self, store):
        store.profile_tables.clear()
        profile_by_rules(store, "schools", "en")
        table = store.profile_tables["en"]
        profile_by_rules(store, "pubs", "en")
        assert store.profile_tables["en"] is table


def test_a_draft_without_weights_takes_the_rules_reading(store):
    """Opus 5.5 described changes and left both maps empty (three calls in three): the
    rules engine's reading of the description stands in, with its own sentence."""
    draft = ProfileDraft(
        preset="family", theme_weights={}, indicator_weights={},
        name="Quiet family life", rationale="Noise and schools raised for the children.",
    )  # fmt: skip
    p = _from_draft(store, draft, "en", "model", description="I hate noise and schools matter")
    assert p.theme_weights["education"] >= 2 and p.indicator_weights["noise_exposure"] == 2
    assert p.name == "Quiet family life" and "raised" not in p.rationale
    # Nothing in the rules either: the preset stands, with the plain sentence
    q = _from_draft(store, draft, "en", "model", description="lorem ipsum")
    assert q.preset == "family" and not q.theme_weights and "Nothing" in q.rationale


class TestContextMeasures:
    """#223: hills, cycle lanes, beaches and the like are measures the cards show but the
    score cannot weigh. A profile names them in its rationale, in reading order, and
    never invents a multiplier for them; the weighting's own reading of the words
    ("nature" → environment) is untouched."""

    CYCLING = (
        "We cycle everywhere with two kids and love the beach: flat streets, lots of cycle "
        "lanes, a short trip to a clean bathing beach and lots of protected nature. Hills "
        "are a no."
    )

    def test_named_in_reading_order_once_each(self, store):
        assert profiles.context_measures(store, self.CYCLING, "en") == [
            "bathing_water_distance",
            "hilliness",
            "cycle_infrastructure",
            "bathing_water_class",
            "protected_nature",
        ]
        assert profiles.context_measures(store, "schools and pubs", "en") == []

    @pytest.mark.parametrize(
        ("description", "named"),
        [
            # Bare words that are usually something else do not fire; phrases do
            ("the life cycle of plants, my bike was stolen, historic mistakes", []),
            ("a sea of paperwork", []),
            ("cycle lanes, a bike lane, by the sea, listed buildings", [
                "cycle_infrastructure", "coast_distance", "listed_buildings"]),
            ("a flat near the river", ["river_status"]),
        ],
    )  # fmt: skip
    def test_phrases_fire_and_bare_words_do_not(self, store, description, named):
        assert profiles.context_measures(store, description, "en") == named

    def test_gaelic_and_irish_forms(self, store):
        assert profiles.context_measures(store, "speur dorcha, taobh na h-aibhne", "gd") == [
            "light_pollution",
            "river_status",
        ]
        assert profiles.context_measures(store, "lána rothaíochta agus spéir dhorcha", "ga") == [
            "cycle_infrastructure",
            "light_pollution",
        ]

    def test_the_rules_engine_says_so_and_weighs_none(self, store):
        p = profile_by_rules(store, self.CYCLING, "en")
        named = set(profiles.context_measures(store, self.CYCLING, "en"))
        assert not named & set(p.indicator_weights)
        assert p.rationale.startswith("Starts from") and len(p.rationale) <= 200
        assert p.rationale.endswith("are shown on the cards, not scored.")
        # Named in reading order, as many as fit in the sentence
        assert "Distance to a bathing water, Hilliness around homes" in p.rationale
        # "nature" still reads as the environment theme for the weighting
        assert p.theme_weights.get("environment", 1) > 1

    def test_only_context_measures_named(self, store):
        p = profile_by_rules(store, "Somewhere flat: we hate steep hills", "en")
        assert p.theme_weights == {} and p.indicator_weights == {}
        assert p.rationale.startswith("Starts from ")
        assert p.rationale.endswith("Hilliness around homes is shown on the cards, not scored.")

    def test_in_welsh(self, store):
        r = profile_by_rules(store, "lonydd beicio a traeth", "cy").rationale
        assert "ar y cardiau" in r and r.endswith("heb eu sgorio.")
        one = profile_by_rules(store, "ysgolion a llyfrgell", "cy").rationale
        assert one.endswith("heb ei sgorio.")

    def test_a_model_sentence_keeps_its_words_and_gains_the_note(self, store):
        draft = ProfileDraft(
            preset="family",
            theme_weights={"environment": 2},
            indicator_weights={},
            name="Cycling family by the sea",
            rationale="Starts from Family; the environment counts more for the beach.",
        )
        p = _from_draft(store, draft, "en", "model", description=self.CYCLING)
        assert p.rationale.startswith("Starts from Family; the environment counts more")
        assert p.rationale.endswith("not scored.") and len(p.rationale) <= 200
        assert p.theme_weights == {"environment": 2}

    def test_the_note_names_fewer_measures_when_it_would_not_fit(self, store):
        long = (
            "Asthma, night shifts, no car, first time buyer, dog walks, pubs and gigs, plus "
            "hills, cycle lanes, a beach, the coast, a nature reserve, a clean river, dark "
            "skies, an allotment, a library and a post office"
        )
        p = profile_by_rules(store, long, "en")
        assert len(p.rationale) <= 200 and p.rationale.endswith(".")
        # The scored sentence makes room, so the first context measure is always named
        assert "Hilliness around homes is shown on the cards, not scored." in p.rationale
        assert p.rationale.startswith("Starts from")

    def test_the_note_survives_a_long_scored_sentence(self, store):
        p = profile_by_rules(store, "asthma, pubs, a beach and dark skies", "en")
        assert p.indicator_weights["no2"] == 2 and p.indicator_weights["well_run_pubs"] == 2
        assert "Distance to a bathing water" in p.rationale and len(p.rationale) <= 200
        assert p.rationale.endswith("shown on the cards, not scored.")


class TestMutatedProblemWords:
    """A Welsh problem word stays a problem in its mutated form: "llai o drosedd" (less
    crime) wants more safety, as "less crime" does in English."""

    def test_less_crime_in_welsh_and_english(self, store):
        assert profile_by_rules(store, "less crime", "en").theme_weights["safety"] == 2
        assert profile_by_rules(store, "llai o drosedd", "cy").theme_weights["safety"] == 2
        assert profile_by_rules(store, "dim llawer o drosedd", "cy").theme_weights["safety"] == 2
        assert profile_by_rules(store, "trosedd isel", "cy").theme_weights["safety"] == 2


STATION = "not bothered about schools, no need for a station within 500 metres"


class TestEnglishOnAnotherLanguagesPage:
    """The v0.7.2 test pass sent one English sentence under each of the four locales,
    and "no need for a station" weighed rail more in Welsh, Gaelic and Irish: Welsh "a"
    (and) ended the negator's reach at the article, and "no" is no negator where it
    means "or". An English negator stops at English conjunctions only, and the phrases
    that open with "no" are read whole."""

    @pytest.mark.parametrize("locale", LOCALES)
    def test_no_need_for_a_station(self, store, locale):
        p = profile_by_rules(store, STATION, locale)
        assert p.indicator_weights == {"rail_service": 0.5}
        assert p.theme_weights == {"education": 0}

    @pytest.mark.parametrize("locale", LOCALES)
    def test_no_interest_in_nightlife(self, store, locale):
        p = profile_by_rules(store, "I never go out, no interest in nightlife", locale)
        assert p.theme_weights == {"amenities": 0}

    def test_each_language_keeps_its_own_reading(self, store):
        # A Welsh conjunction still ends a Welsh negator's reach
        cy = profile_by_rules(store, "dim tafarndai ac ysgolion da", "cy")
        assert cy.indicator_weights["well_run_pubs"] == 0.5 and cy.theme_weights["education"] == 2
        # "no" is still "or" in Gaelic and Irish, before an English word as well
        assert profile_by_rules(store, "bùthan no cinema", "gd").indicator_weights["culture"] == 2
        assert profile_by_rules(store, "siopaí nó gigs", "ga").indicator_weights["culture"] == 2
        # Wanting a station is still wanting one
        for locale in LOCALES:
            wanted = profile_by_rules(store, "I need a station within 500 metres", locale)
            assert wanted.indicator_weights == {"rail_service": 2}

    @pytest.mark.parametrize("locale", LOCALES)
    def test_a_draft_without_weights_takes_that_reading(self, store, locale):
        # What the model's drafts were in the pass: a name, a sentence and two empty
        # maps, so the profile is the rules engine's under the model's name
        draft = ProfileDraft(
            preset="balanced", theme_weights={}, indicator_weights={},
            name="No schools, no station", rationale="Schools and rail are turned down.",
        )  # fmt: skip
        p = _from_draft(store, draft, locale, "model", description=STATION)
        rules = profile_by_rules(store, STATION, locale)
        assert p.indicator_weights == {"rail_service": 0.5} and p.theme_weights == {"education": 0}
        assert p.rationale == rules.rationale and p.name == "No schools, no station"


class TestADraftIsHeldToTheDescription:
    """A model's draft is held to what the description disowns outright ("no need for a
    station", "not bothered about schools", "no kids"): what it turned up against that
    takes the rules' value, and the sentence is written again from the weights. Only a
    contradiction is corrected: a draft that agrees in direction keeps its own value,
    and where the rules read nothing disowned the draft stands."""

    @staticmethod
    def draft(themes: dict, measures: dict) -> ProfileDraft:
        return ProfileDraft(
            preset="balanced", theme_weights=themes, indicator_weights=measures,
            name="A model's name", rationale="The model's own sentence.",
        )  # fmt: skip

    @pytest.mark.parametrize("locale", LOCALES)
    def test_no_need_for_a_station_turned_up(self, store, locale):
        draft = self.draft({"education": 0}, {"rail_service": 2})
        p = _from_draft(store, draft, locale, "model", description=STATION)
        assert p.indicator_weights == {"rail_service": 0.5} and p.theme_weights == {"education": 0}
        # The sentence agrees with the weights: rail weighs less, nothing weighs more
        labels = profiles._labels(store, locale)
        base = dict(store.presets["balanced"].themes)
        assert p.rationale == profiles._rationale(
            store, locale, "balanced", {"education": 0}, base, {"rail_service": 0.5}
        )
        less = message(
            "profile_less_indicators_note_one",
            locale,
            fallback="profile_less_indicators_note",
            indicators=labels["indicators"]["rail_service"],
        )
        assert p.rationale.endswith(less) and p.name == "A model's name"

    def test_a_theme_turned_up_takes_the_rules_value(self, store):
        p = _from_draft(
            store, self.draft({"education": 2}, {}), "en", "model",
            description="not bothered about schools",
        )  # fmt: skip
        assert p.theme_weights == {"education": 0}  # 1 - 1, as the rules have it
        assert p.rationale == "Starts from Balanced; Schools & childcare counts less."
        # From the preset's own weight: Climate starts at a half
        q = _from_draft(
            store, self.draft({"climate": 1.5}, {}), "en", "model",
            description="the weather doesn't matter to me",
        )  # fmt: skip
        assert q.theme_weights == {"climate": 0}
        # A phrase that only turns things down is a negation too
        r = _from_draft(
            store, self.draft({"education": 2, "amenities": 2}, {}), "en", "model",
            description="pubs and gigs, no kids",
        )  # fmt: skip
        assert r.theme_weights == {"education": 0, "amenities": 2}

    def test_a_wish_turned_up_is_left_alone(self, store):
        draft = self.draft({}, {"rail_service": 2})
        for locale in LOCALES:
            p = _from_draft(
                store, draft, locale, "model", description="I need a station within 500 metres"
            )
            assert p.indicator_weights == {"rail_service": 2} and p.theme_weights == {}
            assert p.rationale == "The model's own sentence."

    def test_a_draft_stands_where_the_rules_read_no_negation(self, store):
        draft = self.draft({"climate": 2, "housing": 0.5}, {"sunshine": 3, "house_price": 0.5})
        p = _from_draft(store, draft, "en", "model", description="sun please, schools matter")
        assert p.theme_weights == {"climate": 2, "housing": 0.5}
        assert p.indicator_weights == {"sunshine": 3, "house_price": 0.5}
        assert p.rationale == "The model's own sentence."
        # Nor does the guard add what a draft left out: "gan páistí" is not a phrase the
        # rules know (they know the lenited "gan pháistí"), and the draft stands
        ga = _from_draft(
            store, self.draft({}, {"well_run_pubs": 2}), "ga", "model",
            description="tithe tábhairne agus ceol beo, gan páistí",
        )  # fmt: skip
        assert ga.theme_weights == {} and ga.indicator_weights == {"well_run_pubs": 2}
        assert ga.rationale == "The model's own sentence."

    def test_a_draft_that_agrees_in_direction_keeps_its_own_value(self, store):
        draft = self.draft({"education": 0.5}, {"rail_service": 0.25})
        p = _from_draft(store, draft, "en", "model", description=STATION)
        assert p.theme_weights == {"education": 0.5}  # the rules say 0
        assert p.indicator_weights == {"rail_service": 0.25}  # the rules say 0.5
        assert p.rationale == "The model's own sentence."

    @pytest.mark.parametrize(
        ("description", "measure"),
        [
            # Each is read as turned down by the rules, and each asks for the thing
            ("not far from a station", "rail_service"),
            ("I have no car so the bus matters", "connectivity_overall"),
            ("no kids so pubs and gigs matter", "well_run_pubs"),
            ("I can't live without a park nearby", "green_space_access"),
        ],
    )
    def test_a_bare_no_or_not_does_not_overrule_a_draft(self, store, description, measure):
        # The rules take any "no", "not" or "without" within four words for a negation,
        # and are wrong often enough: only what a description says outright ("no need
        # for", "not bothered about", "no kids") holds a model's draft
        assert profile_by_rules(store, description, "en").indicator_weights[measure] == 0.5
        p = _from_draft(store, self.draft({}, {measure: 2}), "en", "model", description=description)
        assert p.indicator_weights == {measure: 2}
        assert p.rationale == "The model's own sentence."

    def test_a_trade_is_not_a_negation(self, store):
        # Working from home turns transport and rail down by the rules, beside broadband
        # up: a judgement, not a negation. Broadband is in the transport theme, and a
        # model may raise the theme for it
        rules = profile_by_rules(store, "I work from home", "en")
        assert rules.theme_weights == {"transport": 0.5}
        draft = self.draft({"transport": 1.5}, {"gigabit_broadband": 2})
        p = _from_draft(store, draft, "en", "model", description="I work from home")
        assert p.theme_weights == {"transport": 1.5}
        assert p.indicator_weights == {"gigabit_broadband": 2}
        assert p.rationale == "The model's own sentence."

    def test_a_correction_is_logged_by_id_only(self, store, caplog):
        draft = self.draft({"education": 2}, {"rail_service": 2})
        with caplog.at_level(logging.INFO):
            _from_draft(store, draft, "en", "model", description=STATION)
        (line,) = [r.getMessage() for r in caplog.records if "disowns" in r.getMessage()]
        assert "education" in line and "rail_service" in line
        assert "station" not in line and "bothered" not in line  # never the description

    def test_the_log_lines_reach_the_servers_log(self):
        # INFO lines from a logger outside "lix" are dropped by the server's logging: the
        # line saying the rules stood in for a draft without weights was never printed
        assert profiles.logger.name.startswith("lix.")
        assert profiles.logger.isEnabledFor(logging.INFO)
