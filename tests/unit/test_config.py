"""Tests for environment-backed settings without reading the developer's .env."""

import pytest

from scione.config import ConfigurationError, Settings


def test_settings_mask_key_and_return_secret_only_on_request() -> None:
    settings = Settings(groq_api_key="secret-test-key", _env_file=None)

    assert "secret-test-key" not in repr(settings)
    assert settings.require_groq_api_key() == "secret-test-key"


def test_settings_reject_missing_key() -> None:
    settings = Settings(groq_api_key=None, _env_file=None)

    with pytest.raises(ConfigurationError, match="GROQ_API_KEY is missing"):
        settings.require_groq_api_key()
