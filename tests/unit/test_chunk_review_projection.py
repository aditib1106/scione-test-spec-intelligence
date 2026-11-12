"""Document-context review projection needs original chunk and source proof."""

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

REGIONAL = (
    "Page 2 regional annex lists accepted method equivalents "
    "(SAE J400 for ISO 20567-1; ASTM D3359 for ISO 2409) for tests whose full conditions "
    "are on context page 1, which is outside this chunk's primary pages; these "
    "equivalence clauses could not be attached to test rows emitted here."
)
APPROVAL = (
    "Page 3 approval-package table (new color release, plant introduction, "
    "requalification, change approval) is administrative workflow, not independently "
    "requested tests; the referenced test packages (e.g. xenon 2000 kJ/m², stone chip "
    "at overbake) are summaries of tests detailed elsewhere in the document."
)
REGIONAL_OTHER_PRIMARY = (
    "Page 12 regional annex lists accepted method equivalents (SAE J400 for ISO 20567-1; "
    "ASTM D3359 for ISO 2409; ASTM B117 for ISO 9227; GB/T 9286 for ISO 2409; "
    "GB/T 1771 for ISO 9227; GB/T 1865 accepted with correlation data to SAE J2527). "
    "These are equivalence declarations for tests whose substantive rows appear on other "
    "pages not supplied as primary here, so no separate test rows were emitted for them."
)
APPROVAL_OTHER_SECTIONS = (
    "Page 13 lists approval packages (full system approval, new color release, plant "
    "introduction, requalification, change approval); these are package summaries referencing "
    "other sections, not new requested tests on the primary pages."
)


def fixture(issues=(REGIONAL, APPROVAL)):
    lines = [
        "Gloss ISO 2813: test at 23 °C, acceptance >=80 GU.",
        "Cross-cut ISO 2409: test at 23 °C, acceptance <=1 class.",
        "Stone chip ISO 20567-1: test at 23 °C, acceptance <=1 class.",
    ]
    texts = [
        "\n".join(lines),
        "6.2 Regional annex\nSAE J400 accepted for ISO 20567-1; "
        "ASTM D3359 for ISO 2409;\n7. Approval types and test packages",
        "Approval type   Trigger   Test package   Documents updated\n"
        "New color release: xenon 2000 kJ/m², stone chip at overbake\n"
        "Plant introduction\nRequalification\nChange approval",
    ]
    source = IngestedDocument(
        document_id="chunk-review-source", file_name="customer.pdf", sha256="a" * 64,
        page_count=3, extraction_status="ready", text_reading_order="geometric",
        pages=[DocumentPage(
            page_number=i, text=text, character_count=len(text),
            non_whitespace_character_count=len("".join(text.split())),
        ) for i, text in enumerate(texts, 1)],
    )
    rows = [CustomerRequirement(
        id=f"req_{i}", original_test_name=name,
        requested_methods=[RequestedMethod(code=code)],
        conditions=[RequirementValue(name="Temperature", raw_value="23 °C")],
        acceptance_criteria=[RequirementValue(name="Criterion", raw_value=criterion)],
        evidence=[RequirementEvidence(page=1, quote=line)],
    ) for i, (name, code, criterion, line) in enumerate(zip(
        ["Gloss", "Cross-cut", "Stone chip"], ["ISO 2813", "ISO 2409", "ISO 20567-1"],
        [">=80 GU", "<=1 class", "<=1 class"], lines, strict=True,
    ), 1)]
    request = StructuredGenerationRequest(
        system_prompt="Offline fixture", user_prompt=source.as_prompt_text(),
        schema_name="fixture", json_schema={"type": "object"},
    )
    run = CustomerExtractionRun(
        run_id="chunk-review-run", started_at=datetime.now(UTC), document_id=source.document_id,
        strategy="compact_document", prompt_version="offline", schema_version="0.1",
        provider="static", model="fixture", latency_ms=0, usage=TokenUsage(),
        raw_response=json.dumps({"input_partitioning": {"policy": SOURCE_CHUNK_POLICY}}),
        raw_response_kind="focused_pass_manifest",
        calls=[CustomerExtractionCall(
            call_index=1, focus_pages=[2, 3], provider="static", model="fixture", latency_ms=0,
            usage=TokenUsage(), raw_response=json.dumps({"review_issues": list(issues)}),
            request=request,
        )],
        extraction=CustomerExtraction(
            schema_version="0.1", document=CustomerDocumentMetadata(), requirements=rows,
            review_issues=list(issues),
        ),
    )
    return run, source


