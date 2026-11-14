"""Reproducible comparison-policy checks and replay of saved matching decisions.

This evaluates decisions against supplied labels, not PDF/LLM extraction quality.
The bundled fixtures are hand-authored development examples, not independently
validated ground truth. Unknown outputs are counted separately from automation;
a system that defers everything cannot claim perfect selective accuracy.
"""

from __future__ import annotations

import hashlib
import importlib.abc
import json
import socket
import sys
from collections import Counter
from contextlib import ExitStack, contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal, Self
from unittest.mock import patch

from pydantic import BaseModel, ConfigDict, Field, model_validator

from scione.structured.models import (
    NORMALIZER_VERSION,
    AtomicClaim,
    NormalizedRequirement,
    StructuredComparison,
)

Decision = Literal["supported", "gap", "unknown", "informational"]
Overall = Literal["fully_matched", "partially_matched", "not_matched", "needs_information"]
DECISIONS = ("supported", "gap", "unknown", "informational")


class EvaluationCase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1)
    family: str = Field(min_length=1)
    split: str = Field(min_length=1)
    requirement_statement: str = Field(min_length=1)
    lab_statement: str = Field(min_length=1)
    expected_checks: dict[str, Decision] = Field(min_length=1)
    rationale: str = Field(min_length=1)
    method_status: Literal["supported", "gap", "unknown"] = "supported"
    expected_overall: Overall | None = None
    normalized: NormalizedRequirement | None = None
    lab_claims: list[AtomicClaim] = Field(default_factory=list)
    artifact_requirement_id: str | None = Field(default=None, min_length=1)
    artifact_claim_ids: dict[str, str] = Field(default_factory=dict)

    @model_validator(mode="after")
    def check_claim_ids(self) -> Self:
        if any(not key.strip() for key in self.expected_checks):
            raise ValueError("Expected decision IDs must not be blank.")
        if self.artifact_claim_ids.keys() - self.expected_checks.keys():
            raise ValueError("Artifact mappings must identify a labelled decision.")
        mapped = [self.artifact_claim_ids.get(key, key) for key in self.expected_checks]
        if any(not key.strip() for key in mapped) or len(mapped) != len(set(mapped)):
            raise ValueError("Artifact claim mappings must be nonblank and one-to-one.")
        if self.normalized is not None:
            ids = [claim.id for claim in self.normalized.claims]
            if len(ids) != len(set(ids)):
                raise ValueError("Requirement claim IDs must be unique within a case.")
            if set(self.expected_checks) != set(ids):
                raise ValueError(
                    "Label every supplied requirement claim exactly once; "
                    "use a separately scoped dataset for a subset evaluation."
                )
        lab_ids = [claim.id for claim in self.lab_claims]
        if len(lab_ids) != len(set(lab_ids)):
            raise ValueError("Lab claim IDs must be unique within a case.")
        return self


