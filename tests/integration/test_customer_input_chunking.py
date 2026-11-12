"""Bounded input partitioning contracts; fixtures never call a real model.

These tests establish source transport, ownership and failure behavior, not model
recall or accuracy. The bundled client PDF is used without rewriting its pages.
"""

from __future__ import annotations

import copy
import importlib
import json
import re
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest
from test_workspace_api import LocalHTTPClient

from scione.api.app import create_app
from scione.config import Settings
from scione.ingestion.pdf_text import PyMuPDFTextIngestor
from scione.providers import ProviderError, StaticModelProvider, TokenUsage
from scione.schemas import DocumentPage, IngestedDocument, TextReadingOrder
from scione.source_text import citation_contains
from scione.specifications.extraction import (
    CustomerExtractionError,
    CustomerRequirementRunner,
    _chunk_inventory_guidance,
    _chunk_inventory_names,
    _is_chunk_bookkeeping,
)
from scione.specifications.inventory import CustomerTestInventory
from scione.specifications.models import CustomerExtraction, CustomerExtractionRun
from scione.specifications.source_chunking import SourceChunk, SourceContextSpan
from scione.specifications.store import FileSpecStore

CLIENT_PDF = Path(__file__).resolve().parents[2] / "docs/samples/MS-PNT-410_demo_spec.pdf"
SHARED_CONDITION = "7 days at 23 °C, 50 % RH (ISO 3270)"


@pytest.mark.parametrize(("note", "bookkeeping"), [
    ("Other tests are on context page 7 and were not emitted in this chunk.", True),
    ("The index is not among the supplied primary pages; those tests are not emitted.", True),
    ("Applicability to future test rows in unsupplied sections cannot be resolved here.", True),
    ("Pressure water jet adhesion: distance reference is not stated in this chunk.", False),
    ("req_001: a required limit on context page 7 was not emitted.", False),
    ("A test was not emitted in this chunk because the source table is unreadable.", False),
    ("The stated cold temperature conflicts with another source limit.", False),
    ("The 60 °C condition was not emitted in this chunk because its unit is ambiguous.", False),
    ("The temperature limit was not emitted in this chunk because its applicability is uncertain.",
     False),
    ("A required pressure limit was not emitted in this chunk.", False),
])
def test_chunk_bookkeeping_never_hides_row_or_scientific_uncertainty(note, bookkeeping):
    part = CustomerExtraction.model_validate({
        "schema_version": "0.1", "document": {},
        "requirements": [{
            "id": "req_001", "original_test_name": "Pressure water jet adhesion",
            "evidence": [{"page": 6, "quote": "Pressure water jet adhesion"}],
        }],
    })
    assert _is_chunk_bookkeeping(note, part) is bookkeeping


def physical_pages(request):
    return tuple(int(value) for value in re.findall(r"=== PAGE (\d+) ===", request.user_prompt))


def primary_pages(request):
    marker = re.search(r"(?m)^primary_pages:\s*([^\n]+)", request.user_prompt)
    return (
        tuple(int(value) for value in re.findall(r"\d+", marker[1]))
        if marker
        else physical_pages(request)
    )


def request_characters(request):
    return (
        len(request.system_prompt)
        + len(request.user_prompt)
        + len(json.dumps(request.json_schema, ensure_ascii=False))
    )


def compact_payload(requirements=(), shared=()):
    return {
        "document": {"title": None, "identifier": None, "revision": None},
        "requirements": copy.deepcopy(list(requirements)),
        "shared_requirements": copy.deepcopy(list(shared)),
        "review_issues": [],
        "document_notices": [],
    }


def pencil_requirement():
    return {
        "test": "Pencil hardness",
        "methods": ["ISO 15184"],
        "method_roles": [],
        "conditions": ["750 g"],
        "criteria": ["≥ F"],
        "scope": [],
        "instructions": [],
        "atomic_claims": [],
        "uncertainty": None,
        "evidence": [{"page": 6, "quote": "Pencil hardness"}],
    }