def test_region_questions_stay_on_document_and_known_owners_without_flagging_unrelated_tests():
    run, source = fixture()
    before = run.model_dump_json()
    projection = source_chunk_review_projection(run, source)
    assert projection.document_issues == (REGIONAL, APPROVAL)
    assert projection.row_targets == {REGIONAL: frozenset({"req_2", "req_3"})}
    assert document_review_issues(run, source) == (REGIONAL, APPROVAL)
    result = assess_requirements(run, source)
    assert result["req_1"].state == "ready"
    assert REGIONAL in result["req_2"].reasons
    assert REGIONAL in result["req_3"].reasons
    assert all(APPROVAL not in item.reasons for item in result.values())
    assert run.model_dump_json() == before


def test_interleaved_table_labels_are_layout_locations_never_scientific_proof():
    run, source = fixture()
    source.pages[1].text = source.pages[1].text.replace(
        "ASTM D3359", "ASTM\nDifferent column text    D3359"
    )
    source.pages[2].text = source.pages[2].text.replace(
        "New color release", "New color    Different column text\nrelease"
    ).replace(
        "Plant introduction", "Plant    Different column text\nintroduction"
    ).replace(
        "Change approval", "Change    Different column text\napproval"
    )
    projection = source_chunk_review_projection(run, source)
    assert projection.document_issues == (REGIONAL, APPROVAL)
    assert projection.row_targets[REGIONAL] == frozenset({"req_2", "req_3"})
    for location in projection.source_locations.values():
        assert location["location_kind"] == "layout_column_tokens"
        assert location["establishes_method_equivalence"] is False
        assert location["establishes_limit_or_coverage"] is False
        assert location["document_review_required"] is True


@pytest.mark.parametrize("change", [
    "no_policy", "wrong_policy", "no_original_call", "no_call_request",
    "wrong_page", "no_regional_heading", "invented_method", "extra_science",
])
def test_regional_projection_fails_closed_without_every_required_proof(change):
    run, source = fixture((REGIONAL,))
    if change == "no_policy":
        run.raw_response = "{}"
    elif change == "wrong_policy":
        run.raw_response = json.dumps({"input_partitioning": {"policy": "unknown"}})
    elif change == "no_original_call":
        run.calls[0].raw_response = json.dumps({"review_issues": []})
    elif change == "no_call_request":
        run.calls[0].request = None
    elif change == "no_regional_heading":
        source.pages[1].text = source.pages[1].text.replace("Regional annex", "Other section")
    else:
        issue = REGIONAL
        if change == "wrong_page":
            issue = issue.replace("Page 2", "Page 1")
        elif change == "invented_method":
            issue = issue.replace("ASTM D3359", "ASTM D99999")
        else:
            issue += " The temperature limit is ambiguous."
        run.extraction.review_issues = [issue]
        run.calls[0].raw_response = json.dumps({"review_issues": [issue]})
    assert source_chunk_review_projection(run, source).document_issues == ()
    assert assess_requirements(run, source)["req_1"].state == "needs_attention"


@pytest.mark.parametrize("change", ["extra_science", "wrong_value", "missing_heading"])
def test_approval_comment_does_not_hide_added_science_or_unlocated_package_facts(change):
    run, source = fixture((APPROVAL,))
    if change == "missing_heading":
        source.pages[1].text = source.pages[1].text.replace(
            "Approval types and test packages", "Other section"
        )
    else:
        issue = APPROVAL + " The acceptance criterion is missing."
        if change == "wrong_value":
            issue = APPROVAL.replace("2000", "2500")
        run.extraction.review_issues = [issue]
        run.calls[0].raw_response = json.dumps({"review_issues": [issue]})
    assert source_chunk_review_projection(run, source).document_issues == ()
    assert assess_requirements(run, source)["req_1"].state == "needs_attention"


