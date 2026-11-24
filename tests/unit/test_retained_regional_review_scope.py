"""Child-window regional concerns keep their source question and actual owners."""

import json
from datetime import UTC, datetime

import pytest

from scione.providers import StructuredGenerationRequest, TokenUsage
from scione.schemas import DocumentPage, IngestedDocument
from scione.specifications.chunk_review_projection import source_chunk_review_projection
from scione.specifications.models import (
    CustomerDocumentMetadata,
    CustomerExtraction,
    CustomerExtractionCall,
    CustomerExtractionRun,
    CustomerRequirement,
    RequestedMethod,
    RequirementEvidence,
    RequirementValue,
)
from scione.specifications.review import assess_requirements, document_review_issues
from scione.specifications.source_chunking import SOURCE_CHUNK_POLICY

NOTE = (
    "Page 2 lists accepted method equivalents (SAE J400 for ISO 20567-1; "
    "ASTM D3359 for ISO 2409) for tests whose substantive rows are on other pages "
    "not supplied as primary here; they are retained here as unresolved for review "
    "rather than attached to the regional exposure rows."
)


def fixture():
    lines = [
        ("Gloss", "ISO 2813", "Gloss ISO 2813: test at 23 °C; at least 80 GU."),
        ("Cross-cut", "ISO 2409", "Cross-cut ISO 2409: test at 23 °C; at most 1 class."),
        ("Stone chip", "ISO 20567-1", "Stone chip ISO 20567-1: test at 23 °C; at most 1 class."),
    ]
    texts = [
        "\n".join(line for _, _, line in lines),
        "6.2 Regional annex\nSAE J400 for ISO 20567-1; ASTM D3359 for ISO 2409",
        "Regional validation details continue here.",
    ]
    source = IngestedDocument(
        document_id="retained-region-source", file_name="customer.pdf", sha256="a" * 64,
        page_count=3, extraction_status="ready", text_reading_order="geometric",
        pages=[DocumentPage(
            page_number=index, text=text, character_count=len(text),
            non_whitespace_character_count=len("".join(text.split())),
        ) for index, text in enumerate(texts, 1)],
    )
    rows = [CustomerRequirement(
        id=f"req_{index}", original_test_name=name,
        requested_methods=[RequestedMethod(code=code)],
        conditions=[RequirementValue(name="Temperature", raw_value="23 °C")],
        acceptance_criteria=[RequirementValue(
            name="Criterion", raw_value="at least 80 GU" if index == 1 else "at most 1 class",
        )],
        evidence=[RequirementEvidence(page=1, quote=line)],
    ) for index, (name, code, line) in enumerate(lines, 1)]
    manifest = {"input_partitioning": {
        "policy": SOURCE_CHUNK_POLICY,
        "chunks": [{"primary_pages": [2, 3], "supplied_pages": [1, 2, 3]}],
    }}
    run = CustomerExtractionRun(
        run_id="retained-region-run", started_at=datetime.now(UTC),
        document_id=source.document_id, strategy="compact_document", prompt_version="offline",
        schema_version="0.1", provider="static", model="fixture", latency_ms=0,
        usage=TokenUsage(), raw_response=json.dumps(manifest),
        raw_response_kind="focused_pass_manifest",
        calls=[CustomerExtractionCall(
            call_index=1, focus_pages=[2, 3], provider="static", model="fixture",
            latency_ms=0, usage=TokenUsage(), raw_response=json.dumps({"review_issues": [NOTE]}),
            request=StructuredGenerationRequest(
                system_prompt="Offline fixture", user_prompt=source.as_prompt_text(),
                schema_name="fixture", json_schema={"type": "object"},
            ),
        )],
        extraction=CustomerExtraction(
            schema_version="0.1", document=CustomerDocumentMetadata(), requirements=rows,
            review_issues=[NOTE],
        ),
    )
    return run, source


def _refresh_fixture_request(run, source):
    for page in source.pages:
        page.character_count = len(page.text)
        page.non_whitespace_character_count = len("".join(page.text.split()))
    run.calls[0].request = run.calls[0].request.model_copy(update={
        "user_prompt": source.as_prompt_text(),
    })


