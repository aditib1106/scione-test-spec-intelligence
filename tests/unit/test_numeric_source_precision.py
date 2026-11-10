"""Exact model quantities survive parsing/storage and scientific source grounding."""

from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace

import httpx
import pytest

from scione.catalogs.models import LabCapability
from scione.catalogs.seed import load_reference_catalog
from scione.catalogs.sql_lab_store import SQLLabStore
from scione.library.store import LibraryStore
from scione.numeric_literals import source_numbers, validate_numeric_complexity
from scione.parameters.units import source_numbers_for_unit
from scione.providers.base import ProviderError, StructuredGenerationRequest
from scione.providers.gemini import GeminiModelProvider
from scione.providers.groq import GroqModelProvider
from scione.providers.json_output import loads_exact
from scione.providers.openai_compatible import OpenAICompatibleModelProvider
from scione.specifications.corrections import SQLFactsStore
from scione.specifications.extraction import CustomerRequirementRunner, _structured_customer_schema
from scione.specifications.models import (
    CompactAtomicClaim,
    CompactCustomerExtraction,
    CustomerRequirement,
)
from scione.specifications.output_schema import constrain_compact_extraction_schema
from scione.structured import normalize_requirement
from scione.structured.models import AtomicClaim


def structured_request():
    return StructuredGenerationRequest(
        system_prompt="Extract only source-stated facts.",
        user_prompt="A synthetic quantity, without any inference call.",
        schema_name="precision_fixture",
        json_schema={"type": "object", "properties": {"value": {"type": "number"}}},
    )


def response_provider(name, text):
    if name in {"sail", "fireworks"}:
        response = {
            "model": "offline-model",
            "choices": [{"finish_reason": "stop", "message": {"content": text}}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 10, "total_tokens": 20},
        }
        return OpenAICompatibleModelProvider(
            provider_name=name,
            api_key="fixture",
            model_name="offline-model",
            client=httpx.Client(
                transport=httpx.MockTransport(lambda _request: httpx.Response(200, json=response))
            ),
        )
    if name == "gemini":
        response = SimpleNamespace(
            text=text,
            candidates=[SimpleNamespace(finish_reason="STOP")],
            usage_metadata=None,
        )
        return GeminiModelProvider(
            api_key="fixture",
            model_name="offline-model",
            client=SimpleNamespace(models=SimpleNamespace(generate_content=lambda **_kw: response)),
        )
    completion = SimpleNamespace(
        choices=[SimpleNamespace(finish_reason="stop", message=SimpleNamespace(content=text))],
        usage=None,
    )
    return GroqModelProvider(
        api_key="fixture",
        model_name="offline-model",
        client=SimpleNamespace(
            chat=SimpleNamespace(completions=SimpleNamespace(create=lambda **_kw: completion))
        ),
    )


@pytest.mark.parametrize("provider", ["sail", "fireworks", "gemini", "groq"])
def test_adapters_keep_decimal_lexemes_and_large_integer_exact(provider):
    text = '{"value":0.1234567890123456789012345,"large":9007199254740993,"small":1.2e-30}'
    result = response_provider(provider, text).generate_structured(structured_request())
    assert result.parsed["value"] == Decimal("0.1234567890123456789012345")
    assert result.parsed["large"] == 9007199254740993
    assert result.parsed["small"] == Decimal("1.2e-30")
    assert result.raw_text == text
    # Typed artifacts preserve exact values as strings while raw_text stays intact.
    assert "0.1234567890123456789012345" in result.model_dump_json()


@pytest.mark.parametrize("provider", ["sail", "fireworks", "gemini", "groq"])
@pytest.mark.parametrize("text", ['{"value":NaN}', '{"value":Infinity}', '{"value":1,"value":2}'])
def test_exact_parser_never_accepts_nonfinite_or_duplicate_quantities(provider, text):
    with pytest.raises(ProviderError):
        response_provider(provider, text).generate_structured(structured_request())


def test_decimal_compact_values_still_require_numeric_wire_items():
    schema = constrain_compact_extraction_schema(
        _structured_customer_schema(CompactCustomerExtraction.model_json_schema())
    )
    for branch in schema["$defs"]["CompactAtomicClaim"]["anyOf"]:
        assert branch["properties"]["values"]["items"] == {"type": "number"}


@pytest.mark.parametrize("literal", ["1e100000000", "1e-100000000", "9" * 1001])
def test_numeric_complexity_budget_rejects_model_literals_without_rounding(literal):
    with pytest.raises(ValueError, match="budget"):
        loads_exact('{"value":' + literal + "}")


