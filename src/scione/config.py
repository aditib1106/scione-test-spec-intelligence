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
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    model_provider: str = "groq"
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
    scione_single_attempt_mode: bool = True
    scione_runs_dir: Path = Path("runs")

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
