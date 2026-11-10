"""Literal source recovery preserves facts and refuses ambiguous ownership."""

import hashlib
from copy import deepcopy
from decimal import Decimal

import pytest

from scione.schemas import DocumentPage, IngestedDocument, TextExtractionStatus, TextReadingOrder
from scione.specifications.atom_citations import MAX_ANCHOR_GAP, enrich_atom_citations
from scione.specifications.models import (
    CustomerDocumentMetadata,
    CustomerExtraction,
    CustomerRequirement,
    RequestedMethod,
    RequirementEvidence,
    RequirementValue,
)
from scione.structured.models import AtomicClaim
from scione.structured.normalization import normalize_requirement

NAME = "Water jet adhesion"
RAW = "Water temperature 60 °C"


def document(*texts):
    return IngestedDocument(
        document_id="source_fixture",
        file_name="source.txt",
        sha256=hashlib.sha256("\n".join(texts).encode()).hexdigest(),
        page_count=len(texts),
        pages=[
            DocumentPage(
                page_number=index,
                text=text,
                character_count=len(text),
                non_whitespace_character_count=len("".join(text.split())),
            )
            for index, text in enumerate(texts, 1)
        ],
        text_reading_order=TextReadingOrder.CONTENT_STREAM,
        extraction_status=TextExtractionStatus.READY,
    )


def row(name=NAME, raw=RAW, *, identifier="req_one", page=1, evidence=None, methods=()):
    return CustomerRequirement(
        id=identifier,
        original_test_name=name,
        requested_methods=[RequestedMethod(code=code) for code in methods],
        conditions=[RequirementValue(name="Customer condition", raw_value=raw)],
        evidence=evidence or [RequirementEvidence(page=page, quote=name)],
        atomic_claims=[
            AtomicClaim(
                id=f"{identifier}_claim_1",
                parameter_id="water_temperature",
                label="Water temperature",
                kind="condition",
                stage="measurement",
                operator="=",
                value=60,
                unit="°C",
                raw_text=raw,
                source_pages=[page],
            )
        ],
    )


def extraction(*rows):
    return CustomerExtraction(
        schema_version="0.1",
        document=CustomerDocumentMetadata(),
        requirements=list(rows or [row()]),
    )


def test_adds_only_verbatim_source_span_and_preserves_every_machine_fact():
    source = document(f"Unrelated document header\n{NAME}\n{RAW}.\n")
    given = extraction()
    before = given.model_dump(mode="json")
    enriched, actions = enrich_atom_citations(given, source)
    assert given.model_dump(mode="json") == before
    assert enriched is not given
    assert enriched.requirements[0].atomic_claims == given.requirements[0].atomic_claims
    assert enriched.requirements[0].conditions == given.requirements[0].conditions
    assert enriched.requirements[0].evidence[-1].quote == RAW
    assert len(actions) == 1
    action = actions[0]
    assert action.policy_version == "source_atom_citation_v1"
    assert action.action == "added_atom_evidence"
    assert action.requirement_id == "req_one"
    assert action.claim_ids == ["req_one_claim_1"]
    assert action.page == 1
    assert source.pages[0].text[action.source_start : action.source_end] == action.citation == RAW


def test_existing_valid_quote_is_a_noop_and_second_pass_is_idempotent():
    source = document(f"{NAME}\n{RAW}.")
    given = extraction()
    enriched, actions = enrich_atom_citations(given, source)
    assert actions
    again, repeated = enrich_atom_citations(enriched, source)
    assert repeated == []
    assert again == enriched
    quoted = extraction(row(evidence=[RequirementEvidence(page=1, quote=f"{NAME}\n{RAW}.")]))
    assert enrich_atom_citations(quoted, source) == (quoted, [])


def test_matches_wrapping_whitespace_and_keeps_original_offsets_and_case():
    source = document(f"{NAME}\nWater\n temperature   60 °C.")
    enriched, actions = enrich_atom_citations(extraction(), source)
    assert len(actions) == 1
    assert actions[0].citation == "Water\n temperature   60 °C"
    assert enriched.requirements[0].atomic_claims[0].raw_text == RAW


def test_unique_method_quote_can_anchor_paraphrased_parent_and_preceding_preparation():
    source = document(f"Original source heading\n{RAW}.\nMethod SYN-AB-108.")
    given = extraction(
        row(
            "Paraphrased customer request",
            methods=["SYN-AB-108"],
            evidence=[RequirementEvidence(page=1, quote="Method SYN-AB-108.")],
        )
    )
    enriched, actions = enrich_atom_citations(given, source)
    assert len(actions) == 1
    assert enriched.requirements[0].original_test_name == "Paraphrased customer request"


def test_page_two_needs_own_valid_evidence_as_well_as_global_parent_identity():
    source = document("Method SYN-AB-108.", f"Specimen is steel.\n{RAW}.")
    given = extraction(
        row(
            "Paraphrased request",
            page=2,
            methods=["SYN-AB-108"],
            evidence=[
                RequirementEvidence(page=1, quote="Method SYN-AB-108."),
                RequirementEvidence(page=2, quote="Specimen is steel."),
            ],
        )
    )
    enriched, actions = enrich_atom_citations(given, source)
    assert len(actions) == 1 and actions[0].page == 2
    assert enriched.requirements[0].atomic_claims[0].source_pages == [2]
    given.requirements[0].evidence.pop()
    assert enrich_atom_citations(given, source)[1] == []