def current_fixture(issues=(REGIONAL_OTHER_PRIMARY, APPROVAL_OTHER_SECTIONS)):
    run, source = fixture(issues)
    regional = source.pages[1].text.replace(
        "ASTM D3359 for ISO 2409;",
        "ASTM D3359 for ISO 2409; ASTM B117 for ISO 9227; GB/T 9286 for ISO 2409; "
        "GB/T 1771 for ISO 9227; GB/T 1865 accepted with correlation data to SAE J2527;",
    ) + "\nFull system approval\n"
    texts = [source.pages[0].text, *["Independent background page."] * 10,
             regional, source.pages[2].text]
    source = source.model_copy(update={
        "page_count": 13,
        "pages": [DocumentPage(
            page_number=i, text=text, character_count=len(text),
            non_whitespace_character_count=len("".join(text.split())),
        ) for i, text in enumerate(texts, 1)],
    })
    run.calls[0].focus_pages = [12, 13]
    run.calls[0].request = run.calls[0].request.model_copy(update={
        "user_prompt": source.as_prompt_text(),
    })
    return run, source


def test_current_closed_comments_route_regional_owners_and_approval_once_without_mutation():
    run, source = current_fixture()
    before = run.model_dump_json()
    projection = source_chunk_review_projection(run, source)
    assert projection.document_issues == (REGIONAL_OTHER_PRIMARY, APPROVAL_OTHER_SECTIONS)
    assert projection.row_targets == {REGIONAL_OTHER_PRIMARY: frozenset({"req_2", "req_3"})}
    assert projection.source_locations[APPROVAL_OTHER_SECTIONS]["source_pages"] == [12, 13]
    result = assess_requirements(run, source)
    assert result["req_1"].state == "ready"
    assert REGIONAL_OTHER_PRIMARY in result["req_2"].reasons
    assert REGIONAL_OTHER_PRIMARY in result["req_3"].reasons
    assert all(APPROVAL_OTHER_SECTIONS not in item.reasons for item in result.values())
    assert run.model_dump_json() == before


def test_current_comment_phrasing_is_source_proved_on_the_actual_client_pdf():
    from pathlib import Path

    from scione.ingestion.pdf_text import PyMuPDFTextIngestor
    from scione.schemas import TextReadingOrder

    run, _ = current_fixture()
    source = PyMuPDFTextIngestor(reading_order=TextReadingOrder.GEOMETRIC).ingest(
        Path(__file__).resolve().parents[2] / "docs/samples/MS-PNT-410_demo_spec.pdf"
    )
    run.document_id = source.document_id
    run.calls[0].request = run.calls[0].request.model_copy(update={
        "user_prompt": source.as_prompt_text(),
    })
    projection = source_chunk_review_projection(run, source)
    assert projection.document_issues == (REGIONAL_OTHER_PRIMARY, APPROVAL_OTHER_SECTIONS)
    assert projection.row_targets[REGIONAL_OTHER_PRIMARY] == frozenset({"req_2", "req_3"})
    assert all(not p["establishes_method_equivalence"]
               and not p["establishes_limit_or_coverage"]
               and p["document_review_required"] for p in projection.source_locations.values())


