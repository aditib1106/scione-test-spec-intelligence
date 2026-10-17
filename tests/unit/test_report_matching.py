"""Offline safety checks for identity matching, human decisions and draft pricing."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal, localcontext
from pathlib import Path

import pytest
from pydantic import ValidationError

from scione.catalogs.models import Alias, LabCapability
from scione.catalogs.seed import default_demo_lab, load_reference_catalog
from scione.providers import TokenUsage
from scione.reports.matching import build_report, input_fingerprint, lab_fingerprint
from scione.reports.models import MappingChoice, QuoteChoice, ReportSnapshot, ReportWorkspace
from scione.schemas import DocumentPage, IngestedDocument, TextExtractionStatus, TextReadingOrder
from scione.specifications.models import (
    CustomerDocumentMetadata,
    CustomerExtraction,
    CustomerExtractionRun,
    CustomerRequirement,
    RequestedMethod,
    RequirementEvidence,
    RequirementValue,
)
from scione.specifications.store import RequirementReview, RequirementReviewSet, StoredSpecification

NOW = datetime(2026, 10, 17, tzinfo=UTC)
DOC_ID = "a" * 16


def _requirement(
    name: str = "Cross-cut adhesion",
    methods: tuple[str, ...] = ("ISO 2409",),
    identifier: str = "req_001",
) -> CustomerRequirement:
    return CustomerRequirement(
        id=identifier,
        original_test_name=name,
        requested_methods=[RequestedMethod(code=code) for code in methods],
        conditions=[RequirementValue(name="Spacing", raw_value="2 mm")],
        acceptance_criteria=[RequirementValue(name="Rating", raw_value="GT 0 to 1")],
        applicability=["All bake conditions"],
        special_instructions=["Retain source qualifications"],
        evidence=[RequirementEvidence(page=1, quote="Source quotation")],
    )


def _inputs(rows: list[CustomerRequirement] | None = None):
    text = "Source quotation"
    source = IngestedDocument(
        document_id=DOC_ID,
        file_name="customer.pdf",
        sha256="a" * 64,
        page_count=1,
        pages=[
            DocumentPage(
                page_number=1,
                text=text,
                character_count=len(text),
                non_whitespace_character_count=len(text.replace(" ", "")),
            )
        ],
        text_reading_order=TextReadingOrder.CONTENT_STREAM,
        extraction_status=TextExtractionStatus.READY,
    )
    stored = StoredSpecification(source, "customer.pdf", NOW, Path("/unused/customer.pdf"))
    run = CustomerExtractionRun(
        run_id="run_fixture",
        started_at=NOW,
        document_id=DOC_ID,
        prompt_version="fixture",
        schema_version="0.1",
        provider="offline",
        model="not-called",
        latency_ms=0,
        usage=TokenUsage(),
        raw_response="  original raw output\n",
        extraction=CustomerExtraction(
            schema_version="0.1",
            document=CustomerDocumentMetadata(),
            requirements=rows if rows is not None else [_requirement()],
        ),
    )
    review = RequirementReviewSet(document_id=DOC_ID, run_id=run.run_id)
    workspace = ReportWorkspace(document_id=DOC_ID, run_id=run.run_id)
    return stored, run, review, default_demo_lab(), load_reference_catalog(), workspace


def _supported(inputs, *, quantity: str = "3", **choice):
    workspace = inputs[-1]
    choice.setdefault("canonical_test_id", "cross_cut_adhesion")
    workspace.mappings["req_001"] = MappingChoice(
        capability_verdict="supported",
        lab_fingerprint=lab_fingerprint(inputs[3]),
        note="Engineer checked scope and conditions.",
        **choice,
    )
    workspace.quantities["req_001"] = QuoteChoice(
        quantity=Decimal(quantity),
        scope_and_basis_confirmed=True,
        lab_fingerprint=lab_fingerprint(inputs[3]),
        note="Three applicable panels; overlaps reviewed against customer scope.",
    )
    return inputs


@pytest.mark.parametrize(
    "name",
    ["Cross-cut adhesion", "cross cut adhesion", "Ｃｒｏｓｓ－ｃｕｔ adhesion", "划格附着力"],
)
def test_curated_english_chinese_aliases_and_unicode_formatting(name):
    line = build_report(*_inputs([_requirement(name)])).lines[0]
    assert line.canonical_test_id == "cross_cut_adhesion"
    assert line.status == "in_house"
    assert line.extraction_review_state == "ready"
    assert line.quote is None
    assert "not been verified" in " ".join(line.reasons)


@pytest.mark.parametrize("code", ["ISO2409", "iso 2409", "ＩＳＯ ２４０９"])
def test_identifier_formatting_is_safe_without_equating_authorities(code):
    line = build_report(*_inputs([_requirement(methods=(code,))])).lines[0]
    assert line.status == "in_house"
    assert line.method_ids == ["iso_2409"]
    assert line.considered_method_codes == [code]


def test_same_test_wrong_method_is_a_method_gap_not_equivalence():
    line = build_report(*_inputs([_requirement(methods=("ASTM D3359",))])).lines[0]
    assert line.status == "method_gap"
    assert line.method_ids == ["astm_d3359"]
    assert line.lab_method_identifiers == ["ISO 2409"]
    assert line.capability.id == "cap_cross_cut"


def test_known_test_absent_from_lab_is_catalog_gap_not_proof_impossible():
    line = build_report(*_inputs([_requirement("Pull-off adhesion", ("ISO 4624",))])).lines[0]
    assert line.status == "test_gap"
    assert "not proof" in " ".join(line.reasons)


@pytest.mark.parametrize("code", ["ISO 20567", "ISO 20567-2", "ASTM 20567-1"])
def test_method_parts_and_authorities_never_collapse(code):
    line = build_report(*_inputs([_requirement("Stone-chip resistance", (code,))])).lines[0]
    assert line.status == "needs_review"
    assert line.method_ids == []
    assert line.unknown_methods == [code]


def test_unknown_custom_test_does_not_invent_catalog_identity():
    line = build_report(
        *_inputs([_requirement("Moon glass portal stability", ("Client PROC-42",))])
    ).lines[0]
    assert line.canonical_test_id is None
    assert line.status == "needs_review"
    assert line.unknown_methods == ["Client PROC-42"]


@pytest.mark.parametrize("name", ["Cross cut adheison", "Customer adhesion check"])
def test_fuzzy_or_method_only_suggestions_require_human_identity(name):
    inputs = _inputs([_requirement(name)])
    line = build_report(*inputs).lines[0]
    assert line.status == "needs_review"
    assert line.canonical_test_id is None
    assert "cross_cut_adhesion" in line.candidate_test_ids
    inputs[-1].mappings["req_001"] = MappingChoice(
        canonical_test_id="cross_cut_adhesion", note="Engineer confirmed terminology."
    )
    confirmed = build_report(*inputs).lines[0]
    assert confirmed.status == "in_house"
    assert confirmed.match_basis == "Human-confirmed test identity"


def test_shared_alias_remains_ambiguous():
    inputs = _inputs()
    inputs[-2].test_by_id("gloss").aliases.append(Alias(text="Cross-cut adhesion", language="en"))
    line = build_report(*inputs).lines[0]
    assert line.canonical_test_id is None
    assert line.status == "needs_review"
    assert set(line.candidate_test_ids) >= {"cross_cut_adhesion", "gloss"}


@pytest.mark.parametrize("code", ["ISO 2409:2020", "ISO 2409 (latest)", "ISO 2409 Method B"])
def test_revisions_and_qualifiers_block_false_support(code):
    inputs = _supported(_inputs([_requirement(methods=(code,))]))
    line = build_report(*inputs).lines[0]
    assert line.status == "needs_review"
    assert line.method_ids == ["iso_2409"]
    assert line.unknown_methods == [code]
    assert line.quote is None


def test_explicit_method_revision_needs_known_matching_catalog_edition():
    inputs = _inputs()
    inputs[1].extraction.requirements[0].requested_methods[0].revision = "2020"
    assert build_report(*inputs).lines[0].status == "needs_review"
    inputs[-2].method_by_id("iso_2409").edition = "2020"
    assert build_report(*inputs).lines[0].status == "in_house"
    inputs[-2].method_by_id("iso_2409").edition = "2013"
    assert build_report(*inputs).lines[0].status == "needs_review"


def test_astm_trailing_year_is_revision_not_an_ignored_suffix():
    inputs = _inputs([_requirement(methods=("ASTM D3359-23",))])
    line = build_report(*inputs).lines[0]
    assert line.status == "needs_review"
    assert line.method_ids == ["astm_d3359"]
    assert "revision 23" in " ".join(line.reasons)


def test_conflicting_test_and_method_requires_review_even_with_supported_choice():
    inputs = _supported(_inputs([_requirement(methods=("ISO 2813",))]))
    line = build_report(*inputs).lines[0]
    assert line.status == "needs_review"
    assert "conflict" in " ".join(line.reasons)
    assert line.quote is None
    inputs[-1].mappings["req_001"].canonical_test_id = "gloss"
    assert build_report(*inputs).lines[0].status == "in_house"


def test_multiple_methods_require_explicit_subset_of_original_codes():
    inputs = _inputs([_requirement(methods=("ISO 2409", "ASTM D3359"))])
    assert build_report(*inputs).lines[0].status == "needs_review"
    inputs[-1].mappings["req_001"] = MappingChoice(
        canonical_test_id="cross_cut_adhesion",
        selected_method_codes=["ISO 2409"],
        note="ISO 2409 applies to this region; ASTM is an unselected alternative.",
    )
    line = build_report(*inputs).lines[0]
    assert line.status == "in_house"
    assert line.considered_method_codes == ["ISO 2409"]
    assert [item.code for item in line.requirement.requested_methods] == ["ISO 2409", "ASTM D3359"]
    inputs[-1].mappings["req_001"].selected_method_codes = ["ISO 2813"]
    assert build_report(*inputs).lines[0].status == "needs_review"


def test_human_can_choose_both_methods_but_no_combined_capability_is_invented():
    inputs = _inputs([_requirement(methods=("ISO 2409", "ASTM D3359"))])
    inputs[-1].mappings["req_001"] = MappingChoice(
        canonical_test_id="cross_cut_adhesion",
        selected_method_codes=["ISO 2409", "ASTM D3359"],
        note="Both are required.",
    )
    assert build_report(*inputs).lines[0].status == "method_gap"


def test_multiple_capabilities_choose_unique_full_match_not_first_row():
    inputs = _inputs()
    inputs[3].capabilities.insert(
        0,
        LabCapability(
            id="cap_other_adhesion",
            canonical_test_id="cross_cut_adhesion",
            method_ids=["astm_d3359"],
        ),
    )
    line = build_report(*inputs).lines[0]
    assert line.status == "in_house"
    assert line.capability.id == "cap_cross_cut"


def test_multiple_full_capabilities_require_explicit_capability_and_validate_test():
    inputs = _inputs()
    inputs[3].capabilities.append(
        inputs[3].capabilities[0].model_copy(update={"id": "cap_cross_cut_second"}, deep=True)
    )
    assert build_report(*inputs).lines[0].status == "needs_review"
    inputs[-1].mappings["req_001"] = MappingChoice(
        canonical_test_id="cross_cut_adhesion",
        capability_id="cap_cross_cut_second",
        note="Use second declaration.",
    )
    assert build_report(*inputs).lines[0].capability.id == "cap_cross_cut_second"
    inputs[-1].mappings["req_001"].capability_id = "cap_gloss"
    assert build_report(*inputs).lines[0].status == "needs_review"


@pytest.mark.parametrize("canonical_id", ["does_not_exist", "gloss"])
def test_invalid_or_conflicting_human_test_choice_does_not_force_support(canonical_id):
    inputs = _supported(_inputs(), canonical_test_id=canonical_id)
    line = build_report(*inputs).lines[0]
    assert line.status == "needs_review"
    assert line.quote is None


def test_explicit_unresolved_choice_is_not_silently_replaced_by_automatic_alias():
    inputs = _supported(_inputs(), canonical_test_id=None)
    line = build_report(*inputs).lines[0]
    assert line.canonical_test_id is None
    assert line.status == "needs_review"
    assert line.match_basis == "Human left test identity unresolved"
    assert line.quote is None


def test_conditions_do_not_auto_compare_free_text_but_human_can_record_gap():
    inputs = _inputs([_requirement("Stone-chip resistance", ("ISO 20567-1",))])
    inputs[1].extraction.requirements[0].conditions[0].raw_value = "-20 °C"
    assert build_report(*inputs).lines[0].status == "in_house"
    inputs[-1].mappings["req_001"] = MappingChoice(
        canonical_test_id="stone_chip_resistance",
        lab_fingerprint=lab_fingerprint(inputs[3]),
        capability_verdict="unsupported",
        note="Lab can only do 23 °C, not required -20 °C.",
    )
    line = build_report(*inputs).lines[0]
    assert line.status == "condition_gap"
    assert line.quote is None


def test_supported_override_never_changes_method_gap():
    inputs = _supported(_inputs([_requirement(methods=("ASTM D3359",))]))
    report = build_report(*inputs)
    assert report.lines[0].status == "method_gap"
    assert report.lines[0].quote is None
    assert report.totals_by_currency == {}
    assert any("saved quantity was not priced" in message for message in report.warnings)


def test_no_default_quantity_or_automatic_price_from_record_count():
    report = build_report(*_inputs())
    assert report.lines[0].quote is None
    assert report.totals_by_currency == {}
    assert any("record counts are not quantities" in message for message in report.warnings)


def test_explicit_scope_quantity_and_supported_conditions_enable_decimal_quote():
    report = build_report(*_supported(_inputs(), quantity="3.5"))
    line = report.lines[0]
    assert line.quote.quantity == Decimal("3.5")
    assert line.quote.unit_price == Decimal("35.00")
    assert line.quote.subtotal == Decimal("122.50")
    assert line.quote.pricing_basis == "per panel"
    assert report.totals_by_currency == {"USD": Decimal("122.50")}
    assert report.status == "draft"


def test_zero_price_is_known_not_missing():
    inputs = _inputs()
    inputs[3].capabilities[0].price = Decimal("0")
    report = build_report(*_supported(inputs))
    assert report.lines[0].quote.unit_price == Decimal("0")
    assert report.totals_by_currency == {"USD": Decimal("0.00")}


def test_unknown_price_never_becomes_zero():
    inputs = _supported(_inputs())
    inputs[3].capabilities[0].price = None
    report = build_report(*inputs)
    assert report.lines[0].quote is None
    assert report.totals_by_currency == {}


def test_mixed_currencies_have_separate_subtotals_no_exchange_rate():
    inputs = _inputs([_requirement(), _requirement("Gloss", ("ISO 2813",), "req_002")])
    inputs[3].capabilities[1].currency = "EUR"
    _supported(inputs)
    inputs[-1].mappings["req_002"] = MappingChoice(
        canonical_test_id="gloss",
        lab_fingerprint=lab_fingerprint(inputs[3]),
        capability_verdict="supported",
        note="Gloss conditions confirmed.",
    )
    inputs[-1].quantities["req_002"] = QuoteChoice(
        quantity=2,
        scope_and_basis_confirmed=True,
        lab_fingerprint=lab_fingerprint(inputs[3]),
        note="Two panels, separately applicable.",
    )
    report = build_report(*inputs)
    assert report.totals_by_currency == {"USD": Decimal("105.00"), "EUR": Decimal("50.00")}


def test_pending_content_review_is_never_silently_confirmed_or_priced():
    inputs = _supported(_inputs())
    inputs[1].extraction.review_issues = ["req_001 scope is unclear."]
    report = build_report(*inputs)
    assert report.lines[0].status == "in_house"
    assert report.lines[0].extraction_review_state == "needs_attention"
    assert report.lines[0].quote is None
    inputs[2].decisions["req_001"] = RequirementReview(
        requirement_id="req_001", status="confirmed", note="Source checked", updated_at=NOW
    )
    assert build_report(*inputs).lines[0].quote is not None


def test_excluded_rows_are_preserved_full_fidelity_but_unpriced():
    inputs = _supported(_inputs())
    inputs[2].decisions["req_001"] = RequirementReview(
        requirement_id="req_001", status="excluded", note="Not applicable", updated_at=NOW
    )
    line = build_report(*inputs).lines[0]
    assert line.status == "excluded"
    assert line.extraction_review_state == "excluded"
    assert line.requirement == inputs[1].extraction.requirements[0]
    assert line.quote is None


def test_stale_unknown_quantity_is_flagged_and_never_billed():
    inputs = _supported(_inputs())
    inputs[-1].quantities["req_missing"] = inputs[-1].quantities.pop("req_001")
    report = build_report(*inputs)
    assert report.totals_by_currency == {}
    assert any("req_missing: stale" in message for message in report.warnings)


def test_coverage_hints_and_source_warnings_remain_document_level():
    inputs = _inputs()
    inputs[0].document.pages[0].text += " ISO 15184"
    inputs[0].document.warnings = ["Synthetic source warning"]
    report = build_report(*inputs)
    assert "Synthetic source warning" in report.warnings
    assert any("Coverage review" in message for message in report.warnings)
    assert any("Regional / alternative" in message for message in report.warnings)


def test_all_source_inputs_are_fingerprinted_and_report_time_id_are_not():
    inputs = _supported(_inputs())
    fingerprint = input_fingerprint(*inputs)
    first = build_report(*inputs, report_id="report_a", created_at=NOW)
    second = build_report(
        *inputs, report_id="report_b", created_at=datetime(2026, 10, 18, tzinfo=UTC)
    )
    assert first.input_fingerprint == second.input_fingerprint == fingerprint
    inputs[3].capabilities[0].price = Decimal("36.00")
    assert input_fingerprint(*inputs) != fingerprint
    fingerprint = input_fingerprint(*inputs)
    inputs[-1].mappings["req_001"].note = "Changed reviewed scope"
    assert input_fingerprint(*inputs) != fingerprint


def test_build_does_not_mutate_sources_and_snapshot_does_not_share_mutable_data():
    inputs = _supported(_inputs())
    before = [item.model_dump_json() for item in inputs[1:]]
    report = build_report(*inputs)
    assert [item.model_dump_json() for item in inputs[1:]] == before
    assert report.source_run.raw_response == "  original raw output\n"
    inputs[1].extraction.requirements[0].applicability.append("Changed later")
    inputs[-1].mappings["req_001"].note = "Changed later"
    inputs[3].capabilities[0].price = Decimal("99")
    assert "Changed later" not in report.lines[0].requirement.applicability
    assert report.workspace.mappings["req_001"].note != "Changed later"
    assert report.lines[0].capability.price == Decimal("35.00")


@pytest.mark.parametrize("index", [1, 2, 5])
def test_cross_document_or_run_inputs_rejected(index):
    inputs = list(_inputs())
    inputs[index].document_id = "b" * 16
    with pytest.raises(ValueError, match="different source documents"):
        build_report(*inputs)


@pytest.mark.parametrize("verdict", ["supported", "unsupported"])
@pytest.mark.parametrize("fingerprint", [None, "b" * 64])
def test_unbound_or_stale_scope_verdict_cannot_approve_or_reject_conditions(verdict, fingerprint):
    inputs = _supported(_inputs())
    inputs[-1].mappings["req_001"].lab_fingerprint = fingerprint
    inputs[-1].mappings["req_001"].capability_verdict = verdict
    report = build_report(*inputs)
    line = report.lines[0]
    assert line.canonical_test_id == "cross_cut_adhesion"
    assert line.status == "in_house"
    assert "Renew the scope review" in " ".join(line.reasons)
    assert line.quote is None
    assert report.totals_by_currency == {}


@pytest.mark.parametrize("fingerprint", [None, "b" * 64])
def test_quantity_approval_requires_its_own_current_lab_fingerprint(fingerprint):
    inputs = _supported(_inputs())
    inputs[-1].quantities["req_001"].lab_fingerprint = fingerprint
    report = build_report(*inputs)
    line = report.lines[0]
    assert "Human review confirmed" in " ".join(line.reasons)
    assert line.quote is None
    assert report.totals_by_currency == {}


@pytest.mark.parametrize(
    ("field", "replacement"),
    [
        ("price", Decimal("40")),
        ("pricing_basis", "per batch"),
        ("declared_constraints", "Metal only"),
        ("currency", "EUR"),
    ],
)
def test_lab_declaration_changes_invalidate_both_prior_approvals(field, replacement):
    inputs = _supported(_inputs())
    assert build_report(*inputs).lines[0].quote is not None
    fingerprint = lab_fingerprint(inputs[3])
    setattr(inputs[3].capabilities[0], field, replacement)
    assert lab_fingerprint(inputs[3]) != fingerprint
    report = build_report(*inputs)
    assert report.lines[0].quote is None
    assert "Renew the scope review" in " ".join(report.lines[0].reasons)
    inputs[-1].mappings["req_001"].lab_fingerprint = lab_fingerprint(inputs[3])
    assert build_report(*inputs).lines[0].quote is None
    inputs[-1].quantities["req_001"].lab_fingerprint = lab_fingerprint(inputs[3])
    assert build_report(*inputs).lines[0].quote is not None


def test_draft_amounts_are_exact_not_rounded_to_two_currency_decimals():
    inputs = _inputs()
    inputs[3].capabilities[0].price = Decimal("0.12345")
    inputs[3].capabilities[0].currency = "KWD"
    report = build_report(*_supported(inputs, quantity="2.5"))
    assert report.lines[0].quote.subtotal == Decimal("0.308625")
    assert report.totals_by_currency == {"KWD": Decimal("0.308625")}
    assert any("currency-specific minor-unit" in warning for warning in report.warnings)


def test_product_and_currency_sum_are_exact_even_under_small_decimal_context():
    inputs = _inputs([_requirement(), _requirement("Gloss", ("ISO 2813",), "req_002")])
    inputs[3].capabilities[0].price = Decimal("123456789012345678901234567890.123456789")
    inputs[3].capabilities[1].price = Decimal("0.000000000000001")
    _supported(inputs, quantity="2")
    inputs[-1].mappings["req_002"] = MappingChoice(
        canonical_test_id="gloss",
        capability_verdict="supported",
        lab_fingerprint=lab_fingerprint(inputs[3]),
        note="Engineer checked gloss scope.",
    )
    inputs[-1].quantities["req_002"] = QuoteChoice(
        quantity=1,
        scope_and_basis_confirmed=True,
        lab_fingerprint=lab_fingerprint(inputs[3]),
        note="One separately applicable panel.",
    )
    with localcontext() as context:
        context.prec = 4
        report = build_report(*inputs)
    assert report.lines[0].quote.subtotal == Decimal("246913578024691357802469135780.246913578")
    assert report.totals_by_currency == {
        "USD": Decimal("246913578024691357802469135780.246913578000001")
    }


def test_snapshot_rejects_inconsistent_line_arithmetic():
    report = build_report(*_supported(_inputs()))
    data = report.model_dump()
    data["lines"][0]["quote"]["subtotal"] = Decimal("999")
    data["totals_by_currency"] = {"USD": Decimal("999")}
    with pytest.raises(ValidationError, match="exact quantity"):
        ReportSnapshot.model_validate(data)


@pytest.mark.parametrize("totals", [{}, {"USD": Decimal("999")}, {"EUR": Decimal("105")}])
def test_snapshot_rejects_currency_total_mismatch(totals):
    data = build_report(*_supported(_inputs())).model_dump()
    data["totals_by_currency"] = totals
    with pytest.raises(ValidationError, match="currency totals"):
        ReportSnapshot.model_validate(data)


@pytest.mark.parametrize("field", ["status", "extraction_review_state"])
def test_snapshot_rejects_priced_excluded_lines(field):
    data = build_report(*_supported(_inputs())).model_dump()
    data["lines"][0][field] = "excluded"
    with pytest.raises(ValidationError, match="Excluded requirements"):
        ReportSnapshot.model_validate(data)


def test_snapshot_validates_exact_quoted_source_data_on_round_trip():
    report = build_report(*_supported(_inputs(), quantity="3.001"))
    assert ReportSnapshot.model_validate_json(report.model_dump_json()) == report
