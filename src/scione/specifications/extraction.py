"""Explicitly bounded customer extraction using provider-neutral adapters."""

from __future__ import annotations

import json
import re
from collections import Counter
from datetime import UTC, datetime
from hashlib import sha256
from time import perf_counter
from typing import Any, Literal
from uuid import uuid4

from pydantic import ValidationError

from scione.catalogs.extraction_context import extraction_catalog_guidance
from scione.inference_progress import ProgressProvider, ProgressReporter
from scione.ingestion.context_inventory import source_context_guidance
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
from scione.source_text import citation_contains
from scione.specifications.atom_citations import enrich_atom_citations
from scione.specifications.attempts import (
    ExtractionAttemptCall,
    ExtractionFailureArtifact,
    GenerationFailure,
)
from scione.specifications.chunk_aggregation import merge_chunk_extractions
from scione.specifications.chunk_execution import generate_chunk_wave
from scione.specifications.context_coverage import source_context_coverage_issues
from scione.specifications.inventory import (
    INVENTORY_GROUNDING_POLICY_VERSION,
    INVENTORY_PROMPT_VERSION,
    INVENTORY_SYSTEM_PROMPT,
    CustomerTestInventory,
    ground_inventory,
    inventory_guidance,
    parse_inventory_payload,
    shared_applicability_has_exception,
)
from scione.specifications.method_label_grounding import enrich_method_labels
from scione.specifications.models import (
    CompactCustomerExtraction,
    CustomerDocumentNoticeAudit,
    CustomerExtraction,
    CustomerExtractionCall,
    CustomerExtractionRun,
    CustomerInventoryAudit,
    CustomerRequirement,
    CustomerWireFormatAction,
    RequestedMethod,
    RequirementEvidence,
    RequirementValue,
)
from scione.specifications.named_scale_units import enrich_named_scale_citations
from scione.specifications.output_schema import constrain_compact_extraction_schema
from scione.specifications.provenance import filter_document_notices
from scione.specifications.section_scopes import source_section_intros
from scione.specifications.source_chunking import (
    MAX_SOURCE_CHUNKS,
    SOURCE_CHUNK_POLICY,
    SourceChunk,
    SourceChunkPlanningError,
    SourceContextSpan,
    _section_source_spans,
    plan_source_chunks,
)
from scione.specifications.source_grounding import enrich_stage_citations
from scione.specifications.wire_normalization import normalize_compact_containers

DEFAULT_CUSTOMER_INPUT_CHARACTER_LIMIT = 60_000
ExtractionStrategy = Literal[
    "whole_document", "focused_passes", "compact_document", "inventory_compact_document"
]
MAX_FOCUSED_CALLS = 8


