"""A missing metric needs a unique physical named-scale row, not a default."""

import json
from copy import deepcopy
from hashlib import sha256

import pytest

from scione.providers import StaticModelProvider
from scione.schemas import DocumentPage, IngestedDocument, TextExtractionStatus, TextReadingOrder
from scione.specifications.extraction import CustomerRequirementRunner
from scione.specifications.models import (
    CustomerDocumentMetadata,
    CustomerExtraction,
    CustomerExtractionRun,
    CustomerRequirement,
    RequestedMethod,
    RequirementEvidence,
    RequirementValue,
)
from scione.specifications.named_scale_units import (
    _literal_spans,
    enrich_named_scale_citations,
    physical_named_scale_unit_evidence,
)
from scione.structured.models import AtomicClaim

BODY = "The overall colour difference may not exceed 1.0."
PARENT = f"Measure colour difference by ISO 60570:2025. {BODY}"
ROW = "Master-panel colour drift        ΔE*ab <=1.0"


def document(*pages):
    return IngestedDocument(
        document_id="unit_source",
        file_name="colour.pdf",
        sha256=sha256("\n".join(pages).encode()).hexdigest(),
        page_count=len(pages),
        pages=[
            DocumentPage(
                page_number=index,
                text=text,
                character_count=len(text),
                non_whitespace_character_count=len("".join(text.split())),
            )
            for index, text in enumerate(pages, 1)
        ],
        text_reading_order=TextReadingOrder.GEOMETRIC,
        extraction_status=TextExtractionStatus.READY,
    )


def requirement(**claim_updates):
    native = AtomicClaim(
        id="native_colour",
        parameter_id="color_difference",
        label="Colour difference",
        kind="criterion",
        stage="measurement",
        operator="<=",
        value=1,
        raw_text=BODY,
        source_pages=[1],
    )
    native = AtomicClaim.model_validate({**native.model_dump(), **claim_updates})
    return CustomerRequirement(
        id="req_colour",
        original_test_name="Colour difference",
        requested_methods=[RequestedMethod(code="ISO 60570:2025")],
        acceptance_criteria=[RequirementValue(name="Colour difference", raw_value=BODY)],
        evidence=[RequirementEvidence(page=1, quote=PARENT)],
        atomic_claims=[native],
    )


def extraction(*rows):
    return CustomerExtraction(
        schema_version="0.1",
        document=CustomerDocumentMetadata(),
        requirements=list(rows or [requirement()]),
    )


def test_literal_unit_enrichment_preserves_every_native_fact_and_source_offset():
    given = extraction()
    before = given.model_dump(mode="json")
    source = document(f"{PARENT}\nAcceptance summary\n    {ROW}\n")
    enriched, actions = enrich_named_scale_citations(given, source)
    assert given.model_dump(mode="json") == before
    assert enriched.requirements[0].atomic_claims == given.requirements[0].atomic_claims
    assert enriched.requirements[0].acceptance_criteria == given.requirements[0].acceptance_criteria
    assert enriched.requirements[0].evidence[-1].quote == ROW
    assert len(actions) == 1
    action = actions[0]
    assert action.action == "added_unit_evidence"
    assert action.source_unit == "ΔE*ab"
    assert action.claim_ids == ["native_colour"]
    assert source.pages[0].text[action.source_start : action.source_end] == ROW
    proof = physical_named_scale_unit_evidence(
        enriched.requirements[0].atomic_claims[0],
        enriched.requirements[0],
        source,
        requirements=enriched.requirements,
    )
    assert proof["canonical"] == "ΔE*ab"
    assert proof["source"] is None
    assert proof["body_quote"] == BODY
    assert proof["document_sha256"] == source.sha256
    assert enrich_named_scale_citations(enriched, source) == (enriched, [])


