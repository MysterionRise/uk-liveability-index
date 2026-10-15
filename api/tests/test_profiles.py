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
