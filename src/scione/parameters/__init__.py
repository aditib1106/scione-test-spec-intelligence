"""Controlled pilot parameter vocabulary and deterministic comparison rules."""

from .engine import (
    PairEvaluation,
    contexts_match,
    evaluate_pair,
    generic_reporting_unavailable,
    normalization_evidence,
    normalize_claim,
    normalize_claims,
)
from .registry import REGISTRY_VERSION, ParameterDefinition, definitions, resolve_definition

__all__ = [
    "REGISTRY_VERSION",
    "PairEvaluation",
    "ParameterDefinition",
    "contexts_match",
    "definitions",
    "evaluate_pair",
    "generic_reporting_unavailable",
    "normalization_evidence",
    "normalize_claim",
    "normalize_claims",
    "resolve_definition",
]