class ChunkProvider(StaticModelProvider):
    """Returns sparse source-grounded rows to audit transport, not extraction skill."""

    def __init__(self, *, duplicate_context=False, fail_extraction=None):
        super().__init__({}, model_name="offline-chunk-regression")
        self.requests = []
        self.responses = []
        self.extraction_count = 0
        self.duplicate_context = duplicate_context
        self.fail_extraction = fail_extraction

    def generate_structured(self, request):
        self.requests.append(request)
        if request.schema_name.startswith("customer_test_inventory"):
            value = {"tests": [], "attachments": [], "uncertainties": []}
        else:
            self.extraction_count += 1
            if self.extraction_count == self.fail_extraction:
                raise ProviderError("Offline planned second-chunk failure", raw_text="STOP")
            pages = physical_pages(request)
            owned = pages if self.duplicate_context else primary_pages(request)
            requirements = [pencil_requirement()] if 6 in owned else []
            shared = []
            if requirements and 3 in pages:
                shared = [{
                    "applies_to": [1],
                    "conditions": [SHARED_CONDITION],
                    "criteria": [],
                    "scope": [],
                    "instructions": [],
                    "atomic_claims": [],
                    "evidence": [{"page": 3, "quote": SHARED_CONDITION}],
                }]
            value = compact_payload(requirements, shared)
        response = StaticModelProvider(value).generate_structured(request).model_copy(update={
            "usage": TokenUsage(input_tokens=17, output_tokens=3, total_tokens=20),
            "latency_ms": 2.5,
            "provider_request_id": f"offline-chunk-{len(self.requests)}",
        })
        self.responses.append(response)
        return response


def client_source():
    return PyMuPDFTextIngestor(reading_order=TextReadingOrder.GEOMETRIC).ingest(CLIENT_PDF)


def test_original_thirteen_page_client_source_is_completely_partitioned_before_inference():
    document = client_source()
    original = document.model_dump(mode="json")
    provider = ChunkProvider()
    run = CustomerRequirementRunner(provider, max_input_characters=60_000).run(
        document, strategy="inventory_compact_document"
    )
    extraction_calls = [call for call in run.calls if call.purpose == "extraction"]
    assert len(extraction_calls) >= 2
    assert len(extraction_calls) <= 8
    assert all(call.request is not None for call in extraction_calls)
    requests = [call.request for call in extraction_calls]
    primary = [page for request in requests for page in primary_pages(request)]
    assert Counter(primary) == Counter(range(1, 14))
    assert all(request_characters(request) <= 60_000 for request in provider.requests)
    assert all(len(set(physical_pages(request))) < 13 for request in requests)
    assert all(document.as_prompt_text() not in request.user_prompt for request in requests)
    assert all("<SOURCE_CHUNK " in request.user_prompt for request in requests)
    for call in extraction_calls:
        assert call.focus_pages == list(primary_pages(call.request))
        assert set(call.focus_pages) <= set(physical_pages(call.request))
        for page in document.pages:
            if page.page_number in call.focus_pages:
                assert (
                    f"=== PAGE {page.page_number} ===\nPAGE_ROLE: PRIMARY\n{page.text.rstrip()}"
                    in call.request.user_prompt
                )
    assert document.model_dump(mode="json") == original
    assert run.document_id == document.document_id
    assert [row.original_test_name for row in run.extraction.requirements] == ["Pencil hardness"]
    requirement = run.extraction.requirements[0]
    assert any(SHARED_CONDITION in row.raw_value for row in requirement.conditions)
    assert {3, 6} <= {evidence.page for evidence in requirement.evidence}
    assert [json.loads(call.raw_response) for call in run.calls] == [
        response.parsed for response in provider.responses
    ]
    assert run.usage.total_tokens == 20 * len(provider.requests)
    assert run.latency_ms == 2.5 * len(provider.requests)
    manifest_chunks = json.loads(run.raw_response)["input_partitioning"]["chunks"]
    assert any(chunk["context_source_spans"] for chunk in manifest_chunks)
    for chunk, call in zip(manifest_chunks, extraction_calls, strict=True):
        spans = chunk["context_source_spans"]
        for number in {span["page_number"] for span in spans}:
            assert number not in chunk["primary_pages"] + chunk["shared_context_pages"]
            text = document.pages[number - 1].text
            page_spans = [span for span in spans if span["page_number"] == number]
            assert all(0 <= span["start"] < span["end"] <= len(text) for span in page_spans)
            fragment = "".join(text[span["start"]:span["end"]] for span in page_spans)
            assert (
                f"=== PAGE {number} ===\nPAGE_ROLE: CONTEXT_ONLY\n"
                "SOURCE_VIEW: exact referenced subsection excerpts, not the whole page.\n"
                f"{fragment.rstrip()}"
                in call.request.user_prompt
            )
    assert CustomerExtractionRun.model_validate_json(run.model_dump_json()) == run


