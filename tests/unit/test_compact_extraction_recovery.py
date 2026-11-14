"""Benign wire cleanup cannot relax source facts or method approval requirements."""

import copy
import json

import pytest

from scione.providers import StaticModelProvider
from scione.reports.matching import _selected_methods
from scione.schemas import DocumentPage, IngestedDocument
from scione.specifications.extraction import CustomerExtractionError, CustomerRequirementRunner
from scione.specifications.inventory import has_applicability_exception
from scione.specifications.models import CustomerExtractionRun
from scione.specifications.review import assess_requirements
from scione.specifications.review_audits import nonblocking_shared_clause_issues

SOURCE = (
    "Adhesion check. Use ISO 60038:2021 as the primary method. "
    "ASTM D1037-20 is optional with prior buyer approval. Temperature 31 °C."
)


def document():
    return IngestedDocument(
        document_id="source_roles", file_name="roles.pdf", sha256="a" * 64,
        page_count=1,
        pages=[DocumentPage(page_number=1, text=SOURCE, character_count=len(SOURCE),
                            non_whitespace_character_count=len(SOURCE.replace(" ", "")))],
        text_reading_order="content_stream", extraction_status="ready",
    )


def payload():
    return {
        "document": {"title": None, "identifier": None, "revision": None},
        "requirements": [{
            "test": "Adhesion check",
            "methods": ["ISO 60038:2021", "ASTM D1037-20"],
            "conditions": ["Temperature 31 °C."], "criteria": [], "scope": [],
            "instructions": [], "evidence": [{"page": 1, "quote": SOURCE}],
            "atomic_claims": [], "uncertainty": None,
        }],
        "review_issues": [],
    }


def run(value):
    return CustomerRequirementRunner(StaticModelProvider(value)).run(
        document(), strategy="compact_document"
    )


def test_blank_optional_entries_are_audited_and_raw_provider_bytes_survive():
    value = payload()
    value["requirements"][0]["instructions"] = ["Keep the source record", " \n\t"]
    value["review_issues"] = [""]
    original = copy.deepcopy(value)
    result = run(value)
    assert json.loads(result.raw_response) == original
    assert result.calls[0].raw_response == result.raw_response
    assert value == original
    assert result.extraction.requirements[0].special_instructions == ["Keep the source record"]
    assert {action.path for action in result.wire_format_actions} == {
        "calls[1].review_issues[0]", "calls[1].requirements[0].instructions[1]",
    }
    assert CustomerExtractionRun.model_validate_json(result.model_dump_json()) == result


@pytest.mark.parametrize("field,bad", [("methods", [""]), ("instructions", [False]),
                                      ("evidence", [{"page": 1, "quote": ""}])])
def test_cleanup_never_drops_invalid_methods_evidence_or_substantive_types(field, bad):
    value = payload()
    value["requirements"][0][field] = bad
    with pytest.raises(CustomerExtractionError) as error:
        run(value)
    assert error.value.artifact.error_code == "invalid_output_schema"
    assert json.loads(error.value.raw_response) == value


@pytest.mark.parametrize("exception", [
    "All samples except cold-test specimens are conditioned at 31 °C.",
    "Todas las muestras excepto las destinadas al ensayo en frío: 31 °C.",
    "Alle Proben außer Kaltversuchen: 31 °C.",
    "Toutes les éprouvettes sauf les essais à froid: 31 °C.",
    "所有样品（低温试验样品除外）在31 °C调节。",
])
def test_shared_exception_is_not_silently_accepted_as_blanket_applicability(exception):
    value = payload()
    value["shared_requirements"] = [{
        "applies_to": [1], "conditions": [exception],
        "evidence": [{"page": 1, "quote": exception}],
    }]
    result = run(value)
    assert any(
        issue.startswith("req_001:") and "shared source conditions contain an exception" in issue
        for issue in result.extraction.review_issues
    )
    assert exception in [row.raw_value for row in result.extraction.requirements[0].conditions]
    assert json.loads(result.raw_response) == value


