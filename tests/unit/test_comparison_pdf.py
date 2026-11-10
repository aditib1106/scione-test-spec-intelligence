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


def test_document_revision_question_remains_visible_without_repeating_per_test() -> None:
    payload = _payload()
    question = "Header says Rev A while the issue block says Rev B. Confirm the revision."
    payload["document"]["document_review_issues"] = [question]
    before = copy.deepcopy(payload)
    text, _pages = _compact(render_comparison_pdf(payload))
    assert "Check document revision" in text
    assert text.count(question) == 1
    assert payload == before


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


def test_saved_web_candidate_hints_do_not_break_or_change_the_printed_assessment():
    payload = _payload()
    check = payload["requirements"][2]["comparison"]["checks"][0]
    check["candidate_lab_entries"] = [{
        "claim_id": "UNCONFIRMED_CANDIDATE_ID",
        "label": "Unconfirmed nearby lab field",
        "value": "UNCONFIRMED_CANDIDATE_VALUE",
        "stage": "general",
        "context": None,
        "confirmed": False,
    }]
    before = copy.deepcopy(payload)
    text, _pages = _compact(render_comparison_pdf(payload))
    assert "Needs information" in text
    assert "Not recorded by the lab" in text
    assert "UNCONFIRMED_CANDIDATE" not in text
    assert payload == before


def test_pdf_adapter_still_rejects_unrecognized_machine_check_fields():
    payload = _payload()
    payload["requirements"][0]["comparison"]["checks"][0]["invented_machine_verdict"] = True
    with pytest.raises(ComparisonPDFError):
        render_comparison_pdf(payload)


def test_long_report_keeps_shared_preparation_and_every_test_on_complete_pages():
    payload = _payload()
    for index, row in enumerate(payload["requirements"]):
        checks = []
        for number in range(24):
            checks.append({
                "id": f"own_{index}_{number}",
                "parameter_id": f"measurement_{index}_{number}",
                "label": f"Specific measurement {number + 1}",
                "kind": "criterion", "stage": "measurement",
                "requested": f"OWN_{index}_{number} Customer threshold and reporting context.",
                "declared": "Recorded laboratory operating and reporting range.",
                "status": "supported",
                "reason": "The requested result is within this method's declared capability.",
                "source_pages": [2],
            })
        for number in range(25):
            checks.append({
                "id": f"shared_{index}_{number}",
                "parameter_id": f"preparation_{number}",
                "label": f"Shared preparation setting {number + 1}",
                "kind": "condition", "stage": "conditioning",
                "requested": f"SHARED_{number}_VALUE Customer preparation and handling details.",
                "declared": "Recorded laboratory preparation setting.",
                "status": "supported",
                "reason": "The preparation is included in the saved declaration.",
                "source_pages": [1],
            })
        row["comparison"]["checks"] = checks
    before = copy.deepcopy(payload)
    text, pages = _compact(render_comparison_pdf(payload))
    assert 4 < pages < 64
    for index in range(4):
        assert f"OWN_{index}_0" in text and f"OWN_{index}_23" in text
    assert text.count("SHARED_0_VALUE") == 1
    assert text.count("SHARED_24_VALUE") == 1
    assert "Shared requirements" in text
    assert payload == before


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


def test_long_combined_row_context_flows_and_following_table_restores_full_columns():
    from scione.structured.pdf import _CHECK_TABLE_OPEN, _html

    payload = _payload()
    check = payload["requirements"][0]["comparison"]["checks"][0]
    check["requested"] = "BEGIN_REQUEST " + "requested detail " * 12 + " END_REQUEST"
    check["reason"] = "BEGIN_REASON " + "reason detail " * 12 + " END_REASON"
    check["requirement_claim"] = {
        "id": "context_atom",
        "parameter_id": "custom_metric",
        "label": "Retention",
        "kind": "criterion",
        "operator": "text",
        "text_value": check["requested"],
        "raw_text": check["requested"],
        "source_context": "CONTEXT_MARKER " + "each panel " * 14,
        "configuration": "CONFIGURATION_MARKER",
        "stage": "after_cycle",
    }
    payload["requirements"][0]["comparison"]["checks"].append(
        {
            "id": "following",
            "label": "FOLLOWING_ROW",
            "kind": "scope",
            "requested": "FOLLOWING_VALUE",
            "status": "unknown",
            "reason": "FOLLOWING_REASON",
        }
    )
    html = _html(payload)
    assert "</div>" + _CHECK_TABLE_OPEN in html
    assert re.search(r"</div><table>(?!<tr><th)", html) is None
    text, _pages = _compact(render_comparison_pdf(payload))
    for marker in [
        "BEGIN_REQUEST",
        "END_REQUEST",
        "BEGIN_REASON",
        "END_REASON",
        "CONTEXT_MARKER",
        "CONFIGURATION_MARKER",
        "FOLLOWING_ROW",
        "FOLLOWING_VALUE",
    ]:
        assert marker in text


