"""How the assistant's models are built: a keyless start (#239) and each model's own
settings on the profile call (#234)."""

import logging

from lix_api.agent import models


def _start(monkeypatch, caplog, model: str) -> logging.LogRecord:
    monkeypatch.setenv("LIX_MODEL", model)
    for key in ("OPENROUTER_API_KEY", "ANTHROPIC_API_KEY", "LIX_FALLBACK_MODELS"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setattr(models, "MODEL_PROBLEM", None)
    caplog.clear()
    with caplog.at_level(logging.INFO):
        models.build_model()
    (record,) = [r for r in caplog.records if "can't be used" in r.message]
    return record


def test_a_missing_key_is_information_and_a_wrong_model_is_an_error(monkeypatch, caplog):
    # Every keyless start used to log an ERROR, on the path the README recommends first
    assert (
        _start(monkeypatch, caplog, "openrouter:anthropic/claude-haiku-5.5").levelno == logging.INFO
    )
    assert _start(monkeypatch, caplog, "anthropic:claude-haiku-5-5").levelno == logging.INFO
    assert _start(monkeypatch, caplog, "no-such-provider:model").levelno == logging.ERROR
    monkeypatch.setattr(models, "MODEL_PROBLEM", None)


def test_every_model_keeps_its_own_settings_on_the_profile_call(monkeypatch):
    # Settings given to a run override each model's own, so the profile run sets only the
    # generic caps: the fallback used to take the primary's provider settings (#234)
    monkeypatch.setenv("LIX_MODEL", "openrouter:anthropic/claude-haiku-5.5")
    monkeypatch.setenv("LIX_FALLBACK_MODELS", "openrouter:qwen/qwen3.8-27b")
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test")
    monkeypatch.setattr(models, "MODEL_PROBLEM", None)
    primary, fallback = models.build_model().models
    assert primary.settings["openrouter_provider"]["order"] == ["anthropic"]
    assert "openrouter_provider" not in fallback.settings  # Anthropic's endpoint is the primary's
    assert "openrouter_reasoning" in fallback.settings
    assert set(models.generic_settings(max_tokens=3000)) == {"max_tokens", "timeout"}
    assert set(models.generic_settings()) < set(models.model_settings())
