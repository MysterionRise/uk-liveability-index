"""A weighting profile from a description of the user: the rules engine, the checks on
a model's draft, the engine choice and the model path."""

import json
import os

import pytest
from pydantic import ValidationError
from pydantic_ai.messages import ModelResponse, TextPart, ToolCallPart
from pydantic_ai.models.function import FunctionModel

from lix_api.models import ProfileDraft, WeightProfile
from lix_api.services import profiles
from lix_api.services.profiles import _from_draft, engine_choice, profile_by_rules, profile_run

OWNER = "I'm a young professional with one child going into primary school and I have a sun deficit"


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
        assert "Rail service" not in p.rationale and "weigh" in p.rationale
        # "fibre" names the measure alone ("broadband" is the transport theme as well)
        only_down = profile_by_rules(store, "I don't care about fibre", "en").rationale
        assert "weighs less within its theme" in only_down

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
