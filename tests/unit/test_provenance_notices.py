"""Literal metadata notices cannot hide scientific questions or source failures."""

import json

import pytest

from scione.providers import StaticModelProvider
from scione.schemas import DocumentPage, IngestedDocument
from scione.specifications.extraction import CustomerRequirementRunner
from scione.specifications.models import CustomerDocumentNotice
from scione.specifications.provenance import (
    metadata_only_synthetic_quote,
    nonblocking_notice_issues,
    provenance_source_issues,
)
from scione.specifications.review import assess_requirements

TEST = "Gloss per ASTM D1627-16; acceptance 82-92 GU."
DEMONSTRATION_NOTICE = (
    "Demonstration data only. The customer requirements below are original fictional "
    "examples. Method identifiers are references only; no standard procedure text is "
    "reproduced."
)


def document(text):
    text = TEST + "\n" + text
    return IngestedDocument(
        document_id="notice_source", file_name="notice.pdf", sha256="a" * 64,
        page_count=1, extraction_status="ready", text_reading_order="geometric",
        pages=[DocumentPage(
            page_number=1, text=text, character_count=len(text),
            non_whitespace_character_count=len("".join(text.split())),
        )],
    )


def payload(quote):
    return {
        "document": {"title": None, "identifier": None, "revision": None},
        "requirements": [{
            "test": "Gloss", "methods": ["ASTM D1627-16"],
            "criteria": ["acceptance 82-92 GU"],
            "evidence": [{"page": 1, "quote": TEST}], "uncertainty": None,
        }],
        "review_issues": [],
        "document_notices": [{"kind": "synthetic_provenance", "page": 1, "quote": quote}],
    }


@pytest.mark.parametrize("quote", [
    "The method IDs are synthetic bank references; no official equivalence is asserted.",
    "All method IDs are synthetic; no official equivalence is claimed.",
    "Cited method IDs are synthetic bank entries; no official equivalence is claimed.",
    "ISO 60070:2020 and ASTM F1070-20 are synthetic method designations.",
    "PLR-03 and PLR-06 are fictional bank references.",
    "FICTIONAL CUSTOMER SPECIFICATION • SYNTHETIC METHOD IDS WHERE REFERENCED",
    "Prepared for synthetic extraction and normalization benchmarking. All customer "
    "entities, acceptance limits and cited test-method designations in this document are "
    "fictional; no method is asserted to be a published or officially equivalent standard.",
    DEMONSTRATION_NOTICE,
])
def test_literal_provenance_is_separate_from_row_review_and_original_output_survives(quote):
    source = document(quote)
    notice = CustomerDocumentNotice(page=1, quote=quote)
    assert provenance_source_issues(notice, source) == []
    raw = payload(quote)
    run = CustomerRequirementRunner(StaticModelProvider(raw)).run(
        source, strategy="compact_document"
    )
    assert len(run.extraction.document_notices) == 1
    assert run.extraction.review_issues == []
    assert assess_requirements(run, source)["req_001"].state == "ready"
    assert json.loads(run.raw_response) == raw


@pytest.mark.parametrize("quote", [
    "Method IDs are synthetic; test at 30 °C.",
    "Method IDs are synthetic; the acceptance limit is unclear.",
    "Method IDs are synthetic; customer approval is required.",
    "Method IDs are synthetic; two requirements are missing.",
    "Method IDs are not synthetic.",
    "Method IDs are synthetic; not all methods are equivalent.",
    "Method IDs are synthetic; 温度范围有冲突。",
    "The specimens are synthetic.",
    "Method IDs are synthetic; no customer acceptance limits are stated.",
    "Method IDs are synthetic; acceptance limits are not stated.",
    "No method IDs are synthetic.",
])
def test_mixed_or_unknown_content_remains_actionable_even_if_literal(quote):
    source = document(quote)
    assert provenance_source_issues(CustomerDocumentNotice(page=1, quote=quote), source)
    raw = payload(quote)
    run = CustomerRequirementRunner(StaticModelProvider(raw)).run(
        source, strategy="compact_document"
    )
    assert run.extraction.document_notices == []
    assert run.extraction.review_issues
    assert assess_requirements(run, source)["req_001"].state == "needs_attention"
    assert json.loads(run.raw_response) == raw


@pytest.mark.parametrize("page,quote", [
    (2, "Method IDs are synthetic."),
    (1, "All methods are fictional and equivalent."),
])
def test_wrong_page_or_unstated_notice_never_becomes_nonblocking(page, quote):
    notice = CustomerDocumentNotice(page=page, quote=quote)
    assert provenance_source_issues(notice, document("Method IDs are synthetic."))