def build_plan(
    document: IngestedDocument, strategy: ExtractionStrategy = "whole_document"
) -> tuple[tuple[int, ...], ...]:
    """Plan all calls before inference; focus ranges limit output, not input.

    Every call receives the complete original source. The inventory-guided
    strategy additionally plans one inventory call before this extraction plan.
    This is neither input chunking nor retrieval, and does not bypass an
    input-token quota. There are no automatic model retries.
    """

    pages = tuple(page.page_number for page in document.pages)
    if strategy in {"whole_document", "compact_document", "inventory_compact_document"}:
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
    context_issues = source_context_coverage_issues(extraction, document)
    if not missing:
        return context_issues
    examples = ", ".join(missing[:12])
    suffix = f", and {len(missing) - 12} more" if len(missing) > 12 else ""
    return [
        "Coverage review: source method references are not represented in the "
        f"extracted requested_methods: {examples}{suffix}. These may signal omitted "
        "tests, shared procedures or background references; review the source. "
        "This is a reference-coverage hint, not test recall, an accuracy score, "
        "or proof that every reference is a required test.",
        *context_issues,
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

    def __init__(
        self,
        message: str,
        *,
        raw_response: str | None = None,
        artifact: ExtractionFailureArtifact | None = None,
        error_code: str | None = None,
    ) -> None:
        super().__init__(message)
        self.raw_response = raw_response
        self.artifact = artifact
        self.error_code = error_code or (artifact.error_code if artifact else None)


def validate_customer_sources(
    extraction: CustomerExtraction,
    document: IngestedDocument,
) -> list[str]:
    """Flag literal evidence problems without claiming semantic validation.

    Whitespace wrapping differences are ignored, but spelling/case and the physical
    page must match. A found quote is not proof that a requested value is correct.
    """

    pages = {page.page_number: page.text for page in document.pages}
    issues: list[str] = []
    for requirement in extraction.requirements:
        for index, evidence in enumerate(requirement.evidence, start=1):
            prefix = f"{requirement.id} evidence {index}"
            if evidence.page > document.page_count or evidence.page not in pages:
                issues.append(
                    f"{prefix}: physical page {evidence.page} is not in the source document; "
                    "review the citation before using this requirement."
                )
            elif not citation_contains(pages[evidence.page], evidence.quote):
                issues.append(
                    f"{prefix}: quoted text was not found on physical page {evidence.page} "
                    "after whitespace normalization; review the citation and requirement."
                )
    return issues


def _expand_compact_extraction(
    compact: CompactCustomerExtraction, document: IngestedDocument | None = None
) -> CustomerExtraction:
    """Expand JSON scaffolding only; never parse, infer or normalize source values.

    The original compact provider response remains untouched in run.raw_response.
    The prediction is the review contract, with locally assigned stable row IDs.
    """

    requirements: list[CustomerRequirement] = []
    issues = list(compact.review_issues)
    document_notices = [notice.model_copy(deep=True) for notice in compact.document_notices]
    for index, original_row in enumerate(compact.requirements, start=1):
        row = original_row.model_copy(deep=True)
        identifier = f"req_{index:03d}"
        for shared in compact.shared_requirements:
            if index in shared.applies_to:
                if shared_applicability_has_exception(shared):
                    # applies_to is a model proposal, not proof that a shared
                    # exception includes this test. Keep the source and route
                    # that applicability decision to review instead of silently
                    # approving a blanket condition for every target.
                    issues.append(
                        f"{identifier}: shared source conditions contain an exception; "
                        "verify their applicability to this test before accepting the draft."
                    )
                for field in ("conditions", "criteria", "scope", "instructions", "evidence",
                              "atomic_claims"):
                    getattr(row, field).extend(getattr(shared, field))
        # The wire response is immutable. Local expansion removes only identical
        # entries accidentally emitted in both local/shared views (or twice in
        # one view). Different stages, contexts, units, pages, wording and
        # payloads survive. This is representational deduplication, not semantic
        # merging or a claim that source facts are complete.
        for field in ("conditions", "criteria", "scope", "instructions", "evidence",
                      "atomic_claims"):
            unique = []
            seen = set()
            for item in getattr(row, field):
                key = item if isinstance(item, str) else item.model_dump_json()
                if key not in seen:
                    unique.append(item)
                    seen.add(key)
            setattr(row, field, unique)
        role_evidence: list[RequirementEvidence] = []
        roles: dict[str, str] = {}
        for entry in row.method_roles:
            quote = " ".join(entry.quote.split())
            code = " ".join(entry.code.split())
            source_page = next(
                (page for page in document.pages if page.page_number == entry.page), None
            ) if document is not None else None
            verified = (
                source_page is not None
                and entry.page in {evidence.page for evidence in row.evidence}
                and citation_contains(source_page.text, entry.quote)
                and code in quote
                and len(quote.replace(code, "").strip(" .,:;")) >= 3
            )
            if not verified:
                issues.append(
                    f"{identifier}: role for {entry.code} lacks a literal, applicable source "
                    "quotation; no automatic method-role selection was accepted."
                )
                continue
            roles[entry.code] = (
                "optional customer approval required"
                if entry.role == "optional_approval_required" else entry.role
            )
            role_evidence.append(RequirementEvidence(page=entry.page, quote=entry.quote))
        requirements.append(
            CustomerRequirement(
                id=identifier,
                original_test_name=row.test,
                requested_methods=[
                    RequestedMethod(code=method, role=roles.get(method)) for method in row.methods
                ],
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
                ] + role_evidence,
                atomic_claims=[
                    claim.expand(f"{identifier}_claim_{claim_index}")
                    for claim_index, claim in enumerate(row.atomic_claims, start=1)
                ],
            )
        )
        if row.uncertainty is not None:
            issues.append(f"{identifier}: {row.uncertainty}")
        if any(claim.operator == "one_of" and not claim.values for claim in row.atomic_claims):
            issues.append(
                f"{identifier}: a model-proposed alternative set has text but no typed values; "
                "the source wording is retained as unresolved rather than inferred."
            )
    return CustomerExtraction(
        schema_version="0.1",
        document=compact.document,
        requirements=requirements,
        review_issues=issues,
        document_notices=document_notices,
    )


def _chunk_inventory_guidance(inventory: CustomerTestInventory, chunk: SourceChunk) -> str:
    """Keep only source-located guide entries whose complete evidence is supplied.

    A global inventory is not a substitute for missing chunk context. In
    particular, do not send an exception with its limiting quote removed.
    """
    supplied = {page.page_number for page in chunk.document.pages}
    visible = {page.page_number: page.text for page in chunk.document.pages}
    primary = set(chunk.primary_pages)
    selected = inventory.model_copy(deep=True)
    selected.tests = []
    selected.attachments = []
    indexes: dict[int, int] = {}
    for index, test in enumerate(inventory.tests, start=1):
        pages = {item.page for item in test.evidence}
        if pages <= supplied and pages & primary and all(
            citation_contains(visible[item.page], item.quote) for item in test.evidence
        ):
            indexes[index] = len(selected.tests) + 1
            selected.tests.append(test.model_copy(deep=True))
    if not selected.tests:
        return ""
    for attachment in inventory.attachments:
        if (
            {item.page for item in attachment.evidence} <= supplied
            and all(index in indexes for index in attachment.applies_to)
            and all(citation_contains(visible[item.page], item.quote)
                    for item in attachment.evidence)
        ):
            selected.attachments.append(attachment.model_copy(update={
                "applies_to": [indexes[index] for index in attachment.applies_to],
            }, deep=True))
    return inventory_guidance(selected).replace(
        "Check against the complete original source;",
        "Check against the supplied original source pages;",
    )


