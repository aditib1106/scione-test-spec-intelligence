"""Environment-backed application settings."""

from __future__ import annotations

from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import make_url
from sqlalchemy.exc import ArgumentError


class ConfigurationError(RuntimeError):
    """Raised when required runtime configuration is missing or unsafe."""


class Settings(BaseSettings):
    """Load local configuration without exposing secret values in logs or run files."""

    model_config = SettingsConfigDict(
        env_file=(".env", ".env.database"),
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
        hide_input_in_errors=True,
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
    fireworks_reasoning_effort: Literal["none", "low", "medium", "high", "xhigh", "max"] | None = (
        None
    )
    sail_api_key: SecretStr | None = None
    sail_model: str = "zai-org/GLM-5.3"
    # Whole-test matching can include many independently grounded fields. Allow
    # provider load variance without discarding a valid long response; every
    # request still has a finite deadline and the Sail adapter never retries.
    sail_timeout_seconds: float = Field(default=300.0, gt=0)
    sail_max_output_tokens: int = Field(default=12288, gt=0)
    sail_temperature: float = Field(default=0.0, ge=0, le=2)
    scione_single_attempt_mode: bool = True
    scione_workspace_dir: Path = Path("local_data")
    # Optional cloud/local MySQL URL. None uses an embedded SQL database inside
    # the workspace, so read-only/clone workflows require no database daemon.
    scione_database_url: SecretStr | None = None
    scione_customer_input_character_limit: int = Field(default=60_000, gt=0)
    scione_customer_gemini_output_tokens: int = Field(default=16_000, gt=0)
    scione_matching_input_character_limit: int = Field(default=120_000, gt=0)
    scione_long_document_parallelism: int = Field(default=4, ge=1, le=4)
    scione_environment: Literal["development", "production"] = "development"
    scione_public_url: str | None = None
    scione_allowed_hosts: list[str] = Field(
        default_factory=lambda: ["localhost", "127.0.0.1", "[::1]", "testserver"]
    )
    scione_trusted_origins: list[str] = Field(default_factory=list)
    scione_cookie_secure: bool | None = None
    scione_frontend_dir: Path | None = None
    scione_auth_enabled: bool = False
    scione_auth_database_url: SecretStr | None = None
    scione_auth_session_hours: int = Field(default=12, ge=1, le=168)
    scione_auth_idle_minutes: int = Field(default=60, ge=5, le=1440)
    scione_auth_login_window_seconds: int = Field(default=900, ge=60, le=3600)
    scione_auth_login_user_limit: int = Field(default=8, ge=3, le=100)
    scione_auth_login_ip_limit: int = Field(default=40, ge=5, le=1000)
    scione_bootstrap_accounts: SecretStr | None = None

    @field_validator("scione_database_url", "scione_auth_database_url", mode="before")
    @classmethod
    def database_driver(cls, value):
        if value is None:
            return None
        text = value.get_secret_value() if isinstance(value, SecretStr) else value
        if not isinstance(text, str):
            raise ValueError("Enter a valid database connection URL.")
        try:
            parsed = make_url(text)
        except (ArgumentError, ValueError, TypeError):
            raise ValueError("Enter a valid database connection URL.") from None
        if parsed.drivername == "mysql":
            return SecretStr(
                parsed.set(drivername="mysql+pymysql").render_as_string(hide_password=False)
            )
        return SecretStr(text)

    @model_validator(mode="after")
    def authentication_configuration(self):
        if self.scione_environment == "production":
            if not self.scione_auth_enabled:
                raise ValueError("Production requires SCIONE_AUTH_ENABLED=true.")
            if self.scione_cookie_secure is False:
                raise ValueError("Production session cookies must use Secure.")
            if not self.scione_public_url:
                raise ValueError("Production requires SCIONE_PUBLIC_URL with its HTTPS origin.")
        origins = [*self.scione_trusted_origins]
        if self.scione_public_url:
            origins.append(self.scione_public_url)
        for origin in origins:
            parsed = urlsplit(origin)
            if (
                parsed.scheme not in {"http", "https"}
                or not parsed.netloc
                or parsed.username is not None
                or parsed.password is not None
                or parsed.path not in {"", "/"}
                or parsed.query
                or parsed.fragment
            ):
                raise ValueError("Application origins must be HTTP(S) origins without paths.")
            if self.scione_environment == "production" and parsed.scheme != "https":
                raise ValueError("Production application origins must use HTTPS.")
        return self

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
