"""Transparent metrics for the v0.1 test-method extraction schema."""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Iterable

from pydantic import BaseModel, ConfigDict, Field

from scione.schemas import TestMethodExtraction


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


class EvaluationReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: str
    metrics: list[SetMetric]
    unsupported_acceptance_criteria: int = Field(ge=0)


def _normalize(value: str | int | float | None) -> str:
    if value is None:
        return "<null>"
    return re.sub(r"\s+", " ", str(value).strip().casefold())


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
    )


class TestMethodEvaluator:
    """Compare semantically important fields without hiding errors in one score."""

    def evaluate(
        self,
        prediction: TestMethodExtraction,
        ground_truth: TestMethodExtraction,
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
            ("procedure_conditions", self._conditions),
            ("parameters", self._parameters),
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
        )

    @staticmethod
    def _document_identity(extraction: TestMethodExtraction) -> Iterable[str]:
        document = extraction.document
        values = {
            "document_type": document.document_type,
            "designation": document.designation,
            "title": document.title,
            "version": document.version,
            "organization": document.organization,
            "is_fictional": document.is_fictional,
        }
        return [f"{key}={_normalize(value)}" for key, value in values.items()]

    @staticmethod
    def _test_items(extraction: TestMethodExtraction) -> Iterable[str]:
        return [_normalize(item.normalized_name) for item in extraction.test_items]

    @staticmethod
    def _methods(extraction: TestMethodExtraction) -> Iterable[str]:
        return [
            f"{_normalize(method.method_id)}|{_normalize(method.name)}"
            for method in extraction.methods
        ]

    @staticmethod
    def _exposures(extraction: TestMethodExtraction) -> Iterable[str]:
        return [
            f"{_normalize(method.method_id)}|{_normalize(exposure.name)}|{_normalize(exposure.cycle_count)}"
            for method in extraction.methods
            for exposure in method.exposures
        ]

    @staticmethod
    def _conditions(extraction: TestMethodExtraction) -> Iterable[str]:
        return [
            "|".join(
                [
                    _normalize(method.method_id),
                    _normalize(condition.name),
                    _normalize(condition.raw_value),
                    _normalize(condition.unit),
                ]
            )
            for method in extraction.methods
            for exposure in method.exposures
            for condition in exposure.conditions
        ]

    @staticmethod
    def _parameters(extraction: TestMethodExtraction) -> Iterable[str]:
        return [
            f"{_normalize(method.method_id)}|{_normalize(parameter.name)}|{_normalize(parameter.unit)}"
            for method in extraction.methods
            for parameter in method.parameters
        ]

    @staticmethod
    def _classification_labels(extraction: TestMethodExtraction) -> Iterable[str]:
        return [
            f"{_normalize(method.method_id)}|{_normalize(level.label)}"
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
                    "method_id": _normalize(criterion.method_id),
                    "parameter": _normalize(criterion.parameter),
                    "operator": _normalize(criterion.operator),
                    "value": _normalize(criterion.value),
                    "unit": _normalize(criterion.unit),
                },
                sort_keys=True,
            )
            for criterion in extraction.acceptance_criteria
        ]
