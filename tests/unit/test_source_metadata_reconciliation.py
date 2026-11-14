"""Existing physical source proofs resolve only their own parser metadata flags."""

from scione.specifications.models import CustomerRequirement
from scione.structured.models import AtomicClaim
from scione.structured.normalization import _unparsed_qualification, normalize_requirement


def test_ordered_owned_context_words_do_not_create_a_second_source_review_gate():
    body = (
        "Record total dry film build and the separate topcoat thickness; "
        "the latter must be between 42 and 55 µm on every panel."
    )
    raw = "the latter must be between 42 and 55 µm on every panel"
    row = CustomerRequirement(
        id="req_owned",
        original_test_name="Topcoat dry film thickness measurement",
        atomic_claims=[
            AtomicClaim(
                id="native",
                parameter_id="topcoat_thickness",
                label="Topcoat thickness",
                kind="criterion",
                operator="range",
                minimum=42,
                maximum=55,
                unit="µm",
                raw_text=raw,
                source_context="separate topcoat thickness; on every panel",
                source_pages=[1],
            )
        ],
        acceptance_criteria=[dict(name="Limit", raw_value=raw)],
        evidence=[dict(page=1, quote=body)],
    )
    normalized = normalize_requirement(row)
    assert not normalized.review_issues
    assert any(
        a.get("rule") == "same_occurrence_metadata_source_proof"
        for a in normalized.normalization_actions
    )
    row.atomic_claims[0].source_context += "; under nitrogen"
    assert normalize_requirement(row).review_issues


def test_a_source_owned_numeric_text_descriptor_stays_retained_without_orphan_digits():
    value = "100 × 200 mm"
    atom = AtomicClaim(
        id="native_geometry",
        parameter_id="panel_geometry",
        label="Geometry",
        operator="text",
        text_value=value,
        raw_text=f"Geometry {value} except aluminium.",
        source_pages=[1],
    )
    before = atom.model_dump_json()
    residual = _unparsed_qualification(atom.raw_text, [], [atom], source_text=atom.raw_text)
    assert "100" not in residual and "200" not in residual
    assert "except aluminium" in residual
    assert atom.model_dump_json() == before
    # A second occurrence outside the exact retained descriptor is not consumed.
    raw = atom.raw_text + " with 100 screws."
    assert "100" in _unparsed_qualification(raw, [], [atom], source_text=raw)