@pytest.mark.parametrize("field", ["value", "minimum", "maximum", "tolerance", "values"])
@pytest.mark.parametrize("literal", ["1e100000000", "1e-100000000", "9" * 1001])
def test_typed_atomic_inputs_are_bounded_before_display_or_fraction(field, literal):
    supplied = Decimal(literal)
    values = {field: [supplied] if field == "values" else supplied}
    with pytest.raises(ValueError, match="budget"):
        AtomicClaim(
            id="bounded_number",
            parameter_id="custom_metric",
            label="Metric",
            operator="text",
            raw_text="Untrusted numeric input",
            **values,
        )


@pytest.mark.parametrize("literal", ["1e100000000", "1e-100000000", "9" * 1001])
def test_compact_numeric_strings_cannot_bypass_the_complexity_budget(literal):
    with pytest.raises(ValueError, match="budget"):
        CompactAtomicClaim(
            parameter_id="custom_metric",
            kind="condition",
            stage="measurement",
            operator="=",
            values=[literal],
            raw_text="Untrusted numeric input",
            source_pages=[1],
        )


def test_oversized_json_integer_is_rejected_before_python_integer_conversion():
    with pytest.raises(ValueError, match="size budget"):
        loads_exact('{"value":' + "9" * 5000 + "}")


@pytest.mark.parametrize("literal", ["1e1000", "1e-1000", "9" * 1000])
def test_numeric_budget_edges_preserve_exact_values(literal):
    value = Decimal(literal)
    assert validate_numeric_complexity(value) == value
    assert loads_exact('{"value":' + literal + "}")["value"] == value
    claim = AtomicClaim(
        id="bounded_edge",
        parameter_id="custom_metric",
        label="Metric",
        operator="=",
        value=value,
        raw_text=literal,
    )
    assert len(claim.display_value) <= 1003


@pytest.mark.parametrize("literal", ["9007199254740993", "0.1234567890123456789012345", "1.2e-30"])
def test_compact_expansion_keeps_exact_decoder_values(literal):
    text = (
        '{"parameter_id":"custom_quantity","kind":"condition","stage":"measurement",'
        '"operator":"=","values":[' + literal + '],"unit":null,"text_value":null,'
        '"raw_text":"quantity ' + literal + '","source_pages":[1]}'
    )
    atom = CompactAtomicClaim.model_validate(loads_exact(text)).expand("precision_atom")
    assert atom.value == Decimal(literal)
    assert atom.raw_text == "quantity " + literal


def requirement(raw, value, *, unit="s", parameter="duration"):
    return CustomerRequirement(
        id="req_number",
        original_test_name="Synthetic scientific quantities",
        conditions=[{"name": "Measurement", "raw_value": raw}],
        evidence=[{"page": 1, "quote": raw}],
        atomic_claims=[
            AtomicClaim(
                id="model_number",
                parameter_id=parameter,
                label="Quantity",
                kind="condition",
                stage="measurement",
                operator="=",
                value=Decimal(value),
                unit=unit,
                raw_text=raw,
                source_pages=[1],
            )
        ],
    )


@pytest.mark.parametrize(
    ("raw", "value"),
    [
        ("duration 2.5e2 s", "250"),
        ("duration 2.5E+2 s", "250"),
        ("duration 1e-3 s", "0.001"),
        ("duration .5 s", "0.5"),
        ("duration −2.5e−2 s", "-0.025"),
        ("时长2.5e2秒", "250"),
    ],
)
def test_scientific_source_quantity_is_not_replaced_by_exponent_tail(raw, value):
    normalized = normalize_requirement(requirement(raw, value))
    supplied = normalized.claims[0]
    assert supplied.value == Decimal(value)
    assert supplied.normalization_state == "parsed"
    assert Decimal(value) in source_numbers_for_unit("s", raw)
    assert all(
        claim.value == Decimal(value)
        for claim in normalized.claims
        if claim.parameter_id == "duration" and claim.normalization_state != "unresolved"
    )


def test_scientific_source_does_not_ground_a_fabricated_tail_value():
    normalized = normalize_requirement(requirement("duration 2.5e2 s", "2"))
    assert normalized.claims[0].normalization_state == "unresolved"
    assert not any(
        claim.value == 2 and claim.normalization_state != "unresolved"
        for claim in normalized.claims
    )


