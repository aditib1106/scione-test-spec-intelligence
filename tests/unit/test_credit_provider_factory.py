"""Configured credit-provider construction without creating network clients."""

import pytest

import scione.providers.factory as factory
from scione.config import ConfigurationError, Settings


@pytest.mark.parametrize("provider", ["fireworks", "sail"])
@pytest.mark.parametrize("override", [None, "another-authorized-model"])
def test_factory_passes_provider_specific_settings_without_crossing_keys(
    monkeypatch, provider, override
):
    calls = []
    sentinel = object()

    def construct(**arguments):
        calls.append(arguments)
        return sentinel

    monkeypatch.setattr(factory, "OpenAICompatibleModelProvider", construct)
    settings = Settings(
        _env_file=None,
        model_provider=provider,
        fireworks_api_key="fictional-fireworks-key",
        sail_api_key="fictional-sail-key",
        **{f"{provider}_max_output_tokens": 1234, f"{provider}_temperature": 0.2},
    )
    result = factory.create_configured_provider(settings, model_name=override)
    assert result is sentinel
    assert calls == [
        {
            "provider_name": provider,
            "api_key": f"fictional-{provider}-key",
            "model_name": override or getattr(settings, f"{provider}_model"),
            "timeout_seconds": getattr(settings, f"{provider}_timeout_seconds"),
            "max_output_tokens": 1234,
            "temperature": 0.2,
            **({"reasoning_effort": None} if provider == "fireworks" else {}),
        }
    ]


def test_factory_passes_explicit_fireworks_reasoning_override():
    settings = Settings(
        _env_file=None,
        fireworks_api_key="fictional-fireworks-key",
        fireworks_reasoning_effort="low",
    )
    provider = factory.create_configured_provider(settings, provider_name="fireworks")
    assert provider._reasoning_effort == "low"


@pytest.mark.parametrize("provider", ["fireworks", "sail"])
def test_factory_rejects_missing_key_before_constructing_client(monkeypatch, provider):
    def forbidden(**_arguments):
        raise AssertionError("Missing credentials must fail before client construction.")

    monkeypatch.setattr(factory, "OpenAICompatibleModelProvider", forbidden)
    settings = Settings(_env_file=None, **{f"{provider}_api_key": None})
    with pytest.raises(ConfigurationError, match=f"{provider.upper()}_API_KEY"):
        factory.create_configured_provider(settings, provider_name=provider)