def test_physical_pdf_section_intros_reach_continued_primary_rows_in_bounded_requests():
    document = client_source()
    before = document.model_dump_json()
    provider = ChunkProvider()
    run = CustomerRequirementRunner(
        provider, max_input_characters=60_000, source_pdf_bytes=CLIENT_PDF.read_bytes(),
    ).run(document, strategy="inventory_compact_document")
    calls = [call for call in run.calls if call.purpose == "extraction"]
    assert all(request_characters(request) <= 60_000 for request in provider.requests)
    assert Counter(page for call in calls for page in primary_pages(call.request)) == Counter(
        range(1, 14)
    )
    for call in calls:
        for number in call.focus_pages:
            assert (
                f"=== PAGE {number} ===\nPAGE_ROLE: PRIMARY\n"
                + document.pages[number - 1].text.rstrip()
            ) in call.request.user_prompt

    continuation = next(call for call in calls if call.focus_pages == [9, 10, 11])
    prompt = continuation.request.user_prompt
    match = re.search(r"=== PAGE 8 ===\n(.*?)(?=\n\n=== PAGE |\n</DOCUMENT_TEXT>)", prompt, re.S)
    assert match is not None
    supplied = match[1]
    assert "PAGE_ROLE: CONTEXT_ONLY" in supplied
    assert "SOURCE_VIEW: exact referenced subsection excerpts" in supplied
    assert "4.3 Chemical and fluid resistance" in supplied
    assert "Judged 24 h after exposure on low-bake panels." in supplied
    assert "Deionized water" not in supplied and "Sulfuric acid" not in supplied
    structure = prompt.split("<SOURCE_SECTION_CONTEXT>\n", 1)[1].split(
        "\n</SOURCE_SECTION_CONTEXT>", 1,
    )[0]
    intros = json.loads(structure.splitlines()[-1])
    assert any(item["section"] == "4.3" and item["page"] == 8
               and item["quote"] == "Judged 24 h after exposure on low-bake panels."
               for item in intros)
    # Structure hints also have to be present in this actual supplied view.
    assert all(citation_contains(supplied, item["quote"])
               for item in intros if item["page"] == 8)
    chunks = json.loads(run.raw_response)["input_partitioning"]["chunks"]
    recorded = next(chunk for chunk in chunks if chunk["primary_pages"] == [9, 10, 11])
    assert recorded["section_context_pages"] == [8]
    spans = [s for s in recorded["context_source_spans"] if s["page_number"] == 8]
    assert spans
    raw_fragment = "".join(document.pages[7].text[s["start"]:s["end"]] for s in spans)
    assert "Judged 24 h after exposure on low-bake panels." in raw_fragment
    assert "Deionized water" not in raw_fragment
    assert document.model_dump_json() == before


@pytest.mark.parametrize("pdf", [b"different original PDF", "not-bytes"])
def test_wrong_physical_pdf_is_rejected_before_any_provider_request(pdf):
    provider = ChunkProvider()
    with pytest.raises(CustomerExtractionError) as caught:
        CustomerRequirementRunner(provider, source_pdf_bytes=pdf).run(
            client_source(), strategy="inventory_compact_document",
        )
    assert caught.value.error_code == "source_view_mismatch"
    assert provider.requests == []