def roles():
    return [
        {"code": "ISO 60038:2021", "role": "primary", "page": 1,
         "quote": "Use ISO 60038:2021 as the primary method."},
        {"code": "ASTM D1037-20", "role": "optional_approval_required", "page": 1,
         "quote": "ASTM D1037-20 is optional with prior buyer approval."},
    ]


def test_consistent_role_citations_can_repeat_but_conflicting_roles_are_rejected():
    value = payload()
    same = roles()[0]
    value["requirements"][0]["method_roles"] = [same, same.copy()]
    assert run(value).extraction.requirements[0].requested_methods[0].role == "primary"
    assert len(json.loads(run(value).raw_response)["requirements"][0]["method_roles"]) == 2
    value["requirements"][0]["method_roles"][1]["role"] = "alternative"
    with pytest.raises(CustomerExtractionError) as error:
        run(value)
    assert error.value.artifact.error_code == "invalid_output_schema"


def test_explicit_grounded_roles_keep_optional_unapproved_route_out_of_required_method_set():
    value = payload()
    value["requirements"][0]["method_roles"] = roles()
    requirement = run(value).extraction.requirements[0]
    methods, issues = _selected_methods(requirement, None)
    assert [method.code for method in methods] == ["ISO 60038:2021"]
    assert issues == []
    assert [method.code for method in requirement.requested_methods] == [
        "ISO 60038:2021", "ASTM D1037-20",
    ]
    assert any("prior buyer approval" in item.quote for item in requirement.evidence)


@pytest.mark.parametrize("bad_quote,bad_page", [
    ("ASTM D1037-20 is already approved by the buyer.", 1),
    ("ASTM D1037-20", 1),
    ("ASTM D1037-20 is optional with prior buyer approval.", 2),
])
def test_fabricated_naked_or_wrong_page_role_cannot_auto_resolve_method_choice(bad_quote, bad_page):
    value = payload()
    source_roles = roles()
    source_roles[1].update(quote=bad_quote, page=bad_page)
    value["requirements"][0]["method_roles"] = source_roles
    result = run(value)
    requirement = result.extraction.requirements[0]
    assert requirement.requested_methods[1].role is None
    methods, issues = _selected_methods(requirement, None)
    assert len(methods) == 2 and issues
    assert any(
        "no automatic method-role selection" in issue for issue in result.extraction.review_issues
    )


def test_role_for_another_method_is_rejected_instead_of_creating_or_dropping_a_method():
    value = payload()
    value["requirements"][0]["method_roles"] = [
        {**roles()[0], "code": "ISO 99999:2026"},
    ]
    with pytest.raises(CustomerExtractionError) as error:
        run(value)
    assert error.value.artifact.error_code == "invalid_output_schema"


def test_shared_preparation_is_distributed_to_explicit_tests_without_a_fake_test_row():
    value = payload()
    value["requirements"].append({
        **copy.deepcopy(value["requirements"][0]), "test": "Second adhesion setup",
    })
    shared_text = "Temperature 31 °C."
    value["shared_requirements"] = [{
        "applies_to": [2], "conditions": [shared_text],
        "evidence": [{"page": 1, "quote": shared_text}],
    }]
    value["requirements"][1]["conditions"] = []
    original = copy.deepcopy(value)
    result = run(value)
    assert len(result.extraction.requirements) == 2
    first, second = result.extraction.requirements
    assert [item.raw_value for item in first.conditions] == [shared_text]
    assert [item.raw_value for item in second.conditions] == [shared_text]
    assert json.loads(result.raw_response) == original
    assert value == original


def test_same_local_and_shared_clauses_do_not_create_duplicate_requirements():
    value = payload()
    row = value["requirements"][0]
    row["conditions"] = ["Temperature 31 °C.", "Temperature 31 °C."]
    value["shared_requirements"] = [{
        "applies_to": [1], "conditions": ["Temperature 31 °C."],
        "evidence": [{"page": 1, "quote": SOURCE}],
    }]
    original = copy.deepcopy(value)
    result = run(value)
    assert [item.raw_value for item in result.extraction.requirements[0].conditions] == [
        "Temperature 31 °C.",
    ]
    assert len(result.extraction.requirements[0].evidence) == 1
    assert json.loads(result.raw_response) == original


