"""Explicitly bounded customer extraction using provider-neutral adapters."""

from __future__ import annotations

import json
import re
from collections import Counter
from datetime import UTC, datetime
from typing import Any, Literal
from uuid import uuid4

from pydantic import ValidationError

from scione.prompts.customer_requirements import (
    CUSTOMER_REQUIREMENTS_COMPACT_PROMPT_VERSION,
    CUSTOMER_REQUIREMENTS_PROMPT_VERSION,
    build_customer_requirements_user_prompt,
    load_compact_customer_requirements_system_prompt,
    load_customer_requirements_system_prompt,
)
from scione.providers import (
    ModelProvider,
    ProviderError,
    StructuredGenerationRequest,
    TokenUsage,
)
from scione.schemas import IngestedDocument, TextExtractionStatus
from scione.specifications.models import (
    CompactCustomerExtraction,
    CustomerExtraction,
    CustomerExtractionCall,
    CustomerExtractionRun,
    CustomerRequirement,
    RequestedMethod,
    RequirementEvidence,
    RequirementValue,
)

DEFAULT_CUSTOMER_INPUT_CHARACTER_LIMIT = 60_000
ExtractionStrategy = Literal["whole_document", "focused_passes", "compact_document"]
MAX_FOCUSED_CALLS = 8


def build_plan(
    document: IngestedDocument, strategy: ExtractionStrategy = "whole_document"
) -> tuple[tuple[int, ...], ...]:
    """Plan all calls before inference; focus ranges limit output, not input.

    Every call receives the complete original source. This is neither input
    chunking nor retrieval, and does not bypass an input-token quota.
    """

    pages = tuple(page.page_number for page in document.pages)
    if strategy in {"whole_document", "compact_document"}:
        return (pages,)
    if strategy != "focused_passes":
        raise ValueError("Unknown customer extraction strategy.")
    plan = tuple(pages[index : index + 3] for index in range(0, len(pages), 3))
    if len(plan) > MAX_FOCUSED_CALLS:
        raise CustomerExtractionError(
            f"Focused passes would require {len(plan)} model calls; the safety limit is "
            f"{MAX_FOCUSED_CALLS}. No model call was made. Use a smaller document or "
            "the whole-document baseline."
        )
    return plan


# Conservative method-reference signals only: this is not a test inventory or an
# assertion that a referenced method is itself a required standalone test.
_METHOD_REFERENCE = re.compile(
    r"\b(ISO(?:\s*/\s*IEC)?|ASTM|SAE|VDA|GB\s*/\s*T)\s*"
    r"([A-Z]?\s*\d+(?:-\d+)*)\b",
    re.IGNORECASE,
)


def _method_references(text: str) -> set[str]:
    references = set()
    for match in _METHOD_REFERENCE.finditer(text):
        organization = re.sub(r"\s+", "", match[1]).upper()
        code = re.sub(r"\s+", "", match[2]).upper()
        references.add(f"{organization} {code}")
    return references


def _reference_is_represented(reference: str, represented: set[str]) -> bool:
    # A revision/part suffix in only one location does not prove a missed test;
    # this deliberately under-reports rather than declaring such a gap certain.
    return any(
        candidate == reference
        or candidate.startswith(reference + "-")
        or reference.startswith(candidate + "-")
        for candidate in represented
    )


def coverage_issues(extraction: CustomerExtraction, document: IngestedDocument) -> list[str]:
    """Return missing-reference review hints, never a completeness score.

    This is computed from source text and can also audit existing saved runs.
    References can describe preparation, measurement or background rather than
    independently requested tests; absence must therefore be reviewed by a person.
    """

    source = _method_references("\n".join(page.text for page in document.pages))
    represented = _method_references(
        "\n".join(
            method.code
            for requirement in extraction.requirements
            for method in requirement.requested_methods
        )
    )
    missing = sorted(
        reference for reference in source if not _reference_is_represented(reference, represented)
    )
    if not missing:
        return []
    examples = ", ".join(missing[:12])
    suffix = f", and {len(missing) - 12} more" if len(missing) > 12 else ""
    return [
        "Coverage review: source method references are not represented in the "
        f"extracted requested_methods: {examples}{suffix}. These may signal omitted "
        "tests, shared procedures or background references; review the source. "
        "This is a reference-coverage hint, not test recall, an accuracy score, "
        "or proof that every reference is a required test."
    ]