def test_equal_values_with_different_source_material_or_statistic_never_merge():
    payload = _payload()
    for index, context in enumerate(["CONCRETE_MEAN of six cores", "STEEL_INDIVIDUAL specimen"]):
        payload["requirements"][index]["comparison"]["checks"].append(
            {
                "id": f"strength_{index}",
                "parameter_id": "compressive_strength",
                "label": "Strength",
                "kind": "condition",
                "requested": "38 MPa",
                "declared": None,
                "status": "unknown",
                "reason": "Declare the matching material and statistic.",
                "requirement_claim": {
                    "id": f"strength_atom_{index}",
                    "parameter_id": "compressive_strength",
                    "label": "Strength",
                    "operator": "=",
                    "value": 38,
                    "unit": "MPa",
                    "raw_text": "Strength 38 MPa",
                    "source_context": context,
                    "configuration": "Rig-A",
                },
            }
        )
    text, _pages = _compact(render_comparison_pdf(payload))
    assert text.count("Declare the matching material and statistic.") == 2
    assert "CONCRETE_MEAN" in text and "STEEL_INDIVIDUAL" in text and "Rig-A" in text


def test_renderer_timeout_discards_partial_bytes_and_never_forwards_credentials(monkeypatch):
    import subprocess

    from scione.structured import pdf as module

    monkeypatch.setenv("SAIL_API_KEY", "not-forwarded")
    monkeypatch.setenv("SCIONE_DATABASE_URL", "not-forwarded")
    captured = {}

    def stalled(command, **options):
        captured.update(command=command, **options)
        raise subprocess.TimeoutExpired(command, options["timeout"], output=b"%PDF-PARTIAL")

    monkeypatch.setattr(module.subprocess, "run", stalled)
    with pytest.raises(ComparisonPDFError, match="20-second rendering limit") as caught:
        render_comparison_pdf(_payload())
    assert "PARTIAL" not in str(caught.value)
    assert "web report and JSON remain available" in str(caught.value)
    assert captured["timeout"] == 20
    assert captured["command"][-2:] == ["-m", "scione.structured.pdf_worker"]
    assert "SAIL_API_KEY" not in captured["env"] and "SCIONE_DATABASE_URL" not in captured["env"]


@pytest.mark.parametrize("returncode,output", [(1, b"%PDF-PARTIAL"), (0, b"unexpected output")])
def test_failed_or_nonpdf_worker_output_is_never_returned(monkeypatch, returncode, output):
    from types import SimpleNamespace

    from scione.structured import pdf as module

    monkeypatch.setattr(
        module.subprocess,
        "run",
        lambda *_a, **_k: SimpleNamespace(
            returncode=returncode, stdout=output, stderr=b"source text must not leak"
        ),
    )
    with pytest.raises(ComparisonPDFError) as caught:
        render_comparison_pdf(_payload())
    assert "source text" not in str(caught.value)


@pytest.mark.parametrize("mode", ["page", "callback", "elapsed"])
def test_worker_inline_renderer_has_explicit_layout_bounds(monkeypatch, mode):
    from scione.structured import pdf as module

    class LayoutLimitProbe:
        def __init__(self, **_kwargs):
            pass

        def write_with_links(self, callback):
            if mode == "page":
                callback(65, pymupdf.Rect())
            elif mode == "callback":
                for _ in range(257):
                    callback(0, pymupdf.Rect())
            else:
                callback(0, pymupdf.Rect())
            pytest.fail("The renderer must stop before accepting a partial document.")

    monkeypatch.setattr(module.pymupdf, "Story", LayoutLimitProbe)
    if mode == "elapsed":
        ticks = iter([0, 11])
        monkeypatch.setattr(module.time, "monotonic", lambda: next(ticks))
    with pytest.raises(ComparisonPDFError, match="layout limit"):
        module._render_comparison_pdf_inline(_payload())