@pytest.mark.parametrize("change", [
    "no_raw_comment", "no_page_in_request", "no_heading", "missing_method",
    "extra_science", "changed_relationship", "not_other_primary",
])
def test_current_regional_comment_needs_all_original_proofs_and_exact_closed_scope(change):
    issue = REGIONAL_OTHER_PRIMARY
    run, source = current_fixture((issue,))
    if change == "no_raw_comment":
        run.calls[0].raw_response = json.dumps({"review_issues": []})
    elif change == "no_page_in_request":
        run.calls[0].request = run.calls[0].request.model_copy(update={
            "user_prompt": "=== PAGE 13 ===\nUnrelated package page.",
        })
    elif change == "no_heading":
        source.pages[11].text = source.pages[11].text.replace("Regional annex", "Other section")
    elif change == "missing_method":
        source.pages[11].text = source.pages[11].text.replace("ASTM B117", "Unknown reference")
    else:
        if change == "extra_science":
            issue += " The actual temperature limit is uncertain."
        elif change == "changed_relationship":
            issue = issue.replace("SAE J400 for ISO 20567-1", "SAE J400 at 70 °C for ISO 20567-1")
        else:
            issue = issue.replace("other pages not supplied as primary", "these primary pages")
        run.extraction.review_issues = [issue]
        run.calls[0].raw_response = json.dumps({"review_issues": [issue]})
    assert source_chunk_review_projection(run, source).document_issues == ()
    assert assess_requirements(run, source)["req_1"].state == "needs_attention"


@pytest.mark.parametrize("change", [
    "no_previous_page", "missing_heading", "missing_package", "missing_table_labels",
    "extra_science", "invented_package",
])
def test_current_approval_comment_requires_printed_packages_and_supplied_heading_page(change):
    issue = APPROVAL_OTHER_SECTIONS
    run, source = current_fixture((issue,))
    if change == "no_previous_page":
        run.calls[0].request = run.calls[0].request.model_copy(update={
            "user_prompt": "=== PAGE 13 ===\n" + source.pages[12].text,
        })
    elif change == "missing_heading":
        source.pages[11].text = source.pages[11].text.replace(
            "Approval types and test packages", "Other section",
        )
    elif change == "missing_package":
        source.pages[11].text = source.pages[11].text.replace("Full system approval", "")
    elif change == "missing_table_labels":
        source.pages[12].text = source.pages[12].text.replace("Test package", "Other column")
    else:
        issue = (
            issue + " The acceptance criterion is absent."
            if change == "extra_science"
            else issue.replace("full system approval", "new secret test")
        )
        run.extraction.review_issues = [issue]
        run.calls[0].raw_response = json.dumps({"review_issues": [issue]})
    assert source_chunk_review_projection(run, source).document_issues == ()
    assert assess_requirements(run, source)["req_1"].state == "needs_attention"


def test_independent_scientific_issue_remains_after_current_closed_comment_projection():
    scientific = "req_1: the gloss acceptance criterion is ambiguous."
    run, source = current_fixture((REGIONAL_OTHER_PRIMARY, APPROVAL_OTHER_SECTIONS, scientific))
    projection = source_chunk_review_projection(run, source)
    assert scientific not in projection.document_issues
    result = assess_requirements(run, source)
    assert result["req_1"].state == "needs_attention"
    assert scientific in result["req_1"].reasons


BAKE_CONTEXT = (
    "Page 2 states 'Adhesion, stone chip and humidity are tested at all three bake conditions; "
    "other tests at nominal bake unless stated', but the corresponding adhesion, stone-chip "
    "and humidity test rows are printed on context pages (Section 3.2/4.x index on page 1) "
    "not included as primary rows in this chunk; applicability of the three-bake condition "
    "to those tests could not be resolved here."
)
WEATHERING_CONTEXT = (
    "Section 4.2 weathering note on page 2 ('Series release can be granted on accelerated "
    "results; outdoor exposure continues after release and the release is withdrawn if a "
    "checkpoint fails') is a release-workflow clause, retained here rather than as a test "
    "row; confirm whether it should be captured as a shared instruction in a full-document "
    "extraction."
)
FLORIDA_CONTEXT = (
    "Page 2 Europe row states 'Florida exposure (Section 4.2) is sufficient' as a "
    "cross-reference to weathering tests defined on other pages; no separate test row "
    "was emitted for it."
)
REGIONAL_UNATTACHED = (
    "Page 2 lists accepted method equivalents (SAE J400 for ISO 20567-1; ASTM D3359 for "
    "ISO 2409) for tests whose substantive rows are on other pages not supplied here; "
    "they could not be attached to those tests in this chunk."
)
APPROVAL_SUMMARY = (
    "Page 3 approval-package table (new color release, plant introduction, requalification, "
    "change approval) summarizes tests defined elsewhere and was not emitted as new test rows."
)
METADATA_VARIANCE = (
    "Source-window extractions returned differing document metadata; the first window's "
    "metadata was retained and must be checked against the source."
)