def _structured_customer_schema(value: Any) -> Any:
    """Require every generated field, using explicit null/[] for unknown values.

    Local constructors keep ergonomic defaults; the model-facing schema instead
    makes all object properties required for strict structured-output adapters.
    """

    if isinstance(value, list):
        return [_structured_customer_schema(item) for item in value]
    if not isinstance(value, dict):
        return value
    prepared = {
        key: _structured_customer_schema(item) for key, item in value.items() if key != "default"
    }
    if isinstance(prepared.get("properties"), dict):
        prepared["required"] = list(prepared["properties"])
    return prepared


class CustomerExtractionError(RuntimeError):
    """A run could not yield a usable, complete-schema customer extraction.

    A failed output is retained on the exception for explicit developer inspection;
    it is never embedded in the user-facing error or accepted as a partial result.
    """

    def __init__(self, message: str, *, raw_response: str | None = None) -> None:
        super().__init__(message)
        self.raw_response = raw_response


def validate_customer_sources(
    extraction: CustomerExtraction,
    document: IngestedDocument,
) -> list[str]:
    """Flag literal evidence problems without claiming semantic validation.

    Whitespace wrapping differences are ignored, but spelling/case and the physical
    page must match. A found quote is not proof that a requested value is correct.
    """

    pages = {page.page_number: " ".join(page.text.split()) for page in document.pages}
    issues: list[str] = []
    for requirement in extraction.requirements:
        for index, evidence in enumerate(requirement.evidence, start=1):
            prefix = f"{requirement.id} evidence {index}"
            if evidence.page > document.page_count or evidence.page not in pages:
                issues.append(
                    f"{prefix}: physical page {evidence.page} is not in the source document; "
                    "review the citation before using this requirement."
                )
            elif " ".join(evidence.quote.split()) not in pages[evidence.page]:
                issues.append(
                    f"{prefix}: quoted text was not found on physical page {evidence.page} "
                    "after whitespace normalization; review the citation and requirement."
                )
    return issues


def _expand_compact_extraction(compact: CompactCustomerExtraction) -> CustomerExtraction:
    """Expand JSON scaffolding only; never parse, infer or normalize source values.

    The original compact provider response remains untouched in run.raw_response.
    The prediction is the review contract, with locally assigned stable row IDs.
    """

    requirements: list[CustomerRequirement] = []
    issues = list(compact.review_issues)
    for index, row in enumerate(compact.requirements, start=1):
        identifier = f"req_{index:03d}"
        requirements.append(
            CustomerRequirement(
                id=identifier,
                original_test_name=row.test,
                requested_methods=[RequestedMethod(code=method) for method in row.methods],
                conditions=[
                    RequirementValue(name="Customer condition", raw_value=value)
                    for value in row.conditions
                ],
                acceptance_criteria=[
                    RequirementValue(name="Customer criterion", raw_value=value)
                    for value in row.criteria
                ],
                applicability=row.scope,
                special_instructions=row.instructions,
                evidence=[
                    RequirementEvidence(page=evidence.page, quote=evidence.quote)
                    for evidence in row.evidence
                ],
            )
        )
        if row.uncertainty is not None:
            issues.append(f"{identifier}: {row.uncertainty}")
    return CustomerExtraction(
        schema_version="0.1",
        document=compact.document,
        requirements=requirements,
        review_issues=issues,
    )