@pytest.mark.parametrize("raw", ["duration 1×10^-3 s", "duration 2.5e s", "fixture XQ-7 s"])
def test_unsupported_or_identifier_notation_never_asserts_its_numeric_tail(raw):
    assert source_numbers_for_unit("s", raw) == set()
    normalized = normalize_requirement(requirement(raw, "3"))
    assert normalized.claims[0].normalization_state == "unresolved"
    assert not any(
        claim.value is not None and claim.normalization_state != "unresolved"
        for claim in normalized.claims
    )


def test_scientific_range_and_tolerance_bind_both_values_to_the_unit():
    assert source_numbers_for_unit("s", "duration 1e-3 to 2e-3 s") == {
        Decimal("0.001"),
        Decimal("0.002"),
    }
    assert source_numbers_for_unit("s", "duration 2.5e2 ± 1e1 s") == {Decimal(250), Decimal(10)}
    assert source_numbers_for_unit("°C", "temperature 20-26 °C") == {Decimal(20), Decimal(26)}


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("may not exceed 1.0.", {Decimal("1.0")}),
        ("at most .5.", {Decimal("0.5")}),
        ("value 1.2e3.", {Decimal("1200")}),
        ("value -1.2E-3.", {Decimal("-0.0012")}),
        ("limits 1.0, 2.0.", {Decimal("1.0"), Decimal("2.0")}),
    ],
)
def test_numeric_sentence_punctuation_does_not_hide_valid_values(raw, expected):
    assert source_numbers(raw) == expected


@pytest.mark.parametrize("raw", ["1.2.3", "1..2", "1.0.e3", "1.2e3.4"])
def test_malformed_decimal_continuations_do_not_expose_prefix_or_tail(raw):
    assert source_numbers(raw) == set()
    assert source_numbers_for_unit("s", raw + " s") == set()


def test_exact_model_fact_survives_sql_archive_lab_and_review_roundtrip(tmp_path):
    from scione.schemas import (
        DocumentPage,
        IngestedDocument,
        TextExtractionStatus,
        TextReadingOrder,
    )

    literal = "0.1234567890123456789012345"
    source = "Synthetic fixture: custom channel length " + literal + " mm."
    payload = (
        '{"document":{},"requirements":[{"test":"Custom channel","methods":[],"conditions":["'
        + source
        + '"],"criteria":[],"scope":[],"instructions":[],"evidence":[{"page":1,"quote":"'
        + source
        + '"}],"atomic_claims":[{"parameter_id":"custom_channel_length",'
        '"kind":"condition","stage":"measurement","operator":"=","values":[' + literal + "],"
        '"unit":"mm","text_value":null,"raw_text":"' + source + '","source_pages":[1],'
        '"sequence":2,"source_context":"water / aluminium","configuration":"setup_A"}]}],'
        '"review_issues":[]}'
    )
    document = IngestedDocument(
        document_id="0123456789abcdef",
        file_name="synthetic.txt",
        sha256="a" * 64,
        page_count=1,
        pages=[
            DocumentPage(
                page_number=1,
                text=source,
                character_count=len(source),
                non_whitespace_character_count=len(source.replace(" ", "")),
            )
        ],
        text_reading_order=TextReadingOrder.CONTENT_STREAM,
        extraction_status=TextExtractionStatus.READY,
    )
    run = CustomerRequirementRunner(response_provider("sail", payload)).run(
        document, strategy="compact_document"
    )
    atom = run.extraction.requirements[0].atomic_claims[0]
    assert atom.value == Decimal(literal)
    library = LibraryStore(tmp_path)
    library.ensure_document(
        SimpleNamespace(
            document_id=document.document_id,
            original_name="synthetic.pdf",
            created_at=datetime.now(UTC),
        )
    )
    normalized = [normalize_requirement(run.extraction.requirements[0])]
    library.save_requirements(run, normalized)
    assert library.load_requirements(run.run_id)[0]["claims"][0]["value"] == literal

    catalog = load_reference_catalog()
    store = SQLLabStore(library, catalog)
    lab = store.load_or_create()
    lab.capabilities = [
        LabCapability(
            id="cap_channel",
            canonical_test_id=catalog.tests[0].id,
            method_ids=catalog.tests[0].method_ids,
            atomic_claims=[atom],
        )
    ]
    store.save(lab)
    assert store.load_or_create().capabilities[0].atomic_claims[0] == atom
    corrected = run.extraction.requirements[0].model_copy(deep=True)
    corrected.original_test_name = "Reviewed channel"
    facts = SQLFactsStore(library, "local_lab")
    facts.save(run, [corrected], "Offline reviewer fixture", expected_version=0)
    assert facts.latest(run.document_id, run.run_id).requirements[0].atomic_claims[0] == atom
    library.close()
