"""Legacy summaries cannot invent equality or turn process targets into limits."""

from decimal import Decimal

from scione.specifications.models import CustomerRequirement
from scione.structured.models import AtomicClaim
from scione.structured.normalization import (
    _unparsed_qualification,
    known_source_fact_problem,
    normalize_requirement,
)


def source(raw, **extra):
    return CustomerRequirement(
        id="req_limit",
        original_test_name="Dry film thickness",
        acceptance_criteria=[dict(name="Film thickness", raw_value=raw)],
        evidence=[dict(page=1, quote=raw)],
        **extra,
    )


def test_literal_chinese_minimum_remains_minimum_not_equal():
    claims = normalize_requirement(source("其中任意单点不得低于 140 µm")).claims
    assert any(
        c.parameter_id == "film_thickness" and c.operator == ">=" and c.value == 140 for c in claims
    )
    assert not any(c.operator == "=" and c.value == 140 for c in claims)


def test_unlabelled_bare_criterion_is_text_until_its_operator_is_known():
    claims = normalize_requirement(source("Coating thickness 140 µm")).claims
    assert not any(c.operator == "=" and c.value == 140 for c in claims)
    assert any(c.normalization_state == "unresolved" and "140" in c.raw_text for c in claims)
    nominal = normalize_requirement(source("Nominal coating thickness 140 µm")).claims
    assert any(
        c.operator == "=" and c.value == 140 and c.normalization_state == "parsed" for c in nominal
    )


def test_supplier_process_layer_targets_never_become_acceptance_fields():
    raw = (
        "底色漆 12–16 µm 与透明层 40–50 µm 仅为供应商工艺记录及目标，"
        "不作为本方法分层测量或独立判废值。"
    )
    claims = normalize_requirement(source(raw)).claims
    assert all(c.normalization_state == "unresolved" for c in claims)
    assert not any(
        c.parameter_id == "film_thickness" and c.minimum in {Decimal(12), Decimal(40)}
        for c in claims
    )


def test_literal_category_value_covers_only_its_own_words_not_unseen_restrictions():
    raw = "Use the same steel witnesses, except nitrogen exposure."
    native = AtomicClaim(
        id="material",
        parameter_id="specimen_material",
        label="Material",
        operator="text",
        text_value="same steel witnesses",
        raw_text=raw,
        source_pages=[1],
    )
    residual = _unparsed_qualification(
        "same except nitrogen exposure", [], [native], source_text=raw
    )
    assert "same" not in residual
    assert "nitrogen" in residual and "except" in residual
    repeated = "Use the same steel witnesses and the same chemical grade."
    residual = _unparsed_qualification("same chemical grade", [], [native], source_text=repeated)
    assert "same" in residual and "chemical" in residual


def test_wide_numeric_quote_never_masks_unrepresented_qualifier_words():
    raw = "Temperature 23 °C under nitrogen."
    native = AtomicClaim(
        id="temperature",
        parameter_id="temperature",
        label="Temperature",
        operator="=",
        value=23,
        unit="°C",
        raw_text=raw,
        source_pages=[1],
    )
    assert "nitrogen" in _unparsed_qualification("under nitrogen", [], [native], source_text=raw)


def test_prohibiting_rust_spots_above_boundary_allows_equality_but_not_a_stricter_claim():
    raw = "非划痕区域不得有直径超过1.0mm的可见锈蚀点。"
    native = AtomicClaim(
        id="rust_limit",
        parameter_id="rust_spot_diameter",
        label="Rust spot diameter",
        kind="criterion",
        operator="<=",
        value=Decimal("1.0"),
        unit="mm",
        raw_text=raw,
        source_pages=[1],
    )
    requirement = source(raw, atomic_claims=[native])
    assert known_source_fact_problem(native, requirement) is None
    assert known_source_fact_problem(native.model_copy(update={"operator": "<"}), requirement)
    excepted = native.model_copy(update={"raw_text": raw + "特殊样品除外。"})
    assert known_source_fact_problem(excepted, requirement)
    unrelated = native.model_copy(
        update={"raw_text": "锈蚀点直径超过1.0mm需要记录。不得有直径超过2.0mm的锈蚀点。"}
    )
    assert known_source_fact_problem(unrelated, requirement)
