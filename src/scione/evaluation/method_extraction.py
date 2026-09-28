"""Transparent metrics for the v0.1 test-method extraction schema."""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Iterable

from pydantic import BaseModel, ConfigDict, Field

from scione.schemas import IngestedDocument, SourceEvidence, TestMethodExtraction


class SetMetric(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    matched: int = Field(ge=0)
    predicted: int = Field(ge=0)
    expected: int = Field(ge=0)
    precision: float = Field(ge=0, le=1)
    recall: float = Field(ge=0, le=1)
    f1: float = Field(ge=0, le=1)
    missing: list[str]
    unexpected: list[str]
    matched_values: list[str] = Field(default_factory=list)
    predicted_values: list[str] = Field(default_factory=list)
    expected_values: list[str] = Field(default_factory=list)


class EvidenceGrounding(BaseModel):
    """Whether model-cited quotes can be found on their claimed physical pages."""

    model_config = ConfigDict(extra="forbid")

    grounded: int = Field(ge=0)
    total: int = Field(ge=0)
    rate: float | None = Field(default=None, ge=0, le=1)
    ungrounded: list[str] = Field(default_factory=list)


class EvaluationReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: str
    metrics: list[SetMetric]
    unsupported_acceptance_criteria: int = Field(ge=0)
    evaluator_version: str = "0.1"
    evidence_grounding: EvidenceGrounding | None = None


def _normalize(value: str | int | float | None) -> str:
    if value is None:
        return "<null>"
    return re.sub(r"\s+", " ", str(value).strip().casefold().replace("_", " "))


def _normalize_unit(value: str | None) -> str:
    normalized = _normalize(value)
    aliases = {
        "spectral degrees": "spectral degree",
    }
    return aliases.get(normalized, normalized)


def _normalize_method_id(value: str | None) -> str:
    """Treat source-equivalent labels such as A and Test Method A alike."""

    normalized = _normalize(value)
    return re.sub(r"^(?:test\s+)?method\s+", "", normalized)


def _normalize_method_name(value: str, method_id: str | None) -> str:
    """Remove a redundant method label already represented by ``method_id``."""

    normalized = _normalize(value)
    normalized_id = _normalize_method_id(method_id)
    if normalized_id in {"", "<null>"}:
        return normalized
    return re.sub(
        rf"^(?:test\s+)?method\s+{re.escape(normalized_id)}\s*(?:[-:–—]\s*)?",
        "",
        normalized,
    )


def _normalize_designation(value: str | None, version: str | None) -> str:
    """Avoid double-counting a revision suffix already stored as ``version``."""

    normalized = _normalize(value)
    normalized_version = _normalize(version)
    if normalized_version in {"", "<null>"}:
        return normalized
    return re.sub(
        rf"\s*[-–—]\s*{re.escape(normalized_version)}$",
        "",
        normalized,
    )


def _normalize_parameter_name(value: str) -> str:
    """Canonicalize stage labels while retaining the underlying measured concept."""

    normalized = _normalize(value)
    normalized = re.sub(r"^(?:initial|final|verification)\s+", "", normalized)
    normalized = re.sub(r"\s+h\d+$", "", normalized)
    return re.sub(r"\s+result$", "", normalized)


def _metric(name: str, predicted: Iterable[str], expected: Iterable[str]) -> SetMetric:
    predicted_set = set(predicted)
    expected_set = set(expected)
    matched = predicted_set & expected_set

    if not predicted_set and not expected_set:
        precision = recall = f1 = 1.0
    else:
        precision = len(matched) / len(predicted_set) if predicted_set else 1.0
        recall = len(matched) / len(expected_set) if expected_set else 1.0
        f1 = 2 * precision * recall / (precision + recall) if precision + recall > 0 else 0.0

    return SetMetric(
        name=name,
        matched=len(matched),
        predicted=len(predicted_set),
        expected=len(expected_set),
        precision=precision,
        recall=recall,
        f1=f1,
        missing=sorted(expected_set - predicted_set),
        unexpected=sorted(predicted_set - expected_set),
        matched_values=sorted(matched),
        predicted_values=sorted(predicted_set),
        expected_values=sorted(expected_set),
    )


def _iter_named_evidence(
    extraction: TestMethodExtraction,
) -> Iterable[tuple[str, SourceEvidence]]:
    document = extraction.document
    for evidence in document.evidence:
        yield f"document · {document.title}", evidence
    for item in extraction.test_items:
        for evidence in item.evidence:
            yield f"test item · {item.normalized_name}", evidence
    for method in extraction.methods:
        method_label = f"method {method.method_id} · {method.name}"
        for evidence in method.evidence:
            yield method_label, evidence
        for exposure in method.exposures:
            for evidence in exposure.evidence:
                yield f"exposure {method.method_id} · {exposure.name}", evidence
            for condition in exposure.conditions:
                for evidence in condition.evidence:
                    yield f"condition {method.method_id} · {condition.name}", evidence
        for parameter in method.parameters:
            for evidence in parameter.evidence:
                yield f"parameter {method.method_id} · {parameter.name}", evidence
        for level in method.classifications:
            for evidence in level.evidence:
                yield f"classification {method.method_id} · {level.label}", evidence
    for reference in extraction.referenced_documents:
        for evidence in reference.evidence:
            yield f"reference · {reference.code}", evidence
    for criterion in extraction.acceptance_criteria:
        for evidence in criterion.evidence:
            yield f"acceptance criterion · {criterion.parameter}", evidence


def _evaluate_evidence_grounding(
    extraction: TestMethodExtraction,
    document: IngestedDocument,
) -> EvidenceGrounding:
    pages = {
        page.page_number: _normalize(page.text)
        for page in document.pages
    }
    evidence_items = list(_iter_named_evidence(extraction))
    ungrounded: list[str] = []
    for label, evidence in evidence_items:
        page_text = pages.get(evidence.page)
        quote = _normalize(evidence.quote)
        if page_text is None or quote not in page_text:
            ungrounded.append(
                f"{label} | page {evidence.page} | {evidence.quote}"
            )
    grounded = len(evidence_items) - len(ungrounded)
    return EvidenceGrounding(
        grounded=grounded,
        total=len(evidence_items),
        rate=(grounded / len(evidence_items) if evidence_items else None),
        ungrounded=ungrounded,
    )


class TestMethodEvaluator:
    """Compare semantically important fields without hiding errors in one score."""

    def evaluate(
        self,
        prediction: TestMethodExtraction,
        ground_truth: TestMethodExtraction,
        *,
        source_document: IngestedDocument | None = None,
    ) -> EvaluationReport:
        extractors: list[
            tuple[
                str,
                Callable[[TestMethodExtraction], Iterable[str]],
            ]
        ] = [
            ("document_identity", self._document_identity),
            ("test_items", self._test_items),
            ("methods", self._methods),
            ("exposures", self._exposures),
            ("exposure_cycles", self._exposure_cycles),
            ("procedure_conditions", self._conditions),
            ("procedure_condition_values", self._condition_values),
            ("parameters", self._parameters),
            ("parameter_units", self._parameter_units),
            ("classification_labels", self._classification_labels),
            ("referenced_documents", self._referenced_documents),
            ("acceptance_criteria", self._acceptance_criteria),
        ]
        metrics = [
            _metric(name, extractor(prediction), extractor(ground_truth))
            for name, extractor in extractors
        ]

        expected_acceptance = set(self._acceptance_criteria(ground_truth))
        predicted_acceptance = set(self._acceptance_criteria(prediction))

        return EvaluationReport(
            schema_version=prediction.schema_version,
            metrics=metrics,
            unsupported_acceptance_criteria=len(predicted_acceptance - expected_acceptance),
            evaluator_version="0.3",
            evidence_grounding=(
                _evaluate_evidence_grounding(prediction, source_document)
                if source_document is not None
                else None
            ),
        )

    @staticmethod
    def _document_identity(extraction: TestMethodExtraction) -> Iterable[str]:
        document = extraction.document
        values = {
            "document_type": document.document_type,
            "designation": _normalize_designation(document.designation, document.version),
            "title": document.title,
            "version": document.version,
            "organization": document.organization,
            "is_fictional": document.is_fictional,
        }
        return [
            f"{key}={value if key == 'designation' else _normalize(value)}"
            for key, value in values.items()
        ]

    @staticmethod
    def _test_items(extraction: TestMethodExtraction) -> Iterable[str]:
        return [_normalize(item.normalized_name) for item in extraction.test_items]

    @staticmethod
    def _methods(extraction: TestMethodExtraction) -> Iterable[str]:
        return [
            "|".join(
                [
                    _normalize_method_id(method.method_id),
                    _normalize_method_name(method.name, method.method_id),
                ]
            )
            for method in extraction.methods
        ]

    @staticmethod
    def _exposures(extraction: TestMethodExtraction) -> Iterable[str]:
        return [
            f"{_normalize_method_id(method.method_id)}|{_normalize(exposure.name)}|{_normalize(exposure.cycle_count)}"
            for method in extraction.methods
            for exposure in method.exposures
        ]

    @staticmethod
    def _exposure_cycles(extraction: TestMethodExtraction) -> Iterable[str]:
        return [
            f"{_normalize_method_id(method.method_id)}|{_normalize(exposure.cycle_count)}"
            for method in extraction.methods
            for exposure in method.exposures
        ]

    @staticmethod
    def _conditions(extraction: TestMethodExtraction) -> Iterable[str]:
        return [
            "|".join(
                [
                    _normalize_method_id(method.method_id),
                    _normalize(condition.name),
                    _normalize(condition.raw_value),
                    _normalize_unit(condition.unit),
                ]
            )
            for method in extraction.methods
            for exposure in method.exposures
            for condition in exposure.conditions
        ]

    @staticmethod
    def _condition_values(extraction: TestMethodExtraction) -> Iterable[str]:
        return [
            "|".join(
                [
                    _normalize_method_id(method.method_id),
                    _normalize(condition.raw_value),
                    _normalize_unit(condition.unit),
                ]
            )
            for method in extraction.methods
            for exposure in method.exposures
            for condition in exposure.conditions
        ]

    @staticmethod
    def _parameters(extraction: TestMethodExtraction) -> Iterable[str]:
        return [
            f"{_normalize_method_id(method.method_id)}|{_normalize_parameter_name(parameter.name)}"
            for method in extraction.methods
            for parameter in method.parameters
        ]

    @staticmethod
    def _parameter_units(extraction: TestMethodExtraction) -> Iterable[str]:
        return [
            "|".join(
                [
                    _normalize_method_id(method.method_id),
                    _normalize_parameter_name(parameter.name),
                    _normalize_unit(parameter.unit),
                ]
            )
            for method in extraction.methods
            for parameter in method.parameters
        ]

    @staticmethod
    def _classification_labels(extraction: TestMethodExtraction) -> Iterable[str]:
        return [
            f"{_normalize_method_id(method.method_id)}|{_normalize(level.label)}"
            for method in extraction.methods
            for level in method.classifications
        ]

    @staticmethod
    def _referenced_documents(extraction: TestMethodExtraction) -> Iterable[str]:
        return [_normalize(reference.code) for reference in extraction.referenced_documents]

    @staticmethod
    def _acceptance_criteria(extraction: TestMethodExtraction) -> Iterable[str]:
        return [
            json.dumps(
                {
                    "method_id": _normalize_method_id(criterion.method_id),
                    "parameter": _normalize(criterion.parameter),
                    "operator": _normalize(criterion.operator),
                    "value": _normalize(criterion.value),
                    "unit": _normalize(criterion.unit),
                },
                sort_keys=True,
            )
            for criterion in extraction.acceptance_criteria
        ]