def test_pdf_ligatures_retain_physical_offsets_without_fuzzy_word_repair():
    physical = PARENT.replace("difference", "diﬀerence")
    row_text = "Colour diﬀerence ΔE*ab <=1.0"
    source = document(f"{physical}\n{row_text}")
    given = extraction()
    enriched, actions = enrich_named_scale_citations(given, source)
    assert len(actions) == 1
    assert actions[0].citation == row_text
    assert source.pages[0].text[actions[0].source_start : actions[0].source_end] == row_text
    assert enriched.requirements[0].atomic_claims == given.requirements[0].atomic_claims
    assert _literal_spans("oﬃce", "office") == [(0, 4)]
    assert _literal_spans("oﬃce", "ofice") == []
    assert _literal_spans("ﬁ", "f") == []
    wrong = document(f"{PARENT.replace('difference', 'divergence')}\n{ROW}")
    assert enrich_named_scale_citations(given, wrong) == (given, [])


def test_layout_matching_never_rewrites_superscript_numbers_into_limits():
    given = extraction()
    assert enrich_named_scale_citations(
        given, document(f"{PARENT}\nColour difference ΔE*ab <=¹.⁰")
    ) == (given, [])


def test_existing_named_unit_can_be_proved_from_the_table_and_adjacent_owned_quote_segments():
    body = "The colour difference, calculated from measured L*, a* and b*, may not exceed 1.0."
    row = requirement(unit="ΔE*ab", raw_text=body).model_copy(
        update={
            "evidence": [
                RequirementEvidence(
                    page=1, quote="The colour difference, calculated from measured"
                ),
                RequirementEvidence(page=1, quote="L*, a* and b*, may not exceed 1.0."),
            ],
            "acceptance_criteria": [RequirementValue(name="Colour difference", raw_value=body)],
        }
    )
    source = document(body.replace("measured ", "measured\n ") + "\n" + ROW)
    given = extraction(row)
    enriched, actions = enrich_named_scale_citations(given, source)
    assert enriched.requirements[0].atomic_claims == row.atomic_claims
    assert {action.action for action in actions} == {"added_atom_evidence", "added_unit_evidence"}
    proof = physical_named_scale_unit_evidence(row.atomic_claims[0], row, source)
    assert proof["source"] == "ΔE*ab"
    assert proof["canonical"] == "ΔE*ab"
    assert proof["body_source_quote"] == body.replace("measured ", "measured\n ")


def test_unquoted_intervening_words_are_not_treated_as_wrapping_space():
    body = "The colour difference, calculated from measured L*, a* and b*, may not exceed 1.0."
    row = requirement(unit="ΔE*ab", raw_text=body).model_copy(
        update={
            "evidence": [
                RequirementEvidence(page=1, quote="The colour difference"),
                RequirementEvidence(page=1, quote="may not exceed 1.0."),
            ],
        }
    )
    given = extraction(row)
    assert enrich_named_scale_citations(given, document(body + "\n" + ROW)) == (given, [])


@pytest.mark.parametrize(
    "table",
    [
        "",
        "1.0",
        "ΔE*ab <=1.0",
        "Colour difference <=1.0",
        "Colour difference ΔE <=1.0",
        "Colour difference ΔE00 <=1.0",
        "Colour difference CIEDE2000 <=1.0",
        "Colour difference ΔE*ab >=1.0",
        "Colour difference ΔE*ab <=1.1",
        "Colour difference ΔE*ab <1.0",
        "Colour difference ΔE*ab <=1.0 after aging",
        "Other property ΔE*ab <=1.0",
        "Not colour difference ΔE*ab <=1.0",
        ROW + "\n" + ROW,
        ROW + "\nColour difference ΔE00 <=1.0",
        ROW + "\nColour difference ΔE*ab <=0.8",
    ],
)
def test_no_unit_default_operator_change_detached_number_or_competing_summary(table):
    given = extraction()
    enriched, actions = enrich_named_scale_citations(given, document(f"{PARENT}\n{table}"))
    assert enriched == given
    assert actions == []


