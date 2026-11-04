"""Explicit human-prepared example for a single bundled synthetic document.

This is an offline worked example, never model inference or an accuracy score.
It is available only for the exact source PDF it was reviewed against.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from uuid import uuid4

from scione.providers import TokenUsage
from scione.specifications.models import (
    CustomerDocumentMetadata,
    CustomerExtraction,
    CustomerExtractionRun,
    CustomerRequirement,
    RequestedMethod,
    RequirementEvidence,
    RequirementValue,
)
from scione.specifications.store import StoredSpecification

CSP301_REFERENCE_SHA256 = "f2e64188ba1a3c61357df7956c4b456e1bc914ff2fe2a17b0552978c2f5b830c"


def csp301_reference_run(stored: StoredSpecification) -> CustomerExtractionRun:
    """Return a clearly labelled manual example; make no provider request."""
    if stored.document.sha256 != CSP301_REFERENCE_SHA256:
        raise ValueError("The manual example is available only for the bundled CSP-EN-301 PDF.")
    page_text = {page.page_number: " ".join(page.text.split()) for page in stored.document.pages}

    def source(page: int, text: str) -> str:
        compact = " ".join(text.split())
        if compact not in page_text.get(page, ""):
            raise ValueError("The bundled source no longer matches the reviewed manual example.")
        return compact

    def evidence(page: int, text: str) -> RequirementEvidence:
        return RequirementEvidence(page=page, quote=source(page, text))

    def value(page: int, text: str, name: str = "Customer requirement") -> RequirementValue:
        return RequirementValue(name=name, raw_value=source(page, text))

    cycle = "five cycles between -20 °C for two hours and 70 °C for two hours"
    recovery = "a 30-minute room-temperature recovery between transfers"
    conditioning = "Allow seven days at 23 ± 2 °C and 50 ± 5% RH before initial readings."
    preparation = [
        source(1, "Submit two independently mixed topcoat lots."),
        source(
            1,
            "From each lot, coat nine flat 100 × 200 mm steel witnesses using the"
            " same e-coat, primer and basecoat as the nominated part; assign "
            "three to baseline checks, three to thermal cycling and three to "
            "dispute retention.",
        ),
        source(
            1,
            "Actual metal temperature, not oven setting, is the cure reference: "
            "20 minutes at 140 ± 3 °C.",
        ),
        source(1, "Mark a repeatable five-position map on each panel."),
    ]
    workflow = [
        source(
            2,
            "Report panel-level scores, photos of the grids, all gloss readings "
            "and the complete temperature-cycle record.",
        ),
        source(2, "Do not conceal a failed panel inside a passing batch average."),
        source(2, "Release requires written approval from Customer Materials Engineering."),
        source(
            2,
            "For release review, the supplier must retain the recorded raw "
            "observations and submit any proposed deviations before testing.",
        ),
    ]
    shared_evidence = [
        evidence(1, conditioning),
        *[RequirementEvidence(page=1, quote=text) for text in preparation],
        *[RequirementEvidence(page=2, quote=text) for text in workflow],
    ]
    shared_instructions = preparation + workflow
    adhesion = CustomerRequirement(
        id="req_001",
        original_test_name="Cross-cut adhesion",
        requested_methods=[
            RequestedMethod(code="ISO 60038:2021", role="required"),
            RequestedMethod(
                code="ASTM D1037-20",
                role=(
                    "Optional North American route; customer agreement is "
                    "required before use. Not interchangeable by default."
                ),
            ),
        ],
        conditions=[
            value(2, cycle, "Thermal-cycle exposure"),
            value(2, recovery, "Between-transfer recovery"),
            value(1, conditioning, "Shared conditioning"),
        ],
        acceptance_criteria=[
            value(2, "only class 0 or 1 is acceptable", "ISO native adhesion class")
        ],
        applicability=[
            source(
                2,
                "Repeat the check both before and after five cycles",
            )
        ],
        special_instructions=shared_instructions
        + [
            source(
                2,
                "A North American supplier may request ASTM D1037-20 for the same"
                " paint-retention question, but must identify the cutting "
                "spacing, tape, scoring and rating scale before any results are "
                "compared.",
            ),
            source(2, "We are not declaring those two procedures interchangeable."),
            source(
                2,
                "if an ASTM route is approved, our team will issue the "
                "corresponding acceptance class before testing.",
            ),
            source(
                2,
                "Any full flake lifting at a scored square, any cut-through to "
                "bare metal away from the intended score, or any site failing the"
                " approved adhesion rating is a lot failure.",
            ),
        ],
        evidence=shared_evidence
        + [
            evidence(
                2, "For this grid-and-pull assessment the receiving site should use ISO 60038:2021."
            ),
            evidence(2, cycle),
            evidence(2, recovery),
            evidence(2, "only class 0 or 1 is acceptable"),
        ],
    )
    gloss = CustomerRequirement(
        id="req_002",
        original_test_name="Specular gloss",
        requested_methods=[RequestedMethod(code="ASTM D1627-16", role="required")],
        conditions=[
            value(2, "60° specular-gloss reading", "Measurement angle"),
            value(2, "five marked locations", "Measurement locations"),
            value(2, cycle, "Shared thermal-cycle exposure"),
            value(2, recovery, "Between-transfer recovery"),
            value(1, conditioning, "Shared conditioning"),
        ],
        acceptance_criteria=[
            value(2, "The arithmetic mean must be 82-92 GU as delivered", "Initial mean gloss"),
            value(
                2,
                "after cycling, each location must retain at least 90% of its own original value",
                "Post-cycle retention at every location",
            ),
        ],
        special_instructions=shared_instructions,
        evidence=shared_evidence
        + [
            evidence(
                2,
                "Record the 60° specular-gloss reading using ASTM D1627-16 at "
                "five marked locations.",
            ),
            evidence(2, "The arithmetic mean must be 82-92 GU as delivered"),
            evidence(
                2, "after cycling, each location must retain at least 90% of its own original value"
            ),
            evidence(2, cycle),
            evidence(2, recovery),
        ],
    )
    colour = CustomerRequirement(
        id="req_003",
        original_test_name="Colour difference",
        requested_methods=[RequestedMethod(code="ISO 60570:2025", role="required")],
        conditions=[
            value(
                2,
                "compare the same panel with an untouched master under D65 lighting",
                "Master comparison and lighting",
            ),
            value(1, conditioning, "Shared conditioning"),
        ],
        acceptance_criteria=[value(2, "ΔE*ab <=1.0", "Buyer colour-difference threshold")],
        special_instructions=shared_instructions,
        evidence=shared_evidence
        + [
            evidence(2, "compare the same panel with an untouched master under D65 lighting"),
            evidence(2, "ΔE*ab <=1.0"),
            evidence(2, "Measure colour difference by ISO 60570:2025 (COR-11)"),
        ],
    )
    thickness = CustomerRequirement(
        id="req_004",
        original_test_name="Dry film thickness",
        requested_methods=[RequestedMethod(code="ISO 60152:2025", role="required")],
        conditions=[value(1, conditioning, "Shared conditioning")],
        acceptance_criteria=[value(2, "42-55 µm on each panel", "Separate topcoat thickness")],
        special_instructions=shared_instructions
        + [
            source(1, "Record total dry film build and the separate topcoat thickness"),
        ],
        evidence=shared_evidence
        + [
            evidence(1, "Record total dry film build and the separate topcoat thickness"),
            evidence(2, "Topcoat film thickness"),
            evidence(2, "42-55 µm on each panel"),
            evidence(2, "COR-04 Dry film thickness: ISO 60152:2025 on OEM steel finish."),
        ],
    )
    extraction = CustomerExtraction(
        schema_version="0.1",
        document=CustomerDocumentMetadata(
            title="Exterior Fascia Finish - Change Approval Rider",
            identifier="CSP-EN-301",
            revision="Issue A",
        ),
        requirements=[adhesion, gloss, colour, thickness],
        review_issues=[
            "req_003: The colour measurement stage is not explicit; "
            "post-cycle measurement is not assumed.",
        ],
    )
    payload = {
        "kind": "manual_reference",
        "source_sha256": stored.document.sha256,
        "description": "Explicit human-prepared example. No model was called.",
        "extraction": extraction.model_dump(mode="json"),
    }
    return CustomerExtractionRun(
        run_id=str(uuid4()),
        started_at=datetime.now(UTC),
        document_id=stored.document_id,
        strategy="compact_document",
        prompt_version="manual_reference_v1",
        schema_version="0.1",
        provider="manual_reference",
        model="human-prepared-example",
        latency_ms=0,
        usage=TokenUsage(),
        raw_response=json.dumps(payload, ensure_ascii=False),
        raw_response_kind="manual_reference",
        calls=[],
        extraction=extraction,
    )