def scoped_context_fixture():
    issues = (BAKE_CONTEXT, WEATHERING_CONTEXT, FLORIDA_CONTEXT,
              REGIONAL_UNATTACHED, APPROVAL_SUMMARY, METADATA_VARIANCE)
    run, source = fixture(issues)
    run.extraction.requirements[1].original_test_name = "Cross-cut adhesion"
    run.extraction.requirements[1].evidence[0].quote = (
        run.extraction.requirements[1].evidence[0].quote.replace("Cross-cut", "Cross-cut adhesion")
    )
    source.pages[0].text = source.pages[0].text.replace("Cross-cut", "Cross-cut adhesion")
    source.pages[0].text += (
        "\nExample standard additional details\nStandard no. EX-55\n"
        "Revision C (supersedes Rev B)\n"
    )
    for number, name, code in [(4, "Humidity", "ISO 6270-2"),
                               (5, "Xenon weathering", "SAE J2527")]:
        row = run.extraction.requirements[0].model_copy(deep=True)
        row.id = f"req_{number}"
        row.original_test_name = name
        row.requested_methods = [RequestedMethod(code=code)]
        line = f"{name} {code}: test at 23 °C, acceptance >=80 GU."
        row.evidence = [RequirementEvidence(page=1, quote=line)]
        run.extraction.requirements.append(row)
        source.pages[0].text += line + "\n"
    source.pages[1].text += (
        "\nAdhesion, stone chip and humidity are tested at all three bake conditions; "
        "other tests at nominal bake unless stated\n4.2 Weathering\n"
        "Series release can be granted on accelerated results; outdoor exposure continues "
        "after release and the release is withdrawn if a checkpoint fails\n"
        "Europe: Florida exposure (Section 4.2) is sufficient\n"
    )
    metadata = {"title": "Example standard additional details", "identifier": "EX-55",
                "revision": "C (supersedes Rev B)"}
    payload = {"review_issues": list(issues), "document": metadata}
    run.calls[0].raw_response = json.dumps(payload)
    run.calls[0].request = run.calls[0].request.model_copy(update={
        "user_prompt": source.as_prompt_text(),
    })
    second = run.calls[0].model_copy(deep=True)
    second.call_index = 2
    second.raw_response = json.dumps({"document": {
        "title": "Example standard", "identifier": "EX-55", "revision": "C",
    }})
    run.calls.append(second)
    return run, source


def test_scoped_chunk_notes_preserve_real_owner_reviews_and_metadata_once():
    run, source = scoped_context_fixture()
    original = run.model_dump_json()
    projection = source_chunk_review_projection(run, source)
    assert set(projection.document_issues) == set(run.extraction.review_issues)
    assert projection.row_targets[BAKE_CONTEXT] == {"req_2", "req_3", "req_4"}
    assert projection.row_targets[WEATHERING_CONTEXT] == {"req_5"}
    assert projection.row_targets[FLORIDA_CONTEXT] == {"req_5"}
    assert projection.row_targets[REGIONAL_UNATTACHED] == {"req_2", "req_3"}
    assert APPROVAL_SUMMARY not in projection.row_targets
    assert METADATA_VARIANCE not in projection.row_targets
    result = assess_requirements(run, source)
    assert result["req_1"].state == "ready"
    assert BAKE_CONTEXT in result["req_4"].reasons
    assert WEATHERING_CONTEXT in result["req_5"].reasons
    assert run.model_dump_json() == original


@pytest.mark.parametrize("issue", [BAKE_CONTEXT, WEATHERING_CONTEXT, FLORIDA_CONTEXT,
                                       REGIONAL_UNATTACHED, APPROVAL_SUMMARY])