def test_context_overlap_does_not_create_duplicate_scientific_rows():
    provider = ChunkProvider(duplicate_context=True)
    run = CustomerRequirementRunner(provider, max_input_characters=60_000).run(
        client_source(), strategy="inventory_compact_document"
    )
    names = [row.original_test_name for row in run.extraction.requirements]
    assert names == ["Pencil hardness"]
    assert len({row.id for row in run.extraction.requirements}) == len(names)


def ownership_fixture(monkeypatch, *, mixed_warning=False):
    """Fix chunk boundaries to distinguish earlier support from row ownership."""
    extraction_module = importlib.import_module("scione.specifications.extraction")
    from scione.specifications.source_chunking import SourceChunk

    prep = "Preparation: condition all specimens for 2 h at 23 °C."
    impact = "Impact resistance ISO 6272-1: no cracking at 50 cm."
    text_by_page = {
        2: "Appearance inspection: no visible defects.",
        3: "Film thickness ISO 2808: report thickness in micrometres.",
        6: prep,
        7: impact,
    }
    padding = "Reference background without additional test requirements.\n" * 60
    pages = []
    for number in range(1, 14):
        text = text_by_page.get(number, "Source documentation and background.") + "\n" + padding
        pages.append(DocumentPage(
            page_number=number, text=text, character_count=len(text),
            non_whitespace_character_count=len("".join(text.split())),
        ))
    document = IngestedDocument(
        document_id="chunk_ownership_fixture", file_name="ownership.pdf", sha256="b" * 64,
        page_count=13, extraction_status="ready", text_reading_order="geometric", pages=pages,
    )

    def controlled_chunks(source, *, request_size, max_input_characters, **_kwargs):
        chunks = []
        for start in range(1, 14, 3):
            primary = tuple(range(start, min(start + 3, 14)))
            context = tuple(sorted(({1, start - 1} - set(primary)) - {0}))
            included = set(primary) | set(context)
            chunk = SourceChunk(
                index=len(chunks) + 1, primary_pages=primary, context_pages=context,
                shared_context_pages=(1,) if 1 in context else (),
                document=source.model_copy(update={
                    "pages": [p for p in source.pages if p.page_number in included],
                }),
            )
            assert request_size(chunk) <= max_input_characters
            chunks.append(chunk)
        return tuple(chunks)

    monkeypatch.setattr(extraction_module, "plan_source_chunks", controlled_chunks)

    def row(name, evidence, methods=(), conditions=()):
        return {
            "test": name, "methods": list(methods), "method_roles": [],
            "conditions": list(conditions), "criteria": [], "scope": [], "instructions": [],
            "atomic_claims": [], "uncertainty": None, "evidence": evidence,
        }

    class OwnershipProvider(ChunkProvider):
        def generate_structured(self, request):
            if request.schema_name.startswith("customer_test_inventory"):
                return super().generate_structured(request)
            self.requests.append(request)
            self.extraction_count += 1
            primary = primary_pages(request)
            requirements = []
            issues = []
            if 2 in primary:
                requirements = [
                    row("Appearance inspection", [{"page": 2, "quote": text_by_page[2]}]),
                    row("Film thickness", [{"page": 3, "quote": text_by_page[3]}], ["ISO 2808"]),
                ]
            elif 7 in primary:
                assert 6 in physical_pages(request)
                requirements = [
                    row("Preparation context", [{"page": 6, "quote": prep}]),
                    row("Impact resistance", [
                        {"page": 6, "quote": prep}, {"page": 7, "quote": impact},
                    ], ["ISO 6272-1"], [prep]),
                ]
                if mixed_warning:
                    issues = ["req_002 differs from req_001; check the preparation scope."]
            value = compact_payload(requirements)
            value["review_issues"] = issues
            return StaticModelProvider(value).generate_structured(request)

    provider = OwnershipProvider()
    return CustomerRequirementRunner(provider).run(
        document, strategy="inventory_compact_document"
    )