class CustomerRequirementRunner:
    """Compact/rich whole-document extraction or full-context focused passes.

    The configurable budget is an input-character guard over source + prompts +
    schema, not a tokenizer, provider context-window guarantee, or spending limit.
    Provider-specific token/output quotas still apply. Oversized input is rejected
    before inference rather than silently truncating source material.
    """

    strategy_name = "whole_document"

    def __init__(
        self,
        provider: ModelProvider,
        *,
        max_input_characters: int = DEFAULT_CUSTOMER_INPUT_CHARACTER_LIMIT,
    ) -> None:
        if isinstance(max_input_characters, bool) or not isinstance(max_input_characters, int):
            raise ValueError("max_input_characters must be a positive integer.")
        if max_input_characters < 1:
            raise ValueError("max_input_characters must be a positive integer.")
        self.provider = provider
        self.max_input_characters = max_input_characters

    def run(
        self, document: IngestedDocument, *, strategy: ExtractionStrategy = "whole_document"
    ) -> CustomerExtractionRun:
        if document.extraction_status != TextExtractionStatus.READY:
            raise CustomerExtractionError(
                "Customer extraction requires ready text; received "
                f"{document.extraction_status.value}. OCR is not available in this workflow."
            )
        if not document.pages or not any(page.text.strip() for page in document.pages):
            raise CustomerExtractionError("Customer extraction requires nonempty source text.")

        plan = build_plan(document, strategy)
        compact = strategy == "compact_document"
        response_model = CompactCustomerExtraction if compact else CustomerExtraction
        requests = [
            StructuredGenerationRequest(
                system_prompt=(
                    load_compact_customer_requirements_system_prompt()
                    if compact
                    else load_customer_requirements_system_prompt()
                ),
                user_prompt=build_customer_requirements_user_prompt(
                    document, focus_pages=pages if strategy == "focused_passes" else None
                ),
                schema_name=(
                    "customer_requirements_compact_v0_1"
                    if compact
                    else "customer_requirements_v0_1"
                ),
                json_schema=_structured_customer_schema(response_model.model_json_schema()),
            )
            for pages in plan
        ]
        # Check every planned request before making ANY network request.
        for request in requests:
            input_characters = (
                len(request.system_prompt)
                + len(request.user_prompt)
                + len(json.dumps(request.json_schema, ensure_ascii=False))
            )
            if input_characters > self.max_input_characters:
                raise CustomerExtractionError(
                    f"Customer request contains {input_characters:,} input characters including "
                    f"document, prompts, and schema; the configured character guard is "
                    f"{self.max_input_characters:,}. No model call was made and no source was "
                    "truncated. Focused passes retain the entire input; input chunking is not "
                    "implemented. Select a smaller document or explicitly review the limit. "
                    "This character guard is not a token guarantee."
                )
        started_at = datetime.now(UTC)
        extractions: list[CustomerExtraction] = []
        calls: list[CustomerExtractionCall] = []
        for index, (request, pages) in enumerate(zip(requests, plan, strict=True), start=1):
            progress = (
                f"Pass {index}/{len(plan)} failed after {len(calls)} completed passes. "
                "No partial extraction was accepted or saved. "
                if len(plan) > 1
                else ""
            )
            try:
                response = self.provider.generate_structured(request)
            except ProviderError as exc:
                raise CustomerExtractionError(f"{progress}Provider failed: {exc}") from exc

            try:
                partial = (
                    _expand_compact_extraction(
                        CompactCustomerExtraction.model_validate(response.parsed)
                    )
                    if compact
                    else CustomerExtraction.model_validate(response.parsed)
                )
            except ValidationError as exc:
                raise CustomerExtractionError(
                    f"{progress}Provider output did not conform to {response_model.__name__} v0.1 "
                    f"({exc.error_count()} validation errors). No partial rows were accepted; "
                    "inspect the raw response and retry explicitly if appropriate.",
                    raw_response=response.raw_text,
                ) from exc
            extractions.append(partial)
            calls.append(
                CustomerExtractionCall(
                    call_index=index,
                    focus_pages=list(pages),
                    provider=response.provider,
                    model=response.model,
                    latency_ms=response.latency_ms,
                    usage=response.usage,
                    raw_response=response.raw_text,
                    provider_request_id=response.provider_request_id,
                )
            )

        extraction = (
            self._aggregate(extractions) if strategy == "focused_passes" else extractions[0]
        )

        if not extraction.requirements:
            raise CustomerExtractionError(
                "No customer test requirements were extracted. This is not a completed "
                "analysis; review the source and model response before trying again.",
                raw_response=calls[-1].raw_response,
            )

        for issue in validate_customer_sources(extraction, document):
            if issue not in extraction.review_issues:
                extraction.review_issues.append(issue)

        return CustomerExtractionRun(
            run_id=str(uuid4()),
            started_at=started_at,
            document_id=document.document_id,
            strategy=strategy,
            prompt_version=(
                CUSTOMER_REQUIREMENTS_COMPACT_PROMPT_VERSION
                if compact
                else CUSTOMER_REQUIREMENTS_PROMPT_VERSION
            ),
            schema_version=extraction.schema_version,
            provider=calls[0].provider,
            model=calls[0].model,
            latency_ms=sum(call.latency_ms for call in calls),
            usage=self._sum_usage(calls),
            raw_response=(
                calls[0].raw_response
                if len(calls) == 1
                else json.dumps(
                    {
                        "kind": "focused_pass_manifest",
                        "description": "Local manifest, NOT one provider's raw response. "
                        "Each untouched response is stored in run.calls[index].raw_response.",
                        "calls": [
                            {
                                "call_index": call.call_index,
                                "focus_pages": call.focus_pages,
                                "raw_response_path": f"calls[{index}].raw_response",
                            }
                            for index, call in enumerate(calls)
                        ],
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            ),
            raw_response_kind="provider_text" if len(calls) == 1 else "focused_pass_manifest",
            calls=calls,
            provider_request_id=calls[0].provider_request_id if len(calls) == 1 else None,
            extraction=extraction,
        )

    @staticmethod
    def _sum_usage(calls: list[CustomerExtractionCall]) -> TokenUsage:
        def total(field: str) -> int | None:
            values = [getattr(call.usage, field) for call in calls]
            return sum(values) if all(value is not None for value in values) else None

        return TokenUsage(
            **{field: total(field) for field in ("input_tokens", "output_tokens", "total_tokens")}
        )

    @staticmethod
    def _aggregate(parts: list[CustomerExtraction]) -> CustomerExtraction:
        """Only exact content duplicates are removed; scoped variants survive."""

        result = parts[0].model_copy(deep=True)
        result.requirements = []
        result.review_issues = list(
            dict.fromkeys(issue for part in parts for issue in part.review_issues)
        )
        seen: set[str] = set()
        for part in parts:
            if part.document != result.document:
                issue = (
                    "Focused passes returned differing document metadata; the first pass's "
                    "metadata was retained and must be checked against the source."
                )
                if issue not in result.review_issues:
                    result.review_issues.append(issue)
            for requirement in part.requirements:
                fingerprint = json.dumps(
                    requirement.model_dump(mode="json", exclude={"id"}),
                    sort_keys=True,
                    ensure_ascii=False,
                )
                if fingerprint in seen:
                    continue
                seen.add(fingerprint)
                row = requirement.model_copy(deep=True)
                row.id = f"req_{len(result.requirements) + 1:03d}"
                result.requirements.append(row)
        repeated = Counter(row.original_test_name.casefold() for row in result.requirements)
        if any(count > 1 for count in repeated.values()):
            result.review_issues.append(
                "Several retained records share a test name. They may be distinct scoped "
                "cases or overlapping focused-pass results; review them before confirmation. "
                "They were not merged by name."
            )
        return result