def test_shared_instruction_exception_does_not_claim_uncertain_scientific_applicability():
    value = payload()
    value["shared_requirements"] = [{
        "applies_to": [1],
        "instructions": ["Retain records unless the buyer authorizes disposal."],
        "evidence": [{"page": 1, "quote": SOURCE}],
    }]
    result = run(value)
    assert result.extraction.requirements[0].special_instructions == [
        "Retain records unless the buyer authorizes disposal.",
    ]
    assert not any("shared source conditions" in issue for issue in result.extraction.review_issues)


@pytest.mark.parametrize("targets", [[], [0], [3], [1, 1]])
def test_shared_clause_cannot_target_missing_repeated_or_implicit_all_tests(targets):
    value = payload()
    value["shared_requirements"] = [{
        "applies_to": targets, "conditions": ["Temperature 31 °C."],
        "evidence": [{"page": 1, "quote": "Temperature 31 °C."}],
    }]
    with pytest.raises(CustomerExtractionError) as error:
        run(value)
    assert error.value.artifact.error_code == "invalid_output_schema"


@pytest.mark.parametrize("text,expected", [
    ("Unless stated otherwise, condition all specimens for 48 h at 23 °C.", False),
    ("Unless otherwise specified, condition all specimens for 48 h.", False),
    ("Sauf indication contraire, conserver tous les échantillons à 23 °C.", False),
    ("Une mesure hors limite ne doit pas être effacée.", False),
    ("結果を削除してはならない。外観を記録する。", False),
    ("All samples except cold-test specimens: 23 °C for 24 h.", True),
    ("Unless stated otherwise, all samples except cold-test specimens: 23 °C.", True),
    ("所有样品（低温样品除外）在23 °C调节。", True),
    ("除低温样品外，所有样品在23 °C调节。", True),
    ("Toutes les éprouvettes hors les éprouvettes à froid: 23 °C.", True),
    ("Condition at 23 °C unless the customer requires a cold test.", True),
])
def test_exception_signals_do_not_confuse_default_rules_or_unrelated_workflow(text, expected):
    assert has_applicability_exception(text) is expected


def test_unrelated_administrative_exception_does_not_change_scientific_applicability():
    value = payload()
    clause = "Temperature 31 °C. Retain records unless the buyer authorizes disposal."
    value["shared_requirements"] = [{
        "applies_to": [1], "conditions": ["Temperature 31 °C."],
        "evidence": [{"page": 1, "quote": clause}],
    }]
    result = run(value)
    assert not any("shared source conditions" in issue for issue in result.extraction.review_issues)


def test_old_software_default_exception_flag_is_audited_without_forcing_review():
    clause = "Unless stated otherwise, temperature 31 °C."
    value = payload()
    value["shared_requirements"] = [{
        "applies_to": [1], "conditions": [clause],
        "evidence": [{"page": 1, "quote": clause}],
    }]
    source = document()
    source.pages[0].text += "\n" + clause
    result = CustomerRequirementRunner(StaticModelProvider(value)).run(
        source, strategy="compact_document"
    )
    old_issue = (
        "req_001: shared source conditions contain an exception; verify their "
        "applicability to this test before accepting the draft."
    )
    result.extraction.review_issues.append(old_issue)
    assert nonblocking_shared_clause_issues(result) == {old_issue}
    assert assess_requirements(result, source)["req_001"].state == "ready"
    assert result.extraction.review_issues == [old_issue]
    # A concern actually emitted by the model is not a software-only diagnostic.
    value["review_issues"] = [old_issue]
    direct = CustomerRequirementRunner(StaticModelProvider(value)).run(
        source, strategy="compact_document"
    )
    assert nonblocking_shared_clause_issues(direct) == set()
    assert assess_requirements(direct, source)["req_001"].state == "needs_attention"