def _chunk_inventory_names(
    inventory: CustomerTestInventory, chunk: SourceChunk, *, include_context: bool = True
) -> str:
    """Compact ownership hints when full inventory quotations do not fit.

    Only independently source-located test names are referenced; the complete
    raw source remains supplied. This is not an exhaustive extraction whitelist
    and conveys no attachment, exclusion, equivalence or applicability decision.
    """
    supplied = {page.page_number for page in chunk.document.pages}
    visible = {page.page_number: page.text for page in chunk.document.pages}
    primary = set(chunk.primary_pages)
    owned, context = [], []
    for test in inventory.tests:
        pages = {item.page for item in test.evidence}
        if pages and pages <= supplied and all(
            citation_contains(visible[item.page], item.quote) for item in test.evidence
        ):
            (owned if pages & primary else context).append(test.test)
    if not owned and not context:
        return ""
    return (
        "\n<CHUNK_INVENTORY_NAMES>\n"
        "Source-located names, not facts or a whitelist. Extract PRIMARY body rows, "
        "including new names; a primary heading cannot own separate CONTEXT_ONLY rows.\n"
        + json.dumps({
            "primary_names": owned,
            **({"context_only_names": context} if include_context else {}),
        }, ensure_ascii=False, separators=(",", ":"))
        + "\n</CHUNK_INVENTORY_NAMES>\n"
    )


def _is_chunk_bookkeeping(issue: str, extraction: CustomerExtraction) -> bool:
    """Keep expected window exclusions in the audit, not as global uncertainty.

    A row-scoped or scientific warning is retained. This only recognizes
    explicit statements that other windows' context/index work was not emitted
    here; it does not certify that the eventual whole-document extraction is
    complete. Original model text remains in the per-call raw response.
    """
    if re.search(r"\breq_[A-Za-z0-9_-]+\b", issue):
        return False
    if any(re.match(rf"^{re.escape(row.original_test_name)}\s*:", issue, re.I)
           for row in extraction.requirements):
        return False
    if re.search(
        r"\b(?:illegible|unreadable|contradict\w*|conflict\w*|truncat\w*|"
        r"ambiguous|uncertain|unsupported|insufficient)\b", issue, re.I
    ):
        return False
    scoped_elsewhere = re.search(
        r"\b(?:context pages?|this chunk|supplied primary pages|future test rows|"
        r"unsupplied sections)\b", issue, re.I,
    )
    expected_omission = re.search(
        r"\b(?:not emitted|not among|future test rows)\b", issue, re.I,
    )
    other_work = re.search(
        r"\b(?:other tests|test item index|the index|future test rows)\b", issue, re.I,
    )
    return bool(scoped_elsewhere and expected_omission and other_work)


