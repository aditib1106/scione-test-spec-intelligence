"""Structured comparison exports stay readable, complete and business-facing."""

from __future__ import annotations

import copy
import re
import unicodedata

import pymupdf
import pytest

from scione.structured.pdf import ComparisonPDFError, render_comparison_pdf


def _payload() -> dict:
    statuses = ["fully_matched", "partially_matched", "needs_information", "not_matched"]
    names = ["Cross-cut adhesion", "Specular gloss", "Colour difference", "Dry film thickness"]
    requirements = []
    for index, (status, name) in enumerate(zip(statuses, names, strict=True)):
        check_status = ["supported", "gap", "unknown", "gap"][index]
        requirements.append(
            {
                "requirement_id": f"INTERNAL_REQUIREMENT_{index}",
                "test_name": name,
                "methods": ["ISO 60038:2021"],
                "comparison": {
                    "requirement_id": f"INTERNAL_REQUIREMENT_{index}",
                    "status": status,
                    "checks": [
                        {
                            "id": "INTERNAL_CHECK",
                            "label": "Retention",
                            "kind": "criterion",
                            "stage": "after_cycle",
                            "requested": "At least 90% of the original reading",
                            "declared": None if check_status == "unknown" else "Not reportable",
                            "status": check_status,
                            "reason": "Ask the lab to confirm post-cycle reporting.",
                            "source_pages": [2],
                        }
                    ],
                    "supported_count": int(check_status == "supported"),
                    "gap_count": int(check_status == "gap"),
                    "unknown_count": int(check_status == "unknown"),
                },
            }
        )
    return {
        "comparison_id": "INTERNAL_COMPARISON_ID",
        "created_at": "2026-11-03T12:30:00Z",
        "document": {
            "company_name": "Synthetic Vehicle Group",
            "title": "Exterior finish approval",
            "specification_id": "CSP-EN-301",
            "revision": "Issue A",
        },
        "lab_name": "Example Coatings Lab",
        "lab_version": 3,
        "requirements": requirements,
        "raw_response": "INTERNAL_MODEL_RESPONSE",
    }


def _compact(data: bytes) -> tuple[str, int]:
    with pymupdf.open(stream=data, filetype="pdf") as pdf:
        texts = []
        for index, page in enumerate(pdf, 1):
            assert page.rect == pymupdf.paper_rect("a4")
            text = page.get_text()
            assert f"Page {index} of {len(pdf)}" in text
            red, green, blue = page.get_pixmap().pixel(10, 10)
            assert red < 45 and green < 75 and blue < 120
            texts.append(text)
        text = unicodedata.normalize("NFKC", " ".join(texts))
        return re.sub(r"\s+", " ", text), len(pdf)


def test_structured_pdf_uses_saved_comparison_without_mutating_or_leaking_diagnostics() -> None:
    payload = _payload()
    before = copy.deepcopy(payload)

    data = render_comparison_pdf(payload)

    assert data.startswith(b"%PDF-")
    text, pages = _compact(data)
    assert pages <= 6
    for expected in [
        "Synthetic Vehicle Group",
        "CSP-EN-301",
        "Issue A",
        "Example Coatings Lab",
        "03 Nov 2026",
        "4 tests assessed",
        "Fully matched",
        "Partially matched",
        "Needs information",
        "Not matched",
        "Not recorded by the lab",
        "Spec p. 2",
        "Ask the lab to confirm",
        "Pricing has not been calculated",
    ]:
        assert expected in text
    assert "INTERNAL_" not in text
    assert payload == before


def test_structured_pdf_escapes_html_and_preserves_long_requirements() -> None:
    payload = _payload()
    payload["document"]["company_name"] = "<script>company</script> & coatings"
    check = payload["requirements"][0]["comparison"]["checks"][0]
    check["requested"] = "START_MARKER " + "A complete requirement. " * 120 + " END_MARKER"

    text, _pages = _compact(render_comparison_pdf(payload))

    assert "<script>company</script> & coatings" in text
    assert "START_MARKER" in text
    assert "END_MARKER" in text


def test_structured_pdf_keeps_excluded_tests_outside_assessed_counts() -> None:
    payload = _payload()
    payload["requirements"][0]["excluded"] = True

    text, _pages = _compact(render_comparison_pdf(payload))

    assert "3 tests assessed" in text
    assert "1 tests excluded" in text


def test_structured_pdf_rejects_invalid_saved_comparison() -> None:
    payload = _payload()
    del payload["requirements"][0]["comparison"]["checks"]
    with pytest.raises(ComparisonPDFError, match="Unable to create"):
        render_comparison_pdf(payload)


def test_manual_reference_pdf_is_labelled_without_printing_provider_identifiers() -> None:
    payload = _payload()
    payload["document"]["provider"] = "manual_reference"
    text, _pages = _compact(render_comparison_pdf(payload))
    assert "Prepared reference example; no model was called." in text
    assert "manual_reference" not in text


def test_identical_shared_instructions_appear_once_with_test_scope() -> None:
    payload = _payload()
    instruction = {
        "id": "shared_source",
        "label": "Instruction Test one",
        "kind": "instruction",
        "stage": "general",
        "requested": "Submit two independently mixed topcoat lots.",
        "declared": None,
        "status": "informational",
        "reason": "Retain this instruction for the lab workflow.",
        "source_pages": [1],
    }
    for index in [0, 1]:
        clone = copy.deepcopy(instruction)
        clone["id"] = f"instruction_{index}"
        clone["label"] = f"Instruction Test {index}"
        payload["requirements"][index]["comparison"]["checks"].append(clone)
    before = copy.deepcopy(payload)

    text, _pages = _compact(render_comparison_pdf(payload))

    assert text.count("Submit two independently mixed topcoat lots.") == 1
    assert "Shared requirements" in text
    assert "Applies to: Cross-cut adhesion, Specular gloss" in text
    assert payload == before


def test_shared_duration_checks_keep_distinct_thermal_cycle_steps() -> None:
    payload = _payload()
    for index in [0, 1]:
        for step in [1, 2]:
            payload["requirements"][index]["comparison"]["checks"].append(
                {
                    "id": f"cycle_{index}_{step}",
                    "parameter_id": "duration",
                    "label": "Duration",
                    "kind": "condition",
                    "stage": "thermal_cycle",
                    "unit": "h",
                    "requested": "2 h",
                    "declared": "2 h",
                    "status": "supported",
                    "reason": "The same cycle step is declared.",
                    "source_pages": [2],
                    "requirement_claim": {
                        "id": f"claim_{index}_{step}",
                        "parameter_id": "duration",
                        "label": "Duration",
                        "kind": "condition",
                        "stage": "thermal_cycle",
                        "operator": "=",
                        "value": "2",
                        "unit": "h",
                        "raw_text": "two hours at the corresponding cycle temperature",
                        "sequence": step,
                        "source_pages": [2],
                    },
                }
            )

    text, _pages = _compact(render_comparison_pdf(payload))

    assert "Step 1" in text
    assert "Step 2" in text
    assert text.count("The same cycle step is declared.") == 2
