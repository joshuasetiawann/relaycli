"""Settings field validation: blank model, implausible API keys, numeric bounds."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from relaycli.core.config import Settings


def _hermetic(**kw) -> Settings:
    kw.setdefault("relay_enabled", False)
    return Settings(_env_file=None, **kw)


# -- model ----------------------------------------------------------------
def test_blank_model_rejected():
    with pytest.raises(ValidationError, match="model must not be blank"):
        _hermetic(model="")


def test_whitespace_only_model_rejected():
    with pytest.raises(ValidationError, match="model must not be blank"):
        _hermetic(model="   ")


def test_normal_model_accepted():
    assert _hermetic(model="gpt-4o-mini").model == "gpt-4o-mini"


# -- API keys ---------------------------------------------------------------
def test_api_key_with_whitespace_rejected():
    with pytest.raises(ValidationError, match="contains whitespace"):
        _hermetic(OPENAI_API_KEY="sk-abc 123")


def test_api_key_too_short_rejected():
    with pytest.raises(ValidationError, match="too short"):
        _hermetic(OPENAI_API_KEY="ab")


def test_api_key_none_accepted():
    assert _hermetic(OPENAI_API_KEY=None).openai_api_key is None


def test_api_key_empty_string_accepted():
    # Matches test_doctor.py's convention: "" means "no key configured",
    # not an implausibly-short one - never rejected.
    assert _hermetic(OPENROUTER_API_KEY="").openrouter_api_key == ""


def test_short_but_realistic_placeholder_key_accepted():
    # The test suite's own placeholder convention (e.g. "sk-ant", 6 chars) -
    # the length floor must stay well below this.
    assert _hermetic(ANTHROPIC_API_KEY="sk-ant").anthropic_api_key == "sk-ant"


# -- pre-existing numeric bounds (regression guard, not new validators) -----
def test_temperature_out_of_range_rejected():
    with pytest.raises(ValidationError):
        _hermetic(temperature=9.0)


def test_max_iterations_below_one_rejected():
    with pytest.raises(ValidationError):
        _hermetic(max_iterations=0)


def test_token_budget_below_floor_rejected():
    with pytest.raises(ValidationError):
        _hermetic(token_budget=100)