def _replace_requested_method(run, source, row_index, new_code):
    row = run.extraction.requirements[row_index]
    old_code = row.requested_methods[0].code
    old_quote = row.evidence[0].quote
    row.requested_methods[0].code = new_code
    row.evidence[0].quote = old_quote.replace(old_code, new_code)
    source.pages[0].text = source.pages[0].text.replace(old_quote, row.evidence[0].quote)
    _refresh_fixture_request(run, source)


def test_retained_regional_comment_stays_visible_for_document_and_method_owners():
    run, source = fixture()
    original = run.model_dump_json()
    projection = source_chunk_review_projection(run, source)
    assert projection.document_issues == (NOTE,)
    assert projection.row_targets == {NOTE: frozenset({"req_2", "req_3"})}
    assert document_review_issues(run, source) == (NOTE,)
    location = projection.source_locations[NOTE]
    assert location["policy_version"] == "chunk_document_context_v5"
    assert location["document_review_required"] is True
    assert location["establishes_method_equivalence"] is False
    assert location["establishes_limit_or_coverage"] is False
    assessed = assess_requirements(run, source)
    assert assessed["req_1"].state == "ready"
    assert assessed["req_2"].state == assessed["req_3"].state == "needs_attention"
    assert NOTE in assessed["req_2"].reasons and NOTE in assessed["req_3"].reasons
    assert run.model_dump_json() == original


@pytest.mark.parametrize("change", [
    "no_manifest", "wrong_policy", "no_window", "wrong_primary", "missing_supplied_page",
    "no_original_comment", "no_request", "wrong_focus", "missing_request_page",
    "wrong_physical_page", "missing_annex_heading", "missing_method", "invented_method",
    "scientific_ambiguity", "changed_relationship", "global_uncertainty",
])
def test_regional_routing_does_not_hide_unproved_or_scientifically_broader_concerns(change):
    run, source = fixture()
    issue = NOTE
    manifest = json.loads(run.raw_response)
    if change == "no_manifest":
        run.raw_response = "{}"
    elif change == "wrong_policy":
        manifest["input_partitioning"]["policy"] = "unproved_policy"
    elif change == "no_window":
        manifest["input_partitioning"].pop("chunks")
    elif change == "wrong_primary":
        manifest["input_partitioning"]["chunks"][0]["primary_pages"] = [1]
    elif change == "missing_supplied_page":
        manifest["input_partitioning"]["chunks"][0]["supplied_pages"] = [1]
    elif change == "no_original_comment":
        run.calls[0].raw_response = json.dumps({"review_issues": []})
    elif change == "no_request":
        run.calls[0].request = None
    elif change == "wrong_focus":
        run.calls[0].focus_pages = [1]
    elif change == "missing_request_page":
        run.calls[0].request = run.calls[0].request.model_copy(update={
            "user_prompt": "=== PAGE 3 ===\nRegional validation details continue here.",
        })
    elif change == "wrong_physical_page":
        issue = issue.replace("Page 2", "Page 3")
    elif change == "missing_annex_heading":
        source.pages[1].text = source.pages[1].text.replace("Regional annex", "Other section")
    elif change == "missing_method":
        source.pages[1].text = source.pages[1].text.replace("ASTM D3359", "Unspecified method")
    elif change == "invented_method":
        issue = issue.replace("ASTM D3359", "ASTM D99999")
    elif change == "scientific_ambiguity":
        issue += " The actual temperature limit is ambiguous."
    elif change == "changed_relationship":
        issue = issue.replace("SAE J400 for ISO 20567-1", "SAE J400 at 70 °C for ISO 20567-1")
    elif change == "global_uncertainty":
        issue += " All test rows may have missing conditions."
    if change != "no_manifest":
        run.raw_response = json.dumps(manifest)
    if issue != NOTE:
        run.extraction.review_issues = [issue]
        run.calls[0].raw_response = json.dumps({"review_issues": [issue]})
    assert source_chunk_review_projection(run, source).document_issues == ()
    assert assess_requirements(run, source)["req_1"].state == "needs_attention"


def test_independent_scientific_warning_still_blocks_the_previously_unrelated_test():
    run, source = fixture()
    science = "req_1: confirm whether the requested gloss limit applies after cycling."
    run.extraction.review_issues.append(science)
    assessed = assess_requirements(run, source)
    assert assessed["req_1"].state == "needs_attention"
    assert science in assessed["req_1"].reasons
    assert NOTE not in assessed["req_1"].reasons


