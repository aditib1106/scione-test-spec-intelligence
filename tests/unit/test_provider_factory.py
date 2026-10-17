"""Provider construction contracts using stubs, never SDK or network clients."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

import scione.providers.factory as factory
from scione.config import ConfigurationError, Settings


def _settings(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "model_provider": "groq",
        "groq_api_key": "fictional-groq-factory-key",
        "groq_model": "offline-groq-default",
        "groq_timeout_seconds": 17.0,
        "groq_max_retries": 3,
        "groq_max_completion_tokens": 987,
        "groq_reasoning_effort": "medium",
        "groq_temperature": 0.25,
        "groq_strict_structured_output": False,
        "gemini_api_key": "fictional-gemini-factory-key",
        "gemini_model": "offline-gemini-default",
        "gemini_timeout_seconds": 23.0,
        "gemini_max_retries": 4,
        "gemini_max_output_tokens": 857,
        "gemini_temperature": 0.5,
        "gemini_thinking_level": "high",
        "scione_single_attempt_mode": True,
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)


@pytest.fixture
def adapters(monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    """Capture constructor arguments without constructing either vendor SDK."""

    calls: dict[str, list[dict[str, object]]] = {"groq": [], "gemini": []}
    instances = {"groq": object(), "gemini": object()}

    def groq_stub(**kwargs: object) -> object:
        calls["groq"].append(kwargs)
        return instances["groq"]

    def gemini_stub(**kwargs: object) -> object:
        calls["gemini"].append(kwargs)
        return instances["gemini"]

    monkeypatch.setattr(factory, "GroqModelProvider", groq_stub)
    monkeypatch.setattr(factory, "GeminiModelProvider", gemini_stub)
    return SimpleNamespace(calls=calls, instances=instances)


@pytest.mark.parametrize("provider", ["groq", "gemini"])
@pytest.mark.parametrize("model_override", [None, "offline-model-override"])
def test_factory_uses_configured_provider_and_default_or_overridden_model(
    adapters: SimpleNamespace, provider: str, model_override: str | None
) -> None:
    settings = _settings(model_provider=provider)

    result = factory.create_configured_provider(settings, model_name=model_override)

    assert result is adapters.instances[provider]
    assert len(adapters.calls[provider]) == 1
    arguments = adapters.calls[provider][0]
    expected_model = model_override or getattr(settings, f"{provider}_model")
    assert arguments["model_name"] == expected_model
    assert not adapters.calls["gemini" if provider == "groq" else "groq"]


@pytest.mark.parametrize("provider", ["groq", "gemini"])
def test_factory_explicit_provider_overrides_settings_and_normalizes_name(
    adapters: SimpleNamespace, provider: str
) -> None:
    other = "gemini" if provider == "groq" else "groq"
    settings = _settings(model_provider=other)

    result = factory.create_configured_provider(settings, provider_name=f" {provider.upper()} ")

    assert result is adapters.instances[provider]
    assert adapters.calls[provider][0]["model_name"] == getattr(settings, f"{provider}_model")
    assert not adapters.calls[other]


@pytest.mark.parametrize("provider", ["groq", "gemini"])
@pytest.mark.parametrize("single_attempt", [True, False])
def test_factory_single_attempt_overrides_retry_settings_only_when_enabled(
    adapters: SimpleNamespace, provider: str, single_attempt: bool
) -> None:
    settings = _settings(
        model_provider=provider,
        scione_single_attempt_mode=single_attempt,
    )

    factory.create_configured_provider(settings)

    expected = 0 if single_attempt else getattr(settings, f"{provider}_max_retries")
    assert adapters.calls[provider][0]["max_retries"] == expected


def test_factory_passes_groq_specific_configuration(adapters: SimpleNamespace) -> None:
    settings = _settings(scione_single_attempt_mode=False)

    factory.create_configured_provider(settings)

    assert adapters.calls["groq"] == [
        {
            "api_key": settings.require_groq_api_key(),
            "model_name": "offline-groq-default",
            "timeout_seconds": 17.0,
            "max_retries": 3,
            "max_completion_tokens": 987,
            "reasoning_effort": "medium",
            "temperature": 0.25,
            "strict_structured_output": False,
        }
    ]


def test_factory_passes_gemini_specific_configuration(adapters: SimpleNamespace) -> None:
    settings = _settings(model_provider="gemini", scione_single_attempt_mode=False)

    factory.create_configured_provider(settings)

    assert adapters.calls["gemini"] == [
        {
            "api_key": settings.require_gemini_api_key(),
            "model_name": "offline-gemini-default",
            "timeout_seconds": 23.0,
            "max_retries": 4,
            "max_output_tokens": 857,
            "temperature": 0.5,
            "thinking_level": "high",
        }
    ]


@pytest.mark.parametrize("provider", ["groq", "gemini"])
def test_factory_passes_trimmed_key_without_printing_or_unmasking_settings(
    adapters: SimpleNamespace, capsys: pytest.CaptureFixture[str], provider: str
) -> None:
    key = f"fictional-{provider}-only-for-this-test"
    settings = _settings(model_provider=provider, **{f"{provider}_api_key": f" {key} "})

    factory.create_configured_provider(settings)

    assert adapters.calls[provider][0]["api_key"] == key
    assert key not in repr(settings)
    output = capsys.readouterr()
    assert key not in output.out
    assert key not in output.err


@pytest.mark.parametrize("provider", ["groq", "gemini"])
@pytest.mark.parametrize("key", [None, "", "placeholder"])
def test_factory_rejects_missing_or_placeholder_keys_before_constructing_adapter(
    adapters: SimpleNamespace, provider: str, key: str | None
) -> None:
    value = f"replace_with_your_{provider}_api_key" if key == "placeholder" else key
    settings = _settings(model_provider=provider, **{f"{provider}_api_key": value})

    with pytest.raises(ConfigurationError, match=f"{provider.upper()}_API_KEY"):
        factory.create_configured_provider(settings)

    assert adapters.calls == {"groq": [], "gemini": []}


def test_factory_rejects_unsupported_provider_without_constructing_adapters(
    adapters: SimpleNamespace,
) -> None:
    settings = _settings(model_provider="not-installed")

    with pytest.raises(ConfigurationError, match="Unsupported provider='not-installed'"):
        factory.create_configured_provider(settings)

    assert adapters.calls == {"groq": [], "gemini": []}