def test_primary_test_survives_when_its_first_citation_is_neighboring_preparation(monkeypatch):
    run = ownership_fixture(monkeypatch)
    impact = next(
        r for r in run.extraction.requirements if r.original_test_name == "Impact resistance"
    )
    assert impact.id == "req_003"
    assert [e.page for e in impact.evidence][:2] == [6, 7]
    assert any("2 h at 23 °C" in value.raw_value for value in impact.conditions)
    assert not any(
        r.original_test_name == "Preparation context" for r in run.extraction.requirements
    )


def test_mixed_context_warning_retains_its_primary_row_with_correct_global_identifier(monkeypatch):
    run = ownership_fixture(monkeypatch, mixed_warning=True)
    warning = next(
        issue for issue in run.extraction.review_issues if "check the preparation scope" in issue
    )
    assert warning == (
        "req_003 differs from an overlapping context-only requirement; check the preparation scope."
    )
    assert "req_001" not in warning and "req_002" not in warning


def test_later_chunk_failure_preserves_exact_attempt_history_and_never_returns_partial_success():
    provider = ChunkProvider(fail_extraction=2)
    with pytest.raises(CustomerExtractionError) as captured:
        CustomerRequirementRunner(provider, max_input_characters=60_000).run(
            client_source(), strategy="inventory_compact_document"
        )
    assert provider.extraction_count == 2
    artifact = captured.value.artifact
    assert artifact is not None and artifact.status == "failed"
    assert artifact.error_code == "provider_error"
    assert len(artifact.calls) == len(provider.requests)
    assert artifact.calls[-1].failure.raw_text == "STOP"
    assert artifact.calls[-1].response is None
    assert artifact.calls[-1].request == provider.requests[-1]
    assert all(call.response is not None for call in artifact.calls[:-1])
    assert "partial" in str(captured.value).lower()


def hidden_context_provider(monkeypatch, document):
    """Quote literal original-page text that a context fragment did not supply."""
    extraction_module = importlib.import_module("scione.specifications.extraction")
    original_planner = extraction_module.plan_source_chunks
    planned = []
    by_page = {page.page_number: page for page in document.pages}

    def record_plan(*args, **kwargs):
        chunks = original_planner(*args, **kwargs)
        planned.extend(chunks)
        return chunks

    monkeypatch.setattr(extraction_module, "plan_source_chunks", record_plan)

    class HiddenContextProvider(ChunkProvider):
        hidden_source = None

        def generate_structured(self, request):
            if request.schema_name.startswith("customer_test_inventory"):
                return super().generate_structured(request)
            chunk = next(item for item in planned
                         if item.primary_pages == primary_pages(request))
            for span in chunk.context_source_spans:
                supplied = next(page for page in chunk.document.pages
                                if page.page_number == span.page_number)
                hidden = next((line.strip() for line in by_page[span.page_number].text.splitlines()
                               if len(line.strip()) > 12
                               and not citation_contains(supplied.text, line.strip())
                               and line.strip() not in request.user_prompt), None)
                if hidden is None:
                    continue
                self.requests.append(request)
                self.extraction_count += 1
                self.hidden_source = (span.page_number, hidden)
                row = pencil_requirement()
                primary = by_page[chunk.primary_pages[0]]
                primary_quote = next(line.strip() for line in primary.text.splitlines()
                                     if line.strip())
                row["evidence"] = [
                    {"page": primary.page_number, "quote": primary_quote},
                    {"page": span.page_number, "quote": hidden},
                ]
                response = StaticModelProvider(compact_payload([row])).generate_structured(request)
                self.responses.append(response)
                return response
            return super().generate_structured(request)

    return HiddenContextProvider(), planned