@pytest.mark.parametrize(("row_index", "code"), [
    (1, "ISO 2409:2013"),
    (1, "ISO 2409:2013-06"),
    (1, "ＩＳＯ ２４０９"),
    (1, "ISO_2409"),
    (1, "ISO 2409 (method A)"),
    (1, "ISO 2409 / ASTM D3359"),
    (1, "ASTM D3359-17"),
    (2, "ISO 20567\u20111"),
])
def test_edition_or_format_uncertainty_retains_global_review_without_equating_methods(
    row_index, code,
):
    run, source = fixture()
    _replace_requested_method(run, source, row_index, code)
    original_run, original_source = run.model_dump_json(), source.model_dump_json()
    projection = source_chunk_review_projection(run, source)
    assert projection.document_issues == (NOTE,)
    assert projection.row_targets[NOTE] == frozenset({"req_1", "req_2", "req_3"})
    location = projection.source_locations[NOTE]
    assert location["owner_routing"] == "global_ownership_uncertain"
    assert location["establishes_method_equivalence"] is False
    assert location["establishes_limit_or_coverage"] is False
    assert document_review_issues(run, source) == (NOTE,)
    assessed = assess_requirements(run, source)
    assert all(NOTE in value.reasons and value.state == "needs_attention"
               for value in assessed.values())
    assert run.extraction.requirements[row_index].requested_methods[0].code == code
    assert run.model_dump_json() == original_run
    assert source.model_dump_json() == original_source


def test_incomplete_pair_ownership_does_not_project_a_partial_set_as_complete():
    run, source = fixture()
    # The stone-chip pair still has its exact owner; the cross-cut pair does not.
    # The complete source-backed note remains globally actionable rather than
    # silently being narrowed to that one remaining test.
    _replace_requested_method(run, source, 1, "ISO 24090")
    projection = source_chunk_review_projection(run, source)
    assert projection.row_targets[NOTE] == frozenset({"req_1", "req_2", "req_3"})
    assert projection.source_locations[NOTE]["owner_routing"] == "global_ownership_uncertain"
    assert all(NOTE in row.reasons for row in assess_requirements(run, source).values())


@pytest.mark.parametrize("extra_code", [
    "ISO 24090", "ISO 2409-1", "ISO 20567-2", "ASTM D33590", "ASTM D3363-20",
])
def test_exact_pair_owners_do_not_flag_unrelated_number_part_or_standard(extra_code):
    run, source = fixture()
    quote = f"Independent test {extra_code}: test at 23 °C; at least 80 GU."
    extra = run.extraction.requirements[0].model_copy(deep=True, update={
        "id": "req_extra", "original_test_name": "Independent test",
        "requested_methods": [RequestedMethod(code=extra_code)],
        "evidence": [RequirementEvidence(page=1, quote=quote)],
    })
    run.extraction.requirements.append(extra)
    source.pages[0].text += "\n" + quote
    _refresh_fixture_request(run, source)
    projection = source_chunk_review_projection(run, source)
    assert projection.row_targets == {NOTE: frozenset({"req_2", "req_3"})}
    assert "owner_routing" not in projection.source_locations[NOTE]
    assessed = assess_requirements(run, source)
    assert NOTE not in assessed["req_1"].reasons
    assert NOTE not in assessed["req_extra"].reasons
    assert assessed["req_extra"].state == "ready"
    assert NOTE in assessed["req_2"].reasons and NOTE in assessed["req_3"].reasons


def test_nonexact_extra_owner_is_not_hidden_when_other_exact_owners_exist():
    run, source = fixture()
    quote = "Cross-cut ISO 2409:2013: test at 23 °C; at most 1 class."
    extra = run.extraction.requirements[1].model_copy(deep=True, update={
        "id": "req_extra", "requested_methods": [RequestedMethod(code="ISO 2409:2013")],
        "evidence": [RequirementEvidence(page=1, quote=quote)],
    })
    run.extraction.requirements.append(extra)
    source.pages[0].text += "\n" + quote
    _refresh_fixture_request(run, source)
    projection = source_chunk_review_projection(run, source)
    assert projection.row_targets[NOTE] == frozenset({"req_1", "req_2", "req_3", "req_extra"})
    assessed = assess_requirements(run, source)
    assert NOTE in assessed["req_extra"].reasons
    assert assessed["req_extra"].state == "needs_attention"