class EvaluationDataset(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["1.0"]
    dataset_id: str = Field(min_length=1)
    annotation_status: Literal[
        "author_written_unreviewed", "independently_reviewed", "user_supplied_unreviewed"
    ]
    scope: str = Field(min_length=1)
    limitations: list[str] = Field(min_length=1)
    cases: list[EvaluationCase] = Field(min_length=1)

    @model_validator(mode="after")
    def unique_cases(self) -> Self:
        ids = [case.id for case in self.cases]
        if len(ids) != len(set(ids)):
            raise ValueError("Evaluation case IDs must be unique.")
        return self


class CasePrediction(BaseModel):
    """Small replay contract, keyed by stable case and requirement-claim IDs.

    Existing saved StructuredComparison objects can be wrapped in `comparison`.
    No free-text LLM output is interpreted or converted into a decision here.
    """

    model_config = ConfigDict(extra="forbid")

    case_id: str = Field(min_length=1)
    checks: dict[str, Decision] = Field(default_factory=dict)
    overall: Overall | None = None
    reasons: dict[str, str] = Field(default_factory=dict)
    error: str | None = None
    comparison: StructuredComparison | None = None

    @model_validator(mode="after")
    def expand_comparison(self) -> Self:
        if self.comparison is not None:
            ids = [check.id for check in self.comparison.checks]
            if len(ids) != len(set(ids)):
                raise ValueError("Saved comparison contains duplicate decision IDs.")
            expanded = {check.id: check.status for check in self.comparison.checks}
            if self.checks and self.checks != expanded:
                raise ValueError("Prediction checks disagree with the saved comparison.")
            if self.overall is not None and self.overall != self.comparison.status:
                raise ValueError("Prediction overall status disagrees with the saved comparison.")
            self.checks = expanded
            self.overall = self.comparison.status
            self.reasons = {check.id: check.reason for check in self.comparison.checks}
        return self


class PredictionSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["1.0"] = "1.0"
    strategy: str = Field(min_length=1)
    input_kind: Literal["supplied_typed_facts", "saved_output_replay"] = "supplied_typed_facts"
    captured_at: str = Field(min_length=1)
    dataset_id: str = Field(min_length=1)
    dataset_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    source_path: str | None = None
    source_sha256: str | None = None
    source_policy: dict[str, Any] = Field(default_factory=dict)
    annotation_status: str | None = None
    label_corrections: list[dict[str, Any]] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
    predictions: list[CasePrediction] = Field(min_length=1)

    @model_validator(mode="after")
    def unique_predictions(self) -> Self:
        ids = [prediction.case_id for prediction in self.predictions]
        if len(ids) != len(set(ids)):
            raise ValueError(
                "Prediction case IDs must be unique; duplicate outputs cannot be scored."
            )
        return self


def dataset_sha256(dataset: EvaluationDataset) -> str:
    """Hash semantic fixture data; source byte hashes are separately preserved."""
    serialized = json.dumps(
        dataset.model_dump(mode="json"), sort_keys=True, ensure_ascii=False, separators=(",", ":")
    )
    return hashlib.sha256(serialized.encode()).hexdigest()


def load_dataset(path: Path | str) -> EvaluationDataset:
    return EvaluationDataset.model_validate_json(Path(path).read_text(encoding="utf-8"))


def load_snapshot(path: Path | str) -> PredictionSnapshot:
    return PredictionSnapshot.model_validate_json(Path(path).read_text(encoding="utf-8"))


class _ForbidProviderImports(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname == "scione.config" or fullname.startswith("scione.providers"):
            raise RuntimeError(
                "Offline evaluation must not load API providers or application settings."
            )
        return None


@contextmanager
def offline_guard():
    """Forbid provider/settings imports and network connection attempts in a run."""

    def no_network(*args, **kwargs):
        raise RuntimeError("Network access is forbidden during offline evaluation.")

    finder = _ForbidProviderImports()
    sys.meta_path.insert(0, finder)
    try:
        with ExitStack() as stack:
            stack.enter_context(patch.object(socket.socket, "connect", no_network))
            stack.enter_context(patch.object(socket.socket, "connect_ex", no_network))
            stack.enter_context(patch.object(socket, "create_connection", no_network))
            yield
    finally:
        sys.meta_path.remove(finder)


def _source_policy() -> dict[str, Any]:
    source_root = Path(__file__).resolve().parents[1]
    paths = [source_root / "structured" / "comparison.py", source_root / "structured" / "models.py"]
    parameter_root = source_root / "parameters"
    if parameter_root.is_dir():
        paths.extend(sorted(parameter_root.rglob("*.py")))
    return {
        "normalizer_version": NORMALIZER_VERSION,
        "parameter_registry_version": getattr(
            sys.modules.get("scione.parameters"), "REGISTRY_VERSION", None
        ),
        "source_sha256": {
            str(path.relative_to(source_root.parent)): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in paths
        },
    }


def run_current_comparator(
    dataset: EvaluationDataset,
    *,
    strategy: str = "current_comparator",
    fixture_path: Path | None = None,
) -> PredictionSnapshot:
    """Run the real local comparator on supplied facts, without model calls.

    Case exceptions remain visible as errors. They do not disappear from the
    accuracy/coverage denominator or turn into legitimate unknown decisions.
    """
    predictions = []
    with offline_guard():
        from scione.structured.comparison import compare_requirement

        policy = _source_policy()
        for case in dataset.cases:
            if case.normalized is None:
                raise ValueError("Current comparator runs require typed facts in every case.")
            try:
                comparison = compare_requirement(
                    case.normalized, case.lab_claims, method_status=case.method_status
                )
                predictions.append(CasePrediction(case_id=case.id, comparison=comparison))
            except Exception as error:
                predictions.append(
                    CasePrediction(case_id=case.id, error=f"{type(error).__name__}: {error}")
                )
        if policy != _source_policy():
            raise ValueError(
                "Comparison policy source changed during the run. Retry on stable code."
            )
    source = Path(__file__).resolve().parents[1] / "structured" / "comparison.py"
    return PredictionSnapshot(
        strategy=strategy,
        captured_at=datetime.now(UTC).isoformat(),
        dataset_id=dataset.dataset_id,
        dataset_sha256=(
            hashlib.sha256(fixture_path.read_bytes()).hexdigest()
            if fixture_path is not None
            else dataset_sha256(dataset)
        ),
        source_path="src/scione/structured/comparison.py",
        source_sha256=hashlib.sha256(source.read_bytes()).hexdigest(),
        source_policy=policy,
        annotation_status=dataset.annotation_status,
        metadata={"model_calls": 0, "network_allowed": False},
        predictions=predictions,
    )


def snapshot_from_matching_artifacts(
    dataset: EvaluationDataset,
    path: Path | str,
    *,
    fixture_sha256: str | None = None,
) -> PredictionSnapshot:
    """Replay the API's accepted machine comparison, without re-running anything.

    Label case/claim IDs identify the original requirement/claim IDs, unless an
    explicit one-to-one reviewer mapping is supplied. Unlabelled extra outputs
    are rejected, rather than silently removed from the evaluation universe.
    Human report decisions and semantic proposals are never used as predictions.
    """
    raw = Path(path).read_bytes()
    artifact = json.loads(raw)
    if not isinstance(artifact, dict):
        raise ValueError("Matching artifact must be a JSON object.")
    manifest = artifact.get("manifest")
    comparisons = artifact.get("comparison_validated")
    machine = artifact.get("matching_machine")
    if not isinstance(manifest, dict) or not isinstance(comparisons, list):
        raise ValueError("Expected a matching artifact manifest and comparison_validated list.")
    if not isinstance(machine, dict):
        raise ValueError("Expected the original matching_machine provenance object.")
    status = manifest.get("status")
    if status not in {"completed", "failed"}:
        raise ValueError("Matching artifact manifest needs an explicit completed/failed status.")
    if machine.get("status", status) != status:
        raise ValueError("Matching machine and manifest status disagree.")
    parsed = [StructuredComparison.model_validate(item) for item in comparisons]
    comparison_ids = [item.requirement_id for item in parsed]
    if len(comparison_ids) != len(set(comparison_ids)):
        raise ValueError("Matching artifact contains duplicate requirement IDs.")
    mapped_ids = [case.artifact_requirement_id or case.id for case in dataset.cases]
    if len(mapped_ids) != len(set(mapped_ids)):
        raise ValueError("Artifact requirement mappings must be one-to-one across labelled cases.")
    if extra := set(comparison_ids) - set(mapped_ids):
        raise ValueError(
            "Matching artifact has unlabelled requirements; label all exported items first: "
            + ", ".join(sorted(extra))
        )
    by_id = {item.requirement_id: item for item in parsed}
    predictions = []
    for case in dataset.cases:
        identifier = case.artifact_requirement_id or case.id
        comparison = by_id.get(identifier)
        if status == "failed" or comparison is None:
            predictions.append(
                CasePrediction(
                    case_id=case.id,
                    error=(
                        "The saved matching run failed."
                        if status == "failed"
                        else "The saved artifact has no output for this labelled requirement."
                    ),
                )
            )
            continue
        ids = [check.id for check in comparison.checks]
        if len(ids) != len(set(ids)):
            raise ValueError("Matching artifact contains duplicate claim IDs within a requirement.")
        mapped_checks = {key: case.artifact_claim_ids.get(key, key) for key in case.expected_checks}
        allowed = {*mapped_checks.values(), f"{identifier}_method"}
        if extra := set(ids) - allowed:
            raise ValueError(
                f"Requirement {identifier} has unlabelled checks: {', '.join(sorted(extra))}. "
                "Label them or use an explicitly scoped export."
            )
        checks = {check.id: check for check in comparison.checks}
        predictions.append(
            CasePrediction(
                case_id=case.id,
                checks={
                    key: checks[artifact_id].status
                    for key, artifact_id in mapped_checks.items()
                    if artifact_id in checks
                },
                reasons={
                    key: checks[artifact_id].reason
                    for key, artifact_id in mapped_checks.items()
                    if artifact_id in checks
                },
                overall=comparison.status,
            )
        )
    metadata = {
        "artifact_sha256": hashlib.sha256(raw).hexdigest(),
        "artifact_filename": Path(path).name,
        "comparison_source": "comparison_validated",
        "new_model_calls": 0,
    }
    for key in (
        "document_id",
        "source_sha256",
        "extraction_run_id",
        "matching_run_id",
        "input_fingerprint",
        "capability_revision",
        "catalog_revision",
        "review_revision",
        "normalizer_versions",
        "human_corrections_included",
    ):
        if key in manifest:
            metadata[key] = manifest[key]
    for key in ("provider", "model", "prompt_version", "validation_version"):
        if key in machine:
            metadata[key] = machine[key]
    return PredictionSnapshot(
        strategy="saved_application_matching_artifact",
        input_kind="saved_output_replay",
        captured_at=str(machine.get("finished_at") or datetime.now(UTC).isoformat()),
        dataset_id=dataset.dataset_id,
        dataset_sha256=fixture_sha256 or dataset_sha256(dataset),
        annotation_status=dataset.annotation_status,
        metadata=metadata,
        predictions=predictions,
    )


def _ratio(numerator: int, denominator: int) -> float | None:
    return numerator / denominator if denominator else None


def _metrics(assertions: list[dict[str, Any]]) -> dict[str, Any]:
    count = len(assertions)
    confusion = {expected: dict.fromkeys((*DECISIONS, "error"), 0) for expected in DECISIONS}
    for assertion in assertions:
        confusion[assertion["expected"]][assertion["predicted"]] += 1
    predicted = Counter(assertion["predicted"] for assertion in assertions)
    correct = sum(assertion["correct"] for assertion in assertions)
    supported_count = predicted["supported"]
    false_support = sum(
        assertion["predicted"] == "supported" and assertion["expected"] != "supported"
        for assertion in assertions
    )
    expected_supported = sum(assertion["expected"] == "supported" for assertion in assertions)
    true_supported = confusion["supported"]["supported"]
    decisive = predicted["supported"] + predicted["gap"]
    decisive_errors = sum(
        assertion["predicted"] in {"supported", "gap"} and not assertion["correct"]
        for assertion in assertions
    )
    expected_decidable = sum(
        assertion["expected"] in {"supported", "gap"} for assertion in assertions
    )
    correctly_decided = sum(
        assertion["expected"] in {"supported", "gap"} and assertion["correct"]
        for assertion in assertions
    )
    return {
        "assertion_count": count,
        "correct_count": correct,
        "accuracy": _ratio(correct, count),
        "predicted_supported_count": supported_count,
        "false_support_count": false_support,
        "false_support_rate": _ratio(false_support, supported_count),
        "false_support_denominator": "predicted_supported_count",
        "support_precision": _ratio(true_supported, supported_count),
        "expected_supported_count": expected_supported,
        "support_recall": _ratio(true_supported, expected_supported),
        "decisive_count": decisive,
        "automation_coverage": _ratio(decisive, count),
        "decisive_error_count": decisive_errors,
        "selective_error_rate": _ratio(decisive_errors, decisive),
        "unknown_count": predicted["unknown"],
        "unknown_rate": _ratio(predicted["unknown"], count),
        "error_count": predicted["error"],
        "error_rate": _ratio(predicted["error"], count),
        "expected_decidable_count": expected_decidable,
        "correctly_resolved_decidable_count": correctly_decided,
        "correctly_resolved_decidable_rate": _ratio(correctly_decided, expected_decidable),
        "confusion_matrix": confusion,
    }


def evaluate_snapshot(
    dataset: EvaluationDataset,
    snapshot: PredictionSnapshot,
    *,
    fixture_sha256: str | None = None,
) -> dict[str, Any]:
    """Score exact case/claim IDs against labels, preserving missing-output errors."""
    expected_hash = fixture_sha256 or dataset_sha256(dataset)
    if snapshot.dataset_id != dataset.dataset_id or snapshot.dataset_sha256 != expected_hash:
        raise ValueError(
            "Snapshot and label dataset differ. Refuse to score stale or changed cases."
        )
    cases = {case.id: case for case in dataset.cases}
    predicted = {prediction.case_id: prediction for prediction in snapshot.predictions}
    if extra := predicted.keys() - cases.keys():
        raise ValueError(f"Prediction contains unknown case IDs: {', '.join(sorted(extra))}")
    assertions = []
    case_results = []
    overall_correct = 0
    overall_count = 0
    for case in dataset.cases:
        prediction = predicted.get(case.id)
        error = "Missing case output." if prediction is None else prediction.error
        if prediction is not None:
            allowed = set(case.expected_checks)
            if case.normalized is not None:
                allowed.update(claim.id for claim in case.normalized.claims)
                allowed.add(f"{case.normalized.requirement_id}_method")
            if prediction.comparison is not None:
                allowed.add(f"{prediction.comparison.requirement_id}_method")
            if extra := prediction.checks.keys() - allowed:
                raise ValueError(
                    f"Prediction {case.id} has unlabelled check IDs: {', '.join(sorted(extra))}"
                )
        rows = []
        for check_id, expected in case.expected_checks.items():
            actual = "error" if error else prediction.checks.get(check_id, "error")
            row = {
                "case_id": case.id,
                "check_id": check_id,
                "family": case.family,
                "split": case.split,
                "expected": expected,
                "predicted": actual,
                "correct": actual == expected,
                "reason": prediction.reasons.get(check_id) if prediction is not None else None,
            }
            rows.append(row)
            assertions.append(row)
        overall_ok = None
        if case.expected_overall is not None:
            overall_count += 1
            overall_ok = bool(
                not error and prediction is not None and prediction.overall == case.expected_overall
            )
            overall_correct += overall_ok
        case_results.append(
            {
                "case_id": case.id,
                "family": case.family,
                "split": case.split,
                "requirement_statement": case.requirement_statement,
                "lab_statement": case.lab_statement,
                "rationale": case.rationale,
                "assertions": rows,
                "expected_overall": case.expected_overall,
                "predicted_overall": prediction.overall if prediction is not None else None,
                "overall_correct": overall_ok,
                "error": error,
                "correct": all(row["correct"] for row in rows) and overall_ok is not False,
            }
        )
    return {
        "strategy": snapshot.strategy,
        "input_kind": snapshot.input_kind,
        "annotation_status": dataset.annotation_status,
        "scope": dataset.scope,
        "limitations": dataset.limitations,
        "snapshot": snapshot.model_dump(mode="json", exclude={"predictions"}),
        "metrics": _metrics(assertions),
        "case_count": len(case_results),
        "case_correct_count": sum(case["correct"] for case in case_results),
        "overall_assertion_count": overall_count,
        "overall_correct_count": overall_correct,
        "overall_accuracy": _ratio(overall_correct, overall_count),
        "by_family": {
            family: _metrics([row for row in assertions if row["family"] == family])
            for family in sorted({row["family"] for row in assertions})
        },
        "by_split": {
            split: _metrics([row for row in assertions if row["split"] == split])
            for split in sorted({row["split"] for row in assertions})
        },
        "cases": case_results,
        "errors": [row for row in assertions if not row["correct"]],
        "overall_errors": [case for case in case_results if case["overall_correct"] is False],
    }


def compare_snapshots(
    dataset: EvaluationDataset,
    current: PredictionSnapshot,
    baseline: PredictionSnapshot | None = None,
    *,
    fixture_sha256: str | None = None,
) -> dict[str, Any]:
    current_result = evaluate_snapshot(dataset, current, fixture_sha256=fixture_sha256)
    result = {
        "schema_version": "1.0",
        "generated_at": datetime.now(UTC).isoformat(),
        "dataset_id": dataset.dataset_id,
        "annotation_status": dataset.annotation_status,
        "scope": dataset.scope,
        "limitations": dataset.limitations,
        "current": current_result,
        "baseline": None,
        "changes": [],
    }
    if baseline is not None:
        baseline_result = evaluate_snapshot(dataset, baseline, fixture_sha256=fixture_sha256)
        result["baseline"] = baseline_result
        previous = {case["case_id"]: case for case in baseline_result["cases"]}
        for case in current_result["cases"]:
            old = previous[case["case_id"]]
            for before, after in zip(old["assertions"], case["assertions"], strict=True):
                if before["predicted"] != after["predicted"]:
                    result["changes"].append(
                        {
                            "case_id": case["case_id"],
                            "check_id": after["check_id"],
                            "family": case["family"],
                            "expected": after["expected"],
                            "before": before["predicted"],
                            "after": after["predicted"],
                            "became_correct": after["correct"] and not before["correct"],
                            "regression": before["correct"] and not after["correct"],
                        }
                    )
    return result


def _percent(value: float | None) -> str:
    return "n/a (zero denominator)" if value is None else f"{100 * value:.1f}%"


def report_markdown(report: dict[str, Any]) -> str:
    current = report["current"]
    baseline = report["baseline"]
    lines = [
        "# Offline capability comparison evaluation",
        "",
        f"Dataset: `{report['dataset_id']}`. Annotation status: `{report['annotation_status']}`.",
        "",
        report["scope"],
        "",
        *[f"- {limitation}" for limitation in report["limitations"]],
        "",
        "These fixture results are development evidence, not a client accuracy claim. "
        "Zero model/API calls are made. A saved-output replay also makes no new calls.",
        "",
        "| Metric | Baseline | Current |",
        "| --- | ---: | ---: |",
    ]
    for label, key in [
        ("Parameter decision accuracy", "accuracy"),
        ("Automation coverage (supported or gap / all assertions)", "automation_coverage"),
        ("Unknown rate", "unknown_rate"),
        ("Error rate (missing/failed output)", "error_rate"),
        ("Error rate among automated decisions", "selective_error_rate"),
        ("False support rate (wrong supported / predicted supported)", "false_support_rate"),
        ("Support recall", "support_recall"),
        ("Correctly resolved / expected decidable", "correctly_resolved_decidable_rate"),
    ]:
        before = _percent(baseline["metrics"][key]) if baseline else "—"
        lines.append(f"| {label} | {before} | {_percent(current['metrics'][key])} |")
    metrics = current["metrics"]
    lines.extend(
        [
            "",
            f"Current: {metrics['correct_count']}/{metrics['assertion_count']} correct parameter "
            f"decisions; {metrics['false_support_count']}/{metrics['predicted_supported_count']} "
            "false-supported decisions. A zero support denominator is reported as n/a, not 0%.",
            "",
            f"Method/configuration overall checks: {current['overall_correct_count']}/"
            f"{current['overall_assertion_count']}. Supplied method statuses test gate "
            "propagation; they do not evaluate method identity resolution.",
            "",
            "## Per-family results",
            "",
            "| Family | Correct / total | Automation coverage | False support / predicted support "
            "|",
            "| --- | ---: | ---: | ---: |",
        ]
    )
    for family, family_metrics in current["by_family"].items():
        lines.append(
            f"| {family} | {family_metrics['correct_count']}/{family_metrics['assertion_count']} "
            f"| {_percent(family_metrics['automation_coverage'])} "
            f"| {family_metrics['false_support_count']}/"
            f"{family_metrics['predicted_supported_count']} |"
        )
    lines.extend(
        [
            "",
            "## Author-defined splits",
            "",
            "| Split | Correct / total | Automation coverage |",
            "| --- | ---: | ---: |",
        ]
    )
    for split, split_metrics in current["by_split"].items():
        lines.append(
            f"| {split} | {split_metrics['correct_count']}/{split_metrics['assertion_count']} "
            f"| {_percent(split_metrics['automation_coverage'])} |"
        )
    lines.extend(["", "## Changed decisions", ""])
    if report["changes"]:
        lines.extend(
            f"- `{change['case_id']} / {change['check_id']}`: {change['before']} → "
            f"{change['after']}; expected {change['expected']}."
            for change in report["changes"]
        )
    else:
        lines.append("No changed parameter decisions, or no baseline supplied.")
    lines.extend(["", "## Errors against supplied labels", ""])
    if current["errors"]:
        lines.extend(
            f"- `{error['case_id']} / {error['check_id']}`: expected {error['expected']}, "
            f"received {error['predicted']}. {error['reason'] or ''}"
            for error in current["errors"]
        )
    else:
        lines.append("No parameter-decision errors on these author-written cases.")
    for case in current["overall_errors"]:
        lines.append(
            f"- `{case['case_id']}` overall gate: expected {case['expected_overall']}, "
            f"received {case['predicted_overall']}."
        )
    lines.extend(["", "## Source policy", ""])
    for label, strategy_result in (("Baseline", baseline), ("Current", current)):
        if strategy_result is not None:
            snapshot = strategy_result["snapshot"]
            version = snapshot["source_policy"].get("normalizer_version", "not recorded")
            registry = snapshot["source_policy"].get("parameter_registry_version", "not recorded")
            lines.append(
                f"- {label}: `{strategy_result['strategy']}`, normalizer `{version}`, "
                f"parameter registry `{registry}`, "
                f"comparator SHA256 `{snapshot['source_sha256'] or 'not recorded'}`."
            )
    lines.append(f"- Dataset SHA256: `{current['snapshot']['dataset_sha256']}`.")
    lines.extend(
        [
            "",
            "## Provenance",
            "",
            "The JSON report includes each source statement, expected label/rationale, predicted "
            "decision/reason, confusion matrices, family/split breakdown, timestamps, dataset "
            "hash, comparator hash and current policy source hashes. Never relabel synthetic "
            "fixtures as independent gold or mix this supplied-facts score with end-to-end "
            "PDF/AI extraction accuracy.",
        ]
    )
    return "\n".join(lines) + "\n"


def write_report(report: dict[str, Any], directory: Path | str) -> tuple[Path, Path]:
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    json_path, markdown_path = directory / "report.json", directory / "report.md"
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    markdown_path.write_text(report_markdown(report), encoding="utf-8")
    return json_path, markdown_path
