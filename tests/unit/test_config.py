"""Tests for environment-backed settings without reading the developer's .env."""

import pytest
from pydantic import ValidationError

from scione.config import ConfigurationError, Settings


def test_settings_mask_key_and_return_secret_only_on_request() -> None:
    settings = Settings(
        groq_api_key="secret-test-key",
        gemini_api_key="secret-gemini-key",
        _env_file=None,
    )

    assert "secret-test-key" not in repr(settings)
    assert "secret-gemini-key" not in repr(settings)
    assert settings.require_groq_api_key() == "secret-test-key"
    assert settings.require_gemini_api_key() == "secret-gemini-key"


def test_settings_reject_missing_key() -> None:
    settings = Settings(groq_api_key=None, _env_file=None)

    with pytest.raises(ConfigurationError, match="GROQ_API_KEY is missing"):
        settings.require_groq_api_key()

    with pytest.raises(ConfigurationError, match="GEMINI_API_KEY is missing"):
        settings.require_gemini_api_key()


@pytest.mark.parametrize("provider", ["fireworks", "sail"])
def test_credit_provider_secrets_stay_masked_and_are_trimmed(provider):
    key = f"fictional-{provider}-test-key"
    settings = Settings(_env_file=None, **{f"{provider}_api_key": f" {key} "})
    assert key not in repr(settings)
    assert settings.require_api_key(provider) == key
    assert settings.provider_configured(provider)


@pytest.mark.parametrize("provider", ["fireworks", "sail"])
@pytest.mark.parametrize("value", [None, "", "  ", "replace_with_your_api_key"])
def test_blank_and_example_credit_keys_are_unavailable(provider, value):
    settings = Settings(_env_file=None, **{f"{provider}_api_key": value})
    assert not settings.provider_configured(provider)
    with pytest.raises(ConfigurationError, match=f"{provider.upper()}_API_KEY"):
        settings.require_api_key(provider)


@pytest.mark.parametrize("effort", [None, "none", "low", "medium", "high", "xhigh", "max"])
def test_fireworks_optional_reasoning_setting_preserves_explicit_choice(effort):
    settings = Settings(_env_file=None, fireworks_reasoning_effort=effort)
    assert settings.fireworks_reasoning_effort == effort


@pytest.mark.parametrize("effort", [False, 1024, "invalid"])
def test_fireworks_reasoning_setting_rejects_ambiguous_or_unsupported_values(effort):
    with pytest.raises(ValidationError):
        Settings(_env_file=None, fireworks_reasoning_effort=effort)