@pytest.mark.parametrize(
    "texts",
    [
        (f"{NAME}\n{RAW}.\nOther content\n{RAW}.",),
        (f"{NAME}\n{RAW}.", f"Other page\n{RAW}."),
    ],
)
def test_duplicate_source_phrase_anywhere_in_document_remains_ambiguous(texts):
    given = extraction()
    assert enrich_atom_citations(given, document(*texts)) == (given, [])


def test_wrong_claimed_page_is_not_repaired_by_searching_another_page():
    source = document(NAME, f"Other source heading\n{RAW}.")
    given = extraction()
    assert enrich_atom_citations(given, source) == (given, [])


def test_parent_identity_cannot_be_borrowed_without_valid_existing_identity_evidence():
    given = extraction(row(evidence=[RequirementEvidence(page=1, quote="Unrelated valid line")]))
    source = document(f"{NAME}\nUnrelated valid line\n{RAW}.")
    assert enrich_atom_citations(given, source) == (given, [])


def test_atom_not_retained_in_own_raw_fields_cannot_gain_grounding_from_source_alone():
    given = extraction()
    given.requirements[0].conditions = [
        RequirementValue(name="Condition", raw_value="Duration 10 s")
    ]
    source = document(f"{NAME}\n{RAW}.")
    assert enrich_atom_citations(given, source) == (given, [])


def test_other_parent_raw_field_ownership_blocks_neighbor_fact_even_on_same_page():
    given = extraction(row(), row("Different test", identifier="req_other"))
    source = document(f"{NAME}\nDifferent test\n{RAW}.")
    assert enrich_atom_citations(given, source) == (given, [])


def test_other_parent_source_anchor_blocks_crossing_its_section():
    other = row("Different test", "Duration 10 s", identifier="req_other")
    source = document(f"{NAME}\nDifferent test\n{RAW}.\nDuration 10 s")
    given = extraction(row(), other)
    enriched, actions = enrich_atom_citations(given, source)
    assert not any(action.requirement_id == "req_one" for action in actions)
    assert enriched.requirements[0] == given.requirements[0]


def test_unextracted_explicit_foreign_test_heading_is_a_boundary():
    given = extraction()
    source = document(f"{NAME}\nTest B\n{RAW}.")
    assert enrich_atom_citations(given, source) == (given, [])


def test_detached_far_span_or_blank_paragraph_splice_does_not_gain_evidence():
    given = extraction()
    source = document(f"{NAME}\n{'x' * (MAX_ANCHOR_GAP + 2)}\n{RAW}.")
    assert enrich_atom_citations(given, source) == (given, [])
    detached = document(f"{NAME}\nWater\n\n temperature 60 °C.")
    assert enrich_atom_citations(given, detached) == (given, [])


@pytest.mark.parametrize("raw", ["60", "60 °C", "D65", "AX-17", "0B to 10B", "23 h"])
def test_bare_number_unit_or_code_fragment_cannot_establish_parameter_ownership(raw):
    given = extraction(row(raw=raw))
    source = document(f"{NAME}\n{raw}.")
    assert enrich_atom_citations(given, source) == (given, [])


@pytest.mark.parametrize(
    "source_text,raw",
    [
        ("Water temperature 160 °C", "Water temperature 60 °C"),
        ("Water temperature 60 °C", "Water temperature 60 °c"),
        ("Probe AX-17B is required", "Probe AX-17"),
        ("Probe AX–17 is required", "Probe AX-17"),
        ("Temperature is 0.10 °C", "Temperature is 0.1 °C"),
    ],
)
def test_numbers_case_symbols_and_embedded_codes_are_never_normalized_to_create_match(
    source_text, raw
):
    given = extraction(row(raw=raw))
    source = document(f"{NAME}\n{source_text}.")
    assert enrich_atom_citations(given, source) == (given, [])


def test_duplicate_atom_span_merges_action_claim_ids_without_duplicate_evidence():
    given = extraction()
    copied = deepcopy(given.requirements[0].atomic_claims[0])
    copied.id = "req_one_claim_2"
    given.requirements[0].atomic_claims.append(copied)
    enriched, actions = enrich_atom_citations(given, document(f"{NAME}\n{RAW}."))
    assert len(actions) == 1
    assert actions[0].claim_ids == ["req_one_claim_1", "req_one_claim_2"]
    assert len(enriched.requirements[0].evidence) == 2


def test_invalid_old_citation_is_preserved_and_not_silently_repaired():
    given = extraction()
    given.requirements[0].evidence.append(RequirementEvidence(page=1, quote="Invented old quote"))
    enriched, actions = enrich_atom_citations(given, document(f"{NAME}\n{RAW}."))
    assert actions
    assert enriched.requirements[0].evidence[1].quote == "Invented old quote"
    assert given.requirements[0].evidence[1].quote == "Invented old quote"


def test_a_found_span_does_not_repair_or_certify_wrong_numeric_interpretation():
    given = extraction()
    given.requirements[0].atomic_claims[0].value = Decimal("999")
    enriched, actions = enrich_atom_citations(given, document(f"{NAME}\n{RAW}."))
    assert actions
    assert enriched.requirements[0].atomic_claims[0].value == 999
    normalized = normalize_requirement(enriched.requirements[0])
    supplied = next(claim for claim in normalized.claims if claim.id == "req_one_claim_1")
    assert supplied.normalization_state == "unresolved"


def test_shared_or_repeated_method_reference_does_not_supply_unique_parent_identity():
    given = extraction(
        row(
            "Paraphrased request",
            methods=["SYN-AB-108"],
            evidence=[RequirementEvidence(page=1, quote="Method SYN-AB-108.")],
        )
    )
    source = document(f"Method SYN-AB-108.\n{RAW}.", "Appendix: SYN-AB-108")
    assert enrich_atom_citations(given, source) == (given, [])