def test_literal_hidden_context_text_cannot_become_proof_just_because_original_page_exists(
    monkeypatch,
):
    document = client_source()
    original = document.model_dump_json()
    provider, planned = hidden_context_provider(monkeypatch, document)

    with pytest.raises(CustomerExtractionError) as captured:
        CustomerRequirementRunner(provider, max_input_characters=60_000).run(
            document, strategy="inventory_compact_document"
        )

    assert provider.hidden_source is not None
    page_number, hidden = provider.hidden_source
    assert citation_contains(document.pages[page_number - 1].text, hidden)
    assert hidden not in provider.requests[-1].user_prompt
    assert captured.value.error_code == "chunk_source_scope_error"
    artifact = captured.value.artifact
    assert artifact is not None and artifact.status == "failed"
    assert artifact.calls[-1].request == provider.requests[-1]
    assert artifact.calls[-1].response.raw_text == provider.responses[-1].raw_text
    assert Counter(page for chunk in planned for page in chunk.primary_pages) == Counter(
        range(1, 14)
    )
    for chunk in planned:
        for primary in chunk.primary_pages:
            assert next(page for page in chunk.document.pages if page.page_number == primary) == (
                document.pages[primary - 1]
            )
    assert document.model_dump_json() == original


@pytest.mark.parametrize("guide_kind", ["full", "names", "primary_names"])
def test_inventory_cannot_reintroduce_context_facts_hidden_from_the_supplied_fragment(guide_kind):
    primary = "Requested visible body test."
    supplied = "4.4 Visible support\nVisible supporting clause.\n"
    hidden = "4.5 Unrelated work\nHidden supporting clause.\n"
    pages = [DocumentPage(
        page_number=number, text=text, character_count=len(text),
        non_whitespace_character_count=len("".join(text.split())),
    ) for number, text in enumerate([primary, supplied + hidden], 1)]
    document = IngestedDocument(
        document_id="fragment-guide-regression", file_name="fragment.pdf", sha256="a" * 64,
        page_count=2, extraction_status="ready", text_reading_order="geometric", pages=pages,
    )
    chunk = SourceChunk(
        index=1, primary_pages=(1,), context_pages=(2,), shared_context_pages=(),
        referenced_context_pages=(2,),
        context_source_spans=(SourceContextSpan(2, 0, len(supplied)),),
        document=document.model_copy(update={"pages": [pages[0], pages[1].model_copy(update={
            "text": supplied, "character_count": len(supplied),
            "non_whitespace_character_count": len("".join(supplied.split())),
        })]}),
    )
    inventory = CustomerTestInventory.model_validate({
        "tests": [
            {"test": "Visible objective", "evidence": [{"page": 1, "quote": primary}]},
            {"test": "Hidden objective", "evidence": [{"page": 2, "quote": hidden}]},
            {"test": "Hidden mixed objective", "evidence": [
                {"page": 1, "quote": primary}, {"page": 2, "quote": hidden},
            ]},
        ],
        "attachments": [{
            "relation": "preparation", "applies_to": [1],
            "evidence": [{"page": 2, "quote": hidden}],
        }],
    })
    original = inventory.model_dump_json()
    guide = (
        _chunk_inventory_guidance(inventory, chunk) if guide_kind == "full"
        else _chunk_inventory_names(inventory, chunk, include_context=guide_kind == "names")
    )
    assert "Visible objective" in guide
    assert "Hidden objective" not in guide and "Hidden mixed objective" not in guide
    assert "Hidden supporting clause" not in guide
    assert inventory.model_dump_json() == original


