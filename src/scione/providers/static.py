"""Deterministic provider used to test orchestration without an external API."""

from __future__ import annotations

import json
from pathlib import Path
from time import perf_counter
from typing import Any

from scione.providers.base import (
    ModelProvider,
    ProviderError,
    StructuredGenerationRequest,
    StructuredGenerationResponse,
)


class StaticModelProvider(ModelProvider):
    """Return a pre-supplied payload; never make a network request."""

    def __init__(self, payload: dict[str, Any], *, model_name: str = "static-fixture") -> None:
        self._payload = payload
        self._model_name = model_name

    @classmethod
    def from_json_file(cls, path: str | Path) -> StaticModelProvider:
        fixture_path = Path(path)
        try:
            payload = json.loads(fixture_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ProviderError(f"Unable to load static response {fixture_path}: {exc}") from exc

        if not isinstance(payload, dict):
            raise ProviderError(f"Static response must be a JSON object: {fixture_path}")
        return cls(payload)

    @property
    def provider_name(self) -> str:
        return "static"

    @property
    def model_name(self) -> str:
        return self._model_name

    def generate_structured(
        self,
        request: StructuredGenerationRequest,
    ) -> StructuredGenerationResponse:
        del request
        started = perf_counter()
        raw_text = json.dumps(self._payload, ensure_ascii=False)
        return StructuredGenerationResponse(
            provider=self.provider_name,
            model=self.model_name,
            raw_text=raw_text,
            parsed=self._payload,
            latency_ms=(perf_counter() - started) * 1000,
        )