@pytest.mark.parametrize("quote", [
    "Cited method IDs are synthetic bank entries, not official standards.",
    "All IDs are synthetic bank references; no official equivalence is implied.",
    "All IDs are synthetic; limits, locations and timing remain customer-defined.",
    "Method IDs are fictional; this memo's wash history, scales and release limits govern.",
    "These checks and limits are customer-set; the bank ID is synthetic.",
    "Fictional customer requirement / dataset evaluation only.",
    "Method IDs are synthetic; the 180 MOhm and other limits are buyer-set.",
    "Methodenkennungen sind fiktiv; Kundenbedingungen und Grenzwerte bleiben maßgeblich.",
    "Les seuils ci-dessus sont propres au client; les identifiants de méthode sont fictifs.",
    "以上编号均为 SciOne AI 方法库的 synthetic references，并非正式标准文件，亦不宣称方法等效。",
    "本文件引用 SciOne AI Method Bank 的模拟方法编号，"
    "对应总干膜厚、色差、光泽、湿热及交叉切割附着。"
    "编号仅为本数据集匹配测试路径，并非正式发布标准或方法等效声明。",
])
def test_multilingual_metadata_ownership_does_not_request_scientific_review(quote):
    assert metadata_only_synthetic_quote(quote)
    raw = payload(quote)
    run = CustomerRequirementRunner(StaticModelProvider(raw)).run(
        document(quote), strategy="compact_document"
    )
    assert run.extraction.review_issues == []
    assert len(run.extraction.document_notices) == 1
    assert run.notice_audits[0].status == "source_verified"
    assert assess_requirements(run, document(quote))["req_001"].state == "ready"


def test_unlocated_pure_metadata_is_unused_and_audited_without_invalidating_test_facts():
    quote = "All method IDs are synthetic; no official equivalence is asserted."
    source = document("No such disclosure is printed here.")
    raw = payload(quote)
    run = CustomerRequirementRunner(StaticModelProvider(raw)).run(
        source, strategy="compact_document"
    )
    assert run.extraction.document_notices == []
    assert run.extraction.review_issues == []
    assert run.notice_audits[0].status == "unused_unverified_metadata"
    assert run.notice_audits[0].proposal.quote == quote
    assert run.notice_audits[0].issues
    assert json.loads(run.calls[0].raw_response) == raw
    assert assess_requirements(run, source)["req_001"].state == "ready"


def test_historical_notice_error_is_reclassified_only_with_actual_raw_typed_proposal():
    quote = "All method IDs are synthetic; no official equivalence is asserted."
    source = document("No such disclosure is printed here.")
    run = CustomerRequirementRunner(StaticModelProvider(payload(quote))).run(
        source, strategy="compact_document"
    )
    issue = (
        "Document notice needs review: Provenance notice is not a literal quotation "
        "on its physical source page. " + quote
    )
    run.extraction.review_issues.append(issue)
    assert nonblocking_notice_issues(run, source) == {issue}
    assert assess_requirements(run, source)["req_001"].state == "ready"
    assert run.extraction.review_issues == [issue]
    unrelated = run.model_copy(deep=True)
    unrelated.calls[0].raw_response = json.dumps({"document_notices": []})
    assert nonblocking_notice_issues(unrelated, source) == set()
    assert assess_requirements(unrelated, source)["req_001"].state == "needs_attention"


@pytest.mark.parametrize("quote", [
    "Method IDs are synthetic; insulation resistance must be >=180 MOhm.",
    "Method IDs are synthetic; the test temperature is not stated.",
    "Method IDs are fictional; the acceptance limit is unclear.",
    "方法编号为合成方法，但温度条件未明确。",
])
def test_quantities_or_real_concerns_cannot_hide_inside_provenance(quote):
    assert not metadata_only_synthetic_quote(quote)
    source = document(quote)
    run = CustomerRequirementRunner(StaticModelProvider(payload(quote))).run(
        source, strategy="compact_document"
    )
    assert run.notice_audits[0].status == "needs_review"
    assert nonblocking_notice_issues(run, source) == set()
    assert assess_requirements(run, source)["req_001"].state == "needs_attention"


@pytest.mark.parametrize("restriction", [
    "Test at 30 °C.",
    "The acceptance criterion is at least 4B.",
    "Only the published edition may be used.",
    "No other method edition is permitted.",
    "The test temperature is not stated.",
    "No standard procedure is required.",
])
def test_demonstration_disclaimer_does_not_hide_appended_scientific_restrictions(restriction):
    quote = DEMONSTRATION_NOTICE + " " + restriction
    assert not metadata_only_synthetic_quote(quote)
    source = document(quote)
    run = CustomerRequirementRunner(StaticModelProvider(payload(quote))).run(
        source, strategy="compact_document"
    )
    assert run.notice_audits[0].status == "needs_review"
    assert run.extraction.document_notices == []
    assert nonblocking_notice_issues(run, source) == set()
    assert assess_requirements(run, source)["req_001"].state == "needs_attention"


def test_historical_demo_notice_error_is_reprojected_without_changing_raw_output():
    source = document(DEMONSTRATION_NOTICE)
    raw = payload(DEMONSTRATION_NOTICE)
    run = CustomerRequirementRunner(StaticModelProvider(raw)).run(
        source, strategy="compact_document"
    )
    issue = (
        "Document notice needs review: Provenance notice contains other content that "
        "still requires review. " + DEMONSTRATION_NOTICE
    )
    run.extraction.review_issues.append(issue)
    assert nonblocking_notice_issues(run, source) == {issue}
    assert assess_requirements(run, source)["req_001"].state == "ready"
    assert run.extraction.review_issues == [issue]
    assert json.loads(run.raw_response) == raw