@pytest.mark.parametrize("change", ["no_comment", "no_request_page", "added_science"])
def test_new_context_variants_fail_closed_without_original_proofs(issue, change):
    run, source = scoped_context_fixture()
    run.extraction.review_issues = [issue]
    if change == "no_comment":
        run.calls[0].raw_response = json.dumps({"review_issues": []})
    elif change == "no_request_page":
        run.calls[0].request = run.calls[0].request.model_copy(update={
            "user_prompt": "=== PAGE 1 ===\nUnrelated background.",
        })
    else:
        issue += " The acceptance temperature is uncertain."
        run.extraction.review_issues = [issue]
        run.calls[0].raw_response = json.dumps({"review_issues": [issue]})
    assert source_chunk_review_projection(run, source).document_issues == ()
    assert assess_requirements(run, source)["req_1"].state == "needs_attention"


@pytest.mark.parametrize("change", ["identifier", "revision", "previous_revision",
                                      "missing_printed_revision", "invented_title"])
def test_metadata_projection_does_not_hide_true_conflict_or_unproved_details(change):
    run, source = scoped_context_fixture()
    run.extraction.review_issues = [METADATA_VARIANCE]
    payload = json.loads(run.calls[1].raw_response)
    if change == "identifier":
        payload["document"]["identifier"] = "EX-56"
    elif change == "revision":
        payload["document"]["revision"] = "D"
    elif change == "previous_revision":
        payload["document"]["revision"] = "C (supersedes Rev A)"
    elif change == "invented_title":
        payload["document"]["title"] = "An unprinted title"
    else:
        source.pages[0].text = source.pages[0].text.replace(
            "Revision C (supersedes Rev B)", "Document control not supplied",
        )
    run.calls[1].raw_response = json.dumps(payload)
    assert source_chunk_review_projection(run, source).document_issues == ()
    assert assess_requirements(run, source)["req_1"].state == "needs_attention"


def test_missing_affected_family_or_scientific_quote_remains_global():
    run, source = scoped_context_fixture()
    run.extraction.review_issues = [BAKE_CONTEXT]
    run.extraction.requirements = [row for row in run.extraction.requirements
                                   if row.original_test_name != "Humidity"]
    assert source_chunk_review_projection(run, source).document_issues == ()
    assert assess_requirements(run, source)["req_1"].state == "needs_attention"


COVERAGE_NOTE = (
    "The test item index on page 1 (context-only) lists Gloss, Cross-cut and Stone chip, "
    "but their substantive test rows are not on the supplied primary pages 2-3, "
    "so no rows were emitted for them in this chunk."
)


def coverage_fixture(issue=COVERAGE_NOTE):
    run, source = fixture((issue,))
    source.pages[0].text = "2.4 Test item index\n" + source.pages[0].text
    run.calls[0].request = run.calls[0].request.model_copy(update={
        "user_prompt": source.as_prompt_text(),
    })
    run.raw_response = json.dumps({"input_partitioning": {
        "policy": SOURCE_CHUNK_POLICY, "chunks": [
            {"primary_pages": [1], "supplied_pages": [1, 2, 3]},
            {"primary_pages": [2, 3], "supplied_pages": [1, 2, 3]},
        ],
    }})
    return run, source


def test_saved_window_index_omission_is_document_coverage_not_every_row_error():
    run, source = coverage_fixture()
    original = run.model_dump_json()
    result = source_chunk_review_projection(run, source)
    assert result.document_issues == (COVERAGE_NOTE,)
    assert not result.row_targets
    assert result.source_locations[COVERAGE_NOTE]["document_review_required"]
    assert not result.source_locations[COVERAGE_NOTE]["establishes_limit_or_coverage"]
    assert all(item.state == "ready" for item in assess_requirements(run, source).values())
    assert run.model_dump_json() == original