@pytest.mark.parametrize(
    "updates",
    [
        {"unit": "ΔE00"},
        {"value": 2},
        {"operator": ">="},
        {"label": "Gloss"},
        {"parameter_id": "gloss"},
        {"parameter_id": "custom_delta"},
        {"kind": "instruction"},
        {"stage": "initial"},
        {"stage": "after_aging"},
        {"configuration": "route B"},
        {"sequence": 1},
        {"source_pages": []},
        {"source_pages": [1, 2]},
        {"normalization_state": "unresolved"},
        {"source_context": "after aging"},
        {"source_context": "CIEDE2000"},
        {"availability": "unknown"},
    ],
)
def test_named_scale_recovery_never_resolves_an_unproved_source_identity_or_setup(updates):
    given = extraction(requirement(**updates))
    enriched, actions = enrich_named_scale_citations(given, document(f"{PARENT}\n{ROW}"))
    assert enriched == given
    assert actions == []


def test_a_matching_unit_on_another_physical_page_cannot_be_borrowed():
    given = extraction()
    enriched, actions = enrich_named_scale_citations(given, document(PARENT, ROW))
    assert enriched == given
    assert actions == []


@pytest.mark.parametrize(
    "body",
    [
        "The optional colour difference may not exceed 1.0.",
        "If the colour difference is requested, it may not exceed 1.0.",
        "Do not apply this colour difference; it may not exceed 1.0.",
        "This is not a colour difference; it may not exceed 1.0.",
        "The colour difference may not not exceed 1.0.",
        "The pseudocolour difference may not exceed 1.0.",
    ],
)
def test_literal_but_conditional_negated_or_different_body_is_not_reinterpreted(body):
    row = requirement(raw_text=body).model_copy(
        update={
            "evidence": [RequirementEvidence(page=1, quote=body)],
            "acceptance_criteria": [RequirementValue(name="Colour difference", raw_value=body)],
        }
    )
    given = extraction(row)
    assert enrich_named_scale_citations(given, document(f"{body}\n{ROW}")) == (given, [])


def test_missing_or_repeated_physical_body_and_fabricated_parent_are_not_proof():
    source = document(f"{PARENT}\n{ROW}")
    for given in (
        extraction(requirement(raw_text="A fabricated colour difference may not exceed 1.0.")),
        extraction(
            requirement().model_copy(
                update={
                    "evidence": [RequirementEvidence(page=1, quote="Fabricated parent " + BODY)]
                }
            )
        ),
    ):
        assert enrich_named_scale_citations(given, source) == (given, [])
    given = extraction()
    assert enrich_named_scale_citations(given, document(f"{PARENT}\n{BODY}\n{ROW}")) == (given, [])


def test_competing_colour_owner_or_second_phase_prevents_summary_transfer():
    first = requirement()
    second = requirement().model_copy(update={"id": "req_other"})
    given = extraction(first, second)
    assert enrich_named_scale_citations(given, document(f"{PARENT}\n{ROW}")) == (given, [])
    second_phase = first.atomic_claims[0].model_copy(
        update={"id": "native_after", "stage": "after_aging"}
    )
    given = extraction(
        first.model_copy(update={"atomic_claims": [*first.atomic_claims, second_phase]})
    )
    assert enrich_named_scale_citations(given, document(f"{PARENT}\n{ROW}")) == (given, [])


def test_runner_keeps_original_provider_json_but_audits_new_literal_unit_evidence():
    given = extraction()
    payload = given.model_dump(mode="json")
    untouched = deepcopy(payload)
    run = CustomerRequirementRunner(StaticModelProvider(payload)).run(document(f"{PARENT}\n{ROW}"))
    assert payload == untouched
    assert run.extraction.requirements[0].atomic_claims == given.requirements[0].atomic_claims
    assert any(action.action == "added_unit_evidence" for action in run.grounding_actions)
    assert "added_unit_evidence" not in run.raw_response
    assert json.loads(run.calls[0].raw_response) == untouched
    reloaded = CustomerExtractionRun.model_validate_json(run.model_dump_json())
    assert reloaded.grounding_actions == run.grounding_actions