class CustomerRequirementRunner:
    """Whole-document extraction with bounded source chunks for oversized compact input.

    The configurable budget is an input-character guard over source + prompts +
    schema, not a tokenizer, provider context-window guarantee, or spending limit.
    Provider-specific token/output quotas still apply. Compact input can be split
    into fully planned page windows; original source pages are never truncated.
    """

    strategy_name = "whole_document"

    def __init__(
        self,
        provider: ModelProvider,
        *,
        max_input_characters: int = DEFAULT_CUSTOMER_INPUT_CHARACTER_LIMIT,
        max_concurrent_chunks: int = 2,
        source_pdf_bytes: bytes | None = None,
    ) -> None:
        if isinstance(max_input_characters, bool) or not isinstance(max_input_characters, int):
            raise ValueError("max_input_characters must be a positive integer.")
        if max_input_characters < 1:
            raise ValueError("max_input_characters must be a positive integer.")
        if type(max_concurrent_chunks) is not int or not 1 <= max_concurrent_chunks <= 4:
            raise ValueError("max_concurrent_chunks must be an integer between 1 and 4.")
        self.provider = provider
        self.max_input_characters = max_input_characters
        self.max_concurrent_chunks = max_concurrent_chunks
        self.source_pdf_bytes = source_pdf_bytes

    def run(
        self, document: IngestedDocument, *, strategy: ExtractionStrategy = "whole_document",
        on_progress=None,
    ) -> CustomerExtractionRun:
        activity = ProgressReporter(on_progress)
        activity.emit("planning")
        if document.extraction_status != TextExtractionStatus.READY:
            raise CustomerExtractionError(
                "Customer extraction requires ready text; received "
                f"{document.extraction_status.value}. OCR is not available in this workflow."
            )
        if not document.pages or not any(page.text.strip() for page in document.pages):
            raise CustomerExtractionError("Customer extraction requires nonempty source text.")
        if self.source_pdf_bytes is not None and (
            not isinstance(self.source_pdf_bytes, bytes)
            or sha256(self.source_pdf_bytes).hexdigest() != document.sha256
        ):
            raise CustomerExtractionError(
                "PDF bytes do not match the source document. No model call was made.",
                error_code="source_view_mismatch",
            )

        plan = build_plan(document, strategy)
        compact = strategy in {"compact_document", "inventory_compact_document"}
        guided = strategy == "inventory_compact_document"
        response_model = CompactCustomerExtraction if compact else CustomerExtraction
        wire_schema = _structured_customer_schema(response_model.model_json_schema())
        if compact:
            wire_schema = constrain_compact_extraction_schema(wire_schema)
        system_prompt = (
            load_compact_customer_requirements_system_prompt()
            if compact else load_customer_requirements_system_prompt()
        )
        section_intros = ()

        def section_context(view: IngestedDocument) -> str:
            visible = {page.page_number: page.text for page in view.pages}
            records = [{
                "section": intro.section_id, "heading": intro.section_title,
                "page": intro.page_number, "quote": intro.quote,
            } for intro in section_intros
                if intro.page_number in visible
                and citation_contains(visible[intro.page_number], intro.quote)]
            if not records:
                return ""
            return (
                "<SOURCE_SECTION_CONTEXT>\n"
                "Physical section introductions, not new customer facts. Keep section-local "
                "conditions with that section and its continuations; do not attach them "
                "to unrelated sections. Explicit source exceptions and global clauses govern.\n"
                + json.dumps(records, ensure_ascii=False, separators=(",", ":"))
                + "\n</SOURCE_SECTION_CONTEXT>"
            )

        def request_size(request: StructuredGenerationRequest) -> int:
            return (
                len(request.system_prompt) + len(request.user_prompt)
                + len(json.dumps(request.json_schema, ensure_ascii=False))
            )

        def chunk_request(chunk: SourceChunk, total: int = MAX_SOURCE_CHUNKS):
            return StructuredGenerationRequest(
                system_prompt=system_prompt,
                user_prompt=build_customer_requirements_user_prompt(
                    chunk.document, focus_pages=chunk.primary_pages,
                    chunk_index=chunk.index, chunk_count=total,
                    context_pages=chunk.context_pages,
                    fragment_pages=tuple(sorted({
                        span.page_number for span in chunk.context_source_spans
                    })),
                    section_context=section_context(chunk.document),
                ),
                schema_name="customer_requirements_compact_v0_1",
                json_schema=wire_schema,
            )

        requests = [
            StructuredGenerationRequest(
                system_prompt=system_prompt,
                user_prompt=build_customer_requirements_user_prompt(
                    document, focus_pages=pages if strategy == "focused_passes" else None
                ),
                schema_name=(
                    "customer_requirements_compact_v0_1"
                    if compact
                    else "customer_requirements_v0_1"
                ),
                json_schema=wire_schema,
            )
            for pages in plan
        ]
        inventory_request = (
            StructuredGenerationRequest(
                system_prompt=INVENTORY_SYSTEM_PROMPT,
                user_prompt=(
                    source_context_guidance(document)
                    + extraction_catalog_guidance(document)
                    + "<SOURCE_DOCUMENT>\n"
                    f"file_name: {document.file_name}\n"
                    f"page_count: {document.page_count}\n"
                    + document.as_prompt_text()
                    + "\n</SOURCE_DOCUMENT>"
                ),
                schema_name=INVENTORY_PROMPT_VERSION.replace(".", "_"),
                json_schema=_structured_customer_schema(CustomerTestInventory.model_json_schema()),
            )
            if guided else None
        )
        chunks: tuple[SourceChunk, ...] = ()
        inventory_skipped_for_budget = False
        if compact and any(request_size(item) > self.max_input_characters for item in requests):
            intro_spans = ()
            if self.source_pdf_bytes is not None:
                section_intros = source_section_intros(document, self.source_pdf_bytes)
                ranges = _section_source_spans(document)
                ends = {}
                for intro in section_intros:
                    ends[intro.section_id] = max(ends.get(intro.section_id, 0), intro.end)
                intro_spans = tuple((identifier, SourceContextSpan(
                    ranges[identifier][0].page_number,
                    ranges[identifier][0].start, end,
                )) for identifier, end in ends.items())
            try:
                chunks = plan_source_chunks(
                    document,
                    request_size=lambda chunk: request_size(chunk_request(chunk)),
                    max_input_characters=self.max_input_characters,
                    section_intro_spans=intro_spans,
                )
            except SourceChunkPlanningError as exc:
                raise CustomerExtractionError(
                    f"Customer request contains {max(request_size(item) for item in requests):,} "
                    "input characters including document, prompts, and schema. "
                    f"Complete source chunks could not fit the configured character guard: {exc}",
                    error_code="input_too_large",
                ) from exc
            plan = tuple(chunk.primary_pages for chunk in chunks)
            requests = [chunk_request(chunk, len(chunks)) for chunk in chunks]
            if inventory_request and request_size(inventory_request) > self.max_input_characters:
                # A long PDF can exceed even the lightweight whole-source inventory.
                # All source still reaches the extraction chunks; never truncate an
                # inventory to force it through the provider.
                inventory_request = None
                inventory_skipped_for_budget = True
        # Every fixed extraction request is checked before ANY network request.
        # A generated guide is checked independently before the second call; an
        # oversized guide is unused, never truncated with the source.
        for request in [*requests, *([inventory_request] if inventory_request else [])]:
            input_characters = request_size(request)
            if input_characters > self.max_input_characters:
                raise CustomerExtractionError(
                    f"Customer request contains {input_characters:,} input characters including "
                    f"document, prompts, and schema; the configured character guard is "
                    f"{self.max_input_characters:,}. No model call was made and no source was "
                    "truncated. Focused passes retain the entire input. "
                    "Use automatic compact extraction or review the configured limit. "
                    "This character guard is not a token guarantee.",
                    error_code="input_too_large",
                )
        started_at = datetime.now(UTC)
        activity.plan(
            len(requests) + int(inventory_request is not None),
            stage="inventory" if inventory_request is not None else "extracting",
        )
        extraction_provider = (
            ProgressProvider(self.provider, activity, stage="extracting")
            if on_progress is not None else self.provider
        )
        started_clock = perf_counter()
        parallel_source_chunks = bool(
            chunks and self.provider.provider_name in {"sail", "fireworks"}
        )
        wave_width = self.max_concurrent_chunks if parallel_source_chunks else 1
        extractions: list[CustomerExtraction] = []
        calls: list[CustomerExtractionCall] = []
        attempt_calls: list[ExtractionAttemptCall] = []
        wire_format_actions: list[CustomerWireFormatAction] = []
        inventory_audit: CustomerInventoryAudit | None = None
        notice_audits: list[CustomerDocumentNoticeAudit] = []
        context_only_rows: list[dict[str, Any]] = []
        chunk_scope_notes: list[dict[str, Any]] = []

        def failure(code: str, message: str, raw_response: str | None = None):
            return CustomerExtractionError(
                message,
                raw_response=raw_response,
                artifact=ExtractionFailureArtifact(
                    attempt_id=str(uuid4()),
                    document_id=document.document_id,
                    started_at=started_at,
                    finished_at=datetime.now(UTC),
                    requested_provider=self.provider.provider_name,
                    requested_model=self.provider.model_name,
                    generation_settings=self.provider.generation_settings,
                    strategy=strategy,
                    prompt_version=(
                        CUSTOMER_REQUIREMENTS_COMPACT_PROMPT_VERSION
                        if compact else CUSTOMER_REQUIREMENTS_PROMPT_VERSION
                    ),
                    error_code=code,
                    error_message=message,
                    calls=attempt_calls,
                ),
            )

        if inventory_request is not None:
            try:
                inventory_provider = (
                    ProgressProvider(self.provider, activity, stage="inventory")
                    if on_progress is not None else self.provider
                )
                inventory_response = inventory_provider.generate_structured(inventory_request)
            except ProviderError as exc:
                attempt_calls.append(ExtractionAttemptCall(
                    call_index=1, focus_pages=[p.page_number for p in document.pages],
                    request=inventory_request,
                    failure=GenerationFailure.from_provider_error(exc), purpose="inventory",
                    prompt_version=INVENTORY_PROMPT_VERSION,
                ))
                raise failure(
                    "provider_error", f"Test inventory failed: {exc}. "
                    "No extraction call or automatic retry was made."
                ) from exc
            attempt_calls.append(ExtractionAttemptCall(
                call_index=1, focus_pages=[p.page_number for p in document.pages],
                request=inventory_request,
                response=inventory_response, purpose="inventory",
                prompt_version=INVENTORY_PROMPT_VERSION,
            ))
            calls.append(CustomerExtractionCall(
                call_index=1, focus_pages=[p.page_number for p in document.pages],
                provider=inventory_response.provider,
                model=inventory_response.model, latency_ms=inventory_response.latency_ms,
                usage=inventory_response.usage, raw_response=inventory_response.raw_text,
                provider_request_id=inventory_response.provider_request_id,
                generation_settings=(
                    inventory_response.generation_settings or self.provider.generation_settings
                ), purpose="inventory", prompt_version=INVENTORY_PROMPT_VERSION,
                request=inventory_request,
            ))
            inventory_audit = CustomerInventoryAudit(
                prompt_version=INVENTORY_PROMPT_VERSION, call_index=1,
                status="unused_invalid_schema",
                grounding_policy_version=INVENTORY_GROUNDING_POLICY_VERSION,
            )
            try:
                parsed_inventory = parse_inventory_payload(inventory_response.parsed)
                inventory = parsed_inventory.inventory
            except ValidationError as exc:
                inventory_audit.issues = [
                    f"Inventory proposal had {exc.error_count()} schema errors and was unused. "
                    "The extraction still received the complete original source."
                ]
            else:
                grounded = ground_inventory(inventory, document)
                grounding_issues = []
                for issue in grounded.issues:
                    # These two messages are generated by ground_inventory,
                    # not provider prose. Its local attachment positions refer
                    # to the parsed subset; expose original raw positions.
                    attachment_issue = re.fullmatch(
                        r"Inventory attachment ([1-9]\d*)( (?:evidence [1-9]\d* is not a "
                        r"literal fragment on physical page [1-9]\d*[.]|was unused because "
                        r"not all target tests had independently located evidence[.]))", issue,
                    )
                    if attachment_issue:
                        original_index = parsed_inventory.kept_attachment_original_indices[
                            int(attachment_issue[1]) - 1
                        ]
                        issue = f"Inventory attachment {original_index}{attachment_issue[2]}"
                    grounding_issues.append(issue)
                inventory_audit.issues = [*parsed_inventory.issues, *grounding_issues]
                inventory_audit.omitted_test_indexes = list(grounded.omitted_test_indexes)
                inventory_audit.omitted_attachment_indexes = sorted({
                    *parsed_inventory.unused_attachment_indices,
                    *[
                        parsed_inventory.kept_attachment_original_indices[index - 1]
                        for index in grounded.omitted_attachment_indexes
                    ],
                })
                if not inventory.tests:
                    inventory_audit.status = "unused_empty_inventory"
                elif not grounded.proposal.tests:
                    inventory_audit.status = "unused_invalid_source"
                else:
                    guide = inventory_guidance(grounded.proposal)
                    if inventory_audit.issues:
                        guide = (
                            "\nThe inventory guide is incomplete: unlocated fragments or "
                            "their target links were omitted without repair. Read the full "
                            "original source to recover any genuine work not in this guide.\n"
                            + guide
                        )
                    used_guides = []
                    for request_index, original in enumerate(requests):
                        current_guide = guide
                        if chunks:
                            chunk = chunks[request_index]
                            candidates = [
                                _chunk_inventory_guidance(grounded.proposal, chunk),
                                _chunk_inventory_names(grounded.proposal, chunk),
                                _chunk_inventory_names(
                                    grounded.proposal, chunk, include_context=False
                                ),
                            ]
                            current_guide = next((value for value in candidates if value and
                                request_size(original.model_copy(update={
                                    "user_prompt": original.user_prompt + value,
                                })) <= self.max_input_characters), "")
                        if not current_guide:
                            continue
                        candidate = original.model_copy(update={
                            "user_prompt": original.user_prompt + current_guide,
                        })
                        if request_size(candidate) <= self.max_input_characters:
                            requests[request_index] = candidate
                            used_guides.append(current_guide)
                    if not used_guides:
                        inventory_audit.status = "unused_input_budget"
                        inventory_audit.issues.append(
                            "The inventory guide did not fit the configured input-character "
                            "budget and was unused. The original source was not truncated."
                        )
                    else:
                        inventory_audit.status = (
                            "source_verified_partial_guidance" if inventory_audit.issues
                            else "source_verified_guidance"
                        )
                        inventory_audit.guidance_sha256 = sha256(
                            "\n".join(used_guides).encode()
                        ).hexdigest()

        wave_results = []
        for pass_index, (request, pages) in enumerate(zip(requests, plan, strict=True), start=1):
            index = len(calls) + 1
            progress = (
                f"Pass {pass_index}/{len(plan)} failed after {len(calls)} completed passes. "
                "No partial extraction was accepted or saved. "
                if len(plan) > 1
                else ""
            )
            if parallel_source_chunks:
                offset = pass_index - 1
                if offset % wave_width == 0:
                    activity.emit("extracting")
                    wave_results = generate_chunk_wave(
                        extraction_provider, requests[offset:offset + wave_width],
                        max_concurrent_calls=wave_width,
                    )
                    # Keep every admitted response, including a sibling that
                    # finishes after another request fails. No later wave starts
                    # until this entire wave has passed the ordinary validators.
                    for sibling, result in enumerate(wave_results):
                        attempt_calls.append(ExtractionAttemptCall(
                            call_index=index + sibling,
                            focus_pages=list(plan[offset + sibling]),
                            request=requests[offset + sibling],
                            response=None if isinstance(result, ProviderError) else result,
                            failure=(GenerationFailure.from_provider_error(result)
                                     if isinstance(result, ProviderError) else None),
                            prompt_version=CUSTOMER_REQUIREMENTS_COMPACT_PROMPT_VERSION,
                        ))
                result = wave_results[offset % wave_width]
                if isinstance(result, ProviderError):
                    raise failure(
                        "provider_error", f"{progress}Provider failed: {result}"
                    ) from result
                response = result
            else:
                try:
                    activity.emit("extracting")
                    response = extraction_provider.generate_structured(request)
                except ProviderError as exc:
                    attempt_calls.append(
                        ExtractionAttemptCall(
                            call_index=index,
                            focus_pages=list(pages),
                            request=request,
                            failure=GenerationFailure.from_provider_error(exc),
                            prompt_version=(CUSTOMER_REQUIREMENTS_COMPACT_PROMPT_VERSION if compact
                                            else CUSTOMER_REQUIREMENTS_PROMPT_VERSION),
                        )
                    )
                    raise failure("provider_error", f"{progress}Provider failed: {exc}") from exc
                attempt_calls.append(
                    ExtractionAttemptCall(
                        call_index=index,
                        focus_pages=list(pages),
                        request=request,
                        response=response,
                        prompt_version=(CUSTOMER_REQUIREMENTS_COMPACT_PROMPT_VERSION if compact
                                        else CUSTOMER_REQUIREMENTS_PROMPT_VERSION),
                    )
                )
            activity.emit("validating")
            try:
                parsed = response.parsed
                if compact:
                    parsed, cleanups = normalize_compact_containers(parsed, call_index=index)
                    wire_format_actions.extend(cleanups)
                partial = (
                    _expand_compact_extraction(
                        CompactCustomerExtraction.model_validate(parsed),
                        chunks[pass_index - 1].document if chunks else document,
                    )
                    if compact
                    else CustomerExtraction.model_validate(response.parsed)
                )
            except ValidationError as exc:
                raise failure(
                    "invalid_output_schema",
                    f"{progress}Provider output did not conform to {response_model.__name__} v0.1 "
                    f"({exc.error_count()} validation errors). No partial rows were accepted; "
                    "inspect the raw response and retry explicitly if appropriate.",
                    raw_response=response.raw_text,
                ) from exc
            if chunks:
                chunk = chunks[pass_index - 1]
                supplied_pages = {page.page_number for page in chunk.document.pages}
                cited_pages = {
                    evidence.page
                    for requirement in partial.requirements for evidence in requirement.evidence
                } | {
                    page for requirement in partial.requirements
                    for claim in requirement.atomic_claims for page in claim.source_pages
                } | {notice.page for notice in partial.document_notices}
                if not cited_pages <= supplied_pages:
                    raise failure(
                        "chunk_source_scope_error",
                        f"{progress}A chunk response cited physical pages not supplied to it: "
                        f"{sorted(cited_pages - supplied_pages)}. "
                        "No partial extraction was accepted.",
                        raw_response=response.raw_text,
                    )
                # A referenced context page can contain an exact original
                # subsection rather than unrelated neighboring sections. A
                # quotation that occurs only in its omitted text was not seen
                # in this request, even though that physical page was supplied.
                restricted = {
                    span.page_number for span in chunk.context_source_spans
                }
                originals = {page.page_number: page.text for page in document.pages}
                supplied = {page.page_number: page.text for page in chunk.document.pages}
                unseen = [
                    evidence.page
                    for requirement in partial.requirements
                    for evidence in requirement.evidence
                    if evidence.page in restricted
                    and citation_contains(originals[evidence.page], evidence.quote)
                    and not citation_contains(supplied[evidence.page], evidence.quote)
                ]
                if unseen:
                    raise failure(
                        "chunk_source_scope_error",
                        f"{progress}A chunk response quoted text outside its supplied "
                        f"context fragments on physical pages {sorted(set(unseen))}. "
                        "No partial extraction was accepted.",
                        raw_response=response.raw_text,
                    )
                # Raw context is repeated for interpretation, not independently
                # owned output. A test on the primary pages can cite an earlier
                # supporting page; the minimum citation is not its ownership.
                kept = []
                removed_ids = set()
                for requirement in partial.requirements:
                    evidence_pages = {item.page for item in requirement.evidence}
                    if evidence_pages & set(chunk.primary_pages):
                        kept.append(requirement)
                    else:
                        removed_ids.add(requirement.id)
                        context_only_rows.append({
                            "chunk_index": chunk.index, "requirement_id": requirement.id,
                            "test_name": requirement.original_test_name,
                            "evidence_pages": sorted(evidence_pages),
                        })
                retained_ids = {item.id for item in kept}
                retained_issues = []
                for issue in partial.review_issues:
                    if _is_chunk_bookkeeping(issue, partial):
                        chunk_scope_notes.append({"chunk_index": chunk.index, "note": issue})
                        continue
                    mentioned_ids = set(re.findall(
                        r"(?<![\w-])req_[A-Za-z0-9_-]+(?![\w-])", issue
                    ))
                    if mentioned_ids & removed_ids:
                        if not mentioned_ids & retained_ids:
                            continue
                        for row_id in removed_ids:
                            issue = re.sub(
                                rf"(?<![\w-]){re.escape(row_id)}(?![\w-])",
                                "an overlapping context-only requirement", issue,
                            )
                    retained_issues.append(issue)
                partial = partial.model_copy(update={
                    "requirements": kept,
                    "review_issues": retained_issues,
                })
            extractions.append(partial)
            partial, current_notice_audits = filter_document_notices(
                partial, document, call_index=index
            )
            extractions[-1] = partial
            notice_audits.extend(current_notice_audits)
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
                    generation_settings=(
                        response.generation_settings or self.provider.generation_settings
                    ),
                    prompt_version=(CUSTOMER_REQUIREMENTS_COMPACT_PROMPT_VERSION if compact
                                    else CUSTOMER_REQUIREMENTS_PROMPT_VERSION),
                    request=request if guided or chunks else None,
                )
            )

        extraction = (
            merge_chunk_extractions(extractions) if chunks else
            self._aggregate(extractions) if strategy == "focused_passes" else extractions[0]
        )

        if not extraction.requirements:
            raise failure(
                "empty_extraction",
                "No customer test requirements were extracted. This is not a completed "
                "analysis; review the source and model response before trying again.",
                raw_response=calls[-1].raw_response,
            )

        for issue in validate_customer_sources(extraction, document):
            if issue not in extraction.review_issues:
                extraction.review_issues.append(issue)

        extraction, grounding_actions = enrich_stage_citations(extraction, document)
        extraction, atom_actions = enrich_atom_citations(extraction, document)
        grounding_actions.extend(atom_actions)
        extraction, unit_actions = enrich_named_scale_citations(extraction, document)
        grounding_actions.extend(unit_actions)
        extraction, method_actions = enrich_method_labels(extraction, document)
        grounding_actions.extend(method_actions)

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
            generation_settings=calls[0].generation_settings,
            latency_ms=(
                (perf_counter() - started_clock) * 1000 if parallel_source_chunks
                else sum(call.latency_ms for call in calls)
            ),
            usage=self._sum_usage(calls),
            raw_response=(
                calls[0].raw_response
                if len(calls) == 1
                else json.dumps(
                    {
                        "kind": "inventory_guided_manifest" if guided else "focused_pass_manifest",
                        "description": "Local manifest, NOT one provider's raw response. "
                        "Each untouched response is stored in run.calls[index].raw_response.",
                        **({
                            "input_partitioning": {
                                "policy": SOURCE_CHUNK_POLICY,
                                "original_page_count": document.page_count,
                                "inventory_skipped_for_budget": inventory_skipped_for_budget,
                                "referenced_context_policy": "exact_section_spans_v1",
                                "max_concurrent_chunks": wave_width,
                                "latency_semantics": (
                                    "wall_time_ms" if parallel_source_chunks else "sum_call_ms"
                                ),
                                "chunks": [{
                                    "index": chunk.index,
                                    "primary_pages": list(chunk.primary_pages),
                                    "context_pages": list(chunk.context_pages),
                                    "shared_context_pages": list(chunk.shared_context_pages),
                                    "referenced_context_pages": list(
                                        chunk.referenced_context_pages
                                    ),
                                    "section_context_pages": list(chunk.section_context_pages),
                                    "context_source_spans": [{
                                        "page_number": span.page_number,
                                        "start": span.start,
                                        "end": span.end,
                                    } for span in chunk.context_source_spans],
                                    "supplied_pages": [p.page_number for p in chunk.document.pages],
                                } for chunk in chunks],
                                "context_only_rows_omitted": context_only_rows,
                                "chunk_scope_notes": chunk_scope_notes,
                                "parameter_vocabulary_format": "ids_and_labels",
                            },
                        } if chunks else {}),
                        "calls": [
                            {
                                "call_index": call.call_index,
                                "focus_pages": call.focus_pages,
                                "purpose": call.purpose,
                                "prompt_version": call.prompt_version,
                                "raw_response_path": f"calls[{index}].raw_response",
                            }
                            for index, call in enumerate(calls)
                        ],
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            ),
            raw_response_kind=(
                "provider_text" if len(calls) == 1 else
                "inventory_guided_manifest" if guided else "focused_pass_manifest"
            ),
            calls=calls,
            provider_request_id=calls[0].provider_request_id if len(calls) == 1 else None,
            grounding_actions=grounding_actions,
            wire_format_actions=wire_format_actions,
            inventory_audit=inventory_audit,
            notice_audits=notice_audits,
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
        notice_keys = set()
        result.document_notices = []
        for part in parts:
            for notice in part.document_notices:
                key = notice.model_dump_json()
                if key not in notice_keys:
                    result.document_notices.append(notice.model_copy(deep=True))
                    notice_keys.add(key)
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
