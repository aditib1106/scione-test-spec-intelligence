"""Explicit semantic matching, separate from extraction and numeric comparison."""

from scione.matching.comparison import compile_matching_run
from scione.matching.models import (
    AlignmentValidationIssue,
    CapabilityCandidate,
    ClaimAlignment,
    ItemAlignment,
    MatchingInput,
    RequirementCandidates,
    SemanticMatchingOutput,
    SemanticMatchingRun,
)
from scione.matching.service import (
    SemanticMatchingError,
    SemanticMatchingRunner,
    build_matching_input,
    input_fingerprint,
    validate_matching_output,
)

__all__ = [
    "AlignmentValidationIssue",
    "CapabilityCandidate",
    "ClaimAlignment",
    "ItemAlignment",
    "MatchingInput",
    "RequirementCandidates",
    "SemanticMatchingError",
    "SemanticMatchingOutput",
    "SemanticMatchingRun",
    "SemanticMatchingRunner",
    "build_matching_input",
    "compile_matching_run",
    "input_fingerprint",
    "validate_matching_output",
]