@pytest.mark.parametrize("citation_site", ["requirement", "atomic_claim"])
def test_chunk_rejects_actual_original_page_that_was_not_supplied_in_that_call(citation_site):
    document = client_source()
    by_page = {page.page_number: page for page in document.pages}

    class OutsideChunkProvider(ChunkProvider):
        def generate_structured(self, request):
            if request.schema_name.startswith("customer_test_inventory"):
                return super().generate_structured(request)
            self.requests.append(request)
            self.extraction_count += 1
            supplied = set(physical_pages(request))
            missing = next(number for number in by_page if number not in supplied)
            present = min(supplied)
            present_quote = next(line.strip() for line in by_page[present].text.splitlines()
                                 if line.strip())
            missing_quote = next(line.strip() for line in by_page[missing].text.splitlines()
                                 if line.strip())
            row = pencil_requirement()
            row["evidence"] = [{"page": present, "quote": present_quote}]
            if citation_site == "requirement":
                row["evidence"] = [{"page": missing, "quote": missing_quote}]
            else:
                row["atomic_claims"] = [{
                    "parameter_id": None, "kind": "condition", "stage": "general",
                    "operator": "text", "values": [], "unit": None,
                    "text_value": missing_quote, "raw_text": missing_quote,
                    "source_pages": [missing], "sequence": None,
                    "source_context": None, "configuration": None,
                }]
            response = StaticModelProvider(compact_payload([row])).generate_structured(request)
            self.responses.append(response)
            return response

    provider = OutsideChunkProvider()
    with pytest.raises(CustomerExtractionError) as captured:
        CustomerRequirementRunner(provider, max_input_characters=60_000).run(
            document, strategy="inventory_compact_document"
        )
    assert provider.extraction_count == 1
    assert captured.value.error_code == "chunk_source_scope_error"
    artifact = captured.value.artifact
    assert artifact is not None and artifact.error_code == "chunk_source_scope_error"
    assert artifact.calls[-1].request == provider.requests[-1]
    assert artifact.calls[-1].response.raw_text == provider.responses[-1].raw_text
    assert "not supplied" in str(captured.value)


@pytest.mark.parametrize("failure_kind", ["provider_error", "hidden_context_literal"])
def test_failed_chunked_api_analysis_keeps_previous_run_and_original_pdf(
    tmp_path, monkeypatch, failure_kind,
):
    settings = Settings(
        _env_file=None, scione_workspace_dir=tmp_path, scione_database_url=None,
        groq_api_key=None, gemini_api_key=None, sail_api_key=None, fireworks_api_key=None,
        scione_customer_input_character_limit=60_000,
    )
    provider = (
        ChunkProvider(fail_extraction=2) if failure_kind == "provider_error"
        else hidden_context_provider(monkeypatch, client_source())[0]
    )
    api = importlib.import_module("scione.api.app")
    monkeypatch.setattr(api, "create_configured_provider", lambda *_args, **_kwargs: provider)
    client = LocalHTTPClient(create_app(settings, frontend_dir=tmp_path / "no-frontend"))
    source_bytes = CLIENT_PDF.read_bytes()
    uploaded = client.post(
        "/api/specifications",
        files={"file": ("original-client-spec.pdf", source_bytes, "application/pdf")},
    )
    assert uploaded.status_code == 200, uploaded.text
    document_id = uploaded.json()["document_id"]
    previous = CustomerExtractionRun.model_validate({
        "run_id": str(uuid4()), "document_id": document_id,
        "started_at": datetime.now(UTC), "prompt_version": "offline_previous_draft",
        "provider": "static", "model": "offline_previous_draft", "latency_ms": 1,
        "usage": {}, "schema_version": "0.1", "raw_response": "Previous untouched model text",
        "extraction": {
            "schema_version": "0.1", "document": {"title": "Previous saved draft"},
            "requirements": [{
                "id": "req_previous", "original_test_name": "Pencil hardness",
                "evidence": [{"page": 6, "quote": "Pencil hardness"}],
            }],
        },
    })
    specs = FileSpecStore(tmp_path)
    specs.save_extraction(document_id, previous)
    result = client.post(f"/api/specifications/{document_id}/analysis", json={
        "provider": "groq", "model": "offline_chunk_regression",
        "strategy": "inventory_compact_document", "output_limit": 4096,
        "permission": True,
    })
    assert result.status_code == 400, result.text
    if failure_kind == "provider_error":
        assert provider.extraction_count == 2
    else:
        assert provider.hidden_source is not None
    assert specs.load_latest_extraction(document_id) == previous
    expected_error = (
        "provider_error" if failure_kind == "provider_error" else "chunk_source_scope_error"
    )
    assert specs.load_failure(document_id).error_code == expected_error
    assert client.get(f"/api/specifications/{document_id}/pdf").content == source_bytes
    saved = client.get(f"/api/specifications/{document_id}")
    assert saved.status_code == 200, saved.text
    assert saved.json()["run"]["run_id"] == previous.run_id


