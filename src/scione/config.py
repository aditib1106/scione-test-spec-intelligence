"""Environment-backed application settings."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class ConfigurationError(RuntimeError):
    """Raised when required runtime configuration is missing or unsafe."""


class Settings(BaseSettings):
    """Load local configuration without exposing secret values in logs or run files."""

    model_config = SettingsConfigDict(
        env_file=(".env", ".env.database"),
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    model_provider: str = "sail"
    groq_api_key: SecretStr | None = None
    groq_model: str = "qwen/qwen3.8-27b"
    groq_timeout_seconds: float = Field(default=60.0, gt=0)
    groq_max_retries: int = Field(default=2, ge=0)
    groq_max_completion_tokens: int = Field(default=6000, gt=0)
    groq_reasoning_effort: Literal["none", "default", "low", "medium", "high"] = "none"
    groq_temperature: float = Field(default=0.7, ge=0, le=2)
    groq_strict_structured_output: bool = True
    gemini_api_key: SecretStr | None = None
    gemini_model: str = "gemini-3.5-flash-lite"
    gemini_timeout_seconds: float = Field(default=60.0, gt=0)
    gemini_max_retries: int = Field(default=0, ge=0)
    gemini_max_output_tokens: int = Field(default=6000, gt=0)
    gemini_temperature: float = Field(default=0.0, ge=0, le=2)
    gemini_thinking_level: Literal["low", "medium", "high"] = "low"
    fireworks_api_key: SecretStr | None = None
    fireworks_model: str = "accounts/fireworks/models/deepseek-v4p1-flash-1017"
    fireworks_timeout_seconds: float = Field(default=90.0, gt=0)
    fireworks_max_output_tokens: int = Field(default=8192, gt=0)
    fireworks_temperature: float = Field(default=0.0, ge=0, le=2)
    fireworks_reasoning_effort: (
        Literal["none", "low", "medium", "high", "xhigh", "max"] | None
    ) = None
    sail_api_key: SecretStr | None = None
    sail_model: str = "zai-org/GLM-5.3"
    sail_timeout_seconds: float = Field(default=90.0, gt=0)
    sail_max_output_tokens: int = Field(default=8192, gt=0)
    sail_temperature: float = Field(default=0.0, ge=0, le=2)
    scione_single_attempt_mode: bool = True
    scione_workspace_dir: Path = Path("local_data")
    # Optional cloud/local MySQL URL. None uses an embedded SQL database inside
    # the workspace, so read-only/clone workflows require no database daemon.
    scione_database_url: SecretStr | None = None
    scione_customer_input_character_limit: int = Field(default=60_000, gt=0)
    scione_customer_gemini_output_tokens: int = Field(default=16_000, gt=0)
    scione_matching_input_character_limit: int = Field(default=120_000, gt=0)

    def require_groq_api_key(self) -> str:
        if self.groq_api_key is None:
            raise ConfigurationError(
                "GROQ_API_KEY is missing. Copy .env.example to .env and set a local key."
            )
        value = self.groq_api_key.get_secret_value().strip()
        if not value or value == "replace_with_your_groq_api_key":
            raise ConfigurationError("GROQ_API_KEY still contains the placeholder value.")
        return value

    def require_gemini_api_key(self) -> str:
        if self.gemini_api_key is None:
            raise ConfigurationError(
                "GEMINI_API_KEY is missing. Copy the Gemini settings from "
                ".env.example into your local .env."
            )
        value = self.gemini_api_key.get_secret_value().strip()
        if not value or value == "replace_with_your_gemini_api_key":
            raise ConfigurationError("GEMINI_API_KEY still contains the placeholder value.")
        return value

    def require_api_key(self, provider: str) -> str:
        """Return an explicitly configured secret; never treat examples as keys."""
        if provider == "groq":
            return self.require_groq_api_key()
        if provider == "gemini":
            return self.require_gemini_api_key()
        if provider not in {"fireworks", "sail"}:
            raise ConfigurationError(f"Unsupported provider={provider!r}.")
        name = f"{provider.upper()}_API_KEY"
        secret = getattr(self, f"{provider}_api_key")
        if secret is None or not secret.get_secret_value().strip():
            raise ConfigurationError(f"{name} is missing. Fill it in your local .env.")
        value = secret.get_secret_value().strip()
        if value.casefold().startswith("replace_with_"):
            raise ConfigurationError(f"{name} still contains the placeholder value.")
        return value

    def provider_configured(self, provider: str) -> bool:
        try:
            self.require_api_key(provider)
        except ConfigurationError:
            return False
        return True