@pytest.mark.parametrize("change", [
    "no_partition", "wrong_focus", "unsupplied_page", "missing_index",
    "incomplete_merge", "no_original_comment", "scientific_defect", "new_numeric_claim",
    "invented_method", "row_defect",
])
def test_window_coverage_route_requires_original_partition_and_no_scientific_defect(change):
    run, source = coverage_fixture()
    if change == "no_partition":
        run.raw_response = json.dumps({"input_partitioning": {"policy": SOURCE_CHUNK_POLICY}})
    elif change == "wrong_focus":
        run.calls[0].focus_pages = [2]
    elif change == "unsupplied_page":
        run.calls[0].request = run.calls[0].request.model_copy(update={
            "user_prompt": "=== PAGE 2 ===\nOther source",
        })
    elif change == "missing_index":
        source.pages[0].text = source.pages[0].text.replace("2.4 Test item index", "Other section")
    elif change == "incomplete_merge":
        manifest = json.loads(run.raw_response)
        manifest["input_partitioning"]["chunks"].pop(0)
        run.raw_response = json.dumps(manifest)
    elif change == "no_original_comment":
        run.calls[0].raw_response = json.dumps({"review_issues": []})
    else:
        suffix = {
            "scientific_defect": " The acceptance criterion is absent.",
            "new_numeric_claim": " The test must run at 70 °C.",
            "invented_method": " ISO 99999 was referenced.",
            "row_defect": " req_1 requires correction.",
        }[change]
        issue = COVERAGE_NOTE + suffix
        run.extraction.review_issues = [issue]
        run.calls[0].raw_response = json.dumps({"review_issues": [issue]})
    assert not source_chunk_review_projection(run, source).document_issues


def test_quoted_shared_clause_does_not_globalize_explicit_single_property_concern():
    from scione.specifications.review import _catalogued_name_issue_targets

    run, _ = fixture()
    run.extraction.requirements[0].original_test_name = "Film thickness"
    issue = (
        "The shared clause 'Adhesion and humidity use all bake conditions; other tests "
        "use nominal bake' does not explicitly state applicability to the film thickness row."
    )
    assert _catalogued_name_issue_targets(issue, run.extraction.requirements) == {"req_1"}
    assert _catalogued_name_issue_targets(
        issue + " All other tests also require correction.", run.extraction.requirements,
    ) is None


def test_full_source_named_titles_locate_a_shared_ambiguity_without_approving_it():
    from scione.specifications.review import _literal_title_issue_targets

    run, source = fixture()
    run.extraction.requirements[0].original_test_name = "Humid-tropical exposure, Hainan (China)"
    run.extraction.requirements[1].original_test_name = "Dry-heat exposure, Turpan (China)"
    source.pages[0].text += "\nChina: Hainan humid-tropical and Turpan dry-heat exposures."
    issue = (
        "The China annex combines the Hainan humid-tropical and Turpan dry-heat exposures "
        "with one printed limit pair; applicability to both exposures is ambiguous."
    )
    assert _literal_title_issue_targets(issue, run.extraction.requirements, source) == {
        "req_1", "req_2",
    }
    source.pages[0].text = source.pages[0].text.replace("Hainan", "Unknown location")
    assert _literal_title_issue_targets(issue, run.extraction.requirements, source) == {"req_2"}
    assert _literal_title_issue_targets(
        issue + " All tests require correction.", run.extraction.requirements, source,
    ) is None


@pytest.mark.parametrize("physical_quote", [True, False])
def test_original_window_cross_reference_requires_quoted_physical_words(physical_quote):
    issue = (
        "Europe's 'Florida exposure (Section 4.2) is sufficient' on page 2 is a "
        "cross-reference to weathering tests on context pages, not a new test row "
        "on the primary pages."
    )
    run, source = scoped_context_fixture()
    run.extraction.review_issues = [issue]
    run.calls[0].raw_response = json.dumps({"review_issues": [issue]})
    run.raw_response = json.dumps({"input_partitioning": {
        "policy": SOURCE_CHUNK_POLICY, "chunks": [
            {"primary_pages": [1], "supplied_pages": [1, 2, 3]},
            {"primary_pages": [2, 3], "supplied_pages": [1, 2, 3]},
        ],
    }})
    if not physical_quote:
        source.pages[1].text = source.pages[1].text.replace("Florida exposure", "Other region")
    result = source_chunk_review_projection(run, source)
    if physical_quote:
        assert result.document_issues == (issue,)
        assert result.row_targets[issue] == {"req_5"}
    else:
        assert not result.document_issues