def test_unfit_single_page_fails_preflight_without_any_provider_request():
    provider = ChunkProvider()
    with pytest.raises(CustomerExtractionError) as captured:
        CustomerRequirementRunner(provider, max_input_characters=10).run(
            client_source(), strategy="inventory_compact_document"
        )
    assert provider.requests == []
    assert captured.value.error_code == "input_too_large"


def test_short_documents_keep_existing_two_call_inventory_workflow():
    text = "Pencil hardness ISO 15184, 750 g, acceptance ≥ F."
    document = IngestedDocument(
        document_id="short-chunk-regression", file_name="short.pdf", sha256="a" * 64,
        page_count=1, extraction_status="ready", text_reading_order="geometric",
        pages=[DocumentPage(
            page_number=1, text=text, character_count=len(text),
            non_whitespace_character_count=len("".join(text.split())),
        )],
    )
    requirement = pencil_requirement()
    requirement["evidence"] = [{"page": 1, "quote": text}]

    class ShortProvider(ChunkProvider):
        def generate_structured(self, request):
            self.requests.append(request)
            value = (
                {"tests": [], "attachments": [], "uncertainties": []}
                if request.schema_name.startswith("customer_test_inventory")
                else compact_payload([requirement])
            )
            return StaticModelProvider(value).generate_structured(request)

    provider = ShortProvider()
    run = CustomerRequirementRunner(provider).run(document, strategy="inventory_compact_document")
    assert len(provider.requests) == 2
    assert [call.purpose for call in run.calls] == ["inventory", "extraction"]
    assert all("<SOURCE_CHUNK " not in request.user_prompt for request in provider.requests)


def test_short_physical_pdf_keeps_two_serial_calls_without_section_hint(tmp_path, monkeypatch):
    import pymupdf

    path = tmp_path / "ordinary-two-page.pdf"
    with pymupdf.open() as pdf:
        pdf.new_page().insert_text((30, 50), "Pencil hardness ISO 15184, 750 g, acceptance >= F.")
        pdf.new_page().insert_text(
            (30, 50), "The customer asks for a written laboratory test report.",
        )
        path.write_bytes(pdf.tobytes())
    document = PyMuPDFTextIngestor(
        reading_order=TextReadingOrder.GEOMETRIC, minimum_document_characters=50,
    ).ingest(path)
    row = pencil_requirement()
    row["evidence"] = [{"page": 1, "quote": document.pages[0].text.strip()}]

    def unexpected_structure(*_args, **_kwargs):
        raise AssertionError("Fitting short documents keep their original input path.")

    monkeypatch.setattr(
        "scione.specifications.extraction.source_section_intros", unexpected_structure,
    )

    class SerialProvider(ChunkProvider):
        provider_name = "sail"

        def __init__(self):
            super().__init__()
            self.events = []
            self.active = self.peak = 0

        def generate_structured(self, request):
            self.requests.append(request)
            purpose = (
                "inventory" if request.schema_name.startswith("customer_test_inventory")
                else "extraction"
            )
            self.active += 1
            self.peak = max(self.peak, self.active)
            self.events.append(("start", purpose))
            try:
                value = (
                    {"tests": [], "attachments": [], "uncertainties": []}
                    if purpose == "inventory" else compact_payload([row])
                )
                return StaticModelProvider(value).generate_structured(request)
            finally:
                self.events.append(("finished", purpose))
                self.active -= 1

    provider = SerialProvider()
    run = CustomerRequirementRunner(provider, source_pdf_bytes=path.read_bytes()).run(
        document, strategy="inventory_compact_document",
    )
    assert [call.purpose for call in run.calls] == ["inventory", "extraction"]
    assert provider.events == [
        ("start", "inventory"), ("finished", "inventory"),
        ("start", "extraction"), ("finished", "extraction"),
    ]
    assert provider.peak == 1 and provider.active == 0
    assert all("<SOURCE_SECTION_CONTEXT>" not in request.user_prompt
               and "<SOURCE_CHUNK " not in request.user_prompt for request in provider.requests)
    assert all(request_characters(request) <= 60_000 for request in provider.requests)

